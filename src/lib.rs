//! relic-extract — step 2 of scan -> extract -> embed, as a library: relic-scan month files -> per-stream, deduped,
//! cleaned, chunked parts for embedding. The `relic-extract` binary (src/main.rs) is a thin CLI over it; relic-embed
//! can depend on it so msgkey, key and the cut can never drift between the two.

pub mod analyse;
pub mod clean;
pub mod config;
pub mod extract;
pub mod json;
pub mod read;
pub mod run;
pub mod tui;
