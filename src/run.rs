//! `run` and `status`: which months of which streams to cut, and OUT/<stream>/_extract.json.
//!
//! A month is cut again when its source is newer than the part, or when the stream's settings changed
//! (_extract.json keeps their hash; the same hash as the Python version, so its parts stay fresh).

use std::fs;
use std::path::Path;

use serde_json::{Value, json};

use crate::config::{self, Config};
use crate::extract::{self, Done};

/// json.dumps(v, indent=1) layout (non-ASCII is written as is, not escaped).
pub fn pretty(v: &Value) -> String {
    let mut buf = Vec::new();
    let fmt = serde_json::ser::PrettyFormatter::with_indent(b" ");
    let mut ser = serde_json::Serializer::with_formatter(&mut buf, fmt);
    serde::Serialize::serialize(v, &mut ser).unwrap();
    String::from_utf8(buf).unwrap()
}

/// Worker threads for months: RELIC_EXTRACT_THREADS, else one per core up to 8 (each holds one month in memory).
pub fn threads() -> usize {
    std::env::var("RELIC_EXTRACT_THREADS")
        .ok()
        .and_then(|v| v.parse().ok())
        .filter(|&n: &usize| n > 0)
        .unwrap_or_else(|| std::thread::available_parallelism().map_or(4, |n| n.get()).min(8))
}

/// `f` over `items` on `threads()` workers; results in the items' order.
pub fn par_map<T: Sync, R: Send>(items: &[T], f: impl Fn(&T) -> R + Sync) -> Vec<R> {
    let next = std::sync::atomic::AtomicUsize::new(0);
    let out: Vec<std::sync::Mutex<Option<R>>> = items.iter().map(|_| std::sync::Mutex::new(None)).collect();
    std::thread::scope(|sc| {
        for _ in 0..threads().min(items.len()) {
            sc.spawn(|| {
                loop {
                    let i = next.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                    let Some(it) = items.get(i) else { break };
                    *out[i].lock().unwrap() = Some(f(it));
                }
            });
        }
    });
    out.into_iter().map(|m| m.into_inner().unwrap().unwrap()).collect()
}

fn mtime(p: &Path) -> Option<std::time::SystemTime> {
    fs::metadata(p).and_then(|m| m.modified()).ok()
}

/// Cut every enabled stream's months that need it. `months`: only these; `force`: cut them all.
pub fn run(cfg: &Config, months: Option<Vec<String>>, force: bool, only: Option<&str>) -> Result<Vec<Done>, String> {
    let src = extract::months_in(cfg.source());
    if src.is_empty() {
        return Err(format!(
            "no month files in {}\n  relic-scan run   (or set [extract] source)",
            cfg.source()
        ));
    }
    // "changed" | "all" | a list
    let want: Option<Vec<String>> = match months {
        Some(mut m) => {
            m.sort();
            Some(m)
        }
        None => cfg.v["months"]
            .as_array()
            .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect()),
    };
    let changed_only = want.is_none() && cfg.v["months"] == "changed";
    if let Some(w) = &want {
        let missing: Vec<&str> = w.iter().filter(|m| !src.contains_key(*m)).map(String::as_str).collect();
        if !missing.is_empty() {
            return Err(format!("no source for month(s) {} in {}", missing.join(", "), cfg.source()));
        }
    }
    if let Some(s) = only
        && !config::STREAMS.contains(&s)
    {
        return Err(format!("unknown stream {s} (known: {})", config::STREAMS.join(", ")));
    }
    let _lock = config::lock_out(cfg.out())?;
    let sess = extract::sessions(cfg.source(), cfg.v["sessions"] != "main")?;
    let mut done = vec![];
    for name in config::STREAMS {
        let st = cfg.stream(name);
        if st["enabled"] != true || only.is_some_and(|s| s != name) {
            continue;
        }
        let sdir = Path::new(cfg.out()).join(name);
        let state_f = sdir.join("_extract.json");
        let state: Value = fs::read(&state_f)
            .ok()
            .and_then(|b| serde_json::from_slice(&b).ok())
            .unwrap_or(json!({}));
        let h = cfg.stream_hash(name);
        let stale = state["hash"].as_str() != Some(h.as_str());
        if stale && state.as_object().is_some_and(|m| !m.is_empty()) {
            eprintln!("extract {name}: settings changed since the last run, every month is cut again");
        }
        let before = done.len();
        let mut todo = vec![];
        for (m, f) in &src {
            if want.as_ref().is_some_and(|w| !w.contains(m)) {
                continue;
            }
            let out = sdir.join(format!("{m}.parquet"));
            let fresh = matches!((mtime(&out), mtime(f)), (Some(o), Some(s)) if o >= s);
            if changed_only && fresh && !stale && !force {
                continue;
            }
            todo.push((m.as_str(), f.as_path(), out));
        }
        let cut = par_map(&todo, |(m, f, out)| extract::cut_month(cfg, name, m, f, out, &sess));
        for r in cut {
            done.push(r?);
        }
        // a stale stream cut for a few months only keeps its old hash, so the rest are cut on the next full run
        if !(stale && want.is_some()) {
            config::private_dir(&sdir).map_err(|e| e.to_string())?;
            let at = chrono::Local::now().format("%Y-%m-%dT%H:%M:%S%z").to_string();
            let body = json!({"hash": h, "settings": {"stream": st, "dedupe": cfg.v["dedupe"], "chunk": cfg.v["chunk"]},
                              "source": cfg.source(), "at": at});
            // written last: a part the state names is always on disk
            config::write_private(&state_f, |mut f| {
                use std::io::Write;
                f.write_all(pretty(&body).as_bytes())
                    .and_then(|_| f.sync_all())
                    .map_err(|e| e.to_string())
            })?;
        }
        eprintln!("extract {name}: {} month(s) cut -> {}", done.len() - before, sdir.display());
    }
    Ok(done)
}

/// `[extract] after`: once `run` has returned (OUT/.lock released, so a push that takes it does not wait on us),
/// run the command through `sh -c` if any part was cut, with RELIC_EXTRACT_CUT (the parts, comma separated,
/// relative to OUT) and RELIC_EXTRACT_OUT in its env. A command that fails is an error.
pub fn after(cfg: &Config, done: &[extract::Done]) -> Result<(), String> {
    let Some(cmd) = cfg.after() else { return Ok(()) };
    if done.is_empty() {
        return Ok(());
    }
    let cut: Vec<&str> = done.iter().map(|d| d.part.as_str()).collect();
    eprintln!("after: {cmd}");
    let st = std::process::Command::new("sh")
        .arg("-c")
        .arg(cmd)
        .env("RELIC_EXTRACT_CUT", cut.join(","))
        .env("RELIC_EXTRACT_OUT", cfg.out())
        .status()
        .map_err(|e| format!("after: {e}"))?;
    if st.success() {
        Ok(())
    } else {
        Err(format!("after: exited {}", st.code().map_or("by a signal".into(), |c| c.to_string())))
    }
}

/// Per stream: months cut, chunks, distinct texts, newest message. `json`: one object for scripts,
/// `{"out": …, "streams": {"text": {"enabled": true, "months": 15, "chunks": …, "distinct": …, "newest": …}, …}}`;
/// a stream with no parts has months 0 and chunks 0; one that cannot be read has an "error" field instead.
pub fn status(cfg: &Config, json: bool) {
    let mut streams = serde_json::Map::new();
    for name in config::STREAMS {
        let enabled = cfg.stream(name)["enabled"] == true;
        let on = if enabled { "on " } else { "off" };
        let d = Path::new(cfg.out()).join(name);
        let mut parts: Vec<_> = fs::read_dir(&d)
            .into_iter()
            .flatten()
            .flatten()
            .map(|e| e.path())
            .filter(|p| p.extension().is_some_and(|x| x == "parquet"))
            .collect();
        parts.sort();
        let mut row = serde_json::json!({"enabled": enabled, "months": parts.len()});
        if parts.is_empty() {
            row["chunks"] = 0.into();
            row["distinct"] = 0.into();
            row["newest"] = serde_json::Value::Null;
            if !json {
                println!("{name:9} {on}  no parts");
            }
        } else {
            match crate::read::part_stats(&parts) {
                Ok((n, u, last)) => {
                    row["chunks"] = n.into();
                    row["distinct"] = u.into();
                    row["newest"] = last.clone().map_or(serde_json::Value::Null, Into::into);
                    if !json {
                        println!(
                            "{name:9} {on}  {} months · {} chunks · {} distinct texts · newest {}",
                            parts.len(),
                            extract::num(n),
                            extract::num(u),
                            last.unwrap_or_default()
                        );
                    }
                }
                Err(e) => {
                    row["error"] = e.to_string().into();
                    if !json {
                        println!("{name:9} {on}  ✗ {e}");
                    }
                }
            }
        }
        streams.insert(name.to_string(), row);
    }
    if json {
        println!("{}", serde_json::json!({"out": cfg.out(), "streams": streams}));
    }
}
