"""config.py — ~/.arra/relic.toml: one file for the whole chain, one nested table per tool.

  scan -> extract -> embed
  [scan]      relic-scan (today it still reads ~/.relic-scan/_config.json; reserved here)
  [extract]   this tool: which months and streams go in, how text is deduped and cut
  [embed]     relic-embed (next): model, gb300 / local runs, output

ARRA_CONFIG overrides the path; --config on the command line overrides both. A missing file or key falls back
to DEFAULTS, which are jsonl-truth embed/workflow.toml's choices as of 2026-10-22 (text only, content dedupe,
6000/400 chunks), so a machine with no config gets what the fleet already runs.
"""
import copy, hashlib, json, os, tomllib

PATH = os.environ.get("ARRA_CONFIG", "~/.arra/relic.toml")

DEFAULTS = {
    "extract": {
        "source": "~/.relic-scan",     # relic-scan's OUT: YYYY-MM.parquet + meta/sessions.parquet
        "out": "~/.relic-extract",     # <stream>/YYYY-MM.parquet
        "months": "changed",           # "changed" (source newer than the part) | "all" | ["2026-10", "2026-09"]
        "dedupe": {"key": "content"},  # "content": one row per message (msgkey) · "h": one per truth row
        "chunk": {"max_chars": 6000, "overlap": 400},
        "streams": {
            "text": {"enabled": True, "field": "text"},
            "thinking": {"enabled": False, "field": "text"},
            "tools": {"enabled": False, "field": "body", "kinds": ["call"], "max_chars": 2000},
        },
    },
}

# What a stream selects from the month rows (the s column); the stream's name picks it.
SELECT = {"text": "text", "thinking": "thinking", "tools": "tools"}

TEMPLATE = """# ~/.arra/relic.toml — the relic chain: scan -> extract -> embed. Each tool reads its own table.

[extract]
source = "~/.relic-scan"        # relic-scan's output: YYYY-MM.parquet + meta/sessions.parquet
out = "~/.relic-extract"        # <stream>/YYYY-MM.parquet, ready for relic-embed
months = "changed"              # "changed" | "all" | ["2026-10", "2026-09"]

[extract.dedupe]
key = "content"                 # "content": the same message rewritten with other bytes is embedded once
                                # "h": one row per truth row

[extract.chunk]
max_chars = 6000                # longer texts are cut, with this overlap
overlap = 400

[extract.streams.text]
enabled = true
field = "text"

[extract.streams.thinking]
enabled = false                 # Nat 2026-10-22: "just text no thinking for the embed"
field = "text"

[extract.streams.tools]
enabled = false                 # Nat 2026-10-22: "next time we skip tools"
field = "body"
kinds = ["call"]                # "call" = tool inputs; add "result" for outputs
max_chars = 2000                # a tool body is cut here, not chunked

# [embed]                       # relic-embed (next): model, gb300 / local runs, output
"""


def _merge(base, over):
    out = copy.deepcopy(base)
    for k, v in over.items():
        out[k] = _merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def load(path=None):
    """The [extract] table, merged over DEFAULTS, with ~ expanded. Returns (config, path, existed)."""
    p = os.path.expanduser(path or PATH)
    raw = {}
    if os.path.exists(p):
        with open(p, "rb") as f:
            raw = tomllib.load(f)
    cfg = _merge(DEFAULTS, raw)["extract"]
    for k in ("source", "out"):
        cfg[k] = os.path.expanduser(cfg[k])
    bad = [s for s in cfg["streams"] if s not in SELECT]
    if bad:
        raise SystemExit(f"✗ {p}: unknown stream(s) {', '.join(bad)} (known: {', '.join(SELECT)})")
    if cfg["dedupe"]["key"] not in ("content", "h"):
        raise SystemExit(f"✗ {p}: [extract.dedupe] key must be \"content\" or \"h\"")
    c = cfg["chunk"]
    if not 0 <= c["overlap"] < c["max_chars"]:
        raise SystemExit(f"✗ {p}: [extract.chunk] needs 0 <= overlap < max_chars")
    return cfg, p, os.path.exists(p)


def stream_hash(cfg, name):
    """What a part of this stream depends on, besides its source month: change it and every month is cut again."""
    deps = {"stream": cfg["streams"][name], "dedupe": cfg["dedupe"], "chunk": cfg["chunk"]}
    return hashlib.blake2b(json.dumps(deps, sort_keys=True).encode(), digest_size=8).hexdigest()


def init(path=None):
    p = os.path.expanduser(path or PATH)
    if os.path.exists(p):
        return p, False
    os.makedirs(os.path.dirname(p), mode=0o700, exist_ok=True)
    with open(p, "w") as f:
        f.write(TEMPLATE)
    return p, True
