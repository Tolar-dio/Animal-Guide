"""Tag every H3 res-3 cell with the countries it touches.

Uses Natural Earth 1:50m admin-0 countries (public domain). Species without a
curated country list (birds, reptiles) get their countries from the cells of
their range, so the list is approximate at the edges and labelled that way.

Writes data/ranges/cell_countries.parquet: cell, countries (list of names).
"""
from __future__ import annotations

import json

import h3
import pyarrow as pa
import pyarrow.parquet as pq
import shapely
from shapely.geometry import Polygon, shape

from common import DATA, Http

NE = "https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_50m_admin_0_countries.geojson"
RENAME = {"United States of America": "United States", "Dem. Rep. Congo": "Democratic Republic of the Congo",
          "Congo": "Republic of the Congo", "Central African Rep.": "Central African Republic",
          "S. Sudan": "South Sudan", "Eq. Guinea": "Equatorial Guinea", "Côte d'Ivoire": "Ivory Coast",
          "Bosnia and Herz.": "Bosnia and Herzegovina", "Dominican Rep.": "Dominican Republic",
          "Solomon Is.": "Solomon Islands", "Falkland Is.": "Falkland Islands", "W. Sahara": "Western Sahara",
          "Fr. Polynesia": "French Polynesia", "N. Cyprus": "Cyprus", "eSwatini": "Eswatini",
          "Timor-Leste": "East Timor", "Macedonia": "North Macedonia"}


def main() -> None:
    gj = Http(min_interval=0.2).json(NE)
    names, geoms = [], []
    for f in gj["features"]:
        p = f["properties"]
        name = p.get("NAME") or p.get("ADMIN")
        names.append(RENAME.get(name, name))
        geoms.append(shapely.make_valid(shape(f["geometry"])))
    tree = shapely.STRtree(geoms)

    cells_out, countries_out = [], []
    for base in h3.get_res0_cells():
        for c in h3.cell_to_children(base, 3):
            ring = [(lng, lat) for lat, lng in h3.cell_to_boundary(c)]
            lngs = [p[0] for p in ring]
            if max(lngs) - min(lngs) > 180:  # antimeridian cells: shift to one side
                ring = [(lng + 360 if lng < 0 else lng, lat) for lng, lat in ring]
            poly = Polygon(ring)
            hits = tree.query(poly, predicate="intersects")
            if len(hits):
                cells_out.append(c)
                countries_out.append(sorted({names[i] for i in hits}))
    pq.write_table(pa.table({"cell": cells_out, "countries": countries_out}),
                   DATA / "ranges" / "cell_countries.parquet", compression="zstd")
    print(f"tagged {len(cells_out)} land/coast cells with {len(set(sum(countries_out, [])))} countries")


if __name__ == "__main__":
    main()
