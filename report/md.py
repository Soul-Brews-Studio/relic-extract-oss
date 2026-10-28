"""The report as Markdown for an AI to read: what is running now, then the year in tables.

    uv run python report/md.py ~/.relic-extract/report-share     # report.json -> index.md + llms.txt beside it

Reads the share view's report.json (no quoted text, no session id) and writes only counts, types, project names and
model names from it. just publish serves it at relic.buildwithoracle.com/report/index.md, with llms.txt pointing to it.
"""

import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

URL = "https://relic.buildwithoracle.com/report"
TYPES = ("build", "fix", "ops", "research", "writing", "planning", "mixed")


def pct(a, b):
    return f"{a / b:.0%}" if b else "-"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def mix(c):
    n = sum(c.values())
    return " · ".join(f"{k} {pct(v, n)}" for k, v in c.most_common(4)) if n else "-"


def main():
    d = Path(sys.argv[1] if len(sys.argv) > 1 else "~/.relic-extract/report-share").expanduser()
    R = json.loads((d / "report.json").read_text())
    M, tot = R["meta"], R["meta"]["totals"]
    md = [f"# A year of agent work, month by month", "",
          f"Built {M['built']} (Bangkok), months {M['months'][0]} to {M['months'][-1]}, {M.get('view')} view: every number, no quoted text.",
          f"Human page: {URL}/ · data: {URL}/report.json (the page's own data) · monthly values: {URL}/monthly.parquet", "",
          "Source: every Claude Code / Codex transcript relic-scan found on Nat's machines, cleaned by relic-extract, "
          "embedded on gb300, counted. Each window of time (day, 3h, 1h, 15m) is read by a model, which says what kind "
          "of work it was (build, fix, ops, research, writing, planning, mixed).", "",
          "## Totals", "",
          table(["what", "count"], [["rows in the index", f"{tot['rows']:,}"], ["sessions", f"{tot['sessions']:,}"],
                                    ["prompts in main sessions", f"{tot['main_msgs']:,}"], ["prompts one agent wrote for another", f"{tot['agent_msgs']:,}"],
                                    ["tool calls", f"{tot['tool_calls']:,}"], ["repositories", f"{tot['repos']:,}"]]), ""]
    S = R.get("status")
    if S:
        md += ["## What we are working on now", "",
               f"As of {S['at']} (Bangkok). " + (f"gb300 lanes run one at a time, {S['night'][0]}:00-0{S['night'][1]}:00 only; z.ai lanes day and night. " if S.get("night") else "On each machine the lanes run one after another, day and night. ") +
               "Finish = windows left / answers in the last hour, counted in the lane's own hours.", "",
               table(["machine", "model", "window", "read", "of", "share", "per hour", "state", "finish (Bangkok)"],
                     [[r["machine"], r["model"], r["unit"] + (" + friction, lesson" if r["deep"] else ""), f"{r['done']:,}", f"{r['total']:,}",
                       pct(r["done"], r["total"]), r["per_hour"] or "", r["state"] + (f" ({r['new']} new since)" if r.get("new") else ""),
                       r.get("finish") or ""] for r in S["lanes"]]), ""]
        if S.get("trial"):
            T = S["trial"]
            md += [f"### Model trial: the same {T['windows']} 3-hour windows read by five models", "",
                   table(["model", "median s per window", "same type as z.ai"],
                         [[r["model"] + (" (kept for gb300)" if r["model"] == T["kept"] else ""), r["secs"],
                           "reference" if r["agree"] is None else pct(r["agree"], 1)] for r in T["rows"]]), ""]
    D = (R.get("days") or {}).get("days") or []
    if D:
        by = defaultdict(lambda: dict(days=0, prompts=0, tools=0, ty=Counter()))
        for x in D:
            b = by[x["day"][:7]]
            b["days"] += 1
            b["prompts"] += x.get("prompts_main") or 0
            b["tools"] += x.get("tool_calls") or 0
            ty = (x.get("summary") or {}).get("type")
            if ty:
                b["ty"][ty] += 1
        md += ["## Month by month (days.py, one reading per day)", "",
               table(["month", "active days", "main prompts", "tool calls", "day types"],
                     [[m, b["days"], f"{b['prompts']:,}", f"{b['tools']:,}", mix(b["ty"])] for m, b in sorted(by.items())]), ""]
    W = R.get("windows") or {}
    if W:
        rows = []
        for name, lane in W.items():
            ty = Counter(r[2] for r in lane["rows"] if r[2] in TYPES)
            rows.append([name, lane.get("model"), f"{len(lane['rows']):,}", mix(ty)])
        md += ["## Window by window (what each window of time was)", "",
               table(["lane", "model", "windows", "types"], rows), ""]
        lane = W.get("1h zai") or next(iter(W.values()))
        pm = defaultdict(Counter)
        for r in lane["rows"]:
            pm[r[0][:7]][r[2]] += 1
        md += [f"### {lane['unit']} windows on {lane['host']} by month", "",
               table(["month", "windows", "types"], [[m, sum(c.values()), mix(c)] for m, c in sorted(pm.items())]), ""]
    X = R.get("switches")
    if X:
        md += ["## How attention moves (projects at once, switches)", "",
               f"{X['prompts']:,} main-session prompts in time order; {X['switch_share']:.0%} land in a different project from the one before. "
               f"{X['runs']['n']:,} runs on one project, median {X['runs']['median_len']} prompt(s). {X['hours_active']:,} active hours.", "",
               table(["month", "median projects at once (per active hour)", "p90"],
                     [[m, a, b] for m, a, b in zip(X["months"], X["at_once"]["median"], X["at_once"]["p90"])]), "",
               "Most frequent switches: " + ", ".join(f"{a} -> {b} ({n:,})" for a, b, n in X["top_switches"][:10]), ""]
    md += ["## Notes", "",
           "- Counts are measured from the index; nothing is estimated except the finish times above.",
           "- The share view drops every quoted passage and every session id; project names stay.",
           f"- Machine-readable: {URL}/report.json has every chart's numbers (same data as the page).", ""]
    (d / "index.md").write_text("\n".join(md))
    (d / "llms.txt").write_text(f"# relic report\n\n> A year of agent work, month by month: counts, window readings, what is running now.\n\n"
                                f"- [The report as Markdown]({URL}/index.md)\n- [All the page's data as JSON]({URL}/report.json)\n"
                                f"- [Monthly values as Parquet]({URL}/monthly.parquet)\n- [The page for people]({URL}/)\n")
    print(f"md: {len(md)} lines -> {d}/index.md, llms.txt")


if __name__ == "__main__":
    main()
