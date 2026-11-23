"""Build the identifier crosswalk from Wikidata (CC0) for one class.

  python fetch_wikidata.py mammals|birds|reptiles   (default: all three)

Matching order:
  mammals   "ASM Mammal Diversity Database ID" (P12560), then exact name (P225)
  birds     "eBird taxon ID" (P3444), then exact name
  reptiles  exact name

Writes data/crosswalk/<class>_wikidata.parquet keyed by `key`:
  qid, inat_id (P3151), gbif_key_old (P846), gbif_key (P14607), iucn_id (P627),
  iucn_status (P141, as a two-letter code), commons_image (P18), enwiki, match
"""
from __future__ import annotations

import sys
from urllib.parse import unquote

import pandas as pd

from common import CONFIG, DATA, Http, chunks

SPARQL = "https://query.wikidata.org/sparql"
STATUS = {"Q211005": "LC", "Q719675": "NT", "Q278113": "VU", "Q11394": "EN", "Q219127": "CR",
          "Q239509": "EW", "Q237350": "EX", "Q3245245": "DD"}

FIELDS = """
  OPTIONAL { ?item wdt:P3151 ?inat. }
  OPTIONAL { ?item wdt:P846 ?gbifOld. }
  OPTIONAL { ?item wdt:P14607 ?gbif. }
  OPTIONAL { ?item wdt:P627 ?iucn. }
  OPTIONAL { ?item wdt:P141 ?status. }
  OPTIONAL { ?item wdt:P18 ?image. }
  OPTIONAL { ?wp schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?enwiki. }
"""
AGG = """(SAMPLE(?inat) AS ?inat) (SAMPLE(?gbifOld) AS ?gbifOld) (SAMPLE(?gbif) AS ?gbif)
 (SAMPLE(?iucn) AS ?iucn) (SAMPLE(?status) AS ?status) (SAMPLE(?image) AS ?image) (SAMPLE(?enwiki) AS ?enwiki)"""

BY_PROP = """SELECT ?item ?ext {agg} WHERE {{
  ?item wdt:{prop} ?ext.
  {fields}
}} GROUP BY ?item ?ext"""

BY_NAME = """SELECT ?item ?name {agg} WHERE {{
  VALUES ?name {{ {values} }}
  ?item wdt:P225 ?name; wdt:P105 wd:Q7432.
  {fields}
}} GROUP BY ?item ?name"""

# class -> (species file, column holding the external id, Wikidata property)
CLASSES = {
    "mammals": ("mammals.parquet", "id", "P12560"),
    "birds": ("birds.parquet", "ebird_code", "P3444"),
    "reptiles": ("reptiles.parquet", None, None),
}
SCORE_COLS = ["inat_id", "gbif_key_old", "iucn_id", "image", "enwiki"]


def run(http: Http, query: str) -> list[dict]:
    r = http.get(SPARQL, params={"query": query, "format": "json"}, timeout=300)
    return [{k: v["value"] for k, v in b.items()} for b in r.json()["results"]["bindings"]]


def tidy(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ("inat", "gbifOld", "gbif", "iucn", "status", "image", "enwiki"):
        if col not in df:
            df[col] = None
    df["qid"] = df["item"].str.rsplit("/", n=1).str[-1]
    df["commons_image"] = df["image"].map(
        lambda s: unquote(s.replace("http://commons.wikimedia.org/wiki/Special:FilePath/", ""))
        if isinstance(s, str) else None)
    df["iucn_status"] = df["status"].map(
        lambda s: STATUS.get(s.rsplit("/", 1)[-1]) if isinstance(s, str) else None)
    df = df.rename(columns={"inat": "inat_id", "gbifOld": "gbif_key_old", "gbif": "gbif_key", "iucn": "iucn_id"})
    df["score"] = df[SCORE_COLS].notna().sum(axis=1)
    return df


def crosswalk(cls: str, http: Http) -> None:
    fname, ext_col, prop = CLASSES[cls]
    sp = pd.read_parquet(DATA / "species" / fname)
    if "key" not in sp:
        sp["key"] = "m" + sp["id"].astype(str)
    matched = pd.DataFrame()
    if prop:
        by_prop = tidy(run(http, BY_PROP.format(agg=AGG, prop=prop, fields=FIELDS)))
        if ext_col == "id":
            by_prop["ext"] = pd.to_numeric(by_prop["ext"], errors="coerce")
            by_prop = by_prop.dropna(subset=["ext"]).astype({"ext": int})
        by_prop = by_prop.sort_values("score", ascending=False).drop_duplicates("ext")
        matched = sp.merge(by_prop, left_on=ext_col, right_on="ext", how="inner")
        matched["match"] = prop

    missing = sp[~sp["key"].isin(matched["key"])] if len(matched) else sp
    rows = []
    for batch in chunks(missing["sciName"].tolist(), 150):
        rows += run(http, BY_NAME.format(agg=AGG, values=" ".join(f'"{n}"' for n in batch), fields=FIELDS))
    if rows:
        by_name = tidy(rows).rename(columns={"name": "sciName"})
        by_name = by_name.sort_values("score", ascending=False).drop_duplicates("sciName")
        named = missing.merge(by_name, on="sciName", how="inner")
        named["match"] = "name"
        matched = pd.concat([matched, named], ignore_index=True)

    cols = ["key", "qid", "inat_id", "gbif_key_old", "gbif_key", "iucn_id", "iucn_status",
            "commons_image", "enwiki", "match"]
    if "id" in sp:
        cols.insert(0, "id")
    out = matched[cols].drop_duplicates("key").copy()
    for c in ("inat_id", "gbif_key_old"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
    (DATA / "crosswalk").mkdir(parents=True, exist_ok=True)
    out.to_parquet(DATA / "crosswalk" / f"{cls}_wikidata.parquet", index=False)
    print(f"Wikidata {cls}: {len(out)}/{len(sp)} matched "
          f"({(out['match'] != 'name').sum()} by id); iNat {out['inat_id'].notna().sum()}, "
          f"GBIF {out['gbif_key_old'].notna().sum()}, status {out['iucn_status'].notna().sum()}, "
          f"image {out['commons_image'].notna().sum()}, enwiki {out['enwiki'].notna().sum()}")


def main() -> None:
    http = Http(min_interval=CONFIG["rate"]["wikidata"])
    for cls in (sys.argv[1:] or list(CLASSES)):
        crosswalk(cls, http)


if __name__ == "__main__":
    main()
