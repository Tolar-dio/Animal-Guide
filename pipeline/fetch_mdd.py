"""Fetch the Mammal Diversity Database release and normalise it.

Writes:
  data/species/mammals.parquet        one row per MDD v2.5 species
  data/species/mdd_release.json       version, date, DOI, counts
  data/species/mdd_changes.parquet    the v2.4 -> v2.5 diff (splits, lumps, renames)
  data/crosswalk/mdd_v1_2_ids.parquet v1.2 names -> MDD ids (joins the range maps)

The Zenodo record id is the only thing to change for a new release.
"""
from __future__ import annotations

import io
import tomllib

import pandas as pd

from common import DATA, Http, slugify, write_json

RECORD = "21654811"          # MDD v2.5
RANGE_RECORD = "6644198"     # Marsh et al. 2022 range maps, MDD v1.2 taxonomy

KEEP = [
    "id", "sciName", "phylosort", "mainCommonName", "otherCommonNames",
    "subclass", "infraclass", "magnorder", "superorder", "order", "suborder",
    "infraorder", "parvorder", "superfamily", "family", "subfamily", "tribe",
    "genus", "subgenus", "specificEpithet",
    "authoritySpeciesAuthor", "authoritySpeciesYear", "authorityParentheses",
    "originalNameCombination", "authoritySpeciesCitation", "authoritySpeciesLink",
    "typeVoucher", "typeKind", "typeLocality", "typeLocalityLatitude", "typeLocalityLongitude",
    "nominalNames", "subspecies", "taxonomyNotes", "taxonomyNotesCitation",
    "distributionNotes", "distributionNotesCitation", "subregionDistribution",
    "countryDistribution", "continentDistribution", "biogeographicRealm",
    "iucnStatus", "extinct", "domestic", "flagged",
]


def zenodo_files(http: Http, record: str) -> dict[str, str]:
    meta = http.json(f"https://zenodo.org/api/records/{record}")
    return {f["key"]: f["links"]["self"] for f in meta["files"]}


def main() -> None:
    http = Http(min_interval=0.5)
    files = zenodo_files(http, RECORD)
    release = tomllib.loads(http.get(files["release.toml"]).text)["metadata"]
    sp = pd.read_csv(io.BytesIO(http.get(files[release["mdd_file"]]).content))
    diff_key = next(k for k in files if k.startswith("Diff_v"))
    diff = pd.read_csv(io.BytesIO(http.get(files[diff_key]).content))

    sp = sp[KEEP].copy()
    sp["sciName"] = sp["sciName"].str.replace("_", " ")
    sp["slug"] = sp["sciName"].map(slugify)
    sp["authorityYear"] = pd.to_numeric(sp["authoritySpeciesYear"], errors="coerce").astype("Int64")
    sp["authority"] = sp.apply(_authority, axis=1)
    for col in ("extinct", "domestic", "flagged"):
        sp[col] = sp[col].fillna(0).astype(int).astype(bool)
    assert sp["slug"].is_unique, "slug collision"

    out = DATA / "species"
    out.mkdir(parents=True, exist_ok=True)
    sp.to_parquet(out / "mammals.parquet", index=False)
    diff.to_parquet(out / "mdd_changes.parquet", index=False)
    write_json(out / "mdd_release.json", {
        "version": release["version"],
        "release_date": release["release_date"],
        "citation": release.get("zenodo_citation"),
        "doi": f"10.5281/zenodo.{RECORD}",
        "species": int(len(sp)),
        "extinct": int(sp["extinct"].sum()),
        "domestic": int(sp["domestic"].sum()),
        "orders": int(sp["order"].nunique()),
        "families": int(sp["family"].nunique()),
        "genera": int(sp["genus"].nunique()),
    })

    rfiles = zenodo_files(http, RANGE_RECORD)
    v12 = pd.read_csv(io.BytesIO(http.get(rfiles["MDD_v1.2_all_6485species.csv"]).content), encoding="latin-1")
    v12 = pd.DataFrame({"sciname_v1_2": v12["sciName"].str.replace("_", " "), "id": v12["id"]})
    (DATA / "crosswalk").mkdir(parents=True, exist_ok=True)
    v12.to_parquet(DATA / "crosswalk" / "mdd_v1_2_ids.parquet", index=False)

    print(f"MDD {release['version']}: {len(sp)} species, {sp['order'].nunique()} orders, "
          f"{len(diff)} changes since previous release")


def _authority(row) -> str | None:
    author, year = row["authoritySpeciesAuthor"], row["authorityYear"]
    if pd.isna(author):
        return None
    s = f"{author}, {year}" if pd.notna(year) else str(author)
    return f"({s})" if row["authorityParentheses"] == 1 else s


if __name__ == "__main__":
    main()
