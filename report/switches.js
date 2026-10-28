// How attention moves (switches.py): projects at once, runs on one project, switches, each hour's pattern.
/* global DATA, h, fmtN, fmtP, sum, section, S, stackBars, lines, t */

function switchSection() {
  if (!DATA.switches) return;
  const X = DATA.switches, xm = X.months, pt = X.pattern_totals, tot = sum(Object.values(pt));
  const top = Object.entries(pt).sort((a, b) => b[1] - a[1])[0];
  section(t("sw.h"), t("sw.lead", { n: fmtN(X.prompts), s: fmtP(X.switch_share), runs: fmtN(X.runs.n), med: X.runs.median_len,
    hrs: fmtN(X.hours_active), top: t("sw.p." + top[0]), tp: fmtP(top[1] / tot), pk: Math.max(...X.at_once.median) }), (s) => {
    h("h3", null, t("sw.h3a"), s);
    lines(s, xm, [{ name: t("sw.median"), values: X.at_once.median }, { name: "p90", values: X.at_once.p90 }]);
    h("h3", null, t("sw.h3b"), s);
    stackBars(s, xm, Object.entries(X.patterns).map(([k, v]) => ({ name: t("sw.p." + k), values: v })), { percent: true });
    const cols = h("div", "mcols", null, s);
    for (const [title, items] of [[t("sw.pp"), X.top_pingpong], [t("sw.sw"), X.top_switches]]) {
      const b = h("div", "mlist", null, cols);
      h("h3", null, title, b);
      const max = Math.max(...items.map((x) => x[2]));
      items.slice(0, 10).forEach(([a, c, n]) => {
        const r = h("div", "mrow", null, b);
        h("span", "mk", `${a} ${title === t("sw.pp") ? "⇄" : "→"} ${c}`, r);
        h("span", "mfill", null, h("span", "mbar", null, r)).style.width = Math.max(2, (n / max) * 100) + "%";
        h("span", "mn", fmtN(n), r);
      });
    }
    const runs = X.runs.bins;
    h("p", "note", t("sw.runs", { a: fmtN(runs["1"] || 0), b: fmtN(runs["2-4"] || 0), c: fmtN(runs["5-19"] || 0), d: fmtN(runs["20+"] || 0) }), s);
  }, t("sw.note"));
}
