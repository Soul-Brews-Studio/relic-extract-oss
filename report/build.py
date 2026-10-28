"""relic-extract report: month-by-month numbers over relic-scan's index and relic-extract's cleaned text.

    uv run python report/build.py [--public] [--out DIR]

Reads ~/.relic-scan (every row, every stream) and ~/.relic-extract/text (the cleaned human and AI text), and writes
OUT/report.json, OUT/monthly.parquet and OUT/index.html (one self-contained page, no network). Only counts leave the
data: no message text, no paths. --public also drops repository and skill names.
"""

import argparse
import datetime as dt
import json
import os
import re
import time
from pathlib import Path

import duckdb

import common

HERE = Path(__file__).parent
HOME = Path.home()

# tool names grouped into families, so a month's tool mix stays within 7 series
TOOL_FAMILY = """
case
  when name = 'Bash' then 'Bash'
  when name in ('exec_command', 'exec', 'write_stdin', 'shell', 'local_shell') then 'Codex shell'
  when name in ('Read', 'Grep', 'Glob', 'LS', 'NotebookRead') then 'Read & search'
  when name in ('Edit', 'Write', 'MultiEdit', 'NotebookEdit', 'apply_patch') then 'Edit & write'
  when name in ('WebFetch', 'WebSearch') then 'Web'
  when name in ('Agent', 'Task', 'StructuredOutput', 'SendMessage', 'TaskOutput') then 'Agents'
  else 'Other'
end"""
TOOL_ORDER = ["Bash", "Codex shell", "Read & search", "Edit & write", "Web", "Agents", "Other"]


def rows(con, sql):
    cur = con.execute(sql)
    cols = [d[0] for d in cur.description]
    return [dict(zip(cols, r)) for r in cur.fetchall()]


def by_month(con, sql, months, keys):
    """{key: [value per month]} from rows of (m, k, n)."""
    out = {k: [0] * len(months) for k in keys}
    ix = {m: i for i, m in enumerate(months)}
    for r in rows(con, sql):
        if r["m"] in ix and r["k"] in out:
            out[r["k"]][ix[r["m"]]] = r["n"] or 0
    return out


def build(scan: Path, extract: Path, public: bool):
    con = common.connect(scan, extract)
    t0 = time.time()

    months = [r["m"] for r in rows(con, "select distinct m from r where m <> '' order by m")]
    T = {}

    # 1. what the machine holds: rows by kind
    T["kinds"] = by_month(con, """select m, case when s = 'tools' then case when kind = 'call' then 'tool calls' else 'tool results' end
                                  when s = 'thinking' then 'thinking' when role = 'user' then 'user text' else 'AI text' end k,
                                  count(*) n from r group by all""", months,
                          ["user text", "AI text", "thinking", "tool calls", "tool results"])

    # 2. who writes the prompts: user messages in main sessions, prompts one agent wrote for another, and the
    #    harness text the clean step dropped
    raw_user = by_month(con, "select m, 'raw' k, count(*) n from r where s = 'text' and role = 'user' group by all", months, ["raw"])["raw"]
    u = by_month(con, """select substr(ts_th, 1, 7) m, case when tier = 'main' then 'main' else 'agent' end k, count(distinct msgkey) n
                         from x where role = 'user' group by all""", months, ["main", "agent"])
    T["human"] = {"main session": u["main"], "agent to agent": u["agent"],
                  "harness text": [max(a - b - c, 0) for a, b, c in zip(raw_user, u["main"], u["agent"])]}
    T["ai_msgs"] = by_month(con, "select substr(ts_th, 1, 7) m, 'a' k, count(distinct msgkey) n from x where role = 'assistant' group by all",
                            months, ["a"])["a"]

    # characters written (cleaned, chunk overlaps taken back out)
    chars = by_month(con, """select m, k, sum(c) n from (select substr(any_value(ts_th), 1, 7) m,
                             case when any_value(role) = 'assistant' then 'AI' when any_value(tier) = 'main' then 'main session'
                             else 'agent to agent' end k, sum(length(text)) - 400 * (count(*) - 1) c from x group by msgkey) group by all""",
                     months, ["main session", "agent to agent", "AI"])
    T["chars"] = chars

    # Thai in what a person typed
    th = by_month(con, """select substr(ts_th, 1, 7) m, 'th' k, avg(case when regexp_matches(text, '[฀-๿]') then 1 else 0 end) n
                          from x where role = 'user' and tier = 'main' and chunk = 0 group by all""", months, ["th"])["th"]
    T["thai_share"] = [round(v, 4) for v in th]

    # 3. sessions started, by tier (agents spawning agents)
    T["tiers"] = by_month(con, """select substr(first_ts, 1, 7) m, tier k, count(*) n from s where first_ts <> '' group by all""",
                          months, ["main", "subagent", "workflow-agent"])
    T["engines"] = by_month(con, """select m, case when engine in ('claude', 'codex') then engine else 'other' end k, count(*) n
                                    from r group by all""", months, ["claude", "codex", "other"])

    # 4. tools: family mix and the error rate
    T["tools"] = by_month(con, f"select m, {TOOL_FAMILY} k, count(*) n from r where s = 'tools' and kind = 'call' group by all",
                          months, TOOL_ORDER)
    err = by_month(con, """select m, 'e' k, avg(case when err then 1 else 0 end) n from r
                           where s = 'tools' and kind = 'result' group by all""", months, ["e"])["e"]
    T["tool_error_rate"] = [round(v, 4) for v in err]

    # 5. thinking: saved text vs signature only (Claude Code stopped writing the text in 2026)
    T["thinking"] = by_month(con, """select m, case when text = '' then 'signature only' else 'text saved' end k, count(*) n
                                     from r where s = 'thinking' group by all""", months, ["text saved", "signature only"])

    # 6. rhythm: when a person writes, Bangkok time
    heat = [[0] * 24 for _ in range(7)]
    for r in rows(con, """select isodow(cast(substr(ts_th, 1, 19) as timestamp)) - 1 d, cast(substr(ts_th, 12, 2) as int) h,
                          count(distinct msgkey) n from x where role = 'user' and tier = 'main' and ts_th <> '' group by all"""):
        heat[r["d"]][r["h"]] = r["n"]
    T["rhythm"] = heat

    # 7. session shape: main sessions by how many messages they had in them
    T["session_size"] = by_month(con, """with p as (select sid, count(distinct msgkey) n from x where role = 'user' and tier = 'main' group by sid)
                         select substr(s.first_ts, 1, 7) m, case when coalesce(p.n, 0) = 0 then 'no prompt kept'
                           when p.n = 1 then '1 prompt' when p.n < 5 then '2-4' else '5 or more' end k, count(*) n
                         from s left join p using (sid) where s.tier = 'main' and s.first_ts <> '' group by all""",
                                 months, ["1 prompt", "2-4", "5 or more", "no prompt kept"])

    # 8. names: skills loaded and repositories worked in (private view only)
    if not public:
        skills = rows(con, """select regexp_extract(text, 'skills/([A-Za-z0-9_.:-]+)', 1) k, count(*) n from r
                              where s = 'text' and role = 'user' and text like 'Base directory for this skill:%' group by 1
                              having k <> '' order by n desc limit 14""")
        names = [r["k"] for r in skills]
        T["skills"] = {"names": names, "cells": by_month(con, """select m, regexp_extract(text, 'skills/([A-Za-z0-9_.:-]+)', 1) k, count(*) n
                       from r where s = 'text' and role = 'user' and text like 'Base directory for this skill:%' group by all""", months, names)}
        repos = rows(con, """select ses.cat k, count(distinct x.msgkey) n from x join ses using (sid) where x.role = 'user' and x.tier = 'main'
                             group by 1 order by n desc limit 14""")
        names = [r["k"] for r in repos]
        T["repos"] = {"names": names, "cells": by_month(con, """select substr(x.ts_th, 1, 7) m, ses.cat k, count(distinct x.msgkey) n
                      from x join ses using (sid) where x.role = 'user' and x.tier = 'main' group by all""", months, names)}
    T["new_repos"] = by_month(con, """select m, 'n' k, count(*) n from (select cat, min(substr(first_ts, 1, 7)) m from s
                                      where first_ts <> '' group by cat) group by all""", months, ["n"])["n"]

    # 10. how long: tool call -> its result, by family (median, p90, total hours, a call capped at 1 h)
    con.execute(f"""create table tl as with c as (select call_id, m, {TOOL_FAMILY} fam, ts from r
                      where s = 'tools' and kind = 'call' and call_id <> ''),
                    rr as (select call_id, min(ts) AS rts from r where s = 'tools' and kind = 'result' and call_id <> '' group by 1)
                    select c.m, c.fam, date_diff('millisecond', cast(c.ts as timestamp), cast(rr.rts as timestamp)) / 1000.0 AS d
                    from c join rr using (call_id)""")
    con.execute("delete from tl where d < 0")
    T["tool_hours"] = by_month(con, "select m, fam k, sum(least(d, 3600)) / 3600 n from tl group by all", months, TOOL_ORDER)
    T["tool_median_s"] = by_month(con, "select m, fam k, median(d) n from tl group by all", months, TOOL_ORDER)
    T["tool_p90_s"] = by_month(con, "select m, fam k, quantile_cont(d, 0.9) n from tl group by all", months, TOOL_ORDER)
    tl_all = rows(con, "select count(*) AS n, median(d) AS med, quantile_cont(d, 0.9) AS p90, sum(least(d, 3600)) / 3600 AS hrs from tl")[0]

    # session length by tier: median minutes, and agent-hours (a session capped at 12 h)
    con.execute("""create table sl as select substr(first_ts, 1, 7) m, tier,
                   date_diff('second', cast(substr(first_ts, 1, 19) as timestamp), cast(substr(last_ts, 1, 19) as timestamp)) / 60.0 AS mins
                   from s where first_ts <> '' and last_ts <> ''""")
    tiers3 = ["main", "subagent", "workflow-agent"]
    T["session_median_min"] = by_month(con, "select m, tier k, median(mins) n from sl group by all", months, tiers3)
    T["agent_hours"] = by_month(con, "select m, tier k, sum(least(mins, 720)) / 60 n from sl group by all", months, tiers3)

    # the history-search tools over time: calls of each tool, and loads of the skills that search history
    T["search_tools"] = by_month(con, """select m, k, count(*) n from (select m, case
        when name like 'mcp__relic3__%' or (name in ('Bash', 'exec_command', 'exec') and body like '%relic3 %') then 'relic3'
        when name like 'mcp__relic__%' or (name in ('Bash', 'exec_command', 'exec') and regexp_matches(body, 'relic4 |relic-scan|relic-extract')) then 'relic4 / relic-scan / relic-extract'
        when name like 'mcp__session-viewer__%' or name like 'mcp__session-search__%' or name like 'mcp__session-dream__%' then 'session-viewer MCP'
        when name like 'mcp__jsonl-indexer__%' or (name in ('Bash', 'exec_command', 'exec') and regexp_matches(body, '(^|[ ;|(])tq |jsonl-truth')) then 'jsonl-truth / tq'
        end k from r where s = 'tools' and kind = 'call') where k is not null group by m, k""", months,
        ["relic3", "relic4 / relic-scan / relic-extract", "session-viewer MCP", "jsonl-truth / tq"])
    T["search_skills"] = by_month(con, """select m, regexp_extract(text, 'skills/([A-Za-z0-9_.:-]+)', 1) k, count(*) n from r
        where s = 'text' and role = 'user' and text like 'Base directory for this skill:%' group by all""", months,
        ["dig", "trace", "relic3", "ralph-dig", "find-session", "jsonl-truth"])

    # 9. month by month: each month's own rankings (top names, busiest day)
    det = {m: {} for m in months}

    def top_per_month(key, sql, n=6):
        for r in rows(con, f"""select m, k, n from (select m, k, n, row_number() over (partition by m order by n desc) rk
                               from ({sql})) where rk <= {n} order by m, n desc"""):
            if r["m"] in det:
                det[r["m"]].setdefault(key, []).append([r["k"], r["n"]])

    if not public:
        top_per_month("repos", """select substr(x.ts_th, 1, 7) m, ses.cat k, count(distinct x.msgkey) n
                                 from x join ses using (sid) where x.role = 'user' and x.tier = 'main' group by all""")
        top_per_month("skills", """select m, regexp_extract(text, 'skills/([A-Za-z0-9_.:-]+)', 1) k, count(*) n from r
                                  where s = 'text' and role = 'user' and text like 'Base directory for this skill:%' group by all""")
        top_per_month("commands", """select substr(ts_th, 1, 7) m, regexp_extract(text, '^(/[A-Za-z0-9_.:-]+)', 1) k,
                                    count(distinct msgkey) n from x where role = 'user' and tier = 'main' and chunk = 0
                                    and regexp_matches(text, '^/[A-Za-z]')
                                    and not regexp_matches(text, '^/(Users|opt|home|tmp|private|var|etc|Volumes)/') group by all""")
        top_per_month("tools", "select m, name k, count(*) n from r where s = 'tools' and kind = 'call' group by all")
    top_per_month("days", """select substr(ts_th, 1, 7) m, substr(ts_th, 1, 10) k, count(distinct msgkey) n
                             from x where role = 'user' and tier = 'main' and ts_th <> '' group by all""", 3)
    for r in rows(con, """select substr(ts_th, 1, 7) m, count(distinct substr(ts_th, 1, 10)) n_days,
                          count(distinct sid) sessions from x where role = 'user' and tier = 'main' and ts_th <> '' group by all"""):
        if r["m"] in det:
            det[r["m"]]["active_days"] = r["n_days"]
            det[r["m"]]["prompted_sessions"] = r["sessions"]
    T["month_detail"] = det

    tot = rows(con, "select count(*) rows from r")[0]
    tot["sessions"] = rows(con, "select count(*) n from s")[0]["n"]
    meta = {
        "built": dt.datetime.now(dt.timezone(dt.timedelta(hours=7))).strftime("%Y-%m-%d %H:%M"),
        "months": months,
        "public": public,
        "secs": round(time.time() - t0, 1),
        "totals": {
            "rows": tot["rows"],
            "sessions": tot["sessions"],
            "main_msgs": sum(T["human"]["main session"]),
            "agent_msgs": sum(T["human"]["agent to agent"]),
            "harness_msgs": sum(T["human"]["harness text"]),
            "ai_msgs": sum(T["ai_msgs"]),
            "tool_calls": sum(T["kinds"]["tool calls"]),
            "repos": rows(con, "select count(distinct cat) n from s")[0]["n"],
            "tool_pairs": tl_all["n"], "tool_median_s": round(tl_all["med"], 2), "tool_p90_s": round(tl_all["p90"], 1),
            "tool_hours": round(tl_all["hrs"]),
        },
    }
    return {"meta": meta, "t": T}


def tidy(data, out: Path):
    """monthly.parquet: one row per (month, metric, series, value), the data-engineering view of the same numbers."""
    months, recs = data["meta"]["months"], []
    for metric, v in data["t"].items():
        if metric == "month_detail":  # rankings, not values per month
            continue
        if isinstance(v, dict) and "cells" in v:
            v = v["cells"]
        if isinstance(v, dict):
            for series, vals in v.items():
                recs += [(m, metric, series, float(x)) for m, x in zip(months, vals)]
        elif isinstance(v, list) and len(v) == len(months) and not isinstance(v[0], list):
            recs += [(m, metric, "", float(x)) for m, x in zip(months, v)]
    con = duckdb.connect()
    con.execute("create table t (month varchar, metric varchar, series varchar, value double)")
    con.executemany("insert into t values (?, ?, ?, ?)", recs)
    con.execute(f"copy t to '{out / 'monthly.parquet'}' (format parquet)")
    return len(recs)


def page(data):
    html = (HERE / "index.html").read_text()
    css = (HERE / "report.css").read_text()
    js = "\n".join((HERE / f).read_text() for f in ("charts.js", "days.js", "windows.js", "work.js", "switches.js", "report.js", "status.js", "pictures.js", "deep.js", "focus.js", "oracles.js", "memory.js", "months.js", "nav.js"))
    html = html.replace("/*I18N*/", (HERE / "i18n.js").read_text())
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    return html.replace("/*CSS*/", css).replace("/*JS*/", js).replace("/*DATA*/null", blob)


TYPES = ("build", "fix", "ops", "research", "writing", "planning", "mixed")


def window_sessions(scan, extract):
    """{unit: {(day, start minute): [session ids]}}: the main-session prompts each window was read from, the same
    rows readings.py and blocks.py feed the model (user, chunk 0, main tier), so a reading leads back to its sessions."""
    con = common.connect(scan, extract)
    mins = "(cast(substr(ts_th, 12, 2) as int) * 60 + cast(substr(ts_th, 15, 2) as int))"
    out = {}
    for unit, span in (("day", 1440), ("3h", 180), ("1h", 60), ("15m", 15)):
        out[unit] = {(d, int(m)): sids for d, m, sids in con.execute(f"""select substr(ts_th, 1, 10) d, ({mins} // {span}) * {span} AS m0,
            list(distinct sid order by sid) from x where role = 'user' and chunk = 0 and tier = 'main' and ts_th <> '' group by 1, 2""").fetchall()}
    return out


def windows(rep, public, share, sess=None):
    """Every window reading, one compact row each: [day, start minute, type, top project, prompts, focus, friction,
    lesson, session ids]. Lanes: blocks.json (3h) and blocks-1h.json on gb300, readings-<unit>-<lane>-<model>[-deep].json.
    The share view keeps type, project and count, not the model's sentences; the public view keeps type and count.
    Session ids only in the private view (sess is None otherwise)."""
    lanes = {}

    def row(day, m0, rd, shape, unit):
        ty = rd.get("type") if rd.get("type") in TYPES else "mixed"
        txt = not (public or share)
        return [day, m0, ty, None if public else shape.get("top_project"), shape.get("prompts", 0),
                rd.get("focus") if txt else None, rd.get("friction") if txt else None, rd.get("lesson") if txt else None,
                (sess or {}).get(unit, {}).get((day, m0)) if sess else None]

    for f, unit in (("blocks.json", "3h"), ("blocks-1h.json", "1h")):
        if (rep / f).exists():
            b = json.loads((rep / f).read_text())
            rows = [row(x["day"], x["h0"] * 60, x.get("reading") or {}, x.get("shape") or {}, unit) for x in b["blocks"]]
            lanes[f"{unit} gb300"] = dict(unit=unit, host="gb300", model=b.get("model"), deep=False, built=b.get("built"),
                                          rows=sorted(rows, key=lambda r: (r[0], r[1])),
                                          summary=None if public else b.get("summary"))
    for f in sorted(rep.glob("readings-*.json")):
        if f.name.startswith("readings-cache"):
            continue
        r = json.loads(f.read_text())
        lane = r["model"].split(":", 1)[0]
        if r["unit"] in ("3h", "1h") and lane == "gb300":
            continue  # the blocks files already carry these
        rows = []
        for w in r["windows"]:
            lab = w["window"]
            day, m0 = lab[:10], (int(lab[11:13]) * 60 + int(lab[14:16])) if len(lab) > 10 else 0
            rows.append(row(day, m0, w.get("reading") or {}, w.get("shape") or {}, r["unit"]))
        if len(rows) < 30:
            continue  # a trial run (--limit), not a lane
        lanes[f"{r['unit']} {lane}{' deep' if r.get('deep') else ''}"] = dict(
            unit=r["unit"], host=lane, model=r["model"].split(":", 1)[1], deep=r.get("deep", False), built=r.get("built"),
            rows=sorted(rows, key=lambda x: (x[0], x[1])), summary=None)
    return lanes


def share(data):
    """The share view: keep every number, chart and hand-given name; drop every quoted passage and every
    machine-picked word (a c-TF-IDF word can be a token, a password-like string or a person's handle)."""
    data["meta"]["view"] = "share"
    data.pop("asks", None)
    for t in (data.get("topics") or {}).get("topics", []):
        t.pop("examples", None)
        t["terms"] = [t.get("name") or f"topic {t['id']}"]
    v = data.get("vspace")
    if v:
        v["lessons"] = {}
    f = data.get("focus")
    if f:
        for fam in f["families"].values():
            fam["lessons"] = []
            for r in fam["repos"]:
                r["started"], r["now"] = [], []
            for ft in fam["features"]:
                ft["examples"] = []
                ft["terms"] = [ft.get("name") or f"feature {ft['id']}"]


UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", re.I)


def scrub_ids(x):
    """A session id can sit inside a folder name (Claude Code's per-session scratchpad dirs): the share and public
    views write "<session>" in its place, keys included."""
    if isinstance(x, str):
        return UUID.sub("<session>", x)
    if isinstance(x, list):
        return [scrub_ids(v) for v in x]
    if isinstance(x, dict):
        return {scrub_ids(k): scrub_ids(v) for k, v in x.items()}
    return x


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", default=str(HOME / ".relic-scan"))
    ap.add_argument("--extract", default=str(HOME / ".relic-extract"))
    ap.add_argument("--out", default=None)
    ap.add_argument("--public", action="store_true", help="counts only: no repository or skill names")
    ap.add_argument("--share", action="store_true", help="every chart and name, no quoted text: for publishing")
    a = ap.parse_args()
    out = Path(a.out or Path(a.extract) / ("report-public" if a.public else "report-share" if a.share else "report")).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    data = build(Path(a.scan).expanduser(), Path(a.extract).expanduser(), a.public)
    vp = Path(a.extract).expanduser() / "report" / "vspace.json"
    if vp.exists():  # vspace.py's output; the public view keeps only what names no repository and quotes no text
        v = json.loads(vp.read_text())
        if a.public:
            v = {"months": v["months"], "growth": v["growth"], "ship": v["ship"], "friction": v["friction"],
                 "works": {"by_month": v["works"]["by_month"], "shipped": v["works"]["shipped"], "by_topic": [], "by_oracle": [], "flows": []}}
        data["vspace"] = v
    ap_ = Path(a.extract).expanduser() / "report" / "asks.json"
    if ap_.exists() and not a.public:  # asks.py's output: quoted passages, so the private view only
        data["asks"] = json.loads(ap_.read_text())
    sp = Path(a.extract).expanduser() / "report" / "switches.json"
    if sp.exists() and not a.public:  # switches.py's output: project names and counts
        data["switches"] = json.loads(sp.read_text())
    wp = Path(a.extract).expanduser() / "report" / "work.json"
    if wp.exists() and not a.public:  # work.py's output: repository names and counts
        data["work"] = json.loads(wp.read_text())
    dp = Path(a.extract).expanduser() / "report" / "days.json"
    if dp.exists():  # days.py's output; the share and public views keep each day's numbers and type, not its sentences
        dd = json.loads(dp.read_text())
        if a.public or a.share:
            for d in dd["days"]:
                ty = (d.get("summary") or {}).get("type")
                d["summary"] = {"type": ty} if ty in TYPES else None
                if a.public:
                    d["repos"] = []
        data["days"] = dd
    sess = None if (a.public or a.share) else window_sessions(Path(a.scan).expanduser(), Path(a.extract).expanduser())
    lanes = windows(Path(a.extract).expanduser() / "report", a.public, a.share, sess)
    if lanes:  # blocks.py and readings.py: what each 3h/1h/15m window was, on gb300 and z.ai
        data["windows"] = lanes
    stp = Path(a.extract).expanduser() / "report" / "status.json"
    if stp.exists():  # status.py's output: each reading lane's counts, pace and finish, and the model trial (counts and model names)
        data["status"] = json.loads(stp.read_text())
    pp = Path(a.extract).expanduser() / "report" / "pictures.json"
    if pp.exists() and not a.public:  # pictures.py's output: topic names and typed words, no passages
        data["pictures"] = json.loads(pp.read_text())
    mp = Path(a.extract).expanduser() / "report" / "memory.json"
    if mp.exists() and not a.public:  # memory.py's output: names oracles, no note text
        data["memory"] = json.loads(mp.read_text())
    op = Path(a.extract).expanduser() / "report" / "oracles.json"
    if op.exists() and not a.public:  # oracles.py's output: names repositories, so not in the counts-only view
        data["oracles"] = json.loads(op.read_text())
    fp = Path(a.extract).expanduser() / "report" / "focus.json"
    if fp.exists():  # focus.py's output; the public view keeps only the monthly split by family
        f = json.loads(fp.read_text())
        data["focus"] = {"months": f["months"], "story": f["story"], "families": {}} if a.public else f
    tp = out / "topics.json" if not a.public else Path(a.extract).expanduser() / "report" / "topics.json"
    if tp.exists():  # topics.py's output; the public view drops the example prompts
        data["topics"] = json.loads(tp.read_text())
        if a.public:
            for t in data["topics"]["topics"]:  # words can name a repository: only hand-given names stay
                t.pop("examples", None)
                t["terms"] = [t.get("name") or f"topic {t['id']}"]
    if a.share:
        share(data)
    if a.share or a.public:
        data = scrub_ids(data)
    (out / "report.json").write_text(json.dumps(data, ensure_ascii=False, indent=1))
    n = tidy(data, out)
    (out / "index.html").write_text(page(data))
    for f in ("report.json", "monthly.parquet", "index.html"):
        os.chmod(out / f, 0o600)
    m = data["meta"]
    print(f"report: {len(m['months'])} months · {m['totals']['rows']:,} rows · {n:,} monthly values · {m['secs']}s -> {out}/index.html")


if __name__ == "__main__":
    main()
