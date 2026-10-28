//! The clean step: what a message's text becomes before it is cut, for a stream with `[extract.streams.<s>.clean]`.
//!
//! ```text
//!   1. strip_tags      remove <tag>…</tag> blocks anywhere (an unclosed one runs to the end)
//!   2. commands        a slash-command wrapper (<command-name>/x … <command-args>a</command-args>):
//!                      "args" -> "/x a" (dropped when there are no args) · "drop" · "keep" as it is
//!   3. trim            whitespace and zero-width characters at both ends
//!   4. drop            empty · no visible character (drop_invisible) · starts with <tag of drop_tags ·
//!                      starts with one of drop_prefixes · is exactly one of drop_texts (status chatter) ·
//!                      fewer than min_chars characters · more than drop_over_chars (a pasted dump)
//!   5. redact          secrets relic-scan's 10 rules miss: WireGuard keys, Telegram bot tokens, Discord webhooks
//!                      and bot tokens, written as [kind:abcd…N] like relic-scan does
//! ```
//!
//! msgkey stays on the raw row (relic4 finds the message by it); key is the blake2b of the cleaned chunk.
//! Measured on m5 (docs/SCAN-INPUT.md): about 18 % of text chunks are harness text.

use std::borrow::Cow;

use regex::Regex;
use serde_json::Value;

#[derive(Clone, Copy, PartialEq, Debug)]
pub enum Commands {
    Args,
    Drop,
    Keep,
}

/// Why a message was dropped, for the run's log.
#[derive(Clone, Copy, PartialEq, Eq, Hash, Debug, PartialOrd, Ord)]
pub enum Drop {
    Empty,
    Invisible,
    Tag(usize),    // index in drop_tags
    Prefix(usize), // index in drop_prefixes
    Command,
    Short,
    Stripped,    // nothing left once strip_tags ran
    Text(usize), // index in drop_texts
    Long,
}

impl Drop {
    /// The log's word for it: "tag", "prefix", …
    pub fn class(&self) -> &'static str {
        match self {
            Drop::Empty => "empty",
            Drop::Invisible => "invisible",
            Drop::Tag(_) => "tag",
            Drop::Prefix(_) => "prefix",
            Drop::Command => "command",
            Drop::Short => "short",
            Drop::Stripped => "stripped",
            Drop::Text(_) => "text",
            Drop::Long => "long",
        }
    }
}

/// What the clean step did to a message it kept (for `analyse`).
#[derive(Default, Debug)]
pub struct Trace {
    pub stripped_chars: usize, // characters removed by strip_tags
    pub command: bool,         // a slash-command wrapper rewritten to "/x args"
    pub redacted: Vec<&'static str>,
}

pub struct Clean {
    strip: Vec<Regex>,
    commands: Commands,
    drop_tags: Vec<String>,
    drop_prefixes: Vec<String>,
    drop_texts: Vec<String>,
    drop_invisible: bool,
    min_chars: usize,
    drop_over_chars: usize,
    redact: bool,
}

fn strs(v: &Value) -> Vec<String> {
    v.as_array()
        .map(|a| a.iter().filter_map(|x| x.as_str().map(str::to_string)).collect())
        .unwrap_or_default()
}

fn invisible(c: char) -> bool {
    c.is_whitespace() || matches!(c, '\u{200B}'..='\u{200F}' | '\u{2060}' | '\u{FEFF}' | '\u{00AD}' | '\u{180E}')
}

struct Rule {
    kind: &'static str,
    rx: Regex,
    group: usize,
}

fn rules() -> &'static Vec<Rule> {
    static R: std::sync::OnceLock<Vec<Rule>> = std::sync::OnceLock::new();
    R.get_or_init(|| {
        let r = |p: &str| Regex::new(p).expect("redact rule");
        vec![
            Rule {
                kind: "wireguard-key",
                rx: r(r"(?i)\bPrivateKey\s*=\s*([A-Za-z0-9+/]{42,43}=)"),
                group: 1,
            },
            Rule {
                kind: "telegram-token",
                rx: r(r"\b([0-9]{8,10}:AA[A-Za-z0-9_-]{33})\b"),
                group: 1,
            },
            Rule {
                kind: "discord-webhook",
                rx: r(r"discord(?:app)?\.com/api/webhooks/[0-9]+/([A-Za-z0-9_-]{50,})"),
                group: 1,
            },
            Rule {
                kind: "discord-token",
                rx: r(r"\b([MN][A-Za-z0-9_-]{23,25}\.[A-Za-z0-9_-]{6}\.[A-Za-z0-9_-]{27,38})\b"),
                group: 1,
            },
        ]
    })
}

/// The extra redaction: each secret becomes [kind:first4…len].
#[cfg(test)]
pub fn redact(s: &str) -> Cow<'_, str> {
    redact_traced(s, &mut vec![])
}

fn redact_traced<'a>(s: &'a str, kinds: &mut Vec<&'static str>) -> Cow<'a, str> {
    let mut cur: Cow<str> = Cow::Borrowed(s);
    for rule in rules() {
        if !rule.rx.is_match(&cur) {
            continue;
        }
        let mut out = String::with_capacity(cur.len());
        let mut last = 0;
        for caps in rule.rx.captures_iter(&cur) {
            let m = caps.get(rule.group).unwrap();
            out.push_str(&cur[last..m.start()]);
            let v = m.as_str();
            let head: String = v.chars().take(4).collect();
            out.push_str(&format!("[{}:{}…{}]", rule.kind, head, v.chars().count()));
            kinds.push(rule.kind);
            last = m.end();
        }
        out.push_str(&cur[last..]);
        cur = Cow::Owned(out);
    }
    cur
}

fn tag_body<'a>(s: &'a str, tag: &str) -> Option<&'a str> {
    let open = format!("<{tag}>");
    let a = s.find(&open)? + open.len();
    let z = s[a..].find(&format!("</{tag}>")).map_or(s.len(), |z| a + z);
    Some(s[a..z].trim())
}

impl Clean {
    /// The stream's clean settings, or None when it has none or `enabled = false`.
    pub fn from(st: &Value) -> Option<Clean> {
        let c = &st["clean"];
        if c["enabled"] != true {
            return None;
        }
        let strip = strs(&c["strip_tags"])
            .iter()
            .map(|t| Regex::new(&format!(r"(?s)<{0}>.*?(?:</{0}>|\z)", regex::escape(t))).expect("tag"))
            .collect();
        let commands = match c["commands"].as_str() {
            Some("drop") => Commands::Drop,
            Some("keep") => Commands::Keep,
            _ => Commands::Args,
        };
        Some(Clean {
            strip,
            commands,
            drop_tags: strs(&c["drop_tags"]),
            drop_prefixes: strs(&c["drop_prefixes"]),
            drop_texts: strs(&c["drop_texts"]),
            drop_invisible: c["drop_invisible"] != false,
            min_chars: c["min_chars"].as_u64().unwrap_or(0) as usize,
            drop_over_chars: c["drop_over_chars"].as_u64().unwrap_or(0) as usize,
            redact: c["redact"] != false,
        })
    }

    /// A label for a drop: "tag <task-notification>", "prefix \"Stop hook feedback:\"", "invisible", …
    pub fn label(&self, d: &Drop) -> String {
        match d {
            Drop::Tag(i) => format!("tag <{}>", self.drop_tags[*i]),
            Drop::Prefix(i) => format!("prefix {:?}", self.drop_prefixes[*i]),
            Drop::Command => "command without args".into(),
            Drop::Short => format!("shorter than {} chars", self.min_chars),
            Drop::Stripped => "only stripped tags".into(),
            Drop::Text(i) => format!("text {:?}", self.drop_texts[*i]),
            Drop::Long => format!("longer than {} chars", self.drop_over_chars),
            other => other.class().into(),
        }
    }

    /// The text to cut, or why the message is dropped.
    pub fn apply<'a>(&self, raw: &'a str) -> Result<Cow<'a, str>, Drop> {
        self.apply_traced(raw, &mut Trace::default())
    }

    /// `apply`, also saying what it stripped, rewrote and redacted.
    pub fn apply_traced<'a>(&self, raw: &'a str, tr: &mut Trace) -> Result<Cow<'a, str>, Drop> {
        let mut s: Cow<str> = Cow::Borrowed(raw);
        for rx in &self.strip {
            if rx.is_match(&s) {
                let before = s.chars().count();
                s = Cow::Owned(rx.replace_all(&s, "").into_owned());
                tr.stripped_chars += before - s.chars().count();
            }
        }
        let head = s.trim_start_matches(invisible);
        if head.starts_with("<command-message>") || head.starts_with("<command-name>") {
            match self.commands {
                Commands::Drop => return Err(Drop::Command),
                Commands::Keep => {}
                Commands::Args => {
                    let name = tag_body(head, "command-name").unwrap_or("");
                    let args = tag_body(head, "command-args").unwrap_or("");
                    if args.is_empty() {
                        return Err(Drop::Command);
                    }
                    s = Cow::Owned(format!("{name} {args}").trim().to_string());
                    tr.command = true;
                }
            }
        }
        let t = s.trim_matches(invisible);
        if t.is_empty() {
            return Err(if raw.trim().is_empty() {
                Drop::Empty
            } else if tr.stripped_chars > 0
                && raw.len() != s.len()
                && !raw.trim_matches(invisible).is_empty()
                && s.trim_matches(invisible).is_empty()
                && matches!(s, Cow::Owned(_))
            {
                Drop::Stripped
            } else if self.drop_invisible {
                Drop::Invisible
            } else {
                return Ok(s); // drop_invisible = false: a zero-width-only reply is kept as it is
            });
        }
        if let Some(i) = self.drop_tags.iter().position(|g| {
            t.strip_prefix('<')
                .and_then(|r| r.strip_prefix(g.as_str()))
                .is_some_and(|r| r.starts_with('>') || r.starts_with(char::is_whitespace))
        }) {
            return Err(Drop::Tag(i));
        }
        if let Some(i) = self.drop_prefixes.iter().position(|p| t.starts_with(p.as_str())) {
            return Err(Drop::Prefix(i));
        }
        if let Some(i) = self.drop_texts.iter().position(|x| t == x.as_str()) {
            return Err(Drop::Text(i));
        }
        if self.min_chars > 0 && t.chars().count() < self.min_chars {
            return Err(Drop::Short);
        }
        // bytes bound characters from above: count only when the bytes could exceed the limit
        if self.drop_over_chars > 0 && t.len() > self.drop_over_chars && t.chars().count() > self.drop_over_chars {
            return Err(Drop::Long);
        }
        let t = if t.len() == s.len() { s } else { Cow::Owned(t.to_string()) };
        Ok(if self.redact {
            match redact_traced(&t, &mut tr.redacted) {
                Cow::Borrowed(_) => t,
                Cow::Owned(o) => Cow::Owned(o),
            }
        } else {
            t
        })
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn c() -> Clean {
        Clean::from(
            &json!({"clean": {"enabled": true, "strip_tags": ["system-reminder"], "commands": "args",
            "drop_tags": ["task-notification", "local-command-stdout"], "drop_prefixes": ["Stop hook feedback:"],
            "min_chars": 0}}),
        )
        .unwrap()
    }

    #[test]
    fn strips_reminders_and_keeps_the_human() {
        let t = "<system-reminder>\nrules\n</system-reminder>\nfix the bug please";
        assert_eq!(c().apply(t).unwrap(), "fix the bug please");
        assert_eq!(c().apply("<system-reminder>only</system-reminder>"), Err(Drop::Stripped));
        assert_eq!(c().apply("hi <system-reminder>never closed"), Ok(Cow::Borrowed("hi")));
    }

    #[test]
    fn commands_keep_their_args() {
        let t = "<command-message>goal</command-message>\n<command-name>/goal</command-name>\n<command-args>full analyse</command-args>";
        assert_eq!(c().apply(t).unwrap(), "/goal full analyse");
        assert_eq!(
            c().apply(
                "<command-name>/model</command-name>\n<command-message>model</command-message>\n<command-args></command-args>"
            ),
            Err(Drop::Command)
        );
    }

    #[test]
    fn drops_harness_turns() {
        assert_eq!(c().apply("<task-notification>\n<task-id>x</task-id>"), Err(Drop::Tag(0)));
        assert_eq!(
            c().apply("<local-command-stdout>Goal set</local-command-stdout>"),
            Err(Drop::Tag(1))
        );
        assert_eq!(
            c().apply("<task-notifications-are-not-this>"),
            Ok(Cow::Borrowed("<task-notifications-are-not-this>"))
        );
        assert_eq!(c().apply("Stop hook feedback: [x]"), Err(Drop::Prefix(0)));
        assert_eq!(c().label(&Drop::Tag(0)), "tag <task-notification>");
        assert_eq!(c().apply("\u{200E}"), Err(Drop::Invisible));
        assert_eq!(c().apply("\u{200B} \n"), Err(Drop::Invisible));
        assert_eq!(c().apply("ทำต่อ"), Ok(Cow::Borrowed("ทำต่อ")));
        let mut keep = c();
        keep.drop_invisible = false;
        assert_eq!(keep.apply("\u{200E}").unwrap(), "\u{200E}");
    }

    #[test]
    fn min_chars_counts_characters() {
        let mut k = c();
        k.min_chars = 5;
        assert_eq!(k.apply("ทำต่อ").unwrap(), "ทำต่อ"); // 5 characters, 15 bytes
        assert_eq!(k.apply("ok"), Err(Drop::Short));
    }

    #[test]
    fn drops_status_chatter_and_dumps() {
        let mut k = c();
        k.drop_texts = vec!["(holding)".into()];
        k.drop_over_chars = 5;
        assert_eq!(k.apply("  (holding)\n"), Err(Drop::Text(0)));
        assert_eq!(k.apply("(holding) the rest"), Err(Drop::Long));
        assert_eq!(k.apply("ทำต่อนะ").unwrap_err(), Drop::Long); // 7 characters
        assert_eq!(k.apply("ทำต่อ").unwrap(), "ทำต่อ"); // 5 characters, 15 bytes: kept
        assert_eq!(k.label(&Drop::Text(0)), "text \"(holding)\"");
        assert_eq!(k.label(&Drop::Long), "longer than 5 chars");
    }

    #[test]
    fn redacts_what_relic_scan_misses() {
        let wg = format!("[Interface]\nPrivateKey = {}=\n", "A".repeat(43));
        assert_eq!(redact(&wg), "[Interface]\nPrivateKey = [wireguard-key:AAAA…44]\n");
        let tg = format!("bot {}:AA{} ok", "123456789", "b".repeat(33));
        assert_eq!(redact(&tg), "bot [telegram-token:1234…45] ok");
        let wh = format!("https://discord.com/api/webhooks/123/{}", "x".repeat(60));
        assert_eq!(redact(&wh), "https://discord.com/api/webhooks/123/[discord-webhook:xxxx…60]");
        assert_eq!(redact("nothing here"), "nothing here");
    }

    #[test]
    fn off_unless_enabled() {
        assert!(Clean::from(&json!({})).is_none());
        assert!(Clean::from(&json!({"clean": {"enabled": false}})).is_none());
    }
}
