"""What is being read right now: every reading lane's windows read, windows in all, pace and finish time.

    uv run python report/status.py     # -> ~/.relic-extract/report/status.json (build.py puts it on the page)

A lane ahead of its machine's running lane has finished its pass; its windows not read are new since ("new").
A window counts as read when its lane's cache holds it (an older reading of a window whose prompts changed since
counts too, so "read" can run ahead of the lane's own count by the windows it will read again). Pace = answers
logged in readings.jsonl in the hour before the lane's last answer (per minute since its first, for a lane that started inside it); finish = windows left / pace. The gb300 lanes run one at a time, in
the queue's order, only between 23:00 and 08:00 (Bangkok); the z.ai lanes run one after another, day and night.
Counts and model names only: no reading text, no session id.
"""

import datetime as dt
import json
import os
from pathlib import Path

import common

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
BKK = dt.timezone(dt.timedelta(hours=7))

# (lane id, machine, model, unit, deep, min prompts, cache file, cache key of a window, log model, log unit)
LANES = [
    ("gb300-15m", "gb300", "qwen3:14b", "15m", True, 3, "readings-cache-15m-gb300-qwen3_14b-deep.json",
     lambda d, h, m: f"{d} {h:02d}:{m:02d}-{(h * 60 + m + 15) // 60:02d}:{(h * 60 + m + 15) % 60:02d}", "gb300:qwen3:14b deep", "15m"),
    ("gb300-day", "gb300", "qwen3:14b", "day", False, 1, "days-cache.json", lambda d, h, m: f"qwen3:14b|{d}", None, None),
    ("gb300-3h", "gb300", "qwen3:14b", "3h", False, 5, "blocks-cache.json", lambda d, h, m: f"qwen3:14b|{d}|{h:02d}", None, None),
    ("gb300-1h", "gb300", "qwen3:14b", "1h", False, 5, "blocks-cache-1h.json", lambda d, h, m: f"qwen3:14b|{d}|{h:02d}-1h", None, None),
    ("zai-day", "z.ai", "glm-5.3", "day", False, 5, "readings-cache-day-zai-glm-5.3.json", lambda d, h, m: d, "zai:glm-5.3", "day"),
    ("zai-3h", "z.ai", "glm-5.3", "3h", False, 5, "readings-cache-3h-zai-glm-5.3.json",
     lambda d, h, m: f"{d} {h:02d}:00-{h + 3:02d}:00", "zai:glm-5.3", "3h"),
    ("zai-1h", "z.ai", "glm-5.3", "1h", False, 5, "readings-cache-1h-zai-glm-5.3.json",
     lambda d, h, m: f"{d} {h:02d}:00-{h + 1:02d}:00", "zai:glm-5.3", "1h"),
    ("zai-15m", "z.ai", "glm-5.3", "15m", True, 3, "readings-cache-15m-zai-glm-5.3-deep.json",
     lambda d, h, m: f"{d} {h:02d}:{m:02d}-{(h * 60 + m + 15) // 60:02d}:{(h * 60 + m + 15) % 60:02d}", "zai:glm-5.3 deep", "15m"),
]
SPAN = {"day": 1440, "3h": 180, "1h": 60, "15m": 15}
NIGHT = None  # the gb300 queue's hours, (23, 8) when it runs nights only; None = day and night (since 2026-10-25 08:38)


def windows(con, unit, mn):
    span = SPAN[unit]
    return con.execute(f"""select substr(ts_th, 1, 10) d, ((cast(substr(ts_th, 12, 2) as int) * 60 + cast(substr(ts_th, 15, 2) as int)) // {span}) * {span} m0
                           from x where role = 'user' and chunk = 0 and tier = 'main' and ts_th <> ''
                           group by 1, 2 having count(distinct msgkey) >= ?
                           and count(*) filter (where length(trim(text, ' \t\n\r')) >= 8) > 0 order by 1, 2""", [mn]).fetchall()
    # the second test: a window whose prompts are all under 8 characters ("ok", "go") gives the lanes nothing to read, so they skip it


def pace(now):
    """Answers per hour for each lane over the hour before its own last answer (so a paused lane keeps the pace it
    ran at), counted per minute since the first answer in that hour when it started inside it; and when each lane
    last answered. Reads the tail of readings.jsonl only."""
    ts = {}
    f = OUT / "readings.jsonl"
    if not f.exists():
        return {}, {}
    with open(f, "rb") as fh:
        fh.seek(max(0, f.stat().st_size - 40_000_000))
        fh.readline()
        for line in fh:
            try:
                r = json.loads(line)
                at = dt.datetime.strptime(r["at"], "%Y-%m-%dT%H:%M:%S%z")
            except (ValueError, KeyError):
                continue
            ts.setdefault((r["model"], r["unit"].split()[-1]), []).append(at)
    n, last = {}, {}
    for k, v in ts.items():
        end = max(v)
        last[k] = end
        win = [t for t in v if (end - t).total_seconds() <= 3600]
        n[k] = round(len(win) * 60 / max(10, (end - min(win)).total_seconds() / 60))
    return n, last


def night_hours(start, hours):
    """Advance `hours` of gb300 night time (23:00-08:00) from `start`; returns the wall-clock end."""
    t, left = start, hours * 3600
    while left > 0:
        if t.hour >= NIGHT[0] or t.hour < NIGHT[1]:
            end = (t + dt.timedelta(days=1 if t.hour >= NIGHT[0] else 0)).replace(hour=NIGHT[1], minute=0, second=0, microsecond=0)
            step = min(left, (end - t).total_seconds())
            t, left = t + dt.timedelta(seconds=step), left - step
        else:
            t = t.replace(hour=NIGHT[0], minute=0, second=0, microsecond=0)
    return t


def main():
    now = dt.datetime.now(BKK)
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    n_hr, last = pace(now)
    out = []
    clock = {"gb300": now, "z.ai": now}  # lanes on one machine run one after another
    for lid, mach, model, unit, deep, mn, cf, key, lmodel, lunit in LANES:
        ws = windows(con, unit, mn)
        p = OUT / cf
        cache = json.loads(p.read_text()) if p.exists() else {}
        done = sum(key(d, int(m0) // 60, int(m0) % 60) in cache for d, m0 in ws)
        total = len(ws)
        k = (lmodel, lunit)
        rate = n_hr.get(k, 0)
        seen = last.get(k)
        live = seen is not None and (now - seen).total_seconds() < 900
        if not live and lmodel is None and p.exists():  # days.py / blocks.py log nothing to readings.jsonl: their cache moving is the sign
            seen = dt.datetime.fromtimestamp(p.stat().st_mtime, BKK)
            live = (now - seen).total_seconds() < 900
        state = "done" if done >= total else "running" if live else "queued"
        if NIGHT and mach == "gb300" and state == "queued" and lid == "gb300-15m":
            state = "paused"  # the queue's first lane, outside its night hours
        left = max(0, total - done)
        row = dict(id=lid, machine=mach, model=model, unit=unit, deep=deep, min=mn, done=done, total=total,
                   per_hour=rate, state=state, last=seen.strftime("%Y-%m-%d %H:%M") if seen else None)
        if state in ("running", "paused") and rate:
            hrs = left / rate
            fin = night_hours(clock[mach], hrs) if mach == "gb300" and NIGHT else clock[mach] + dt.timedelta(hours=hrs)
            row["finish"] = fin.strftime("%Y-%m-%d %H:%M")
            clock[mach] = fin
        out.append(row)
    for mach in ("gb300", "z.ai"):  # one lane reads at a time on a machine: the one that wrote last
        live = [r for r in out if r["machine"] == mach and r["state"] == "running"]
        for r in sorted(live, key=lambda r: r["last"])[:-1]:
            r["state"] = "queued"
    for mach in ("gb300", "z.ai"):  # a lane before the machine's running one has finished its pass: windows left came in since
        rows = [r for r in out if r["machine"] == mach]
        cur = next((i for i, r in enumerate(rows) if r["state"] in ("running", "paused")), None)
        if cur is None:
            cur = len(rows)  # nothing reads on this machine: every lane has finished its pass
        for i, r in enumerate(rows):
            if cur is not None and i < cur and r["state"] == "queued":
                r["state"], r["new"] = "done", r["total"] - r["done"]
    for r in out:
        if r["state"] in ("done", "queued"):
            r["per_hour"] = 0  # a pace is shown only for a lane that reads now or between its nights
    trial = None
    tf = OUT / "trial-set-3h.json"
    if tf.exists():  # the model trial (trial.py): the same 3h windows read by every model
        trial = trial_summary()
    res = dict(at=now.strftime("%Y-%m-%d %H:%M"), lanes=out, trial=trial, night=list(NIGHT) if NIGHT else None)
    f = OUT / "status.json"
    f.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    os.chmod(f, 0o600)
    for r in out:
        print(f"  {r['id']:<10} {r['state']:<8} {r['done']:>6,} of {r['total']:>6,} · {r['per_hour']:>4}/h · finish {r.get('finish') or '-'}")
    print(f"status: {len(out)} lanes · {now:%H:%M} -> {f}")


def trial_summary():
    import statistics
    tags = [("z.ai glm-5.3", "zai-glm-5.3"), ("qwen3:14b", "gb300-qwen3_14b"), ("Nemotron 3.5 Bolt E4B", "gb300-nemotron-bolt-e4b_q8"),
            ("typhoon2.1-gemma3-12b", "gb300-typhoon2.1-gemma3-12b_latest"), ("qwen3:30b-a3b", "gb300-qwen3_30b-a3b")]
    want = set(json.loads((OUT / "trial-set-3h.json").read_text()))
    C = {}
    for name, t in tags:
        p = OUT / f"readings-cache-3h-{t}.json"
        if p.exists():
            C[name] = json.loads(p.read_text())
    both = sorted(want.intersection(*(set(c) for c in C.values())))
    if not both or "z.ai glm-5.3" not in C:
        return None
    ref = C["z.ai glm-5.3"]
    rows = []
    for name, c in C.items():
        secs = [c[w]["secs"] for w in both if c[w].get("secs") is not None]
        rows.append(dict(model=name, secs=round(statistics.median(secs), 1) if secs else None,
                         agree=None if name == "z.ai glm-5.3" else round(sum(c[w].get("type") == ref[w].get("type") for w in both) / len(both), 3)))
    return dict(windows=len(both), rows=rows, kept="qwen3:14b")


if __name__ == "__main__":
    main()
