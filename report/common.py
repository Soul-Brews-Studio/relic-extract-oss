"""The views every report step shares, over relic-scan's index and relic-extract's text parts.

  r    every row of the scan, with m = its Bangkok month
  s    meta/sessions.parquet;  ses: one row per sid, its main-tier entry first
  f    meta/files.parquet (path -> tier)
  mt   msgkey -> the tier of the file the message was read from
  x    relic-extract's text chunks, with that tier
"""

import duckdb


def connect(scan, extract):
    con = duckdb.connect()
    con.execute(f"create view r as select *, substr(ts_th, 1, 7) m from read_parquet('{scan}/*.parquet')")
    con.execute(f"create view s as select * from read_parquet('{scan}/meta/sessions.parquet')")
    # the session a row belongs to: its main-tier entry when there is one
    con.execute("""create table ses as select sid, arg_min(tier, case tier when 'main' then 0 when 'subagent' then 1 else 2 end) tier,
                   arg_min(cat, case tier when 'main' then 0 else 1 end) cat from s group by sid""")
    con.execute(f"create view f as select path, tier from read_parquet('{scan}/meta/files.parquet')")
    # each message's tier comes from the file it was read from (a subagent's rows carry its parent's session id);
    # msgkey is recomputed here exactly as relic-extract does, md5(to_json([s, role, ts, text]))
    con.execute("""create table mt as select md5(to_json([r.s, r.role, r.ts, r.text])) msgkey,
                   arg_min(f.tier, case f.tier when 'main' then 0 when 'subagent' then 1 else 2 end) tier
                   from r left join f on f.path = regexp_replace(r.sources[1], '^[^:]+:(.*):[0-9]+$', '\\1')
                   where r.s = 'text' group by 1""")
    con.execute(f"""create view x as select x.*, coalesce(mt.tier, 'main') tier
                    from read_parquet('{extract}/text/*.parquet') x left join mt using (msgkey)""")
    return con
