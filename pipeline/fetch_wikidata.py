"""Build the identifier crosswalk for mammals from Wikidata (CC0).

Matches MDD species to Wikidata by the "ASM Mammal Diversity Database ID"
property (P12560), then by exact scientific name (P225) for the rest.

Writes data/crosswalk/mammals_wikidata.parquet with one row per MDD id:
  qid, inat_id (P3151), gbif_key_old (P846), gbif_key (P14607), iucn_id (P627),
  commons_image (P18), enwiki (English Wikipedia title), match ("mdd_id"|"name")
"""
from __future__ import annotations

import pandas as pd

from common import CONFIG, DATA, Http, chunks

SPARQL = "https://query.wikidata.org/sparql"

FIELDS = """
  OPTIONAL { ?item wdt:P3151 ?inat. }
  OPTIONAL { ?item wdt:P846 ?gbifOld. }
  OPTIONAL { ?item wdt:P14607 ?gbif. }
  OPTIONAL { ?item wdt:P627 ?iucn. }
  OPTIONAL { ?item wdt:P18 ?image. }
  OPTIONAL { ?wp schema:about ?item; schema:isPartOf <https://en.wikipedia.org/>; schema:name ?enwiki. }
"""
AGG = """(SAMPLE(?inat) AS ?inat) (SAMPLE(?gbifOld) AS ?gbifOld) (SAMPLE(?gbif) AS ?gbif)
 (SAMPLE(?iucn) AS ?iucn) (SAMPLE(?image) AS ?image) (SAMPLE(?enwiki) AS ?enwiki)"""

BY_ID = f"""SELECT ?item ?mdd {AGG} WHERE {{
  ?item wdt:P12560 ?mdd.
  {FIELDS}
}} GROUP BY ?item ?mdd"""

BY_NAME = """SELECT ?item ?name {agg} WHERE {{
  VALUES ?name {{ {values} }}
  ?item wdt:P225 ?name; wdt:P105 wd:Q7432.
  {fields}
}} GROUP BY ?item ?name"""


def run(http: Http, query: str) -> list[dict]:
    r = http.get(SPARQL, params={"query": query, "format": "json"}, timeout=180)
    rows = []
    for b in r.json()["results"]["bindings"]:
        rows.append({k: v["value"] for k, v in b.items()})
    return rows


def tidy(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ("inat", "gbifOld", "gbif", "iucn", "image", "enwiki"):
        if col not in df:
            df[col] = None
    df["qid"] = df["item"].str.rsplit("/", n=1).str[-1]
    df["commons_image"] = df["image"].str.replace(
        "http://commons.wikimedia.org/wiki/Special:FilePath/", "", regex=False)
    df["commons_image"] = df["commons_image"].map(
        lambda s: requests_unquote(s) if isinstance(s, str) else None)
    return df.rename(columns={"inat": "inat_id", "gbifOld": "gbif_key_old", "gbif": "gbif_key",
                              "iucn": "iucn_id"})


def requests_unquote(s: str) -> str:
    from urllib.parse import unquote
    return unquote(s)


def main() -> None:
    http = Http(min_interval=CONFIG["rate"]["wikidata"])
    sp = pd.read_parquet(DATA / "species" / "mammals.parquet", columns=["id", "sciName"])

    by_id = tidy(run(http, BY_ID))
    by_id["id"] = pd.to_numeric(by_id["mdd"], errors="coerce")
    by_id = by_id.dropna(subset=["id"]).astype({"id": int})
    # One MDD id can sit on two items (e.g. a species and a redirect). Prefer the item with more links.
    by_id["score"] = by_id[["inat_id", "gbif_key_old", "iucn_id", "image", "enwiki"]].notna().sum(axis=1)
    by_id = by_id.sort_values("score", ascending=False).drop_duplicates("id")
    matched = sp.merge(by_id, on="id", how="inner")
    matched["match"] = "mdd_id"

    missing = sp[~sp["id"].isin(matched["id"])]
    by_name_rows = []
    for batch in chunks(missing["sciName"].tolist(), 150):
        values = " ".join(f'"{n}"' for n in batch)
        by_name_rows += run(http, BY_NAME.format(agg=AGG, values=values, fields=FIELDS))
    if by_name_rows:
        by_name = tidy(by_name_rows).rename(columns={"name": "sciName"})
        by_name["score"] = by_name[["inat_id", "gbif_key_old", "iucn_id", "image", "enwiki"]].notna().sum(axis=1)
        by_name = by_name.sort_values("score", ascending=False).drop_duplicates("sciName")
        named = missing.merge(by_name, on="sciName", how="inner")
        named["match"] = "name"
        matched = pd.concat([matched, named], ignore_index=True)

    cols = ["id", "qid", "inat_id", "gbif_key_old", "gbif_key", "iucn_id", "commons_image", "enwiki", "match"]
    out = matched[cols].copy()
    for c in ("inat_id", "gbif_key_old"):
        out[c] = pd.to_numeric(out[c], errors="coerce").astype("Int64")
    (DATA / "crosswalk").mkdir(parents=True, exist_ok=True)
    out.to_parquet(DATA / "crosswalk" / "mammals_wikidata.parquet", index=False)

    n = len(sp)
    print(f"Wikidata: {len(out)}/{n} matched ({(out['match'] == 'mdd_id').sum()} by MDD id); "
          f"iNat {out['inat_id'].notna().sum()}, GBIF {out['gbif_key_old'].notna().sum()}, "
          f"IUCN {out['iucn_id'].notna().sum()}, image {out['commons_image'].notna().sum()}, "
          f"enwiki {out['enwiki'].notna().sum()}")


if __name__ == "__main__":
    main()
