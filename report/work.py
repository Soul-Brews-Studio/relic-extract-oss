"""Did the work land? Each repository's prompts set against its git history and its ψ vault, month by month.

    uv run python report/work.py [--top 40]

For the --top repositories by main-session prompts: the local clone (ghq; the most recently changed clone when
several share the name), `git log --all` since the first month of the index (each commit once, by hash) ->
commits, authored lines (+/-), markdown files touched; the vault (vaults.py) -> notes written per month.
Per repository: commits per 100 prompts, and the share of its active months that had commits. -> work.json
"""

import argparse
import json
import os
import subprocess
import time
from collections import defaultdict
from pathlib import Path

import common

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"


def clones():
    root = subprocess.run(["ghq", "root"], capture_output=True, text=True).stdout.strip()
    best = {}
    for rel in subprocess.run(["ghq", "list"], capture_output=True, text=True).stdout.split():
        p = os.path.join(root, rel)
        parts = rel.split("/")
        try:
            mt = os.stat(os.path.join(p, ".git")).st_mtime
        except OSError:
            continue
        # relic-scan's cat is the repo name lowercased, sometimes with its org in front (Arkkra-Co/volt-oracle ->
        # arkkra-co-volt-oracle): index both
        for name in {parts[-1].lower(), "-".join(parts[-2:]).lower()}:
            if name not in best or mt > best[name][1]:
                best[name] = (p, mt)
    return {k: v[0] for k, v in best.items()}


def history(path, since):
    """{month: {commits, add, del, md}} over every branch, each commit once."""
    out = subprocess.run(["git", "-C", path, "log", "--all", f"--since={since}", "--no-merges", "--date=format:%Y-%m",
                          "--format=@@%H|%ad", "--numstat"], capture_output=True, text=True, timeout=120).stdout
    seen, m, by = set(), None, defaultdict(lambda: dict(commits=0, add=0, dele=0, md=0))
    skip = False
    for line in out.splitlines():
        if line.startswith("@@"):
            h, m = line[2:].split("|", 1)
            skip = h in seen
            seen.add(h)
            if not skip:
                by[m]["commits"] += 1
        elif line.strip() and not skip and m:
            parts = line.split("\t")
            if len(parts) == 3:
                a, d, f = parts
                if a.isdigit():
                    by[m]["add"] += int(a)
                if d.isdigit():
                    by[m]["dele"] += int(d)
                if f.endswith(".md"):
                    by[m]["md"] += 1
    return by


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=40)
    a = ap.parse_args()
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    months = [r[0] for r in con.execute("select distinct substr(ts_th, 1, 7) from x where ts_th <> '' order by 1").fetchall()]
    mi = {m: i for i, m in enumerate(months)}
    top = con.execute("""select ses.cat, count(distinct x.msgkey) n from x join ses using (sid)
                         where x.role = 'user' and x.tier = 'main' and ses.cat not in ('home / other', 'codex (no cwd in path)', 'omp (no cwd in path)')
                         group by 1 order by n desc limit ?""", [a.top]).fetchall()
    notes_f = HOME / ".relic-extract" / "vault" / "notes.parquet"
    if notes_f.exists():
        con.execute(f"create view notes as select * from read_parquet('{notes_f}')")
    cl = clones()
    repos = []
    tot = dict(prompts=[0] * len(months), commits=[0] * len(months), notes=[0] * len(months))
    for cat, n in top:
        pm = [0] * len(months)
        for m, k in con.execute("""select substr(x.ts_th, 1, 7), count(distinct x.msgkey) from x join ses using (sid)
                                   where x.role = 'user' and x.tier = 'main' and ses.cat = ? and x.ts_th <> '' group by 1""", [cat]).fetchall():
            pm[mi[m]] = k
        path = cl.get(cat.lower())
        hist = history(path, months[0] + "-01") if path else {}
        cm = [hist.get(m, {}).get("commits", 0) for m in months]
        nm = [0] * len(months)
        if notes_f.exists():
            for m, k in con.execute("select month, count(distinct msgkey) from notes where cat = ? and chunk = 0 group by 1", [cat]).fetchall():
                if m in mi:
                    nm[mi[m]] = k
        active = [i for i, v in enumerate(pm) if v]
        repos.append(dict(name=cat, clone=bool(path), prompts=sum(pm), commits=sum(cm), notes=sum(nm),
                          add=sum(v["add"] for v in hist.values()), dele=sum(v["dele"] for v in hist.values()),
                          md=sum(v["md"] for v in hist.values()),
                          per100=round(100 * sum(cm) / max(1, sum(pm)), 1),
                          landed=round(sum(1 for i in active if cm[i]) / max(1, len(active)), 3),
                          months=dict(prompts=pm, commits=cm, notes=nm)))
        for k, arr in (("prompts", pm), ("commits", cm), ("notes", nm)):
            tot[k] = [x + y for x, y in zip(tot[k], arr)]
        print(f"  {cat[:34]:<34} prompts {sum(pm):>6,} commits {sum(cm):>5,} md {repos[-1]['md']:>5,} notes {sum(nm):>5,} "
              f"per100 {repos[-1]['per100']:>5} {'' if path else '(no clone)'}", flush=True)
    out = dict(built=time.strftime("%Y-%m-%d %H:%M"), months=months, repos=repos, totals=tot)
    (OUT / "work.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (OUT / "work.json").chmod(0o600)
    print(f"work: {len(repos)} repositories · {sum(r['clone'] for r in repos)} with a clone · {time.time() - t0:.0f}s -> {OUT / 'work.json'}")


if __name__ == "__main__":
    main()
