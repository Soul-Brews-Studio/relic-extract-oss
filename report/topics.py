"""Topics per month: what the prompts were about, from their embeddings (scan -> extract -> embed -> topics).

    uv run --with scikit-learn --with pythainlp python report/topics.py [--vectors DIR] [--k 36]

Every distinct prompt text (the first chunk of a user message, main session or agent to agent) is joined to its
vector by key (blake2b of the chunk text, the same key relic4's cache uses), clustered with k-means on the unit
sphere (cosine), weighted by how many messages carry that text. Each topic is named by c-TF-IDF: the words that
are frequent in it and rare in the others (English words, Thai words cut with pythainlp). Writes OUT/topics.json;
build.py puts it in the page. Only counts, words and (private view) three short example prompts per topic leave
the data.
"""

import argparse
import glob
import json
import math
import re
import time
from collections import Counter
from pathlib import Path

import numpy as np

import common

HOME = Path.home()
STOP = set("""the and for you that this with are not but can have from was what all use your will now then just one
also any its our out get how into run see let new make why when who which more need there they them their than only
here more like want please should could would about been some each other via was were has had does did done yes
lets ok okay still very much many over under after before again same using used file files code check sure think
http https www com github html json true false null none pls plz show tell give look find keep try sure never real
line also create update read write help done good work working thing things way time first last next now""".split())
EN = re.compile(r"[a-z][a-z0-9_.+-]{2,}")
TH = re.compile(r"[฀-๿]")


def vector_dir(given):
    if given:
        return Path(given).expanduser()
    for root in ("~/relic4-fresh", "~/relic4", "~/relic4-scratch"):
        hits = sorted(glob.glob(str(Path(root).expanduser() / "vec/_cache/unsloth--embeddinggemma-300m*/")))
        hits = [h for h in hits if glob.glob(str(Path(h) / "*.parquet"))]
        if hits:
            return Path(hits[0])
    raise SystemExit("no vector cache found: run relic4 embed text-v2 (or pass --vectors)")


URLISH = re.compile(r"\S*[/\\]\S*|\S+@\S+|<[^>]{0,600}>")


def tokens(text, thai_cut, thai_stop):
    t = URLISH.sub(" ", text[:3000].lower())  # paths, urls, addresses and tags name places, not topics
    out = [w.strip(".-_+") for w in EN.findall(t)]
    out = [w for w in out if len(w) > 2 and w not in STOP and not w.isdigit()]
    if TH.search(t):
        out += [w for w in thai_cut(" ".join(re.findall(r"[฀-๿]+", t))) if len(w) > 1 and TH.search(w) and w not in thai_stop]
    return out


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", default=str(HOME / ".relic-scan"))
    ap.add_argument("--extract", default=str(HOME / ".relic-extract"))
    ap.add_argument("--vectors", default=None, help="a vector cache folder: *.parquet with (key, vector)")
    ap.add_argument("--k", type=int, default=36)
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    t0 = time.time()
    vdir = vector_dir(a.vectors)
    con = common.connect(Path(a.scan).expanduser(), Path(a.extract).expanduser())

    # one row per distinct prompt text: its vector, how many messages hold it, and in which months
    con.execute(f"""create table p as select x.key, any_value(x.text) text, count(distinct x.msgkey) n,
                    list(distinct substr(x.ts_th, 1, 7)) ms from x where x.role = 'user' and x.chunk = 0 group by x.key""")
    con.execute(f"create table v as select key, any_value(vector) vector from read_parquet('{vdir}/*.parquet') "
                f"where key in (select key from p) group by key")
    n_p, n_v = con.execute("select count(*), (select count(*) from v) from p").fetchall()[0]
    tab = con.sql("select p.key, p.text, p.n, v.vector from p join v using (key) order by p.key").to_arrow_table()
    keys = tab.column("key").to_pylist()
    texts = tab.column("text").to_pylist()
    w = np.asarray(tab.column("n").to_pylist(), np.float64)
    vec = tab.column("vector").combine_chunks()
    X = vec.flatten().to_numpy(zero_copy_only=False).astype(np.float32).reshape(len(vec), -1)
    X /= np.linalg.norm(X, axis=1, keepdims=True) + 1e-9
    print(f"topics: {n_p:,} distinct prompt texts · {n_v:,} with a vector ({n_v / max(n_p, 1):.2%}) · dim {X.shape[1]} · from {vdir}")

    from sklearn.cluster import MiniBatchKMeans
    km = MiniBatchKMeans(n_clusters=a.k, random_state=0, batch_size=8192, n_init=5, max_iter=200)
    lab = km.fit_predict(X, sample_weight=w)
    C = km.cluster_centers_ / (np.linalg.norm(km.cluster_centers_, axis=1, keepdims=True) + 1e-9)
    sim = (X * C[lab]).sum(1)  # cosine to the own centroid
    print(f"topics: k-means k={a.k} in {time.time() - t0:.0f}s · mean cosine to centroid {np.average(sim, weights=w):.3f}")

    # c-TF-IDF names
    from pythainlp.tokenize import word_tokenize
    from pythainlp.corpus import thai_stopwords
    thai_cut = lambda s: word_tokenize(s, engine="newmm", keep_whitespace=False)
    thai_stop = set(thai_stopwords()) | {"ครับ", "คะ", "ค่ะ", "นะ", "หน่อย", "เลย", "ด้วย", "ให้", "แล้ว", "ไหม", "มั้ย", "อะ", "จ้า"}
    tf = [Counter() for _ in range(a.k)]
    for i, t in enumerate(texts):
        for tok in set(tokens(t, thai_cut, thai_stop)):
            tf[lab[i]][tok] += w[i]
    # BERTopic's class-based TF-IDF: (frequency in the topic) × log(1 + average topic size / frequency overall)
    freq = Counter()
    for c in tf:
        freq.update(c)
    sizes = np.bincount(lab, weights=w, minlength=a.k)
    A = sum(freq.values()) / a.k
    terms = []
    for c in range(a.k):
        tot = sum(tf[c].values()) or 1
        df = lambda tok: sum(1 for t in tf if tok in t)
        sc = {tok: (f / tot) * math.log(1 + A / freq[tok]) for tok, f in tf[c].items() if f >= 3}
        top = sorted(sc.items(), key=lambda kv: (-round(kv[1], 12), kv[0]))[:60]  # then favour words few topics share
        sc = {tok: v * math.log(1 + a.k / df(tok)) for tok, v in top}
        terms.append([t for t, _ in sorted(sc.items(), key=lambda kv: (-round(kv[1], 12), kv[0]))[:8]])

    # per month: messages per topic
    ix = {k: i for i, k in enumerate(keys)}
    months = [r[0] for r in con.execute("select distinct substr(ts_th, 1, 7) m from x where ts_th <> '' order by m").fetchall()]
    mi = {m: i for i, m in enumerate(months)}
    cells = np.zeros((a.k, len(months)))
    for key, m, n in con.execute("""select key, substr(ts_th, 1, 7) m, count(distinct msgkey) n from x
                                    where role = 'user' and chunk = 0 and ts_th <> '' group by all""").fetchall():
        if key in ix and m in mi:
            cells[lab[ix[key]], mi[m]] += n

    # examples: the three distinct texts nearest the centroid, short ones first among the near
    ex = []
    for c in range(a.k):
        idx = np.where(lab == c)[0]
        near = idx[np.argsort(-sim[idx])][:40]
        near = sorted(near, key=lambda i: (len(texts[i]) > 220, -sim[i]))[:3]
        ex.append([re.sub(r"\s+", " ", texts[i])[:160] for i in near])

    # a 2-D map of the topics: the first two principal axes of the centroids
    Cc = C - C.mean(0)
    _, _, vt = np.linalg.svd(Cc, full_matrices=False)
    xy = Cc @ vt[:2].T
    xy = (xy - xy.min(0)) / (xy.max(0) - xy.min(0) + 1e-9)

    order = list(np.argsort(-sizes))
    out = {
        "built": time.strftime("%Y-%m-%d %H:%M"), "k": a.k, "months": months, "vectors": str(vdir).replace(str(HOME), "~"),
        "prompts": int(n_p), "with_vector": int(n_v), "secs": round(time.time() - t0, 1),
        "topics": [{"id": int(c), "terms": terms[c], "msgs": int(sizes[c]), "texts": int((lab == c).sum()),
                    "cohesion": round(float(np.average(sim[lab == c], weights=w[lab == c])), 3),
                    "months": [int(v) for v in cells[c]], "examples": ex[c], "xy": [round(float(v), 3) for v in xy[c]]}
                   for c in order],
    }
    # hand-given names (report/topic-names.json), keyed by a topic's first three words: when a re-run moves the
    # words, the name falls away and the words show instead of a stale name
    names_f = Path(__file__).parent / "topic-names.json"
    names = json.loads(names_f.read_text()) if names_f.exists() else {}
    used = set()
    for t in out["topics"]:
        fp = " · ".join(sorted(t["terms"][:3]))  # order-free: near-equal scores swap places between runs
        t["fingerprint"] = fp
        if fp in names:
            t["name"] = names[fp]
            used.add(fp)
    for t in out["topics"]:
        if "name" not in t:
            nm = fuzzy_name(t["terms"], names, used)
            if nm:
                t["name"] = nm
    named = sum(1 for t in out["topics"] if "name" in t)
    print(f"topics: {named}/{a.k} named from {names_f.name}")
    dst = Path(a.out or Path(a.extract).expanduser() / "report").expanduser()
    dst.mkdir(parents=True, exist_ok=True)
    (dst / "topics.json").write_text(json.dumps(out, ensure_ascii=False, indent=1))
    # each prompt text's topic, for vspace.py (key -> topic id, cosine to its centre)
    import pyarrow as pa
    import pyarrow.parquet as pq
    pq.write_table(pa.table({"key": keys, "topic": lab.astype("int32"), "cos": sim.astype("float32")}), dst / "topic_of.parquet")
    (dst / "topic_of.parquet").chmod(0o600)
    (dst / "topics.json").chmod(0o600)
    print(f"topics: {a.k} topics · {time.time() - t0:.0f}s -> {dst}/topics.json")
    for t in out["topics"][:12]:
        print(f"  {t['msgs']:>7,}  {' · '.join(t['terms'][:6])}")


if __name__ == "__main__":
    main()
