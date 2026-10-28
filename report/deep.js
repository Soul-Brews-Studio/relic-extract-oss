// The deep sections: how long things take, searching our own history, and the vector space read for patterns
// (which oracle does what, what works, growth, lessons learned). vspace.py writes DATA.vspace.
/* global DATA, T, M, months, h, fmtN, fmtP, sum, argmax, mon, section, part, S, stackBars, lines, heat, bubbles, t */

{
  const fmtS = (v) => (v < 60 ? v.toFixed(v < 10 ? 1 : 0) + " s" : (v / 60).toFixed(1) + " min");
  const fmtH = (v) => fmtN(v) + " h";
  const fmtMin = (v) => (v < 60 ? v.toFixed(1) + " min" : (v / 60).toFixed(1) + " h");

  // how long
  if (T.tool_hours) {
    const th = T.tool_hours, tot = months.map((_, i) => sum(Object.values(th).map((v) => v[i])));
    const ah = T.agent_hours, peak = argmax(months.map((_, i) => ah.main[i] + ah.subagent[i] + ah["workflow-agent"][i]));
    section(t("long.h"), t("long.lead", { n: fmtN(M.totals.tool_pairs), med: fmtS(M.totals.tool_median_s), p90: fmtS(M.totals.tool_p90_s),
      hrs: fmtN(M.totals.tool_hours), m: mon(peak), ah: fmtN(ah.main[peak] + ah.subagent[peak] + ah["workflow-agent"][peak]) }),
      (s) => {
        h("h3", null, t("long.h3a"), s);
        stackBars(s, months, S(th), { fmt: fmtH });
        h("h3", null, t("long.h3b"), s);
        lines(s, months, S(T.tool_median_s, ["Bash", "Read & search", "Edit & write", "Web", "Agents"]), { fmt: fmtS });
        h("h3", null, t("long.h3c"), s);
        lines(s, months, S(T.session_median_min), { fmt: fmtMin });
        h("h3", null, t("long.h3d"), s);
        stackBars(s, months, S(ah), { fmt: fmtH });
      }, t("long.note"));
  }

  // searching our own history
  if (T.search_tools) {
    const st = T.search_tools, sk = T.search_skills;
    const r3 = st.relic3[months.length - 1];
    section(t("hist.h"), t("hist.lead", { m: mon(st["session-viewer MCP"].findIndex((v) => v > 0)), n: fmtN(r3), m2: mon(months.length - 1) }),
      (s) => {
        h("h3", null, t("hist.h3a"), s);
        stackBars(s, months, S(st));
        h("h3", null, t("hist.h3b"), s);
        lines(s, months, S(sk, Object.keys(sk).filter((k) => sum(sk[k]) > 0)));
      }, t("hist.note"));
  }

  const V = DATA.vspace;
  if (V) {
    part("part.deep");
    const vm = V.months, at = (arr, m) => arr[vm.indexOf(m)];

    // which oracle does what
    if (V.oracles && V.oracles.length) {
      const o = V.oracles, focused = [...o].sort((a, b) => a.focus - b.focus)[0], broad = [...o].sort((a, b) => b.focus - a.focus)[0];
      section(t("orc.h"), t("orc.lead", { n: o.length, f: focused.name, fn: focused.focus, b: broad.name, bn: broad.focus }), (s) => {
        bubbles(s, o.map((x) => ({ x: x.xy[0], y: x.xy[1], w: x.prompts, label: x.name, short: x.name.replace(/-oracle$/, ""), x0: x })),
          { labelTop: 22, tipLines: (d) => [[d.x0.top.map((x) => x[0]).join(" · "), t("prompts", { n: fmtN(d.x0.prompts) }), null],
            [t("orc.near", { near: d.x0.near.join(", ") }), "", null]] });
        const tb = h("tbody", null, null, h("table", "topics", null, s));
        o.forEach((x) => {
          const r = h("tr", null, null, tb);
          h("td", null, fmtN(x.prompts), r);
          const c = h("td", null, null, r);
          h("strong", null, x.name, c);
          h("span", "share", t("orc.row", { n: x.focus, near: x.near.join(", ") }), c);
          h("div", "ex", x.top.map(([n, v]) => `${n} ${fmtP(v)}`).join("  ·  "), c);
        });
      }, t("orc.note"));

      const P = DATA.topics;
      if (P) {
        const topT = [...P.topics].sort((a, b) => b.msgs - a.msgs).slice(0, 16).map((x) => x.name || x.terms[0]);
        const rowsO = o.slice(0, 20);
        section(t("ot.h"), t("ot.lead"),
          (s) => heat(s, rowsO.map((x) => x.name), topT, rowsO.map((x) => topT.map((n) => x.mix[n] || 0)),
            { fmt: fmtP, colFmt: (c) => c.split(/[ :,]/)[0].slice(0, 9), rowW: 230 }));
      }
    }

    // what works
    if (V.works) {
      const w = V.works, bm = [...w.by_month].sort((a, b) => a.name.localeCompare(b.name));
      const shipRate = vm.map((m) => { const x = w.shipped.find((y) => y.month === m); return x && x.sessions ? x.shipped / x.sessions : 0; });
      const fr = vm.map((m) => (bm.find((x) => x.name === m) || {}).friction || 0);
      const lastF = fr[fr.length - 1], peakF = Math.max(...fr), peakM = vm[fr.indexOf(peakF)];
      section(t("works.h"), t("works.lead", { m: peakM, p: fmtP(peakF), p2: fmtP(lastF), m2: vm[vm.length - 1] }), (s) => {
        h("h3", null, t("works.h3a"), s);
        lines(s, vm, [{ name: t("ship"), values: vm.map((m) => (bm.find((x) => x.name === m) || {}).ship || 0) }, { name: t("friction"), values: fr }], { fmt: fmtP });
        h("h3", null, t("works.h3b"), s);
        lines(s, vm, [{ name: t("sessions that shipped"), values: shipRate }], { fmt: fmtP, max: 1 });
        const ranked = (list, title, k) => {
          h("h3", null, title, s);
          const tb = h("tbody", null, null, h("table", "topics", null, s));
          list.slice(0, k).forEach((x) => {
            const r = h("tr", null, null, tb);
            h("td", null, fmtN(x.n), r);
            const c = h("td", null, null, r);
            h("span", null, x.name, c);
            h("span", "share", t("works.row", { s: fmtP(x.ship), f: fmtP(x.friction) }), c);
          });
        };
        const skip = new Set([...V.ship, ...V.friction]);
        const topics = w.by_topic.filter((x) => !skip.has(x.name));
        ranked([...topics].sort((a, b) => a.friction - b.friction), t("works.least"), 8);
        ranked([...topics].sort((a, b) => b.friction - a.friction), t("works.most"), 8);
        if (w.by_oracle.length) ranked([...w.by_oracle].sort((a, b) => (b.ship - b.friction) - (a.ship - a.friction)), t("works.oracles"), 12);
        h("h3", null, t("works.next"), s);
        const tb = h("tbody", null, null, h("table", "topics", null, s));
        w.flows.slice(0, 14).forEach((f) => {
          const r = h("tr", null, null, tb);
          h("td", null, fmtN(f.n), r);
          h("td", null, `${f.src}  →  ${f.dst}`, r);
        });
      }, t("works.note"));
    }

    if (typeof workSection === "function") workSection();
    if (typeof switchSection === "function") switchSection();

    // growth
    if (V.growth) {
      const g = V.growth;
      const topB = argmax(g.breadth);
      section(t("grow.h"), t("grow.lead", { a: g.breadth[0], m: vm[0], b: g.breadth[topB], m2: vm[topB], m3: vm[vm.length - 1],
        p: fmtP(g.novel[g.novel.length - 1]) }), (s) => {
        h("h3", null, t("grow.h3a"), s);
        lines(s, vm, [{ name: t("topics"), values: g.breadth }], { fmt: (v) => v.toFixed(0) });
        h("h3", null, t("grow.h3b"), s);
        lines(s, vm, [{ name: t("new: nothing earlier within 0.80"), values: g.novel }, { name: t("repeat: an earlier one within 0.97"), values: g.repeat }], { fmt: fmtP, max: 1 });
        h("h3", null, t("grow.h3c"), s);
        lines(s, vm, [{ name: t("cosine distance"), values: g.drift }], { fmt: (v) => v.toFixed(3) });
        h("h3", null, t("grow.h3d"), s);
        stackBars(s, vm, [{ name: t("active repositories"), values: g.oracles_active }]);
      }, t("grow.note"));
    }

    // lessons
    if (V.lessons && Object.keys(V.lessons).length) {
      section(t("less.h"), t("less.lead"), (s) => {
        [...vm].reverse().forEach((m) => {
          const ls = V.lessons[m] || [];
          if (!ls.length) return;
          h("h3", null, mon(months.indexOf(m)) || m, s);
          const tb = h("tbody", null, null, h("table", "topics", null, s));
          ls.forEach((l) => {
            const r = h("tr", null, null, tb);
            h("td", null, l.kind, r);
            const c = h("td", null, null, r);
            h("span", null, l.text, c);
            h("div", "ex", `${l.role} · ${l.cat} · ${l.ts} · msgkey ${l.msgkey}`, c);
          });
        });
      }, t("less.note"));
    }
  }
}

// ask the index: example questions answered by the fused search (asks.py)
if (DATA.asks && DATA.asks.asks.length) {
  section(t("ask.h"), t("ask.lead"), (s) => {
    DATA.asks.asks.forEach((a) => {
      h("h3", null, "“" + a.q + "”", s);
      const tb = h("tbody", null, null, h("table", "topics", null, s));
      a.hits.forEach((x) => {
        const r = h("tr", null, null, tb);
        h("td", null, (x.both ? "= " : "") + t(x.stream), r);
        const c = h("td", null, null, r);
        h("span", null, x.text, c);
        h("div", "ex", `${x.ts} · ${x.who} · ${x.repo} · msgkey ${x.msgkey}`, c);
      });
    });
    h("pre", null, 'just search "your question" -n 15 --stream text,thinking,tools --month 2026-10', s).tabIndex = 0;
  }, t("ask.note"));
}
