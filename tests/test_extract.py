"""relic-extract on a tiny fake relic-scan output: dedupe, cut, streams on/off, change detection, config merge."""
import os, time

import duckdb
import pyarrow.parquet as pq

from relic_extract import config, extract

ROWS = """
  ('text', 1, 0, 'claude', 'user', 'user', 'sid-a', '2026-10-01T00:00:00Z', 't', 'hello', NULL, NULL, NULL, NULL, NULL::BOOLEAN),
  ('text', 2, 0, 'claude', 'user', 'user', 'sid-a', '2026-10-01T00:00:00Z', 't', 'hello', NULL, NULL, NULL, NULL, NULL::BOOLEAN),
  ('text', 3, 0, 'claude', 'assistant', 'assistant', 'sid-a', '2026-10-01T00:00:01Z', 't', '{long}', NULL, NULL, NULL, NULL, NULL::BOOLEAN),
  ('text', 4, 0, 'claude', 'user', 'user', 'sid-a', '2026-10-01T00:00:02Z', 't', '   ', NULL, NULL, NULL, NULL, NULL::BOOLEAN),
  ('thinking', 5, 0, 'claude', 'thinking', 'assistant', 'sid-a', '2026-10-01T00:00:03Z', 't', 'hmm', NULL, NULL, NULL, NULL, NULL::BOOLEAN),
  ('tools', 6, 0, 'claude', NULL, NULL, 'sid-a', '2026-10-01T00:00:04Z', 't', NULL, 'call', 'c1', 'Bash', '{body}', NULL::BOOLEAN),
  ('tools', 6, 1, 'claude', NULL, NULL, 'sid-a', '2026-10-01T00:00:05Z', 't', NULL, 'result', 'c1', 'Bash', 'out', false)
"""


def scan_out(tmp_path, long_text="x" * 13000, body="b" * 3000):
    src = tmp_path / "scan"
    (src / "meta").mkdir(parents=True)
    c = duckdb.connect()
    c.execute(f"""COPY (SELECT * FROM (VALUES {ROWS.replace('{long}', long_text).replace('{body}', body)})
        AS v(s, h, idx, engine, role, type, sid, ts, ts_th, text, kind, call_id, name, body, err))
        TO '{src}/2026-10.parquet' (FORMAT parquet)""")
    c.execute(f"""COPY (SELECT 'sid-a' AS sid, 'main' AS tier, 'neo-oracle' AS cat, false AS probe)
        TO '{src}/meta/sessions.parquet' (FORMAT parquet)""")
    return src


def cfg_for(tmp_path, src, **streams):
    cfg = config._merge(config.DEFAULTS, {"extract": {"streams": streams}})["extract"]
    cfg["source"], cfg["out"] = str(src), str(tmp_path / "out")
    return cfg


def read(cfg, stream, month="2026-10"):
    return pq.read_table(os.path.join(cfg["out"], stream, f"{month}.parquet")).to_pylist()


def test_text_dedupes_by_content_and_cuts_like_relic4(tmp_path):
    cfg = cfg_for(tmp_path, scan_out(tmp_path))
    done = extract.run(cfg, log=lambda *_: None)
    assert [(d["stream"], d["messages"]) for d in done] == [("text", 2)]   # 'hello' twice -> once; blank dropped
    rows = read(cfg, "text")
    hello = [r for r in rows if r["text"] == "hello"][0]
    assert (hello["n_h"], hello["h"], hello["cat"], hello["probe"]) == (2, 1, "neo-oracle", False)
    long = [r for r in rows if r["role"] == "assistant"]
    assert [len(r["text"]) for r in long] == [6000, 6000, 1800]           # 6000 chars, 400 overlap: starts 0, 5600, 11200
    assert [r["chunk"] for r in long] == [0, 1, 2]
    assert long[0]["key"] == extract.key("x" * 6000)
    assert not os.path.exists(os.path.join(cfg["out"], "thinking"))       # off by default


def test_streams_on_and_tool_cut(tmp_path):
    cfg = cfg_for(tmp_path, scan_out(tmp_path), thinking={"enabled": True}, tools={"enabled": True})
    extract.run(cfg, log=lambda *_: None)
    assert [r["text"] for r in read(cfg, "thinking")] == ["hmm"]
    tools = read(cfg, "tools")
    assert [(r["kind"], r["name"], len(r["text"])) for r in tools] == [("call", "Bash", 2000)]   # results off, cut


def test_only_changed_months_and_settings_are_cut_again(tmp_path):
    src = scan_out(tmp_path)
    cfg = cfg_for(tmp_path, src)
    quiet = lambda *_: None
    assert len(extract.run(cfg, log=quiet)) == 1
    assert extract.run(cfg, log=quiet) == []                                # nothing changed
    later = time.time() + 5
    os.utime(src / "2026-10.parquet", (later, later))
    assert len(extract.run(cfg, log=quiet)) == 1                            # source newer
    cfg["chunk"] = {"max_chars": 5000, "overlap": 400}
    assert len(extract.run(cfg, log=quiet)) == 1                            # settings changed
    assert [len(r["text"]) for r in read(cfg, "text") if r["role"] == "assistant"][0] == 5000


def test_dedupe_h_keeps_every_row(tmp_path):
    src = scan_out(tmp_path)
    cfg = cfg_for(tmp_path, src)
    cfg["dedupe"] = {"key": "h"}
    extract.run(cfg, log=lambda *_: None)
    assert sum(r["text"] == "hello" for r in read(cfg, "text")) == 2


def test_config_file_nested_tables_merge_over_defaults(tmp_path):
    f = tmp_path / "relic.toml"
    f.write_text('[extract]\nsource = "/x"\n[extract.streams.thinking]\nenabled = true\n[embed]\nmodel = "m"\n')
    cfg, p, existed = config.load(str(f))
    assert existed and cfg["source"] == "/x"
    assert cfg["streams"]["thinking"] == {"enabled": True, "field": "text"}  # the rest of the table stays default
    assert cfg["streams"]["text"]["enabled"] and cfg["chunk"]["max_chars"] == 6000
    p2, made = config.init(str(tmp_path / "a" / "relic.toml"))
    assert made and config.load(p2)[0]["streams"] == config.DEFAULTS["extract"]["streams"]   # template = defaults
