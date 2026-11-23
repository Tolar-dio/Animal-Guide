"""Collect iNaturalist taxon ids, observation counts and licensed photos.

1. Fill missing iNat taxon ids from the iNaturalist Open Data taxa file
   (s3://inaturalist-open-data/taxa.csv.gz) by exact, active species name.
2. Pull each taxon's curated photos from the API (/v1/taxa/{ids}, 30 at a time).
3. For species whose curated photos are all unusable, search research-grade
   observations for CC0 / CC BY / CC BY-SA photos (one call per species).

Writes data/media/inat_photos.parquet (one row per candidate photo) and
data/media/inat_taxa.parquet (id -> inat_id, observations_count, common name).
All-rights-reserved photos are never stored.
"""
from __future__ import annotations

import gzip
import io
import json
from datetime import date

import pandas as pd

from common import CACHE, CONFIG, DATA, Http, chunks

API = "https://api.inaturalist.org/v1"
TAXA_CSV = "https://inaturalist-open-data.s3.amazonaws.com/taxa.csv.gz"
OPEN = {"cc0", "cc-by", "cc-by-sa"}
NC = {"cc-by-nc", "cc-by-nc-sa", "cc-by-nc-nd", "cc-by-nd"}


def fill_ids(http: Http, sp: pd.DataFrame) -> pd.DataFrame:
    cache = CACHE / "inat" / "taxa.csv.gz"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        cache.write_bytes(http.get(TAXA_CSV, timeout=600).content)
    # requests transparently un-gzips this file, so the cache may be plain text.
    raw = cache.read_bytes()
    text = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    taxa = pd.read_csv(io.BytesIO(text), sep="\t", usecols=["taxon_id", "name", "rank", "active", "ancestry"])
    mammals = taxa[(taxa["rank"] == "species") & (taxa["active"]) &
                   taxa["ancestry"].str.contains("/40151/", na=False)]  # 40151 = Mammalia
    lookup = mammals.drop_duplicates("name").set_index("name")["taxon_id"]
    filled = sp["inat_id"].isna() & sp["sciName"].isin(lookup.index)
    sp.loc[filled, "inat_id"] = sp.loc[filled, "sciName"].map(lookup)
    print(f"iNat ids: {filled.sum()} filled from open data, {sp['inat_id'].notna().sum()} total")
    return sp


def cached(http: Http, key: str, url: str, params: dict | None = None) -> dict:
    """API call cached under cache/inat/api/<date>/ so an interrupted run resumes."""
    path = CACHE / "inat" / "api" / date.today().isoformat() / f"{key}.json"
    if path.exists():
        return json.loads(path.read_text())
    data = http.json(url, params=params)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return data


def photo_row(mdd_id: int, p: dict, source: str, rank: int) -> dict | None:
    lic = (p.get("license_code") or "").lower()
    if lic not in OPEN | NC:
        return None
    url = p["url"]
    return {
        "id": mdd_id, "photo_id": p["id"], "license": lic, "open": lic in OPEN,
        "attribution": p.get("attribution"),
        "url_template": url.replace("/square.", "/{size}."),
        "width": (p.get("original_dimensions") or {}).get("width"),
        "height": (p.get("original_dimensions") or {}).get("height"),
        "source": source, "rank": rank,
        "page": f"https://www.inaturalist.org/photos/{p['id']}",
    }


def main() -> None:
    http = Http(min_interval=CONFIG["rate"]["inaturalist"])
    sp = pd.read_parquet(DATA / "species" / "mammals.parquet", columns=["id", "sciName"])
    xw = pd.read_parquet(DATA / "crosswalk" / "mammals_wikidata.parquet", columns=["id", "inat_id"])
    sp = sp.merge(xw, on="id", how="left")
    sp = fill_ids(http, sp)

    by_inat = {int(r.inat_id): int(r.id) for r in sp.dropna(subset=["inat_id"]).itertuples()}
    photos, taxa = [], []
    ids = list(by_inat)
    for i, batch in enumerate(chunks(ids, 30)):
        res = cached(http, f"taxa-{batch[0]}-{len(batch)}", f"{API}/taxa/{','.join(map(str, batch))}")["results"]
        for t in res:
            mdd_id = by_inat.get(t["id"])
            if mdd_id is None:
                continue
            taxa.append({"id": mdd_id, "inat_id": t["id"], "inat_name": t["name"],
                         "observations_count": t.get("observations_count", 0),
                         "inat_common_name": t.get("preferred_common_name"),
                         "inat_active": t.get("is_active", True),
                         # Short Wikipedia summary iNat mirrors (CC BY-SA); fallback text only.
                         "wikipedia_summary": t.get("wikipedia_summary"),
                         "wikipedia_url": t.get("wikipedia_url")})
            for rank, tp in enumerate(t.get("taxon_photos") or []):
                row = photo_row(mdd_id, tp["photo"], "inat_taxon", rank)
                if row:
                    photos.append(row)
        if i % 20 == 0:
            print(f"  taxa batch {i + 1}/{(len(ids) + 29) // 30}", flush=True)

    have_open = {p["id"] for p in photos if p["open"]}
    # Reuse last run's observation photos; only search species that still have nothing open.
    prev_path = DATA / "media" / "inat_photos.parquet"
    if prev_path.exists():
        prev = pd.read_parquet(prev_path)
        prev = prev[(prev["source"] == "inat_observation") & (~prev["id"].isin(have_open))]
        photos += prev.to_dict("records")
        have_open |= set(prev.loc[prev["open"], "id"])
        print(f"reused {len(prev)} observation photos from the last run")
    gaps = [(inat, mdd) for inat, mdd in by_inat.items() if mdd not in have_open]
    print(f"observation search for {len(gaps)} species without an open photo")
    for j, (inat, mdd_id) in enumerate(gaps):
        obs = cached(http, f"obs-{inat}", f"{API}/observations", params={
            "taxon_id": inat, "photo_license": "cc0,cc-by,cc-by-sa", "quality_grade": "research",
            "order_by": "votes", "per_page": 3, "photos": "true"})["results"]
        rank = 100
        for o in obs:
            for p in (o.get("photos") or [])[:1]:
                row = photo_row(mdd_id, p, "inat_observation", rank)
                if row:
                    row["observation"] = f"https://www.inaturalist.org/observations/{o['id']}"
                    photos.append(row)
                    rank += 1
        if j % 100 == 0:
            print(f"  observation search {j}/{len(gaps)}", flush=True)

    out = DATA / "media"
    out.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(photos).to_parquet(out / "inat_photos.parquet", index=False)
    pd.DataFrame(taxa).to_parquet(out / "inat_taxa.parquet", index=False)
    df = pd.DataFrame(photos)
    print(f"iNat photos: {len(df)} candidates; species with open photo {df[df.open].id.nunique()}, "
          f"with any (incl. NC) {df.id.nunique()}")


if __name__ == "__main__":
    main()
