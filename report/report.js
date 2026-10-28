// The page, Part 1: one section per perspective, each with a measured headline sentence and a chart.
// Every word goes through t() (i18n.js). section() also records each headline for the overview card.
/* global DATA, stackBars, lines, heat, bubbles, h, fmtN, fmtP, t, LANG */

const M = DATA.meta, T = DATA.t, months = M.months;
const S = (obj, names) => (names || Object.keys(obj)).map((n) => ({ name: t(n), values: obj[n] }));
const sum = (a) => a.reduce((x, y) => x + y, 0);
const argmax = (a) => a.indexOf(Math.max(...a));
const MONTHS_LONG = t("months.long").split(",");
const mon = (i) => {
  if (i < 0 || !months[i]) return "–";
  const [y, m] = months[i].split("-");
  return MONTHS_LONG[+m - 1] + " " + (LANG === "th" ? +y + 543 : y);
};
const last = months.length - 1;
const FINDINGS = [];

const main = document.getElementById("main");
document.getElementById("title").textContent = t("title");
document.getElementById("intro").textContent = t("intro");
document.getElementById("footer").textContent = t("footer");
document.title = "Relic · " + t("title");
document.getElementById("built").textContent = t("built", { built: M.built, from: months[0], to: months[last],
  view: t(M.public ? "view.public" : M.view === "share" ? "view.share" : "view.private") });
{
  const b = document.getElementById("lang");
  b.textContent = t("lang.button");
  b.onclick = () => {
    // remember the choice, drop any ?lang= from the address, and reload in place (the #section is kept)
    localStorage.setItem("relic-report-lang", LANG === "th" ? "en" : "th");
    const u = new URL(location.href);
    u.searchParams.delete("lang");
    history.replaceState(null, "", u.toString());
    location.reload();
  };
}

// hero tiles
const tiles = h("div", "tiles", null, main);
for (const [v, l] of [
  [M.totals.rows, "tile.rows"], [M.totals.sessions, "tile.sessions"], [M.totals.main_msgs, "tile.main"],
  [M.totals.agent_msgs, "tile.agent"], [M.totals.tool_calls, "tile.calls"], [M.totals.repos, "tile.repos"],
]) {
  const c = h("div", "tile", null, tiles);
  h("div", "tile-v", fmtN(v), c);
  h("div", "tile-l", t(l), c);
}

function part(key) {
  const d = h("div", "part", t(key), main);
  d.id = "part-" + key.split(".")[1];
  return d;
}

function section(title, lead, draw, note) {
  const s = h("section", "card", null, main);
  s.id = "s-" + FINDINGS.length + "-" + title.replace(/\W+/g, "-").toLowerCase().slice(0, 24);
  h("h2", null, title, s);
  if (lead) h("p", "lead", lead, s);
  draw(s);
  if (note) h("p", "note", note, s);
  FINDINGS.push({ id: s.id, title, lead });
  return s;
}

part("part.year");

// 1. what the index holds
{
  const k = T.kinds, tot = months.map((_, i) => sum(Object.values(k).map((v) => v[i])));
  section(t("kinds.h"), t("kinds.lead", { m: mon(argmax(tot)), n: fmtN(Math.max(...tot)),
    p: fmtP(sum(k["tool calls"].concat(k["tool results"])) / sum(tot)) }), (s) => stackBars(s, months, S(k)), t("kinds.note"));
}

// 2. who writes the prompts
{
  const u = T.human, c = T.chars;
  const ratio = months.map((_, i) => (c["agent to agent"][i] || 0) / (c["main session"][i] || 1));
  const top = argmax(ratio);
  section(t("who.h"), t("who.lead", { m: mon(top), x: ratio[top].toFixed(1), n: fmtN(M.totals.harness_msgs) }), (s) => {
    h("h3", null, t("who.h3a"), s);
    stackBars(s, months, S(u));
    h("h3", null, t("who.h3b"), s);
    lines(s, months, S(c));
  }, t("who.note"));
}

// 3. agents spawning agents
{
  const tr = T.tiers, tot = months.map((_, i) => tr.main[i] + tr.subagent[i] + tr["workflow-agent"][i]);
  const peak = argmax(tr["workflow-agent"]);
  section(t("tiers.h"), t("tiers.lead", { m: mon(peak), wf: fmtN(tr["workflow-agent"][peak]), sub: fmtN(tr.subagent[peak]),
    main: fmtN(tr.main[peak]), p: fmtP(tr.main[peak] / tot[peak]) }), (s) => stackBars(s, months, S(tr)), t("tiers.note"));
}

// 4. engines
{
  const e = T.engines, cp = argmax(e.codex);
  section(t("eng.h"), t("eng.lead", { m: mon(cp), n: fmtN(e.codex[cp]), m2: mon(e.other.findIndex((v) => v > 0)) }),
    (s) => stackBars(s, months, S(e)));
}

// 5. tools
{
  const tf = T.tools, tot = months.map((_, i) => sum(Object.values(tf).map((v) => v[i])));
  section(t("tools.h"), t("tools.lead", { p: fmtP(sum(tf.Bash) / sum(tot)), n: fmtN(sum(tot)),
    lo: fmtP(Math.min(...T.tool_error_rate.filter((v) => v > 0))), hi: fmtP(Math.max(...T.tool_error_rate)) }), (s) => {
    h("h3", null, t("tools.h3a"), s);
    stackBars(s, months, S(tf), { percent: true });
    h("h3", null, t("tools.h3b"), s);
    lines(s, months, [{ name: t("error rate"), values: T.tool_error_rate }], { fmt: fmtP });
  }, t("tools.note"));
}

// 6. thinking
{
  const th = T.thinking, i0 = th["signature only"].findIndex((v, i) => v > th["text saved"][i]);
  const all = sum(th["signature only"]) + sum(th["text saved"]);
  section(t("think.h"), t("think.lead", { m: mon(i0), p: fmtP(sum(th["signature only"]) / all), n: fmtN(all) }),
    (s) => stackBars(s, months, S(th)), t("think.note"));
}

// 7. rhythm
{
  const days = t("days").split(",");
  const byHour = Array.from({ length: 24 }, (_, hr) => sum(T.rhythm.map((d) => d[hr])));
  section(t("rhythm.h"), t("rhythm.lead", { busy: argmax(byHour), quiet: argmax(byHour.map((v) => -v)), day: days[argmax(T.rhythm.map(sum))] }),
    (s) => heat(s, days, Array.from({ length: 24 }, (_, i) => i), T.rhythm, { colFmt: (c) => String(c).padStart(2, "0"), rowW: 70 }),
    t("rhythm.note"));
}

// day by day: the calendar (days.js)
if (typeof daySection === "function") daySection();
if (typeof winSection === "function") winSection();

// 8. language
{
  const ts = T.thai_share, top = argmax(ts);
  section(t("thai.h"), t("thai.lead", { p: fmtP(ts[top]), m: mon(top), p2: fmtP(ts[last]), m2: mon(last) }),
    (s) => lines(s, months, [{ name: t("prompts with Thai"), values: ts }], { fmt: fmtP, max: Math.min(1, Math.ceil(Math.max(...ts) / 0.2) * 0.2 || 1) }));
}

// 9. session shape
{
  const z = T.session_size, tot = sum(months.map((_, i) => sum(Object.values(z).map((v) => v[i]))));
  section(t("shape.h"), t("shape.lead", { p: fmtP(sum(z["1 prompt"]) / tot), p2: fmtP(sum(z["5 or more"]) / tot) }),
    (s) => stackBars(s, months, S(z), { percent: true }));
}

// topics: what the prompts were about (scan -> extract -> embed -> k-means on the vectors)
if (DATA.topics) {
  const P = DATA.topics, tm = P.months;
  const name = (x) => x.name || x.terms.slice(0, 3).join(" · ");
  const byPeak = [...P.topics].sort((a, b) => argmax(a.months) - argmax(b.months) || b.msgs - a.msgs);
  const share = (x, i) => x.months[i] / (sum(P.topics.map((u) => u.months[i])) || 1);
  const recent = (x) => sum(x.months.slice(-3)) / (sum(P.topics.map((u) => sum(u.months.slice(-3)))) || 1);
  const before = (x) => sum(x.months.slice(0, -3)) / (sum(P.topics.map((u) => sum(u.months.slice(0, -3)))) || 1);
  const rise = [...P.topics].sort((a, b) => (recent(b) - before(b)) - (recent(a) - before(a)));
  section(t("topics.h"), t("topics.lead", { k: P.topics.length, n: fmtN(P.with_vector), up: name(rise[0]), down: name(rise[rise.length - 1]) }),
    (s) => {
      h("h3", null, t("topics.h3"), s);
      heat(s, byPeak.map(name), tm, byPeak.map((x) => x.months), { colFmt: (m) => m.slice(2), rowW: 260 });
    }, t("topics.note", { v: P.vectors, k: P.k }));
  section(t("mtop.h"), t("mtop.lead"), (s) => {
    const tb = h("tbody", null, null, h("table", "topics", null, s));
    tm.forEach((m, i) => {
      const top = [...P.topics].sort((a, b) => b.months[i] - a.months[i]).slice(0, 3).filter((u) => u.months[i] > 0);
      const r = h("tr", null, null, tb);
      h("td", null, m, r);
      const c = h("td", null, null, r);
      top.forEach((u, j) => {
        if (j) c.appendChild(document.createTextNode("  ·  "));
        h("span", null, name(u), c);
        h("span", "share", " " + fmtP(share(u, i)), c);
      });
    });
  });
  section(t("tmap.h"), t("tmap.lead"),
    (s) => bubbles(s, P.topics.map((x) => ({ x: x.xy[0], y: x.xy[1], w: x.msgs, label: name(x), short: x.name || x.terms[0], x0: x })),
      { tipLines: (d) => [[d.x0.terms.join(", "), t("prompts", { n: fmtN(d.x0.msgs) }), null],
        ...(d.x0.examples || []).map((e) => ["", "“" + e.slice(0, 90) + "”", null])] }), t("tmap.note"));
}

// names (private view only)
if (T.skills) {
  section(t("skills.h"), t("skills.lead", { s: T.skills.names[0] }),
    (s) => heat(s, T.skills.names, months, T.skills.names.map((n) => T.skills.cells[n]), { colFmt: (m) => m.slice(2), rowW: 230 }));
}
if (T.repos) {
  section(t("repos.h"), t("repos.lead", { n: T.repos.names.length }),
    (s) => heat(s, T.repos.names, months, T.repos.names.map((n) => T.repos.cells[n]), { colFmt: (m) => m.slice(2), rowW: 230 }));
}
section(t("newrepo.h"), t("newrepo.lead", { n: fmtN(sum(T.new_repos)) }),
  (s) => stackBars(s, months, [{ name: t("new repositories"), values: T.new_repos }]));
