//! Minimal JSON reader (enough for tests.json).
#[derive(Debug, Clone)]
pub enum J {
    Null,
    Bool(bool),
    Num(f64),
    Str(String),
    Arr(Vec<J>),
    Obj(Vec<(String, J)>),
}

impl J {
    pub fn get(&self, k: &str) -> Option<&J> {
        match self {
            J::Obj(v) => v.iter().find(|(a, _)| a == k).map(|(_, b)| b),
            _ => None,
        }
    }
    pub fn as_str(&self) -> Option<&str> {
        match self {
            J::Str(s) => Some(s),
            _ => None,
        }
    }
    pub fn as_f64(&self) -> Option<f64> {
        match self {
            J::Num(n) => Some(*n),
            _ => None,
        }
    }
    pub fn as_arr(&self) -> Option<&Vec<J>> {
        match self {
            J::Arr(v) => Some(v),
            _ => None,
        }
    }
}

pub fn parse(s: &str) -> Result<J, String> {
    let b = s.as_bytes();
    let mut i = 0;
    let v = value(b, &mut i)?;
    Ok(v)
}

fn ws(b: &[u8], i: &mut usize) {
    while *i < b.len() && (b[*i] as char).is_ascii_whitespace() {
        *i += 1;
    }
}

fn value(b: &[u8], i: &mut usize) -> Result<J, String> {
    ws(b, i);
    if *i >= b.len() {
        return Err("eof".into());
    }
    match b[*i] {
        b'{' => {
            *i += 1;
            let mut v = Vec::new();
            loop {
                ws(b, i);
                if b[*i] == b'}' {
                    *i += 1;
                    break;
                }
                let k = match value(b, i)? {
                    J::Str(s) => s,
                    _ => return Err("key".into()),
                };
                ws(b, i);
                if b[*i] != b':' {
                    return Err("colon".into());
                }
                *i += 1;
                let x = value(b, i)?;
                v.push((k, x));
                ws(b, i);
                if b[*i] == b',' {
                    *i += 1;
                }
            }
            Ok(J::Obj(v))
        }
        b'[' => {
            *i += 1;
            let mut v = Vec::new();
            loop {
                ws(b, i);
                if b[*i] == b']' {
                    *i += 1;
                    break;
                }
                v.push(value(b, i)?);
                ws(b, i);
                if b[*i] == b',' {
                    *i += 1;
                }
            }
            Ok(J::Arr(v))
        }
        b'"' => {
            *i += 1;
            let mut out: Vec<u8> = Vec::new();
            while b[*i] != b'"' {
                if b[*i] == b'\\' {
                    *i += 1;
                    match b[*i] {
                        b'n' => out.push(b'\n'),
                        b't' => out.push(b'\t'),
                        b'r' => out.push(b'\r'),
                        b'b' => out.push(8),
                        b'f' => out.push(12),
                        b'u' => {
                            let h = std::str::from_utf8(&b[*i + 1..*i + 5]).map_err(|_| "u")?;
                            let mut cp = u32::from_str_radix(h, 16).map_err(|_| "u")?;
                            *i += 4;
                            if (0xD800..0xDC00).contains(&cp) && b.get(*i + 1) == Some(&b'\\') {
                                let h2 = std::str::from_utf8(&b[*i + 3..*i + 7]).map_err(|_| "u")?;
                                let lo = u32::from_str_radix(h2, 16).map_err(|_| "u")?;
                                cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                                *i += 6;
                            }
                            let c = char::from_u32(cp).unwrap_or('\u{fffd}');
                            let mut buf = [0u8; 4];
                            out.extend_from_slice(c.encode_utf8(&mut buf).as_bytes());
                        }
                        c => out.push(c),
                    }
                    *i += 1;
                } else {
                    out.push(b[*i]);
                    *i += 1;
                }
            }
            *i += 1;
            Ok(J::Str(String::from_utf8_lossy(&out).into_owned()))
        }
        b't' => {
            *i += 4;
            Ok(J::Bool(true))
        }
        b'f' => {
            *i += 5;
            Ok(J::Bool(false))
        }
        b'n' => {
            *i += 4;
            Ok(J::Null)
        }
        _ => {
            let st = *i;
            while *i < b.len() && matches!(b[*i], b'-' | b'+' | b'.' | b'e' | b'E' | b'0'..=b'9') {
                *i += 1;
            }
            let t = std::str::from_utf8(&b[st..*i]).unwrap();
            t.parse::<f64>().map(J::Num).map_err(|_| format!("num {t}"))
        }
    }
}

pub fn esc(s: &str) -> String {
    let mut o = String::with_capacity(s.len() + 2);
    o.push('"');
    for c in s.chars() {
        match c {
            '"' => o.push_str("\\\""),
            '\\' => o.push_str("\\\\"),
            '\n' => o.push_str("\\n"),
            '\r' => o.push_str("\\r"),
            '\t' => o.push_str("\\t"),
            c if (c as u32) < 0x20 => o.push_str(&format!("\\u{:04x}", c as u32)),
            c => o.push(c),
        }
    }
    o.push('"');
    o
}
