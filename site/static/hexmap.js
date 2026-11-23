// Shared map helpers: basemap, globe projection, H3 cells to GeoJSON.
window.HexMap = {
  style: "https://tiles.openfreemap.org/styles/dark",

  create(container, opts = {}) {
    const map = new maplibregl.Map({
      container,
      style: this.style,
      center: opts.center || [10, 20],
      zoom: opts.zoom ?? 1.4,
      minZoom: 0.6,
      maxZoom: 9,
      attributionControl: { compact: true },
      renderWorldCopies: false,
    });
    map.addControl(new maplibregl.NavigationControl({ showCompass: false }), "bottom-right");
    map.on("style.load", () => {
      map.setProjection({ type: "globe" });
      // Ocean a deep teal, land a warm charcoal, so coastlines read under the data.
      for (const layer of map.getStyle().layers) {
        if (layer.id === "background") map.setPaintProperty(layer.id, "background-color", "#1c211d");
        if (layer.type === "fill" && /^water/.test(layer.id)) map.setPaintProperty(layer.id, "fill-color", "#0c2027");
        if (layer.type === "fill" && /landcover|landuse|park/.test(layer.id)) map.setLayoutProperty(layer.id, "visibility", "none");
      }
      // Quieter basemap: drop most labels so the data reads first.
      for (const layer of map.getStyle().layers) {
        if (layer.type !== "symbol") continue;
        if (opts.labels === false || !/country|continent|ocean/.test(layer.id)) {
          map.setLayoutProperty(layer.id, "visibility", "none");
        } else {
          map.setLayoutProperty(layer.id, "text-field", ["coalesce", ["get", "name:en"], ["get", "name_en"], ["get", "name"]]);
        }
      }
    });
    return map;
  },

  // One polygon per cell. Cells that straddle the antimeridian get their
  // western longitudes shifted by +360 so MapLibre draws them in one piece.
  cellsToGeoJSON(cells, propsFn) {
    const features = [];
    for (const c of cells) {
      const ring = h3.cellToBoundary(c, true);
      const lngs = ring.map(p => p[0]);
      if (Math.max(...lngs) - Math.min(...lngs) > 180) {
        for (const p of ring) if (p[0] < 0) p[0] += 360;
      }
      features.push({ type: "Feature", properties: propsFn ? propsFn(c) : {}, geometry: { type: "Polygon", coordinates: [ring] } });
    }
    return { type: "FeatureCollection", features };
  },

  bounds(cells) {
    // Pick whichever longitude frame (-180..180 or 0..360) gives the tighter box,
    // so ranges that cross the date line (Alaska to Siberia) still fit.
    let s = 90, n = -90;
    const a = [], b = [];
    for (const c of cells) {
      const [lat, lng] = h3.cellToLatLng(c);
      s = Math.min(s, lat); n = Math.max(n, lat);
      a.push(lng); b.push(lng < 0 ? lng + 360 : lng);
    }
    const span = v => Math.max(...v) - Math.min(...v);
    const v = span(b) < span(a) ? b : a;
    return [[Math.min(...v), s], [Math.max(...v), n]];
  },

  center(cells) {
    const [[w, s], [e, n]] = this.bounds(cells);
    let lng = (w + e) / 2;
    if (lng > 180) lng -= 360;
    return [lng, (s + n) / 2];
  },
};
