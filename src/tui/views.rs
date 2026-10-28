//! What each view of `relic-extract tui` draws.

use ratatui::Frame;
use ratatui::layout::{Constraint, Layout, Rect};
use ratatui::style::{Color, Modifier, Style};
use ratatui::text::{Line, Span};
use ratatui::widgets::{Block, Cell, Paragraph, Row, Table, TableState, Wrap};

use super::App;
use crate::analyse::{Side, head, mchars};
use crate::extract::num;

const CYAN: Color = Color::Rgb(0, 214, 255);
const PINK: Color = Color::Rgb(255, 60, 172);
const AMBER: Color = Color::Rgb(255, 184, 0);
const DIM: Color = Color::Rgb(128, 136, 156);
pub(super) const TABS: [&str; 6] = ["overview", "months", "rules", "biggest", "repeats", "repos"];

pub(super) fn draw(f: &mut Frame, app: &App) {
    let [top, body, foot] = Layout::vertical([Constraint::Length(2), Constraint::Min(3), Constraint::Length(1)]).areas(f.area());
    let a = &app.r.base;
    let first = a.months.keys().next().cloned().unwrap_or_default();
    let last = a.months.keys().last().cloned().unwrap_or_default();
    let mut tabs = vec![Span::styled(
        " relic-extract ",
        Style::new().fg(PINK).add_modifier(Modifier::BOLD),
    )];
    for (i, t) in TABS.iter().enumerate() {
        let st = if i == app.tab {
            Style::new().fg(CYAN).add_modifier(Modifier::BOLD | Modifier::UNDERLINED)
        } else {
            Style::new().fg(DIM)
        };
        tabs.push(Span::styled(format!(" {} {t} ", i + 1), st));
    }
    let what = if app.r.sets.is_empty() {
        String::new()
    } else {
        format!(" · what-if: {}", app.r.sets.join(" · "))
    };
    let info = format!(
        " {} · {} months ({first} … {last}) · {}{what}",
        app.r.name, app.r.n_months, app.r.source
    );
    f.render_widget(
        Paragraph::new(vec![Line::from(tabs), Line::styled(info, Style::new().fg(DIM))]),
        top,
    );
    match app.tab {
        0 => overview(f, body, app),
        1 => months(f, body, app),
        2 => rules(f, body, app),
        3 => biggest(f, body, app),
        4 => repeats(f, body, app),
        _ => repos(f, body, app),
    }
    let foot_text = match &app.input {
        Some(b) => Line::from(vec![
            Span::styled(" what-if> ", Style::new().fg(AMBER)),
            Span::raw(b.clone()),
            Span::raw("▏"),
        ]),
        None if !app.msg.is_empty() => Line::styled(format!(" {}", app.msg), Style::new().fg(AMBER)),
        None => Line::styled(
            " ↑↓ choose · ←→ sample · PgUp/PgDn scroll · 1-6 Tab view · w what-if (min_chars=24 ; max_chars=2000) · q quit",
            Style::new().fg(DIM),
        ),
    };
    f.render_widget(Paragraph::new(foot_text), foot);
}

fn count_row(label: &str, a: usize, b: Option<usize>) -> Row<'static> {
    let mut cells = vec![Cell::from(label.to_string()), Cell::from(format!("{:>12}", num(a)))];
    if let Some(b) = b {
        let d = b as i64 - a as i64;
        let st = if d == 0 {
            Style::new().fg(DIM)
        } else if d < 0 {
            Style::new().fg(PINK)
        } else {
            Style::new().fg(CYAN)
        };
        let pct = if a > 0 {
            format!("{:+.1}%", d as f64 * 100.0 / a as f64)
        } else {
            String::new()
        };
        cells.push(Cell::from(format!("{:>12}", num(b))));
        cells.push(Cell::from(format!("{d:>+11} {pct:>7}")).style(st));
    }
    Row::new(cells)
}

fn overview(f: &mut Frame, area: Rect, app: &App) {
    let (a, b) = (&app.r.base, app.r.alt.as_ref());
    let v = |g: fn(&Side) -> usize| b.map(g);
    let rows = vec![
        count_row("messages (raw cut)", a.raw_msgs, v(|s| s.raw_msgs)),
        count_row(
            "  other roles",
            a.other_roles.values().sum(),
            v(|s| s.other_roles.values().sum()),
        ),
        count_row(
            "  cleaned out",
            a.drops.values().map(|d| d.0).sum(),
            v(|s| s.drops.values().map(|d| d.0).sum()),
        ),
        count_row("  kept", a.kept, v(|s| s.kept)),
        count_row("chunks (raw cut)", a.raw_chunks, v(|s| s.raw_chunks)),
        count_row("chunks", a.chunks, v(|s| s.chunks)),
        count_row("distinct texts", a.distinct.len(), v(|s| s.distinct.len())),
        count_row("chunks without a session", a.no_session, v(|s| s.no_session)),
    ];
    let on = b.is_some();
    let head_row = Row::new([
        "",
        "         now",
        if on { "     what-if" } else { "" },
        if on { "     change" } else { "" },
    ])
    .style(Style::new().fg(DIM));
    let [t, rest] = Layout::vertical([Constraint::Length(11), Constraint::Min(1)]).areas(area);
    let w = [
        Constraint::Length(28),
        Constraint::Length(13),
        Constraint::Length(13),
        Constraint::Length(20),
    ];
    f.render_widget(
        Table::new(rows, w)
            .header(head_row)
            .block(Block::bordered().title(" totals ")),
        t,
    );
    let h = &a.hist;
    let red: Vec<String> = a.redacted.iter().map(|(k, n)| format!("{k} {}", num(*n))).collect();
    let lines = vec![
        Line::from(format!(
            "M chars          {} raw -> {} kept{}",
            mchars(a.raw_chars),
            mchars(a.chars),
            b.map_or(String::new(), |b| format!(" (what-if {})", mchars(b.chars)))
        )),
        Line::from(format!(
            "chunks/message   1: {} · 2: {} · 3-5: {} · 6-10: {} · >10: {}",
            num(h[0]),
            num(h[1]),
            num(h[2]),
            num(h[3]),
            num(h[4])
        )),
        Line::from(format!(
            "kept messages    {} had a stripped tag ({} M chars removed) · {} slash commands kept as \"/x args\"",
            num(a.stripped.0),
            mchars(a.stripped.1),
            num(a.commands)
        )),
        Line::from(format!(
            "redacted         {}",
            if red.is_empty() { "nothing".into() } else { red.join(" · ") }
        )),
        Line::styled(format!("computed in {:.1}s", app.r.secs), Style::new().fg(DIM)),
    ];
    f.render_widget(Paragraph::new(lines).block(Block::bordered().title(" the clean step ")), rest);
}

fn table(f: &mut Frame, area: Rect, title: &str, head: Row<'static>, rows: Vec<Row<'static>>, w: Vec<Constraint>, sel: usize) {
    let mut st = TableState::default().with_selected(Some(sel));
    let t = Table::new(rows, w)
        .header(head.style(Style::new().fg(DIM)))
        .block(Block::bordered().title(title.to_string()))
        .row_highlight_style(Style::new().fg(CYAN).add_modifier(Modifier::BOLD))
        .highlight_symbol("▶ ");
    f.render_stateful_widget(t, area, &mut st);
}

fn bar(share: f64, width: usize) -> String {
    let n = (share * width as f64).round() as usize;
    format!("{}{}", "█".repeat(n.min(width)), "·".repeat(width - n.min(width)))
}

fn months(f: &mut Frame, area: Rect, app: &App) {
    let rows: Vec<Row> = app
        .r
        .base
        .months
        .iter()
        .map(|(m, v)| {
            let share = if v[0] > 0 { v[4] as f64 / v[0] as f64 } else { 0.0 };
            Row::new(vec![
                Cell::from(m.clone()),
                Cell::from(format!("{:>10}", num(v[0]))),
                Cell::from(format!("{:>10}", num(v[1]))),
                Cell::from(format!("{:>10}", num(v[2]))),
                Cell::from(format!("{:>10}", num(v[3]))),
                Cell::from(format!("{:>10}", num(v[4]))),
                Cell::from(format!("{} {:>4.1}%", bar(share, 20), share * 100.0)).style(Style::new().fg(PINK)),
            ])
        })
        .collect();
    let head_row = Row::new([
        "month",
        "  raw msgs",
        "      kept",
        "    chunks",
        "  distinct",
        "   cleaned",
        "share cleaned out",
    ]);
    let w = vec![
        Constraint::Length(9),
        Constraint::Length(11),
        Constraint::Length(11),
        Constraint::Length(11),
        Constraint::Length(11),
        Constraint::Length(11),
        Constraint::Fill(1),
    ];
    table(f, area, " by month ", head_row, rows, w, app.sel[1]);
}

fn detail(f: &mut Frame, area: Rect, title: String, text: &str, scroll: u16) {
    let p = Paragraph::new(text.to_string())
        .wrap(Wrap { trim: false })
        .scroll((scroll, 0))
        .block(Block::bordered().title(title).title_style(Style::new().fg(AMBER)));
    f.render_widget(p, area);
}

fn rules(f: &mut Frame, area: Rect, app: &App) {
    let [l, r] = Layout::horizontal([Constraint::Percentage(55), Constraint::Percentage(45)]).areas(area);
    let labels = app.r.rules();
    let alt = app.r.alt.as_ref();
    let rows: Vec<Row> = labels
        .iter()
        .map(|lb| {
            let (n, ch, _) = app.r.base.drops.get(lb).cloned().unwrap_or_default();
            let mut c = vec![
                Cell::from(head(lb, 44)),
                Cell::from(format!("{:>9}", num(n))),
                Cell::from(format!("{:>7}", mchars(ch))),
            ];
            if let Some(b) = alt {
                let m = b.drops.get(lb).map_or(0, |d| d.0);
                let st = if m == n { Style::new().fg(DIM) } else { Style::new().fg(PINK) };
                c.push(Cell::from(format!("{:>9}", num(m))).style(st));
            }
            Row::new(c)
        })
        .collect();
    let head_row = Row::new(["rule", " messages", "M chars", if alt.is_some() { "  what-if" } else { "" }]);
    let w = vec![
        Constraint::Fill(1),
        Constraint::Length(10),
        Constraint::Length(8),
        Constraint::Length(10),
    ];
    let sel = app.sel[2].min(labels.len().saturating_sub(1));
    table(f, l, " clean rules (messages taken out) ", head_row, rows, w, sel);
    let samples = labels.get(sel).and_then(|lb| app.r.base.drops.get(lb)).map(|d| &d.2);
    match samples {
        Some(s) if !s.is_empty() => {
            let i = app.sample.min(s.len() - 1);
            detail(
                f,
                r,
                format!(" sample {}/{} · raw text, as it was before the clean step ", i + 1, s.len()),
                &s[i],
                app.scroll,
            );
        }
        _ => detail(
            f,
            r,
            " sample ".into(),
            "(none: this rule takes nothing out with the current settings)",
            0,
        ),
    }
}

fn biggest(f: &mut Frame, area: Rect, app: &App) {
    let [l, r] = Layout::horizontal([Constraint::Percentage(45), Constraint::Percentage(55)]).areas(area);
    let big = app.biggest();
    let rows: Vec<Row> = big
        .iter()
        .map(|(n, ch, m, role, t)| {
            Row::new(vec![
                Cell::from(format!("{n:>5}")),
                Cell::from(format!("{:>6}", mchars(*ch))),
                Cell::from(m.clone()),
                Cell::from(role.clone()),
                Cell::from(head(t, 40)),
            ])
        })
        .collect();
    let head_row = Row::new(["chunks", "M chr", "month", "role", "text"]);
    let w = vec![
        Constraint::Length(6),
        Constraint::Length(7),
        Constraint::Length(8),
        Constraint::Length(10),
        Constraint::Fill(1),
    ];
    let sel = app.sel[3].min(big.len().saturating_sub(1));
    table(f, l, " messages that make more than 10 chunks ", head_row, rows, w, sel);
    let text = big.get(sel).map_or("(none)", |b| b.4.as_str());
    detail(f, r, " text (first 8,000 characters) ".into(), text, app.scroll);
}

fn repeats(f: &mut Frame, area: Rect, app: &App) {
    let rep = app.r.repeats();
    let rows: Vec<Row> = rep
        .iter()
        .map(|(n, t)| Row::new(vec![Cell::from(format!("{:>8}", num(*n))), Cell::from(t.clone())]))
        .collect();
    let w = vec![Constraint::Length(9), Constraint::Fill(1)];
    table(
        f,
        area,
        " most repeated chunk texts (rows holding one text) ",
        Row::new(["    rows", "text"]),
        rows,
        w,
        app.sel[4],
    );
}

fn repos(f: &mut Frame, area: Rect, app: &App) {
    let c = app.r.repos();
    let max = c.first().map_or(1, |x| x.1.max(1));
    let rows: Vec<Row> = c
        .iter()
        .map(|(k, n)| {
            Row::new(vec![
                Cell::from(head(k, 50)),
                Cell::from(format!("{:>9}", num(*n))),
                Cell::from(bar(*n as f64 / max as f64, 30)).style(Style::new().fg(CYAN)),
            ])
        })
        .collect();
    let w = vec![Constraint::Fill(1), Constraint::Length(10), Constraint::Length(31)];
    table(
        f,
        area,
        " chunks per repository ",
        Row::new(["repository", "   chunks", ""]),
        rows,
        w,
        app.sel[5],
    );
}
