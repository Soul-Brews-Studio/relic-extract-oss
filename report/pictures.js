// Two pictures of what was asked (pictures.py): a semantic map of the prompts (UMAP of their vectors) and a word cloud.
/* global DATA, h, fmtN, fmtP, section, t, showTip, hideTip */

if (DATA.pictures) {
  const PI = DATA.pictures;
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  if (PI.map) {
    const Mp = PI.map, top7 = Mp.labels.slice(0, 7).map((l) => l.topic);
    section(t("pic.map.h"), t("pic.map.lead", { n: fmtN(Mp.points.length), from: fmtN(Mp.from_n), k: Object.keys(Mp.topics).length }), (s) => {
      const lg = h("div", "legend", null, s);
      Mp.labels.slice(0, 7).forEach((l, i) => {
        const it = h("span", "lg-i", null, lg);
        h("span", "lg-box", null, it).style.background = `var(--s${i + 1})`;
        h("span", null, `${l.name} ${fmtP(l.share)}`, it);
      });
      const it = h("span", "lg-i", null, lg);
      h("span", "lg-box", null, it).style.background = "var(--base)";
      h("span", null, t("pic.map.rest"), it);
      const wrap = h("div", "mapwrap", null, s);
      const cv = h("canvas", "semmap", null, wrap);
      const W = 1440, H = 900;
      cv.width = W; cv.height = H;
      const draw = () => {
        const g = cv.getContext("2d");
        if (!g) return;
        g.clearRect(0, 0, W, H);
        const col = top7.map((_, i) => css(`--s${i + 1}`)), rest = css("--base");
        for (const pass of [0, 1]) {   // grey first, colours on top
          for (const [x, y, tp] of Mp.points) {
            const k = top7.indexOf(tp);
            if ((k >= 0) !== !!pass) continue;
            g.fillStyle = k >= 0 ? col[k] : rest;
            g.globalAlpha = k >= 0 ? 0.75 : 0.45;
            g.fillRect(20 + (x / 1000) * (W - 40), 20 + (1 - y / 1000) * (H - 40), 2.4, 2.4);
          }
        }
        g.globalAlpha = 1;
        g.textAlign = "left";
        const placed = [];   // label boxes already drawn: a new one moves down until it overlaps none, or is skipped
        const hit = (b) => placed.some((q) => b.x < q.x + q.w && q.x < b.x + b.w && b.y < q.y + q.h && q.y < b.y + b.h);
        for (const l of Mp.labels) {
          g.font = "600 15px system-ui, -apple-system, sans-serif";
          const tw = Math.max(g.measureText(l.name).width, 40);
          let px = 20 + (l.x / 1000) * (W - 40) - tw / 2, py = 20 + (1 - l.y / 1000) * (H - 40);
          px = Math.max(6, Math.min(W - tw - 6, px));           // stay inside the canvas
          let box = { x: px - 3, y: py - 15, w: tw + 6, h: 36 }, tries = 0;
          while (hit(box) && tries++ < 6) { py += 20; box = { ...box, y: py - 15 }; }
          if (hit(box) || py > H - 20) continue;
          placed.push(box);
          g.lineWidth = 4; g.strokeStyle = css("--surface"); g.fillStyle = css("--ink");
          g.strokeText(l.name, px, py); g.fillText(l.name, px, py);
          g.font = "13px system-ui, -apple-system, sans-serif"; g.fillStyle = css("--ink-2");
          g.strokeText(fmtP(l.share), px, py + 16); g.fillText(fmtP(l.share), px, py + 16);
        }
      };
      draw();
      window.matchMedia?.("(prefers-color-scheme: dark)").addEventListener?.("change", draw);
      new MutationObserver(draw).observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
      // hover: the nearest point's topic (a coarse grid keeps it cheap)
      const grid = new Map();
      Mp.points.forEach((p) => { const key = (p[0] >> 5) * 64 + (p[1] >> 5); (grid.get(key) || grid.set(key, []).get(key)).push(p); });
      cv.addEventListener("pointermove", (ev) => {
        const r = cv.getBoundingClientRect();
        const x = ((ev.clientX - r.x) / r.width * W - 20) / (W - 40) * 1000, y = (1 - ((ev.clientY - r.y) / r.height * H - 20) / (H - 40)) * 1000;
        let best = null, bd = 1e9;
        for (let gx = (x >> 5) - 1; gx <= (x >> 5) + 1; gx++) for (let gy = (y >> 5) - 1; gy <= (y >> 5) + 1; gy++) {
          for (const p of grid.get(gx * 64 + gy) || []) { const dd = (p[0] - x) ** 2 + (p[1] - y) ** 2; if (dd < bd) { bd = dd; best = p; } }
        }
        if (best && bd < 900) showTip(ev, Mp.topics[best[2]], [[t("pic.map.tip"), "", null]]); else hideTip();
      });
      cv.addEventListener("pointerleave", hideTip);
    }, t("pic.map.note", { out: Mp.outside }));
  }
  if (PI.cloud) {
    const C = PI.cloud, max = C.words[0][1];
    section(t("pic.cloud.h"), t("pic.cloud.lead", { n: fmtN(C.prompts), w: C.words.length, top: C.words.slice(0, 5).map((x) => x[0]).join(", ") }), (s) => {
      const lg = h("div", "legend", null, s);
      for (const [k, v] of [["pic.cloud.en", "var(--s1)"], ["pic.cloud.th", "var(--s2)"]]) {
        const it = h("span", "lg-i", null, lg);
        h("span", "lg-box", null, it).style.background = v;
        h("span", null, t(k), it);
      }
      const box = h("div", "cloud", null, s);
      const order = [...C.words].sort((a, b) => a[0].localeCompare(b[0]));
      order.forEach(([w, n, th]) => {
        const e = h("span", "cw" + (th ? " th" : ""), w, box);
        e.style.fontSize = (12 + 46 * Math.sqrt(n / max)).toFixed(1) + "px";
        e.title = t("pic.cloud.tip", { n: fmtN(n) });
      });
      const d = h("details", "tv", null, s);
      h("summary", null, t("Table"), d);
      const tb = h("tbody", null, null, h("table", null, null, d));
      C.words.slice(0, 60).forEach(([w, n]) => { const r = h("tr", null, null, tb); h("td", null, w, r); h("td", "num", fmtN(n), r); });
    }, t("pic.cloud.note"));
  }
}
