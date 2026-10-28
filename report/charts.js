// Small SVG charts (words through t(), i18n.js): stacked bars, lines, a heat grid. Every chart has a legend (2+ series), a hover tooltip and a
// table view. Labels go in with textContent, never innerHTML. Colours come from CSS roles (--s1 … --s7, --seq-*).

const NS = "http://www.w3.org/2000/svg";
const W = 720, PAD = { l: 52, r: 12, t: 12, b: 28 };

function el(tag, attrs = {}, parent) {
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  if (parent) parent.appendChild(e);
  return e;
}
function h(tag, cls, text, parent) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = text;
  if (parent) parent.appendChild(e);
  return e;
}

const fmtN = (v) => {
  const a = Math.abs(v);
  if (a >= 1e6) return (v / 1e6).toFixed(a >= 1e7 ? 0 : 1) + " M";
  if (a >= 1e4) return Math.round(v / 1e3) + " k";
  if (a >= 1e3) return (v / 1e3).toFixed(1) + " k";
  return String(Math.round(v * 10) / 10);
};
const fmtP = (v) => (v * 100).toFixed(v < 0.1 ? 1 : 0) + "%";
const monthLabel = (m) => {
  const [y, mo] = m.split("-");
  return t("months.short").split(",")[+mo - 1] + (mo === "01" || m === MONTHS0 ? " " + y.slice(2) : "");
};
let MONTHS0 = "";

// one tooltip for the page
const tip = h("div", "tip", null, document.body);
function showTip(ev, title, lines) {
  tip.replaceChildren();
  h("div", "tip-t", title, tip);
  for (const [name, val, color] of lines) {
    const r = h("div", "tip-r", null, tip);
    const k = h("span", "tip-k", null, r);
    if (color) k.style.background = color;
    h("strong", null, val, r);
    h("span", "tip-n", name, r);
  }
  tip.style.display = "block";
  const x = Math.min(ev.clientX + 14, window.innerWidth - tip.offsetWidth - 8);
  const y = Math.min(ev.clientY + 14, window.innerHeight - tip.offsetHeight - 8);
  tip.style.left = x + "px";
  tip.style.top = y + "px";
}
const hideTip = () => (tip.style.display = "none");

const color = (i) => `var(--s${i + 1})`;

function legend(parent, series, kind = "rect") {
  if (series.length < 2) return;
  const lg = h("div", "legend", null, parent);
  series.forEach((s, i) => {
    const it = h("span", "lg-i", null, lg);
    h("span", kind === "line" ? "lg-line" : "lg-box", null, it).style.background = s.color || color(i);
    h("span", null, s.name, it);
  });
}

function niceMax(v) {
  if (v <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(v)));
  for (const m of [1, 1.5, 2, 2.5, 3, 4, 5, 6, 8, 10]) if (m * p >= v) return m * p;
  return 10 * p;
}

function axes(svg, H, max, fmt, months, xOf) {
  const ih = H - PAD.t - PAD.b;
  for (let i = 0; i <= 4; i++) {
    const y = PAD.t + ih - (ih * i) / 4;
    el("line", { x1: PAD.l, x2: W - PAD.r, y1: y, y2: y, class: i ? "grid" : "base" }, svg);
    el("text", { x: PAD.l - 6, y: y + 4, class: "tick", "text-anchor": "end" }, svg).textContent = fmt((max * i) / 4);
  }
  months.forEach((m, i) => {
    if (months.length > 10 && i % 2 && i !== months.length - 1) return;
    el("text", { x: xOf(i), y: H - 8, class: "tick", "text-anchor": "middle" }, svg).textContent = monthLabel(m);
  });
}

// table view twin of a chart
function tableView(parent, months, series, fmt) {
  const d = h("details", "tv", null, parent);
  h("summary", null, t("Table"), d);
  const tbl = h("table", null, null, d);
  const hr = h("tr", null, null, h("thead", null, null, tbl));
  h("th", null, t("month"), hr);
  series.forEach((s) => h("th", null, s.name, hr));
  const tb = h("tbody", null, null, tbl);
  months.forEach((m, i) => {
    const r = h("tr", null, null, tb);
    h("td", null, m, r);
    series.forEach((s) => h("td", "num", fmt(s.values[i]), r));
  });
}

// stacked bars, one per month; percent = each bar to 100 %
function stackBars(parent, months, series, { percent = false, fmt = fmtN, H = 240 } = {}) {
  MONTHS0 = months[0];
  const totals = months.map((_, i) => series.reduce((a, s) => a + s.values[i], 0));
  const max = percent ? 1 : niceMax(Math.max(...totals));
  legend(parent, series);
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img" }, parent);
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b, bw = iw / months.length;
  const xOf = (i) => PAD.l + bw * i + bw / 2;
  axes(svg, H, max, percent ? fmtP : fmt, months, xOf);
  months.forEach((m, i) => {
    let y = PAD.t + ih;
    const tot = totals[i] || 1;
    const segs = [];
    series.forEach((s, k) => {
      const v = percent ? s.values[i] / tot : s.values[i];
      const hgt = (v / max) * ih;
      if (hgt <= 0) return;
      const top = y - hgt;
      segs.push({ top, hgt, k });
      y = top;
    });
    const bwid = Math.min(bw * 0.62, 34), x = xOf(i) - bwid / 2;
    segs.forEach((sg, j) => {
      const gap = j ? 2 : 0; // 2px surface gap between fills
      const last = j === segs.length - 1;
      const hh = Math.max(sg.hgt - gap, 0.5);
      el(last && hh > 4 ? "path" : "rect", last && hh > 4
        ? { d: roundTop(x, sg.top, bwid, hh, 4), fill: color(sg.k) }
        : { x, y: sg.top, width: bwid, height: hh, fill: color(sg.k) }, svg);
    });
    const hit = el("rect", { x: PAD.l + bw * i, y: PAD.t, width: bw, height: ih, class: "hit", tabindex: 0 }, svg);
    const show = (ev) => showTip(ev, m + (percent ? "" : " · " + fmt(totals[i])),
      series.map((s, k) => [s.name, percent ? fmtP(s.values[i] / (totals[i] || 1)) + "  " + fmt(s.values[i]) : fmt(s.values[i]), color(k)]).reverse());
    hit.addEventListener("pointermove", show);
    hit.addEventListener("focus", (ev) => { const r = hit.getBoundingClientRect(); show({ clientX: r.x + r.width / 2, clientY: r.y + 40 }); });
    hit.addEventListener("pointerleave", hideTip);
    hit.addEventListener("blur", hideTip);
  });
  tableView(parent, months, series, fmt);
}

function roundTop(x, y, w, hgt, r) {
  r = Math.min(r, w / 2, hgt);
  return `M${x},${y + hgt}V${y + r}Q${x},${y} ${x + r},${y}H${x + w - r}Q${x + w},${y} ${x + w},${y + r}V${y + hgt}Z`;
}

// lines with a crosshair that snaps to the month
function lines(parent, months, series, { fmt = fmtN, H = 220, max } = {}) {
  MONTHS0 = months[0];
  max = max || niceMax(Math.max(...series.flatMap((s) => s.values)));
  legend(parent, series, "line");
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img" }, parent);
  const iw = W - PAD.l - PAD.r, ih = H - PAD.t - PAD.b;
  const xOf = (i) => PAD.l + (months.length < 2 ? iw / 2 : (iw * i) / (months.length - 1));
  const yOf = (v) => PAD.t + ih - (v / max) * ih;
  axes(svg, H, max, fmt, months, xOf);
  series.forEach((s, k) => {
    el("path", { d: s.values.map((v, i) => (i ? "L" : "M") + xOf(i) + "," + yOf(v)).join(""), class: "ln", stroke: s.color || color(k) }, svg);
    const i = s.values.length - 1; // direct label at the end point
    el("circle", { cx: xOf(i), cy: yOf(s.values[i]), r: 4, fill: s.color || color(k), class: "dot" }, svg);
  });
  const cross = el("line", { y1: PAD.t, y2: PAD.t + ih, class: "cross", visibility: "hidden" }, svg);
  const hit = el("rect", { x: PAD.l, y: PAD.t, width: iw, height: ih, class: "hit", tabindex: 0 }, svg);
  hit.addEventListener("pointermove", (ev) => {
    const r = svg.getBoundingClientRect(), px = ((ev.clientX - r.x) / r.width) * W;
    const i = Math.max(0, Math.min(months.length - 1, Math.round(((px - PAD.l) / iw) * (months.length - 1))));
    cross.setAttribute("x1", xOf(i));
    cross.setAttribute("x2", xOf(i));
    cross.setAttribute("visibility", "visible");
    showTip(ev, months[i], series.map((s, k) => [s.name, fmt(s.values[i]), s.color || color(k)]));
  });
  hit.addEventListener("pointerleave", () => { hideTip(); cross.setAttribute("visibility", "hidden"); });
  tableView(parent, months, series, fmt);
}

// heat grid: rows × columns, one blue ramp light→dark (sqrt scale so small counts still show)
function heat(parent, rowNames, colNames, cells, { fmt = fmtN, colFmt = (c) => c, rowW = 150, cell = 0 } = {}) {
  const max = Math.max(1, ...cells.flat());
  const cw = cell || (W - rowW - 8) / colNames.length, ch = 20;
  const H = rowNames.length * ch + 26;
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img" }, parent);
  rowNames.forEach((r, i) => {
    el("text", { x: rowW - 8, y: i * ch + 14, class: "tick", "text-anchor": "end" }, svg).textContent = r.length > 34 ? r.slice(0, 16) + "…" + r.slice(-16) : r;
    colNames.forEach((c, j) => {
      const v = cells[i][j];
      const step = v ? 1 + Math.min(8, Math.floor(Math.sqrt(v / max) * 8.999)) : 0;
      const rect = el("rect", { x: rowW + j * cw + 1, y: i * ch + 1, width: cw - 2, height: ch - 2, rx: 3, fill: `var(--seq-${step})`, class: "cell", tabindex: -1 }, svg);
      rect.addEventListener("pointermove", (ev) => showTip(ev, r, [[colFmt(c), fmt(v), null]]));
      rect.addEventListener("pointerleave", hideTip);
    });
  });
  colNames.forEach((c, j) => {
    if (colNames.length > 14 && j % 2) return;
    el("text", { x: rowW + j * cw + cw / 2, y: H - 8, class: "tick", "text-anchor": "middle" }, svg).textContent = colFmt(c);
  });
  const sc = h("div", "scale", null, parent);
  h("span", null, t("fewer"), sc);
  for (let s = 1; s <= 9; s++) h("span", "sw", null, sc).style.background = `var(--seq-${s})`;
  h("span", null, t("more", { max: fmt(max) }), sc);
  tableView(parent, colNames.map(colFmt), rowNames.map((r, i) => ({ name: r, values: cells[i] })), fmt);
}

// bubbles on a 2-D map (one series, one colour); size = weight; label the biggest
function bubbles(parent, items, { H = 420, labelTop = 14, tipLines } = {}) {
  const svg = el("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img" }, parent);
  const maxW = Math.max(...items.map((d) => d.w)), pad = 40;
  const r = (d) => 5 + 26 * Math.sqrt(d.w / maxW);
  const X = (d) => pad + d.x * (W - 2 * pad), Y = (d) => pad + d.y * (H - 2 * pad);
  const byW = [...items].sort((a, b) => b.w - a.w);
  byW.slice().reverse().forEach((d) => {
    const c = el("circle", { cx: X(d), cy: Y(d), r: r(d), class: "bub", tabindex: 0 }, svg);
    const show = (ev) => showTip(ev, d.label, tipLines(d));
    c.addEventListener("pointermove", show);
    c.addEventListener("focus", () => { const b = c.getBoundingClientRect(); show({ clientX: b.x + b.width, clientY: b.y }); });
    c.addEventListener("pointerleave", hideTip);
    c.addEventListener("blur", hideTip);
  });
  byW.slice(0, labelTop).forEach((d) => {
    el("text", { x: X(d), y: Y(d) - r(d) - 4, class: "bub-l", "text-anchor": "middle" }, svg).textContent = d.short;
  });
}
