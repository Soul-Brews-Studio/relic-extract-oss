# relic-extract formats: everything read and written

As of 2026-10-27, binary `relic-extract` 26.10.23-alpha (Rust), `report/` scripts (Python). Paths are on m5.
There is no format version number. A stream's settings are tracked by the hash in `<stream>/_extract.json`, and
parts get re-cut when it changes. Nothing listed here is planned to change.

## Who reads what

| Output | relic5 (hub) | Only relic-extract |
|---|---|---|
| `<stream>/YYYY-MM.parquet` (text, thinking, tools) | MUST: rsynced to hub `parts/<machine>/`, the whole index | |
| `vault/notes.parquet` | MUST: pushed with the parts | |
| `<stream>/_extract.json` | | yes (decides a re-cut) |
| `relic-extract status --json` (stdout) | MUST: relic5-import SYNC verdict | |
| `report/readings.jsonl` | NICE: hub serves `/api/readings` from `readings/<machine>.jsonl` (rsync from us, by hand) | |
| `report/readings-cache-*.json`, `readings-<unit>-<lane>.json`, `days*.json`, `blocks*.json` | | yes (report page, book) |
| `report/` page + `report-share/`, `report-public/` | no (published at relic.buildwithoracle.com/report) | yes |
| `lanes/` (scripts, state) | no | yes |
| `bk-*` (backups) | no | yes |

## Inputs (all from relic-scan, read-only, never written)

| Path | Type | Columns or fields used | Notes |
|---|---|---|---|
| `~/.relic-scan/YYYY-MM.parquet`, `undated.parquet` | parquet, one per month | `s, h, idx, engine, role, type, sid, ts, ts_th, text, kind, call_id, name, body, err` | relic-scan alpha.753 added `model, msg_id, out_tokens, in_tokens, cache_read, cache_write` (we don't read them yet; old months NULL). A month is re-cut when its file mtime changes |
| `~/.relic-scan/meta/sessions.parquet` | parquet | `sid, tier, cat, probe` (tier = main) | gives `cat` and `probe` per row |
| `~/.relic-scan/meta/vaults.json`, `meta/vaults/<id>.json` | json | vault `id, name, kind, path`; notes per vault | `report/vaults.py`; written atomically since relic-scan alpha.753 (#40 closed) |

## Parts: `~/.relic-extract/{text,thinking,tools}/YYYY-MM.parquet` (+ `undated.parquet`)

Parquet (zstd), one file per stream per month. The whole file is rewritten when the month is re-cut, never appended.
Each row is one chunk to embed. Rows are ordered by `ts, msgkey`.

| Column | Type | Meaning |
|---|---|---|
| `key` | VARCHAR | blake2b-128 hex of the cleaned chunk text: the embedding cache key (`hashlib.blake2b(text, digest_size=16)`) |
| `msgkey` | VARCHAR | md5 hex of DuckDB `to_json([...])`, byte-identical to agent-relic-v4 `sql.py MSGKEY`. text/thinking: `[s, role, ts, text]` of the raw row; tools: `[s, ts, idx, kind, call_id, name, body, err]` |
| `chunk` | BIGINT | 0, 1, … within the message (text/thinking cut at max_chars with overlap; tools body cut at max_chars) |
| `h`, `n_h` | BIGINT | source row hash of the kept copy (smallest `h, idx`); n_h = how many scan rows held this message |
| `month` | VARCHAR | `YYYY-MM` (Bangkok) |
| `sid` | VARCHAR | session id |
| `ts`, `ts_th` | VARCHAR | ISO UTC, ISO +07:00 |
| `engine` | VARCHAR | `claude` / `codex` |
| `role`, `type`, `kind`, `name` | VARCHAR | from scan (`kind`/`name` set for tools) |
| `cat` | VARCHAR | session category from sessions.parquet, `(no session)` when none |
| `probe` | BOOLEAN | session is a probe |
| `text` | VARCHAR | the cleaned chunk (system-reminder stripped, notification tags dropped) |
| `src_agent`, `src_path`, `src_line` | VARCHAR, VARCHAR, BIGINT | which machine, transcript file and line it came from |

Dedupe is on msgkey: one message is kept once, from the smallest `(h, idx)`, so the same message in two homes is one row.
The same chunk text in different messages gives the same `key` and different `msgkey`s.

Example row (text, trimmed): `key c85625e4…5081 · msgkey 33c9e3d9…a7fd · chunk 0 · month 2026-10 · ts 2026-09-30T17:07:12.330Z
· engine claude · role user · cat odin-oracle · probe false · text "/huginn — write the hourly digest…" · src_agent beta@m5 · src_line 3`

`<stream>/_extract.json`: `{"hash": "<blake2b-8 of sorted settings JSON>", "settings": {...}}`. The parts get re-cut when the hash changes.
Written by `relic-extract run` only, by hand (relic5-import runs it). It holds `~/.relic-extract/.lock` while it writes.

## Vault notes: `~/.relic-extract/vault/notes.parquet`

Parquet, one file, fully rewritten each run (`report/vaults.py`; the old file is kept if meta changed mid-read, exit 4,
or fewer than `--min-notes`, exit 3). Notes are cut like text: 6000 chars, 400 overlap, secrets redacted.

Columns: `key` (blake2b-128 of chunk), `msgkey` (md5 of `vault path \0 note path`), `chunk`, `month`, `sid` = `vault:<id>`,
`ts`/`ts_th` = note mtime, `engine` = `vault`, `role` = `type` = `note`, `kind` = "", `name` = section, `cat` = vault's oracle,
`probe` false, `text`, `vault` (vault kind), `path` (note path in vault), `section`, `title`.

Ran hourly at :15 from a herdr pane until m5's reboot on 2026-10-26. Last written 2026-10-26 05:16. Nothing runs it now.

## `relic-extract status --json` (stdout)

`{"out": "<dir>", "streams": {"text"|"thinking"|"tools": {"enabled", "months", "chunks", "distinct", "newest"}}}`.
An unreadable stream carries `"error"` instead.

## Readings: `~/.relic-extract/report/readings.jsonl`

JSON lines, **append-only**: one line per model answer, never rewritten. A window can appear many times: once per model,
and again when its prompt changed (new scan data) or a lane re-read it. **The newest `at` per `(unit, model)` wins.**
58,961 lines, 15,554 `(unit, model)` pairs with more than one line (2026-10-26).

| Field | Type | Meaning |
|---|---|---|
| `at` | string | when answered, `%Y-%m-%dT%H:%M:%S%z` (+0700) |
| `unit` | string | the window (shapes below) |
| `model` | string | the model and lane, recorded on every row |
| `prompt` | string | first 12 hex chars of md5(prompt text): same value = same input |
| `secs` | number | seconds for the answer |
| `answer` | object | `type` (build/fix/ops/research/writing/planning/mixed), `focus`, `distraction`, `work`, `agents`, `pattern`, `en`, `th`; deep lanes add `friction`, `lesson`; plus `json_ok`, `think`, `secs` |

`unit` shapes. They come from three scripts and are not uniform:

| Script | Grain | `unit` example | `model` example |
|---|---|---|---|
| `days.py` | day | `2025-09-21` | `qwen3:14b` (= gb300) |
| `blocks.py` | 3h | `2025-09-19 15` (start hour) | `qwen3:14b` |
| `blocks.py --hours 1` | 1h | `2025-09-19 15-1h` | `qwen3:14b` |
| `readings.py` | day / 3h / 1h / 15m | `2025-10-24 day`, `2025-09-19 21:00-24:00 3h`, `2025-09-19 21:00-22:00 1h`, `2025-09-19 15:00-15:15 15m` | `zai:glm-5.3`, `zai:glm-5.3 deep`, `gb300:qwen3:14b deep` |

Lines from the 2026-10-24 five-model trial (200 3h windows each): `gb300:qwen3:14b`, `gb300:qwen3:30b-a3b`,
`gb300:nemotron-bolt-e4b:q8`, `gb300:typhoon2.1-gemma3-12b:latest`. For an import, skip these.

fm lane (2026-10-27): `model` = `fm:system` / `fm:system deep`, Apple's on-device model through macOS 27 `fm respond
--schema report/fm-reading[-deep].schema.json` (always valid JSON). Each answer also carries `host` (the Mac that
answered: `local` or an ssh host). It runs one request at a time per Mac, about 430 windows an hour on m5.

Models: gb300 = korn's ollama on gb300 over an ssh tunnel (127.0.0.1:11447), one request at a time. This is not
nazt's vLLM. z.ai = glm-5.3 over z.ai's Anthropic-compatible API.

Run by hand in herdr panes. gb300 finished 2026-10-25 19:26. The z.ai lane was stopped 2026-10-26 04:4x and nothing runs now.
The windows cover scan data up to 2026-10-25 16:30. "15m" is the window size, not how often anything runs.

Caches: `report/readings-cache-<unit>-<lane>.json` = `{label: {h: md5(model+prompt), …answer…}}`. One entry per
window, replaced when the prompt changes. A rerun reads only windows whose `h` changed. `readings-<unit>-<lane>.json`
is the finished list for the page.

## Report: `report/`, `report-share/`, `report-public/`

`report-share/` = the published share view: `index.html`, `index.md`, `llms.txt`, `report.json`, `monthly.parquet`
(`month, metric, series, value`). It carries no quoted text and no session ids. `report-public/` is the older public cut.
`just publish` rewrites them, by hand. No other agent reads them.
