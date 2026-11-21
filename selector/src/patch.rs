//! Unified (git) diff parsing and application.

#[derive(Debug, Clone)]
pub struct Hunk {
    pub old_start: usize,
    pub old_len: usize,
    pub lines: Vec<(u8, String)>, // b' ', b'-', b'+'
    pub new_no_nl: bool,
}

#[derive(Debug, Clone, Default)]
pub struct FilePatch {
    pub old_path: Option<String>,
    pub new_path: Option<String>,
    pub hunks: Vec<Hunk>,
    pub binary: bool,
    pub mode_change: bool,
}

impl FilePatch {
    pub fn path(&self) -> &str {
        self.old_path.as_deref().or(self.new_path.as_deref()).unwrap_or("")
    }
}

fn strip_prefix(p: &str) -> Option<String> {
    let p = p.trim_end_matches('\t').trim();
    let p = p.split('\t').next().unwrap_or(p);
    if p == "/dev/null" {
        return None;
    }
    let p = if p.starts_with('"') && p.ends_with('"') && p.len() >= 2 { &p[1..p.len() - 1] } else { p };
    if let Some(r) = p.strip_prefix("a/").or_else(|| p.strip_prefix("b/")) {
        Some(r.to_string())
    } else {
        Some(p.to_string())
    }
}

fn parse_range(s: &str) -> (usize, usize) {
    let s = &s[1..];
    let mut it = s.split(',');
    let a = it.next().unwrap_or("0").parse().unwrap_or(0);
    let b = it.next().map(|x| x.parse().unwrap_or(1)).unwrap_or(1);
    (a, b)
}

pub fn parse(text: &str) -> Vec<FilePatch> {
    let lines: Vec<&str> = text.split('\n').collect();
    let mut out: Vec<FilePatch> = Vec::new();
    let mut cur: Option<FilePatch> = None;
    let mut i = 0;
    while i < lines.len() {
        let l = lines[i].strip_suffix('\r').unwrap_or(lines[i]);
        if let Some(rest) = l.strip_prefix("diff --git ") {
            if let Some(c) = cur.take() {
                out.push(c);
            }
            let mut fp = FilePatch::default();
            // best effort: "a/x b/y"
            if let Some(pos) = rest.find(" b/") {
                fp.old_path = strip_prefix(&rest[..pos]);
                fp.new_path = strip_prefix(&rest[pos + 1..]);
            }
            cur = Some(fp);
            i += 1;
            continue;
        }
        if l.starts_with("--- ") && i + 1 < lines.len() && lines[i + 1].starts_with("+++ ") {
            let fp = cur.get_or_insert_with(FilePatch::default);
            fp.old_path = strip_prefix(&l[4..]);
            fp.new_path = strip_prefix(&lines[i + 1][4..]);
            i += 2;
            continue;
        }
        if l.starts_with("@@ ") {
            let fp = cur.get_or_insert_with(FilePatch::default);
            let parts: Vec<&str> = l.split(' ').collect();
            let (os, ol) = parse_range(parts.get(1).unwrap_or(&"-0,0"));
            let (_ns, nl) = parse_range(parts.get(2).unwrap_or(&"+0,0"));
            let mut h = Hunk { old_start: os, old_len: ol, lines: Vec::new(), new_no_nl: false };
            let (mut ro, mut rn) = (ol, nl);
            i += 1;
            let mut last = b' ';
            while i < lines.len() {
                let hl = lines[i];
                if hl.starts_with('\\') {
                    if last == b'+' || last == b' ' {
                        h.new_no_nl = true;
                    }
                    i += 1;
                    continue;
                }
                if ro == 0 && rn == 0 {
                    break;
                }
                let (k, body) = if hl.is_empty() {
                    (b' ', "")
                } else {
                    (hl.as_bytes()[0], &hl[1..])
                };
                match k {
                    b' ' => {
                        ro = ro.saturating_sub(1);
                        rn = rn.saturating_sub(1);
                    }
                    b'-' => ro = ro.saturating_sub(1),
                    b'+' => rn = rn.saturating_sub(1),
                    _ => break,
                }
                // a hunk "line" that is empty in the file text with a missing leading space
                if hl.is_empty() && i == lines.len() - 1 {
                    break;
                }
                last = k;
                h.lines.push((k, body.strip_suffix('\r').map(|x| x.to_string()).unwrap_or(body.to_string())));
                i += 1;
            }
            fp.hunks.push(h);
            continue;
        }
        if let Some(fp) = cur.as_mut() {
            if l.starts_with("new file mode") {
                fp.old_path = None;
            } else if l.starts_with("deleted file mode") {
                fp.new_path = None;
            } else if let Some(r) = l.strip_prefix("rename from ") {
                fp.old_path = Some(r.to_string());
            } else if let Some(r) = l.strip_prefix("rename to ") {
                fp.new_path = Some(r.to_string());
            } else if let Some(r) = l.strip_prefix("copy from ") {
                fp.old_path = Some(r.to_string());
            } else if let Some(r) = l.strip_prefix("copy to ") {
                fp.new_path = Some(r.to_string());
            } else if l.starts_with("Binary files") || l.starts_with("GIT binary patch") {
                fp.binary = true;
            } else if l.starts_with("old mode") || l.starts_with("new mode") {
                fp.mode_change = true;
            }
        }
        i += 1;
    }
    if let Some(c) = cur.take() {
        out.push(c);
    }
    // a "new file" header sets old_path None only if it came after diff --git; re-check by /dev/null handled in ---
    out
}

/// Split text into lines (without terminators) and whether it ends with a newline.
pub fn split_lines(text: &str) -> (Vec<String>, bool) {
    if text.is_empty() {
        return (Vec::new(), true);
    }
    let nl = text.ends_with('\n');
    let body = if nl { &text[..text.len() - 1] } else { text };
    (body.split('\n').map(|s| s.to_string()).collect(), nl)
}

pub fn join_lines(lines: &[String], nl: bool) -> String {
    let mut s = lines.join("\n");
    if nl && !lines.is_empty() {
        s.push('\n');
    }
    s
}

/// Apply hunks to old text. Returns new text or an error.
pub fn apply(old: &str, hunks: &[Hunk]) -> Result<String, String> {
    let (ol, onl) = split_lines(old);
    let mut out: Vec<String> = Vec::new();
    let mut pos = 0usize; // index into ol
    let mut new_nl = onl;
    for h in hunks {
        let olds: Vec<&String> = h.lines.iter().filter(|(k, _)| *k != b'+').map(|(_, s)| s).collect();
        let want = if h.old_len == 0 { h.old_start } else { h.old_start.saturating_sub(1) };
        let matches = |at: usize| -> bool {
            if at + olds.len() > ol.len() {
                return false;
            }
            olds.iter().enumerate().all(|(j, s)| ol[at + j] == **s)
        };
        let mut at = None;
        if want >= pos && matches(want) {
            at = Some(want);
        } else {
            for d in 1..ol.len() + 1 {
                if want >= d && want - d >= pos && matches(want - d) {
                    at = Some(want - d);
                    break;
                }
                if want + d <= ol.len() && matches(want + d) {
                    at = Some(want + d);
                    break;
                }
                if d > want && want + d > ol.len() {
                    break;
                }
            }
        }
        let at = at.ok_or_else(|| format!("hunk at {} does not apply", h.old_start))?;
        if at < pos {
            return Err("overlapping hunks".into());
        }
        out.extend(ol[pos..at].iter().cloned());
        for (k, s) in &h.lines {
            if *k != b'-' {
                out.push(s.clone());
            }
        }
        pos = at + olds.len();
        if pos >= ol.len() {
            new_nl = !h.new_no_nl;
        }
    }
    out.extend(ol[pos..].iter().cloned());
    Ok(join_lines(&out, new_nl))
}

/// Old-file line numbers (1-based) removed/changed, and insertion points.
/// An insertion point `a` means new lines were inserted after old line `a` (0 = top).
pub struct Touch {
    pub removed: Vec<usize>,
    /// (insert after old line a, lines, replaces: a is the last line of a removed run)
    pub inserted: Vec<(usize, Vec<String>, bool)>,
    pub removed_text: Vec<String>,
    pub added_text: Vec<String>,
}

pub fn touches(hunks: &[Hunk]) -> Touch {
    let mut t = Touch { removed: vec![], inserted: vec![], removed_text: vec![], added_text: vec![] };
    for h in hunks {
        let mut o = if h.old_len == 0 { h.old_start } else { h.old_start.saturating_sub(1) };
        let mut minus_run = 0usize; // removed lines right before the current + run
        let mut pending: Vec<String> = Vec::new();
        let mut pending_at = o;
        let mut pending_minus = 0usize;
        let flush = |t: &mut Touch, pending: &mut Vec<String>, at: usize, minus: usize| {
            if pending.is_empty() {
                return;
            }
            if minus == 0 {
                t.inserted.push((at, std::mem::take(pending), false));
            } else if pending.len() > minus {
                // a replacement that also adds lines: attribute it to the replaced statement
                t.inserted.push((at, std::mem::take(pending), true));
            } else {
                pending.clear();
            }
        };
        for (k, s) in &h.lines {
            match *k {
                b' ' => {
                    flush(&mut t, &mut pending, pending_at, pending_minus);
                    minus_run = 0;
                    o += 1;
                }
                b'-' => {
                    if !pending.is_empty() {
                        flush(&mut t, &mut pending, pending_at, pending_minus);
                        minus_run = 0;
                    }
                    o += 1;
                    minus_run += 1;
                    t.removed.push(o);
                    t.removed_text.push(s.clone());
                }
                _ => {
                    t.added_text.push(s.clone());
                    if pending.is_empty() {
                        pending_at = o;
                        pending_minus = minus_run;
                    }
                    pending.push(s.clone());
                }
            }
        }
        flush(&mut t, &mut pending, pending_at, pending_minus);
    }
    t
}
