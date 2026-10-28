"""Embed relic-extract's other streams (thinking, tools) with relic4's own embed step, one LanceDB table per stream.

    RELIC4_ROOT=~/relic4-fresh uv run --project ../agent-relic-v4 python report/embed_streams.py [thinking tools]

relic4's text-v2 recipe covers the text stream. This does the same for the others, with relic4's code so nothing
drifts: keys missing from relic4's vector cache (vec/_cache/<model>@<hash>) go to gb300 through
relic4.embed.remote_embed, then <stream>/*.parquet JOIN cache -> RELIC4_ROOT/vec/streams/<model>/<stream>.lance,
with an ICU full-text index on `text`, the same settings as relic4's. The cache is shared: a text already embedded
for any stream is not embedded again.
"""

import glob
import json
import os
import sys
import time

import duckdb
import lancedb
import pyarrow as pa
from lancedb.index import FTS
from relic4 import embed, paths

EXTRACT = os.path.expanduser("~/.relic-extract")


def one(stream, model=embed.MODEL, log=print):
    t0 = time.time()
    parts = sorted(glob.glob(os.path.join(EXTRACT, stream, "*.parquet")))
    if not parts:
        log(f"{stream}: no parts in {EXTRACT}/{stream} (enable the stream in ~/.arra/relic.toml, relic-extract run)")
        return None
    cdir = embed.cache_dir(model)
    os.makedirs(cdir, mode=0o700, exist_ok=True)
    shards = os.path.join(cdir, "*.parquet")
    con = duckdb.connect()
    plist = "[" + ",".join(f"'{p}'" for p in parts) + "]"
    con.execute(f"create view parts as select * from read_parquet({plist})")
    cache = lambda: f"select key, vector from read_parquet('{shards}')" if glob.glob(shards) else \
        "select null::varchar AS key, null::float[] AS vector where false"
    con.execute("create or replace view cache as " + cache())
    n_rows, n_keys = con.execute("select count(*), count(distinct key) from parts").fetchall()[0]
    missing = os.path.join(paths.ROOT, "vec", f"missing-{stream}-{embed.slug(model)}.parquet")
    os.makedirs(os.path.dirname(missing), mode=0o700, exist_ok=True)
    con.execute(f"""copy (select key, any_value(text) AS text from parts where key not in (select key from cache) group by key)
                    to '{missing}' (format parquet, compression zstd)""")
    n_miss = con.execute(f"select count(*) from '{missing}'").fetchall()[0][0]
    log(f"{stream}: {len(parts)} parts · {n_rows:,} chunks · {n_keys:,} distinct texts · in cache {n_keys - n_miss:,} · to embed {n_miss:,}")
    t_gpu = 0.0
    if n_miss:
        t = time.time()
        embed.remote_embed(missing, model, log)
        t_gpu = time.time() - t
        con.execute("create or replace view cache as " + cache())
    os.remove(missing)

    t = time.time()
    tab = con.sql("""select p.*, c.vector from parts p join (select key, any_value(vector) AS vector from cache
                     where key in (select key from parts) group by key) c using (key) order by p.ts, p.msgkey, p.chunk""").to_arrow_table()
    if tab.num_rows != n_rows:
        raise SystemExit(f"{stream}: {n_rows - tab.num_rows:,} chunks have no vector")
    vec = tab.column("vector").combine_chunks()
    dim = len(vec[0].as_py())
    tab = tab.set_column(tab.schema.get_field_index("vector"), "vector",
                         pa.FixedSizeListArray.from_arrays(vec.flatten().cast(pa.float32()), dim))
    out = os.path.join(paths.ROOT, "vec", "streams", embed.slug(model))
    os.makedirs(out, mode=0o700, exist_ok=True)
    db = lancedb.connect(out)
    tb = db.create_table(stream, tab, mode="overwrite")
    t_write = time.time() - t
    t = time.time()
    tb.create_index("text", config=FTS(**embed.FTS_SETTINGS), replace=True)
    t_fts = time.time() - t
    meta = dict(stream=stream, model=model, dim=dim, rows=tb.count_rows(), distinct=n_keys, embedded_now=n_miss,
                from_cache=n_keys - n_miss, embed_s=round(t_gpu, 1), write_s=round(t_write, 1), fts_s=round(t_fts, 1),
                total_s=round(time.time() - t0, 1), built_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"))
    json.dump(meta, open(os.path.join(out, f"{stream}._meta.json"), "w"), indent=1)
    log(f"{stream}: {tb.count_rows():,} rows -> {out}/{stream}.lance · write {t_write:.1f}s · ICU {t_fts:.1f}s · total {time.time() - t0:.1f}s")
    return meta


if __name__ == "__main__":
    for s in sys.argv[1:] or ["thinking", "tools"]:
        one(s)
