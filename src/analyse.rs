//! `relic-extract analyse`: what a run would cut, and what the clean step takes out, without writing anything.
//!
//! It reads the relic-scan months and runs the same select / clean / cut as `run`, in memory. With `--what-if
//! KEY=VALUE` (repeatable) it does the same pass with the changed settings too and prints both side by side, so a
//! setting can be tried before it goes into ~/.arra/relic.toml.
//!
//!   totals        raw cut (every role, no clean) -> other roles -> cleaned out -> kept, chunks, distinct, chars
//!   by month      raw messages, kept, chunks, distinct texts, cleaned out
//!   clean rules   messages and characters each rule takes out, with a sample; what strip_tags, commands and
//!                 redact did to the messages that stay
//!   chunks        chunks per message, the messages that make the most chunks
//!   repeats       the texts held by the most rows (a loop shows up here first)
//!   sessions      chunks without a session, chunks per repository

use std::collections::{BTreeMap, HashMap};

use crate::clean::{Clean, Trace};
use crate::config::Config;
use crate::extract::{self, num};

/// What one clean rule took out: (messages, characters, samples of the raw text).
pub type Dropped = (usize, usize, Vec<String>);

/// One message that makes many chunks: (chunks, chars, month, role, its text up to SAMPLE_CHARS).
pub type Big = (usize, usize, String, String, String);

/// How much of a sample's text is kept, for the TUI's detail pane.
pub const SAMPLE_CHARS: usize = 8000;

/// Everything one pass counts, for one set of settings.
#[derive(Default)]
pub struct Side {
    pub raw_msgs: usize,
    pub raw_chunks: usize,
    pub raw_chars: usize,
    pub other_roles: BTreeMap<String, usize>,
    pub drops: BTreeMap<String, Dropped>,
    pub kept: usize,
    pub chunks: usize,
    pub chars: usize,
    pub distinct: HashMap<String, (usize, String)>, // key -> (rows, head)
    pub stripped: (usize, usize),                   // (messages, chars)
    pub commands: usize,
    pub redacted: BTreeMap<&'static str, usize>,
    pub hist: [usize; 5],
    pub biggest: Vec<(usize, usize, String, String, String)>, // (chunks, chars, month, role, head)
    pub no_session: usize,
    pub cats: HashMap<String, usize>,
    pub months: BTreeMap<String, [usize; 5]>, // raw, kept, chunks, distinct, cleaned out
}

impl Side {
    /// Add one month's numbers (months merged in order, so samples come from the earliest months, as in one pass).
    fn merge(&mut self, o: Side, samples: usize) {
        self.raw_msgs += o.raw_msgs;
        self.raw_chunks += o.raw_chunks;
        self.raw_chars += o.raw_chars;
        for (k, v) in o.other_roles {
            *self.other_roles.entry(k).or_default() += v;
        }
        for (k, (n, ch, sm)) in o.drops {
            let e = self.drops.entry(k).or_default();
            e.0 += n;
            e.1 += ch;
            for x in sm {
                if e.2.len() < samples {
                    e.2.push(x);
                }
            }
        }
        self.kept += o.kept;
        self.chunks += o.chunks;
        self.chars += o.chars;
        for (k, (n, h)) in o.distinct {
            self.distinct.entry(k).or_insert((0, h)).0 += n;
        }
        self.stripped.0 += o.stripped.0;
        self.stripped.1 += o.stripped.1;
        self.commands += o.commands;
        for (k, v) in o.redacted {
            *self.redacted.entry(k).or_default() += v;
        }
        for (a, b) in self.hist.iter_mut().zip(o.hist) {
            *a += b;
        }
        self.biggest.extend(o.biggest);
        self.no_session += o.no_session;
        for (k, v) in o.cats {
            *self.cats.entry(k).or_default() += v;
        }
        self.months.extend(o.months);
    }
}

/// One line, at most `n` characters, for a sample.
pub fn head(s: &str, n: usize) -> String {
    let one: String = s.split_whitespace().collect::<Vec<_>>().join(" ");
    if one.chars().count() <= n {
        one
    } else {
        format!("{}…", one.chars().take(n).collect::<String>())
    }
}

pub fn mchars(n: usize) -> String {
    format!("{:.1}", n as f64 / 1e6)
}

fn bucket(n: usize) -> usize {
    match n {
        1 => 0,
        2 => 1,
        3..=5 => 2,
        6..=10 => 3,
        _ => 4,
    }
}

fn month_pass(
    cfg: &Config,
    name: &str,
    month: &str,
    batches: &[crate::read::Cols],
    sess: &extract::Sessions,
    samples: usize,
    side: &mut Side,
) {
    let st = cfg.stream(name);
    let roles: Vec<&str> = st["roles"]
        .as_array()
        .map(|a| a.iter().filter_map(|x| x.as_str()).collect())
        .unwrap_or_default();
    let clean = Clean::from(st);
    let mut month_keys = std::collections::HashSet::new();
    let mut m = [0usize; 5];
    for p in extract::select(cfg, name, batches, true) {
        let (c, i) = (&batches[p.row.0], p.row.1);
        let raw = c.field.get(i).unwrap_or("");
        side.raw_msgs += 1;
        m[0] += 1;
        side.raw_chars += raw.chars().count();
        side.raw_chunks += extract::pieces(cfg, name, raw).len();
        let role = c.role.get(i).unwrap_or("");
        if !roles.is_empty() && !roles.contains(&role) {
            *side
                .other_roles
                .entry(if role.is_empty() { "(none)".into() } else { role.to_string() })
                .or_default() += 1;
            m[4] += 1;
            continue;
        }
        let mut tr = Trace::default();
        let body = match &clean {
            None => std::borrow::Cow::Borrowed(raw),
            Some(k) => match k.apply_traced(raw, &mut tr) {
                Ok(t) => t,
                Err(d) => {
                    let e = side.drops.entry(k.label(&d)).or_default();
                    e.0 += 1;
                    e.1 += raw.chars().count();
                    if e.2.len() < samples {
                        e.2.push(extract::clip(raw, SAMPLE_CHARS).to_string());
                    }
                    m[4] += 1;
                    continue;
                }
            },
        };
        if tr.stripped_chars > 0 {
            side.stripped.0 += 1;
            side.stripped.1 += tr.stripped_chars;
        }
        side.commands += tr.command as usize;
        for k in tr.redacted {
            *side.redacted.entry(k).or_default() += 1;
        }
        let pieces = extract::pieces(cfg, name, &body);
        side.kept += 1;
        m[1] += 1;
        side.chars += body.chars().count();
        side.hist[bucket(pieces.len())] += 1;
        if pieces.len() > 10 {
            side.biggest.push((
                pieces.len(),
                body.chars().count(),
                month.to_string(),
                role.to_string(),
                extract::clip(&body, SAMPLE_CHARS).to_string(),
            ));
        }
        let cat = c
            .sid
            .get(i)
            .and_then(|s| sess.get(s))
            .and_then(|x| x.0.clone())
            .unwrap_or("(no session)".into());
        for piece in &pieces {
            let k = extract::key(piece);
            month_keys.insert(k.clone());
            let e = side.distinct.entry(k).or_insert_with(|| (0, head(piece, 60)));
            e.0 += 1;
            if cat == "(no session)" {
                side.no_session += 1;
            }
            *side.cats.entry(cat.clone()).or_default() += 1;
        }
        side.chunks += pieces.len();
        m[2] += pieces.len();
    }
    m[3] = month_keys.len();
    side.months.insert(month.to_string(), m);
}

fn row(label: &str, a: usize, b: Option<usize>) {
    match b {
        None => println!("  {label:<26} {:>12}", num(a)),
        Some(b) => {
            let d = b as i64 - a as i64;
            let pct = if a > 0 {
                format!("{:+.1}%", d as f64 * 100.0 / a as f64)
            } else {
                String::new()
            };
            println!("  {label:<26} {:>12} {:>12} {:>+12} {pct:>8}", num(a), num(b), d);
        }
    }
}

/// One analyse pass: the base settings, and the what-if settings when there are any.
pub struct Report {
    pub name: String,
    pub source: String,
    pub n_months: usize,
    pub secs: f64,
    pub sets: Vec<String>,
    pub base: Side,
    pub alt: Option<Side>,
}

impl Report {
    /// The clean rules, most messages first, then the ones only the what-if has.
    pub fn rules(&self) -> Vec<String> {
        let mut r: Vec<(&String, &Dropped)> = self.base.drops.iter().collect();
        r.sort_by_key(|x| std::cmp::Reverse(x.1.0));
        let mut labels: Vec<String> = r.iter().map(|x| x.0.clone()).collect();
        if let Some(b) = &self.alt {
            labels.extend(b.drops.keys().filter(|l| !self.base.drops.contains_key(*l)).cloned());
        }
        labels
    }

    /// The texts held by more than one row, most first: (rows, head).
    pub fn repeats(&self) -> Vec<(usize, String)> {
        let mut rep: Vec<(usize, String)> = self.base.distinct.values().filter(|v| v.0 > 1).cloned().collect();
        rep.sort_by_key(|x| std::cmp::Reverse(x.0));
        rep
    }

    /// Chunks per repository, most first.
    pub fn repos(&self) -> Vec<(String, usize)> {
        let mut c: Vec<(String, usize)> = self.base.cats.iter().map(|(k, v)| (k.clone(), *v)).collect();
        c.sort_by(|x, y| y.1.cmp(&x.1).then(x.0.cmp(&y.0)));
        c
    }
}

pub fn analyse(
    base: &Config,
    alt: Option<(&Config, Vec<String>)>,
    months: Option<Vec<String>>,
    name: &str,
    samples: usize,
    top: usize,
) -> Result<(), String> {
    let tty = std::io::IsTerminal::is_terminal(&std::io::stderr());
    let mut r = compute(base, alt, months, name, samples, tty)?;
    print(&mut r, top);
    Ok(())
}

/// The pass itself: every chosen month, in parallel, merged in month order.
pub fn compute(
    base: &Config,
    alt: Option<(&Config, Vec<String>)>,
    months: Option<Vec<String>>,
    name: &str,
    samples: usize,
    tty: bool,
) -> Result<Report, String> {
    let src = extract::months_in(base.source());
    if src.is_empty() {
        return Err(format!("no month files in {}", base.source()));
    }
    if let Some(w) = &months {
        let missing: Vec<&str> = w.iter().filter(|m| !src.contains_key(*m)).map(String::as_str).collect();
        if !missing.is_empty() {
            return Err(format!("no source for month(s) {} in {}", missing.join(", "), base.source()));
        }
    }
    let t0 = std::time::Instant::now();
    let sess_a = extract::sessions(base.source(), base.v["sessions"] != "main")?;
    let sess_b = match &alt {
        Some((c, _)) => Some(extract::sessions(c.source(), c.v["sessions"] != "main")?),
        None => None,
    };
    let todo: Vec<(&String, &std::path::PathBuf)> = src
        .iter()
        .filter(|(m, _)| months.as_ref().is_none_or(|w| w.contains(*m)))
        .collect();
    let n_months = todo.len();
    let done = std::sync::atomic::AtomicUsize::new(0);
    let per = crate::run::par_map(&todo, |(m, f)| -> Result<(Side, Side), String> {
        let batches = extract::read_stream(base, name, f)?;
        let (mut a, mut b) = (Side::default(), Side::default());
        month_pass(base, name, m, &batches, &sess_a, samples, &mut a);
        if let (Some((c, _)), Some(s)) = (&alt, &sess_b) {
            month_pass(c, name, m, &batches, s, 0, &mut b);
        }
        let n = done.fetch_add(1, std::sync::atomic::Ordering::Relaxed) + 1;
        if tty {
            eprint!("\r  analyse {name}: {n}/{n_months} months …");
        }
        Ok((a, b))
    });
    if tty {
        eprint!("\r{:50}\r", "");
    }
    let (mut a, mut b) = (Side::default(), Side::default());
    for r in per {
        let (x, y) = r?;
        a.merge(x, samples);
        b.merge(y, 0);
    }
    Ok(Report {
        name: name.to_string(),
        source: base.source().to_string(),
        n_months,
        secs: t0.elapsed().as_secs_f64(),
        sets: alt.as_ref().map(|(_, s)| s.clone()).unwrap_or_default(),
        base: a,
        alt: alt.is_some().then_some(b),
    })
}

/// The report as text, for `analyse`.
pub fn print(r: &mut Report, top: usize) {
    let (name, n_months) = (r.name.clone(), r.n_months);
    let rules = r.rules();
    let rep = r.repeats();
    let cats = r.repos();
    let alt_on = r.alt.is_some();
    let empty = Side::default();
    let b = r.alt.as_ref().unwrap_or(&empty);
    let a = &r.base;
    let bv = |f: &dyn Fn(&Side) -> usize| alt_on.then(|| f(b));
    let first = a.months.keys().next().cloned().unwrap_or_default();
    let last = a.months.keys().last().cloned().unwrap_or_default();
    println!(
        "relic-extract analyse · {name} · {n_months} months ({first} … {last}) · {} · {:.1}s",
        r.source, r.secs
    );
    if alt_on {
        println!("what-if: {}", r.sets.join(" · "));
        println!("\n  {:<26} {:>12} {:>12} {:>12} {:>8}", "", "now", "what-if", "change", "");
    } else {
        println!();
    }
    row("messages (raw cut)", a.raw_msgs, bv(&|s| s.raw_msgs));
    row(
        "  other roles",
        a.other_roles.values().sum(),
        bv(&|s| s.other_roles.values().sum()),
    );
    row(
        "  cleaned out",
        a.drops.values().map(|d| d.0).sum(),
        bv(&|s| s.drops.values().map(|d| d.0).sum()),
    );
    row("  kept", a.kept, bv(&|s| s.kept));
    row("chunks (raw cut)", a.raw_chunks, bv(&|s| s.raw_chunks));
    row("chunks", a.chunks, bv(&|s| s.chunks));
    row("distinct texts", a.distinct.len(), bv(&|s| s.distinct.len()));
    println!(
        "  {:<26} {:>12}{}",
        "M chars (raw -> kept)",
        format!("{} -> {}", mchars(a.raw_chars), mchars(a.chars)),
        if alt_on {
            format!(" {:>12}", format!("-> {}", mchars(b.chars)))
        } else {
            String::new()
        }
    );
    if !a.other_roles.is_empty() {
        let r: Vec<String> = a.other_roles.iter().map(|(k, v)| format!("{k} {}", num(*v))).collect();
        println!("  other roles: {}", r.join(" · "));
    }

    println!("\nby month       raw msgs        kept      chunks    distinct  cleaned out");
    for (m, v) in &a.months {
        println!(
            "  {m:<9} {:>11} {:>11} {:>11} {:>11} {:>12}",
            num(v[0]),
            num(v[1]),
            num(v[2]),
            num(v[3]),
            num(v[4])
        );
    }

    println!(
        "\nclean rules                                   messages   M chars{}   sample",
        if alt_on { "   what-if" } else { "" }
    );
    for l in &rules {
        let (n, ch, sm) = a.drops.get(l).cloned().unwrap_or_default();
        let sm: Vec<String> = sm.iter().map(|x| head(x, 70)).collect();
        let w = if alt_on {
            format!(" {:>9}", num(b.drops.get(l).map_or(0, |d| d.0)))
        } else {
            String::new()
        };
        println!(
            "  {:<44} {:>9} {:>9}{w}   {}",
            head(l, 44),
            num(n),
            mchars(ch),
            sm.first().cloned().unwrap_or_default()
        );
        for s in sm.iter().skip(1) {
            println!(
                "  {:<44} {:>9} {:>9}{}   {s}",
                "",
                "",
                "",
                if alt_on { " ".repeat(10) } else { String::new() }
            );
        }
    }
    println!(
        "  kept messages: {} had a stripped tag ({} M chars removed) · {} slash commands kept as \"/x args\"",
        num(a.stripped.0),
        mchars(a.stripped.1),
        num(a.commands)
    );
    if !a.redacted.is_empty() {
        let r: Vec<String> = a.redacted.iter().map(|(k, v)| format!("{k} {}", num(*v))).collect();
        println!("  redacted: {}", r.join(" · "));
    }

    let h = &a.hist;
    println!(
        "\nchunks per message   1: {} · 2: {} · 3-5: {} · 6-10: {} · >10: {}",
        num(h[0]),
        num(h[1]),
        num(h[2]),
        num(h[3]),
        num(h[4])
    );
    let mut big: Vec<&Big> = a.biggest.iter().collect();
    big.sort_by_key(|x| std::cmp::Reverse(x.0));
    for (n, ch, m, role, t) in big.iter().take(top) {
        println!("  {:>5} chunks {:>6} M chars  {m} {role:<9} {}", n, mchars(*ch), head(t, 60));
    }

    println!("\nmost repeated texts (rows holding one chunk text)");
    for (n, t) in rep.iter().take(top) {
        println!("  {:>8} ×  {t}", num(*n));
    }

    let c: Vec<String> = cats.iter().take(top).map(|(k, v)| format!("{k} {}", num(*v))).collect();
    println!(
        "\nsessions   chunks without a session: {}{}",
        num(a.no_session),
        if alt_on {
            format!(" (what-if {})", num(b.no_session))
        } else {
            String::new()
        }
    );
    println!("  top repositories by chunks: {}", c.join(" · "));
}
