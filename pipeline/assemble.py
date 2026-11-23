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
from collections import Counter, defaultdict
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


CLASS_INFO = {
    "mammals": {"label": "Mammals", "singular": "mammal", "iconic": "Mammalia",
                "release": "mdd_release.json", "list": "Mammal Diversity Database"},
    "birds": {"label": "Birds", "singular": "bird", "iconic": "Aves",
              "release": "birds_release.json", "list": "eBird/Clements Checklist"},
    "reptiles": {"label": "Reptiles", "singular": "reptile", "iconic": "Reptilia",
                 "release": "reptiles_release.json", "list": "The Reptile Database"},
}
RANGE_SOURCES = {
    "mdd": {"kind": "expert", "name": "Marsh et al. 2022 (MDD expert maps)", "url": "https://doi.org/10.5281/zenodo.6644198", "license": "CC BY 4.0"},
    "gard": {"kind": "expert", "name": "GARD 1.7 (Caetano et al. 2022)", "url": "https://doi.org/10.5281/zenodo.6499637", "license": "CC0"},
    "inat": {"kind": "modeled", "name": "iNaturalist Open Range Maps (Geomodel)", "url": "https://www.inaturalist.org/pages/range_maps", "license": "CC BY 4.0"},
}


def _title_authority(a: str | None) -> str | None:
    """'(LESSON & DELATTRE, 1839)' -> '(Lesson & Delattre, 1839)'."""
    if not a or not isinstance(a, str):
        return None
    return re.sub(r"[A-ZÀ-Ý][A-ZÀ-Ý'\-]+", lambda m: m.group(0).capitalize(), a).replace(" And ", " and ").replace(" Et ", " et ")


def _media_for(sp: pd.DataFrame, commons, photos, allow_nc: bool) -> dict:
    media: dict[str, list[dict]] = defaultdict(list)
    if commons is not None and "commons_image" in sp:
        cm = {r.file: commons_media(r) for r in commons.itertuples()}
        for r in sp.dropna(subset=["commons_image"]).itertuples():
            m = cm.get(r.commons_image)
            if m and (m["open"] or allow_nc):
                media[r.key].append({**m, "rank": -1})
    if photos is not None:
        for r in photos.sort_values(["key", "rank"]).itertuples():
            if r.open or allow_nc:
                media[r.key].append({**inat_media(r), "rank": int(r.rank)})
    return media


def _ranges() -> dict:
    """Load every range table once: lookups by MDD id, sciname (GARD) and iNat taxon id."""
    out = {"mdd": {}, "gard": {}, "inat": {}, "inat_name": {}}
    mdd = _read(DATA / "ranges" / "mdd_ranges_h3.parquet")
    v12 = _read(DATA / "crosswalk" / "mdd_v1_2_ids.parquet")
    if mdd is not None and v12 is not None:
        name_to_id = dict(zip(v12["sciname_v1_2"], v12["id"]))
        for r in mdd.itertuples():
            if r.sciname in name_to_id:
                out["mdd"][name_to_id[r.sciname]] = (list(r.cells_r3), list(r.cells_r4))
    gard = _read(DATA / "ranges" / "gard_h3.parquet")
    if gard is not None:
        out["gard"] = {r.sciname: (list(r.cells_r3), list(r.cells_r4)) for r in gard.itertuples()}
    inat = _read(DATA / "ranges" / "inat_h3.parquet")
    if inat is not None:
        out["inat"] = {int(r.taxon_id): (list(r.cells_r3), list(r.cells_r4)) for r in inat.itertuples()}
        out["inat_name"] = {r.name: (list(r.cells_r3), list(r.cells_r4)) for r in inat.itertuples()}
    cc = _read(DATA / "ranges" / "cell_countries.parquet")
    out["countries"] = {r.cell: list(r.countries) for r in cc.itertuples()} if cc is not None else {}
    return out


def load() -> dict:
    allow_nc = CONFIG["allow_nc"]
    rng = _ranges()
    records, releases = [], {}
    for cls, info in CLASS_INFO.items():
        path = DATA / "species" / f"{cls}.parquet"
        if not path.exists():
            continue
        sp = pd.read_parquet(path)
        if "key" not in sp:
            sp["key"] = "m" + sp["id"].astype(str)
        releases[cls] = json.loads((DATA / "species" / info["release"]).read_text())
        xw = _read(DATA / "crosswalk" / f"{cls}_wikidata.parquet")
        if xw is not None:
            sp = sp.merge(xw.drop(columns=[c for c in xw.columns if c in sp.columns and c != "key"]), on="key", how="left")
        taxa = _read(DATA / "media" / f"{cls}_inat_taxa.parquet")
        if taxa is not None:
            t = taxa.drop_duplicates("key")
            sp = sp.merge(t.drop(columns=[c for c in t.columns if c in sp.columns and c != "key"] + ["inat_id"], errors="ignore"),
                          on="key", how="left")
            sp["inat_id"] = sp.get("inat_id", pd.Series(index=sp.index, dtype="Int64")).fillna(sp["key"].map(t.set_index("key")["inat_id"]))
        media = _media_for(sp, _read(DATA / "media" / f"{cls}_commons_images.parquet"),
                           _read(DATA / "media" / f"{cls}_inat_photos.parquet"), allow_nc)
        leads = _read(DATA / "text" / f"{cls}_wikipedia_leads.parquet")
        lead_by_title = {r.enwiki: r for r in leads.itertuples()} if leads is not None else {}
        traits = _read(DATA / "traits" / f"{cls}.parquet")
        traits_by_key = {r["key"]: {k: _clean(v) for k, v in r.items()} for r in traits.to_dict("records")} if traits is not None else {}

        for row in sp.itertuples():
            d = {k: _clean(getattr(row, k)) for k in sp.columns}
            d["cls"], d["class_label"], d["singular"] = cls, info["label"], info["singular"]
            d["family"] = d.get("family") or "Incertae sedis"
            d["order"] = d.get("order") or "Incertae sedis"
            for k in ("observations_count", "inat_common_name", "inat_id", "gbif_key_old", "qid", "enwiki",
                      "commons_image", "range_km2", "mainCommonName", "authority", "authorityYear",
                      "family_common", "ebird_code", "iucn_status", "id"):
                d.setdefault(k, None)
            for k in ("observations_count", "inat_id", "gbif_key_old", "authorityYear"):
                if d[k] is not None:
                    d[k] = int(d[k])
            t = traits_by_key.get(row.key) or {}
            d["traits"] = t or None

            ms = media.get(row.key, [])
            ms.sort(key=lambda m: (not m["open"], m["rank"]))
            seen, uniq = set(), []
            for m in ms:
                if m["src"] not in seen:
                    seen.add(m["src"])
                    uniq.append(m)
            d["media"] = uniq[:9]

            raw = (d.get("iucnStatus") or d.get("iucn_status") or t.get("tt_status") or "NE").strip()
            code = raw[:2]
            d["status_as"] = raw.split("(as ", 1)[1].rstrip(")") if "(as " in raw else None
            d["iucnStatus"] = code if code in STATUS else "NE"
            code = d["iucnStatus"]
            d["status_name"], d["status_rank"] = STATUS.get(code, ("Not Evaluated", -1))
            if d.get("extinct") and code not in ("EX", "EW"):
                d["status_name"], d["status_rank"], d["iucnStatus"] = "Extinct", 7, "EX"

            if not d["authority"] and t.get("tt_authority"):
                d["authority"] = _title_authority(t["tt_authority"])
                d["authorityYear"] = int(t["tt_year"]) if t.get("tt_year") else None

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

            # Range: expert first, then iNaturalist's modeled range.
            cells, src = None, None
            if cls == "mammals" and d["id"] in rng["mdd"]:
                cells, src = rng["mdd"][d["id"]], "mdd"
            elif cls == "reptiles" and d["sciName"] in rng["gard"]:
                cells, src = rng["gard"][d["sciName"]], "gard"
            if cells is None and d["inat_id"] in rng["inat"]:
                cells, src = rng["inat"][d["inat_id"]], "inat"
            if cells is None and d["sciName"] in rng["inat_name"]:
                cells, src = rng["inat_name"][d["sciName"]], "inat"
            d["range"] = {"r3": cells[0], "r4": cells[1], "source": src, **RANGE_SOURCES[src]} if cells else None

            if d.get("countryDistribution"):
                d["countries"] = [c.strip() for c in d["countryDistribution"].split("|") if c.strip()]
                d["countries_approx"] = False
            elif d["range"]:
                hits = Counter()
                for c in d["range"]["r3"]:
                    hits.update(rng["countries"].get(c, ()))
                # Modeled ranges carry stray hexagons; ask for two before naming a country.
                need = 2 if d["range"]["kind"] == "modeled" and len(d["range"]["r3"]) >= 10 else 1
                d["countries"], d["countries_approx"] = sorted(k for k, v in hits.items() if v >= need), True
            else:
                d["countries"], d["countries_approx"] = [], False
            d["realms"] = [c.strip() for c in (d.get("biogeographicRealm") or "").split("|") if c.strip()]
            d["continents"] = [c.strip().replace(" (Continent)", "") for c in (d.get("continentDistribution") or "").split("|") if c.strip()]
            d["common"] = d.get("mainCommonName") or d.get("inat_common_name") or d["sciName"]
            d["has_common"] = d["common"] != d["sciName"]
            d["other_common"] = [c.strip() for c in (d.get("otherCommonNames") or "").split("|") if c.strip()]
            d["nominal"] = [c.strip() for c in (d.get("nominalNames") or "").split("|") if c.strip()]
            d["tax_cites"] = [c.strip() for c in (d.get("taxonomyNotesCitation") or "").split("|") if c.strip()]
            d["dist_cites"] = [c.strip() for c in (d.get("distributionNotesCitation") or "").split("|") if c.strip()]
            records.append(d)

    # Slugs must be unique across classes (rare homonyms get a class suffix).
    seen: dict[str, int] = {}
    for d in records:
        if d["slug"] in seen:
            d["slug"] = f"{d['slug']}-{d['singular']}"
        seen[d["slug"]] = 1
    return {"species": records, "releases": releases, "release": releases.get("mammals"),
            "classes": {k: v for k, v in CLASS_INFO.items() if k in releases},
            "has_ranges": any(d["range"] for d in records)}
