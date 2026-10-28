// What is being read right now (status.py): each reading lane's windows read of all, pace, finish; then the model
// trial. nav.js puts it right under the overview, so the page opens on what is still moving.
/* global DATA, h, fmtP, section, t */

const fmtC = (v) => Number(v).toLocaleString("en-US");

function statusSection() {
  const X = DATA.status;
  if (!X) return;
  const run = X.lanes.filter((r) => r.state === "running" || r.state === "paused");
  const s = section(t("st.h"), X.night ? t("st.lead", { at: X.at, n: run.length, a: X.night[0], b: X.night[1] }) : t("st.lead2", { at: X.at, n: run.length }), (s) => {
    const tb = h("table", "st", null, s);
    const hr = h("tr", null, null, h("thead", null, null, tb));
    for (const k of ["st.lane", "st.window", "st.read", "st.pace", "st.state", "st.finish"]) h("th", null, t(k), hr);
    const body = h("tbody", null, null, tb);
    for (const r of X.lanes) {
      const tr = h("tr", "st-" + r.state, null, body);
      h("td", null, `${r.machine} · ${r.model}`, tr);
      h("td", null, t("st.unit." + r.unit) + (r.deep ? " + " + t("st.deep") : ""), tr);
      const c = h("td", "st-read", null, tr);
      const bar = h("span", "mbar", null, c);
      h("span", "mfill", null, bar).style.width = Math.max(1, Math.min(100, (r.done / Math.max(1, r.total)) * 100)) + "%";
      h("span", "mn", `${fmtC(r.done)} / ${fmtC(r.total)} (${fmtP(r.done / Math.max(1, r.total))})`, c);
      h("td", null, r.per_hour ? t("st.perh", { n: fmtC(r.per_hour) }) : "", tr);
      h("td", null, t("st.s." + r.state) + (r.new ? " " + t("st.new", { n: fmtC(r.new) }) : ""), tr);
      h("td", null, r.finish || (r.state === "queued" ? t("st.after") : ""), tr);
    }
    if (X.trial) {
      h("h3", null, t("st.trial", { n: fmtC(X.trial.windows) }), s);
      const tt = h("table", "st", null, s);
      const th = h("tr", null, null, h("thead", null, null, tt));
      for (const k of ["st.model", "st.secs", "st.agree"]) h("th", null, t(k), th);
      const tbd = h("tbody", null, null, tt);
      for (const r of X.trial.rows) {
        const tr = h("tr", r.model === X.trial.kept ? "st-kept" : null, null, tbd);
        h("td", null, r.model + (r.model === X.trial.kept ? " · " + t("st.kept") : ""), tr);
        h("td", null, r.secs == null ? "" : r.secs + " s", tr);
        h("td", null, r.agree == null ? t("st.ref") : fmtP(r.agree), tr);
      }
    }
  }, t("st.note"));
  s.id = "s-status";  // nav.js moves it right under the overview
}
statusSection();
