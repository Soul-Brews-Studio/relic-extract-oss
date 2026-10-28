// Oracles: how they are born, what for, how long they live, how they are used, whether they work. oracles.py.
/* global DATA, h, fmtN, fmtP, sum, section, part, stackBars, lines, t */

if (DATA.oracles) {
  const O = DATA.oracles, om = O.months, C = O.cohorts;
  const long = C["over 90 days"], one = C["1 day"];
  const allP = sum(Object.values(C).map((c) => c.prompts)) || 1;
  part("part.oracles");
  section(t("or.h"), t("or.lead", { n: fmtN(O.n), alive: O.alive, one: one.n, long: long.n, p: fmtP(long.prompts / allP) }), (s) => {
    h("h3", null, t("or.h3born"), s);
    stackBars(s, om, Object.entries(O.births).filter(([, v]) => sum(v) > 0).map(([k, v]) => ({ name: t("or.how." + k), values: v })));
    h("h3", null, t("or.h3active"), s);
    lines(s, om, [{ name: t("or.active"), values: O.active }, { name: t("or.quiet"), values: O.went_quiet }]);
  }, t("or.note", { end: O.end }));

  section(t("or.life.h"), t("or.life.lead", { one: one.n, op: fmtN(one.prompts), long: long.n, lp: fmtN(long.prompts), la: long.alive }), (s) => {
    const tb = h("tbody", null, null, h("table", "topics", null, s));
    for (const [k, c] of Object.entries(C)) {
      const r = h("tr", null, null, tb);
      h("td", null, fmtN(c.n), r);
      const cell = h("td", null, null, r);
      h("strong", null, t(k), cell);
      h("span", "share", t("or.life.row", { alive: c.alive, p: fmtN(c.prompts), s: c.ship == null ? "–" : fmtP(c.ship), f: c.friction == null ? "–" : fmtP(c.friction) }), cell);
      h("div", "ex", t("or.bornfor") + " " + c.born_for.map(([n, v]) => `${n} (${v})`).join(" · "), cell);
    }
  }, t("or.life.note"));

  section(t("or.use.h"), t("or.use.lead", { top: O.oracles[0].name, n: fmtN(O.oracles[0].prompts_main + O.oracles[0].prompts_agent) }), (s) => {
    const cols = h("div", "mcols", null, s);
    for (const [title, items] of [[t("or.bornfor.all"), O.born_for], [t("or.skills"), O.skills]]) {
      const b = h("div", "mlist", null, cols);
      h("h3", null, title, b);
      const max = Math.max(...items.map((x) => x[1]));
      items.forEach(([k, n]) => {
        const r = h("div", "mrow", null, b);
        h("span", "mk", k, r);
        h("span", "mfill", null, h("span", "mbar", null, r)).style.width = Math.max(2, (n / max) * 100) + "%";
        h("span", "mn", fmtN(n), r);
      });
    }
    h("h3", null, t("or.top"), s);
    const tb = h("tbody", null, null, h("table", "topics", null, s));
    O.oracles.slice(0, 30).forEach((o) => {
      const r = h("tr", null, null, tb);
      h("td", null, fmtN(o.prompts_main + o.prompts_agent), r);
      const c = h("td", null, null, r);
      h("strong", null, (o.alive ? "● " : "○ ") + o.name, c);
      h("span", "share", t("or.top.row", { born: o.born, days: o.days, pm: fmtN(o.prompts_main), pa: fmtN(o.prompts_agent),
        s: o.ship == null ? "–" : fmtP(o.ship), f: o.friction == null ? "–" : fmtP(o.friction), e: o.error_rate == null ? "–" : fmtP(o.error_rate) }), c);
      if (o.born_for) h("div", "ex", t("or.bornfor") + " " + o.born_for + (o.how !== "none" ? " · " + t("or.how." + o.how) : ""), c);
    });
  }, t("or.use.note"));
}
