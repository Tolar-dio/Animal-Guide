// Home globe: mammal species richness per H3 res-3 hexagon. Click a hexagon to list its species.
(function () {
  const el = document.getElementById("globe");
  if (!el || !window.maplibregl) return;
  const base = AG.base;
  const map = HexMap.create(el, { center: [20, 15], zoom: window.innerWidth < 700 ? 0.9 : 1.7 });
  let spinning = true;
  const stop = () => { spinning = false; };
  ["mousedown", "touchstart", "wheel"].forEach(ev => el.addEventListener(ev, stop, { passive: true }));
  function spin() {
    if (!spinning) return;
    const c = map.getCenter();
    map.easeTo({ center: [c.lng - 4, c.lat], duration: 1000, easing: t => t });
  }
  map.on("moveend", () => { if (spinning) spin(); });

  map.on("load", async () => {
    if (el.dataset.ranges === "1") {
      const rich = await fetch(base + "/data/richness.json").then(r => r.json());
      const cells = Object.keys(rich);
      const max = Math.max(...Object.values(rich));
      map.addSource("rich", { type: "geojson", data: HexMap.cellsToGeoJSON(cells, c => ({ n: rich[c], cell: c })),
        attribution: 'Ranges: <a href="https://doi.org/10.5281/zenodo.6644198">Marsh et al. 2022</a>' });
      map.addLayer({ id: "rich", type: "fill", source: "rich", paint: {
        "fill-color": ["interpolate", ["linear"], ["sqrt", ["get", "n"]],
          1, "#1f4a52", Math.sqrt(max) * 0.3, "#2f7d6b", Math.sqrt(max) * 0.5, "#78b35a",
          Math.sqrt(max) * 0.75, "#e3c24f", Math.sqrt(max), "#f39a3d"],
        "fill-opacity": 0.82, "fill-outline-color": "#00000000" } });
      map.addLayer({ id: "rich-hover", type: "line", source: "rich", paint: { "line-color": "#fff", "line-width": 2 },
        filter: ["==", ["get", "cell"], ""] });
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
    ["Threatened", r => ["CR", "EN", "VU"].includes(r[5])],
    ["Primates", r => r[4] === "Primates"],
    ["Carnivorans", r => r[4] === "Carnivora"],
    ["Hoofed", r => r[4] === "Artiodactyla" || r[4] === "Perissodactyla"],
    ["Bats", r => r[4] === "Chiroptera"],
    ["Rodents", r => r[4] === "Rodentia"],
    ["Shrews & kin", r => r[4] === "Eulipotyphla"],
  ];

  async function openCell(cell, lngLat) {
    const parent = h3.cellToParent(cell, 1);
    const [{ byId }, shard] = await Promise.all([AG.index(), fetch(`${base}/data/hex/${parent}.json`).then(r => r.json())]);
    const rows = (shard[cell] || []).map(id => byId.get(id)).filter(Boolean);
    const lat = lngLat.lat.toFixed(1), lng = lngLat.lng.toFixed(1);
    document.getElementById("panel-title").textContent = `${rows.length} mammal species live here`;
    document.getElementById("panel-sub").textContent = `Hexagon around ${Math.abs(lat)}°${lat >= 0 ? "N" : "S"}, ${Math.abs(lng)}°${lng >= 0 ? "E" : "W"} · about 12,000 km²`;
    let current = 0;
    const draw = () => {
      const fn = FILTERS[current][1];
      const shown = rows.filter(fn);
      list.innerHTML = shown.map(r => `<li><a href="${AG.url(r[0])}">${r[6] ? `<img src="${r[6]}" alt="" loading="lazy">` : '<span class="ph"></span>'}
        <span class="t"><strong>${AG.esc(r[2])}</strong><em>${AG.esc(r[1])}</em></span><span class="dot s-${r[5]}"></span></a></li>`).join("")
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
