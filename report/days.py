"""Day by day: every day's numbers, and a two-line summary of the day written by a language model on gb300.

    uv run python report/days.py [--no-llm] [--model typhoon2.1-gemma3-12b] [--host gb300]

Numbers per Bangkok day: main-session prompts, agent-to-agent prompts, sessions started by tier, tool calls and
errors, active hours, the top topics (topics.py) and repositories. Summary: up to 40 of the day's distinct
main-session prompts (spread over the day, 280 characters each) go to ollama on the GPU host through an ssh tunnel;
the model answers in English and in Thai. A day already summarised with the same prompts is not asked again
(cache: ~/.relic-extract/report/days-cache.json, keyed by model and day). -> ~/.relic-extract/report/days.json
Per day the model also gets the day's shape: projects touched, the top project's share, project switches, loop
prompts (a text seen 3+ times that day, left out of the list), and a sample of the agent-to-agent prompts; it
answers type, focus, distraction, work, agents, pattern, and two-sentence summaries in English and Thai.
"""

import argparse
import hashlib
import json
import os
import re
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from collections import Counter, defaultdict
from pathlib import Path

import common

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
PROMPT = """You read {span} ({day}) of ONE person's work with AI coding agents: the prompts that person typed (English and
Thai), prompts their agents wrote for other agents, and measured numbers. Describe the day for that person's own diary.
Be concrete: name the projects, tools, files and problems you see. Never invent; if the prompts do not show it, say less.

Read these right:
- "The user sent a new message while you were working" is the SAME person adding an instruction to a running agent.
  It is steering, never an interruption by someone else. Do not call it "user messages" or "interruptions".
- Pasted output (PANE …, rc=…, command output) is the person showing an agent a result.
- Repeated loop prompts were removed from the list; their count is in the numbers (loop_prompts).

"type": the kind of work most of the day's prompts belong to:
  build = making a new feature, app, page or tool · fix = debugging, errors, failing tests · research = reading,
  comparing, literature, exploring a codebase · ops = fleet, servers, monitoring, deploy, machines · writing = books,
  docs, posts, chapters · planning = deciding, designing, issues and roadmaps. Use "mixed" ONLY if no kind holds 40%.
"distraction": what pulled attention from the focus: other projects, loops left running, tool errors, monitoring,
  machine trouble. Say "none" if the day stayed on its focus.
"th": two sentences in Thai the way a Thai engineer writes for himself: the topic first, plain verbs, English tech words
  left in English (deploy, test, repo, PR, UI). Never use อย่างไรก็ตาม, ซึ่ง, ในขณะที่, การที่, โดยรวม, นอกจากนี้.

Answer with JSON only, these keys:
{{"type": "build|fix|research|ops|writing|planning|mixed",
  "focus": "the one thing the day was mostly about (one sentence)",
  "distraction": "(one sentence, or \"none\")",
  "work": ["up to 4 short items: what was built, fixed or decided"],
  "agents": "what the agents were set to do, from their prompts (one sentence)",
  "pattern": "how the person worked today, e.g. many short checks, one long build, loops left running (one sentence)",
  "en": "two-sentence summary in English",
  "th": "สรุปสองประโยค"}}

Numbers: {nums}
Prompts the person typed (time, project, text):
{lines}
Prompts agents wrote for other agents (project, start of text):
{agents}
"""


_TUN = {}  # port -> host, so ask() can reopen a tunnel that dropped mid-run


def _ssh_tunnel(host, port):
    """autossh when installed (it restarts ssh by itself when the link drops; -M 0 = watch with ServerAlive, no
    monitor port), else plain ssh; either way ask() reopens the port if it is still gone."""
    opts = ["-o", "BatchMode=yes", "-o", "ExitOnForwardFailure=yes", "-o", "ServerAliveInterval=30", "-o", "ServerAliveCountMax=3",
            "-N", "-L", f"{port}:127.0.0.1:11434", host]
    if shutil.which("autossh"):
        return subprocess.Popen(["autossh", "-M", "0", *opts], env={**os.environ, "AUTOSSH_GATETIME": "0"})
    return subprocess.Popen(["ssh", *opts])


def _reopen(port):
    """An ssh tunnel can drop in a long run (seen 2026-10-24 after 825 of 13,724 windows): open it again."""
    host, s = _TUN.get(port), socket.socket()
    if not host or s.connect_ex(("127.0.0.1", port)) == 0:
        s.close()
        return
    _ssh_tunnel(host, port)
    print(f"    tunnel to {host} reopened", flush=True)
    time.sleep(3)


def tunnel(host, model, port=11447):
    _TUN[port] = host
    s = socket.socket()
    if s.connect_ex(("127.0.0.1", port)) == 0:  # something listens already: use it only if it is an ollama with the model
        s.close()
        tags = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tags", timeout=5).read())
        if not any(m["name"].split(":")[0] == model.split(":")[0] for m in tags.get("models", [])):
            raise SystemExit(f"127.0.0.1:{port} is not an ollama holding {model}: pick another port")
        return None
    p = _ssh_tunnel(host, port)
    for _ in range(50):
        time.sleep(0.2)
        s = socket.socket()
        if s.connect_ex(("127.0.0.1", port)) == 0:
            s.close()
            return p
    raise SystemExit(f"no tunnel to {host}:11434")


KEYS = ("type", "focus", "distraction", "work", "agents", "pattern", "en", "th")
LOG = Path.home() / ".relic-extract" / "report" / "readings.jsonl"


def keep(unit, model, prompt, answer, secs):
    """Append every answer to readings.jsonl, never rewritten: a newer prompt replaces a reading in the cache, the
    log keeps all of them (unit = day or day+block, prompt = its md5, so versions can be compared later)."""
    with open(LOG, "a") as f:
        f.write(json.dumps(dict(at=time.strftime("%Y-%m-%dT%H:%M:%S%z"), unit=unit, model=model,
                                prompt=hashlib.md5(prompt.encode()).hexdigest()[:12], secs=secs, answer=answer), ensure_ascii=False) + "\n")
    LOG.chmod(0o600)


def ask(model, prompt, port=11447, keys=None):
    """keys: the answer keys to keep (KEYS by default; readings.py --deep adds friction and lesson)."""
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json", "think": False,
                       "options": {"temperature": 0.2, "num_ctx": 16384}}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/generate", body, {"Content-Type": "application/json"})
    for attempt in range(4):  # the host may reset a connection while it swaps models in and out of memory
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                out = json.loads(r.read())["response"]
            break
        except (ConnectionError, urllib.error.URLError, TimeoutError) as ex:
            if attempt == 3:
                raise
            print(f"    retry {attempt + 1}: {ex.__class__.__name__}", flush=True)
            time.sleep(5 * (attempt + 1))
            _reopen(port)
    try:
        j = json.loads(out)
        return {k: j.get(k) for k in (keys or KEYS)} | {"json_ok": True}
    except Exception:
        return {"en": out.strip()[:600], "th": "", "json_ok": False}


def day_input(con, d, h0=0, h1=24):
    """The prompts of day d between hours h0 and h1 (Bangkok) for the model, loops out, plus their measured shape."""
    day = f"substr(x.ts_th, 1, 10) = ? and cast(substr(x.ts_th, 12, 2) as int) >= {int(h0)} and cast(substr(x.ts_th, 12, 2) as int) < {int(h1)} and true"
    rows = con.execute(f"""select substr(x.ts_th, 12, 5) AS hm, coalesce(ses.cat, x.cat) AS cat, x.text, x.key
                           from x left join ses using (sid) where x.role = 'user' and x.chunk = 0 and x.tier = 'main'
                           and {day} order by x.ts_th, x.key, cat, x.text""", [d]).fetchall()   # a full order: ties (same second, the merged homes) came back in any order
    rows = [r for r in rows if len(r[2].strip()) >= 8]
    reps = Counter(r[3] for r in rows)
    loops = sum(n for n in reps.values() if n >= 3)
    seen, uniq = set(), []
    for r in rows:
        if reps[r[3]] < 3 and r[3] not in seen:
            seen.add(r[3])
            uniq.append(r)
    cats = Counter(r[1] for r in rows)
    switches = sum(1 for a, b in zip(rows, rows[1:]) if a[1] != b[1])
    agents = con.execute(f"""select coalesce(ses.cat, x.cat), min(x.text) from x left join ses using (sid)
                             where x.role = 'user' and x.chunk = 0 and x.tier <> 'main' and {day} group by 1, x.key
                             order by md5(x.key) limit 12""", [d]).fetchall()
    shape = dict(prompts=len(rows), distinct=len(uniq), loop_prompts=loops, projects=len(cats),
                 top_project=cats.most_common(1)[0][0] if cats else None,
                 top_share=round(cats.most_common(1)[0][1] / len(rows), 2) if rows else 0,
                 project_switches=switches, agent_prompts=len(agents), projects_top=cats.most_common(6))
    step = max(1, len(uniq) // 40)
    pick = uniq[::step][:40]
    lines = "\n".join(f"{hm} [{cat}] {re.sub(r'\s+', ' ', tx)[:280]}" for hm, cat, tx, _ in pick)
    alines = "\n".join(f"[{cat}] {re.sub(r'\s+', ' ', tx)[:200]}" for cat, tx in agents) or "(none)"
    return shape, lines, alines


def on_stop():
    """A kill (SIGTERM, or SIGHUP when the pane closes) runs the finally blocks like Ctrl-C, so the cache is saved and
    the next run resumes (the gb300 queue stops each lane at 08:00)."""
    def _stop(*_):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)  # a second signal would land inside the save
        signal.signal(signal.SIGHUP, signal.SIG_IGN)
        raise KeyboardInterrupt
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGHUP, _stop)


def main():
    on_stop()
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-llm", action="store_true")
    ap.add_argument("--model", default="qwen3:14b")
    ap.add_argument("--host", default="gb300")
    ap.add_argument("--only", default="", help="comma-separated days (for comparing models)")
    a = ap.parse_args()
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    con.execute(f"create view topic_of as select * from read_parquet('{OUT / 'topic_of.parquet'}')")
    tn = {t["id"]: t.get("name") or " · ".join(t["terms"][:3]) for t in json.loads((OUT / "topics.json").read_text())["topics"]}

    day = lambda col: f"substr({col}, 1, 10)"
    D = defaultdict(dict)
    for d, tier, n in con.execute(f"""select {day('ts_th')}, case when tier = 'main' then 'main' else 'agent' end, count(distinct msgkey)
                                      from x where role = 'user' and ts_th <> '' group by all""").fetchall():
        D[d]["prompts_" + tier] = n
    # sessions.first_ts is UTC: shift it to Bangkok before taking the day
    for d, tier, n in con.execute("""select substr(strftime(cast(substr(first_ts, 1, 19) as timestamp) + interval 7 hour, '%Y-%m-%d'), 1, 10),
                                     tier, count(*) from s where first_ts <> '' group by all""").fetchall():
        D[d].setdefault("sessions", {})[tier] = n
    for d, c, e in con.execute(f"""select {day('ts_th')}, count(*) filter (where kind = 'call'), count(*) filter (where kind = 'result' and err)
                                   from r where s = 'tools' and ts_th <> '' group by 1""").fetchall():
        D[d]["tool_calls"], D[d]["tool_errors"] = c, e
    for d, hrs in con.execute(f"""select {day('ts_th')}, list(distinct cast(substr(ts_th, 12, 2) as int)) from x
                                  where role = 'user' and tier = 'main' and ts_th <> '' group by 1""").fetchall():
        D[d]["hours"] = sorted(hrs)
    tops = defaultdict(Counter)
    for d, topic, n in con.execute(f"""select {day('x.ts_th')}, t.topic, count(distinct x.msgkey) from x join topic_of t using (key)
                                      where x.role = 'user' and x.chunk = 0 and x.tier = 'main' and x.ts_th <> '' group by all""").fetchall():
        tops[d][tn[topic]] += n
    reps = defaultdict(Counter)
    for d, cat, n in con.execute(f"""select {day('x.ts_th')}, ses.cat, count(distinct x.msgkey) from x join ses using (sid)
                                     where x.role = 'user' and x.tier = 'main' and x.ts_th <> '' group by all""").fetchall():
        reps[d][cat] += n
    for d in D:
        D[d]["topics"] = tops[d].most_common(4)
        D[d]["repos"] = reps[d].most_common(4)
    days = sorted(k for k in D if k[:2] == "20")
    print(f"days: {len(days)} days with activity, {days[0]} .. {days[-1]} · {time.time() - t0:.0f}s")

    # summaries on the GPU host
    cache_f = OUT / "days-cache.json"
    cache = json.loads(cache_f.read_text()) if cache_f.exists() else {}
    if not a.no_llm:
        tun = tunnel(a.host, a.model)
        try:
            n_ask = 0
            for d in (a.only.split(",") if a.only else days):
                shape, lines, alines = day_input(con, d)
                if not shape["prompts"]:
                    continue
                D[d]["shape"] = shape
                nums = json.dumps({**{k: D[d].get(k) for k in ("prompts_agent", "tool_calls", "tool_errors")},
                                   **{k: v for k, v in shape.items() if k != "projects_top"}, "projects_top": shape["projects_top"]})
                prompt = PROMPT.format(span="one day", day=d, nums=nums, lines=lines, agents=alines)
                h = hashlib.md5((a.model + prompt).encode()).hexdigest()
                ck = f"{a.model}|{d}"
                if cache.get(ck, {}).get("h") != h:
                    t1 = time.time()
                    cache[ck] = dict(h=h, model=a.model, secs=0, **ask(a.model, prompt))
                    cache[ck]["secs"] = round(time.time() - t1, 1)
                    keep(d, a.model, prompt, {k: cache[ck].get(k) for k in KEYS + ("json_ok",)}, cache[ck]["secs"])
                    n_ask += 1
                    print(f"  {d} · {shape['prompts']} prompts ({shape['distinct']} distinct, {shape['loop_prompts']} loop) · "
                          f"{shape['projects']} projects · {cache[ck]['secs']}s · {str(cache[ck].get('focus') or cache[ck].get('en'))[:90]}", flush=True)
                    if n_ask % 10 == 0:
                        cache_f.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        finally:
            cache_f.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
            cache_f.chmod(0o600)
            if tun:
                tun.terminate()
    out = dict(built=time.strftime("%Y-%m-%d %H:%M"), model=a.model, host=a.host,
               days=[dict(day=d, **D[d], summary={k: cache[f"{a.model}|{d}"].get(k) for k in KEYS}
                          if f"{a.model}|{d}" in cache else None) for d in days])
    (OUT / "days.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (OUT / "days.json").chmod(0o600)
    print(f"days: {len(days)} days · {sum(1 for d in days if f"{a.model}|{d}" in cache)} summarised · {time.time() - t0:.0f}s -> {OUT / 'days.json'}")


if __name__ == "__main__":
    main()
