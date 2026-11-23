"""Collect iNaturalist taxon ids, observation counts and licensed photos.

1. Fill missing iNat taxon ids from the iNaturalist Open Data taxa file
   (s3://inaturalist-open-data/taxa.csv.gz) by exact, active species name.
2. Pull each taxon's curated photos from the API (/v1/taxa/{ids}, 30 at a time).
3. For species whose curated photos are all unusable, search research-grade
   observations for CC0 / CC BY / CC BY-SA photos (one call per species).

  python fetch_inat.py mammals|birds|reptiles   (default: all three)

Writes data/media/<class>_inat_photos.parquet (one row per candidate photo) and
data/media/<class>_inat_taxa.parquet (key -> inat_id, observations_count, names).
All-rights-reserved photos are never stored.
"""
from __future__ import annotations

import gzip
import io
import json
import sys
from datetime import date

import pandas as pd
import requests

from common import CACHE, CONFIG, DATA, Http, chunks

API = "https://api.inaturalist.org/v1"
TAXA_CSV = "https://inaturalist-open-data.s3.amazonaws.com/taxa.csv.gz"
OPEN = {"cc0", "cc-by", "cc-by-sa"}
NC = {"cc-by-nc", "cc-by-nc-sa", "cc-by-nc-nd", "cc-by-nd"}
CLASS_TAXON = {"mammals": 40151, "birds": 3, "reptiles": 26036}  # iNat ids for Mammalia, Aves, Reptilia


def fill_ids(http: Http, sp: pd.DataFrame, cls: str) -> pd.DataFrame:
    cache = CACHE / "inat" / "taxa.csv.gz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        cache.write_bytes(http.get(TAXA_CSV, timeout=600).content)
    # requests transparently un-gzips this file, so the cache may be plain text.
    raw = cache.read_bytes()
    text = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    taxa = pd.read_csv(io.BytesIO(text), sep="\t", usecols=["taxon_id", "name", "rank", "active", "ancestry"])
    anc = f"/{CLASS_TAXON[cls]}/"
    group = taxa[(taxa["rank"] == "species") & (taxa["active"]) & taxa["ancestry"].str.contains(anc, na=False)]
    lookup = group.drop_duplicates("name").set_index("name")["taxon_id"]
    filled = sp["inat_id"].isna() & sp["sciName"].isin(lookup.index)
    sp.loc[filled, "inat_id"] = sp.loc[filled, "sciName"].map(lookup)
    print(f"iNat ids: {filled.sum()} filled from open data, {sp['inat_id'].notna().sum()} total")
    return sp


def cached(http: Http, key: str, url: str, params: dict | None = None) -> dict:
    """API call cached under cache/inat/api/<date>/ so an interrupted run resumes."""
    path = CACHE / "inat" / "api" / date.today().isoformat() / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    try:
        data = http.json(url, params=params)
    except requests.HTTPError as exc:  # bad ids (e.g. malformed Wikidata values) return 404/422
        if exc.response is None or exc.response.status_code not in (404, 422):
            raise
        print(f"    skipping {key}: HTTP {exc.response.status_code}", flush=True)
        data = {"results": []}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def photo_row(key: str, p: dict, source: str, rank: int) -> dict | None:
    lic = (p.get("license_code") or "").lower()
    if lic not in OPEN | NC:
        return None
    url = p["url"]
    return {
        "key": key, "photo_id": p["id"], "license": lic, "open": lic in OPEN,
        "attribution": p.get("attribution"),
        "url_template": url.replace("/square.", "/{size}."),
        "width": (p.get("original_dimensions") or {}).get("width"),
        "height": (p.get("original_dimensions") or {}).get("height"),
        "source": source, "rank": rank,
        "page": f"https://www.inaturalist.org/photos/{p['id']}",
    }


def run(cls: str, http: Http) -> None:
    sp = pd.read_parquet(DATA / "species" / f"{cls}.parquet")
    if "key" not in sp:
        sp["key"] = "m" + sp["id"].astype(str)
    sp = sp[["key", "sciName"]]
    xw = pd.read_parquet(DATA / "crosswalk" / f"{cls}_wikidata.parquet", columns=["key", "inat_id"])
    sp = sp.merge(xw, on="key", how="left")
    sp = fill_ids(http, sp, cls)

    by_inat = {int(r.inat_id): r.key for r in sp.dropna(subset=["inat_id"]).itertuples()}
    photos, taxa = [], []
    ids = list(by_inat)
    for i, batch in enumerate(chunks(ids, 30)):
        res = cached(http, f"taxa-{batch[0]}-{len(batch)}", f"{API}/taxa/{','.join(map(str, batch))}")["results"]
        for t in res:
            key = by_inat.get(t["id"])
            if key is None:
                continue
            taxa.append({"key": key, "inat_id": t["id"], "inat_name": t["name"],
                         "observations_count": t.get("observations_count", 0),
                         "inat_common_name": t.get("preferred_common_name"),
                         "inat_active": t.get("is_active", True),
                         # Short Wikipedia summary iNat mirrors (CC BY-SA); fallback text only.
                         "wikipedia_summary": t.get("wikipedia_summary"),
                         "wikipedia_url": t.get("wikipedia_url")})
            for rank, tp in enumerate(t.get("taxon_photos") or []):
                row = photo_row(key, tp["photo"], "inat_taxon", rank)
                if row:
                    photos.append(row)
        if i % 50 == 0:
            print(f"  {cls} taxa batch {i + 1}/{(len(ids) + 29) // 30}", flush=True)

    have_open = {p["key"] for p in photos if p["open"]}
    # Reuse last run's observation photos; only search species that still have nothing open.
    prev_path = DATA / "media" / f"{cls}_inat_photos.parquet"
    if prev_path.exists():
        prev = pd.read_parquet(prev_path)
        prev = prev[(prev["source"] == "inat_observation") & (~prev["key"].isin(have_open))]
        photos += prev.to_dict("records")
        have_open |= set(prev.loc[prev["open"], "key"])
        print(f"reused {len(prev)} observation photos from the last run")
    observed = {t["key"] for t in taxa if (t["observations_count"] or 0) > 0}
    gaps = [(inat, key) for inat, key in by_inat.items() if key not in have_open and key in observed]
    print(f"{cls}: observation search for {len(gaps)} observed species without an open photo", flush=True)
    for j, (inat, key) in enumerate(gaps):
        obs = cached(http, f"obs-{inat}", f"{API}/observations", params={
            "taxon_id": inat, "photo_license": "cc0,cc-by,cc-by-sa", "quality_grade": "research",
            "order_by": "votes", "per_page": 3, "photos": "true"})["results"]
        rank = 100
        for o in obs:
            for p in (o.get("photos") or [])[:1]:
                row = photo_row(key, p, "inat_observation", rank)
                if row:
                    row["observation"] = f"https://www.inaturalist.org/observations/{o['id']}"
                    photos.append(row)
                    rank += 1
        if j % 200 == 0:
            print(f"  {cls} observation search {j}/{len(gaps)}", flush=True)

    out = DATA / "media"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(photos).to_parquet(out / f"{cls}_inat_photos.parquet", index=False)
    pd.DataFrame(taxa).to_parquet(out / f"{cls}_inat_taxa.parquet", index=False)
    df = pd.DataFrame(photos)
    print(f"iNat {cls}: {len(df)} candidates; species with open photo {df[df.open].key.nunique()}, "
          f"with any (incl. NC) {df.key.nunique()}")


def main() -> None:
    http = Http(min_interval=CONFIG["rate"]["inaturalist"])
    for cls in (sys.argv[1:] or list(CLASS_TAXON)):
        run(cls, http)


if __name__ == "__main__":
    main()
