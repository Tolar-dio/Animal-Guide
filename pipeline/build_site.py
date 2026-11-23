"""Render the static site into dist/.

Reads only local data (see assemble.py), so it runs in seconds to minutes and
never touches the network. Output:

  dist/index.html                 globe of species richness
  dist/species/<slug>/index.html  one page per species
  dist/explore/...                orders and families
  dist/coverage/, dist/about/, dist/404.html
  dist/data/search.json           name index for search and "surprise me"
  dist/data/range/<id>.json       H3 cells for each species map
  dist/data/richness.json         res-3 cell -> species count
  dist/data/hex/<res1>.json       res-3 cell -> species ids, sharded by res-1 parent
"""
from __future__ import annotations

import json
import math
import random
import re
import shutil
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import h3
import numpy as np
import yaml
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup, escape

from assemble import load
from common import CONFIG, ROOT, slugify, write_json

DIST = ROOT / "dist"
SITE = ROOT / "site"

ORDER_NAMES = {
    "Monotremata": "Egg-laying mammals", "Didelphimorphia": "American opossums",
    "Paucituberculata": "Shrew opossums", "Microbiotheria": "Monito del monte order",
    "Notoryctemorphia": "Marsupial moles", "Dasyuromorphia": "Carnivorous marsupials",
    "Peramelemorphia": "Bandicoots and bilbies", "Diprotodontia": "Kangaroos, koalas and kin",
    "Afrosoricida": "Tenrecs and golden moles", "Macroscelidea": "Sengis",
    "Tubulidentata": "Aardvark order", "Hyracoidea": "Hyraxes", "Proboscidea": "Elephants",
    "Sirenia": "Manatees and dugongs", "Cingulata": "Armadillos", "Pilosa": "Sloths and anteaters",
    "Scandentia": "Treeshrews", "Dermoptera": "Colugos", "Primates": "Primates",
    "Lagomorpha": "Rabbits, hares and pikas", "Rodentia": "Rodents",
    "Eulipotyphla": "Shrews, moles and hedgehogs", "Chiroptera": "Bats", "Pholidota": "Pangolins",
    "Carnivora": "Carnivorans", "Perissodactyla": "Odd-toed hoofed mammals",
    "Artiodactyla": "Even-toed hoofed mammals and whales",
}
AREA_SCALE = [  # km², for "about the size of"
    (2_000, "a large city"), (4_000, "Rhode Island"), (20_000, "New Jersey"),
    (41_000, "Switzerland"), (130_000, "England"), (270_000, "Colorado"),
    (700_000, "Texas"), (2_000_000, "Mexico"), (4_200_000, "the European Union"),
    (7_700_000, "Australia"), (10_000_000, "the United States"), (17_000_000, "Russia"),
    (31_000_000, "Africa"), (45_000_000, "Asia"), (1e12, "most of the planet"),
]
ITALIC = re.compile(r"_([^_]+)_")


def md_italic(s: str | None) -> Markup:
    if not s:
        return Markup("")
    return Markup(ITALIC.sub(r"<em>\1</em>", str(escape(s))))


def fmt_int(n) -> str:
    return f"{int(n):,}" if n is not None else ""


def area_phrase(km2: float) -> str:
    for limit, name in AREA_SCALE:
        if km2 <= limit:
            return name
    return "most of the planet"


def join_words(items: list[str]) -> str:
    items = [i for i in items if i]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def oxford(items: list[str]) -> str:
    if len(items) == 2 and any(" and " in i for i in items):
        return items[0] + ", and " + items[1]
    if len(items) <= 2:
        return " and ".join(items)
    return ", ".join(items[:-1]) + ", and " + items[-1]


def a_or_an(word: str) -> str:
    return "an" if word[:1].lower() in "aeiou" else "a"


def fmt_mass(g: float) -> str:
    if g < 10:
        return f"{g:.1f} g".replace(".0 g", " g")
    if g < 1000:
        return f"{round(g):,} g"
    if g < 100_000:
        kg = g / 1000
        return f"{kg:.1f} kg".replace(".0 kg", " kg") if kg < 10 else f"{round(kg):,} kg"
    if g < 1_000_000:
        return f"{int(g / 1000 + 0.5):,} kg"
    return f"{g / 1_000_000:,.0f} tonnes"


def fmt_area(km2: float) -> str:
    if km2 >= 1_000_000:
        return f"{km2 / 1e6:.1f} million km²"
    if km2 >= 10_000:
        return f"{round(km2, -3):,.0f} km²"
    return f"{round(km2, -2):,.0f} km²"


def fmt_length(mm: float) -> str:
    if mm < 1000:
        return f"{round(mm / 10, 1):g} cm"
    return f"{mm / 1000:.1f} m".replace(".0 m", " m")


def measured(t: dict, flag: str) -> bool:
    return t.get(flag) in (0, 0.0, False)


DIET = [("diet_fruit", "fruit"), ("diet_nectar", "nectar"), ("diet_seeds", "seeds"),
        ("diet_plants", "leaves and other plants"), ("diet_invertebrates", "insects and other invertebrates"),
        ("diet_fish", "fish"), ("diet_cold_blooded", "reptiles and amphibians"),
        ("diet_warm_blooded", "birds and mammals"), ("diet_carrion", "carrion")]
WAYS = [("arboreal", "in trees"), ("terrestrial", "on the ground"), ("fossorial", "in burrows"),
        ("aquatic", "in water"), ("aerial", "on the wing")]


def activity(t: dict) -> str | None:
    n = t.get("nocturnality")
    if n is None:
        return None
    return "mostly at night" if n >= 0.75 else "mostly by day" if n <= 0.25 else "by day and by night"


def trait_phrases(t: dict) -> str | None:
    """One sentence of life history, using measured values only."""
    if not t:
        return None
    out = []
    if t.get("mass_g") and measured(t, "mass_imputed"):
        out.append(f"The largest adults weigh up to {fmt_mass(t['mass_g'])}.")
    ways = [w for k, w in WAYS if t.get(k) == 1] if measured(t, "habitat_imputed") else []
    act = activity(t) if measured(t, "act_imputed") else None
    if ways and act:
        out.append(f"It lives {join_words(ways)} and is active {act}.")
    elif ways:
        out.append(f"It lives {join_words(ways)}.")
    elif act:
        out.append(f"It is active {act}.")
    diet = [w for k, w in DIET if t.get(k) == 1]
    if diet and t.get("diet_source"):
        out.append(f"Its diet includes {oxford(diet)}.")
    if t.get("longevity_yr") and t.get("longevity_source"):
        out.append(f"The longest recorded lifespan is about {round(t['longevity_yr'])} years.")
    return " ".join(out) or None


def glance(sp: dict, ctx: dict) -> list[str]:
    """Plain-language summary built only from facts in the record."""
    out = []
    name = escape(sp["common"])
    genus_n = ctx["genus_count"][sp["genus"]]
    fam = sp["family"]
    order = ORDER_NAMES.get(sp["order"], sp["order"])
    order = order if order in ("Primates",) else order.lower()
    s = f"The {name} (<em>{sp['sciName']}</em>) belongs to the {order}, in the family {fam}"
    if genus_n == 1:
        s += ", and is the only species in its genus."
    else:
        s += f", and is one of {genus_n} species in the genus <em>{sp['genus']}</em>."
    out.append(s)

    if sp.get("authority"):
        author = re.sub(r"[()]", "", sp["authority"]).rsplit(",", 1)[0]
        year = sp.get("authorityYear")
        s = f"It was first described to science by {author}"
        s += f" in {year}" if year else ""
        orig = sp.get("originalNameCombination")
        if orig and orig.replace("_", " ") != sp["sciName"]:
            s += f", originally under the name <em>{escape(orig)}</em>"
        out.append(s + ".")

    t = sp.get("traits") or {}
    life = trait_phrases(t)
    if life:
        out.append(life)

    countries = sp["countries"]
    if sp.get("extinct"):
        out.append("It is extinct. The species is known from historical records and specimens.")
    elif len(countries) == 1:
        out.append(f"It is found in only one country on Earth: {escape(countries[0])}.")
    elif countries:
        conts = sp["continents"]
        where = f"across {join_words(conts)}" if conts else ""
        out.append(f"It lives in {len(countries)} countries {where}.".replace("  ", " ").replace(" .", "."))

    if sp.get("range_km2"):
        km2 = sp["range_km2"]
        out.append(f"Its mapped range covers roughly {fmt_area(km2)}, "
                   f"about the size of {area_phrase(km2)}.")

    st = sp["iucnStatus"]
    if st in ("CR", "EN", "VU"):
        out.append(f"The IUCN Red List classes it as {sp['status_name']}, meaning it faces a "
                   f"{'extremely high' if st == 'CR' else 'very high' if st == 'EN' else 'high'} risk of extinction in the wild.")
    elif st == "DD":
        out.append("Scientists don't yet know enough about it to judge how threatened it is (Data Deficient).")
    elif st == "NE":
        out.append("It has not yet been evaluated for the IUCN Red List.")

    obs = sp.get("observations_count")
    if obs is not None and not sp.get("extinct"):
        if obs == 0:
            out.append("Nobody has logged it on iNaturalist yet. A first observation would be a genuine contribution.")
        elif obs < 10:
            out.append(f"It has been logged only {obs} time{'s' if obs != 1 else ''} on iNaturalist.")
        else:
            out.append(f"Naturalists have logged it {fmt_int(obs)} times on iNaturalist.")
    return out


def cell_feature_area(cells: list[str]) -> float:
    return sum(h3.cell_area(c, unit="km^2") for c in cells)


def compute_neighbors(species: list[dict], k: int = 8) -> dict[int, list[int]]:
    """Species whose ranges overlap most (Jaccard on res-3 cells)."""
    from scipy.sparse import csr_matrix

    with_range = [s for s in species if s.get("range") and not s.get("extinct")]
    if not with_range:
        return {}
    cell_index: dict[str, int] = {}
    rows, cols = [], []
    for i, s in enumerate(with_range):
        for c in s["range"]["r3"]:
            j = cell_index.setdefault(c, len(cell_index))
            rows.append(i)
            cols.append(j)
    m = csr_matrix((np.ones(len(rows), dtype=np.float32), (rows, cols)),
                   shape=(len(with_range), len(cell_index)))
    sizes = np.asarray(m.sum(axis=1)).ravel()
    inter = (m @ m.T).tocsr()
    out = {}
    for i, s in enumerate(with_range):
        row = inter.getrow(i)
        idx, vals = row.indices, row.data
        mask = idx != i
        idx, vals = idx[mask], vals[mask]
        if not len(idx):
            continue
        jac = vals / (sizes[i] + sizes[idx] - vals)
        top = idx[np.argsort(-jac)[:k]]
        out[s["id"]] = [with_range[t]["id"] for t in top]
    return out


def build() -> None:
    data = load()
    species = data["species"]
    release = data["release"]
    base = CONFIG["base_path"].rstrip("/")
    today = date.today().isoformat()
    allow_nc = CONFIG["allow_nc"]

    if DIST.exists():
        shutil.rmtree(DIST)
    shutil.copytree(SITE / "static", DIST / "static")

    by_id = {s["id"]: s for s in species}
    genus_count = Counter(s["genus"] for s in species)
    by_genus = defaultdict(list)
    for s in species:
        by_genus[s["genus"]].append(s)

    # Ranges: per-species files, richness grid, hex shards, areas.
    richness: Counter = Counter()
    cell_species: dict[str, list[int]] = defaultdict(list)
    for s in species:
        rng = s.get("range")
        if not rng:
            continue
        r3 = rng["r3"]
        cells = rng["r4"] if rng["r4"] else r3
        s["range_km2"] = cell_feature_area(rng["r4"]) if rng["r4"] else cell_feature_area(r3) * 0.85
        write_json(DIST / "data" / "range" / f"{s['id']}.json", {"res": 4 if rng["r4"] else 3, "cells": cells})
        if not s.get("extinct"):
            for c in r3:
                richness[c] += 1
                cell_species[c].append(s["id"])
    if richness:
        write_json(DIST / "data" / "richness.json", dict(richness))
        shards = defaultdict(dict)
        for c, ids in cell_species.items():
            ids.sort(key=lambda i: (-by_id[i]["status_rank"], by_id[i]["common"]))
            shards[h3.cell_to_parent(c, 1)][c] = ids
        for parent, cellmap in shards.items():
            write_json(DIST / "data" / "hex" / f"{parent}.json", cellmap)

    neighbors = compute_neighbors(species)

    # Search index: [slug, sci, common, other names, order, status, thumb, id]
    search = []
    for s in species:
        thumb = s["media"][0]["small"] if s["media"] else ""
        search.append([s["slug"], s["sciName"], s["common"], "; ".join(s["other_common"][:4]),
                       s["order"], s["iucnStatus"], thumb, s["id"]])
    write_json(DIST / "data" / "search.json", search)

    env = Environment(loader=FileSystemLoader(SITE / "templates"), autoescape=select_autoescape(["html"]))
    env.filters["italic"] = md_italic
    env.filters["n"] = fmt_int
    env.filters["slug"] = slugify
    env.filters["mass"] = fmt_mass
    env.filters["area"] = fmt_area
    env.filters["mm"] = fmt_length
    env.globals["activity"] = activity
    env.globals.update(base=base, release=release, today=today, order_names=ORDER_NAMES,
                       allow_nc=allow_nc, site_url=CONFIG["site_url"])

    def render(template: str, out: str, **ctx) -> None:
        path = DIST / out
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(env.get_template(template).render(**ctx))

    ctx = {"genus_count": genus_count}
    tpl_species = env.get_template("species.html")
    for s in species:
        s["glance"] = [Markup(p) for p in glance(s, ctx)]
        rel = [r for r in by_genus[s["genus"]] if r["id"] != s["id"]]
        rel.sort(key=lambda r: (not r["media"], r["common"]))
        page = tpl_species.render(
            sp=s, relatives=rel[:12], relatives_total=len(rel),
            neighbors=[by_id[i] for i in neighbors.get(s["id"], [])],
            order_label=ORDER_NAMES.get(s["order"], s["order"]),
        )
        out = DIST / "species" / s["slug"] / "index.html"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(page)

    # Orders and families
    orders = defaultdict(list)
    for s in sorted(species, key=lambda s: s["phylosort"]):
        orders[s["order"]].append(s)
    order_cards = []
    for order, members in orders.items():
        pic = next((m for m in sorted(members, key=lambda m: -(m.get("observations_count") or 0)) if m["media"]), None)
        fams = defaultdict(list)
        for m in members:
            fams[m["family"]].append(m)
        threatened = sum(1 for m in members if m["iucnStatus"] in ("CR", "EN", "VU"))
        card = {"order": order, "label": ORDER_NAMES.get(order, order), "count": len(members),
                "families": len(fams), "threatened": threatened, "pic": pic, "slug": slugify(order)}
        order_cards.append(card)
        render("order.html", f"explore/{card['slug']}/index.html", card=card, families=fams)
    render("explore.html", "explore/index.html", orders=order_cards)

    # Coverage
    rows = []
    for card in order_cards:
        members = orders[card["order"]]
        rows.append({
            **card,
            "open_photo": sum(1 for m in members if m["media"] and m["media"][0]["open"]),
            "nc_photo": sum(1 for m in members if m["media"] and not m["media"][0]["open"]),
            "no_photo": sum(1 for m in members if not m["media"]),
            "range": sum(1 for m in members if m.get("range")),
            "wiki": sum(1 for m in members if m.get("wiki")),
        })
    totals = {k: sum(r[k] for r in rows) for k in ("count", "open_photo", "nc_photo", "no_photo", "range", "wiki")}
    wanted = [s for s in species if not s["media"] and not s.get("extinct")]
    wanted.sort(key=lambda s: (s["order"], s["family"], s["sciName"]))
    wanted_by_order = defaultdict(list)
    for s in wanted:
        wanted_by_order[s["order"]].append(s)
    render("coverage.html", "coverage/index.html", rows=rows, totals=totals, wanted=wanted_by_order,
           wanted_n=len(wanted))

    # About
    sources = yaml.safe_load((ROOT / "DATA_SOURCES.yaml").read_text())
    render("about.html", "about/index.html", sources=sources)

    # Home
    rnd = random.Random(today)
    spotlight_pool = [s for s in species if s["iucnStatus"] in ("CR", "EN") and s["media"] and s["media"][0]["open"]]
    spotlight = rnd.sample(spotlight_pool, min(8, len(spotlight_pool)))
    render("index.html", "index.html", orders=order_cards, spotlight=spotlight,
           has_ranges=bool(richness), counts=totals,
           threatened=sum(1 for s in species if s["iucnStatus"] in ("CR", "EN", "VU")))
    render("404.html", "404.html")

    # Sitemap
    urls = ["", "explore/", "coverage/", "about/"] + [f"species/{s['slug']}/" for s in species] + \
           [f"explore/{c['slug']}/" for c in order_cards]
    sm = "\n".join(f"<url><loc>{CONFIG['site_url']}/{u}</loc></url>" for u in urls)
    (DIST / "sitemap.xml").write_text(
        f'<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n{sm}\n</urlset>\n')
    (DIST / ".nojekyll").write_text("")

    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file())
    files = sum(1 for p in DIST.rglob("*") if p.is_file())
    print(f"built {len(species)} species pages, {len(order_cards)} orders; "
          f"{files:,} files, {size / 1e6:.0f} MB; ranges {'yes' if richness else 'no'}")


if __name__ == "__main__":
    build()
