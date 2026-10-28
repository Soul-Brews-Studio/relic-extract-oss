"""Which prompts were spoken (speech-to-text) and which were typed: labels from Wispr Flow, a model for the rest.

    uv run --with rapidfuzz --with scikit-learn python report/stt.py

1. Ground truth. Wispr Flow keeps every dictation in ~/Library/Application Support/Wispr Flow/flow.sqlite (History:
   timestamp, app, the text it pasted), opened read-only. A prompt is SPOKEN when a dictation landed in the minutes
   before it was sent and the dictated text is inside the prompt (fuzzy, rapidfuzz partial_ratio >= 85).
2. Negatives. Prompts typed into the terminal over the same weeks (Wispr was running) with no dictation behind them.
3. Model. Char 1-4-gram TF-IDF + a few shape features (punctuation, code, paths, Thai share, length) -> logistic
   regression, scored by 5-fold cross-validation, then applied to every main-session prompt of the year.
-> ~/.relic-extract/report/stt.json: counts per month and project, cross-validated precision/recall, the threshold.
Dictated text and prompt text stay in the private files; stt.json carries counts and scores only, plus up to 12
private examples per side for the private view.
"""

import argparse
import json
import os
import re
import sqlite3
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
from rapidfuzz import fuzz
from scipy.sparse import csr_matrix, hstack
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import average_precision_score, precision_recall_fscore_support, roc_auc_score
from sklearn.model_selection import StratifiedKFold, cross_val_predict

import common

HOME = Path.home()
OUT = HOME / ".relic-extract" / "report"
WISPR = HOME / "Library" / "Application Support" / "Wispr Flow" / "flow.sqlite"
BKK = timezone(timedelta(hours=7))
WRAP = re.compile(r"^<channel [^>]*>\s*|\s*</channel>\s*$")


def norm(s):
    return re.sub(r"\s+", " ", WRAP.sub("", s or "")).strip().lower()


def dictations():
    con = sqlite3.connect(f"file:{WISPR}?mode=ro", uri=True)
    rows = con.execute("""select timestamp, app, coalesce(nullif(pastedText, ''), nullif(editedText, ''), formattedText, asrText),
                          detectedLanguage from History where coalesce(pastedText, editedText, formattedText, asrText) <> ''""").fetchall()
    out = []
    for ts, app, text, lang in rows:
        t = datetime.strptime(ts[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        out.append(dict(t=t, app=app or "", text=norm(text), lang=lang or ""))
    return sorted(out, key=lambda d: d["t"])


def shape(s):
    n = max(len(s), 1)
    thai = sum(1 for c in s if "฀" <= c <= "๿")
    return [np.log1p(len(s)), thai / n, sum(c in ".,;:!?" for c in s) / n, s.count("`") / n,
            float(bool(re.search(r"[/~][\w.-]+/", s))), float(bool(re.search(r"\n", s))),
            sum(c.isupper() for c in s) / n, sum(c.isdigit() for c in s) / n, float(bool(re.search(r"[{}()\[\]=<>]", s)))]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", help="write labelled.jsonl and all.jsonl here (for stt_train.py on gb300) and stop")
    ap.add_argument("--scores", help="scores.jsonl from stt_train.py: use the fine-tuned model's scores instead of the n-gram model")
    args = ap.parse_args()
    t0 = time.time()
    con = common.connect(HOME / ".relic-scan", HOME / ".relic-extract")
    prompts = con.execute("""select msgkey, any_value(x.ts_th) ts, any_value(coalesce(ses.cat, x.cat)) cat, any_value(x.tier) tier,
                             string_agg(x.text, '' order by x.chunk) AS text
                             from x left join ses using (sid) where x.role = 'user' and x.ts_th <> '' group by msgkey""").fetchall()
    P = [dict(key=k, t=datetime.fromisoformat(ts), cat=c, tier=tr, raw=tx, text=norm(tx)) for k, ts, c, tr, tx in prompts]
    P.sort(key=lambda p: p["t"])
    D = dictations()
    print(f"stt: {len(P):,} prompts · {len(D):,} Wispr dictations {D[0]['t']:%Y-%m-%d} .. {D[-1]['t']:%Y-%m-%d} · {time.time() - t0:.0f}s", flush=True)

    # 1. match each dictation to the prompt it went into: sent within 20 min after, dictated text inside the prompt
    times = np.array([p["t"].timestamp() for p in P])
    spoken, matched_d = {}, 0
    for d in D:
        if len(d["text"]) < 12:
            continue
        a, b = np.searchsorted(times, d["t"].timestamp() - 30), np.searchsorted(times, d["t"].timestamp() + 1200)
        best = max(((fuzz.partial_ratio(d["text"], P[i]["text"][: len(d["text"]) * 3 + 200]), i) for i in range(a, b)), default=(0, None))
        if best[0] >= 85:
            matched_d += 1
            p = P[best[1]]
            spoken.setdefault(p["key"], []).append(dict(app=d["app"], score=best[0]))
    print(f"stt: {matched_d:,} dictations found in {len(spoken):,} prompts", flush=True)

    # 2. negatives: main-session prompts while Wispr was in use (first..last dictation), no dictation behind them
    lo, hi = D[0]["t"], D[-1]["t"]
    lab = [(p, 1) for p in P if p["key"] in spoken] + \
          [(p, 0) for p in P if lo <= p["t"] <= hi and p["tier"] == "main" and p["key"] not in spoken and len(p["text"]) >= 12]
    texts, y = [p["text"][:2000] for p, _ in lab], np.array([v for _, v in lab])
    print(f"stt: labelled {y.sum():,} spoken / {(y == 0).sum():,} typed", flush=True)
    if args.export:
        d = Path(args.export).expanduser()
        d.mkdir(parents=True, exist_ok=True)
        os.chmod(d, 0o700)
        with open(d / "labelled.jsonl", "w") as f:
            for p, v in lab:
                f.write(json.dumps(dict(key=p["key"], ts=p["t"].isoformat(), y=int(v), text=p["raw"][:2000]), ensure_ascii=False) + "\n")
        with open(d / "all.jsonl", "w") as f:
            for p in P:
                if p["tier"] == "main" and len(p["text"]) >= 12:
                    f.write(json.dumps(dict(key=p["key"], text=p["raw"][:2000]), ensure_ascii=False) + "\n")
        for f in d.iterdir():
            f.chmod(0o600)
        print(f"stt: exported -> {d}")
        return

    # 3. model + cross-validation
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 4), min_df=3, max_features=200_000, sublinear_tf=True)
    X = hstack([vec.fit_transform(texts), csr_matrix(np.array([shape(p["raw"][:2000]) for p, _ in lab]))]).tocsr()
    clf = LogisticRegression(max_iter=3000, C=4.0, class_weight="balanced")
    cvp = cross_val_predict(clf, X, y, cv=StratifiedKFold(5, shuffle=True, random_state=7), method="predict_proba")[:, 1]
    ths = np.linspace(0.2, 0.95, 31)
    f1s = [precision_recall_fscore_support(y, cvp >= th, average="binary", zero_division=0)[2] for th in ths]
    th = float(ths[int(np.argmax(f1s))])
    pr, rc, f1, _ = precision_recall_fscore_support(y, cvp >= th, average="binary", zero_division=0)
    cv = dict(auc=round(roc_auc_score(y, cvp), 3), ap=round(average_precision_score(y, cvp), 3), threshold=round(th, 2),
              precision=round(pr, 3), recall=round(rc, 3), f1=round(f1, 3), spoken=int(y.sum()), typed=int((y == 0).sum()))
    print(f"stt: 5-fold CV {cv}", flush=True)
    clf.fit(X, y)

    # 4. score every main-session prompt of the year
    main_p = [p for p in P if p["tier"] == "main" and len(p["text"]) >= 12]
    score = np.zeros(len(main_p))
    if args.scores:  # the fine-tuned model's scores (stt_train.py) and its held-out threshold
        sp = Path(args.scores).expanduser()
        got = {r["key"]: r["score"] for r in map(json.loads, open(sp))}
        score = np.array([got.get(p["key"], 0.0) for p in main_p])
        tm = json.loads((sp.parent / "metrics.json").read_text())
        th, cv = tm["best"]["threshold"], tm["best"] | dict(model=tm["model"], baseline_same_split=tm["baseline"], ngram_cv_all=cv)
        print(f"stt: fine-tuned scores for {sum(p['key'] in got for p in main_p):,} prompts, threshold {th}", flush=True)
    else:
        for i in range(0, len(main_p), 20000):
            part = main_p[i: i + 20000]
            Xa = hstack([vec.transform([p["text"][:2000] for p in part]), csr_matrix(np.array([shape(p["raw"][:2000]) for p in part]))]).tocsr()
            score[i: i + len(part)] = clf.predict_proba(Xa)[:, 1]
    month, proj = defaultdict(lambda: [0, 0, 0]), defaultdict(lambda: [0, 0])
    for p, s in zip(main_p, score):
        m = p["t"].astimezone(BKK).strftime("%Y-%m")
        known = p["key"] in spoken
        month[m][0] += 1
        month[m][1] += int(s >= th)
        month[m][2] += int(known)
        proj[p["cat"]][0] += 1
        proj[p["cat"]][1] += int(s >= th)
    apps = Counter(x["app"] for v in spoken.values() for x in v)
    feats = vec.get_feature_names_out()
    w = clf.coef_[0][: len(feats)]
    top = lambda idx: [feats[i] for i in idx if feats[i].strip()][:25]
    order = np.argsort(score)[::-1]
    ex = lambda cond: [dict(ts=main_p[i]["t"].astimezone(BKK).strftime("%Y-%m-%d %H:%M"), cat=main_p[i]["cat"], score=round(float(score[i]), 2),
                            text=main_p[i]["raw"][:220]) for i in order if cond(i)][:12]
    res = dict(built=time.strftime("%Y-%m-%d %H:%M"), source="Wispr Flow History (read-only)", dictations=len(D),
               dictations_matched=matched_d, prompts_spoken_known=len(spoken), apps=apps.most_common(8), cv=cv,
               prompts_scored=len(main_p), predicted_spoken=int((score >= th).sum()),
               months=[dict(m=m, prompts=v[0], spoken=v[1], known=v[2]) for m, v in sorted(month.items())],
               projects=sorted([dict(cat=c, prompts=v[0], spoken=v[1]) for c, v in proj.items() if v[0] >= 200], key=lambda r: -r["spoken"] / r["prompts"])[:20],
               words_spoken=top(np.argsort(w)[::-1]), words_typed=top(np.argsort(w)),
               examples_spoken=ex(lambda i: main_p[i]["key"] not in spoken and score[i] >= th),
               examples_known=[dict(ts=p["t"].astimezone(BKK).strftime("%Y-%m-%d %H:%M"), cat=p["cat"], text=p["raw"][:220]) for p in P if p["key"] in spoken][-12:])
    dst = OUT / "stt.json"
    dst.write_text(json.dumps(res, ensure_ascii=False, indent=1))
    dst.chmod(0o600)
    print(f"stt: {res['predicted_spoken']:,} of {len(main_p):,} prompts read as spoken (threshold {th:.2f}) · {time.time() - t0:.0f}s -> {dst}")
    for r in res["months"]:
        print(f"  {r['m']}  {r['prompts']:>6,} prompts  {r['spoken']:>5,} spoken ({r['spoken'] / max(r['prompts'], 1):.0%})  known {r['known']}")


if __name__ == "__main__":
    main()
