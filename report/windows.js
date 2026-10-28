// Window by window (blocks.py, readings.py): what each 3-hour, 1-hour or 15-minute window was, as the model read it.
// One lane at a time (3h gb300, 1h gb300, 15m z.ai deep, …): a grid of days (columns) by windows of the day (rows),
// coloured by the window's type; the type mix; the day-vs-blocks comparison; friction and lessons where read.
// blockList(day, parent) puts a day's windows under it in its month chapter.
/* global DATA, h, fmtN, fmtP, section, t, showTip, hideTip, legend */

const WIN = DATA.windows || {};
const WTYPES = ["build", "fix", "ops", "research", "writing", "planning", "mixed"];
const wcolor = (ty) => `var(--s${WTYPES.indexOf(ty) + 1})`;
const fmtI = (v) => Number(v).toLocaleString("en-US");
const sids = (a) => a.slice(0, 4).map((x) => x.slice(0, 8)).join(" ") + (a.length > 4 ? ` +${a.length - 4}` : "");
const hm = (m) => String(Math.floor(m / 60)).padStart(2, "0") + ":" + String(m % 60).padStart(2, "0");
const SPAN = { "3h": 180, "1h": 60, "15m": 15, day: 1440 };
// The day's top project against a lane's windows, one rule everywhere (table, paragraph, blocks.py): on days with 3+
// windows, the share of a day's windows its top project leads, averaged over those days; and how many different
// projects lead a window on such a day. Windows with no project are left out of both.
function leadStats(rows, dayTop) {
  const by = {};
  rows.forEach((r) => { (by[r[0]] = by[r[0]] || []).push(r); });
  const days = Object.entries(by).filter(([d, rs]) => rs.length >= 3 && dayTop[d]).map(([d, rs]) => [d, rs.filter((r) => r[3])]).filter(([, rs]) => rs.length);
  if (!days.length) return null;
  return { lead: days.reduce((a, [d, rs]) => a + rs.filter((r) => r[3] === dayTop[d]).length / rs.length, 0) / days.length,
    fp: days.reduce((a, [, rs]) => a + new Set(rs.map((r) => r[3])).size, 0) / days.length, days: days.length };
}
const dayTops = () => Object.fromEntries((DATA.days ? DATA.days.days : []).map((d) => [d.day, d.shape ? d.shape.top_project : null]));
const laneOrder = (k) => ["3h", "1h", "15m", "day"].indexOf(WIN[k].unit) * 10 + (WIN[k].host === "gb300" ? 0 : 1);

function winGrid(parent, lane) {
  const L = WIN[lane], span = SPAN[L.unit], slots = 1440 / span;
  const days = [...new Set(L.rows.map((r) => r[0]))].sort();
  const di = Object.fromEntries(days.map((d, i) => [d, i]));
  const cw = Math.max(2, Math.min(8, Math.floor(1100 / days.length))), ch = Math.max(3, Math.min(14, Math.floor(300 / slots)));
  const NS = "http://www.w3.org/2000/svg", X0 = 44, Y0 = 16, Wd = X0 + days.length * cw + 4, Ht = Y0 + slots * ch + 4;
  const svg = document.createElementNS(NS, "svg");
  svg.setAttribute("viewBox", `0 0 ${Wd} ${Ht}`);
  svg.setAttribute("class", "chart wgrid");
  parent.appendChild(svg);
  const tick = (x, y, txt, anchor = "start") => {
    const tx = document.createElementNS(NS, "text");
    Object.entries({ x, y, class: "tick", "text-anchor": anchor }).forEach(([a, b]) => tx.setAttribute(a, b));
    tx.textContent = txt; svg.appendChild(tx);
  };
  for (let s = 0; s < slots; s += Math.max(1, Math.round(slots / 8))) tick(X0 - 6, Y0 + s * ch + ch, hm(s * span), "end");
  let lastM = "", lastX = -99;
  days.forEach((d, i) => {
    if (d.slice(0, 7) === lastM) return;
    lastM = d.slice(0, 7);
    const x = X0 + i * cw;
    if (x - lastX < 30) return;  // early months have few active days: skip a label that would sit on the previous one
    lastX = x; tick(x, 10, t("months.short").split(",")[+lastM.slice(5) - 1]);
  });
  L.rows.forEach((r) => {
    const rect = document.createElementNS(NS, "rect");
    Object.entries({ x: X0 + di[r[0]] * cw, y: Y0 + (r[1] / span) * ch, width: Math.max(1, cw - (cw > 3 ? 1 : 0)), height: Math.max(1, ch - 1),
      fill: wcolor(r[2]), class: "cell" }).forEach(([a, b]) => rect.setAttribute(a, b));
    rect.addEventListener("pointermove", (ev) => showTip(ev, `${r[0]} ${hm(r[1])}-${hm(r[1] + span)}`,
      [[t("day.type." + r[2]), r[3] || "", null], [t("win.prompts"), fmtN(r[4]), null]].concat(r[5] ? [[r[5].slice(0, 140), "", null]] : []).concat(r[8] ? [[t("win.sess"), sids(r[8]), null]] : [])));
    rect.addEventListener("pointerleave", hideTip);
    rect.addEventListener("click", () => document.getElementById("d-" + r[0])?.scrollIntoView({ behavior: "smooth", block: "center" }));
    svg.appendChild(rect);
  });
  const lg = h("div", "legend", null, parent);
  WTYPES.forEach((ty) => {
    const n = L.rows.filter((r) => r[2] === ty).length;
    if (!n) return;
    const it = h("span", "lg-i", null, lg);
    h("span", "lg-box", null, it).style.background = wcolor(ty);
    h("span", null, `${t("day.type." + ty)} ${fmtI(n)}`, it);
  });
}

function winSection() {
  const lanes = Object.keys(WIN).sort((a, b) => laneOrder(a) - laneOrder(b));
  if (!lanes.length) return;
  const b3 = WIN["3h gb300"];
  section(t("win.h"), t("win.lead", { lanes: lanes.map((k) => `${k} (${fmtI(WIN[k].rows.length)})`).join(" · ") }), (s) => {
    const bar = h("div", "lanes", null, s), body = h("div", null, null, s);
    const show = (k) => {
      body.textContent = "";
      [...bar.children].forEach((b) => b.classList.toggle("on", b.dataset.k === k));
      const L = WIN[k];
      h("p", "ex", t("win.lane", { n: fmtI(L.rows.length), u: L.unit, d: fmtN(new Set(L.rows.map((r) => r[0])).size), m: L.model, host: L.host }), body);
      winGrid(body, k);
      const fr = L.rows.filter((r) => r[6] && r[6] !== "none"), le = L.rows.filter((r) => r[7] && r[7] !== "none");
      if (fr.length || le.length) {
        h("h3", null, t("win.deep", { f: fmtN(fr.length), l: fmtN(le.length) }), body);
        const tb = h("tbody", null, null, h("table", "topics", null, body));
        [...le].sort((a, b) => b[4] - a[4]).slice(0, 24).forEach((r) => {
          const tr = h("tr", null, null, tb);
          h("td", "ex", `${r[0]} ${hm(r[1])} · ${r[3] || ""}${r[8] ? " · " + sids(r[8]) : ""}`, tr);
          const c = h("td", null, null, tr);
          h("div", null, r[7], c);
          if (r[6] && r[6] !== "none") h("div", "ex", t("win.friction") + " " + r[6], c);
        });
      }
    };
    lanes.forEach((k) => { const bt = h("button", "lane", k, bar); bt.dataset.k = k; bt.addEventListener("click", () => show(k)); });
    show(lanes[0]);
    if (b3 && b3.summary) {
      const S = b3.summary;
      h("h3", null, t("win.vs.h"), s);
      const ls = leadStats(b3.rows, dayTops()) || { lead: S.day_top_leads, fp: S.mean_focus_projects };
      h("p", null, t("win.vs", { n: fmtI(S.blocks), d: fmtN(S.days), lead: fmtP(ls.lead), fp: ls.fp.toFixed(2),
        dt: (S.day_types[0] || [])[1], dtn: fmtP(((S.day_types[0] || [])[1] || 0) / S.days),
        bt: fmtP(((S.types[0] || [])[1] || 0) / S.blocks) }), s);
    }
    winCompare(s);
  }, t("win.note"));
}

// Every lane side by side, the day readings as one more row: how the type mix and the lead project move as the
// window shrinks; and where two models read the same windows, how often they give the same type.
function winCompare(parent) {
  const L = { ...WIN };
  if (DATA.days) L["day gb300"] = { unit: "day", host: "gb300", rows: DATA.days.days.filter((d) => d.summary && d.summary.type)
    .map((d) => [d.day, 0, d.summary.type, d.shape ? d.shape.top_project : null, d.prompts_main || 0]) };
  const order = (k) => ["day", "3h", "1h", "15m"].indexOf(L[k].unit) * 10 + (L[k].host === "gb300" ? 0 : 1) + (L[k].deep ? 2 : 0);
  const keys = Object.keys(L).filter((k) => L[k].rows.length).sort((a, b) => order(a) - order(b));
  if (keys.length < 2) return;
  const dayTop = dayTops();
  h("h3", null, t("win.cmp.h"), parent);
  const tbl = h("table", "topics", null, parent), hr = h("tr", null, null, h("thead", null, null, tbl)), tb = h("tbody", null, null, tbl);
  t("win.cmp.cols").split(",").forEach((c) => h("th", null, c, hr));
  keys.forEach((k) => {
    const R = L[k].rows, n = R.length, by = {};
    R.forEach((r) => { (by[r[0]] = by[r[0]] || []).push(r); });
    const share = (...ty) => fmtP(R.filter((r) => ty.includes(r[2])).length / n);
    const ls = L[k].unit === "day" ? null : leadStats(R, dayTop);
    const lead = ls ? fmtP(ls.lead) : "—", fp = ls ? ls.fp.toFixed(2) : "—";
    const tr = h("tr", null, null, tb);
    [k, fmtI(n), fmtI(Object.keys(by).length), share("build"), share("ops"), share("fix"), share("research", "writing"), share("mixed", "planning"), lead, fp]
      .forEach((v, i) => h("td", i ? "num" : null, v, tr));
  });
  keys.forEach((a, i) => keys.slice(i + 1).forEach((b) => {
    if (L[a].unit !== L[b].unit || L[a].host === L[b].host) return;
    const ta = Object.fromEntries(L[a].rows.map((r) => [r[0] + " " + r[1], r[2]]));
    const both = L[b].rows.filter((r) => ta[r[0] + " " + r[1]]);
    if (both.length < 30) return;
    const split = {};
    both.forEach((r) => { const x = ta[r[0] + " " + r[1]]; if (x !== r[2]) split[x + " / " + r[2]] = (split[x + " / " + r[2]] || 0) + 1; });
    const top = Object.entries(split).sort((p, q) => q[1] - p[1])[0];
    h("p", null, t("win.agree", { u: L[a].unit, a, b, n: fmtI(both.length), p: fmtP(both.filter((r) => ta[r[0] + " " + r[1]] === r[2]).length / both.length),
      x: top ? `${top[0]} (${fmtI(top[1])})` : "—" }), parent);
  }));
  h("p", "ex", t("win.cmp.note"), parent);
}

function blockList(day, parent) {
  const L = WIN["3h gb300"];
  if (!L) return;
  const rows = L.rows.filter((r) => r[0] === day);
  if (!rows.length) return;
  const box = h("div", "blocks", null, parent);
  rows.forEach((r) => {
    const ln = h("div", "ex", null, box);
    const sw = h("span", "lg-box", null, ln); sw.style.background = wcolor(r[2]);
    h("span", null, ` ${hm(r[1])}-${hm(r[1] + 180)} ${t("day.type." + r[2])}${r[3] ? " · " + r[3] : ""}${r[5] ? " — " + r[5] : ""}${r[8] ? " · " + sids(r[8]) : ""}`, ln);
  });
}
