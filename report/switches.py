"""How attention moves: projects at once, runs on one project, switches between projects, and each hour's pattern.

    uv run python report/switches.py   -> ~/.relic-extract/report/switches.json

Every main-session prompt (a person's or a loop's) in time order across all sessions, each with its project (cat).
  at once   distinct projects with a prompt in the same clock hour
  run       consecutive prompts on one project before the next prompt is elsewhere (length and minutes)
  switch    two consecutive prompts in different projects; ping-pong = A, B, A within three prompts
  pattern   each active hour: deep (1 project, 10+ prompts) · focused (1-2 projects) · juggling (4+ projects)
            · ping-pong (3+ A-B-A returns) · loop (half of its prompts repeat a text seen 3+ times that day)
No model: all counted.
"""

import json
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import common

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"


def main():
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    rows = con.execute("""select x.ts_th, coalesce(ses.cat, x.cat) AS cat, x.key from x left join ses using (sid)
                          where x.role = 'user' and x.chunk = 0 and x.tier = 'main' and x.ts_th <> ''
                          and coalesce(ses.cat, x.cat) is not null order by x.ts_th""").fetchall()
    rep = Counter((ts[:10], k) for ts, _, k in rows)
    months = sorted({ts[:7] for ts, _, _ in rows})
    mi = {m: i for i, m in enumerate(months)}

    # runs and switches
    runs, run_len, run_start = [], 0, None
    switches = Counter()
    pingpong = Counter()
    prev = None
    for i, (ts, cat, _) in enumerate(rows):
        if cat == prev:
            run_len += 1
        else:
            if prev is not None:
                runs.append((prev, run_len, run_start, rows[i - 1][0]))
                switches[(prev, cat)] += 1
                if i >= 2 and rows[i - 2][1] == cat:
                    pingpong[tuple(sorted((prev, cat)))] += 1
            prev, run_len, run_start = cat, 1, ts
    runs.append((prev, run_len, run_start, rows[-1][0]))
    mins = lambda a, b: (datetime.fromisoformat(b) - datetime.fromisoformat(a)).total_seconds() / 60
    lens = [r[1] for r in runs]
    run_bins = Counter("1" if n == 1 else "2-4" if n < 5 else "5-19" if n < 20 else "20+" for n in lens)

    # hours
    hours = defaultdict(list)
    for ts, cat, k in rows:
        hours[ts[:13]].append((cat, k, ts[:10]))
    patt = defaultdict(lambda: [0] * len(months))
    at_once = defaultdict(list)
    for hkey, items in hours.items():
        cats = [c for c, _, _ in items]
        n_proj = len(set(cats))
        at_once[hkey[:7]].append(n_proj)
        loops = sum(1 for _, k, d in items if rep[(d, k)] >= 3)
        pp = sum(1 for a, b, c in zip(cats, cats[1:], cats[2:]) if a == c and a != b)
        if loops >= len(items) / 2:
            p = "loop"
        elif pp >= 3:
            p = "ping-pong"
        elif n_proj >= 4:
            p = "juggling"
        elif n_proj == 1 and len(items) >= 10:
            p = "deep"
        else:
            p = "focused"
        patt[p][mi[hkey[:7]]] += 1
    med = lambda v: sorted(v)[len(v) // 2] if v else 0
    p90 = lambda v: sorted(v)[int(len(v) * 0.9)] if v else 0
    out = dict(
        built=time.strftime("%Y-%m-%d %H:%M"), months=months, prompts=len(rows), switches_total=sum(switches.values()),
        switch_share=round(sum(switches.values()) / max(1, len(rows) - 1), 4),
        runs=dict(n=len(runs), median_len=med(lens), bins=run_bins,
                  longest=sorted(((c, n, s[:16], round(mins(s, e))) for c, n, s, e in runs), key=lambda r: -r[1])[:8]),
        at_once=dict(median=[med(at_once[m]) for m in months], p90=[p90(at_once[m]) for m in months],
                     max=[max(at_once[m]) if at_once[m] else 0 for m in months]),
        patterns={k: patt[k] for k in ("deep", "focused", "juggling", "ping-pong", "loop")},
        pattern_totals={k: sum(patt[k]) for k in ("deep", "focused", "juggling", "ping-pong", "loop")},
        top_switches=[[a, b, n] for (a, b), n in switches.most_common(12)],
        top_pingpong=[[a, b, n] for (a, b), n in pingpong.most_common(10)],
        hours_active=len(hours))
    (OUT / "switches.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (OUT / "switches.json").chmod(0o600)
    print(f"switches: {len(rows):,} prompts · {out['switches_total']:,} switches ({out['switch_share']:.0%} of steps) · "
          f"{len(runs):,} runs (median {out['runs']['median_len']} prompts) · {len(hours):,} active hours · {time.time() - t0:.0f}s")
    print("  hour patterns:", out["pattern_totals"])
    print("  projects at once (median by month):", dict(zip(months, out["at_once"]["median"])))
    print("  ping-pong:", out["top_pingpong"][:5])


if __name__ == "__main__":
    main()
