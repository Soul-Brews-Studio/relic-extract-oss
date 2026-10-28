// Focus: the story of your attention (each month's share of maw, relic and everything else), then one chapter per
// family: its repositories over time, its features (clusters inside its own vectors), how each repository started and
// where it stands, its tools and skills, how its follow-ups went, its lessons. focus.py writes DATA.focus.
/* global DATA, months, h, fmtN, fmtP, sum, argmax, section, part, S, stackBars, lines, heat, t */

if (DATA.focus) {
  const F = DATA.focus, fm = F.months;
  part("part.focus");
  {
    const st = F.story, tot = fm.map((_, i) => sum(Object.values(st).map((v) => v[i])));
    const share = (f) => fm.map((_, i) => (st[f][i] || 0) / (tot[i] || 1));
    const maw = share("maw"), relic = share("relic");
    const mp = argmax(maw), rp = argmax(relic);
    section(t("story.h"), t("story.lead", { mp: fm[mp], m: fmtP(maw[mp]), rp: fm[rp], r: fmtP(relic[rp]) }), (s) => {
      stackBars(s, fm, S(st), { percent: true });
    }, t("story.note"));
  }
  for (const [fam, D] of Object.entries(F.families)) {
    const fname = (f) => f.name || f.terms.slice(0, 3).join(" · ");
    const top = D.repos[0], all = D.features.reduce((a, f) => a + f.msgs, 0) || 1;
    const busiest = fm[argmax(fm.map((_, i) => sum(D.repos.map((r) => r.months[i]))))];
    section(t("fam.h", { f: fam }), t("fam.lead", { f: fam, n: fmtN(D.prompts), r: D.repos_all, top: top.name, tn: fmtN(top.prompts),
      first: D.repos.map((r) => r.first).sort()[0], peak: busiest, k: D.features.length }), (s) => {
      h("h3", null, t("fam.repos"), s);
      const tops = D.repos.slice(0, 6), rest = fm.map((_, i) => sum(D.repos.slice(6).map((r) => r.months[i])));
      stackBars(s, fm, [...tops.map((r) => ({ name: r.name, values: r.months })), { name: t("Other"), values: rest }]);
      h("h3", null, t("fam.features"), s);
      heat(s, D.features.map(fname), fm, D.features.map((f) => f.months), { colFmt: (m) => m.slice(2), rowW: 260 });
      const tb = h("tbody", null, null, h("table", "topics", null, s));
      D.features.forEach((f) => {
        const r = h("tr", null, null, tb);
        h("td", null, fmtP(f.msgs / all), r);
        const c = h("td", null, null, r);
        h("strong", null, fname(f), c);
        h("span", "share", t("fam.feat.row", { n: fmtN(f.msgs), first: f.first, peak: f.peak, repos: f.repos.map((x) => x[0]).join(", ") }), c);
        if (f.examples && f.examples.length) h("div", "ex", f.examples.slice(0, 2).map((e) => "“" + e + "”").join("   "), c);
      });
    }, t("fam.note", { k: D.k }));

    section(t("fam.arc.h", { f: fam }), t("fam.arc.lead"), (s) => {
      const tb = h("tbody", null, null, h("table", "topics", null, s));
      D.repos.slice(0, 8).forEach((r) => {
        const row = h("tr", null, null, tb);
        h("td", null, fmtN(r.prompts), row);
        const c = h("td", null, null, row);
        h("strong", null, r.name, c);
        h("span", "share", `  ${r.first} → ${r.last}`, c);
        if (r.started && r.started.length) h("div", "ex", t("fam.started") + " " + r.started.map(([d, x]) => `${d} “${x}”`).join("   "), c);
        if (r.now && r.now.length) h("div", "ex", t("fam.now") + " " + r.now.slice(0, 2).map(([d, x]) => `${d} “${x}”`).join("   "), c);
      });
      const cols = h("div", "mcols", null, s);
      for (const [title, items] of [[t("fam.tools"), D.tools], [t("fam.skills"), D.skills]]) {
        if (!items || !items.length) continue;
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
      const wk = D.works.filter((x) => x.n >= 30);
      if (wk.length > 1) {
        h("h3", null, t("works.h3a"), s);
        lines(s, wk.map((x) => x.month), [{ name: t("ship"), values: wk.map((x) => x.ship) }, { name: t("friction"), values: wk.map((x) => x.friction) }], { fmt: fmtP });
      }
      if (D.lessons && D.lessons.length) {
        h("h3", null, t("fam.lessons"), s);
        const lb = h("tbody", null, null, h("table", "topics", null, s));
        D.lessons.forEach((l) => {
          const r = h("tr", null, null, lb);
          h("td", null, l.kind, r);
          const c = h("td", null, null, r);
          h("span", null, l.text, c);
          h("div", "ex", `${l.role} · ${l.repo} · ${l.ts} · msgkey ${l.msgkey}`, c);
        });
      }
    });
  }
}
