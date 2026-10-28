#!/bin/sh
# The proof after any change to the cut: both runs write to a private scratch dir, ~/.relic-extract is only read.
#   raw      `run --raw` must equal RAW_REF (the Python reference's cut)       default ~/.relic-extract-parity/out
#   cleaned  `run` (the config's clean step) must equal CLEAN_REF               default ~/.relic-extract-parity/clean-ref
#   SOURCE   the relic-scan folder both cut from (freeze one with `cp -Rc ~/.relic-scan DIR`, so the live index
#            growing between runs is not mistaken for a difference)          default ~/.relic-extract-parity/scan-frozen
# Every row and every column; prints time and peak memory of each run.
set -u
D=${PARITY_DIR:-$HOME/.relic-extract-parity}
BIN=${BIN:-target/release/relic-extract}
RAW_REF=${RAW_REF:-$D/out/text}
CLEAN_REF=${CLEAN_REF:-$D/clean-ref}
SOURCE=${SOURCE:-$D/scan-frozen}
mkdir -p -m 700 "$D"
for mode in raw cleaned; do
  sed -e "s|^out = .*|out = \"$D/verify-$mode\"|" -e "s|^source = .*|source = \"$SOURCE\"|" "${ARRA_CONFIG:-$HOME/.arra/relic.toml}" > "$D/verify-$mode.toml"
  flag=""; [ "$mode" = raw ] && flag="--raw"; ref=$CLEAN_REF; [ "$mode" = raw ] && ref=$RAW_REF
  /usr/bin/time -l "$BIN" --config "$D/verify-$mode.toml" run --stream text --force $flag 2>&1 | rg "cut in|real|peak memory" | sed "s/^/$mode: /"
  NEW="read_parquet('$D/verify-$mode/text/*.parquet')"; OLD="read_parquet('$ref/*.parquet')"
  duckdb -noheader -list -c "select '$mode: rows ' || (select count(*) from $OLD) || ' vs ' || (select count(*) from $NEW)
    || ' · differ ' || (select count(*) from (select * from $OLD except all select * from $NEW))
    || ' / ' || (select count(*) from (select * from $NEW except all select * from $OLD))"
done
