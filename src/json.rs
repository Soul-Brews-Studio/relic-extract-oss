//! Two JSON encoders whose bytes other tools depend on.
//!
//!   duckdb_list   DuckDB `to_json([a, b, …])` of VARCHARs: the input of msgkey = md5(…), which relic4 keys on.
//!                 Escapes " \ and control characters (\n \t \r \b \f, others as \u00XX upper-case); leaves "/",
//!                 DEL and every non-ASCII character as they are; NULL is `null`. Checked against DuckDB 1.x.
//!   py_dumps      Python `json.dumps(v, sort_keys=True)`: the input of a stream's settings hash, so a part cut by
//!                 the Python relic-extract is not cut again by this one.

use serde_json::Value;

#[cfg(test)]
fn esc_duckdb(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 => out.push_str(&format!("\\u{:04X}", c as u32)),
            c => out.push(c),
        }
    }
    out.push('"');
}

/// `duckdb_list`, fed to `out` piece by piece (no copy of the values): unescaped runs go through as they are.
/// Bytes that need escaping are all ASCII, so scanning bytes never splits a UTF-8 character.
pub fn duckdb_list_feed(items: &[Option<&str>], out: &mut impl FnMut(&[u8])) {
    out(b"[");
    for (i, it) in items.iter().enumerate() {
        if i > 0 {
            out(b",");
        }
        let Some(s) = it else {
            out(b"null");
            continue;
        };
        out(b"\"");
        let b = s.as_bytes();
        let mut run = 0;
        for (j, &c) in b.iter().enumerate() {
            let esc: Option<&[u8]> = match c {
                b'"' => Some(b"\\\""),
                b'\\' => Some(b"\\\\"),
                b'\n' => Some(b"\\n"),
                b'\t' => Some(b"\\t"),
                b'\r' => Some(b"\\r"),
                0x08 => Some(b"\\b"),
                0x0c => Some(b"\\f"),
                _ => None,
            };
            if esc.is_none() && c >= 0x20 {
                continue;
            }
            out(&b[run..j]);
            match esc {
                Some(e) => out(e),
                None => out(format!("\\u{:04X}", c).as_bytes()),
            }
            run = j + 1;
        }
        out(&b[run..]);
        out(b"\"");
    }
    out(b"]");
}

/// DuckDB `to_json([..])` of a list of VARCHAR values (the tests' oracle for `duckdb_list_feed`).
#[cfg(test)]
pub fn duckdb_list(items: &[Option<&str>]) -> String {
    let mut out = String::with_capacity(items.iter().map(|s| s.map_or(4, |s| s.len() + 2)).sum::<usize>() + 8);
    out.push('[');
    for (i, it) in items.iter().enumerate() {
        if i > 0 {
            out.push(',');
        }
        match it {
            Some(s) => esc_duckdb(s, &mut out),
            None => out.push_str("null"),
        }
    }
    out.push(']');
    out
}

fn esc_py(s: &str, out: &mut String) {
    out.push('"');
    for c in s.chars() {
        match c {
            '"' => out.push_str("\\\""),
            '\\' => out.push_str("\\\\"),
            '\n' => out.push_str("\\n"),
            '\t' => out.push_str("\\t"),
            '\r' => out.push_str("\\r"),
            '\u{8}' => out.push_str("\\b"),
            '\u{c}' => out.push_str("\\f"),
            c if (c as u32) < 0x20 || (c as u32) > 0x7e => {
                let mut b = [0u16; 2];
                for u in c.encode_utf16(&mut b) {
                    out.push_str(&format!("\\u{:04x}", u));
                }
            }
            c => out.push(c),
        }
    }
    out.push('"');
}

/// Python `json.dumps(v, sort_keys=True)` (separators ", " and ": ", ensure_ascii).
pub fn py_dumps(v: &Value) -> String {
    let mut out = String::new();
    py_into(v, &mut out);
    out
}

fn py_into(v: &Value, out: &mut String) {
    match v {
        Value::Null => out.push_str("null"),
        Value::Bool(b) => out.push_str(if *b { "true" } else { "false" }),
        Value::Number(n) => out.push_str(&n.to_string()),
        Value::String(s) => esc_py(s, out),
        Value::Array(a) => {
            out.push('[');
            for (i, x) in a.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                py_into(x, out);
            }
            out.push(']');
        }
        Value::Object(m) => {
            let mut keys: Vec<&String> = m.keys().collect();
            keys.sort();
            out.push('{');
            for (i, k) in keys.iter().enumerate() {
                if i > 0 {
                    out.push_str(", ");
                }
                esc_py(k, out);
                out.push_str(": ");
                py_into(&m[*k], out);
            }
            out.push('}');
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn duckdb_escapes_like_duckdb() {
        // DuckDB: to_json(['a/b','q"x','back\slash', chr(10)||chr(9)||chr(13)||chr(8)||chr(12)||chr(1)||chr(31)||chr(127), 'ไทย€😀', NULL])
        let got = duckdb_list(&[
            Some("a/b"),
            Some("q\"x"),
            Some("back\\slash"),
            Some("\n\t\r\u{8}\u{c}\u{1}\u{1f}\u{7f}"),
            Some("ไทย€😀"),
            None,
        ]);
        assert_eq!(
            got,
            "[\"a/b\",\"q\\\"x\",\"back\\\\slash\",\"\\n\\t\\r\\b\\f\\u0001\\u001F\u{7f}\",\"ไทย€😀\",null]"
        );
        assert_eq!(duckdb_list(&[Some("ab\0c")]), "[\"ab\\u0000c\"]");
        // the streaming form writes the same bytes
        for items in [
            vec![Some("a/b"), Some("q\"x\\y\n\u{1}\u{7f}ไทย😀"), None],
            vec![],
            vec![Some("")],
        ] {
            let mut buf = vec![];
            duckdb_list_feed(&items, &mut |b| buf.extend_from_slice(b));
            assert_eq!(String::from_utf8(buf).unwrap(), duckdb_list(&items));
        }
    }

    #[test]
    fn py_dumps_sorts_and_escapes() {
        let v: Value = serde_json::from_str(r#"{"b": [1, true], "a": "é😀"}"#).unwrap();
        assert_eq!(py_dumps(&v), "{\"a\": \"\\u00e9\\ud83d\\ude00\", \"b\": [1, true]}");
    }
}
