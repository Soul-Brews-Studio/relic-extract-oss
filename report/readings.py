"""Read every window of time with a model: days, 3-hour or 1-hour blocks, or 15-minute windows; on gb300 or z.ai.

    uv run python report/readings.py --unit 15m --model zai:glm-5.3 --deep --workers 8
    uv run python report/readings.py --unit 1h  --model gb300:qwen3:14b

--model  gb300:<ollama model> (ssh tunnel to gb300's ollama, one request at a time) or zai:<model> (z.ai's
         Anthropic-compatible API, key from `pass show glm/zai-2`, never printed; --workers requests at a time)
         or fm:system (Apple's on-device model, macOS 27 `fm respond --schema`; nothing leaves the tailnet).
--fm-hosts  fm lane: one worker per Mac, `local` or an ssh host (default local). fm answers one request at a time
         per machine (4 at once on m5 took as long as 4 in a row), so more Macs is the only way to go faster.
--unit   day | 3h | 1h | 15m; a window is read when it holds at least --min main-session prompts
--deep   two more keys: friction (what went wrong or pushed back) and lesson (one lesson learned, or "none")
Same input as days.py (loops out, projects, switches, agent prompts) and the same v3 prompt. Cache per lane:
readings-cache-<lane>.json; every answer also goes to readings.jsonl. -> readings-<unit>-<lane>.json
"""

import argparse
import hashlib
import json
import re
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import common
import days as dy

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
UNITS = {"day": 1440, "3h": 180, "1h": 60, "15m": 15}
DEEP = '''
Also answer two more keys:
  "friction": "what went wrong, failed, was corrected or pushed back in this window (one sentence), or \\"none\\"",
  "lesson": "one lesson the person learned or should keep from this window (one sentence), or \\"none\\""'''
KEYS_DEEP = dy.KEYS + ("friction", "lesson")


def snapshot(con):
    """Every user prompt (chunk 0, all tiers) with its project, copied into memory once. window_input reads only this:
    15,000 queries over the parquet parts took 10-15 minutes per start (issue #38); relic-chain also rewrites those
    parts every few minutes, and a snapshot is not caught by that. Same rows, same order, so the same prompts.
    (The 0%-CPU stall seen 2026-10-26 04:18-04:28 was not DuckDB: neo SIGSTOPped the job to cool m5.)"""
    con.execute("""create or replace temp table xw as select x.ts_th, x.tier, x.text, x.key, coalesce(ses.cat, x.cat) AS pcat
                   from x left join ses using (sid) where x.role = 'user' and x.chunk = 0""")


def window_input(con, d, m0, m1):
    """day_input for minutes m0..m1 of day d (Bangkok); needs snapshot(con) first."""
    mins = "(cast(substr(x.ts_th, 12, 2) as int) * 60 + cast(substr(x.ts_th, 15, 2) as int))"
    cond = f"substr(x.ts_th, 1, 10) = ? and {mins} >= {m0} and {mins} < {m1}"
    rows = con.execute(f"""select substr(x.ts_th, 12, 5) AS hm, x.pcat AS cat, x.text, x.key
                           from xw x where x.tier = 'main'
                           and {cond} order by x.ts_th, x.key, cat, x.text""", [d]).fetchall()   # a full order: ties (same second, the merged homes) came back in any order, so the prompt and its cache hash changed run to run
    rows = [r for r in rows if len(r[2].strip()) >= 8]
    reps = Counter(r[3] for r in rows)
    seen, uniq = set(), []
    for r in rows:
        if reps[r[3]] < 3 and r[3] not in seen:
            seen.add(r[3])
            uniq.append(r)
    cats = Counter(r[1] for r in rows)
    agents = con.execute(f"""select x.pcat, min(x.text) from xw x
                             where x.tier <> 'main' and {cond} group by 1, x.key
                             order by 1, x.key limit 12""", [d]).fetchall()
    shape = dict(prompts=len(rows), distinct=len(uniq), loop_prompts=sum(n for n in reps.values() if n >= 3),
                 projects=len(cats), top_project=cats.most_common(1)[0][0] if cats else None,
                 top_share=round(cats.most_common(1)[0][1] / len(rows), 2) if rows else 0,
                 project_switches=sum(1 for a, b in zip(rows, rows[1:]) if a[1] != b[1]), agent_prompts=len(agents),
                 projects_top=cats.most_common(6))
    step = max(1, len(uniq) // 40)
    lines = "\n".join(f"{hm} [{cat}] {re.sub(r'\s+', ' ', tx)[:280]}" for hm, cat, tx, _ in uniq[::step][:40])
    alines = "\n".join(f"[{cat}] {re.sub(r'\s+', ' ', tx)[:200]}" for cat, tx in agents) or "(none)"
    return shape, lines, alines


FM_SCHEMA = {False: Path(__file__).with_name("fm-reading.schema.json"), True: Path(__file__).with_name("fm-reading-deep.schema.json")}
FM_REMOTE = "~/.relic-extract/fm-reading{}.schema.json"   # copied to each ssh host at start


def fm_cmd(host, deep):
    """fm respond with the reading schema (guided generation, so the answer is always valid JSON), prompt on stdin."""
    if host == "local":
        return ["fm", "respond", "--no-stream", "--schema", str(FM_SCHEMA[deep])]
    return ["ssh", "-o", "BatchMode=yes", host, f"NO_COLOR=1 fm respond --no-stream --schema {FM_REMOTE.format('-deep' if deep else '')}"]


def fm_setup(hosts):
    """Check every host has fm and a model, copy the schemas to the ssh hosts. Returns the hosts that work."""
    ok = []
    for h in hosts:
        pre = [] if h == "local" else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", h]
        r = subprocess.run(pre + ["NO_COLOR=1 fm available"] if pre else ["fm", "available"], capture_output=True, text=True,
                           timeout=60, env={**__import__("os").environ, "NO_COLOR": "1"})
        txt = r.stdout + r.stderr   # macOS 27.0: "System model available"; 27.2: "✓ system" / "✗ system  (modelNotReady)"
        if not ("System model available" in txt or re.search(r"✓\s*system", txt)):   # 27.2 exits 1 when pcc is off
            print(f"fm: {h} has no fm model ({(r.stdout + r.stderr).strip()[-120:]}), skipped", flush=True)
            continue
        if pre:
            for deep in (False, True):
                subprocess.run(pre + [f"mkdir -p ~/.relic-extract && cat > {FM_REMOTE.format('-deep' if deep else '')}"],
                               input=FM_SCHEMA[deep].read_text(), text=True, check=True, timeout=60)
        ok.append(h)
    if not ok:
        raise SystemExit("fm: no host with a model")
    return ok


_FM_LINE = re.compile(r"(?m)^\d\d:\d\d \[")   # a typed-prompt line in the window body (HH:MM [cat] ...)


def _fm_shrink(prompt):
    """Drop every other typed-prompt line (keep the first), so a window too big for fm's 8,192-token context fits on
    retry. Returns None when there is nothing left to cut. The counts in `Numbers:` still describe the whole window."""
    lines = prompt.split("\n")
    body = [i for i, ln in enumerate(lines) if _FM_LINE.match(ln)]
    if len(body) <= 2:
        return None
    drop = set(body[1::2])
    return "\n".join(ln for i, ln in enumerate(lines) if i not in drop)


def ask_fm(prompt, keys, host, deep):
    for attempt in range(5):   # fm's context is 8,192 tokens; a big window overflows, so halve the lines and retry
        r = subprocess.run(fm_cmd(host, deep), input=prompt, capture_output=True, text=True, timeout=900,
                           env={**__import__("os").environ, "NO_COLOR": "1"})
        err = (r.stderr or r.stdout)
        if r.returncode != 0 and "context size" in err.lower():
            sp = _fm_shrink(prompt)
            if sp and len(sp) < len(prompt):
                prompt = sp
                continue
        if r.returncode != 0:
            raise RuntimeError(f"fm on {host}: rc {r.returncode}: {err.strip()[-160:]}")
        got = _parse(r.stdout, keys)
        if got is None:
            return {"en": r.stdout.strip()[:600], "th": "", "json_ok": False, "host": host, "trimmed": attempt}
        if isinstance(got.get("work"), list):
            got["work"] = got["work"][:4]
        return got | {"json_ok": True, "host": host, "trimmed": attempt}
    raise RuntimeError(f"fm on {host}: still over context after trimming to {len(_FM_LINE.findall(prompt))} lines")


_zkeys, _zlock, _zi = [], threading.Lock(), [0]
_zname = {}   # key -> its pass entry name, so a log line can say which key without printing it
ZAI_PASS = ("glm/zai-2", "glm/zai-3", "glm/nat-wrw")   # several keys share the load; a 429 on one backs off without stopping the rest


def _zai_key():
    with _zlock:
        if not _zkeys:
            for e in ZAI_PASS:
                k = subprocess.run(["pass", "show", e], capture_output=True, text=True).stdout.splitlines()
                if k and k[0].strip():
                    _zkeys.append(k[0].strip())
                    _zname[k[0].strip()] = e
        if not _zkeys:
            raise RuntimeError("z.ai: no key left (every key is out of balance)")
        _zi[0] += 1
        return _zkeys[_zi[0] % len(_zkeys)]


def _zai_drop(key):
    """z.ai 1113 (insufficient balance / no resource package) never heals within a run: stop using that key."""
    with _zlock:
        if key in _zkeys and len(_zkeys) > 1:
            _zkeys.remove(key)
            print(f"  z.ai: {_zname.get(key, '?')} is out of balance (1113), {len(_zkeys)} left", flush=True)


def _zai_call(model, prompt, think):
    # glm-5.3 always thinks (z.ai 1210 "cannot be disabled"): the second try just gives the thinking more room
    b = {"model": model, "max_tokens": 16000 if think else 32000, "messages": [{"role": "user", "content": prompt}]}
    data = json.dumps(b).encode()
    for attempt in range(9):
        key = _zai_key()
        req = urllib.request.Request("https://api.z.ai/api/anthropic/v1/messages", data,
                                     {"content-type": "application/json", "x-api-key": key, "authorization": "Bearer " + key,
                                      "anthropic-version": "2023-06-01"})
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                j = json.loads(r.read())
            break
        except urllib.error.HTTPError as e:
            body = e.read()
            if b"1113" in body or e.code == 401:
                _zai_drop(key)
            if attempt == 8:
                raise
            wait = e.headers.get("retry-after") if e.code == 429 else None
            secs = float(wait) if wait and wait.replace(".", "").isdigit() else min(120, 10 * 2 ** attempt if e.code == 429 else 5 * (attempt + 1))
            code = re.search(rb'"code":\s*"?(\d+)', body)
            # every retry is printed (key by its pass name, never the key): a rate limit used to be invisible in the pane
            print(f"  z.ai: HTTP {e.code} code {code.group(1).decode() if code else '-'} on {_zname.get(key, '?')}, "
                  f"try {attempt + 1}/9, retry in {secs:.0f}s", flush=True)
            time.sleep(secs)
        except Exception as e:
            if attempt == 8:
                raise
            print(f"  z.ai: {e.__class__.__name__} on {_zname.get(key, '?')}, try {attempt + 1}/9, retry in {5 * (attempt + 1)}s", flush=True)
            time.sleep(5 * (attempt + 1))
    return "".join(c.get("text", "") for c in j.get("content", []) if c.get("type") == "text"), j.get("stop_reason")


def _parse(text, keys):
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return None
    try:
        a = json.loads(m.group(0))
    except Exception:
        return None
    return {k: a.get(k) for k in keys} if a.get("focus") else None


def ask_zai(model, prompt, keys):
    """GLM with thinking (better reading); if no JSON comes back (the thinking ran out the budget), once without."""
    text, stop = _zai_call(model, prompt, True)
    got = _parse(text, keys)
    if got is None:
        text, stop = _zai_call(model, prompt, False)
        got = _parse(text, keys)
        if got is None:
            return {"en": (text or "").strip()[:600], "th": "", "json_ok": False, "stop": stop}
        return got | {"json_ok": True, "think": False}
    return got | {"json_ok": True, "think": True}


def _stop(*_):
    # the first signal stops the run; ignore the rest (the pane closing sends SIGHUP right after a TERM), or a second
    # one lands inside the cache save in `finally` and the save is lost
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    signal.signal(signal.SIGHUP, signal.SIG_IGN)
    raise KeyboardInterrupt


def main():
    # a kill (SIGTERM) runs the finally below like Ctrl-C does, so the cache is saved and the next run resumes
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)  # the pane closing (herdr pane close) sends SIGHUP
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", default="15m", choices=list(UNITS))
    ap.add_argument("--model", default="zai:glm-5.3")
    ap.add_argument("--deep", action="store_true")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--fm-hosts", default="local", help="fm lane: comma-separated Macs, `local` or ssh hosts (one worker each)")
    ap.add_argument("--gb-workers", type=int, default=1, help="requests at a time on gb300 (default 1)")
    ap.add_argument("--min", type=int, default=3)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--plan", action="store_true", help="count the windows already read, to read again (prompt changed) and new; read none")
    ap.add_argument("--sample", type=int, default=0, help="read N windows spread evenly over the year (a model trial)")
    ap.add_argument("--windows", metavar="CACHE", help="read only the windows in this readings-cache file (the same trial set for every model)")
    ap.add_argument("--restamp", metavar="SINCE", help="give cached readings logged since SINCE (2026-10-24T14:40) the current prompt hash, then stop")
    a = ap.parse_args()
    t0 = time.time()
    lane, model = a.model.split(":", 1)
    span = UNITS[a.unit]
    keys = KEYS_DEEP if a.deep else dy.KEYS
    tag = f"{a.unit}-{lane}-{model.replace(':', '_')}{'-deep' if a.deep else ''}"
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    snapshot(con)
    units = con.execute(f"""select substr(ts_th, 1, 10) d, ((cast(substr(ts_th, 12, 2) as int) * 60 + cast(substr(ts_th, 15, 2) as int)) // {span}) * {span} AS m0,
                            count(distinct msgkey) n from x where role = 'user' and chunk = 0 and tier = 'main' and ts_th <> ''
                            group by 1, 2 having count(distinct msgkey) >= ? order by 1, 2""", [a.min]).fetchall()
    if a.sample:  # every k-th window over the whole span, the same ones each run (a model trial against the lanes)
        units = units[:: max(1, len(units) // a.sample)][: a.sample]
    if a.limit:
        units = units[: a.limit]
    print(f"readings: {len(units):,} windows of {a.unit} (>= {a.min} prompts) · {a.model} · deep={a.deep} · {time.time() - t0:.0f}s", flush=True)
    cache_f = OUT / f"readings-cache-{tag}.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    jobs = []
    for d, m0, n in units:
        m0 = int(m0)
        shape, lines, alines = window_input(con, d, m0, m0 + span)
        if not shape["prompts"]:
            continue
        label = f"{d} {m0 // 60:02d}:{m0 % 60:02d}-{(m0 + span) // 60:02d}:{(m0 + span) % 60:02d}" if a.unit != "day" else d
        span_t = {"day": "one day", "3h": "one 3-hour block", "1h": "one hour", "15m": "15 minutes"}[a.unit]
        prompt = dy.PROMPT.format(span=span_t, day=label, nums=json.dumps(shape), lines=lines, agents=alines)
        if a.unit == "15m":  # the day prompt's "the day" made 15-minute readings start "The day was focused on ..."
            for w0, w1 in (("Describe the day", "Describe these 15 minutes"), ("the day's prompts", "these 15 minutes' prompts"),
                           ("if the day stayed", "if these 15 minutes stayed"), ("the day was mostly about", "these 15 minutes were mostly about"),
                           ("worked today", "worked in these 15 minutes")):
                prompt = prompt.replace(w0, w1)
        if a.deep:
            prompt = prompt.replace("\nNumbers: ", DEEP + "\n\nNumbers: ", 1)
        jobs.append((label, shape, prompt))
    if a.restamp:
        # one-off after the tie-order fix: every prompt's hash moved, though a window read on today's data has not
        # changed. A cached reading logged at or after --restamp (and not from today, which still grows) takes the
        # new hash; the rest stay as they are and are read again.
        last, mt = {}, a.model + (" deep" if a.deep else "")
        for line in open(dy.LOG):
            r = json.loads(line)
            if r.get("model") == mt:
                last[r["unit"]] = max(last.get(r["unit"], ""), r["at"])
        today, kept, stale, ok = time.strftime("%Y-%m-%d"), 0, 0, 0
        for label, _, prompt in jobs:
            h, got = hashlib.md5((a.model + prompt).encode()).hexdigest(), cache.get(label)
            if not got or got.get("h") == h:
                ok += bool(got)
            elif last.get(f"{label} {a.unit}", "") >= a.restamp and not label.startswith(today) and (not a.deep or "friction" in got):
                got["h"], kept = h, kept + 1
            else:
                stale += 1
        cache_f.write_text(json.dumps(cache, ensure_ascii=False))
        cache_f.chmod(0o600)
        print(f"readings: restamp {tag}: {ok:,} already current · {kept:,} restamped (read since {a.restamp}) · {stale:,} left to read again")
        return
    if a.windows:  # --sample drifts as live windows arrive; a later trial reads the first trial's windows instead
        want = set(json.loads(Path(a.windows).expanduser().read_text()))
        jobs = [j for j in jobs if j[0] in want]
        print(f"readings: {len(jobs):,} windows from {Path(a.windows).name}", flush=True)
    if a.plan:  # what a run would do: the same test as one() below, without asking any model
        cur = stale = new = 0
        for label, _, prompt in jobs:
            got = cache.get(label)
            if not got:
                new += 1
            elif got.get("h") == hashlib.md5((a.model + prompt).encode()).hexdigest() and (not a.deep or "friction" in got):
                cur += 1
            else:
                stale += 1
        print(f"readings: plan {tag}: {cur:,} current · {stale:,} to read again (prompt changed) · {new:,} new · "
              f"{stale + new:,} to read · {time.time() - t0:.0f}s")
        return
    lock = threading.Lock()
    done = 0

    def one(job):
        label, shape, prompt = job
        h = hashlib.md5((a.model + prompt).encode()).hexdigest()
        got = cache.get(label, {})
        # a deep reading cached without friction/lesson (days.ask dropped them before 2026-10-24 14:40) is read again
        if got.get("h") == h and (not a.deep or "friction" in got):
            return label, shape, got, False
        t1 = time.time()
        if lane == "zai":
            ans = ask_zai(model, prompt, keys)
        elif lane == "fm":
            host = fm_free.get()   # one request at a time per Mac
            try:
                ans = ask_fm(prompt, keys, host, a.deep)
            finally:
                fm_free.put(host)
        else:
            ans = dy.ask(model, prompt, keys=keys)
        ans["secs"] = round(time.time() - t1, 1)
        dy.keep(f"{label} {a.unit}", a.model + (" deep" if a.deep else ""), prompt, ans, ans["secs"])
        return label, shape, dict(h=h, **ans), True

    tun = dy.tunnel("gb300", model) if lane == "gb300" else None
    fm_free = __import__("queue").Queue()
    if lane == "fm":
        for h in fm_setup([h.strip() for h in a.fm_hosts.split(",") if h.strip()]):
            fm_free.put(h)
        print(f"fm: {fm_free.qsize()} worker(s): {', '.join(list(fm_free.queue))}", flush=True)
    out = []
    # no `with`: leaving a with-block waits for every queued window (hours), so a kill never reached the save
    # gb300: one request at a time unless --gb-workers asks for more (ollama runs them side by side on the shared GPU)
    ex = ThreadPoolExecutor(max_workers=a.workers if lane == "zai" else fm_free.qsize() if lane == "fm" else a.gb_workers)
    try:
        futs = [ex.submit(one, j) for j in jobs]
        for f in as_completed(futs):
            try:
                label, shape, ans, new = f.result()
            except Exception as e:
                print(f"  failed: {e.__class__.__name__}: {str(e)[:120]}", flush=True)
                continue
            with lock:
                cache[label] = ans
                out.append(dict(window=label, shape=shape, reading={k: ans.get(k) for k in keys}))
                done += 1
                if new:
                    print(f"  {label} · {shape['prompts']} prompts · {shape['projects']} projects · {ans.get('secs')}s · "
                          f"{ans.get('type')} · {str(ans.get('focus'))[:70]}"
                          + (f" · friction: {str(ans.get('friction'))[:50]}" if a.deep else ""), flush=True)
                if done % 25 == 0:
                    cache_f.write_text(json.dumps(cache, ensure_ascii=False))
    finally:
        ex.shutdown(wait=False, cancel_futures=True)
        cache_f.write_text(json.dumps(cache, ensure_ascii=False))
        cache_f.chmod(0o600)
        dy._TUN.clear()  # a request still in flight must not reopen the tunnel after we stop
        if tun:
            tun.terminate()
    out.sort(key=lambda r: r["window"])
    dst = OUT / f"{'trial' if a.sample or a.windows else 'readings'}-{tag}.json"   # a --sample trial is not a lane: build.py reads readings-*.json
    dst.write_text(json.dumps(dict(built=time.strftime("%Y-%m-%d %H:%M"), unit=a.unit, model=a.model, deep=a.deep,
                                   windows=out), ensure_ascii=False))
    dst.chmod(0o600)
    print(f"readings: {len(out):,} windows · {time.time() - t0:.0f}s -> {dst}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:  # a stop (TERM, HUP, Ctrl-C): the cache was saved in main's finally; no traceback
        print("readings: stopped, cache saved; the next run resumes from it", flush=True)
        raise SystemExit(130)
