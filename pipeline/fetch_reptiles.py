"""Reptile species list from The Reptile Database via ChecklistBank.

Source: ChecklistBank dataset 1008 (The Reptile Database, Peter Uetz et al.),
CC BY 4.0, exported as ColDP. Accepted species only; order, suborder, family,
subfamily and genus come from walking each name's parent links.

Writes data/species/reptiles.parquet and data/species/reptiles_release.json.
"""
from __future__ import annotations

import io
import zipfile

import pandas as pd
import yaml

from common import DATA, Http, slugify, write_json

DATASET = 1008
EXPORT = f"https://api.checklistbank.org/dataset/{DATASET}/export.zip?format=ColDP"
RANKS = ("class", "order", "suborder", "superfamily", "family", "subfamily", "genus")


def main() -> None:
    http = Http(min_interval=0.5)
    z = zipfile.ZipFile(io.BytesIO(http.get(EXPORT, timeout=600).content))
    meta = yaml.safe_load(z.read("metadata.yaml"))
    nu = pd.read_csv(z.open("NameUsage.tsv"), sep="\t", low_memory=False, dtype=str)
    nu.columns = [c.removeprefix("col:") for c in nu.columns]
    by_id = nu.set_index("ID")

    def lineage(parent: str) -> dict:
        out, seen = {}, set()
        while isinstance(parent, str) and parent in by_id.index and parent not in seen:
            seen.add(parent)
            r = by_id.loc[parent]
            if r["rank"] in RANKS and r["rank"] not in out:
                out[r["rank"]] = r["scientificName"]
            parent = r["parentID"]
        return out

    sp = nu[(nu["rank"] == "species") & (nu["status"].isin(["accepted", "provisionally accepted"]))]
    rows = []
    for r in sp.itertuples():
        lin = lineage(r.parentID)
        auth = r.authorship if isinstance(r.authorship, str) else None
        year = None
        if auth:
            digits = [t.strip("(),") for t in auth.replace(",", " ").split() if t.strip("(),").isdigit()]
            year = int(digits[-1]) if digits else None
        rows.append({
            "key": "r-" + r.ID, "col_id": r.ID, "sciName": r.scientificName,
            "authority": auth, "authorityYear": year,
            "order": lin.get("order"), "suborder": lin.get("suborder"),
            "superfamily": lin.get("superfamily"), "family": lin.get("family"),
            "subfamily": lin.get("subfamily"), "genus": lin.get("genus") or r.scientificName.split(" ")[0],
        })
    out = pd.DataFrame(rows)
    out["authorityYear"] = out["authorityYear"].astype("Int64")
    out["slug"] = out["sciName"].map(slugify)
    out = out.sort_values(["order", "family", "sciName"]).reset_index(drop=True)
    out["phylosort"] = range(len(out))
    out = out.drop_duplicates("slug")

    out.to_parquet(DATA / "species" / "reptiles.parquet", index=False)
    write_json(DATA / "species" / "reptiles_release.json", {
        "name": "The Reptile Database", "version": str(meta.get("version")),
        "issued": str(meta.get("issued")), "doi": meta.get("versionDoi") or meta.get("doi"),
        "license": "CC BY 4.0", "url": "http://www.reptile-database.org/",
        "species": int(len(out)), "orders": int(out["order"].nunique()),
        "families": int(out["family"].nunique()), "genera": int(out["genus"].nunique()),
    })
    print(f"Reptiles (Reptile Database {meta.get('version')}): {len(out)} species, "
          f"{out['order'].nunique()} orders, {out['family'].nunique()} families; "
          f"missing order: {out['order'].isna().sum()}, family: {out['family'].isna().sum()}")


if __name__ == "__main__":
    main()
