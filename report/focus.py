"""Focus: the story of a family of repositories (maw*, relic*), read from its prompts and their vectors.

    RELIC4_ROOT=~/relic4-fresh uv run --project ../agent-relic-v4 --with scikit-learn --with pythainlp python report/focus.py

For each family: its repositories over time, its features (k-means inside the family's own prompt vectors, named by
c-TF-IDF words, then by hand in focus-names.json), how each repository started and where it stands, the tools and
skills its sessions used, how its follow-ups went (ship vs friction), and its lessons (relic4 vector search inside the
family). Plus the story of the whole focus: each month's share of prompts per family. -> ~/.relic-extract/report/focus.json
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
import topics as tp

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
ROOT = Path(os.environ.get("RELIC4_ROOT", HOME / "relic4-fresh")).expanduser()
FAMILIES = {   # family -> (repository regex, prompt-text regex or None): a prompt belongs by its repository first
    "maw": (r"(^|-)maw|mawjs|mawui", None),
    "relic": (r"relic|jsonl", r"(?i)\brelic[0-9]?\b|relic-(scan|extract|studio)|agent-relic|jsonl[- ]truth|jsonl-indexer"),
}
SHIP = {"Commit, push, PR, ship", "Merge it", "OK, sure, go ahead"}
FRICTION = {"Corrections: not this, remove, should be", "Is it working? continue, check, fix"}
LESSONS = {"lesson": "lesson learned: what went wrong and what we will do differently next time",
           "root cause": "the root cause of the bug was",
           "worked": "this worked well, keep doing it this way"}


def unit(a):
    return a / (np.linalg.norm(a, axis=-1, keepdims=True) + 1e-9)


def clean(s, n=200):
    return re.sub(r"\s+", " ", s or "")[:n]


def fuzzy_name(terms, names, used, prefix=""):
    """A hand-given name whose 3 words are, 2 or more of them, among this topic's top 6 words (each name used once):
    near-equal word scores swap places between runs, so an exact match on the top 3 drops names that still fit."""
    top = set(terms[:6])
    best, score = None, 1
    for k, v in names.items():
        if prefix and not k.startswith(prefix + ":"):
            continue
        words = set(k.split(":", 1)[-1].split(" · "))
        hit = len(words & top)
        if hit > score and k not in used:
            best, score = k, hit
    if best:
        used.add(best)
        return names[best]
    return None


def main():
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    vdir = sorted(glob.glob(str(ROOT / "vec/_cache/unsloth--embeddinggemma-300m@*")))[0]
    con.execute(f"create view topic_of as select * from read_parquet('{OUT / 'topic_of.parquet'}')")
    gtopics = json.loads((OUT / "topics.json").read_text())
    gname = {t["id"]: t.get("name") or " · ".join(t["terms"][:3]) for t in gtopics["topics"]}
    fam_case = "case " + " ".join(f"when regexp_matches(coalesce(ses.cat, x.cat), '{rx}') then '{f}'" for f, (rx, _) in FAMILIES.items()) + \
        " " + " ".join(f"when regexp_matches(x.text, '{tx}') then '{f}'" for f, (_, tx) in FAMILIES.items() if tx) + " else 'other' end"
    con.execute(f"""create table pr as select x.msgkey, x.key, x.sid, x.tier, x.ts_th, substr(x.ts_th, 1, 7) m, x.text,
                    coalesce(ses.cat, x.cat) cat, {fam_case} fam, t.topic from x left join ses using (sid) join topic_of t using (key)
                    where x.role = 'user' and x.chunk = 0 and x.ts_th <> ''""")
    months = [r[0] for r in con.execute("select distinct m from pr order by m").fetchall()]
    story = {f: [0] * len(months) for f in list(FAMILIES) + ["other"]}
    mi = {m: i for i, m in enumerate(months)}
    for f, m, n in con.execute("select fam, m, count(distinct msgkey) from pr where tier = 'main' group by all").fetchall():
        story[f][mi[m]] = n

    from pythainlp.corpus import thai_stopwords
    from pythainlp.tokenize import word_tokenize
    from sklearn.cluster import KMeans
    thai_cut = lambda s: word_tokenize(s, engine="newmm", keep_whitespace=False)
    thai_stop = set(thai_stopwords())
    names_f = Path(__file__).parent / "focus-names.json"
    names = json.loads(names_f.read_text()) if names_f.exists() else {}
    try:
        from relic4.core import Relic
        rel = Relic(root=str(ROOT), recipe="text-v2")
    except Exception as ex:
        rel = None
        print(f"focus: lessons skipped ({ex.__class__.__name__})")

    out = {"months": months, "story": story, "families": {}}
    used = set()
    for fam in FAMILIES:
        con.execute(f"create or replace table fp as select * from pr where fam = '{fam}'")
        n_msgs = con.execute("select count(distinct msgkey) from fp").fetchall()[0][0]
        if not n_msgs:
            continue
        # repositories over time
        repos = []
        for cat, n, f, l in con.execute("""select cat, count(distinct msgkey) n, min(m), max(m) from fp group by 1 order by n desc""").fetchall():
            ms = dict(con.execute(f"select m, count(distinct msgkey) from fp where cat = ? group by 1", [cat]).fetchall())
            first = con.execute("select text, ts_th from fp where cat = ? and tier = 'main' and length(text) > 30 order by ts_th limit 3", [cat]).fetchall()
            latest = con.execute("select text, ts_th from fp where cat = ? and tier = 'main' and length(text) > 30 order by ts_th desc limit 3", [cat]).fetchall()
            repos.append(dict(name=cat, prompts=n, first=f, last=l, months=[ms.get(m, 0) for m in months],
                              started=[[ts[:10], clean(tx)] for tx, ts in first], now=[[ts[:10], clean(tx)] for tx, ts in latest]))
        repos_main = [r for r in repos if r["prompts"] >= 20]

        # features: k-means inside the family's distinct prompt texts
        con.execute(f"""create or replace table fk as select fp.key, any_value(fp.text) AS text, count(distinct fp.msgkey) AS n
                        from fp group by 1""")
        con.execute(f"""create or replace table fv as select key, any_value(vector) AS vector from read_parquet('{vdir}/*.parquet')
                        where key in (select key from fk) group by key""")
        tab = con.sql("select fk.key, fk.text, fk.n, fv.vector from fk join fv using (key) order by fk.key").to_arrow_table()
        keys, texts = tab.column("key").to_pylist(), tab.column("text").to_pylist()
        w = np.asarray(tab.column("n").to_pylist(), float)
        vec = tab.column("vector").combine_chunks()
        X = unit(vec.flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(vec), -1))
        k = int(max(4, min(14, round(math.sqrt(len(keys) / 40)))))
        km = KMeans(n_clusters=k, random_state=0, n_init=4).fit(X, sample_weight=w)
        lab = km.labels_
        C = unit(km.cluster_centers_)
        sim = (X * C[lab]).sum(1)
        tf = [Counter() for _ in range(k)]
        for i, tx in enumerate(texts):
            for tok in set(tp.tokens(tx, thai_cut, thai_stop)):
                tf[lab[i]][tok] += w[i]
        freq = Counter()
        for c in tf:
            freq.update(c)
        A = sum(freq.values()) / k
        kix = {kk: i for i, kk in enumerate(keys)}
        fm = defaultdict(lambda: [0] * len(months))
        cat_of = defaultdict(Counter)
        for key, m, cat, n in con.execute("select key, m, cat, count(distinct msgkey) from fp group by all").fetchall():
            if key in kix:
                fm[lab[kix[key]]][mi[m]] += n
                cat_of[lab[kix[key]]][cat] += n
        feats = []
        for c in range(k):
            tot = sum(tf[c].values()) or 1
            sc = {tok: (f / tot) * math.log(1 + A / freq[tok]) for tok, f in tf[c].items() if f >= 2}
            top = sorted(sc.items(), key=lambda kv: (-round(kv[1], 12), kv[0]))[:40]
            df = lambda tok: sum(1 for t in tf if tok in t)
            terms = [t for t, _ in sorted(((t, v * math.log(1 + k / df(t))) for t, v in top), key=lambda kv: (-round(kv[1], 12), kv[0]))[:8]]
            idx = np.where(lab == c)[0]
            near = sorted(idx[np.argsort(-sim[idx])][:30], key=lambda i: (len(texts[i]) > 220, -sim[i]))[:4]
            fp_ = " · ".join(sorted(terms[:3]))
            months_c = fm[c]
            nm = names.get(f"{fam}:{fp_}")
            if nm:
                used.add(f"{fam}:{fp_}")
            feats.append(dict(id=c, terms=terms, fingerprint=fp_, name=nm, msgs=int(sum(months_c)),
                              months=months_c, peak=months[int(np.argmax(months_c))] if sum(months_c) else None,
                              first=next((months[i] for i, v in enumerate(months_c) if v), None),
                              repos=cat_of[c].most_common(3), examples=[clean(texts[i], 180) for i in near]))
        for ft in feats:
            if not ft["name"]:
                ft["name"] = fuzzy_name(ft["terms"], names, used, fam)
        feats.sort(key=lambda f: (f["first"] or "", -f["msgs"]))

        # tools and skills in the family's sessions
        sids = [r[0] for r in con.execute("select distinct sid from fp").fetchall()]
        con.execute("create or replace temp table fs as select unnest(?::varchar[]) sid", [sids])
        tools = con.execute("""select name, count(*) n from r where s = 'tools' and kind = 'call' and sid in (select sid from fs)
                               group by 1 order by n desc limit 10""").fetchall()
        skills = con.execute("""select regexp_extract(text, 'skills/([A-Za-z0-9_.:-]+)', 1) k, count(*) n from r
                                where s = 'text' and role = 'user' and text like 'Base directory for this skill:%' and sid in (select sid from fs)
                                group by 1 having k <> '' order by n desc limit 10""").fetchall()

        # follow-ups: ship vs friction, by month
        seqs = defaultdict(list)
        for sid, ts, topic in con.execute("select sid, ts_th, topic from fp where tier = 'main' order by sid, ts_th").fetchall():
            seqs[sid].append((ts, gname[topic]))
        wk = defaultdict(Counter)
        for sid, rs in seqs.items():
            for a, b in zip(rs, rs[1:]):
                o = "ship" if b[1] in SHIP else "friction" if b[1] in FRICTION else "other"
                wk[a[0][:7]][o] += 1
                wk[a[0][:7]]["n"] += 1
        works = [dict(month=m, n=wk[m]["n"], ship=round(wk[m]["ship"] / wk[m]["n"], 4) if wk[m]["n"] else 0,
                      friction=round(wk[m]["friction"] / wk[m]["n"], 4) if wk[m]["n"] else 0) for m in months]

        # lessons inside the family: vector search per busy repository
        lessons = []
        if rel is not None:
            for r in repos_main[:6]:
                for label, q in LESSONS.items():
                    res = rel.search(q, n=2, mode="vector", cat=r["name"])
                    for h in res.get("vector", []):
                        if len(h["text"]) >= 60:
                            lessons.append(dict(kind=label, repo=r["name"], role=h["role"], ts=h["ts_th"][:16],
                                                distance=h["distance"], text=clean(h["text"], 300), msgkey=h["msgkey"]))
            seen, keep = set(), []
            for l in sorted(lessons, key=lambda l: l["distance"]):
                if l["msgkey"] not in seen:
                    seen.add(l["msgkey"])
                    keep.append(l)
            lessons = keep[:14]
        out["families"][fam] = dict(prompts=int(n_msgs), repos=repos_main, repos_all=len(repos), features=feats,
                                    tools=tools, skills=skills, works=works, lessons=lessons, k=k)
        print(f"focus {fam}: {n_msgs:,} prompts · {len(repos)} repositories · {k} features · {len(lessons)} lessons · {time.time() - t0:.0f}s")
        for f in feats:
            print(f"   {fam}:{f['fingerprint']}  | {f['msgs']:,} first {f['first']} peak {f['peak']} | {f['examples'][0][:90]}")
    (OUT / "focus.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    (OUT / "focus.json").chmod(0o600)
    print(f"focus: done in {time.time() - t0:.0f}s -> {OUT / 'focus.json'}")


if __name__ == "__main__":
    main()
