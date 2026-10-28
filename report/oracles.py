"""Oracles: how they are born, what for, how long they live, how they are used, and whether they work.

    uv run python report/oracles.py   -> ~/.relic-extract/report/oracles.json

An oracle is a repository whose name holds "oracle" (meta/sessions.parquet cat). Born = its first session; how =
the birth skill its first day loaded (awaken, bud, incubate, herdr-work) or none; for what = the topic (topics.py)
of its first main-session prompt; alive = activity in the last 30 days of the index. Works = how its follow-ups
went (ship vs friction, read from the next prompt's topic, as vspace.py does) and its tool-error rate.
"""

import json
import time
from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path

import common

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
SHIP = {"Commit, push, PR, ship", "Merge it", "OK, sure, go ahead"}
FRICTION = {"Corrections: not this, remove, should be", "Is it working? continue, check, fix"}
BIRTH = ["awaken", "bud", "incubate", "herdr-work"]
LIFE = [("1 day", 0, 0), ("2–7 days", 1, 6), ("8–30 days", 7, 29), ("31–90 days", 30, 89), ("over 90 days", 90, 10 ** 6)]


def d(s):
    return date.fromisoformat(s[:10])


def main():
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    con.execute(f"create view topic_of as select * from read_parquet('{OUT / 'topic_of.parquet'}')")
    tn = {t["id"]: t.get("name") or " · ".join(t["terms"][:3]) for t in json.loads((OUT / "topics.json").read_text())["topics"]}

    rows = con.execute("""select cat, min(first_ts) AS born, max(last_ts) AS lastd, count(*) AS sessions,
                          count(*) filter (where tier = 'main') AS mains, list(distinct sid) AS sids
                          from s where cat like '%oracle%' and first_ts <> '' group by cat""").fetchall()
    end = max(d(r[2]) for r in rows if r[2])
    months = [r[0] for r in con.execute("select distinct substr(first_ts, 1, 7) from s where first_ts <> '' order by 1").fetchall()]
    mi = {m: i for i, m in enumerate(months)}

    # birth skill: a birth skill loaded in the oracle's sessions within a day of its first session
    births = defaultdict(set)
    for cat, k, ts in con.execute("""select ses.cat, regexp_extract(r.text, 'skills/([A-Za-z0-9_.:-]+)', 1), min(r.ts_th)
                                     from r join ses using (sid) where r.s = 'text' and r.role = 'user'
                                     and r.text like 'Base directory for this skill:%' and ses.cat like '%oracle%'
                                     group by 1, 2""").fetchall():
        if k in BIRTH:
            births[cat].add((k, ts))
    # usage: prompts by who wrote them, tool calls and errors, per oracle
    use = {r[0]: r[1:] for r in con.execute("""select ses.cat, count(distinct x.msgkey) filter (where x.tier = 'main'),
                 count(distinct x.msgkey) filter (where x.tier <> 'main') from x join ses using (sid)
                 where x.role = 'user' and ses.cat like '%oracle%' group by 1""").fetchall()}
    tools = {r[0]: r[1:] for r in con.execute("""select ses.cat, count(*) filter (where r.kind = 'call'),
                 count(*) filter (where r.kind = 'result' and r.err), count(*) filter (where r.kind = 'result')
                 from r join ses using (sid) where r.s = 'tools' and ses.cat like '%oracle%' group by 1""").fetchall()}
    # first main-session prompt of each oracle, and every main-session follow-up for ship vs friction
    first_topic, flow = {}, defaultdict(Counter)
    seq = defaultdict(list)
    for cat, sid, ts, topic in con.execute("""select ses.cat, x.sid, x.ts_th, t.topic from x join ses using (sid)
            join topic_of t using (key) where x.role = 'user' and x.chunk = 0 and x.tier = 'main' and ses.cat like '%oracle%'
            and x.ts_th <> '' order by ses.cat, x.ts_th""").fetchall():
        first_topic.setdefault(cat, tn[topic])
        seq[(cat, sid)].append(tn[topic])
    for (cat, _), ts_ in seq.items():
        for a, b in zip(ts_, ts_[1:]):
            flow[cat]["ship" if b in SHIP else "friction" if b in FRICTION else "other"] += 1
            flow[cat]["n"] += 1
    skills = Counter(k for k, in con.execute("""select regexp_extract(r.text, 'skills/([A-Za-z0-9_.:-]+)', 1) from r join ses using (sid)
            where r.s = 'text' and r.role = 'user' and r.text like 'Base directory for this skill:%' and ses.cat like '%oracle%'""").fetchall())

    oracles = []
    for cat, born, lastd, sessions, mains, sids in rows:
        b, l = d(born), d(lastd or born)
        bs = sorted(births.get(cat, []), key=lambda x: x[1])
        how = next((k for k, ts in bs if d(ts) <= b + timedelta(days=1)), "none")
        um, ua = use.get(cat, (0, 0))
        tc, te, tr = tools.get(cat, (0, 0, 0))
        f = flow.get(cat, Counter())
        oracles.append(dict(name=cat, born=born[:10], last=(lastd or born)[:10], days=(l - b).days + 1, sessions=sessions,
                            main_sessions=mains, alive=(end - l).days <= 30, how=how, born_for=first_topic.get(cat),
                            prompts_main=um, prompts_agent=ua, tool_calls=tc, error_rate=round(te / tr, 4) if tr else None,
                            followups=f["n"], ship=round(f["ship"] / f["n"], 4) if f["n"] else None,
                            friction=round(f["friction"] / f["n"], 4) if f["n"] else None))
    oracles.sort(key=lambda o: -(o["prompts_main"] + o["prompts_agent"]))

    def life(o):
        return next(name for name, lo, hi in LIFE if lo <= o["days"] - 1 <= hi)

    per_month = {k: [0] * len(months) for k in BIRTH + ["none"]}
    active = [0] * len(months)
    last_seen = [0] * len(months)
    for o in oracles:
        per_month[o["how"]][mi[o["born"][:7]]] += 1
        for i, m in enumerate(months):
            if o["born"][:7] <= m <= o["last"][:7]:
                active[i] += 1
        if not o["alive"]:
            last_seen[mi[o["last"][:7]]] += 1
    cohorts = {}
    for name, _, _ in LIFE:
        cs = [o for o in oracles if life(o) == name]
        n_f = sum(o["followups"] for o in cs)
        cohorts[name] = dict(n=len(cs), alive=sum(o["alive"] for o in cs), sessions=sum(o["sessions"] for o in cs),
                             prompts=sum(o["prompts_main"] + o["prompts_agent"] for o in cs),
                             ship=round(sum((o["ship"] or 0) * o["followups"] for o in cs) / n_f, 4) if n_f else None,
                             friction=round(sum((o["friction"] or 0) * o["followups"] for o in cs) / n_f, 4) if n_f else None,
                             how=Counter(o["how"] for o in cs).most_common(), born_for=Counter(o["born_for"] for o in cs if o["born_for"]).most_common(5))
    out = dict(built=time.strftime("%Y-%m-%d %H:%M"), end=str(end), months=months, n=len(oracles),
               alive=sum(o["alive"] for o in oracles), births=per_month, active=active, went_quiet=last_seen,
               cohorts=cohorts, born_for=Counter(o["born_for"] for o in oracles if o["born_for"]).most_common(10),
               skills=skills.most_common(16), oracles=oracles)
    (OUT / "oracles.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (OUT / "oracles.json").chmod(0o600)
    print(f"oracles: {len(oracles)} oracles · {out['alive']} alive · born by {dict((k, sum(v)) for k, v in per_month.items())} · {time.time() - t0:.0f}s")
    for k, v in cohorts.items():
        print(f"  {k:<13} {v['n']:>4} oracles · alive {v['alive']} · prompts {v['prompts']:,} · ship {v['ship']} · friction {v['friction']} · how {v['how'][:3]}")


if __name__ == "__main__":
    main()
