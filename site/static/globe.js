// Home globe: species richness per H3 res-3 hexagon, by class. Click a hexagon to list its species.
(function () {
  const el = document.getElementById("globe");
  if (!el || !window.maplibregl) return;
  const base = AG.base;
  const wide = window.innerWidth >= 900;
  const map = HexMap.create(el, { center: [20, 15], zoom: wide ? 1.9 : 1.0, labels: false });
  if (wide) map.setPadding({ left: Math.min(520, window.innerWidth * 0.36), top: 0, right: 0, bottom: 0 });
  else {
    // Phones: sit the globe below the headline and stats.
    const intro = document.querySelector(".intro");
    map.setPadding({ top: intro ? intro.offsetHeight + 10 : 300, left: 0, right: 0, bottom: 0 });
  }
  let spinning = true;
  const stop = () => { spinning = false; };
  ["mousedown", "touchstart", "wheel"].forEach(ev => el.addEventListener(ev, stop, { passive: true }));
  function spin() {
    if (!spinning) return;
    const c = map.getCenter();
    map.easeTo({ center: [c.lng - 4, c.lat], duration: 1000, easing: t => t });
  }
  map.on("moveend", () => { if (spinning) spin(); });

  const CODE = { mammals: "m", birds: "b", reptiles: "r" };
  let classes = [], active = "all";

  function colorFor(max) {
    const m = Math.sqrt(Math.max(max, 1));
    return ["interpolate", ["linear"], ["sqrt", ["get", "n"]],
      0, "#0d2427", m * 0.35, "#1d5156", m * 0.55, "#4f9a68", m * 0.75, "#e3c24f", m, "#f39a3d"];
  }

  map.on("load", async () => {
    if (el.dataset.ranges === "1") {
      const rich = await fetch(base + "/data/richness.json").then(r => r.json());
      classes = rich.classes;
      const cells = Object.keys(rich.cells);
      const max = { all: 0 };
      classes.forEach(c => (max[c] = 0));
      const data = HexMap.cellsToGeoJSON(cells, c => {
        const v = rich.cells[c], p = { cell: c, all: v.reduce((a, b) => a + b, 0) };
        classes.forEach((k, i) => { p[k] = v[i]; max[k] = Math.max(max[k], v[i]); });
        max.all = Math.max(max.all, p.all);
        p.n = p.all;
        return p;
      });
      map.addSource("rich", { type: "geojson", data,
        attribution: 'Ranges: MDD (Marsh et al. 2022), GARD 1.7, iNaturalist Geomodel' });
      map.addLayer({ id: "rich", type: "fill", source: "rich", filter: [">", ["get", "n"], 0], paint: {
        "fill-color": colorFor(max.all), "fill-opacity": 0.82, "fill-outline-color": "#00000000" } });
      map.addLayer({ id: "rich-hover", type: "line", source: "rich", paint: { "line-color": "#fff", "line-width": 2 },
        filter: ["==", ["get", "cell"], ""] });

      const toggle = document.getElementById("layer-toggle");
      if (toggle) toggle.addEventListener("click", e => {
        const b = e.target.closest("button");
        if (!b) return;
        active = b.dataset.cls;
        toggle.querySelectorAll("button").forEach(x => x.classList.toggle("on", x === b));
        // Swap which count drives the colour, rescaled to that class's own maximum.
        data.features.forEach(f => (f.properties.n = f.properties[active]));
        map.getSource("rich").setData(data);
        map.setPaintProperty("rich", "fill-color", colorFor(max[active]));
      });

      map.on("mousemove", "rich", e => {
        map.getCanvas().style.cursor = "pointer";
        map.setFilter("rich-hover", ["==", ["get", "cell"], e.features[0].properties.cell]);
      });
      map.on("mouseleave", "rich", () => { map.getCanvas().style.cursor = ""; map.setFilter("rich-hover", ["==", ["get", "cell"], ""]); });
      map.on("click", "rich", e => { stop(); openCell(e.features[0].properties.cell, e.lngLat); });
    }
    setTimeout(spin, 600);
  });

  const panel = document.getElementById("panel");
  const list = document.getElementById("panel-list");
  const filters = document.getElementById("panel-filters");
  document.getElementById("panel-close").addEventListener("click", () => { panel.hidden = true; });
  const FILTERS = [
    ["All", () => true],
    ["Mammals", r => r[8] === "m"],
    ["Birds", r => r[8] === "b"],
    ["Reptiles", r => r[8] === "r"],
    ["Threatened", r => ["CR", "EN", "VU"].includes(r[5])],
    ["Primates", r => r[4] === "Primates"],
    ["Carnivorans", r => r[4] === "Carnivora"],
    ["Bats", r => r[4] === "Chiroptera"],
    ["Rodents", r => r[4] === "Rodentia"],
    ["Raptors", r => ["Accipitriformes", "Falconiformes", "Strigiformes"].includes(r[4])],
    ["Parrots", r => r[4] === "Psittaciformes"],
    ["Songbirds", r => r[4] === "Passeriformes"],
    ["Snakes & lizards", r => r[4] === "Squamata"],
    ["Turtles", r => r[4] === "Testudines"],
  ];

  async function openCell(cell, lngLat) {
    const parent = h3.cellToParent(cell, 1);
    const [{ rows: all }, shard] = await Promise.all([AG.index(), fetch(`${base}/data/hex/${parent}.json`).then(r => r.json())]);
    const rows = (shard[cell] || []).map(n => all[n]).filter(Boolean);
    const lat = lngLat.lat.toFixed(1), lng = lngLat.lng.toFixed(1);
    document.getElementById("panel-title").textContent = `${rows.length.toLocaleString()} species live here`;
    document.getElementById("panel-sub").textContent = `Hexagon around ${Math.abs(lat)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lng)}°${lng >= 0 ? "E" : "W"} · about 12,000 km²`;
    let current = active === "all" ? 0 : FILTERS.findIndex(f => f[0].toLowerCase() === active);
    if (current < 0) current = 0;
    const draw = () => {
      const fn = FILTERS[current][1];
      const shown = rows.filter(fn).slice(0, 400);
      list.innerHTML = shown.map(r => `<li><a href="${AG.url(r[0])}">${r[6] ? `<img src="${r[6]}" alt="" loading="lazy">` : '<span class="ph"></span>'}
        <span class="t">${r[2] ? `<strong>${AG.esc(r[2])}</strong><em>${AG.esc(r[1])}</em>` : `<strong><em>${AG.esc(r[1])}</em></strong>`}</span><span class="dot s-${r[5]}"></span></a></li>`).join("")
        || '<li class="muted">None in this group here.</li>';
      filters.innerHTML = FILTERS.map(([label, f], i) => {
        const n = rows.filter(f).length;
        return n || i === 0 ? `<button data-i="${i}" class="${i === current ? "on" : ""}">${label} ${n}</button>` : "";
      }).join("");
    };
    filters.onclick = e => { const b = e.target.closest("button"); if (b) { current = +b.dataset.i; draw(); } };
    draw();
    panel.hidden = false;
    panel.scrollTop = 0;
  }
})();
