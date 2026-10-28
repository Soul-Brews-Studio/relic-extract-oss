"""extract.py — relic-scan month files -> OUT/<stream>/YYYY-MM.parquet, one row per chunk to embed.

Ported from agent-relic-v4 split.py (recipe text-v1) and jsonl-truth embed/export.py; the cut and the keys are
the same, so a chunk's key finds a vector already embedded by either.

Per stream (text, thinking, tools; each on or off in [extract.streams.*]):
  rows      s = <stream>, the field not empty (tools: kind in kinds)
  dedupe    "content": one row per msgkey — the same message rewritten with other bytes (a ferry changed cwd, a
            resume re-serialised it) is embedded once; n_h counts the rows holding it · "h": one per (h, idx)
  cut       text/thinking: max_chars with overlap (chunk = 0, 1, …) · tools: the body cut at the stream's max_chars

Columns per chunk:
  key       blake2b-128 of the chunk text: the embedding cache key (same text -> same vector, any month, any stream)
  msgkey    md5 of the message's content columns (agent-relic-v4 sql.py MSGKEY) · chunk
  h         one row holding it (smallest h) · n_h rows holding it
  month, sid, ts, ts_th, engine, role, type, kind, name, cat, probe (cat/probe from meta/sessions.parquet), text

A month is cut again when its source is newer than the part, or when the stream's settings changed
(OUT/<stream>/_extract.json keeps a hash of them).
"""
import glob, hashlib, json, os, re, time

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq

from . import config

# agent-relic-v4 src/relic4/sql.py — keep byte-identical: relic4 and relic-embed key on it
MSGKEY = """md5(CASE WHEN s = 'tools'
  THEN to_json([s, ts, CAST(idx AS VARCHAR), kind, call_id, name, body, CAST(err AS VARCHAR)])
  ELSE to_json([s, role, ts, text]) END)"""

MONTH = re.compile(r"^(\d{4}-\d{2}|undated)\.parquet$")
NAMES = ["key", "msgkey", "chunk", "h", "n_h", "month", "sid", "ts", "ts_th", "engine", "role", "type", "kind", "name",
         "cat", "probe", "text"]


def chunks(text, max_chars, overlap):
    """Same cut as jsonl-truth embed/export.py and relic4 split.py."""
    if len(text) <= max_chars:
        return [text]
    out, i, step = [], 0, max_chars - overlap
    while i < len(text):
        out.append(text[i:i + max_chars])
        i += step
    return out


def key(t):
    return hashlib.blake2b(t.encode("utf-8", "surrogatepass"), digest_size=16).hexdigest()


def months_in(source):
    """{month: path} for every month file relic-scan wrote (YYYY-MM.parquet, undated.parquet)."""
    out = {}
    for f in glob.glob(os.path.join(source, "*.parquet")):
        m = MONTH.match(os.path.basename(f))
        if m:
            out[m.group(1)] = f
    return dict(sorted(out.items()))


def _where(name, st):
    f = st["field"]
    w = f"s = '{config.SELECT[name]}' AND {f} IS NOT NULL AND length(trim({f})) > 0"
    if name == "tools":
        w += " AND kind IN (" + ",".join("'" + k.replace("'", "''") + "'" for k in st.get("kinds", ["call"])) + ")"
    return w


def cut_month(cfg, name, month, src, out, log=print):
    st, t0 = cfg["streams"][name], time.time()
    part = "msgkey" if cfg["dedupe"]["key"] == "content" else "h, idx"
    sessions = os.path.join(cfg["source"], "meta", "sessions.parquet")
    con = duckdb.connect()
    con.execute(f"CREATE TEMP VIEW m AS SELECT *, {MSGKEY} AS msgkey FROM read_parquet('{src}')")
    con.execute("CREATE TEMP VIEW s AS " + (
        f"SELECT sid, any_value(cat) AS cat, bool_or(probe) AS probe FROM read_parquet('{sessions}') "
        "WHERE tier = 'main' GROUP BY sid" if os.path.exists(sessions)
        else "SELECT NULL::VARCHAR AS sid, NULL::VARCHAR AS cat, NULL::BOOLEAN AS probe WHERE false"))
    rows = con.execute(f"""
      WITH x AS (
        SELECT msgkey, h, sid, ts, ts_th, engine, role, type, kind, name, {st['field']} AS body,
               count(*) OVER (PARTITION BY {part}) AS n_h,
               row_number() OVER (PARTITION BY {part} ORDER BY h, idx) AS rn
        FROM m WHERE {_where(name, st)})
      SELECT x.msgkey, x.h, x.n_h, x.sid, x.ts, x.ts_th, x.engine, x.role, x.type, x.kind, x.name,
             coalesce(s.cat, '(no session)') AS cat, coalesce(s.probe, false) AS probe, x.body
      FROM x LEFT JOIN s USING (sid)
      WHERE x.rn = 1 ORDER BY x.ts, x.msgkey""").fetchall()
    con.close()
    cols = {c: [] for c in NAMES}
    mc, ov = cfg["chunk"]["max_chars"], cfg["chunk"]["overlap"]
    for msgkey, h, n_h, sid, ts, ts_th, eng, role, typ, kind, tname, cat, probe, body in rows:
        pieces = [body[:st.get("max_chars", 2000)]] if name == "tools" else chunks(body, mc, ov)
        for ci, p in enumerate(pieces):
            for c, v in zip(NAMES, (key(p), msgkey, ci, h, n_h, month, sid, ts, ts_th, eng, role, typ, kind, tname,
                                    cat, probe, p)):
                cols[c].append(v)
    os.makedirs(os.path.dirname(out), mode=0o700, exist_ok=True)
    schema = pa.schema([(c, pa.int64() if c in ("chunk", "h", "n_h") else pa.bool_() if c == "probe" else pa.string())
                        for c in NAMES])
    pq.write_table(pa.table(cols, schema=schema), out + ".part", compression="zstd")
    os.replace(out + ".part", out)
    os.chmod(out, 0o600)
    n, u = len(cols["key"]), len(set(cols["key"]))
    log(f"extract {name} {month}: {len(rows):,} messages -> {n:,} chunks ({u:,} distinct texts) · {time.time() - t0:.1f}s")
    return dict(stream=name, month=month, messages=len(rows), chunks=n, distinct=u)


def run(cfg, months=None, force=False, streams=None, log=print):
    """Cut every enabled stream's months that need it. months: a set to limit to; force: cut them all."""
    src = months_in(cfg["source"])
    if not src:
        raise SystemExit(f"✗ no month files in {cfg['source']}\n  relic-scan run   (or set [extract] source)")
    want = cfg["months"] if months is None else sorted(months)
    if isinstance(want, list):
        missing = [m for m in want if m not in src]
        if missing:
            raise SystemExit(f"✗ no source for month(s) {', '.join(missing)} in {cfg['source']}")
    done = []
    for name, st in cfg["streams"].items():
        if not st.get("enabled") or (streams and name not in streams):
            continue
        sdir = os.path.join(cfg["out"], name)
        state_f = os.path.join(sdir, "_extract.json")
        state = json.load(open(state_f)) if os.path.exists(state_f) else {}
        h = config.stream_hash(cfg, name)
        stale = state.get("hash") != h
        if stale and state:
            log(f"extract {name}: settings changed since the last run, every month is cut again")
        for m, f in src.items():
            if isinstance(want, list) and m not in want:
                continue
            out = os.path.join(sdir, f"{m}.parquet")
            fresh = os.path.exists(out) and os.path.getmtime(out) >= os.path.getmtime(f)
            if want == "changed" and fresh and not stale and not force:
                continue
            done.append(cut_month(cfg, name, m, f, out, log))
        # a stale stream cut for a few months only keeps its old hash, so the rest are cut on the next full run
        if not (stale and isinstance(want, list)):
            os.makedirs(sdir, mode=0o700, exist_ok=True)
            with open(state_f + ".part", "w") as fh:
                json.dump({"hash": h, "settings": {"stream": st, "dedupe": cfg["dedupe"], "chunk": cfg["chunk"]},
                           "source": cfg["source"], "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, fh, indent=1)
            os.replace(state_f + ".part", state_f)
        n = sum(1 for d in done if d["stream"] == name)
        log(f"extract {name}: {n} month(s) cut -> {sdir}")
    return done
