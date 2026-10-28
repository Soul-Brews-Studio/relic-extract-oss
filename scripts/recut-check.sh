#!/bin/sh
# Re-cut ~/.relic-extract with the binary, cut the same index with the Python reference into the parity dir,
# and compare the two row by row (every column).
set -u
D=${PARITY_DIR:-$HOME/.relic-extract-parity}
mkdir -p -m 700 "$D"
sed -e "s|^out = .*|out = \"$D/py\"|" "${ARRA_CONFIG:-$HOME/.arra/relic.toml}" > "$D/py.toml"
relic-extract run --force 2>&1 | tail -1
uv run -q relic-extract run --config "$D/py.toml" --force 2>&1 | tail -1
RS="read_parquet('$HOME/.relic-extract/text/*.parquet')"
PY="read_parquet('$D/py/text/*.parquet')"
duckdb -c "select (select count(*) from $PY) py, (select count(*) from $RS) rs,
  (select count(*) from (select * from $PY except all select * from $RS)) only_py,
  (select count(*) from (select * from $RS except all select * from $PY)) only_rs"
relic-extract status
