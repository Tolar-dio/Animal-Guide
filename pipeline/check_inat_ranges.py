"""Audit iNaturalist modeled ranges against where the species is observed.

Since geomodel 2.21 the open range export drops some polygons that cross the
180th meridian. For a Palearctic bird that means the whole Eurasian range
vanishes and only island fragments remain (the house sparrow was drawn over
Australia and Hawaii). This script scores each suspect range by the share of
research-grade observation grid cells (iNat grid z0, ~5 degree cells) it
covers, and writes data/ranges/inat_audit.parquet. assemble.py drops modeled
ranges that fail, so the page falls back to observations instead of a wrong map.

Suspects: ranges with cells near the antimeridian, or that miss the centroid
of the expert range recorded in TetrapodTraits. Pass --all to score every range.
"""
from __future__ import annotations

import sys
import time

import h3
import pandas as pd

from common import CACHE, DATA, Http, write_json
import json

GRID = "https://api.inaturalist.org/v1/grid/0/0/0.grid.json?taxon_id={}&quality_grade=research&captive=false"
PASS = 0.5


def covered_set(cells):
    cov = set()
    for c in cells:
        p = h3.cell_to_parent(c, 1)
        cov.add(p)
        cov.update(h3.grid_disk(p, 1))
    return cov


def suspects(inat: pd.DataFrame, everything: bool) -> pd.DataFrame:
    rows = []
    for cls in ["mammals", "birds", "reptiles"]:
        cw = pd.read_parquet(DATA / f"crosswalk/{cls}_wikidata.parquet")[["key", "inat_id"]].dropna()
        cw["inat_id"] = cw.inat_id.astype("int64")
        tt = pd.read_parquet(DATA / f"traits/{cls}.parquet")[["key", "tt_lat", "tt_lon"]]
        m = cw.merge(inat, left_on="inat_id", right_on="taxon_id").merge(tt, on="key", how="left")
        for r in m.itertuples():
            cells = list(r.cells_r3)
            t180 = any(abs(h3.cell_to_latlng(c)[1]) > 170 for c in cells)
            miss = False
            if pd.notna(r.tt_lat):
                cov2 = set()
                for c in cells:
                    p = h3.cell_to_parent(c, 2)
                    cov2.add(p)
                    cov2.update(h3.grid_disk(p, 1))
                miss = h3.latlng_to_cell(r.tt_lat, r.tt_lon, 2) not in cov2
            if everything or t180 or miss:
                rows.append({"taxon_id": r.taxon_id, "name": r.name, "t180": t180, "miss": miss})
    return pd.DataFrame(rows).drop_duplicates("taxon_id")


def main():
    inat = pd.read_parquet(DATA / "ranges/inat_h3.parquet")
    inat["taxon_id"] = inat.taxon_id.astype("int64")
    cells = dict(zip(inat.taxon_id, inat.cells_r3))
    sus = suspects(inat, "--all" in sys.argv)
    print(f"{len(sus)} ranges to audit", flush=True)
    http = Http(min_interval=2.5)
    cache = CACHE / "inat" / "grid0"
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for i, r in enumerate(sus.itertuples()):
        f = cache / f"{r.taxon_id}.json"
        if f.exists():
            pts = json.loads(f.read_text())
        else:
            d = http.json(GRID.format(r.taxon_id))
            pts = [[v["latitude"], v["longitude"]] for v in d.get("data", {}).values()]
            write_json(f, pts)
        pts = [p for p in pts if -70 < p[0] < 80]
        cov = covered_set(cells[r.taxon_id])
        hit = sum(h3.latlng_to_cell(lat, lng, 1) in cov for lat, lng in pts)
        score = hit / len(pts) if pts else None
        out.append({"taxon_id": r.taxon_id, "name": r.name, "t180": r.t180, "miss": r.miss,
                    "obs_cells": len(pts), "score": score,
                    "ok": score is None or len(pts) < 5 or score >= PASS})
        if i % 100 == 0:
            print(f"  {i}/{len(sus)}", flush=True)
    df = pd.DataFrame(out)
    df.to_parquet(DATA / "ranges/inat_audit.parquet", index=False)
    print(f"audited {len(df)}; failed {(~df.ok).sum()}")
    print(df[~df.ok].sort_values("score").head(40).to_string())


if __name__ == "__main__":
    main()
