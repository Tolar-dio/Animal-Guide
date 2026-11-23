"""Life-history and ecology traits from TetrapodTraits v2.0.0 (Moura et al. 2024).

Zenodo record 17200405, CC BY 4.0. 33,281 tetrapods; about 5,900 mammals.
Body mass and longevity are maximum reported values, so the site phrases
them as "up to". Values the authors imputed (estimated from relatives) are
flagged and shown as estimates. Each value keeps its source citation.

Writes data/traits/<class>.parquet keyed by `key` for mammals, birds and reptiles.
"""
from __future__ import annotations

import io

import pandas as pd

from common import DATA, Http

RECORD = "17200405"
FILE = "TetrapodTraits_v2.0.0.csv"
KEEP = {
    "BodyMass_g": "mass_g", "ImputedMass": "mass_imputed", "SourceBodyMass": "mass_source",
    "BodyLength_mm": "length_mm", "LengthMeasure": "length_measure", "ImputedLength": "length_imputed",
    "SourceBodyLength": "length_source",
    "Nocturnality": "nocturnality", "ImputedActTime": "act_imputed", "SourceActTime": "act_source",
    "Fos": "fossorial", "Ter": "terrestrial", "Aqu": "aquatic", "Arb": "arboreal", "Aer": "aerial",
    "ImputedHabitat": "habitat_imputed",
    "DietInv": "diet_invertebrates", "DietVend": "diet_warm_blooded", "DietVect": "diet_cold_blooded",
    "DietVfish": "diet_fish", "DietVunk": "diet_vertebrates", "DietScav": "diet_carrion",
    "DietFruit": "diet_fruit", "DietNect": "diet_nectar", "DietSeed": "diet_seeds", "DietPlant": "diet_plants",
    "SourceDiet": "diet_source",
    "MaxLongevity": "longevity_yr", "SourceLongevity": "longevity_source",
    "LitterSize": "litter_size", "SourceLitterSize": "litter_source",
    "Elevation": "elevation_m", "Insularity": "insular",
}


def main() -> None:
    http = Http(min_interval=0.5)
    meta = http.json(f"https://zenodo.org/api/records/{RECORD}")
    url = next(f["links"]["self"] for f in meta["files"] if f["key"] == FILE)
    tt_all = pd.read_csv(io.BytesIO(http.get(url, timeout=600).content), low_memory=False)
    tt_all = tt_all.rename(columns={"Scientific.Name": "sciName"})
    keep = list(KEEP) + ["Authority", "YearOfDescription", "AssessedStatus"]

    for cls, tclass in (("mammals", "Mammalia"), ("birds", "Aves"), ("reptiles", "Reptilia")):
        tt = tt_all[tt_all["Class"] == tclass]
        sp = pd.read_parquet(DATA / "species" / f"{cls}.parquet")
        if "key" not in sp:
            sp["key"] = "m" + sp["id"].astype(str)
        direct = sp[["key", "sciName"] + (["id"] if "id" in sp else [])].merge(tt, on="sciName", how="inner")
        parts = [direct]
        if cls == "mammals":
            # TetrapodTraits follows an earlier MDD taxonomy: try v1.2 names -> MDD id.
            v12 = pd.read_parquet(DATA / "crosswalk" / "mdd_v1_2_ids.parquet")
            rest = tt[~tt["sciName"].isin(direct["sciName"])].merge(
                v12.rename(columns={"sciname_v1_2": "sciName"}), on="sciName", how="inner")
            rest = rest.merge(sp[["id", "key"]], on="id", how="inner")
            parts.append(rest[~rest["key"].isin(direct["key"])])
        both = pd.concat(parts, ignore_index=True).drop_duplicates("key")
        out = both[["key"] + keep].rename(columns={**KEEP, "Authority": "tt_authority",
                                                  "YearOfDescription": "tt_year", "AssessedStatus": "tt_status"})
        out["source_dataset"] = "TetrapodTraits v2.0.0"
        (DATA / "traits").mkdir(parents=True, exist_ok=True)
        out.to_parquet(DATA / "traits" / f"{cls}.parquet", index=False)
        print(f"TetrapodTraits {cls}: {len(out)}/{len(sp)} matched by name "
              f"({len(direct)} direct); {int((out['mass_imputed'] == 0).sum())} with a measured body mass")


if __name__ == "__main__":
    main()
