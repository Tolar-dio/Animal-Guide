# Data and content licenses

animal-guide keeps three kinds of material apart, because they carry different licenses.

**Code** (everything under `pipeline/`, `site/` and `.github/`) is MIT. See `LICENSE`.

**Derived data** (the species tables, H3 range cells, richness grids and search indexes the pipeline writes to `data/`) is released under [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/). Credit the upstream sources listed in `DATA_SOURCES.yaml` as well as this project. Inputs with NonCommercial terms are kept in `data/nc/`, are excluded from this release, and keep their original license.

**Media and quoted text** keep their own licenses. Every photo, sound and Wikipedia passage on the site is shown with its author, license and a link back to the original. Nothing here relicenses them.

- Photos: per file, as shown on each species page and on the Credits page.
- Wikipedia passages: [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/), shown in a separate block that links to the article and its history.
- IUCN Red List categories are shown as labels only. No IUCN assessment text or spatial data is redistributed.

If you fork this project, keep these separations and `DATA_SOURCES.yaml` intact.
