"""Search every stream at once: ICU keywords (BM25) + vector meaning, fused into one list (reciprocal rank fusion).

    RELIC4_ROOT=~/relic4-fresh uv run --project ../agent-relic-v4 python report/search.py "QUERY" [-n 15]
        [--stream text,thinking,tools] [--month 2026-10] [--repo neo-oracle] [--role user] [--mode both|icu|vector] [--json]

Tables: relic4's text-v2 (vec/text-v2/<model>/text.lance) and embed_streams.py's thinking and tools
(vec/streams/<model>/<stream>.lance). Each lane returns its top 4n per stream; a hit's fused score is
sum(1 / (60 + rank)) over the lanes and streams that found it, so a passage both lanes agree on rises.
"""

import argparse
import json
import os
import time
from pathlib import Path

import lancedb

ROOT = Path(os.environ.get("RELIC4_ROOT", Path.home() / "relic4-fresh")).expanduser()
MODEL = "unsloth/embeddinggemma-300m"
SLUG = MODEL.replace("/", "--")
COLS = ["key", "msgkey", "chunk", "month", "sid", "ts_th", "engine", "role", "name", "cat", "text"]


def tables():
    out = {}
    t = ROOT / "vec" / "text-v2" / SLUG
    if (t / "text.lance").exists():
        out["text"] = lancedb.connect(str(t)).open_table("text")
    s = ROOT / "vec" / "streams" / SLUG
    for name in ("thinking", "tools", "vault"):
        if (s / f"{name}.lance").exists():
            out[name] = lancedb.connect(str(s)).open_table(name)
    return out


def encode(q):
    from sentence_transformers import SentenceTransformer
    m = SentenceTransformer(MODEL, device="cpu")
    kw = {"prompt_name": "query"} if "query" in (m.prompts or {}) else {}
    return m.encode([q], normalize_embeddings=True, **kw)[0]


def q(v):
    return "'" + str(v).replace("'", "''") + "'"


def search(query, n=15, streams=("text", "thinking", "tools", "vault"), month=None, repo=None, role=None, mode="both", tabs=None, enc=None):
    """Fused hits for one query: [{stream, msgkey, ts_th, role, name, cat, text, score, lanes}, ...]."""
    tabs = {k: v for k, v in (tabs or tables()).items() if k in streams}
    conds = ["probe = false"] + [f"{c} = {q(v)}" for c, v in (("month", month), ("cat", repo), ("role", role)) if v]
    where = " AND ".join(conds)
    qv = (enc or encode)(query) if mode != "icu" else None
    fused, lanes = {}, {}
    for stream, tb in tabs.items():
        cols = [c for c in COLS if c in tb.schema.names]
        for lane in (("icu", "vector") if mode == "both" else (mode,)):
            try:
                qb = tb.search(query, query_type="fts") if lane == "icu" else tb.search(qv)
                rows = qb.where(where, prefilter=True).limit(n * 4).select(cols + ["_score" if lane == "icu" else "_distance"]).to_list()
            except Exception as ex:
                print(f"  {stream}/{lane}: {ex.__class__.__name__}: {str(ex)[:120]}")
                continue
            seen, rank = set(), 0
            for r in rows:
                if r["msgkey"] in seen:
                    continue
                seen.add(r["msgkey"])
                rank += 1
                hit = fused.setdefault((stream, r["msgkey"]), dict(r, stream=stream, score=0.0, lanes=[]))
                hit["score"] += 1 / (60 + rank)
                hit["lanes"].append(f"{lane}#{rank}")
                lanes[(stream, lane)] = lanes.get((stream, lane), 0) + 1
    hits = sorted(fused.values(), key=lambda h: -h["score"])[:n]
    for h in hits:
        h.pop("_score", None)
        h.pop("_distance", None)
        h["both"] = len({x.split("#")[0] for x in h["lanes"]}) == 2
    return hits, lanes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("query")
    ap.add_argument("-n", type=int, default=15)
    ap.add_argument("--stream", default="text,thinking,tools,vault")
    ap.add_argument("--month")
    ap.add_argument("--repo")
    ap.add_argument("--role")
    ap.add_argument("--mode", default="both", choices=["both", "icu", "vector"])
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    t0 = time.time()
    hits, lanes = search(a.query, a.n, tuple(a.stream.split(",")), a.month, a.repo, a.role, a.mode)
    if a.json:
        print(json.dumps(dict(q=a.query, took_ms=int((time.time() - t0) * 1000), hits=[
            {k: v for k, v in h.items() if k != "vector"} for h in hits]), ensure_ascii=False, indent=1, default=str))
        return
    print(f"{int((time.time() - t0) * 1000)} ms · {', '.join(f'{s}/{l} {n}' for (s, l), n in lanes.items())} · "
          f"'=' both lanes · read one: RELIC4_ROOT={ROOT} relic4 show <msgkey>")
    for h in hits:
        both = "=" if h["both"] else " "
        who = h.get("name") or h.get("role") or ""
        text = " ".join((h.get("text") or "").split())[:130]
        print(f"{both} {h['score']:.4f}  {h['stream']:<8} {h.get('ts_th', '')[:16]}  {who[:10]:<10} {str(h.get('cat'))[:22]:<22} "
              f"{h['msgkey'][:12]}  {text}")


if __name__ == "__main__":
    main()
