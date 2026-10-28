// Month by month: a row of jump chips after the summary tiles, and one full chapter per month at the end of the page:
// its numbers against the month before, its topics (and the ones that rose), and its own rankings.
/* global DATA, T, M, months, h, fmtN, fmtP, sum, mon, main, part, t */

{
  const D = T.month_detail || {}, P = DATA.topics;
  const tname = (x) => x.name || x.terms.slice(0, 3).join(" · ");
  const card = h("section", "card", null, null);
  main.insertBefore(card, main.querySelector(".tiles").nextSibling);
  card.id = "s-months";
  h("h2", null, t("mm.h"), card);
  h("p", "lead", t("mm.lead"), card);
  const chips = h("div", "chips", null, card);
  part("part.months");
  const chapter = h("section", "card chapter", null, main);
  chapter.id = "s-months-intro";
  h("h2", null, t("mm.chapter"), chapter);
  h("p", "lead", t("mm.chapter.lead"), chapter);

  const val = (i) => {
    const tiers = T.tiers, th = T.thinking;
    const think = th["text saved"][i] + th["signature only"][i];
    return {
      "prompts in main sessions": T.human["main session"][i],
      "prompts agent to agent": T.human["agent to agent"][i],
      "harness messages dropped": T.human["harness text"][i],
      "AI replies": T.ai_msgs[i],
      "sessions started": tiers.main[i] + tiers.subagent[i] + tiers["workflow-agent"][i],
      "main sessions": tiers.main[i],
      "rows": Object.values(T.kinds).reduce((a, v) => a + v[i], 0),
      "tool calls": T.kinds["tool calls"][i],
      "tool error rate": T.tool_error_rate[i],
      "prompts with Thai": T.thai_share[i],
      "thinking without text": think ? th["signature only"][i] / think : 0,
      "new repositories": T.new_repos[i],
      "days with prompts": (D[months[i]] || {}).active_days || 0,
      "subagent sessions": tiers.subagent[i],
      "workflow-agent sessions": tiers["workflow-agent"][i],
      "tool results": T.kinds["tool results"][i],
      "hours waiting on tools": T.tool_hours ? Object.values(T.tool_hours).reduce((a, x) => a + x[i], 0) : 0,
      "agent-hours": T.agent_hours ? Object.values(T.agent_hours).reduce((a, x) => a + x[i], 0) : 0,
      "history searches": T.search_tools ? Object.values(T.search_tools).reduce((a, x) => a + x[i], 0) : 0,
    };
  };
  const RATE = new Set(["tool error rate", "prompts with Thai", "thinking without text"]);

  function list(parent, title, items, fmt = fmtN) {
    if (!items || !items.length) return;
    const b = h("div", "mlist", null, parent);
    h("h3", null, t(title), b);
    const max = Math.max(...items.map((x) => x[1]));
    items.forEach(([k, n]) => {
      const r = h("div", "mrow", null, b);
      h("span", "mk", t(k), r);
      const bar = h("span", "mbar", null, r);
      h("span", "mfill", null, bar).style.width = Math.max(2, (n / max) * 100) + "%";
      h("span", "mn", fmt(n), r);
    });
  }

  function show(i) {
    const body = h("section", "card month", null, main);
    body.id = "m-" + months[i];
    const v = val(i), pv = i ? val(i - 1) : null, d = D[months[i]] || {};
    h("h2", "mtitle", mon(i), body);

    // one sentence: what this month was
    const tot = P ? P.topics.map((u) => u.months[P.months.indexOf(months[i])] || 0) : [];
    const all = sum(tot) || 1;
    const top = P ? P.topics.map((u, k) => [u, tot[k]]).sort((a, b) => b[1] - a[1]) : [];
    const ratio = v["prompts agent to agent"] / (v["prompts in main sessions"] || 1);
    h("p", "lead", t("mm.lead.month", { n: fmtN(v["prompts in main sessions"]), s: fmtN(v["main sessions"]), d: v["days with prompts"] }) +
      (ratio ? t("mm.lead.agents", { n: fmtN(v["prompts agent to agent"]) }) : "") +
      (top.length && top[0][1] ? t("mm.lead.topic", { t: tname(top[0][0]), p: fmtP(top[0][1] / all) }) : ""), body);

    const g = h("div", "mstats", null, body);
    for (const [k, x] of Object.entries(v)) {
      const c = h("div", "mstat", null, g);
      h("div", "msv", RATE.has(k) ? fmtP(x) : fmtN(x), c);
      h("div", "msl", t(k), c);
      if (pv) {
        const p0 = pv[k];
        let txt, cls = "flat";
        if (RATE.has(k)) {
          const dd = (x - p0) * 100;
          txt = (dd >= 0 ? "▲ " : "▼ ") + t("mm.pts", { x: Math.abs(dd).toFixed(1) });
          cls = Math.abs(dd) < 0.5 ? "flat" : dd > 0 ? "up" : "down";
        } else if (p0) {
          const dd = (x - p0) / p0;
          txt = (dd >= 0 ? "▲ " : "▼ ") + fmtP(Math.abs(dd));
          cls = Math.abs(dd) < 0.02 ? "flat" : dd > 0 ? "up" : "down";
        } else txt = x ? t("mm.new") : "–";
        h("div", "msd " + cls, t("mm.vs", { d: txt, m: months[i - 1] }), c);
      }
    }

    const cols = h("div", "mcols", null, body);
    if (P) {
      list(cols, "mm.topics", top.slice(0, 6).filter((x) => x[1]).map(([u, n]) => [tname(u), n / all]), fmtP);
      if (i) {
        const pi = P.months.indexOf(months[i - 1]);
        const ptot = sum(P.topics.map((u) => u.months[pi] || 0)) || 1;
        const rise = P.topics.map((u, k) => [u, tot[k] / all - (u.months[pi] || 0) / ptot]).sort((a, b) => b[1] - a[1])
          .slice(0, 4).filter((x) => x[1] > 0.005);
        list(cols, t("mm.rose", { m: months[i - 1] }), rise.map(([u, dd]) => [tname(u), dd]), (x) => "+" + t("mm.pts", { x: (x * 100).toFixed(1) }));
      }
    }
    list(cols, "mm.repos", d.repos);
    list(cols, "mm.skills", d.skills);
    list(cols, "mm.commands", d.commands);
    list(cols, "mm.tools", d.tools);
    list(cols, "mm.days", d.days);
    if (typeof dayList === "function") dayList(months[i], body);
  }

  months.forEach((m, i) => {
    const c = h("a", "chip", m, chips);
    c.href = "#m-" + m;
    show(i);
  });
}
