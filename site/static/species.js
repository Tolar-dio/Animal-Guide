// Species page: range hexes, GBIF observation density, type locality, lightbox.
(function () {
  const el = document.getElementById("map");
  if (el && window.maplibregl) {
    const base = AG.base;
    const key = el.dataset.key;
    const modeled = el.dataset.kind === "modeled";
    const gbif = el.dataset.gbif;
    const hasRange = el.dataset.range === "1";
    const typeLat = parseFloat(el.dataset.typeLat), typeLng = parseFloat(el.dataset.typeLng);
    const CONTINENT = { "Africa": [20, 2], "Asia": [95, 30], "Europe": [15, 50], "North America": [-100, 40],
      "South America": [-60, -15], "Oceania": [140, -20], "Antarctica": [0, -75], "Oceans": [-30, 0] };
    const map = HexMap.create(el, { zoom: 1.2, center: CONTINENT[el.dataset.continent] || [10, 20] });

    map.on("load", async () => {
      if (gbif) {
        map.addSource("gbif", {
          type: "raster", tileSize: 512,
          tiles: [`https://api.gbif.org/v2/map/occurrence/density/{z}/{x}/{y}@2x.png?taxonKey=${gbif}&bin=hex&hexPerTile=60&style=iNaturalist.poly`],
          attribution: '<a href="https://www.gbif.org/">GBIF</a> occurrence data',
        });
        map.addLayer({ id: "gbif", type: "raster", source: "gbif", paint: { "raster-opacity": 0.85 },
          layout: { visibility: document.getElementById("lyr-gbif").checked ? "visible" : "none" } });
      }
      if (hasRange) {
        const data = await fetch(`${base}/data/range/${key}.json`).then(r => r.json());
        map.addSource("range", { type: "geojson", data: HexMap.cellsToGeoJSON(data.cells) });
        const color = modeled ? "#5fc4b8" : "#e3a857";
        map.addLayer({ id: "range", type: "fill", source: "range",
          paint: { "fill-color": color, "fill-opacity": 0.55, "fill-outline-color": "#00000000" } }, gbif ? "gbif" : undefined);
        const b = HexMap.bounds(data.cells);
        const span = Math.max(b[1][0] - b[0][0], b[1][1] - b[0][1]);
        if (span < 300) map.fitBounds(b, { padding: 50, maxZoom: data.res === 4 ? 6 : 4.5, duration: 0 });
      }
      if (!isNaN(typeLat) && !isNaN(typeLng)) {
        const dot = document.createElement("div");
        dot.style.cssText = "width:12px;height:12px;border-radius:50%;background:#fff;border:3px solid #e0402c;box-shadow:0 0 0 3px #0006";
        new maplibregl.Marker({ element: dot }).setLngLat([typeLng, typeLat])
          .setPopup(new maplibregl.Popup({ offset: 10 }).setText("Type locality")).addTo(map);
        if (!hasRange) map.jumpTo({ center: [typeLng, typeLat], zoom: 3 });
      }
    });

    const toggle = (box, layer) => box && box.addEventListener("change", () => {
      if (map.getLayer(layer)) map.setLayoutProperty(layer, "visibility", box.checked ? "visible" : "none");
    });
    toggle(document.getElementById("lyr-range"), "range");
    toggle(document.getElementById("lyr-gbif"), "gbif");
  }

  // Lightbox for the gallery
  const lb = document.getElementById("lb");
  document.querySelectorAll(".lightbox").forEach(a => a.addEventListener("click", e => {
    e.preventDefault();
    lb.querySelector("img").src = a.href;
    lb.querySelector("p").textContent = a.dataset.credit;
    lb.hidden = false;
  }));
  if (lb) lb.addEventListener("click", () => { lb.hidden = true; lb.querySelector("img").src = ""; });
  document.addEventListener("keydown", e => { if (e.key === "Escape" && lb) lb.hidden = true; });
})();
