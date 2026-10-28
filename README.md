# relic-extract

Step 2 of the relic chain: **scan -> extract -> embed**.

```
 relic-scan              relic-extract                         relic-embed (next)         relic4
 find + parse       ──►  pick streams, dedupe, cut        ──►  vectors              ──►  search
 ~/.relic-scan/          ~/.relic-extract/<stream>/            gb300 / local             CLI · API · MCP · web
   YYYY-MM.parquet         YYYY-MM.parquet
   meta/sessions.parquet
```

It reads the month files [relic-scan](https://github.com/Soul-Brews-Studio/relic4-scan) writes. Then it writes, for each stream that is on, one Parquet part per month: one row per chunk to embed. What goes in is set in one TOML file, `~/.arra/relic.toml`.

## Use

One binary, `relic-extract` (Rust, `src/*.rs`). No Python or DuckDB needed on the machine.

```sh
just install                        # build, then put relic-extract in ~/.local/bin
relic-extract init                  # write ~/.arra/relic.toml with the defaults (kept if it exists)
relic-extract run                   # only months whose source changed, and streams whose settings changed
relic-extract run --months 2026-10 --stream text --force
relic-extract status                # per stream: months, chunks, distinct texts, newest message
relic-extract config                # the settings in effect (the file merged over the defaults)
relic-extract analyse               # what a run would cut and what the clean step takes out; writes nothing
relic-extract analyse --months 2026-10 --what-if min_chars=24 --what-if chunk.max_chars=2000
relic-extract tui                   # the same report in the terminal; w tries a what-if live
```

`tui` has six views: 1 overview (totals now / what-if), 2 months, 3 clean rules with 20 samples each (the raw text
in full in the right pane, ← → next sample), 4 the messages that make the most chunks, 5 the most repeated texts,
6 chunks per repository. `w` types a what-if (`min_chars=24 ; max_chars=2000`), Enter recomputes, an empty one
clears it; ↑ ↓ choose, PgUp PgDn scroll, 1-6 or Tab switch, q quits. Nothing is written.

`analyse` runs the same select / clean / cut as `run`, in memory, and prints: totals (raw cut -> other roles ->
cleaned out -> kept, chunks, distinct texts), a table by month, each clean rule with its messages, characters and a
sample, what strip_tags / commands / redact did to the messages that stay, chunks per message and the biggest
messages, the most repeated texts (loops show up here), and chunks per repository. `--what-if KEY=VALUE`
(repeatable) adds a second column with the setting changed; a short key is looked up in `streams.<stream>.clean`,
then `streams.<stream>`, then `chunk`, then `[extract]`. Nothing is written.

`--config FILE` or `ARRA_CONFIG` points to another config.

Months are cut in parallel (`RELIC_EXTRACT_THREADS`, default one per core up to 8); a full run of 15 months takes
about 2 s on an M5. One run at a time per output folder (`OUT/.lock`). Parts and `_extract.json` are written
privately (0600) and atomically. A mistyped or wrongly typed key under `[extract]` is an error.

`just verify` cuts the raw and the cleaned stream from a frozen copy of the source and compares each with its
reference, every row and column (`scripts/verify.sh`). The crate is a library (`src/lib.rs`) with a thin CLI, so
relic-embed can use the same msgkey, key and cut.

The Python version (`src/relic_extract`, `uv run relic-extract …`) is kept as the reference: `just parity` cuts every
month with the binary into `~/.relic-extract-parity` and compares it with `~/.relic-extract` row by row, every column,
and the row order. Both write the same `_extract.json` hash, so either can continue after the other.

## ~/.arra/relic.toml

One file for the whole chain, with nested tables per tool. relic-extract reads `[extract]`. `[scan]` and `[embed]` are kept for relic-scan and relic-embed. A missing file or key falls back to the defaults. The defaults are jsonl-truth's `embed/workflow.toml` choices as of 2026-10-22, so a machine with no config gets what the fleet already runs.

```toml
[extract]
source = "~/.relic-scan"        # relic-scan's output
out = "~/.relic-extract"        # <stream>/YYYY-MM.parquet
months = "changed"              # "changed" | "all" | ["2026-10", "2026-09"]
sessions = "any"                # cat/probe from meta/sessions.parquet: "any" tier (main preferred) | "main" only

[extract.dedupe]
key = "content"                 # "content": a message rewritten with other bytes is embedded once · "h": every row

[extract.chunk]
max_chars = 6000
overlap = 400

[extract.streams.text]
enabled = true
field = "text"
roles = ["user", "assistant"]   # [] = every role

[extract.streams.text.clean]     # before cutting; msgkey stays on the raw row, key is on the cleaned chunk
enabled = true
strip_tags = ["system-reminder"]                 # <tag>…</tag> removed anywhere
commands = "args"               # slash commands: "args" -> "/goal <args>" (none: dropped) | "drop" | "keep"
drop_tags = ["task-notification", "subagent_notification", "goal_context", "hook_prompt", "local-command-stdout", …]
drop_prefixes = ["Stop hook feedback:", "Base directory for this skill:", "[Request interrupted by user", …]
drop_texts = ["(holding)", "Idle.", "Prompt is too long", …]   # the whole message: loop status chatter, echoes
drop_invisible = true           # nothing but whitespace / zero-width characters
min_chars = 0                   # v3 used 24; 0 = keep short messages
drop_over_chars = 1000000       # a pasted dump; 0 = keep every length
redact = true                   # WireGuard keys, Telegram / Discord tokens, Discord webhooks -> [kind:abcd…N]

[extract.streams.thinking]
enabled = false                 # Nat 2026-10-22: "just text no thinking for the embed"
field = "text"
roles = ["thinking"]

[extract.streams.tools]
enabled = false                 # Nat 2026-10-22: "next time we skip tools"
field = "body"
kinds = ["call"]                # add "result" for tool outputs
max_chars = 2000                # a tool body is cut here, not chunked
```

### After a run: the `after` hook

```toml
[extract]
after = "bin/relic5-push && ssh white '~/relic5/app/.venv/bin/relic5 index'"
```

Runs through `sh -c` once a run that cut at least one part has finished and released `OUT/.lock` (so a push that
takes the lock does not wait). Its env: `RELIC_EXTRACT_CUT` (the parts, comma separated, relative to OUT, e.g.
`text/2026-10.parquet,tools/2026-10.parquet`) and `RELIC_EXTRACT_OUT`. A failing command fails the run.
`relic-extract run --no-after` skips it. Every stream's `_extract.json` is written last and its `at` changes
every run: a watcher can use that as the finished marker instead.

## Output

`~/.relic-extract/<stream>/YYYY-MM.parquet`, one row per chunk:

| column | |
|---|---|
| `key` | blake2b-128 of the chunk text: the embedding cache key (same text, same vector, any month) |
| `msgkey`, `chunk` | the message (md5 of its content columns, as in agent-relic-v4) and the chunk number within it |
| `h`, `n_h` | one row holding the message (smallest `h`) and how many rows hold it |
| `month`, `sid`, `ts`, `ts_th`, `engine`, `role`, `type`, `kind`, `name` | from the month file |
| `cat`, `probe` | the session's repo and whether it is an automated probe, from relic-scan's `meta/sessions.parquet` |
| `text` | the chunk |
| `src_agent`, `src_path`, `src_line` | where the kept row was read: the first entry of relic-scan's `sources` ("<agent>:<path>:<line>"), NULL when none |

`<stream>/_extract.json` records the settings a stream was cut with. Change them and every month of that stream is cut again.

The cut and the keys are the same as agent-relic-v4's `split` (recipe `text-v1`) and jsonl-truth's `embed/export.py`. A chunk's `key` therefore finds a vector either of them already made.

## Report: month by month

`report/` turns the chain's output into one long, self-contained page: scan → extract → embed → topics → vector
space → report. It makes no network calls; every number comes from the data.

```
just embed          # relic4 embeds the text parts (gb300); vectors land in its cache, keyed by chunk key
just embed-streams  # the same for thinking and tools: one LanceDB table each, with an ICU index
just topics         # k-means on the prompt vectors -> ~/.relic-extract/report/topics.json, topic_of.parquet
just vspace         # oracles, what works, growth, lessons -> ~/.relic-extract/report/vspace.json
just vaults         # every ψ vault note as a stream -> ~/.relic-extract/vault/notes.parquet
just embed-streams vault   # ... embedded like the others (shared cache: a mirrored note is embedded once)
just focus          # maw and relic: features from their own vectors, arcs, lessons -> focus.json
just oracles        # oracles: born, for what, how long, used, whether they work -> oracles.json
just memory         # each oracle's vault against its own sessions, one vector space -> memory.json
just report         # DuckDB aggregates + the above -> ~/.relic-extract/report/index.html (+ report.json, monthly.parquet)
just report-public  # counts only: no repository, skill, example or lesson text -> report-public/
just asks           # example questions answered by the fused search -> asks.json
just search "QUERY" # ICU keywords + vector meaning over text, thinking, tools and the vaults, fused (RRF)
just publish        # the share view (every number, no quoted text) -> relic.buildwithoracle.com/report
```

Three views come out of one build: **private** (`report/`, quotes examples and lessons, stays local),
**share** (`report-share/`, every chart and name but no quoted passage and no machine-picked word; `just publish`
refuses to deploy if gitleaks finds anything) and **public** (`report-public/`, counts only).

The page reads top to bottom like a long document, or step by step like slides: Back and Next (and ← →) move one
section, a map on the right names every part, section and month. Its parts:

- **The whole year:** what the index holds, who writes the prompts (main sessions, agent to agent, harness
  text), agents spawning agents, engines, tools and errors, thinking saved vs signature only, the hour and weekday
  prompts come in, Thai vs English, conversation length, topics (per month, each month's main ones, a map),
  skills, repositories, new repositories, how long tools and sessions take, and the history-search tools.
- **The vector space:** which oracle does what (each repository's centre, its nearest others, its topic mix),
  oracle × topic, what works (whether the next prompt ships or pushes back, by topic, repository and month),
  growth (breadth, new vs repeated prompts, drift), and lessons learned per month (vector search).
- **Focus, maw and relic:** each family's repositories over time, its features (clusters of its own prompt vectors,
  named by hand), how each repository started and where it stands, its tools, skills, follow-ups and lessons.
- **The oracles:** how they are born (and with which skill), what for, how long they live, how they are used, and
  whether they work; then what the ψ vaults hold and whether each oracle's vault knows what it was asked.
- **Ask the index:** example questions answered by the fused search.
- **The months, one by one:** every month in full against the month before.

An overview card at the top lists every section's finding in one line. Words go through `t()` (`report/i18n.js`):
English by default, Thai on the language button or `?lang=th`. `monthly.parquet` holds the numbers as
(month, metric, series, value) for DuckDB. Topic names are hand-given in `report/topic-names.json`, keyed by a
topic's three top words; when a re-run moves the words, the name falls away rather than going stale.

## Docs

- `docs/SCAN-INPUT.md`: relic-scan's output, read from its source and checked against the data (10 rounds).
- `docs/LITERATURE.md`: what earlier relic generations (v1-v4, relic-lessons, relic4-scan) decided and measured about
  extraction, keys, chunking, storage, FTS/ICU and LanceDB.

## License

MIT, see [LICENSE](LICENSE).
