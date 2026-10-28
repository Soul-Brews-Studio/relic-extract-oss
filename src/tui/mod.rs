//! `relic-extract tui`: the analyse report in the terminal, with what-if settings tried live.
//!
//! ```text
//!   1 overview   totals now / what-if, chunks per message, what the clean step did to the kept messages
//!   2 months     raw messages, kept, chunks, distinct texts, cleaned out, a bar of the share cleaned out
//!   3 rules      each clean rule; the right pane shows its samples, the raw text in full (← → next sample)
//!   4 biggest    the messages that make the most chunks; the right pane shows the text
//!   5 repeats    the texts held by the most rows (a loop shows up here first)
//!   6 repos      chunks per repository
//! ```
//!
//! `w` types a what-if (KEY=VALUE, several joined with ` ; `), Enter recomputes, an empty one clears it.
//! ↑ ↓ choose, PgUp PgDn scroll the right pane, 1-6 or Tab switch, q quits. Nothing is written.
//! No background colour is set, so it reads the same on a dark or a light terminal (as relic-scan's TUI).

use ratatui::DefaultTerminal;
use ratatui::crossterm::event::{self, Event, KeyCode, KeyEventKind};

use crate::analyse::{self, Report};
use crate::config::{self, Config};

mod views;

use views::{TABS, draw};
const SAMPLES: usize = 20;

pub(crate) struct App {
    pub(super) base: Config,
    pub(super) name: String,
    pub(super) months: Option<Vec<String>>,
    pub(super) r: Report,
    pub(super) tab: usize,
    pub(super) sel: [usize; 6],
    pub(super) sample: usize,
    pub(super) scroll: u16,
    pub(super) input: Option<String>,
    pub(super) what_if: String,
    pub(super) msg: String,
}

impl App {
    fn compute(&mut self, what_if: &str) -> Result<(), String> {
        let sets: Vec<&str> = what_if.split(';').map(str::trim).filter(|s| !s.is_empty()).collect();
        let alt = if sets.is_empty() {
            None
        } else {
            let mut v = self.base.v.clone();
            let mut done = vec![];
            for kv in &sets {
                let path = config::set(&mut v, &self.name, kv)?;
                done.push(format!("{path} = {}", kv.split_once('=').map_or("", |x| x.1).trim()));
            }
            Some((
                Config {
                    v,
                    path: self.base.path.clone(),
                    existed: self.base.existed,
                },
                done,
            ))
        };
        self.r = analyse::compute(
            &self.base,
            alt.as_ref().map(|(c, s)| (c, s.clone())),
            self.months.clone(),
            &self.name,
            SAMPLES,
            false,
        )?;
        self.what_if = what_if.to_string();
        Ok(())
    }

    pub(super) fn len(&self) -> usize {
        match self.tab {
            1 => self.r.base.months.len(),
            2 => self.r.rules().len(),
            3 => self.r.base.biggest.len(),
            4 => self.r.repeats().len(),
            5 => self.r.base.cats.len(),
            _ => 0,
        }
    }

    pub(super) fn biggest(&self) -> Vec<&analyse::Big> {
        let mut b: Vec<&analyse::Big> = self.r.base.biggest.iter().collect();
        b.sort_by_key(|x| std::cmp::Reverse(x.0));
        b
    }
}

pub fn tui(cfg: Config, months: Option<Vec<String>>, name: &str, what_if: &str) -> Result<(), String> {
    eprintln!("relic-extract tui: analysing {name} …");
    let r = analyse::compute(&cfg, None, months.clone(), name, SAMPLES, true)?;
    let mut app = App {
        base: cfg,
        name: name.into(),
        months,
        r,
        tab: 0,
        sel: [0; 6],
        sample: 0,
        scroll: 0,
        input: None,
        what_if: String::new(),
        msg: String::new(),
    };
    if !what_if.is_empty() {
        app.compute(what_if)?;
    }
    let mut term = ratatui::init();
    let res = run(&mut term, &mut app);
    ratatui::restore();
    res
}

fn run(term: &mut DefaultTerminal, app: &mut App) -> Result<(), String> {
    loop {
        term.draw(|f| draw(f, app)).map_err(|e| e.to_string())?;
        let Event::Key(k) = event::read().map_err(|e| e.to_string())? else {
            continue;
        };
        if k.kind != KeyEventKind::Press {
            continue;
        }
        if let Some(buf) = app.input.as_mut() {
            match k.code {
                KeyCode::Esc => app.input = None,
                KeyCode::Backspace => {
                    buf.pop();
                }
                KeyCode::Char(c) => buf.push(c),
                KeyCode::Enter => {
                    let w = app.input.take().unwrap_or_default();
                    app.msg = "computing …".into();
                    term.draw(|f| draw(f, app)).map_err(|e| e.to_string())?;
                    app.msg = match app.compute(&w) {
                        Ok(()) if w.trim().is_empty() => "what-if cleared".into(),
                        Ok(()) => format!("what-if: {}", app.r.sets.join(" · ")),
                        Err(e) => format!("✗ {e}"),
                    };
                    app.scroll = 0;
                }
                _ => {}
            }
            continue;
        }
        let n = app.len();
        let t = app.tab;
        match k.code {
            KeyCode::Char('q') | KeyCode::Esc => return Ok(()),
            KeyCode::Char(c @ '1'..='6') => app.tab = c as usize - '1' as usize,
            KeyCode::Tab => app.tab = (app.tab + 1) % TABS.len(),
            KeyCode::BackTab => app.tab = (app.tab + TABS.len() - 1) % TABS.len(),
            KeyCode::Down | KeyCode::Char('j') if n > 0 => app.sel[t] = (app.sel[t] + 1).min(n - 1),
            KeyCode::Up | KeyCode::Char('k') => app.sel[t] = app.sel[t].saturating_sub(1),
            KeyCode::Home | KeyCode::Char('g') => app.sel[t] = 0,
            KeyCode::End | KeyCode::Char('G') => app.sel[t] = n.saturating_sub(1),
            KeyCode::Right | KeyCode::Char('l') => app.sample += 1,
            KeyCode::Left | KeyCode::Char('h') => app.sample = app.sample.saturating_sub(1),
            KeyCode::PageDown | KeyCode::Char(' ') => app.scroll = app.scroll.saturating_add(10),
            KeyCode::PageUp => app.scroll = app.scroll.saturating_sub(10),
            KeyCode::Char('w') => app.input = Some(app.what_if.clone()),
            _ => {}
        }
        if matches!(
            k.code,
            KeyCode::Up | KeyCode::Down | KeyCode::Char('j' | 'k' | 'g' | 'G') | KeyCode::Home | KeyCode::End
        ) || matches!(k.code, KeyCode::Char('1'..='6') | KeyCode::Tab | KeyCode::BackTab)
        {
            app.sample = 0;
            app.scroll = 0;
        }
        if matches!(k.code, KeyCode::Right | KeyCode::Left | KeyCode::Char('h' | 'l')) {
            app.scroll = 0;
        }
    }
}
