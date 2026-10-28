"""The vector space, read for patterns: which oracle does what, what works, how the work grew, lessons learned.

    RELIC4_ROOT=~/relic4-fresh uv run --project ../agent-relic-v4 python report/vspace.py

Needs topics.py's topic_of.parquet (each prompt text's topic) and relic4's vectors (the fresh cache and the text-v2
LanceDB table). Writes ~/.relic-extract/report/vspace.json; build.py puts it in the page.

  oracles   each repository's centre (mean of its prompt vectors), the nearest others, a 2-D map, its topic mix
  works     what the NEXT prompt in a main session is: ship (commit, merge, ok) or friction (a correction,
            "is it working?"); by topic, by oracle, by month. A heuristic read of the conversation, not a test result
  growth    month-to-month drift of the centre, how many topics a month spans, and how many prompts are new
            (nothing earlier within cosine 0.80) or repeats (an earlier one within 0.97)
  lessons   per month, the passages nearest "lesson learned / root cause / never again / what worked" (vector search)
"""

import glob
import json
import math
import os
import re
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

import common

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
ROOT = Path(os.environ.get("RELIC4_ROOT", HOME / "relic4-fresh")).expanduser()
SHIP = {"Commit, push, PR, ship", "Merge it", "OK, sure, go ahead"}
FRICTION = {"Corrections: not this, remove, should be", "Is it working? continue, check, fix"}
LESSON_QUERIES = {
    "lesson": "lesson learned: what went wrong and what we will do differently next time",
    "root cause": "the root cause of the bug was",
    "rule": "from now on always do this, never do that again",
    "worked": "this worked well, keep doing it this way",
}


def unit(a):
    return a / (np.linalg.norm(a, axis=-1, keepdims=True) + 1e-9)


def pca2(M):
    C = M - M.mean(0)
    _, _, vt = np.linalg.svd(C, full_matrices=False)
    xy = C @ vt[:2].T
    return (xy - xy.min(0)) / (xy.max(0) - xy.min(0) + 1e-9)


def main():
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    vdir = sorted(glob.glob(str(ROOT / "vec/_cache/unsloth--embeddinggemma-300m@*")))[0]
    con.execute(f"create view topic_of as select * from read_parquet('{OUT / 'topic_of.parquet'}')")
    topics = json.loads((OUT / "topics.json").read_text())
    tname = {t["id"]: t.get("name") or " · ".join(t["terms"][:3]) for t in topics["topics"]}

    # every prompt (first chunk of a user message) with its session, repository, month, topic and vector
    con.execute("""create table pr as select x.msgkey, x.key, x.sid, x.tier, x.ts_th, substr(x.ts_th, 1, 7) m,
                   coalesce(ses.cat, x.cat) cat, t.topic from x left join ses using (sid) join topic_of t using (key)
                   where x.role = 'user' and x.chunk = 0 and x.ts_th <> ''""")
    con.execute(f"""create table kv as select key, any_value(vector) AS vector from read_parquet('{vdir}/*.parquet')
                    where key in (select distinct key from pr) group by key""")
    kv = con.sql("select key, vector from kv order by key").to_arrow_table()
    keys = kv.column("key").to_pylist()
    vec = kv.column("vector").combine_chunks()
    V = unit(vec.flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(vec), -1))
    kix = {k: i for i, k in enumerate(keys)}
    P = con.execute("select msgkey, key, sid, tier, ts_th, m, cat, topic from pr order by sid, ts_th").fetchall()
    months = sorted({r[5] for r in P})
    print(f"vspace: {len(P):,} prompts · {len(keys):,} distinct vectors · {len(months)} months · {time.time() - t0:.0f}s")

    # ---------- oracles ----------
    by_cat = defaultdict(list)
    for r in P:
        by_cat[r[6]].append(r)
    cats = [c for c, _ in sorted(by_cat.items(), key=lambda kv: -len(kv[1])) if c and c != "home / other"][:30]
    cent = unit(np.stack([V[[kix[r[1]] for r in by_cat[c]]].mean(0) for c in cats]))
    S = cent @ cent.T
    xy = pca2(cent)
    oracles = []
    for i, c in enumerate(cats):
        tc = Counter(r[7] for r in by_cat[c])
        n = sum(tc.values())
        mix = {tname[t]: round(v / n, 4) for t, v in tc.most_common()}
        p = np.array(list(tc.values()), float) / n
        near = [cats[j] for j in np.argsort(-S[i]) if j != i][:3]
        ms = Counter(r[5] for r in by_cat[c])
        oracles.append(dict(name=c, prompts=n, xy=[round(float(v), 3) for v in xy[i]], near=near,
                            near_cos=[round(float(S[i, cats.index(x)]), 3) for x in near],
                            focus=round(float(math.exp(-(p * np.log(p)).sum())), 1),  # effective number of topics
                            top=list(mix.items())[:4], mix=mix, months=[ms.get(m, 0) for m in months]))
    print(f"vspace: {len(cats)} oracles mapped")

    # ---------- what works: the next prompt in the same main session ----------
    nxt_t = Counter()
    src = defaultdict(Counter)   # (dimension, value) -> {"ship": n, "friction": n, "n": n}
    sessions = defaultdict(list)
    for r in P:
        if r[3] == "main":
            sessions[r[2]].append(r)
    shipped = Counter()
    for sid, rs in sessions.items():
        rs.sort(key=lambda r: r[4])
        for a, b in zip(rs, rs[1:]):
            out = "ship" if tname[b[7]] in SHIP else "friction" if tname[b[7]] in FRICTION else "other"
            for dim, val in (("topic", tname[a[7]]), ("oracle", a[6]), ("month", a[5])):
                src[(dim, val)][out] += 1
                src[(dim, val)]["n"] += 1
            nxt_t[(tname[a[7]], tname[b[7]])] += 1
        if len(rs) > 1:
            last = rs[-1]
            shipped[(last[5], "sessions")] += 1
            shipped[(last[5], "shipped")] += any(tname[r[7]] in SHIP for r in rs)

    def rates(dim, keep=None, min_n=200):
        out = []
        for (d, v), c in src.items():
            if d == dim and c["n"] >= min_n and (keep is None or v in keep):
                out.append(dict(name=v, n=c["n"], ship=round(c["ship"] / c["n"], 4), friction=round(c["friction"] / c["n"], 4)))
        return sorted(out, key=lambda x: -x["n"])

    works = dict(by_topic=rates("topic"), by_oracle=rates("oracle", set(cats), 150), by_month=rates("month", min_n=50),
                 shipped=[dict(month=m, sessions=shipped[(m, "sessions")], shipped=shipped[(m, "shipped")]) for m in months],
                 flows=[dict(src=a, dst=b, n=n) for (a, b), n in nxt_t.most_common(40) if a != b])

    # ---------- growth ----------
    mi = {m: i for i, m in enumerate(months)}
    by_m = defaultdict(list)
    first = {}
    for r in P:
        by_m[r[5]].append(kix[r[1]])
        first[r[1]] = min(first.get(r[1], r[5]), r[5])
    mc = unit(np.stack([V[by_m[m]].mean(0) for m in months]))
    drift = [0.0] + [round(float(1 - mc[i] @ mc[i - 1]), 4) for i in range(1, len(months))]
    breadth = []
    for m in months:
        tc = Counter(r[7] for r in P if r[5] == m)
        p = np.array(list(tc.values()), float) / sum(tc.values())
        breadth.append(round(float(math.exp(-(p * np.log(p)).sum())), 1))
    rng = np.random.default_rng(0)
    novel, repeat = [], []
    for i, m in enumerate(months):
        new_keys = [kix[k] for k, fm in first.items() if fm == m]
        old_keys = np.array([kix[k] for k, fm in first.items() if fm < m], int)
        if not len(old_keys) or not new_keys:
            novel.append(1.0 if new_keys else 0.0)
            repeat.append(0.0)
            continue
        sample = rng.choice(new_keys, size=min(3000, len(new_keys)), replace=False)
        best = np.full(len(sample), -1.0, np.float32)
        Q = V[sample]
        for j in range(0, len(old_keys), 20000):
            best = np.maximum(best, (Q @ V[old_keys[j:j + 20000]].T).max(1))
        novel.append(round(float((best < 0.80).mean()), 4))
        repeat.append(round(float((best >= 0.97).mean()), 4))
    growth = dict(months=months, drift=drift, breadth=breadth, novel=novel, repeat=repeat,
                  oracles_active=[sum(1 for o in oracles if o["months"][i]) for i in range(len(months))])
    print(f"vspace: growth over {len(months)} months · {time.time() - t0:.0f}s")

    # ---------- lessons: vector search per month on relic4's text-v2 table ----------
    lessons = {}
    try:
        from relic4.core import Relic
        rel = Relic(root=str(ROOT), recipe="text-v2")
        for m in months:
            got = []
            for label, q in LESSON_QUERIES.items():
                res = rel.search(q, n=3, mode="vector", month=m)
                for h in res.get("vector", []):
                    if len(h["text"]) < 60:
                        continue
                    got.append(dict(kind=label, role=h["role"], cat=h["cat"], ts=h["ts_th"][:16], distance=h["distance"],
                                    text=re.sub(r"\s+", " ", h["text"])[:280], msgkey=h["msgkey"]))
            got.sort(key=lambda g: g["distance"])
            seen, keep = set(), []
            for g in got:
                if g["msgkey"] not in seen:
                    seen.add(g["msgkey"])
                    keep.append(g)
            lessons[m] = keep[:6]
        print(f"vspace: lessons searched · {time.time() - t0:.0f}s")
    except Exception as ex:  # relic4 not importable: the page simply has no lessons section
        print(f"vspace: lessons skipped ({ex.__class__.__name__}: {str(ex)[:120]})")

    out = dict(built=time.strftime("%Y-%m-%d %H:%M"), months=months, oracles=oracles, works=works, growth=growth,
               lessons=lessons, ship=sorted(SHIP), friction=sorted(FRICTION), secs=round(time.time() - t0, 1))
    (OUT / "vspace.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (OUT / "vspace.json").chmod(0o600)
    print(f"vspace: done in {time.time() - t0:.0f}s -> {OUT / 'vspace.json'}")


if __name__ == "__main__":
    main()
