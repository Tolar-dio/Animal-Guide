"""Merge every source into one record per species.

Nothing here fetches from the network: it reads the parquet files the fetch_*
steps wrote under data/, applies the media license policy from config.yaml,
and returns plain dicts for build_site.py.
"""
from __future__ import annotations

import html
import json
import math
import re
from urllib.parse import unquote
from collections import defaultdict
from pathlib import Path

import pandas as pd

from common import CONFIG, DATA

STATUS = {
    "EX": ("Extinct", 7), "EW": ("Extinct in the Wild", 6), "CR": ("Critically Endangered", 5),
    "EN": ("Endangered", 4), "VU": ("Vulnerable", 3), "NT": ("Near Threatened", 2),
    "LC": ("Least Concern", 1), "DD": ("Data Deficient", 0), "NE": ("Not Evaluated", -1),
}
LICENSE_NAMES = {
    "cc0": ("CC0", "https://creativecommons.org/publicdomain/zero/1.0/"),
    "pd": ("Public domain", None),
    "attribution": ("Attribution", None),
    "cc-by": ("CC BY", "https://creativecommons.org/licenses/by/4.0/"),
    "cc-by-sa": ("CC BY-SA", "https://creativecommons.org/licenses/by-sa/4.0/"),
    "cc-by-nc": ("CC BY-NC", "https://creativecommons.org/licenses/by-nc/4.0/"),
    "cc-by-nc-sa": ("CC BY-NC-SA", "https://creativecommons.org/licenses/by-nc-sa/4.0/"),
    "cc-by-nc-nd": ("CC BY-NC-ND", "https://creativecommons.org/licenses/by-nc-nd/4.0/"),
    "cc-by-nd": ("CC BY-ND", "https://creativecommons.org/licenses/by-nd/4.0/"),
}


def _read(path: Path, **kw) -> pd.DataFrame | None:
    return pd.read_parquet(path, **kw) if path.exists() else None


def _clean(v):
    if v is None:
        return None
    if isinstance(v, float) and math.isnan(v):
        return None
    if v is pd.NA or v is pd.NaT:
        return None
    return v


def license_label(code: str | None) -> tuple[str, str | None]:
    if not code:
        return ("Unknown", None)
    if code in LICENSE_NAMES:
        return LICENSE_NAMES[code]
    # Commons short names like "cc-by-sa-4.0"
    base = "-".join(p for p in code.split("-") if not p[0].isdigit())
    ver = code.split("-")[-1] if code.split("-")[-1][0].isdigit() else "4.0"
    name = base.upper().replace("CC-", "CC ").replace("-", "-")
    path = base.removeprefix("cc-")
    return (f"{name} {ver}", f"https://creativecommons.org/licenses/{path}/{ver}/")


def is_open(code: str | None) -> bool:
    if not code:
        return False
    return code in ("cc0", "pd", "attribution") or (code.startswith("cc-by") and "-nc" not in code and "-nd" not in code)


def inat_media(row) -> dict:
    name, url = license_label(row.license)
    tpl = row.url_template
    return {
        "kind": "inat", "src": tpl.format(size="large"), "thumb": tpl.format(size="medium"),
        "small": tpl.format(size="small"), "width": _clean(row.width), "height": _clean(row.height),
        "credit": row.attribution, "license": row.license, "license_name": name, "license_url": url,
        "open": bool(row.open), "page": getattr(row, "observation", None) if isinstance(getattr(row, "observation", None), str) else row.page,
        "via": "iNaturalist",
    }


def commons_media(row) -> dict | None:
    if not _clean(row.license) or not _clean(row.thumb_960):
        return None
    name, url = license_label(row.license)
    t960 = row.thumb_960
    thumb = t960.replace("/960px-", "/330px-") if "/960px-" in t960 else t960
    return {
        "kind": "commons", "src": t960, "thumb": thumb, "small": thumb,
        "width": _clean(row.width), "height": _clean(row.height),
        "credit": re.sub(r"\s*\((talk|discuss)[^)]*\)", "", _clean(row.artist) or _clean(row.credit) or "Wikimedia Commons contributor").strip(),
        "license": row.license, "license_name": name, "license_url": _clean(row.license_url) or url,
        "open": is_open(row.license), "page": row.page, "via": "Wikimedia Commons",
    }


def load() -> dict:
    allow_nc = CONFIG["allow_nc"]
    sp = pd.read_parquet(DATA / "species" / "mammals.parquet")
    release = json.loads((DATA / "species" / "mdd_release.json").read_text())
    xw = _read(DATA / "crosswalk" / "mammals_wikidata.parquet")
    taxa = _read(DATA / "media" / "inat_taxa.parquet")
    photos = _read(DATA / "media" / "inat_photos.parquet")
    commons = _read(DATA / "media" / "commons_images.parquet")
    leads = _read(DATA / "text" / "wikipedia_leads.parquet")
    ranges = _read(DATA / "ranges" / "mdd_ranges_h3.parquet")
    v12 = _read(DATA / "crosswalk" / "mdd_v1_2_ids.parquet")
    traits = _read(DATA / "traits" / "mammals.parquet")
    traits_by_id = {}
    if traits is not None:
        for r in traits.to_dict("records"):
            traits_by_id[r["id"]] = {k: _clean(v) for k, v in r.items()}

    sp = sp.merge(xw, on="id", how="left") if xw is not None else sp
    if taxa is not None:
        sp = sp.merge(taxa.drop(columns=["inat_id"]).drop_duplicates("id"), on="id", how="left")
        inat_ids = taxa.drop_duplicates("id").set_index("id")["inat_id"]
        sp["inat_id"] = sp["inat_id"].fillna(sp["id"].map(inat_ids))

    media: dict[int, list[dict]] = defaultdict(list)
    if commons is not None and xw is not None:
        cm = {r.file: commons_media(r) for r in commons.itertuples()}
        for r in sp.dropna(subset=["commons_image"]).itertuples():
            m = cm.get(r.commons_image)
            if m and (m["open"] or allow_nc):
                media[r.id].append({**m, "rank": -1})
    if photos is not None:
        for r in photos.sort_values(["id", "rank"]).itertuples():
            if r.open or allow_nc:
                media[r.id].append({**inat_media(r), "rank": int(r.rank)})

    lead_by_title = {}
    if leads is not None:
        lead_by_title = {r.enwiki: r for r in leads.itertuples()}

    range_by_id: dict[int, dict] = {}
    if ranges is not None and v12 is not None:
        name_to_id = dict(zip(v12["sciname_v1_2"], v12["id"]))
        current = set(sp["id"])
        for r in ranges.itertuples():
            mid = name_to_id.get(r.sciname)
            if mid in current:
                range_by_id[mid] = {"r3": list(r.cells_r3), "r4": list(r.cells_r4)}

    records = []
    for row in sp.itertuples():
        d = {k: _clean(getattr(row, k)) for k in sp.columns}
        for k in ("observations_count", "inat_common_name", "inat_id", "gbif_key_old", "qid",
                  "enwiki", "commons_image", "range_km2"):
            d.setdefault(k, None)
        for k in ("observations_count", "inat_id", "gbif_key_old"):
            if d[k] is not None:
                d[k] = int(d[k])
        ms = media.get(row.id, [])
        # Hero: open first, then Commons (curated for Wikipedia), then iNat's curated order.
        ms.sort(key=lambda m: (not m["open"], m["rank"]))
        seen, uniq = set(), []
        for m in ms:
            if m["src"] not in seen:
                seen.add(m["src"])
                uniq.append(m)
        d["media"] = uniq[:9]
        raw = (d.get("iucnStatus") or "NE").strip()
        code = raw[:2]
        d["status_as"] = raw.split("(as ", 1)[1].rstrip(")") if "(as " in raw else None
        d["iucnStatus"] = code if code in STATUS else "NE"
        code = d["iucnStatus"]
        d["status_name"], d["status_rank"] = STATUS.get(code, ("Not Evaluated", -1))
        if d.get("extinct") and code not in ("EX", "EW"):
            d["status_name"], d["status_rank"], d["iucnStatus"] = "Extinct", 7, "EX"
        lead = lead_by_title.get(d.get("enwiki"))
        d["wiki"] = None if lead is None else {
            "title": lead.title, "lead": lead.lead, "revid": _clean(lead.revid),
            "url": f"https://en.wikipedia.org/wiki/{lead.title.replace(' ', '_')}",
        }
        summary, wurl = d.get("wikipedia_summary"), d.get("wikipedia_url")
        if d["wiki"] is None and summary and wurl:
            text = re.sub(r"<[^>]+>", "", html.unescape(summary)).strip()
            title = unquote(wurl.rsplit("/", 1)[-1]).replace("_", " ")
            d["wiki"] = {"title": title, "lead": text, "revid": None, "url": wurl, "via": "iNaturalist"}
        d["traits"] = traits_by_id.get(row.id)
        rng = range_by_id.get(row.id)
        d["range"] = rng
        d["countries"] = [c.strip() for c in (d.get("countryDistribution") or "").split("|") if c.strip()]
        d["realms"] = [c.strip() for c in (d.get("biogeographicRealm") or "").split("|") if c.strip()]
        d["continents"] = [c.strip().replace(" (Continent)", "") for c in (d.get("continentDistribution") or "").split("|") if c.strip()]
        d["common"] = d.get("mainCommonName") or d.get("inat_common_name") or d["sciName"]
        d["other_common"] = [c.strip() for c in (d.get("otherCommonNames") or "").split("|") if c.strip()]
        d["nominal"] = [c.strip() for c in (d.get("nominalNames") or "").split("|") if c.strip()]
        d["tax_cites"] = [c.strip() for c in (d.get("taxonomyNotesCitation") or "").split("|") if c.strip()]
        d["dist_cites"] = [c.strip() for c in (d.get("distributionNotesCitation") or "").split("|") if c.strip()]
        records.append(d)
    return {"species": records, "release": release, "has_ranges": bool(range_by_id)}
