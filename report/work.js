// Did the work land? Prompts against commits (git log --all) and vault notes, per repository and month (work.py).
/* global DATA, h, fmtN, fmtP, sum, section, lines, bubbles, t */

function workSection() {
  if (!DATA.work) return;
  const W_ = DATA.work, wm = W_.months, rs = W_.repos.filter((r) => r.clone);
  const P = sum(rs.map((r) => r.prompts)), C = sum(rs.map((r) => r.commits));
  const by = [...rs].sort((a, b) => b.per100 - a.per100), hi = by[0], lo = by.filter((r) => r.prompts >= 1000).slice(-1)[0];
  section(t("work.h"), t("work.lead", { n: rs.length, p: fmtN(P), c: fmtN(C), per: (100 * C / P).toFixed(1), hi: hi.name, hp: hi.per100, lo: lo.name, lp: lo.per100 }), (s) => {
    h("h3", null, t("work.h3a"), s);
    lines(s, wm, [{ name: t("work.prompts"), values: W_.totals.prompts }]);
    lines(s, wm, [{ name: t("work.commits"), values: W_.totals.commits, color: "var(--s2)" }]);
    lines(s, wm, [{ name: t("work.notes"), values: W_.totals.notes, color: "var(--s3)" }]);
    h("h3", null, t("work.h3b"), s);
    const maxP = Math.log10(Math.max(...rs.map((r) => r.prompts)));
    bubbles(s, rs.map((r) => ({ x: Math.log10(Math.max(10, r.prompts)) / maxP, y: 1 - Math.min(1, r.per100 / 100), w: r.prompts, label: r.name,
      short: r.name.replace(/-oracle$/, ""), r })), { labelTop: 20, tipLines: (d) => [[t("work.prompts"), fmtN(d.r.prompts), null],
      [t("work.commits"), fmtN(d.r.commits), null], [t("work.per100"), String(d.r.per100), null]] });
    h("p", "note", t("work.axes"), s);
    const tb = h("tbody", null, null, h("table", "topics", null, s));
    [...rs].sort((a, b) => b.prompts - a.prompts).forEach((r) => {
      const row = h("tr", null, null, tb);
      h("td", null, String(r.per100), row);
      const c = h("td", null, null, row);
      h("strong", null, r.name, c);
      h("span", "share", t("work.row", { p: fmtN(r.prompts), c: fmtN(r.commits), l: fmtP(r.landed), md: fmtN(r.md), n: fmtN(r.notes), a: fmtN(r.add) }), c);
    });
  }, t("work.note"));
}
