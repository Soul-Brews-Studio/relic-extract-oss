"""Which model on gb300 reads a day best? The same 12 days, the same prompt (days.py v2), every model; scored.

    uv run python report/compare_models.py [--models a,b,c] [--days N]

Days: the busiest day of each of the last 12 months, so every model sees busy, mixed, bilingual days.
Scores per model (no judge model, all measured):
  json      share of answers that parsed with every key
  thai      share of Thai letters in the "th" answer (letters only; English words in Thai prose are normal)
  names     share of each day's top-3 projects that the answer names (concrete, not generic)
  generic   share of answers whose focus says "several projects" / "various" (the v1 failure)
  distinct  distinct focus sentences / days (a model that writes the same line every day scores low)
  secs      median seconds per day
-> ~/.relic-extract/report/compare-models.json, and a table on stdout
"""

import argparse
import json
import re
import statistics
import time
from pathlib import Path

import common
import days as dy

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
TH = re.compile(r"[฀-๿]")
LETTER = re.compile(r"[A-Za-z฀-๿]")
GENERIC = re.compile(r"(?i)several projects|various projects|multiple projects|a number of projects|หลายโปรเจกต์|หลายโครงการ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default="typhoon2.1-gemma3-12b,typhoon-judtem-v5,qwen3:14b,qwen3:30b-a3b")
    ap.add_argument("--days", type=int, default=12)
    ap.add_argument("--host", default="gb300")
    a = ap.parse_args()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    picks = [r[0] for r in con.execute("""select d from (select substr(ts_th, 1, 10) d, substr(ts_th, 1, 7) m, count(distinct msgkey) n,
                 row_number() over (partition by substr(ts_th, 1, 7) order by count(distinct msgkey) desc) rk
                 from x where role = 'user' and tier = 'main' and ts_th <> '' group by 1, 2) where rk = 1 order by d desc""").fetchall()][: a.days]
    inputs = {}
    for d in picks:
        shape, lines, alines = dy.day_input(con, d)
        nums = json.dumps({k: v for k, v in shape.items()})
        inputs[d] = (shape, dy.PROMPT.format(span="one day", day=d, nums=nums, lines=lines, agents=alines))
    tun = dy.tunnel(a.host, a.models.split(",")[0])
    res = {}
    try:
        for model in a.models.split(","):
            rows = []
            for d, (shape, prompt) in inputs.items():
                t1 = time.time()
                try:
                    ans = dy.ask(model, prompt)
                except Exception as ex:
                    ans = {"json_ok": False, "error": f"{ex.__class__.__name__}: {str(ex)[:120]}"}
                ans["secs"] = round(time.time() - t1, 1)
                top3 = [p for p, _ in shape["projects_top"][:3]]
                text = json.dumps({k: ans.get(k) for k in dy.KEYS}, ensure_ascii=False).lower()
                ans["names_hit"] = sum(1 for p in top3 if p and (p.lower() in text or p.lower().replace("-oracle", "") in text)) / max(1, len(top3))
                th = str(ans.get("th") or "")
                letters = LETTER.findall(th)
                ans["thai_share"] = len([c for c in letters if TH.match(c)]) / len(letters) if letters else 0
                ans["full_json"] = bool(ans.get("json_ok")) and all(ans.get(k) for k in dy.KEYS)
                rows.append(dict(day=d, **ans))
                print(f"  {model:<24} {d} {ans['secs']:>5}s json {int(ans['full_json'])} names {ans['names_hit']:.2f} thai {ans['thai_share']:.2f} · {str(ans.get('focus'))[:70]}", flush=True)
            n = len(rows)
            res[model] = dict(rows=rows, score=dict(
                json=round(sum(r["full_json"] for r in rows) / n, 3),
                thai=round(statistics.mean(r["thai_share"] for r in rows), 3),
                names=round(statistics.mean(r["names_hit"] for r in rows), 3),
                generic=round(sum(1 for r in rows if GENERIC.search(str(r.get("focus") or "") + str(r.get("en") or ""))) / n, 3),
                distinct=round(len({str(r.get("focus")) for r in rows}) / n, 3),
                secs=statistics.median(r["secs"] for r in rows)))
    finally:
        if tun:
            tun.terminate()
    (OUT / "compare-models.json").write_text(json.dumps(dict(built=time.strftime("%Y-%m-%d %H:%M"), days=picks, models=res), ensure_ascii=False, indent=1))
    (OUT / "compare-models.json").chmod(0o600)
    print(f"\n{'model':<24} {'json':>5} {'thai':>5} {'names':>6} {'generic':>8} {'distinct':>9} {'secs':>5}")
    for m, r in res.items():
        s = r["score"]
        print(f"{m:<24} {s['json']:>5} {s['thai']:>5} {s['names']:>6} {s['generic']:>8} {s['distinct']:>9} {s['secs']:>5}")


if __name__ == "__main__":
    main()
