"""Convert open range datasets to H3 cells.

  python build_ranges_open.py gard   GARD 1.7 reptile ranges (Caetano et al. 2022), CC0,
                                     Zenodo record 6499637 (10,914 terrestrial reptiles)
  python build_ranges_open.py inat   iNaturalist Open Range Maps (Geomodel), CC BY 4.0,
                                     monthly; modeled from observations on an H3-4 grid

Outputs data/ranges/gard_h3.parquet (keyed by sciname) and
data/ranges/inat_h3.parquet (keyed by iNat taxon_id), with cells_r3 and,
for small ranges, cells_r4.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import shapely
from shapely.geometry import mapping

from build_ranges_h3 import SMALL_R3, geom_to_cells, log, part_to_cells
from common import CACHE, DATA

GARD_RECORD = "https://zenodo.org/api/records/6499637"
INAT_BASE = "https://inaturalist-open-data.s3.amazonaws.com/geomodel/geopackages/latest"
INAT_GROUPS = ["Mammalia", "Aves", "Reptilia"]


def fetch(url: str, path: Path, size: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    for _ in range(8):
        if path.exists() and (size is None or path.stat().st_size == size):
            return
        subprocess.run(["curl", "-sL", "-C", "-", "--retry", "5", "-o", str(path), url])
        if size is None:
            return
    raise RuntimeError(f"could not download {url}")


def stream(path: Path, columns: list[str]):
    with pyogrio.open_arrow(path, batch_size=1, columns=columns, use_pyarrow=True) as (meta, reader):
        gcol = meta["geometry_name"] or "wkb_geometry"
        for batch in reader:
            yield {c: batch.column(c)[0].as_py() for c in columns}, shapely.from_wkb(batch.column(gcol)[0].as_py())


def gard() -> None:
    cache = CACHE / "gard"
    meta = json.loads(subprocess.run(["curl", "-sL", GARD_RECORD], capture_output=True, check=True).stdout)
    for f in meta["files"]:
        if f["key"].startswith("Gard_1_7_ranges."):
            fetch(f["links"]["self"], cache / f["key"], f["size"])
    rows = {}
    for i, (attrs, geom) in enumerate(stream(cache / "Gard_1_7_ranges.shp", ["binomial", "family"])):
        name = attrs["binomial"]
        r3 = geom_to_cells(geom, 3)
        r4 = geom_to_cells(geom, 4) if len(r3) < SMALL_R3 else set()
        prev = rows.get(name)
        if prev:
            r3 |= set(prev["r3"]); r4 |= set(prev["r4"])
        rows[name] = {"r3": sorted(r3), "r4": sorted(r4), "family": attrs["family"]}
        if i % 1000 == 0:
            log(f"  GARD {i}")
    write("gard_h3.parquet", {"sciname": list(rows)}, rows, "gard_1_7")


def inat() -> None:
    cache = CACHE / "inatranges"
    listing = subprocess.run(["curl", "-s", "https://inaturalist-open-data.s3.amazonaws.com/?list-type=2&prefix=geomodel/geopackages/latest/"],
                             capture_output=True, check=True).stdout.decode()
    rows = {}
    for group in INAT_GROUPS:
        names = sorted(set(n for n in __import__("re").findall(r"<Key>geomodel/geopackages/latest/([^<]+\.gpkg)</Key>", listing)
                           if n.startswith(f"iNaturalist_geomodel_{group}")))
        for name in names:
            path = cache / name
            fetch(f"{INAT_BASE}/{name}", path)
            log(f"  reading {name}")
            for attrs, geom in stream(path, ["taxon_id", "name", "rank"]):
                if attrs["rank"] != "species" or geom is None or geom.is_empty:
                    continue
                # Geomodel polygons are unions of H3-4 cells, so cell centres recover them exactly.
                r4 = set()
                for part in getattr(geom, "geoms", [geom]):
                    try:
                        r4 |= part_to_cells(part, 4, contain="center")
                    except Exception:
                        c = part.representative_point()
                        r4.add(h3.latlng_to_cell(c.y, c.x, 4))
                r3 = {h3.cell_to_parent(c, 3) for c in r4}
                rows[attrs["taxon_id"]] = {"r3": sorted(r3), "r4": sorted(r4) if len(r3) < SMALL_R3 else [],
                                           "name": attrs["name"], "group": group}
    write("inat_h3.parquet", {"taxon_id": list(rows), "name": [v["name"] for v in rows.values()],
                              "group": [v["group"] for v in rows.values()]}, rows, "inat_geomodel")


def write(fname: str, cols: dict, rows: dict, source: str) -> None:
    vals = list(rows.values())
    table = pa.table({**cols, "cells_r3": [v["r3"] for v in vals], "cells_r4": [v["r4"] for v in vals],
                      "source": [source] * len(vals)})
    out = DATA / "ranges" / fname
    out.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, out, compression="zstd")
    log(f"wrote {out} with {len(vals)} ranges")


if __name__ == "__main__":
    for job in sys.argv[1:] or ["gard", "inat"]:
        {"gard": gard, "inat": inat}[job]()
