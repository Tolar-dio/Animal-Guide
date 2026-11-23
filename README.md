# Animal Guide

An open guide to every animal species on Earth, in the spirit of ARKive. Every species gets a page with photos, a range map, its conservation status and the story of its name. The site is generated entirely from openly licensed data, costs nearly nothing to run, and can be rebuilt by anyone from this repository.

**Phase 1: all 6,904 mammals** from the Mammal Diversity Database v2.5. Birds and reptiles come next, then amphibians and fishes, then every invertebrate.

## What's on the site

- **Globe.** Every mammal's expert range converted to H3 hexagons (about 12,000 km² each). The globe is shaded by how many species live in each hexagon. Tap one to list them.
- **A page per species.** Hero photo, a plain-language summary built from the data, range map with GBIF observation density, the Wikipedia lead section, MDD distribution and taxonomy notes, the original description and type specimen, a photo gallery, relatives in the same genus, and the species whose ranges overlap most.
- **Explore.** All 27 orders and 169 families.
- **Coverage.** What open evidence exists for each group, and a "most wanted" list of mammals with no open photo.

## How it's built

```
pipeline/
  fetch_mdd.py          MDD species list, release info, change log  -> data/species/
  fetch_wikidata.py     ID crosswalk: Wikidata, iNat, GBIF, IUCN, Wikipedia -> data/crosswalk/
  fetch_inat.py         iNaturalist ids, observation counts, licensed photos -> data/media/
  fetch_wikimedia.py    Commons image licenses, Wikipedia lead sections -> data/media/, data/text/
  build_ranges_h3.py    MDD expert range polygons -> H3 cells (one-off, slow) -> data/ranges/
  assemble.py           merges everything into one record per species, applies license policy
  build_site.py         renders dist/: pages, search index, range and richness files
site/templates/         Jinja2 page templates
site/static/            CSS and the map scripts (MapLibre GL, h3-js)
docs/permissions/       permission request drafts and the log of replies
```

Run it locally:

```bash
pip install -r requirements.txt
python pipeline/fetch_mdd.py
python pipeline/fetch_wikidata.py
python pipeline/fetch_inat.py        # about 1.5 hours at iNaturalist's rate limit
python pipeline/fetch_wikimedia.py
python pipeline/build_ranges_h3.py   # about 10 GB of downloads; only needed once
python pipeline/build_site.py        # a few seconds
python -m http.server -d dist 8000   # then set base_path: "" in config.yaml to browse locally
```

GitHub Actions does the rest: `deploy.yml` rebuilds the site on every push, `refresh.yml` refreshes the data every Sunday and redeploys, and `ranges.yml` reruns the range conversion on demand.

## Licenses

Code is MIT. Derived data is CC BY 4.0. Photos and Wikipedia text keep their own licenses and are credited wherever they appear. See [LICENSE-DATA.md](LICENSE-DATA.md) and [DATA_SOURCES.yaml](DATA_SOURCES.yaml) for every source and its terms.

The site carries no ads or revenue, which is what lets it show photos licensed for non-commercial use. Setting `allow_nc: false` in `config.yaml` strips all of them in one rebuild.

## Permissions

IUCN and BirdLife range maps cannot be published without written permission. Drafts for those and three other requests are in [docs/permissions](docs/permissions/README.md). Until replies arrive, mammals use the CC BY range maps from Marsh et al. (2022), and every species also shows live GBIF observation density.
