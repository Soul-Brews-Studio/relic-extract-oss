//! relic-extract — step 2 of scan -> extract -> embed, as one binary.
//!
//!   relic-extract init        write ~/.arra/relic.toml with the defaults, if it is missing
//!   relic-extract config      the [extract] settings in effect (file merged over the defaults)
//!   relic-extract run         relic-scan months -> OUT/<stream>/YYYY-MM.parquet (only what changed)
//!   relic-extract status      per stream: months cut, chunks, distinct texts, newest ts (--json for scripts)

use clap::{Parser, Subcommand};
use relic_extract::{analyse, config, extract, run, tui};

#[derive(Parser)]
#[command(
    version,
    about = "relic-scan month files -> per-stream, deduped, chunked parts for embedding"
)]
struct Cli {
    /// another config (default ~/.arra/relic.toml, or ARRA_CONFIG)
    #[arg(long, global = true)]
    config: Option<String>,
    #[command(subcommand)]
    cmd: Cmd,
}

#[derive(Subcommand)]
enum Cmd {
    /// Write ~/.arra/relic.toml with the defaults (kept if it exists)
    Init,
    /// The settings in effect: the file merged over the defaults
    Config,
    /// Cut the months that changed (or --months) for every stream that is on (or --stream)
    Run {
        /// only these months, comma separated: 2026-09,2026-10
        #[arg(long)]
        months: Option<String>,
        /// only this stream: text | thinking | tools
        #[arg(long)]
        stream: Option<String>,
        /// cut them even if they look fresh
        #[arg(long)]
        force: bool,
        /// the Python reference's cut: every role, no clean step, main-tier sessions (for `just parity`)
        #[arg(long)]
        raw: bool,
        /// do not run the [extract] after command
        #[arg(long)]
        no_after: bool,
    },
    /// Per stream: months, chunks, distinct texts, newest message
    Status {
        /// one JSON object instead of the table (for scripts: relic5's SYNC check)
        #[arg(long)]
        json: bool,
    },
    /// The analyse report in the terminal: overview, months, clean rules with samples, biggest, repeats, repos
    Tui {
        /// only these months, comma separated: 2026-09,2026-10
        #[arg(long)]
        months: Option<String>,
        /// the stream to look at
        #[arg(long, default_value = "text")]
        stream: String,
        /// start with a what-if: "min_chars=24 ; max_chars=2000"
        #[arg(long = "what-if", value_name = "KEY=VALUE[ ; …]", default_value = "")]
        what_if: String,
    },
    /// What a run would cut and what the clean step takes out, without writing anything
    Analyse {
        /// only these months, comma separated: 2026-09,2026-10
        #[arg(long)]
        months: Option<String>,
        /// the stream to look at
        #[arg(long, default_value = "text")]
        stream: String,
        /// try a setting, repeatable: --what-if min_chars=24 --what-if chunk.max_chars=2000
        #[arg(long = "what-if", value_name = "KEY=VALUE")]
        what_if: Vec<String>,
        /// samples per clean rule
        #[arg(long, default_value_t = 1)]
        samples: usize,
        /// rows in the biggest / repeated / repository lists
        #[arg(long, default_value_t = 8)]
        top: usize,
    },
}

fn fail(e: String) -> ! {
    eprintln!("✗ {e}");
    std::process::exit(1);
}

fn main() {
    let cli = Cli::parse();
    let given = cli.config.as_deref();
    if let Cmd::Init = cli.cmd {
        match config::init(given) {
            Ok((p, made)) => println!("{} {}", if made { "wrote" } else { "kept (already there)" }, p.display()),
            Err(e) => fail(e.to_string()),
        }
        return;
    }
    let mut cfg = config::load(given).unwrap_or_else(|e| fail(e));
    match cli.cmd {
        Cmd::Init => unreachable!(),
        Cmd::Config => {
            println!(
                "# {}{}",
                cfg.path.display(),
                if cfg.existed {
                    ""
                } else {
                    "  (missing: defaults; relic-extract init writes it)"
                }
            );
            println!("{}", run::pretty(&cfg.v));
        }
        Cmd::Run {
            months,
            stream,
            force,
            raw,
            no_after,
        } => {
            if raw {
                cfg.raw();
            }
            let t0 = std::time::Instant::now();
            eprintln!(
                "relic-extract: {} -> {} · config {}{}",
                cfg.source(),
                cfg.out(),
                cfg.path.display(),
                if cfg.existed { "" } else { " (defaults)" }
            );
            let months = months.map(|m| m.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect());
            let done = run::run(&cfg, months, force, stream.as_deref()).unwrap_or_else(|e| fail(e));
            let (chunks, dropped): (usize, usize) = done.iter().fold((0, 0), |a, d| (a.0 + d.chunks, a.1 + d.dropped));
            eprintln!(
                "{} part(s) cut in {:.1}s · {} chunks · {} messages cleaned out",
                done.len(),
                t0.elapsed().as_secs_f64(),
                extract::num(chunks),
                extract::num(dropped)
            );
            if !no_after {
                run::after(&cfg, &done).unwrap_or_else(|e| fail(e));
            }
        }
        Cmd::Status { json } => run::status(&cfg, json),
        Cmd::Tui { months, stream, what_if } => {
            if !config::STREAMS.contains(&stream.as_str()) {
                fail(format!("unknown stream {stream} (known: {})", config::STREAMS.join(", ")));
            }
            let months = months.map(|m| m.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect());
            tui::tui(cfg, months, &stream, &what_if).unwrap_or_else(|e| fail(e));
        }
        Cmd::Analyse {
            months,
            stream,
            what_if,
            samples,
            top,
        } => {
            if !config::STREAMS.contains(&stream.as_str()) {
                fail(format!("unknown stream {stream} (known: {})", config::STREAMS.join(", ")));
            }
            let alt = (!what_if.is_empty()).then(|| {
                let mut v = cfg.v.clone();
                let sets: Vec<String> = what_if
                    .iter()
                    .map(|kv| {
                        let path = config::set(&mut v, &stream, kv).unwrap_or_else(|e| fail(e));
                        format!("{path} = {}", kv.split_once('=').map_or("", |x| x.1).trim())
                    })
                    .collect();
                (
                    config::Config {
                        v,
                        path: cfg.path.clone(),
                        existed: cfg.existed,
                    },
                    sets,
                )
            });
            let months = months.map(|m| m.split(',').map(|s| s.trim().to_string()).filter(|s| !s.is_empty()).collect());
            analyse::analyse(&cfg, alt.as_ref().map(|(c, s)| (c, s.clone())), months, &stream, samples, top)
                .unwrap_or_else(|e| fail(e));
        }
    }
}
