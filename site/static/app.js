// Search, "surprise me", and the shared species index.
(function () {
  const base = document.body.dataset.base || "";
  let indexPromise = null;

  // Index rows: [slug, sci, common, other names, order, status, thumb, id]
  window.AG = {
    base,
    index() {
      if (!indexPromise) {
        indexPromise = fetch(base + "/data/search.json").then(r => r.json()).then(rows => {
          const byId = new Map();
          rows.forEach(r => {
            r.hay = (r[2] + " | " + r[1] + " | " + r[3]).toLowerCase();
            byId.set(r[7], r);
          });
          return { rows, byId };
        });
      }
      return indexPromise;
    },
    url(slug) { return base + "/species/" + slug + "/"; },
    esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); },
  };

  const q = document.getElementById("q");
  const box = document.getElementById("results");
  let active = -1;

  function score(r, term) {
    const common = r[2].toLowerCase(), sci = r[1].toLowerCase();
    if (common === term || sci === term) return 0;
    if (common.startsWith(term) || sci.startsWith(term)) return 1;
    if (r.hay.includes(" " + term) || r.hay.includes("|" + term)) return 2;
    if (r.hay.includes(term)) return 3;
    return 9;
  }

  async function search() {
    const term = q.value.trim().toLowerCase();
    if (term.length < 2) { box.hidden = true; return; }
    const { rows } = await AG.index();
    const hits = [];
    for (const r of rows) {
      const s = score(r, term);
      if (s < 9) hits.push([s, r]);
    }
    hits.sort((a, b) => a[0] - b[0] || a[1][2].localeCompare(b[1][2]));
    active = -1;
    box.innerHTML = hits.slice(0, 12).map(([, r]) =>
      `<a href="${AG.url(r[0])}">${r[6] ? `<img src="${r[6]}" alt="" loading="lazy">` : '<span class="ph"></span>'}
       <span><strong>${AG.esc(r[2])}</strong><em>${AG.esc(r[1])}</em></span></a>`).join("") ||
      `<a><span><strong>No matches</strong><em>Try a scientific name or a genus</em></span></a>`;
    box.hidden = false;
  }

  if (q) {
    q.addEventListener("focus", () => AG.index());
    q.addEventListener("input", search);
    q.addEventListener("keydown", e => {
      const links = [...box.querySelectorAll("a[href]")];
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        e.preventDefault();
        active = Math.max(0, Math.min(links.length - 1, active + (e.key === "ArrowDown" ? 1 : -1)));
        links.forEach((l, i) => l.classList.toggle("on", i === active));
      } else if (e.key === "Enter") {
        const target = links[active >= 0 ? active : 0];
        if (target) location.href = target.href;
      } else if (e.key === "Escape") {
        box.hidden = true;
      }
    });
    document.addEventListener("click", e => { if (!e.target.closest(".search")) box.hidden = true; });
  }

  const surprise = document.getElementById("surprise");
  if (surprise) surprise.addEventListener("click", async () => {
    const { rows } = await AG.index();
    const pool = rows.filter(r => r[6]);
    const r = pool[Math.floor(Math.random() * pool.length)];
    location.href = AG.url(r[0]);
  });
})();
