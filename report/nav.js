// Warp points: a map on the right naming every part, section and month; the current one is lit, a click glides
// there; Back and Next step through like slides. Keys: ] next, [ previous, t top. Also builds the overview and the data card.
/* global h, main, t, FINDINGS, tiles */

{
  // the data, last
  const dc = h("section", "card", null, main);
  dc.id = "s-data";
  h("h2", null, t("data.h"), dc);
  h("p", "lead", t("data.lead"), dc);
  h("pre", null, "duckdb -c \"select * from 'monthly.parquet' where metric = 'tiers' order by month\"", dc).tabIndex = 0;

  // the overview: every section's finding in one line, right after the tiles
  const ov = h("section", "card", null, null);
  ov.id = "s-overview";
  main.insertBefore(ov, tiles.nextSibling);
  const st = document.getElementById("s-status");  // status.js: what is being read right now, right under the overview
  if (st) main.insertBefore(st, ov.nextSibling);
  h("h2", null, t("overview.h"), ov);
  h("p", "lead", t("overview.lead"), ov);
  const ul = h("ol", "findings", null, ov);
  FINDINGS.filter((f) => f.lead).forEach((f) => {
    const li = h("li", null, null, ul);
    const a = h("a", null, f.title, li);
    a.href = "#" + f.id;
    h("span", null, " — " + f.lead, li);
  });

  const cards = [...main.querySelectorAll("section.card, div.part")];
  cards.forEach((c, i) => { if (!c.id) c.id = "s-" + i; });

  // the map on the right: every part, section and month by name (months as a compact grid), the current one lit;
  // a click glides there. On narrow screens the Map button opens it.
  const rail = h("nav", "map", null, document.body);
  rail.setAttribute("aria-label", t("warp"));
  let grid = null;
  const items = cards.map((c) => {
    const kind = c.classList.contains("month") ? "m" : c.classList.contains("part") ? "p" : "s";
    const label = kind === "p" ? c.textContent : (c.querySelector("h2")?.textContent || c.id);
    if (kind === "m" && !grid) grid = h("div", "map-g", null, rail);
    const a = h("a", "map-" + kind, kind === "m" ? c.id.slice(2) : label, kind === "m" ? grid : rail);
    a.href = "#" + c.id;
    a.setAttribute("aria-label", label);
    a.title = label;
    a.addEventListener("click", (e) => {
      e.preventDefault();
      c.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth", block: "start" });
      history.replaceState(null, "", "#" + c.id);
      rail.classList.remove("open");
      c.classList.remove("arrive");
      void c.offsetWidth;
      c.classList.add("arrive");
    });
    return [c, a];
  });

  const mb = h("button", "map-btn", t("map"), document.body);
  mb.type = "button";
  mb.onclick = () => rail.classList.toggle("open");

  let cur = -1;
  const light = () => {
    const y = innerHeight * 0.3;
    let k = 0;
    cards.forEach((c, i) => { if (c.getBoundingClientRect().top < y) k = i; });
    if (k === cur) return;
    if (cur >= 0) items[cur][1].removeAttribute("aria-current");
    items[k][1].setAttribute("aria-current", "true");
    cur = k;
  };
  let raf = 0;
  addEventListener("scroll", () => { if (!raf) raf = requestAnimationFrame(() => { raf = 0; light(); }); }, { passive: true });
  light();
  addEventListener("keydown", (e) => {
    if (e.target.closest("input, textarea, select, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return;
    const go = (i) => items[Math.max(0, Math.min(items.length - 1, i))][1].click();
    if (e.key === "]") go(cur + 1);
    else if (e.key === "[") go(cur - 1);
    else if (e.key === "t") scrollTo({ top: 0, behavior: "smooth" });
  });
  // presentation controls: Back and Next, naming where they go; the arrow keys do the same
  const pres = h("div", "pres", null, document.body);
  const back = h("button", "pres-b", null, pres), next = h("button", "pres-b pres-n", null, pres);
  back.type = next.type = "button";
  const nameOf = (i) => (items[i] ? items[i][1].getAttribute("aria-label") : "");
  const label = () => {
    back.disabled = cur <= 0;
    next.disabled = cur >= items.length - 1;
    back.textContent = "‹ " + t("pres.back");
    next.textContent = cur >= items.length - 1 ? t("pres.end") : t("pres.next", { s: nameOf(cur + 1) }) + " ›";
    back.title = nameOf(cur - 1);
  };
  back.onclick = () => items[Math.max(0, cur - 1)][1].click();
  next.onclick = () => items[Math.min(items.length - 1, cur + 1)][1].click();
  addEventListener("scroll", () => requestAnimationFrame(label), { passive: true });
  addEventListener("keydown", (e) => {
    // ← → step like slides (also right after clicking Back or Next); typing in a field is left alone
    if (e.target.closest("input, textarea, select, [contenteditable]") || e.metaKey || e.ctrlKey || e.altKey) return;
    if (e.key === "ArrowRight" || e.key === "PageDown" && e.shiftKey) { e.preventDefault(); next.click(); }
    else if (e.key === "ArrowLeft" || e.key === "PageUp" && e.shiftKey) { e.preventDefault(); back.click(); }
  });
  label();
  if (location.hash) document.getElementById(location.hash.slice(1))?.scrollIntoView();
}
