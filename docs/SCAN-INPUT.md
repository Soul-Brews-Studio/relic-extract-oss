# What relic-extract reads: relic4-scan's output, checked (2026-10-23)

Source read: relic4-scan `alpha@6b8dc6b` — `src/parse/mod.rs`, `src/engine.rs`, `src/parse/merge.rs`,
`src/meta/{mod,agg,rules}.rs`, `src/scan.rs`, `src/state.rs`, `src/run.rs`. Data: `~/.relic-scan` on m5 (index of
2026-10-23 11:58 +07), and our own `~/.relic-extract/text` cut from it. Every number below was measured with DuckDB on
those files; nothing was changed.

## How a scan row is made (from the source)

- One JSONL line gives at most: one `text` row, one `thinking` row (both `idx = 0`), and one `tools` row per tool block
  (`idx` = position). All share `h` = blake2b-8 of the line bytes, `\n` included.
- `text` = only `text | input_text | output_text` blocks, joined with `\n`, Go-trimmed (Unicode White_Space only:
  U+200B and U+200E are kept). A string `content` is taken whole, so a user string carries everything pasted into it.
  `developer` / `system` roles give no text. `type = summary` rows carry the summary with an empty role.
- `thinking` = Claude/omp `thinking` blocks and Codex `reasoning` summaries, joined with `\n\n`. Empty blocks are dropped.
- `tools`: Claude `tool_use` / `tool_result`, Codex `function_call` / `custom_tool_call` / `*_output`, omp
  `toolCall` and `message.role = toolResult`. `body` = input/arguments re-serialised as JSON, or the result text.
- `sid` = the line's own session id, else the first one seen in the file. `ts` = the line's own time, else the file's
  last one, else empty (month `undated`). Month = Bangkok month of `ts`.
- Redaction runs on `text`, `thinking` and `body` at parse time (10 rules); `red` lists what fired.
- Copies are folded on `(s, h, idx)` only, first copy by `(file, line)` wins; `sources` lists every copy.
- Truth is append-only: a rewritten file is parsed again from 0 and its old rows stay.
- A month file is rewritten (atomic rename) only when this index produced rows for it. `meta/` is rebuilt every run.
- `meta/sessions.parquet`: one row per transcript file NAME and tier. `sid` = `file_sid` (first session id in the
  file) for `main`; the `agent-<id>` from the file name for `subagent` / `workflow-agent`.

## Ten checks

| # | question | measured | for relic-extract |
|---|---|---|---|
| 1 | what is in each `s` | text 1,359,349 · thinking 111,326 · tools 4,096,265 (5,566,940 rows) | `s` is the stream; `role`/`kind` say the rest |
| 2 | omp `role = toolResult` | 10,865 rows, **all** in `s = text`, all from the same line (`h`) as an omp `tools`/`result` row; 10,184 equal bodies, 292 differ by trim only | tool output inside the text stream: 12,016 of our chunks. Leave `role = 'toolResult'` out of `text` |
| 3 | does `msgkey` fold mirrored copies | user+assistant text: 1,348,399 rows = 1,348,399 lines -> 976,852 msgkeys (-27.6 %). Tool results: 2,034,629 -> 1,678,619 msgkeys | yes, `key = "content"` is right. The brief's 476,812 duplicate results measures 331,670 here by `(sid, call_id, ts, body)` |
| 4 | same text, same session, other `ts` | 18,832 groups / 211k rows. Top: `Stop hook feedback:` up to 21,483 times in one session; assistant text of only U+200E / U+200B up to 12,049 | not copies — hook loops. msgkey keeps them because `ts` differs |
| 5 | session join (`cat`) | 16,781 chunks get `(no session)`: omp sub-session files (`<ts>_<id>/Name.jsonl`, 66 sids) are tier `other`; 57 claude sids match no file's `file_sid` (a file holding a second session id) | take `cat` from the row's first source path (`meta/files.parquet` has `cat` for every file) instead of `sid` |
| 6 | `probe` | 0 probe sessions on m5 | column is a no-op here |
| 7 | tier of a text row | `sid` cannot tell it (sub-agents carry the parent's id); by source path: workflow-agent user text 34,341 rows / 184 M chars, subagent 15,758 / 33 M | if agent briefs should be separable, add `tier` from the source path |
| 8 | what user text holds | skill bodies (`Base directory for this skill:`, `# /loop`) 121 M chars; stop-hook 23 M; task-notification 28 M; codex `<environment_context>` / AGENTS.md 15 M; compact summaries 11 M; slash-command wrappers 8 M | see the noise table |
| 9 | secrets past the 10 rules | in our text chunks: 10 WireGuard `PrivateKey = …`, 32 Telegram-bot-token shapes, 16 Discord webhook URLs, 73 Discord-bot-token shapes (may include false hits); 0 Anthropic / GitHub keys left | they would reach the embed host. Add rules upstream (relic-scan `engine.rs`) or a second pass here |
| 10 | chunks, undated, change detection | 955,858 of 987,801 messages are one chunk; 675 messages make 16,048 chunks (one makes 931). `undated` = 49 rows. Month mtime 11:58, our part 12:18: the mtime test works because months are replaced atomically | a cap on chunks per message would stop one paste from flooding a search |

## Noise in our text stream today

| class | chunks | distinct texts | M chars |
|---|---|---|---|
| keep | 878,787 | 746,790 | 784.7 |
| stop-hook feedback | 56,080 | 6,151 | 22.7 |
| invisible-only assistant | 45,452 | 3 | 0.0 |
| slash-command wrapper | 24,519 | 4,504 | 8.2 |
| skill body | 23,233 | 4,024 | 120.7 |
| task-notification | 14,614 | 14,445 | 28.1 |
| omp toolResult | 12,016 | 11,098 | 16.8 |
| starts with system-reminder | 6,476 | 1,403 | 6.0 |
| interrupted | 5,252 | 51 | 0.2 |
| codex context | 2,871 | 457 | 14.5 |
| compact summary | 1,911 | 1,751 | 11.4 |

Noise = 192,424 chunks (18 %), 43,887 distinct texts, 229 M chars (23 %). Embed cost follows distinct texts (the cache
key), so the noise costs little to embed but fills search results. Task-notifications and compact summaries carry real
content; whether to keep them is a choice, not a bug.

## Questions for relic4-scan

1. The brief counts 476,812 duplicate tool results; `(sid, call_id, ts, body)` gives 331,670 here. Which query?
2. Should omp `toolResult` keep a `text` row at all? It is jsonl-truth parity, but it puts tool output in `s = text`.
3. Could `sessions.parquet` cover omp sub-session files and second session ids in one file?
4. Rules for WireGuard private keys, Telegram bot tokens and Discord webhooks/tokens in `redact()`?
