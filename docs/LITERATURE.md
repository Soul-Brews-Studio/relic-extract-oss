# What five generations of relic teach extract (review, 2026-10-23)

Sources read (read-only): `relic-lessons` (CORE, LESSONS, OPEN, TIMELINE, book 02–09, rounds/), `agent-relic-v4`
(DESIGN, README, split.py, embed.py, sql.py, core.py), `relic4-scan` (docs/DESIGN, LEARNED, RESEARCH, COMPARE, OUTPUT-CHAIN,
src/), `agents-relic` (v1), `agent-relic-v2`, `agent-relic-v2.1`, `agent-relic-v3`. jsonl-truth was not opened.
Short names: v1 agents-relic · v2 · v2.1 · v3 · v4 agent-relic-v4 · scan relic4-scan · lessons relic-lessons.
Tags: **D** decided · **M** measured · **O** open.

## The lineage in one table

| gen | runtime | storage | embedded what | identity | outcome |
|---|---|---|---|---|---|
| v1 | Bun/TS (+Python, small Rust) | LanceDB per repo, ICU FTS | main tier, ≥24 chars, clip 2000, no chunking | uid = line slot | "Nothing ships" for vectors (`agents-relic bench/README.md:204`) |
| v2 | Bun/TS | v1 + durable JSONL archive | as v1 | uid | vectors in a side table, never a column |
| v2.1 | Bun/TS | monthly Lance partitions, one vector table per model | as v1 | (partition, uid) | ANN recall@10 0.762 at 3.9 M, so exact search forced |
| v3 | Bun/TS + Swift | weekly Parquet + Lance vectors | prose layer only, harness turns dropped, main only | `sha1(clip2000(text))` | first content key; BM25 Thai MRR@10 0.932 |
| v4 | Python + DuckDB | truth Parquet by month → split → Lance + ICU FTS | text-v1: text rows, 6000/400 chunks | blake2b-128 of chunk text, msgkey | live today |
| scan | Rust, one binary | month Parquet + meta | (step 1 only) | `(s, h, idx)` | 5.57 M rows, ~15 s incremental |

## What is settled

1. **Key vectors by the text, never by the slot.** v1's basename uid collided on 374 pairs (`lessons 08-operations.md:165`);
   a uid-keyed dedupe "silently dropped real results; it was reverted" (v2 `UPSTREAM.md:157-160`). Putting the partition
   into the id forces a re-embed on every layout change (`lessons CORE.md:84`). v3 moved to a content hash, v4 to
   blake2b-128 of the exact chunk. **D** — relic-extract's `key` is this.
2. **Hash exactly what is embedded, and cut by Unicode scalar.** v1 had TS (UTF-16) and Python (code points) truncating
   at different offsets (v2 `UPSTREAM.md:331`). **D** — the Rust cut counts `char`s, like Python's `str[i:j]`.
3. **Prose only by default; tools and thinking are opt-in layers.** Tools are 65 % of events (`lessons CORE.md:37`);
   before scan's text-blocks-only rule, 68.5 % of indexed "text" was tool output (`scan LEARNED.md:28`). 90.7–96 % of
   thinking blocks are signature-only (`lessons rounds/round-01-02.md:41`, v3 `DECISIONS.md:8`). **D/M**
4. **Content dedupe.** 37.4 % of text rows are the same message with other bytes (v4 `README.md:90`); here 27.6 % of
   user+assistant rows fold (docs/SCAN-INPUT.md). **M**
5. **Parquet over Lance for anything that is a record.** One week: 31 MB Parquet vs 120 MB Lance, 2,737 vs 63,754 files
   (`lessons CORE.md:48`). Lance failures: commit count (3 per file, ~250× slow), `countRows` wrong after an interrupted
   write, 0-byte data files, 64 % of v3's 14 GB index was the global Lance FTS table (`lessons LESSONS.md:12-14`,
   `CORE.md:112`, `rounds/round-03-04.md:17`). SQLite as the index of record: 92 GB that held 4.2 GB (`CORE.md:54`). **M**
6. **Monthly beats weekly** (MRR@10 0.764 vs 0.600, `scan LEARNED.md:24`) and an unchanged month is never rewritten
   (whole-period rewrite 24.97 s vs 10.17 s, `LEARNED.md:48`). **D/M**
7. **Atomic publish, state last.** tmp → fsync → rename, the done-marker written last; tested with kill -9
   (`lessons CORE.md:61`, `LESSONS.md:65`). **D**
8. **Verify a port with a keyed byte-for-byte row diff** (`lessons LESSONS.md:17`, `scan LEARNED.md:68-71`). **D** —
   `just parity`.
9. **Rust alone is not speed.** A 1:1 Rust port of the Lance writer was byte-identical, 2.5–3× less RAM, same speed —
   the store was the bottleneck (`lessons LESSONS.md:33-35`). Column-wise Arrow took a writer from 111 s to 14 s
   (`LESSONS.md:22`). **M** — the reason for a Rust extract is *one binary, no Python/DuckDB on the box*, not raw speed.
10. **Name every failure.** `catch { return [] }` indexed 27 sessions as 0 with exit 0 (`lessons LESSONS.md:61-63`). **D**

## Noise filters: what earlier generations dropped

| rule | where | status |
|---|---|---|
| text ≥ 24 chars | v1–v3 `embed.ts:362`, v3 `minChars` | D (v3) — not in v4 or extract |
| main tier only (no subagent / workflow-agent) | v1–v3 `isMain`; "45 % of last-7d lines, crowd semantic candidates" (v3) | D (v3) — v4 keeps them |
| harness turns: system-reminder, `<command-*>`, caveat, bash-stdout | v3 `turns.ts:15`, `noise.ts:133` | D (v3) — **v4 and scan filter none of it** |
| file-readback, edit payload, blob (unbroken run ≥ 120 chars) on tool rows | v1, `lessons CORE.md:40` | D for tools |
| prose is never noise: a blob pasted by a human stays | `lessons CORE.md:40` | D |
| log every drop, test each rule on real data | v1 `skipped.jsonl`; v1 bug: rule 2 matched "token" and ate real text | D |

relic-extract today has none of these. docs/SCAN-INPUT.md measures what they would remove: 192,424 chunks (18 %),
229 M chars, including stop-hook loops and zero-width-only replies that no earlier generation saw.

## FTS, ICU, LanceDB — where each belongs

- **FTS beats vectors on what people actually type.** v1: known-item FTS 0.890 vs e5 0.600; embeddings missed 22–40 %
  of known-item queries, FTS 2.5 %; RRF fusion made it worse (0.890 → 0.822) (v1 `bench/README.md`). v3: BM25 Thai
  MRR@10 0.932. Vectors win only on paraphrase (gemma 0.214 vs BM25 0.006–0.072). **M**
- **ICU** is how Thai is tokenized for FTS: v1–v2 used Lance ICU FTS; v4 builds an ICU FTS index on `text` in
  `<group>.lance` at embed time (`v4 embed.py:227`). SQLite FTS5 *trigram* is the other proven route (session-search:
  "the only index that finds Thai inside words").
- **LanceDB** stays the vector store in v4 (`embed.py`: parts ⋈ cache → `<group>.lance`), with the failures in point 5
  as known risks.
- **So:** extract should stay plain Parquet, engine-neutral, and feed *both* FTS and vectors from the same chunks.
  FTS/ICU and Lance are the index step (relic4 / relic-embed), not extract. What extract owes them is clean text, a
  stable `key`/`msgkey`, and per-row `role` / `tier` / `cat` to filter on.

## Embedding notes that constrain chunk size

- Earlier generations clipped at 2,000 chars, no chunking; 93 % of Thai spans fit in e5's 512 tokens; bge-m3's 8,192
  window "buys nothing" (v1 `bench:160-162`). **M** — 6000/400 has no measurement behind it here; it is jsonl-truth's.
- embeddinggemma needs its model-card prompts (Thai known-item 0.476 with, 0.330 without); prompts go into the model
  id, not into the key. Refuse English-only models on Thai scopes. **M/D**
- Cache: same text + model + prompt + dtype → same vector; v4 embeds ≤ 20,000 misses locally, more go to gb300 in
  100,000-row resumable parts (`v4 embed.py:33,92-115`). Part mtime decides re-embedding, so extract must rewrite a
  part only when its source changed. **D**

## Open, and worth deciding before relic-embed

1. Which harness/noise rules go into extract (`[extract.filter]`), on by default or not. v3 says drop; v4 kept all.
2. Main tier only, or a `tier` column so search can choose (v3: subagents crowd results).
3. `min_chars` (v3: 24).
4. Chunk size: 6000/400 vs the 2000 clip every earlier generation measured against.
5. Extra redaction (WireGuard, Telegram, Discord) before text leaves the machine for gb300.
6. Vector re-key by `(model, key)` + a refs table (v3 #19) — relic-embed's problem, but extract's `key` is ready for it.
