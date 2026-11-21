//! String-aware Python line classifier: strips comments, keeps strings.

#[derive(Debug, Clone)]
pub struct LineInfo {
    /// Line content with comments removed and trailing whitespace trimmed.
    pub code: String,
    /// True if the line carries code or string content.
    pub has_code: bool,
    /// Leading indentation width (spaces; tab = 8) when the line starts outside a string.
    pub indent: Option<usize>,
}

pub fn analyze(text: &str) -> Vec<LineInfo> {
    let mut out = Vec::new();
    // state: None = normal, Some((quote, triple))
    let mut st: Option<(char, bool)> = None;
    let mut paren: i32 = 0;
    let mut cont = false; // backslash continuation
    for line in text.split('\n') {
        let line = line.strip_suffix('\r').unwrap_or(line);
        let started_in_string = st.is_some();
        let continued = paren > 0 || cont;
        cont = false;
        let chars: Vec<char> = line.chars().collect();
        let mut code = String::new();
        let mut i = 0;
        while i < chars.len() {
            let c = chars[i];
            match st {
                Some((q, triple)) => {
                    code.push(c);
                    if c == '\\' {
                        if i + 1 < chars.len() {
                            code.push(chars[i + 1]);
                        }
                        i += 2;
                        continue;
                    }
                    if c == q {
                        if triple {
                            if i + 2 < chars.len() && chars[i + 1] == q && chars[i + 2] == q {
                                code.push(q);
                                code.push(q);
                                i += 3;
                                st = None;
                                continue;
                            }
                        } else {
                            st = None;
                        }
                    }
                    i += 1;
                }
                None => {
                    if c == '#' {
                        break;
                    }
                    if c == '\'' || c == '"' {
                        if i + 2 < chars.len() && chars[i + 1] == c && chars[i + 2] == c {
                            st = Some((c, true));
                            code.push(c);
                            code.push(c);
                            code.push(c);
                            i += 3;
                            continue;
                        }
                        st = Some((c, false));
                        code.push(c);
                        i += 1;
                        continue;
                    }
                    match c {
                        '(' | '[' | '{' => paren += 1,
                        ')' | ']' | '}' => paren -= 1,
                        _ => {}
                    }
                    code.push(c);
                    i += 1;
                }
            }
        }
        // single-quoted strings end at line end unless backslash-continued
        if let Some((_, false)) = st {
            if !line.ends_with('\\') {
                st = None;
            }
        }
        if st.is_none() && line.trim_end().ends_with('\\') && !code.is_empty() {
            cont = true;
        }
        let trimmed = code.trim_end().to_string();
        let has_code = started_in_string || !trimmed.trim().is_empty();
        let indent = if started_in_string || continued {
            None
        } else {
            let mut w = 0;
            for ch in line.chars() {
                match ch {
                    ' ' => w += 1,
                    '\t' => w = (w / 8 + 1) * 8,
                    _ => break,
                }
            }
            Some(w)
        };
        let code = if started_in_string { format!("\u{0}{}", line) } else { trimmed };
        out.push(LineInfo { code, has_code, indent });
        if paren < 0 {
            paren = 0;
        }
    }
    out
}

/// True when old and new differ only in comments / blank lines.
pub fn comment_only(old: &str, new: &str) -> bool {
    let a: Vec<String> = analyze(old).into_iter().filter(|l| l.has_code).map(|l| l.code).collect();
    let b: Vec<String> = analyze(new).into_iter().filter(|l| l.has_code).map(|l| l.code).collect();
    a == b
}
