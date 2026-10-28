"""Compare models on the same windows: which type each gives, how often they agree, speed, and answers that parse.

    uv run python report/trial.py --unit 3h day-or-3h-cache-tags...
    uv run python report/trial.py --unit 3h zai-glm-5.3 gb300-qwen3_14b gb300-nemotron-bolt-e4b_q8

Reads ~/.relic-extract/report/readings-cache-<unit>-<tag>.json for each tag and keeps the windows every tag has read.
Prints counts only (no reading text), so the output may go into a capture.
"""

import argparse
import json
import statistics
from collections import Counter
from itertools import combinations
from pathlib import Path

OUT = Path.home() / ".relic-extract" / "report"
TYPES = {"build", "fix", "research", "ops", "writing", "planning", "mixed"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", default="3h")
    ap.add_argument("tags", nargs="+")
    a = ap.parse_args()
    C = {t: json.loads((OUT / f"readings-cache-{a.unit}-{t}.json").read_text()) for t in a.tags}
    both = sorted(set.intersection(*(set(c) for c in C.values())))
    print(f"trial {a.unit}: {len(both):,} windows read by all {len(a.tags)} ({', '.join(f'{t} {len(c):,}' for t, c in C.items())})")
    for t, c in C.items():
        rs = [c[w] for w in both]
        secs = [r["secs"] for r in rs if r.get("secs") is not None]
        ty = Counter(r.get("type") for r in rs)
        th = sum(bool((r.get("th") or "").strip()) for r in rs)
        print(f"  {t:<34} json ok {sum(bool(r.get('json_ok')) for r in rs) / len(rs):4.0%} · type ok {sum(r.get('type') in TYPES for r in rs) / len(rs):4.0%} · median {statistics.median(secs) if secs else 0:5.1f} s"
              f" · Thai {th / len(rs):4.0%} · en {statistics.median(len(r.get('en') or '') for r in rs):4.0f} chars · "
              + " ".join(f"{k} {v / len(rs):.0%}" for k, v in ty.most_common(6)))
    for x, y in combinations(a.tags, 2):
        same = sum(C[x][w].get("type") == C[y][w].get("type") for w in both)
        pair = Counter((C[x][w].get("type"), C[y][w].get("type")) for w in both if C[x][w].get("type") != C[y][w].get("type"))
        print(f"  agree {x} / {y}: {same / len(both):.0%} ({same} of {len(both)}) · most common splits: "
              + ", ".join(f"{p[0]}/{p[1]} {n}" for p, n in pair.most_common(3)))


if __name__ == "__main__":
    main()
