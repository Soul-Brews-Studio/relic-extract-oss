"""Example questions for the page: each answered by search.py's fused ICU + vector search over every stream.

    RELIC4_ROOT=~/relic4-fresh uv run --project ../agent-relic-v4 python report/asks.py   -> ~/.relic-extract/report/asks.json
"""

import json
import re
import time
from pathlib import Path

import search

ASKS = [
    ("Why did black.local keep rebooting?", {}),
    ("How does the embedding cache avoid embedding the same text twice?", {}),
    ("What did we decide about porting maw-js to Rust?", {}),
    ("Which tool calls failed most and why?", {"streams": ("tools", "thinking")}),
    ("What is the lesson about committing with secrets?", {}),
    ("ทำไมต้องใช้ tailscale แทน wireguard", {}),
]


def main():
    t0 = time.time()
    tabs = search.tables()
    model = {}

    def enc(q):
        if "m" not in model:
            from sentence_transformers import SentenceTransformer
            model["m"] = SentenceTransformer(search.MODEL, device="cpu")
        m = model["m"]
        kw = {"prompt_name": "query"} if "query" in (m.prompts or {}) else {}
        return m.encode([q], normalize_embeddings=True, **kw)[0]

    out = []
    for q, kw in ASKS:
        t = time.time()
        hits, lanes = search.search(q, n=6, tabs=tabs, enc=enc, **kw)
        out.append(dict(q=q, ms=int((time.time() - t) * 1000), streams=sorted({s for s, _ in lanes}),
                        hits=[dict(stream=h["stream"], ts=str(h.get("ts_th", ""))[:16], who=h.get("name") or h.get("role") or "",
                                   repo=h.get("cat"), both=h["both"], msgkey=h["msgkey"],
                                   text=re.sub(r"\s+", " ", h.get("text") or "")[:260]) for h in hits]))
        print(f"asks: {q[:50]} · {len(hits)} hits · {out[-1]['ms']} ms")
    dst = Path.home() / ".relic-extract" / "report" / "asks.json"
    dst.write_text(json.dumps(dict(built=time.strftime("%Y-%m-%d %H:%M"), asks=out), ensure_ascii=False, indent=1))
    dst.chmod(0o600)
    print(f"asks: {len(out)} questions in {time.time() - t0:.0f}s -> {dst}")


if __name__ == "__main__":
    main()
