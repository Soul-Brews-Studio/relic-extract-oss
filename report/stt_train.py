"""Fine-tune a spoken-vs-typed classifier on gb300 (GPU): BAAI/bge-m3 (multilingual, Thai) + a classification head.

    python stt_train.py --data ~/stt-train        # labelled.jsonl + all.jsonl from `stt.py --export`
    python stt_train.py --data ~/stt-train --score-only   # the kept model/ scores a new all.jsonl (no training)

Labels are Wispr Flow's: 263 spoken prompts against ~52k typed ones from the same weeks. The typed side is cut to
--neg-per-pos x the spoken count (random, fixed seed) and the loss weights the spoken side up to match. The split is
stratified random 80/20; the held-out 20% is scored for the fine-tuned model AND for the char n-gram baseline
(stt.py's features, fitted on the same 80%), so the two compare on one table at the same base rate.
-> metrics.json, scores.jsonl (key, score) for every prompt in all.jsonl, model/ (the fine-tuned weights).
"""

import argparse
import json
import random
import time
from pathlib import Path

import numpy as np
import torch
from sklearn.metrics import average_precision_score, precision_recall_fscore_support, roc_auc_score
from torch.utils.data import DataLoader
from transformers import AutoModelForSequenceClassification, AutoTokenizer


def load(p):
    return [json.loads(line) for line in open(p)]


def batches(tok, texts, bs, maxlen):
    for i in range(0, len(texts), bs):
        yield tok(texts[i: i + bs], truncation=True, max_length=maxlen, padding=True, return_tensors="pt")


@torch.no_grad()
def predict(model, tok, texts, bs=256, maxlen=256):
    model.eval()
    out = []
    for b in batches(tok, texts, bs, maxlen):
        b = {k: v.cuda() for k, v in b.items()}
        with torch.autocast("cuda", dtype=torch.bfloat16):
            out.append(torch.softmax(model(**b).logits.float(), -1)[:, 1].cpu().numpy())
    return np.concatenate(out) if out else np.zeros(0)


def metrics(y, p):
    ths = np.linspace(0.05, 0.95, 37)
    f1 = [precision_recall_fscore_support(y, p >= t, average="binary", zero_division=0)[2] for t in ths]
    t = float(ths[int(np.argmax(f1))])
    pr, rc, f, _ = precision_recall_fscore_support(y, p >= t, average="binary", zero_division=0)
    return dict(auc=round(roc_auc_score(y, p), 3), ap=round(average_precision_score(y, p), 3), threshold=round(t, 2),
                precision=round(pr, 3), recall=round(rc, 3), f1=round(f, 3), n=len(y), spoken=int(sum(y)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=str(Path.home() / "stt-train"))
    ap.add_argument("--model", default="BAAI/bge-m3")
    ap.add_argument("--neg-per-pos", type=int, default=30)
    ap.add_argument("--epochs", type=int, default=4)
    ap.add_argument("--bs", type=int, default=32)
    ap.add_argument("--lr", type=float, default=2e-5)
    ap.add_argument("--maxlen", type=int, default=256)
    ap.add_argument("--score-only", action="store_true", help="score all.jsonl with the kept model/, keep metrics.json")
    a = ap.parse_args()
    t0 = time.time()
    d = Path(a.data).expanduser()
    if a.score_only:
        tok = AutoTokenizer.from_pretrained(d / "model")
        model = AutoModelForSequenceClassification.from_pretrained(d / "model").cuda()
        allp = load(d / "all.jsonl")
        sc = predict(model, tok, [r["text"] for r in allp], maxlen=a.maxlen)
        with open(d / "scores.jsonl", "w") as f:
            for r, s in zip(allp, sc):
                f.write(json.dumps(dict(key=r["key"], score=round(float(s), 4))) + "\n")
        th = json.loads((d / "metrics.json").read_text())["best"]["threshold"]
        print(f"stt-train: scored {len(allp):,} prompts with the kept model · {int((sc >= th).sum()):,} at or above {th} · {time.time() - t0:.0f}s")
        return
    rng = random.Random(7)
    lab = load(d / "labelled.jsonl")
    pos, neg = [r for r in lab if r["y"] == 1], [r for r in lab if r["y"] == 0]
    rng.shuffle(pos), rng.shuffle(neg)
    neg = neg[: a.neg_per_pos * len(pos)]
    cut = lambda xs: (xs[: int(len(xs) * 0.8)], xs[int(len(xs) * 0.8):])
    (ptr, pte), (ntr, nte) = cut(pos), cut(neg)
    train, test = ptr + ntr, pte + nte
    rng.shuffle(train)
    print(f"stt-train: {len(train):,} train ({len(ptr)} spoken) · {len(test):,} held out ({len(pte)} spoken) · {a.model}", flush=True)

    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(1, 4), min_df=3, max_features=200_000, sublinear_tf=True)
    lr = LogisticRegression(max_iter=3000, C=4.0, class_weight="balanced").fit(vec.fit_transform([r["text"] for r in train]), [r["y"] for r in train])
    baseline = metrics(np.array([r["y"] for r in test]), lr.predict_proba(vec.transform([r["text"] for r in test]))[:, 1])
    print(f"  n-gram baseline, same split: {baseline}", flush=True)

    tok = AutoTokenizer.from_pretrained(a.model)
    model = AutoModelForSequenceClassification.from_pretrained(a.model, num_labels=2).cuda()
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
    w = torch.tensor([1.0, len(ntr) / max(len(ptr), 1)], device="cuda")
    steps = a.epochs * ((len(train) + a.bs - 1) // a.bs)
    sched = torch.optim.lr_scheduler.OneCycleLR(opt, max_lr=a.lr, total_steps=steps, pct_start=0.1)
    texts, ys = [r["text"] for r in train], torch.tensor([r["y"] for r in train])
    best = None
    for ep in range(a.epochs):
        model.train()
        order = torch.randperm(len(train)).tolist()
        tl = 0.0
        for i in range(0, len(order), a.bs):
            idx = order[i: i + a.bs]
            b = tok([texts[j] for j in idx], truncation=True, max_length=a.maxlen, padding=True, return_tensors="pt")
            b = {k: v.cuda() for k, v in b.items()}
            with torch.autocast("cuda", dtype=torch.bfloat16):
                logits = model(**b).logits.float()
            loss = torch.nn.functional.cross_entropy(logits, ys[idx].cuda(), weight=w)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step(), sched.step(), opt.zero_grad()
            tl += loss.item() * len(idx)
        p = predict(model, tok, [r["text"] for r in test], maxlen=a.maxlen)
        m = metrics(np.array([r["y"] for r in test]), p)
        print(f"  epoch {ep + 1}: loss {tl / len(train):.4f} · held out {m} · {time.time() - t0:.0f}s", flush=True)
        if best is None or m["ap"] > best["ap"]:
            best = m | dict(epoch=ep + 1)
            model.save_pretrained(d / "model"), tok.save_pretrained(d / "model")

    model = AutoModelForSequenceClassification.from_pretrained(d / "model").cuda()
    allp = load(d / "all.jsonl")
    sc = predict(model, tok, [r["text"] for r in allp], maxlen=a.maxlen)
    with open(d / "scores.jsonl", "w") as f:
        for r, s in zip(allp, sc):
            f.write(json.dumps(dict(key=r["key"], score=round(float(s), 4))) + "\n")
    res = dict(model=a.model, best=best, baseline=baseline, train=len(train), train_spoken=len(ptr), held_out=len(test), held_out_spoken=len(pte),
               neg_per_pos=a.neg_per_pos, epochs=a.epochs, scored=len(allp), predicted_spoken=int((sc >= best["threshold"]).sum()),
               secs=round(time.time() - t0))
    (d / "metrics.json").write_text(json.dumps(res, indent=1))
    print(f"stt-train: best {best} · {res['predicted_spoken']:,} of {len(allp):,} read as spoken · {res['secs']}s")


if __name__ == "__main__":
    main()
