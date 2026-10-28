//! ~/.arra/relic.toml: one file for the whole chain, one nested table per tool.
//!
//!   scan -> extract -> embed
//!   [scan]      relic-scan (today it still reads ~/.relic-scan/_config.json; reserved here)
//!   [extract]   this tool: which months and streams go in, how text is deduped and cut
//!   [embed]     relic-embed (next): model, gb300 / local runs, output
//!
//! ARRA_CONFIG overrides the path; --config overrides both. A missing file or key falls back to DEFAULTS, which are
//! jsonl-truth embed/workflow.toml's choices as of 2026-10-22 (text only, content dedupe, 6000/400 chunks).

use std::fs;
use std::os::unix::fs::{DirBuilderExt, PermissionsExt};
use std::path::{Path, PathBuf};

use serde_json::{Map, Value, json};

pub const STREAMS: [&str; 3] = ["text", "thinking", "tools"];

pub const TEMPLATE: &str = r##"# ~/.arra/relic.toml — the relic chain: scan -> extract -> embed. Each tool reads its own table.

[extract]
source = "~/.relic-scan"        # relic-scan's output: YYYY-MM.parquet + meta/sessions.parquet
out = "~/.relic-extract"        # <stream>/YYYY-MM.parquet, ready for relic-embed
months = "changed"              # "changed" | "all" | ["2026-10", "2026-09"]
sessions = "any"                # cat/probe from meta/sessions.parquet: "any" tier (main preferred) | "main" only
after = ""                      # a shell command run after a run that cut parts, once the lock is released;
                                # its env: RELIC_EXTRACT_CUT="text/2026-10.parquet,…", RELIC_EXTRACT_OUT=<out>

[extract.dedupe]
key = "content"                 # "content": the same message rewritten with other bytes is embedded once
                                # "h": one row per truth row

[extract.chunk]
max_chars = 6000                # longer texts are cut, with this overlap
overlap = 400

[extract.streams.text]
enabled = true
field = "text"
roles = ["user", "assistant"]   # [] = every role

[extract.streams.text.clean]     # before cutting; msgkey stays on the raw row, key is on the cleaned chunk
enabled = true
strip_tags = ["system-reminder"]                 # <tag>…</tag> removed anywhere
commands = "args"               # slash commands: "args" -> "/goal <args>" (none: dropped) | "drop" | "keep"
drop_tags = ["task-notification", "subagent_notification", "goal_context", "hook_prompt", "local-command-stdout",
             "local-command-stderr", "local-command-caveat", "bash-input", "bash-stdout", "bash-stderr",
             "environment_context", "user_instructions", "teammate-message"]   # a message starting with <tag is dropped
drop_prefixes = ["Stop hook feedback:", "Base directory for this skill:", "[Request interrupted by user",
                 "Caveat: The messages below were generated", "# AGENTS.md instructions", "API Error:",
                 "No response requested.", "[Image: original ", "Tool loaded.",
                 "[Workflow harness — user request]",   # a copy of the human's request, kept in the main session
                 "# /loop — schedule a recurring",        # the /loop skill body, sent on every tick
                 "Warning: The maximum number of unified exec processes",   # codex harness warning
                 "This session is being continued from a previous conversation",   # a compaction summary
                 "A session-scoped Stop hook is now active", "(Re-invocation of /",   # /goal and skill notices
                 "[Image: source: ", "Another Claude session sent a message:"]   # an attached image's path, a team relay
drop_texts = ["(holding)", "Holding, unchanged.", "Unchanged. Re-arming.", "No change since last check.", "Idle.",
              "(silent — home)", "⏳", "_(quiet — codex parked, board unchanged)_ 🌱", "Prompt is too long",
              "health-check-ping", "Continue from where you left off."]   # the whole message, after trimming: loop status chatter, an API echo, a probe
drop_invisible = true           # nothing but whitespace / zero-width characters
min_chars = 0                   # v3 used 24; 0 = keep short messages
drop_over_chars = 1000000       # a pasted dump (a 5.2 M-char crash report); 0 = keep every length
redact = true                   # WireGuard keys, Telegram / Discord tokens, Discord webhooks -> [kind:abcd…N]

[extract.streams.thinking]
enabled = false                 # Nat 2026-10-22: "just text no thinking for the embed"
field = "text"
roles = ["thinking"]

[extract.streams.tools]
enabled = false                 # Nat 2026-10-22: "next time we skip tools"
field = "body"
kinds = ["call"]                # "call" = tool inputs; add "result" for outputs
max_chars = 2000                # a tool body is cut here, not chunked

# [embed]                       # relic-embed (next): model, gb300 / local runs, output
"##;

fn clean_defaults() -> Value {
    json!({
        "enabled": true,
        "strip_tags": ["system-reminder"],
        "commands": "args",
        "drop_tags": ["task-notification", "subagent_notification", "goal_context", "hook_prompt", "local-command-stdout",
                      "local-command-stderr", "local-command-caveat", "bash-input", "bash-stdout", "bash-stderr",
                      "environment_context", "user_instructions", "teammate-message"],
        "drop_prefixes": ["Stop hook feedback:", "Base directory for this skill:", "[Request interrupted by user",
                          "Caveat: The messages below were generated", "# AGENTS.md instructions", "API Error:",
                          "No response requested.", "[Image: original ", "Tool loaded.",
                          "[Workflow harness — user request]", "# /loop — schedule a recurring",
                          "Warning: The maximum number of unified exec processes",
                          "This session is being continued from a previous conversation",
                          "A session-scoped Stop hook is now active", "(Re-invocation of /",
                          "[Image: source: ", "Another Claude session sent a message:"],
        "drop_texts": ["(holding)", "Holding, unchanged.", "Unchanged. Re-arming.", "No change since last check.", "Idle.",
                       "(silent — home)", "⏳", "_(quiet — codex parked, board unchanged)_ 🌱", "Prompt is too long",
                       "health-check-ping", "Continue from where you left off."],
        "drop_invisible": true,
        "min_chars": 0,
        "drop_over_chars": 1000000,
        "redact": true,
    })
}

fn defaults() -> Value {
    json!({"extract": {
        "source": "~/.relic-scan",
        "out": "~/.relic-extract",
        "months": "changed",
        "dedupe": {"key": "content"},
        "chunk": {"max_chars": 6000, "overlap": 400},
        "sessions": "any",
        "after": "",
        "streams": {
            "text": {"enabled": true, "field": "text", "roles": ["user", "assistant"], "clean": clean_defaults()},
            "thinking": {"enabled": false, "field": "text", "roles": ["thinking"]},
            "tools": {"enabled": false, "field": "body", "kinds": ["call"], "max_chars": 2000},
        },
    }})
}

pub fn expand(p: &str) -> String {
    match p.strip_prefix("~") {
        Some(rest) if rest.is_empty() || rest.starts_with('/') => format!("{}{rest}", std::env::var("HOME").unwrap_or_default()),
        _ => p.to_string(),
    }
}

fn merge(base: &Value, over: &Value) -> Value {
    match (base, over) {
        (Value::Object(b), Value::Object(o)) => {
            let mut out: Map<String, Value> = b.clone();
            for (k, v) in o {
                let nv = match (out.get(k), v) {
                    (Some(bv @ Value::Object(_)), Value::Object(_)) => merge(bv, v),
                    _ => v.clone(),
                };
                out.insert(k.clone(), nv);
            }
            Value::Object(out)
        }
        _ => over.clone(),
    }
}

fn toml_to_json(v: toml::Value) -> Value {
    match v {
        toml::Value::String(s) => Value::String(s),
        toml::Value::Integer(i) => json!(i),
        toml::Value::Float(f) => json!(f),
        toml::Value::Boolean(b) => Value::Bool(b),
        toml::Value::Datetime(d) => Value::String(d.to_string()),
        toml::Value::Array(a) => Value::Array(a.into_iter().map(toml_to_json).collect()),
        toml::Value::Table(t) => Value::Object(t.into_iter().map(|(k, v)| (k, toml_to_json(v))).collect()),
    }
}

/// The settings in effect: the [extract] table merged over the defaults, ~ expanded.
pub struct Config {
    pub v: Value,
    pub path: PathBuf,
    pub existed: bool,
}

impl Config {
    pub fn source(&self) -> &str {
        self.v["source"].as_str().unwrap_or("")
    }
    pub fn out(&self) -> &str {
        self.v["out"].as_str().unwrap_or("")
    }
    pub fn stream(&self, name: &str) -> &Value {
        &self.v["streams"][name]
    }

    /// The Python reference's behaviour: every role, no clean step, main-tier sessions (`run --raw`, `just parity`).
    pub fn raw(&mut self) {
        for s in STREAMS {
            if let Some(m) = self.v["streams"][s].as_object_mut() {
                m.remove("roles");
                m.remove("clean");
            }
        }
        self.v["sessions"] = Value::String("main".into());
    }
    pub fn content_dedupe(&self) -> bool {
        self.v["dedupe"]["key"] == "content"
    }
    /// The command `[extract] after` names, if any.
    pub fn after(&self) -> Option<&str> {
        self.v["after"].as_str().map(str::trim).filter(|s| !s.is_empty())
    }

    pub fn max_chars(&self) -> usize {
        self.v["chunk"]["max_chars"].as_u64().unwrap_or(6000) as usize
    }
    pub fn overlap(&self) -> usize {
        self.v["chunk"]["overlap"].as_u64().unwrap_or(400) as usize
    }

    /// What a part of this stream depends on, besides its source month: change it and every month is cut again.
    /// Same bytes as the Python version (json.dumps sort_keys -> blake2b-8), so its parts stay fresh.
    pub fn stream_hash(&self, name: &str) -> String {
        let mut deps = json!({"stream": self.stream(name), "dedupe": self.v["dedupe"], "chunk": self.v["chunk"]});
        if self.v["sessions"] != "main" {
            deps["sessions"] = self.v["sessions"].clone(); // "main" hashes as before, so --raw matches the Python version
        }
        blake2b_simd::Params::new()
            .hash_length(8)
            .hash(crate::json::py_dumps(&deps).as_bytes())
            .to_hex()
            .to_string()
    }
}

pub fn path_of(given: Option<&str>) -> PathBuf {
    let p = given
        .map(str::to_string)
        .or_else(|| std::env::var("ARRA_CONFIG").ok())
        .unwrap_or("~/.arra/relic.toml".into());
    PathBuf::from(expand(&p))
}

fn kind(v: &Value) -> &'static str {
    match v {
        Value::Null => "nothing",
        Value::Bool(_) => "true/false",
        Value::Number(_) => "a number",
        Value::String(_) => "a string",
        Value::Array(_) => "a list",
        Value::Object(_) => "a table",
    }
}

/// Every key the user wrote under [extract] must exist in the defaults, with the same kind of value: a typo
/// (`min_char = 24`) or a wrong type (`min_chars = "24"`) is an error, never a setting silently ignored.
fn check(user: &Value, def: &Value, path: &str) -> Result<(), String> {
    let (Value::Object(u), Value::Object(d)) = (user, def) else {
        return Ok(());
    };
    for (k, v) in u {
        let p = format!("{path}.{k}");
        let Some(dv) = d.get(k) else {
            let known: Vec<&str> = d.keys().map(String::as_str).collect();
            return Err(format!("unknown setting {p} (known here: {})", known.join(", ")));
        };
        let ok = kind(v) == kind(dv) || (p == "extract.months" && v.is_array());
        if !ok {
            return Err(format!("{p} must be {}, not {}", kind(dv), kind(v)));
        }
        check(v, dv, &p)?;
    }
    Ok(())
}

pub fn load(given: Option<&str>) -> Result<Config, String> {
    let path = path_of(given);
    let existed = path.exists();
    let raw = if existed {
        let text = fs::read_to_string(&path).map_err(|e| format!("{}: {e}", path.display()))?;
        toml_to_json(toml::Value::Table(
            toml::from_str(&text).map_err(|e| format!("{}: {e}", path.display()))?,
        ))
    } else {
        json!({})
    };
    if let Some(x) = raw.get("extract") {
        check(x, &defaults()["extract"], "extract").map_err(|e| format!("{}: {e}", path.display()))?;
    }
    let mut v = merge(&defaults(), &raw)["extract"].clone();
    for k in ["source", "out"] {
        let s = expand(
            v[k].as_str()
                .ok_or(format!("{}: [extract] {k} must be a string", path.display()))?,
        );
        v[k] = Value::String(s);
    }
    let p = path.display();
    let bad: Vec<&String> = v["streams"]
        .as_object()
        .map(|m| m.keys().filter(|k| !STREAMS.contains(&k.as_str())).collect())
        .unwrap_or_default();
    if !bad.is_empty() {
        let bad: Vec<&str> = bad.iter().map(|s| s.as_str()).collect();
        return Err(format!(
            "{p}: unknown stream(s) {} (known: {})",
            bad.join(", "),
            STREAMS.join(", ")
        ));
    }
    if !matches!(v["dedupe"]["key"].as_str(), Some("content" | "h")) {
        return Err(format!("{p}: [extract.dedupe] key must be \"content\" or \"h\""));
    }
    let (mc, ov) = (
        v["chunk"]["max_chars"].as_i64().unwrap_or(-1),
        v["chunk"]["overlap"].as_i64().unwrap_or(-1),
    );
    if !(0 <= ov && ov < mc) {
        return Err(format!("{p}: [extract.chunk] needs 0 <= overlap < max_chars"));
    }
    if !matches!(v["sessions"].as_str(), Some("any" | "main")) {
        return Err(format!("{p}: [extract] sessions must be \"any\" or \"main\""));
    }
    match &v["months"] {
        Value::String(s) if s == "changed" || s == "all" => {}
        Value::Array(a) if a.iter().all(Value::is_string) => {}
        _ => {
            return Err(format!(
                "{p}: [extract] months must be \"changed\", \"all\" or a list of \"YYYY-MM\""
            ));
        }
    }
    Ok(Config { v, path, existed })
}

/// `--what-if KEY=VALUE`: change one setting in memory. KEY is a dotted path inside [extract]
/// (`chunk.max_chars`, `streams.text.clean.min_chars`); a bare or short key is looked up in
/// streams.<stream>.clean, then streams.<stream>, then chunk, then [extract]. VALUE is TOML (24, true, ["a"], "x");
/// anything that does not parse as TOML is taken as a string.
pub fn set(v: &mut Value, stream: &str, kv: &str) -> Result<String, String> {
    let (k, val) = kv.split_once('=').ok_or(format!("--what-if {kv}: needs KEY=VALUE"))?;
    let (k, val) = (k.trim(), val.trim());
    let parsed = toml::from_str::<toml::Table>(&format!("v = {val}"))
        .ok()
        .and_then(|mut t| t.remove("v"))
        .map(toml_to_json)
        .unwrap_or(Value::String(val.to_string()));
    let tries = [
        format!("streams.{stream}.clean.{k}"),
        format!("streams.{stream}.{k}"),
        format!("chunk.{k}"),
        k.to_string(),
    ];
    let found = |path: &str| path.split('.').try_fold(&*v, |cur, p| cur.get(p)).is_some();
    let path = tries
        .iter()
        .find(|p| found(p))
        .cloned()
        .ok_or(format!("--what-if {k}: no such setting (try `relic-extract config`)"))?;
    let mut cur = &mut *v;
    for p in path.split('.') {
        cur = cur.get_mut(p).unwrap();
    }
    *cur = parsed;
    Ok(path)
}

/// Write the template if no config is there. Returns (path, written).
pub fn init(given: Option<&str>) -> std::io::Result<(PathBuf, bool)> {
    let p = path_of(given);
    if p.exists() {
        return Ok((p, false));
    }
    if let Some(d) = p.parent() {
        fs::DirBuilder::new().recursive(true).mode(0o700).create(d)?;
    }
    fs::write(&p, TEMPLATE)?;
    Ok((p, true))
}

pub fn private_dir(d: &Path) -> std::io::Result<()> {
    fs::DirBuilder::new().recursive(true).mode(0o700).create(d)
}

/// Write `path` atomically and privately: `<path>.part` is created 0600 (never readable by others, not even for a
/// moment), written and synced by `fill`, renamed over `path`, then the folder is synced so the rename survives a
/// power cut.
pub fn write_private(path: &Path, fill: impl FnOnce(fs::File) -> Result<(), String>) -> Result<(), String> {
    use std::os::unix::fs::OpenOptionsExt;
    let dir = path.parent().unwrap_or(Path::new("."));
    private_dir(dir).map_err(|e| format!("{}: {e}", dir.display()))?;
    let mut tmp = path.as_os_str().to_owned();
    tmp.push(".part");
    let tmp = PathBuf::from(tmp);
    let f = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(true)
        .mode(0o600)
        .open(&tmp)
        .map_err(|e| format!("{}: {e}", tmp.display()))?;
    f.set_permissions(fs::Permissions::from_mode(0o600))
        .map_err(|e| e.to_string())?; // an older .part kept its mode
    fill(f).map_err(|e| format!("{}: {e}", tmp.display()))?;
    fs::rename(&tmp, path).map_err(|e| format!("{}: {e}", path.display()))?;
    fs::File::open(dir)
        .and_then(|d| d.sync_all())
        .map_err(|e| format!("{}: {e}", dir.display()))
}

/// One run at a time per OUT: an exclusive lock on OUT/.lock, held until the returned file is dropped.
pub fn lock_out(out: &str) -> Result<fs::File, String> {
    use std::os::unix::fs::OpenOptionsExt;
    private_dir(Path::new(out)).map_err(|e| format!("{out}: {e}"))?;
    let p = Path::new(out).join(".lock");
    let f = fs::OpenOptions::new()
        .write(true)
        .create(true)
        .truncate(false)
        .mode(0o600)
        .open(&p)
        .map_err(|e| format!("{}: {e}", p.display()))?;
    match f.try_lock() {
        Ok(()) => Ok(f),
        Err(fs::TryLockError::WouldBlock) => Err(format!("another relic-extract run holds {} — wait for it", p.display())),
        Err(fs::TryLockError::Error(e)) => Err(format!("{}: {e}", p.display())),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn raw_text_hash_matches_python() {
        // ~/.relic-extract/text/_extract.json written by the Python version with the default settings
        let mut c = Config {
            v: defaults()["extract"].clone(),
            path: PathBuf::new(),
            existed: false,
        };
        let cleaned = c.stream_hash("text");
        c.raw();
        assert_eq!(c.stream_hash("text"), "f4fda3dd0075a191");
        assert_ne!(cleaned, "f4fda3dd0075a191"); // the clean step's settings re-cut every month
    }

    #[test]
    fn what_if_finds_short_keys() {
        let mut v = defaults()["extract"].clone();
        assert_eq!(set(&mut v, "text", "min_chars=24").unwrap(), "streams.text.clean.min_chars");
        assert_eq!(v["streams"]["text"]["clean"]["min_chars"], 24);
        assert_eq!(set(&mut v, "text", "max_chars = 2000").unwrap(), "chunk.max_chars");
        assert_eq!(set(&mut v, "text", "roles=[\"user\"]").unwrap(), "streams.text.roles");
        assert_eq!(v["streams"]["text"]["roles"], json!(["user"]));
        assert_eq!(set(&mut v, "text", "sessions=main").unwrap(), "sessions");
        assert_eq!(v["sessions"], "main");
        assert!(set(&mut v, "text", "nope=1").is_err());
    }

    #[test]
    fn typos_and_wrong_types_are_errors() {
        let d = defaults()["extract"].clone();
        let t = |s: &str| toml_to_json(toml::Value::Table(toml::from_str(s).unwrap()));
        assert!(
            check(
                &t("[streams.text.clean]\nmin_char = 24")["streams"],
                &d["streams"],
                "extract.streams"
            )
            .unwrap_err()
            .starts_with("unknown setting extract.streams.text.clean.min_char")
        );
        assert!(
            check(&t("[chunk]\nmax_chars = \"6000\"")["chunk"], &d["chunk"], "extract.chunk")
                .unwrap_err()
                .contains("must be a number, not a string")
        );
        assert!(check(&t("months = [\"2026-10\"]\n[streams.text]\nroles = []"), &d, "extract").is_ok());
        let tpl = toml_to_json(toml::Value::Table(toml::from_str(TEMPLATE).unwrap()));
        assert!(check(&tpl["extract"], &d, "extract").is_ok());
    }

    #[test]
    fn template_is_the_defaults() {
        let t = toml_to_json(toml::Value::Table(toml::from_str(TEMPLATE).unwrap()));
        assert_eq!(merge(&defaults(), &t), defaults());
    }
}
