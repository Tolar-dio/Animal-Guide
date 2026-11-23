"""Bird species list from the eBird/Clements Checklist (current version).

Source: eBird taxonomy API (https://api.ebird.org/v2/ref/taxonomy/ebird),
the Clements Checklist of Birds of the World, CC BY 4.0. Only rows with
CATEGORY == "species" are kept (subspecies groups, hybrids and slashes are
eBird reporting units, not species).

AviList (the new unified global checklist, ~97.7% aligned with Clements)
blocks automated downloads; swap it in here if it publishes an open file.

Writes data/species/birds.parquet and data/species/birds_release.json.
"""
from __future__ import annotations

import io

import pandas as pd

from common import DATA, Http, slugify, write_json

TAXONOMY = "https://api.ebird.org/v2/ref/taxonomy/ebird?fmt=csv&locale=en"
VERSIONS = "https://api.ebird.org/v2/ref/taxonomy/versions"


def main() -> None:
    http = Http(min_interval=0.5)
    version = next(v["authorityVer"] for v in http.json(VERSIONS) if v.get("latest"))
    tax = pd.read_csv(io.BytesIO(http.get(TAXONOMY, timeout=300).content))
    sp = tax[tax["CATEGORY"] == "species"].copy()

    out = pd.DataFrame({
        "key": "b-" + sp["SPECIES_CODE"],
        "ebird_code": sp["SPECIES_CODE"],
        "sciName": sp["SCIENTIFIC_NAME"],
        "mainCommonName": sp["COMMON_NAME"],
        "phylosort": sp["TAXON_ORDER"].astype(float),
        "order": sp["ORDER"],
        "family": sp["FAMILY_SCI_NAME"],
        "family_common": sp["FAMILY_COM_NAME"],
        "genus": sp["SCIENTIFIC_NAME"].str.split(" ").str[0],
        "extinct": sp["EXTINCT"].fillna(False).astype(bool),
        "extinct_year": pd.to_numeric(sp["EXTINCT_YEAR"], errors="coerce").astype("Int64"),
    })
    out["slug"] = out["sciName"].map(slugify)
    out = out.sort_values("phylosort").reset_index(drop=True)
    assert out["slug"].is_unique and out["key"].is_unique

    (DATA / "species").mkdir(parents=True, exist_ok=True)
    out.to_parquet(DATA / "species" / "birds.parquet", index=False)
    write_json(DATA / "species" / "birds_release.json", {
        "name": "eBird/Clements Checklist of Birds of the World",
        "version": f"v{int(version) if float(version).is_integer() else version}",
        "license": "CC BY 4.0",
        "url": "https://www.birds.cornell.edu/clementschecklist/",
        "species": int(len(out)), "extinct": int(out["extinct"].sum()),
        "orders": int(out["order"].nunique()), "families": int(out["family"].nunique()),
        "genera": int(out["genus"].nunique()),
    })
    print(f"Birds (Clements {version}): {len(out)} species, {out['order'].nunique()} orders, "
          f"{out['family'].nunique()} families, {int(out['extinct'].sum())} extinct")


if __name__ == "__main__":
    main()
