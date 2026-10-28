"""Two pictures of what was asked: a semantic map (UMAP of the prompt vectors, coloured by topic) and a word cloud.

    uv run --project ../agent-relic-v4 --with umap-learn --with scikit-learn --with pythainlp python report/pictures.py

Semantic map: up to 20,000 distinct main-session prompt texts (stratified by topic, fixed seed), their gemma
vectors -> PCA(50) -> UMAP 2-D (cosine, 30 neighbours, min_dist 0.1); each point keeps its topic; the 15 largest
topics are labelled at their median. Word cloud: every word of every main-session prompt the clean step kept,
English and Thai (pythainlp newmm), counted once per prompt, stopwords and fillers out; the top 220.
-> ~/.relic-extract/report/pictures.json
"""

import glob
import json
import os
import time
from collections import Counter
from pathlib import Path

import numpy as np

import common
import topics as tp

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
ROOT = Path(os.environ.get("RELIC4_ROOT", HOME / "relic4-fresh")).expanduser()
FILLER = set("ok okay yes yep please pls use show sure cool good great thanks thank let lets now just".split())


def main():
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    vdir = sorted(glob.glob(str(ROOT / "vec/_cache/unsloth--embeddinggemma-300m@*")))[0]
    con.execute(f"create view topic_of as select * from read_parquet('{OUT / 'topic_of.parquet'}')")
    topics = json.loads((OUT / "topics.json").read_text())
    tname = {t["id"]: t.get("name") or " · ".join(t["terms"][:3]) for t in topics["topics"]}

    # ---------- semantic map ----------
    con.execute("""create table mp as select t.key, any_value(t.topic) topic, count(distinct x.msgkey) n
                   from x join topic_of t using (key) where x.role = 'user' and x.chunk = 0 and x.tier = 'main' group by 1""")
    rows = con.execute("select key, topic, n from mp").fetchall()
    rng = np.random.default_rng(0)
    by_t = {}
    for k, tpc, n in rows:
        by_t.setdefault(tpc, []).append((k, n))
    total = len(rows)
    want = 20000
    pick = []
    for tpc, items in by_t.items():  # stratified: each topic keeps its share, at least 60 points
        m = min(len(items), max(60, round(want * len(items) / total)))
        idx = rng.choice(len(items), size=m, replace=False)
        pick += [(items[i][0], tpc) for i in idx]
    con.execute("create temp table pk as select * from (select unnest(?::varchar[]) AS key, unnest(?::int[]) AS topic)",
                [[k for k, _ in pick], [int(t) for _, t in pick]])
    tab = con.sql(f"""select pk.key, pk.topic, v.vector from pk join (select key, any_value(vector) AS vector
                     from read_parquet('{vdir}/*.parquet') where key in (select key from pk) group by key) v using (key)
                     order by pk.key""").to_arrow_table()
    lab = np.asarray(tab.column("topic").to_pylist())
    vec = tab.column("vector").combine_chunks()
    X = vec.flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(vec), -1)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    from sklearn.decomposition import PCA
    import umap
    Z = PCA(n_components=50, random_state=0).fit_transform(X)
    Y = umap.UMAP(n_neighbors=30, min_dist=0.1, metric="cosine", random_state=0).fit_transform(Z)
    lo, hi = np.percentile(Y, 1, axis=0), np.percentile(Y, 99, axis=0)  # islands of near-identical prompts sit far out
    inside = ((Y >= lo) & (Y <= hi)).all(1)
    Yn = np.clip((Y - lo) / (hi - lo), 0, 1)
    pts = [[int(a * 1000), int(b * 1000), int(t)] for (a, b), t, ok in zip(Yn, lab, inside) if ok]
    sizes = Counter(int(t) for t in lab)
    labels = []
    for tpc, n in sizes.most_common(15):
        sel = (lab == tpc) & inside
        mx, my = np.median(Yn[sel], axis=0)
        labels.append(dict(topic=int(tpc), name=tname[tpc], share=round(n / len(lab), 4), x=int(mx * 1000), y=int(my * 1000)))
    print(f"pictures: map of {len(pts):,} prompts ({int((~inside).sum())} outside the frame) from {total:,} · {time.time() - t0:.0f}s")

    # ---------- word cloud ----------
    from pythainlp.corpus import thai_stopwords
    from pythainlp.tokenize import word_tokenize
    thai_cut = lambda s: word_tokenize(s, engine="newmm", keep_whitespace=False)
    thai_stop = set(thai_stopwords()) | {"ครับ", "คะ", "ค่ะ", "นะ", "หน่อย", "เลย", "ด้วย", "ให้", "แล้ว", "ไหม", "มั้ย", "อะ", "จ้า", "ได้", "มี", "ไป", "มา", "ก็", "ว่า", "จะ"}
    words, n_prompts = Counter(), 0
    for (text,) in con.execute("""select any_value(text) from x where role = 'user' and chunk = 0 and tier = 'main'
                                  group by msgkey""").fetchall():
        toks = {w for w in tp.tokens(text, thai_cut, thai_stop) if w not in FILLER and len(w) <= 24}
        if toks:
            n_prompts += 1
            words.update(toks)
    cloud = [[w, n, 1 if tp.TH.search(w) else 0] for w, n in words.most_common(220)]
    out = dict(built=time.strftime("%Y-%m-%d %H:%M"), map=dict(points=pts, labels=labels, n=len(lab), from_n=total,
               outside=int((~inside).sum()), topics={int(k): tname[k] for k in sizes}),
               cloud=dict(words=cloud, prompts=n_prompts))
    (OUT / "pictures.json").write_text(json.dumps(out, ensure_ascii=False))
    (OUT / "pictures.json").chmod(0o600)
    print(f"pictures: cloud of {len(cloud)} words from {n_prompts:,} prompts · {time.time() - t0:.0f}s -> {OUT / 'pictures.json'}")
    print("  top:", ", ".join(f"{w} {n}" for w, n, _ in cloud[:25]))


if __name__ == "__main__":
    main()
