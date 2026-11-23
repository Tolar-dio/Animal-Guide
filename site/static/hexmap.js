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
      // Quieter basemap: drop most labels so the data reads first.
      for (const layer of map.getStyle().layers) {
        if (layer.type === "symbol" && !/country|continent|ocean|place_city/.test(layer.id)) {
          map.setLayoutProperty(layer.id, "visibility", "none");
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
    let w = 180, s = 90, e = -180, n = -90;
    for (const c of cells) {
      const [lat, lng] = h3.cellToLatLng(c);
      w = Math.min(w, lng); e = Math.max(e, lng); s = Math.min(s, lat); n = Math.max(n, lat);
    }
    return [[w, s], [e, n]];
  },
};
