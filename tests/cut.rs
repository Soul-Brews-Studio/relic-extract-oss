//! A fixture month through `run`: dedupe, roles, the clean step, the any-tier session join, and keys that must
//! equal DuckDB's md5(to_json(…)) and Python's blake2b (values computed by both and pasted here).

use std::fs;
use std::path::{Path, PathBuf};
use std::sync::Arc;

use arrow::array::{ArrayRef, AsArray, BooleanArray, Int64Array, RecordBatch, StringArray};
use arrow::datatypes::{Field, Schema};
use parquet::arrow::ArrowWriter;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;

type Row = (&'static str, i64, &'static str, &'static str, &'static str, &'static str);

// (s, h, role, sid, ts, text)
const ROWS: [Row; 9] = [
    ("text", 1, "user", "sid-a", "2026-10-01T00:00:00.000Z", "hello"),
    ("text", 2, "user", "sid-a", "2026-10-01T00:00:00.000Z", "hello"), // a mirrored copy: folded, n_h = 2
    (
        "text",
        3,
        "assistant",
        "sid-a",
        "2026-10-01T00:00:01.000Z",
        "ไทย \"q\"\nline\u{1} \\ end",
    ),
    (
        "text",
        4,
        "user",
        "sid-a",
        "2026-10-01T00:00:02.000Z",
        "<system-reminder>x</system-reminder>\nfix it",
    ),
    (
        "text",
        5,
        "user",
        "sid-a",
        "2026-10-01T00:00:03.000Z",
        "Stop hook feedback: loop",
    ),
    ("text", 6, "toolResult", "sid-a", "2026-10-01T00:00:04.000Z", "tool output"),
    (
        "text",
        7,
        "user",
        "sid-a",
        "2026-10-01T00:00:05.000Z",
        "<command-message>goal</command-message>\n<command-name>/goal</command-name>\n<command-args>go far</command-args>",
    ),
    ("text", 8, "user", "sid-sub", "2026-10-01T00:00:06.000Z", "from sub"),
    ("tools", 9, "", "sid-a", "2026-10-01T00:00:07.000Z", ""),
];

fn strs(v: Vec<Option<&str>>) -> ArrayRef {
    Arc::new(StringArray::from(v))
}

fn write(p: &Path, cols: Vec<(&str, ArrayRef)>) {
    let schema = Arc::new(Schema::new(
        cols.iter()
            .map(|(n, a)| Field::new(*n, a.data_type().clone(), true))
            .collect::<Vec<_>>(),
    ));
    let b = RecordBatch::try_new(schema.clone(), cols.into_iter().map(|c| c.1).collect()).unwrap();
    let mut w = ArrowWriter::try_new(fs::File::create(p).unwrap(), schema, None).unwrap();
    w.write(&b).unwrap();
    w.close().unwrap();
}

fn fixture(name: &str) -> (PathBuf, PathBuf) {
    let d = std::env::temp_dir().join(format!("relic-extract-test-{}-{name}", std::process::id()));
    let _ = fs::remove_dir_all(&d);
    let src = d.join("scan");
    fs::create_dir_all(src.join("meta")).unwrap();
    let col = |f: fn(&Row) -> &'static str| strs(ROWS.iter().map(|r| Some(f(r))).collect());
    write(
        &src.join("2026-10.parquet"),
        vec![
            ("s", col(|r| r.0)),
            ("h", Arc::new(Int64Array::from(ROWS.iter().map(|r| r.1).collect::<Vec<_>>()))),
            ("idx", Arc::new(Int64Array::from(vec![0; ROWS.len()]))),
            ("engine", col(|_| "claude")),
            ("role", col(|r| r.2)),
            ("type", col(|r| r.2)),
            ("sid", col(|r| r.3)),
            ("ts", col(|r| r.4)),
            ("ts_th", col(|r| r.4)),
            ("text", strs(ROWS.iter().map(|r| (r.0 == "text").then_some(r.5)).collect())),
            (
                "kind",
                strs(ROWS.iter().map(|r| (r.0 == "tools").then_some("call")).collect()),
            ),
            (
                "call_id",
                strs(ROWS.iter().map(|r| (r.0 == "tools").then_some("c1")).collect()),
            ),
            (
                "name",
                strs(ROWS.iter().map(|r| (r.0 == "tools").then_some("Bash")).collect()),
            ),
            (
                "body",
                strs(ROWS.iter().map(|r| (r.0 == "tools").then_some("{\"cmd\":\"ls\"}")).collect()),
            ),
            ("err", Arc::new(BooleanArray::from(vec![None; ROWS.len()]))),
        ],
    );
    write(
        &src.join("meta/sessions.parquet"),
        vec![
            ("sid", strs(vec![Some("sid-a"), Some("sid-sub")])),
            ("tier", strs(vec![Some("main"), Some("subagent")])),
            ("cat", strs(vec![Some("neo-oracle"), Some("sub-repo")])),
            ("probe", Arc::new(BooleanArray::from(vec![false, false]))),
        ],
    );
    let cfg = d.join("relic.toml");
    fs::write(
        &cfg,
        format!(
            "[extract]\nsource = \"{}\"\nout = \"{}\"\n",
            src.display(),
            d.join("out").display()
        ),
    )
    .unwrap();
    (d, cfg)
}

fn read(p: &Path) -> Vec<(String, String, i64, i64, String, String)> {
    let r = ParquetRecordBatchReaderBuilder::try_new(fs::File::open(p).unwrap())
        .unwrap()
        .build()
        .unwrap();
    let mut out = vec![];
    for b in r {
        let b = b.unwrap();
        let s = |n: &str| b.column_by_name(n).unwrap().as_string::<i32>().clone();
        let i = |n: &str| {
            b.column_by_name(n)
                .unwrap()
                .as_any()
                .downcast_ref::<Int64Array>()
                .unwrap()
                .clone()
        };
        let (key, mk, nh, h, cat, text) = (s("key"), s("msgkey"), i("n_h"), i("h"), s("cat"), s("text"));
        for j in 0..b.num_rows() {
            out.push((
                key.value(j).into(),
                mk.value(j).into(),
                nh.value(j),
                h.value(j),
                cat.value(j).into(),
                text.value(j).into(),
            ));
        }
    }
    out
}

#[test]
fn cleaned_cut_with_golden_keys() {
    let (d, cfgp) = fixture("clean");
    let cfg = relic_extract::config::load(Some(cfgp.to_str().unwrap())).unwrap();
    let done = relic_extract::run::run(&cfg, None, false, None).unwrap();
    assert_eq!(done.len(), 1);
    let rows = read(&d.join("out/text/2026-10.parquet"));
    let want = [
        // key = blake2b-128(cleaned text) (Python hashlib), msgkey = md5(to_json([s, role, ts, raw text])) (DuckDB)
        (
            "46fb7408d4f285228f4af516ea25851b",
            "23167e16da8dab1ba0f4997941b2db96",
            2,
            1,
            "neo-oracle",
            "hello",
        ),
        (
            "58bda1d4c4aafbc952bc3f51c42e414b",
            "6ae835c7ec3f1407a396cfb7aee3692d",
            1,
            3,
            "neo-oracle",
            "ไทย \"q\"\nline\u{1} \\ end",
        ),
        (
            "900184b1cb3edd7706213f45662c2abe",
            "88b1d509ed992cf9bbb458d7e2d25edb",
            1,
            4,
            "neo-oracle",
            "fix it",
        ),
        (
            "a0cc79147b5c766f5370edf723864900",
            "3dc331d0a9628eb7a18cc86bc4de8852",
            1,
            7,
            "neo-oracle",
            "/goal go far",
        ),
        (
            "8a072370508f53c8f84adeb5d8206d90",
            "9f20ea2435c6800e7ed1880d3ad44e71",
            1,
            8,
            "sub-repo",
            "from sub",
        ),
    ];
    let got: Vec<(&str, &str, i64, i64, &str, &str)> = rows
        .iter()
        .map(|r| (r.0.as_str(), r.1.as_str(), r.2, r.3, r.4.as_str(), r.5.as_str()))
        .collect();
    assert_eq!(got, want);
    // a second run cuts nothing: the part is fresh and the settings did not change
    assert_eq!(relic_extract::run::run(&cfg, None, false, None).unwrap().len(), 0);
    // the part and its state are private
    use std::os::unix::fs::PermissionsExt;
    assert_eq!(
        fs::metadata(d.join("out/text/2026-10.parquet")).unwrap().permissions().mode() & 0o777,
        0o600
    );
    assert_eq!(
        fs::metadata(d.join("out/text/_extract.json")).unwrap().permissions().mode() & 0o777,
        0o600
    );
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn raw_cut_keeps_every_role_and_text() {
    let (d, cfgp) = fixture("raw");
    let mut cfg = relic_extract::config::load(Some(cfgp.to_str().unwrap())).unwrap();
    cfg.raw();
    relic_extract::run::run(&cfg, None, false, None).unwrap();
    let rows = read(&d.join("out/text/2026-10.parquet"));
    assert_eq!(rows.len(), 7); // 8 text rows, the copy folded; nothing cleaned, toolResult kept
    assert!(rows.iter().any(|r| r.5.starts_with("<system-reminder>")));
    assert_eq!(rows.iter().find(|r| r.5 == "from sub").unwrap().4, "(no session)"); // main tier only
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn a_second_run_waits_for_the_lock() {
    let (d, cfgp) = fixture("lock");
    let cfg = relic_extract::config::load(Some(cfgp.to_str().unwrap())).unwrap();
    let held = relic_extract::config::lock_out(cfg.out()).unwrap();
    let e = relic_extract::run::run(&cfg, None, false, None).err().unwrap();
    assert!(e.contains("another relic-extract run holds"), "{e}");
    drop(held);
    assert!(relic_extract::run::run(&cfg, None, false, None).is_ok());
    let _ = fs::remove_dir_all(&d);
}

#[test]
fn after_runs_once_the_lock_is_released() {
    let (d, cfgp) = fixture("after");
    let seen = d.join("after.txt");
    let mut body = fs::read_to_string(&cfgp).unwrap();
    body = body.replace("[extract]\n", &format!("[extract]\nafter = \"printf '%s|%s' \\\"$RELIC_EXTRACT_CUT\\\" \\\"$RELIC_EXTRACT_OUT\\\" > {}\"\n", seen.display()));
    fs::write(&cfgp, body).unwrap();
    let cfg = relic_extract::config::load(Some(cfgp.to_str().unwrap())).unwrap();
    let done = relic_extract::run::run(&cfg, None, false, None).unwrap();
    // the lock is free again before the hook runs: a push may take it
    drop(relic_extract::config::lock_out(cfg.out()).unwrap());
    relic_extract::run::after(&cfg, &done).unwrap();
    let got = fs::read_to_string(&seen).unwrap();
    assert_eq!(got, format!("text/2026-10.parquet|{}", d.join("out").display()));
    // nothing cut: the hook does not run
    fs::remove_file(&seen).unwrap();
    let again = relic_extract::run::run(&cfg, None, false, None).unwrap();
    assert!(again.is_empty());
    relic_extract::run::after(&cfg, &again).unwrap();
    assert!(!seen.exists());
    let _ = fs::remove_dir_all(&d);
}
