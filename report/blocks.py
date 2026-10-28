"""Every 3 hours: what happened in each active 3-hour block (Bangkok time), read by the same model and prompt as the days.

    uv run python report/blocks.py [--model qwen3:14b] [--min 5]

Blocks 00-03, 03-06, ... 21-24 of every day with at least --min main-session prompts. Same input shape as days.py
(loops out, projects, switches, agent prompts) and the same v3 prompt with span "one 3-hour block". Then the
comparison: per day, does the day's focus project lead its blocks, how many distinct focus projects its blocks have,
and the blocks' types against the day's type. Cache: blocks-cache.json (model + block). -> blocks.json
"""

import argparse
import hashlib
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

import common
import days as dy

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"


def main():
    dy.on_stop()
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--host", default="gb300")
    ap.add_argument("--min", type=int, default=5)
    ap.add_argument("--limit", type=int, default=0, help="only the first N blocks (a trial)")
    ap.add_argument("--hours", type=int, default=3, choices=[1, 2, 3, 4, 6], help="block length")
    a = ap.parse_args()
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    units = con.execute("""select substr(ts_th, 1, 10) d, (cast(substr(ts_th, 12, 2) as int) // {H}) * {H} AS h0, count(distinct msgkey) n
                           from x where role = 'user' and chunk = 0 and tier = 'main' and ts_th <> '' group by 1, 2
                           having count(distinct msgkey) >= ? order by 1, 2""".replace("{H}", str(a.hours)), [a.min]).fetchall()
    if a.limit:
        units = units[: a.limit]
    print(f"blocks: {len(units)} active {a.hours}-hour blocks (>= {a.min} prompts) · {time.time() - t0:.0f}s", flush=True)
    tag = "" if a.hours == 3 else f"-{a.hours}h"   # the 3-hour run keeps its original file names and cache keys
    cache_f = OUT / f"blocks-cache{tag}.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    tun = dy.tunnel(a.host, a.model)
    out = []
    try:
        for i, (d, h0, n) in enumerate(units):
            h0 = int(h0)
            shape, lines, alines = dy.day_input(con, d, h0, h0 + a.hours)
            if not shape["prompts"]:
                continue
            nums = json.dumps({k: v for k, v in shape.items()})
            span = "one hour" if a.hours == 1 else f"one {a.hours}-hour block"
            prompt = dy.PROMPT.format(span=span, day=f"{d} {h0:02d}:00-{h0 + a.hours:02d}:00", nums=nums, lines=lines, agents=alines)
            h = hashlib.md5((a.model + prompt).encode()).hexdigest()
            ck = f"{a.model}|{d}|{h0:02d}" + tag
            if cache.get(ck, {}).get("h") != h:
                t1 = time.time()
                cache[ck] = dict(h=h, model=a.model, **dy.ask(a.model, prompt))
                cache[ck]["secs"] = round(time.time() - t1, 1)
                dy.keep(f"{d} {h0:02d}{tag}", a.model, prompt, {k: cache[ck].get(k) for k in dy.KEYS + ("json_ok",)}, cache[ck]["secs"])
                print(f"  {d} {h0:02d}-{h0 + a.hours:02d} · {shape['prompts']} prompts · {shape['projects']} projects · {cache[ck]['secs']}s · "
                      f"{cache[ck].get('type')} · {str(cache[ck].get('focus'))[:80]}", flush=True)
                if i % 20 == 0:
                    cache_f.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
            out.append(dict(day=d, h0=h0, shape=shape, reading={k: cache[ck].get(k) for k in dy.KEYS}))
    finally:
        cache_f.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        cache_f.chmod(0o600)
        if tun:
            tun.terminate()

    # compare with the day readings
    days = {x["day"]: x for x in json.loads((OUT / "days.json").read_text())["days"]}
    by_day = defaultdict(list)
    for b in out:
        by_day[b["day"]].append(b)
    cmp = []
    for d, bs in by_day.items():
        day = days.get(d) or {}
        ds = day.get("summary") or {}
        top = (day.get("shape") or {}).get("top_project")
        btops = [t for t in (b["shape"]["top_project"] for b in bs) if t]  # a block with no project counts for neither
        types = Counter(str(b["reading"].get("type")) for b in bs)
        cmp.append(dict(day=d, blocks=len(bs), day_type=ds.get("type"), block_types=types.most_common(),
                        day_top=top, led=sum(1 for t in btops if t == top), named=len(btops), focus_projects=len(set(btops)),
                        day_focus=ds.get("focus")))
    # the rule windows.js leadStats uses too: days with 3+ blocks, each day's share averaged
    multi = [c for c in cmp if c["blocks"] >= 3 and c["day_top"] and c["named"]]
    summary = dict(
        blocks=len(out), days=len(cmp),
        types=Counter(str(b["reading"].get("type")) for b in out).most_common(),
        day_types=Counter(str((days.get(d) or {}).get("summary", {}) and (days[d]["summary"] or {}).get("type")) for d in by_day).most_common(),
        day_top_leads=round(sum(c["led"] / c["named"] for c in multi) / max(1, len(multi)), 3),
        mean_focus_projects=round(sum(c["focus_projects"] for c in multi) / max(1, len(multi)), 2),
        by_hour=sorted(Counter(b["h0"] for b in out).items()))
    dst = OUT / f"blocks{tag}.json"
    dst.write_text(json.dumps(dict(built=time.strftime("%Y-%m-%d %H:%M"), model=a.model, hours=a.hours, summary=summary,
                                                    blocks=out, compare=cmp), ensure_ascii=False, indent=1))
    dst.chmod(0o600)
    print(f"blocks: {len(out)} {a.hours}-hour blocks over {len(cmp)} days · {time.time() - t0:.0f}s -> {dst}")
    print(f"  block types {summary['types'][:6]}")
    print(f"  on days with 3+ blocks: the day's top project leads {summary['day_top_leads']:.0%} of its blocks; "
          f"{summary['mean_focus_projects']} different lead projects a day")


if __name__ == "__main__":
    main()
