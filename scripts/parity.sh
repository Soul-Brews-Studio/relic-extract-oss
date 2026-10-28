#!/bin/sh
# Compare the Rust binary's parts with the Python version's, row by row, every column.
#   scripts/parity.sh [STREAM]      (default text) · needs duckdb and both outputs cut from the same ~/.relic-scan
# The Rust run writes to a private scratch dir; ~/.relic-extract (the Python output) is only read.
set -eu
STREAM=${1:-text}
PY=${PY_OUT:-$HOME/.relic-extract}
DIR=${PARITY_DIR:-$HOME/.relic-extract-parity}
mkdir -p -m 700 "$DIR"
sed -e "s|^out = .*|out = \"$DIR/out\"|" "${ARRA_CONFIG:-$HOME/.arra/relic.toml}" > "$DIR/relic.toml"
BIN=${BIN:-target/release/relic-extract}
/usr/bin/time -l "$BIN" --config "$DIR/relic.toml" run --stream "$STREAM" --force --raw 2>&1 | rg "cut in|real|peak memory"
COLS="key, msgkey, chunk, h, n_h, month, sid, ts, ts_th, engine, role, type, kind, name, cat, probe, text"
duckdb -c "
create view py as select $COLS from read_parquet('$PY/$STREAM/*.parquet');
create view rs as select $COLS from read_parquet('$DIR/out/$STREAM/*.parquet');
select (select count(*) from py) py_rows, (select count(*) from rs) rs_rows,
       (select count(*) from (select * from py except all select * from rs)) only_py,
       (select count(*) from (select * from rs except all select * from py)) only_rs;
select 'cat differs' what, count(*) n from py join rs using (msgkey, chunk) where py.cat is distinct from rs.cat
union all select 'other column differs', count(*) from py join rs using (msgkey, chunk)
  where (py.key, py.h, py.n_h, py.month, py.sid, py.ts, py.engine, py.role, py.text) is distinct from
        (rs.key, rs.h, rs.n_h, rs.month, rs.sid, rs.ts, rs.engine, rs.role, rs.text);
select count(*) n_months, count(*) filter (where p.l = r.l) same_row_order from
  (select month, list(msgkey||':'||chunk order by file_row_number) l from read_parquet('$PY/$STREAM/*.parquet', file_row_number=true) group by 1) p
  join
  (select month, list(msgkey||':'||chunk order by file_row_number) l from read_parquet('$DIR/out/$STREAM/*.parquet', file_row_number=true) group by 1) r using (month);"
