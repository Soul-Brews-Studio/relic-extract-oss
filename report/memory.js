// Memory: what the ψ vaults hold, and each oracle's vault read against its own sessions (memory.py).
/* global DATA, h, fmtN, fmtP, sum, section, S, stackBars, bubbles, t */

if (DATA.memory) {
  const Mm = DATA.memory, mm = Mm.months, os = Mm.oracles;
  const top = Mm.sections[0];
  section(t("mem.h"), t("mem.lead", { n: fmtN(Mm.totals.notes), v: Mm.totals.vaults, s: top[0], p: fmtP(top[1] / Mm.totals.notes) }),
    (s) => stackBars(s, mm, S(Mm.notes_by_month)), t("mem.note"));
  if (os.length) {
    const rec = os.map((o) => o.recall).sort((a, b) => a - b), med = rec[Math.floor(rec.length / 2)];
    const best = [...os].sort((a, b) => b.recall - a.recall)[0], worst = [...os].sort((a, b) => a.recall - b.recall)[0];
    section(t("mem.vs.h"), t("mem.vs.lead", { med: fmtP(med), b: best.name, bp: fmtP(best.recall), w: worst.name, wp: fmtP(worst.recall) }), (s) => {
      bubbles(s, os.map((o) => ({ x: o.unasked, y: 1 - o.recall, w: o.prompts, label: o.name, short: o.name.replace(/-oracle$/, ""), o })),
        { labelTop: 18, tipLines: (d) => [[t("mem.recall"), fmtP(d.o.recall), null], [t("mem.unasked"), fmtP(d.o.unasked), null],
          [t("mem.counts", { p: fmtN(d.o.prompts), n: fmtN(d.o.notes) }), "", null]] });
      h("p", "note", t("mem.axes"), s);
      const tb = h("tbody", null, null, h("table", "topics", null, s));
      os.forEach((o) => {
        const r = h("tr", null, null, tb);
        h("td", null, fmtP(o.recall), r);
        const c = h("td", null, null, r);
        h("strong", null, o.name, c);
        h("span", "share", t("mem.row", { p: fmtN(o.prompts), n: fmtN(o.notes), wn: fmtP(o.wins), u: fmtP(o.unasked), l: o.learnings, r: o.retros }), c);
        h("div", "ex", o.sections.slice(0, 5).map(([k, v]) => `${k} ${fmtN(v)}`).join(" · "), c);
      });
    }, t("mem.vs.note"));
  }
}
