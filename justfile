# relic-extract — step 2 of scan -> extract -> embed. `just` lists the recipes.

default:
    @just --list --unsorted

# write ~/.arra/relic.toml with the defaults (kept if it exists)
init: build
    target/release/relic-extract init

# relic-scan months -> ~/.relic-extract/<stream>/YYYY-MM.parquet (only what changed)
run *ARGS: build
    target/release/relic-extract run {{ARGS}}

# per stream: months, chunks, distinct texts, newest message
status: build
    target/release/relic-extract status

# every test: the binary's and the Python reference's
test: test-rs
    uv run --with pytest -q pytest -q tests

# build the binary, then put relic-extract in ~/.local/bin
install: build
    mkdir -p ~/.local/bin
    cp target/release/relic-extract ~/.local/bin/relic-extract
    @echo "installed: ~/.local/bin/relic-extract ($(~/.local/bin/relic-extract --version))"

# the Rust binary (target/release/relic-extract), optimised
build:
    PATH=$HOME/.cargo/bin:$PATH cargo build --release --locked

# Rust tests
test-rs:
    PATH=$HOME/.cargo/bin:$PATH cargo test --release --locked

# Rust vs Python parts, every row and column (writes only ~/.relic-extract-parity)
parity STREAM="text": build
    scripts/parity.sh {{STREAM}}

# raw and cleaned cut vs their references, on a frozen source (scripts/verify.sh)
verify: build
    scripts/verify.sh

# embed the text parts with relic4 (agent-relic-v4) on gb300; RELIC4_ROOT picks the vector root
embed:
    cd ../agent-relic-v4 && RELIC4_RECIPE=text-v2 RELIC4_EMBED_WHERE=remote uv run relic4 embed text-v2

# thinking and tools embedded with relic4's embed step -> RELIC4_ROOT/vec/streams/<model>/<stream>.lance
embed-streams *STREAMS:
    uv run --project ../agent-relic-v4 python report/embed_streams.py {{STREAMS}}

# the vector space read for patterns -> ~/.relic-extract/report/vspace.json
vspace:
    uv run --project ../agent-relic-v4 python report/vspace.py

# ICU + vector over text, thinking and tools, fused
search *ARGS:
    uv run --project ../agent-relic-v4 python report/search.py {{ARGS}}

# ψ vault notes as a stream -> ~/.relic-extract/vault/notes.parquet (then: just embed-streams vault)
vaults:
    uv run python report/vaults.py

# example questions for the page, answered by the fused search -> ~/.relic-extract/report/asks.json
asks:
    uv run --project ../agent-relic-v4 python report/asks.py

# maw and relic: features, arcs, lessons -> ~/.relic-extract/report/focus.json
focus:
    uv run --project ../agent-relic-v4 --with scikit-learn --with pythainlp python report/focus.py

# did the work land: prompts against git history and vault notes -> ~/.relic-extract/report/work.json
work:
    uv run python report/work.py

# oracles: born, for what, how long, used, works -> ~/.relic-extract/report/oracles.json
oracles:
    uv run python report/oracles.py

# each oracle's vault against its own sessions -> ~/.relic-extract/report/memory.json
memory:
    uv run --project ../agent-relic-v4 python report/memory.py

# topics from the prompt vectors -> ~/.relic-extract/report/topics.json
topics *ARGS:
    uv run --with scikit-learn --with pythainlp python report/topics.py {{ARGS}}

# the month-by-month page -> ~/.relic-extract/report/index.html
report:
    uv run python report/status.py
    uv run python report/build.py

# counts only, no names or example text -> ~/.relic-extract/report-public/index.html
report-public:
    uv run python report/build.py --public

# publish the share view (no quoted text) to relic.buildwithoracle.com/report; refuses if gitleaks finds anything
publish:
    uv run python report/status.py
    uv run python report/build.py --share
    gitleaks dir ~/.relic-extract/report-share --no-banner --redact
    rm -rf report/site/public && mkdir -p report/site/public/report
    cp ~/.relic-extract/report-share/index.html report/site/public/report/index.html
    uv run python report/md.py ~/.relic-extract/report-share
    for f in index.md llms.txt report.json monthly.parquet; do cp ~/.relic-extract/report-share/$f report/site/public/report/; done
    gitleaks dir report/site/public --no-banner --redact
    ! rg -q '[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}' report/site/public
    cd report/site && npx -y wrangler@4 deploy
