"""Convert MDD expert range polygons into H3 cell lists.

Source: Marsh et al. 2022, "Expert range maps of global mammal distributions
harmonised to three taxonomic authorities", MDD v1.2 taxonomy.
Zenodo record 6644198, CC BY 4.0.

Each order is downloaded, streamed feature by feature, converted to H3 cells
and deleted, so peak disk use stays near the size of the largest order.

Output: data/ranges/mdd_ranges_h3.parquet
  sciname     MDD v1.2 name with a space ("Puma concolor")
  order       order name from the source file
  cells_r3    sorted list of H3 res-3 cells touching the range (overlap rule)
  cells_r4    res-4 cells, only for small ranges (< SMALL_R3 res-3 cells)
  n_features  polygons merged into this species
  source      citation key

Run: python pipeline/build_ranges_h3.py --cache ../cache/ranges
Takes hours on a laptop; it is the quarterly job, not the nightly one.
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
import zipfile
from collections import defaultdict
from pathlib import Path

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import pyogrio
import shapely
from shapely.geometry import mapping

ROOT = Path(__file__).resolve().parents[1]
RECORD = "https://zenodo.org/api/records/6644198"
SKIP = {"MDD_Mammalia.zip"}  # the all-in-one zip duplicates the order zips
SIMPLIFY_DEG = 0.02          # ~2 km; far below res-3 cell size (~110 km across)
SMALL_R3 = 40                # below this many res-3 cells, also store res 4


def log(msg: str) -> None:
    print(time.strftime("%H:%M:%S"), msg, flush=True)


def list_files() -> list[dict]:
    out = subprocess.run(["curl", "-sL", RECORD], capture_output=True, check=True)
    files = json.loads(out.stdout)["files"]
    zips = [f for f in files if f["key"].endswith(".zip") and f["key"] not in SKIP]
    return sorted(zips, key=lambda f: f["size"])


STRIP_DEG = 60


def strips(part):
    """Cut a polygon into 60-degree longitude strips.

    H3 treats any polygon edge longer than 180 degrees of longitude as
    crossing the antimeridian, which turns near-global ranges inside out
    (a house sparrow drawn over the empty Pacific). Strips keep every edge short.
    """
    minx, _, maxx, _ = part.bounds
    if maxx - minx <= STRIP_DEG:
        return [part]
    out = []
    x = minx
    while x < maxx:
        piece = part.intersection(shapely.box(x, -90, min(x + STRIP_DEG, maxx), 90))
        out += [g for g in getattr(piece, "geoms", [piece]) if g.geom_type == "Polygon" and not g.is_empty]
        x += STRIP_DEG
    return out


def part_to_cells(part, res: int, contain: str = "overlap") -> set[str]:
    cells: set[str] = set()
    for piece in strips(part):
        cells.update(h3.h3shape_to_cells_experimental(h3.geo_to_h3shape(mapping(piece)), res, contain=contain))
    return cells


def geom_to_cells(geom, res: int) -> set[str]:
    if geom is None or geom.is_empty:
        return set()
    # Simplify first: validity checks on full-resolution coastlines dominate run time.
    simple = shapely.simplify(geom, SIMPLIFY_DEG, preserve_topology=False)
    if simple.is_empty:
        simple = shapely.simplify(geom, SIMPLIFY_DEG / 10, preserve_topology=True)
    geom = simple if simple.is_valid else shapely.make_valid(simple)
    polys = [g for g in getattr(geom, "geoms", [geom]) if g.geom_type in ("Polygon", "MultiPolygon")]
    cells: set[str] = set()
    for p in polys:
        for part in getattr(p, "geoms", [p]):
            if part.is_empty:
                continue
            try:
                cells.update(part_to_cells(part, res))
            except Exception as exc:  # degenerate slivers
                c = part.representative_point()
                cells.add(h3.latlng_to_cell(c.y, c.x, res))
                log(f"    fallback point for a part: {exc}")
    if not cells:
        c = geom.representative_point()
        cells.add(h3.latlng_to_cell(c.y, c.x, res))
    return cells


def process_gpkg(path: Path, acc: dict) -> None:
    with pyogrio.open_arrow(path, batch_size=1, columns=["sciname", "order"], use_pyarrow=True) as (meta, reader):
        geom_col = meta["geometry_name"] or "wkb_geometry"
        for batch in reader:
            names = batch.column("sciname").to_pylist()
            orders = batch.column("order").to_pylist()
            geoms = shapely.from_wkb(batch.column(geom_col).to_pylist())
            for name, order, geom in zip(names, orders, geoms):
                a = acc[name]
                a["order"] = order
                a["n"] += 1
                if geom is not None and not geom.is_empty:
                    geom = shapely.simplify(geom, SIMPLIFY_DEG, preserve_topology=False)
                r3 = geom_to_cells(geom, 3)
                a["r3"] |= r3
                if len(r3) < SMALL_R3:
                    a["r4"] |= geom_to_cells(geom, 4)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cache", default=str(ROOT.parent / "cache" / "ranges"))
    ap.add_argument("--out", default=str(ROOT / "data" / "ranges" / "mdd_ranges_h3.parquet"))
    args = ap.parse_args()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)

    acc: dict = defaultdict(lambda: {"order": None, "n": 0, "r3": set(), "r4": set()})
    done_marker = cache / "done.txt"
    done = set(done_marker.read_text().split()) if done_marker.exists() else set()
    partial = cache / "partial.json"
    if partial.exists():
        for k, v in json.loads(partial.read_text()).items():
            acc[k] = {"order": v["order"], "n": v["n"], "r3": set(v["r3"]), "r4": set(v["r4"])}

    for f in list_files():
        key = f["key"]
        if key in done:
            continue
        zpath = cache / key
        xdir = cache / key.removesuffix(".zip")
        already = xdir.exists() and any(xdir.rglob("*.gpkg")) and not zpath.exists()
        if not already and not (zpath.exists() and zpath.stat().st_size == f["size"]):
            log(f"{key}: downloading {f['size'] / 1e6:.0f} MB")
            for attempt in range(10):  # large files drop mid-transfer; resume with -C -
                r = subprocess.run(["curl", "-sL", "-C", "-", "--retry", "5", "-o", str(zpath), f["links"]["self"]])
                if r.returncode == 0 and zpath.stat().st_size == f["size"]:
                    break
                log(f"  download interrupted (curl {r.returncode}), resuming")
                time.sleep(10)
            else:
                raise RuntimeError(f"could not download {key}")
        # Some order zips use Deflate64, which Python's zipfile cannot read; Info-ZIP unzip can.
        if not already:
            try:
                with zipfile.ZipFile(zpath) as z:
                    z.extractall(xdir)
            except NotImplementedError:
                subprocess.run(["unzip", "-o", "-q", str(zpath), "-d", str(xdir)], check=True)
            zpath.unlink()
        for gpkg in sorted(xdir.rglob("*.gpkg")):
            log(f"  reading {gpkg.name}")
            process_gpkg(gpkg, acc)
        shutil.rmtree(xdir)
        done.add(key)
        done_marker.write_text("\n".join(sorted(done)))
        partial.write_text(json.dumps({k: {"order": v["order"], "n": v["n"], "r3": sorted(v["r3"]),
                                           "r4": sorted(v["r4"])} for k, v in acc.items()}))
        log(f"  {key} done; {len(acc)} species so far")

    rows = sorted(acc.items())
    table = pa.table({
        "sciname": [k for k, _ in rows],
        "order": [v["order"] for _, v in rows],
        "cells_r3": [sorted(v["r3"]) for _, v in rows],
        "cells_r4": [sorted(v["r4"]) for _, v in rows],
        "n_features": [v["n"] for _, v in rows],
        "source": ["marsh2022_mdd_ranges"] * len(rows),
    })
    pq.write_table(table, out, compression="zstd")
    log(f"wrote {out} with {len(rows)} species")


if __name__ == "__main__":
    sys.exit(main())
