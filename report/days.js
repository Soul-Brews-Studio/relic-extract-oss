// Day by day (days.py): a calendar of the whole span (one cell a day, darker = more main-session prompts), and
// dayList(month, parent) for the month chapters: every day's numbers, its topics, and the gb300 model's summary.
/* global DATA, h, fmtN, fmtP, section, t, LANG, showTip, hideTip, blockList */

const DAYS = DATA.days ? Object.fromEntries(DATA.days.days.map((d) => [d.day, d])) : {};

function dayList(month, parent) {
  const ds = Object.values(DAYS).filter((d) => d.day.startsWith(month));
  if (!ds.length) return;
  h("h3", null, t("day.list", { n: ds.length }), parent);
  const tb = h("tbody", null, null, h("table", "topics days", null, parent));
  ds.forEach((d) => {
    const r = h("tr", null, null, tb);
    r.id = "d-" + d.day;
    const c0 = h("td", null, null, r);
    h("strong", null, d.day.slice(8), c0);
    h("div", "ex", t("days").split(",")[(new Date(d.day + "T12:00:00").getDay() + 6) % 7], c0);
    const c = h("td", null, null, r);
    const S_ = d.summary || {};
    const sum = S_[LANG] || S_.en;
    if (S_.type || S_.focus) {
      const top = h("div", null, null, c);
      if (S_.type) h("span", "dtype", t("day.type." + S_.type), top);
      if (S_.focus) h("strong", null, " " + S_.focus, top);
    }
    if (sum) h("div", null, sum, c);
    const extra = [["day.distraction", S_.distraction], ["day.agents", S_.agents], ["day.pattern", S_.pattern],
      ["day.work", Array.isArray(S_.work) ? S_.work.join(" · ") : S_.work]].filter(([, v]) => v && v !== "none");
    if (extra.length) h("div", "ex", extra.map(([k, v]) => t(k) + " " + v).join("  ·  "), c);
    if (d.shape) h("div", "ex", t("day.shape", { n: d.shape.projects, top: d.shape.top_project || "–", s: fmtP(d.shape.top_share || 0),
      sw: d.shape.project_switches, l: d.shape.loop_prompts }), c);
    h("div", "ex", t("day.row", { p: fmtN(d.prompts_main || 0), a: fmtN(d.prompts_agent || 0), c: fmtN(d.tool_calls || 0),
      e: fmtN(d.tool_errors || 0), hrs: (d.hours || []).length }) + (d.topics && d.topics.length ? " · " + d.topics.slice(0, 3).map((x) => x[0]).join(" · ") : "")
      + (d.repos && d.repos.length ? " · " + d.repos.slice(0, 2).map((x) => x[0]).join(", ") : ""), c);
    if (typeof blockList === "function") blockList(d.day, c);
  });
}

function daySection() {
  if (!DATA.days) return;
  const all = DATA.days.days, max = Math.max(...all.map((d) => d.prompts_main || 0)), busiest = [...all].sort((a, b) => (b.prompts_main || 0) - (a.prompts_main || 0))[0];
  section(t("day.h"), t("day.lead", { n: all.length, d0: all[0].day, d1: all[all.length - 1].day, b: busiest.day, bn: fmtN(busiest.prompts_main),
    sum: (() => { const n = all.filter((d) => d.summary).length;
      return n ? t("day.sum.yes", { s: n, m: DATA.days.model, host: DATA.days.host }) : t("day.sum.no", { m: DATA.days.model, host: DATA.days.host }); })() }), (s) => {
    const start = new Date(all[0].day + "T12:00:00"), end = new Date(all[all.length - 1].day + "T12:00:00");
    start.setDate(start.getDate() - ((start.getDay() + 6) % 7));
    const weeks = Math.ceil(((end - start) / 864e5 + 1) / 7);
    const NS = "http://www.w3.org/2000/svg", cell = 13, W = 60 + weeks * cell, H = 7 * cell + 30;
    const svg = document.createElementNS(NS, "svg");
    svg.setAttribute("viewBox", `0 0 ${W} ${H}`);
    svg.setAttribute("class", "chart cal");
    s.appendChild(svg);
    t("days").split(",").forEach((nm, i) => {
      if (i % 2) return;
      const tx = document.createElementNS(NS, "text");
      Object.entries({ x: 52, y: 22 + i * cell + 9, class: "tick", "text-anchor": "end" }).forEach(([k, v]) => tx.setAttribute(k, v));
      tx.textContent = nm; svg.appendChild(tx);
    });
    let lastM = "";
    for (let w = 0; w < weeks; w++) for (let k = 0; k < 7; k++) {
      const dt = new Date(start); dt.setDate(start.getDate() + w * 7 + k);
      if (dt > end) continue;
      const key = dt.toISOString().slice(0, 10), d = DAYS[key], v = d ? d.prompts_main || 0 : 0;
      const step = v ? 1 + Math.min(8, Math.floor(Math.sqrt(v / max) * 8.999)) : 0;
      const r = document.createElementNS(NS, "rect");
      Object.entries({ x: 60 + w * cell, y: 20 + k * cell, width: cell - 2, height: cell - 2, rx: 2, fill: `var(--seq-${step})`, class: "cell" }).forEach(([a, b]) => r.setAttribute(a, b));
      r.addEventListener("pointermove", (ev) => showTip(ev, key, [[t("tile.main"), fmtN(v), null]].concat(d && d.summary ? [[(d.summary[LANG] || d.summary.en).slice(0, 120), "", null]] : [])));
      r.addEventListener("pointerleave", hideTip);
      r.addEventListener("click", () => document.getElementById("d-" + key)?.scrollIntoView({ behavior: "smooth", block: "center" }));
      svg.appendChild(r);
      if (k === 0 && key.slice(0, 7) !== lastM) {
        lastM = key.slice(0, 7);
        const tx = document.createElementNS(NS, "text");
        Object.entries({ x: 60 + w * cell, y: 12, class: "tick" }).forEach(([a, b]) => tx.setAttribute(a, b));
        tx.textContent = t("months.short").split(",")[+lastM.slice(5) - 1]; svg.appendChild(tx);
      }
    }
    const sc = h("div", "scale", null, s);
    h("span", null, t("fewer"), sc);
    for (let i = 1; i <= 9; i++) h("span", "sw", null, sc).style.background = `var(--seq-${i})`;
    h("span", null, t("more", { max: fmtN(max) }), sc);
  }, t("day.note"));
}
