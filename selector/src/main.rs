mod json;
mod patch;
mod pyline;

use std::collections::{BTreeSet, HashMap, HashSet};
use std::fs;
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::Instant;

macro_rules! log {
    ($($t:tt)*) => {{ eprintln!($($t)*); }};
}

struct Test {
    number: i64,
    id: String,
    file: String,
    ms: u64,
}

#[derive(Default)]
struct FileMap {
    units: Vec<(u32, u32, u32)>,
    stmts: Vec<(u32, u32, u32)>,
    classes: Vec<(u32, u32, u32)>,
    docs: Vec<(u32, u32, u32)>,
    lines: HashMap<u32, u32>,
    dep: Option<u32>,
    src: Option<u32>,
}

#[derive(Default)]
struct Map {
    ids: Vec<String>,
    vcost: HashMap<usize, u64>,
    sets: Vec<Vec<u32>>,
    files: HashMap<String, FileMap>,
    procs: u32,
}

fn load_map(path: &Path) -> Map {
    let mut m = Map::default();
    let text = match fs::read_to_string(path) {
        Ok(t) => t,
        Err(e) => {
            log!("map read failed: {e}");
            return m;
        }
    };
    let mut cur: Option<String> = None;
    for line in text.lines() {
        let (tag, rest) = line.split_at(1);
        let rest = rest.trim_start();
        match tag {
            "T" => {
                let n: usize = rest.parse().unwrap_or(0);
                m.ids = vec![String::new(); n];
            }
            "I" => {
                let (n, id) = rest.split_once(' ').unwrap();
                let n: usize = n.parse().unwrap();
                if n >= m.ids.len() {
                    m.ids.resize(n + 1, String::new());
                }
                m.ids[n] = id.to_string();
            }
            "P" => m.procs = rest.parse().unwrap_or(0),
            "V" => {
                let (n, c) = rest.split_once(' ').unwrap();
                m.vcost.insert(n.parse().unwrap(), c.parse().unwrap());
            }
            "S" => {
                let mut it = rest.split(' ').filter(|x| !x.is_empty());
                let k: usize = it.next().unwrap().parse().unwrap();
                let v: Vec<u32> = it.map(|x| x.parse().unwrap()).collect();
                if k >= m.sets.len() {
                    m.sets.resize(k + 1, Vec::new());
                }
                m.sets[k] = v;
            }
            "F" => {
                cur = Some(rest.to_string());
                m.files.entry(rest.to_string()).or_default();
            }
            "U" => {
                let v: Vec<u32> = rest.split(' ').map(|x| x.parse().unwrap()).collect();
                m.files.get_mut(cur.as_ref().unwrap()).unwrap().units.push((v[0], v[1], v[2]));
            }
            "X" | "C" | "Z" => {
                let v: Vec<u32> = rest.split(' ').map(|x| x.parse().unwrap()).collect();
                let f = m.files.get_mut(cur.as_ref().unwrap()).unwrap();
                let e = (v[0], v[1], v[2]);
                match tag {
                    "X" => f.stmts.push(e),
                    "C" => f.classes.push(e),
                    _ => f.docs.push(e),
                }
            }
            "L" => {
                let v: Vec<u32> = rest.split(' ').map(|x| x.parse().unwrap()).collect();
                m.files.get_mut(cur.as_ref().unwrap()).unwrap().lines.insert(v[0], v[1]);
            }
            "D" => m.files.get_mut(cur.as_ref().unwrap()).unwrap().dep = Some(rest.parse().unwrap()),
            "R" => m.files.get_mut(cur.as_ref().unwrap()).unwrap().src = Some(rest.parse().unwrap()),
            _ => {}
        }
    }
    m
}

fn load_tests(path: &Path) -> Vec<Test> {
    let text = fs::read_to_string(path).expect("tests.json");
    let j = json::parse(&text).expect("tests.json parse");
    let mut out = Vec::new();
    if let Some(arr) = j.get("tests").and_then(|x| x.as_arr()) {
        for t in arr {
            let number = t.get("number").and_then(|x| x.as_f64()).unwrap_or(-1.0) as i64;
            if number < 0 {
                continue;
            }
            let id = t.get("id").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let file = t.get("file").and_then(|x| x.as_str()).unwrap_or("").to_string();
            let secs = t.get("seconds").and_then(|x| x.as_f64()).unwrap_or(0.0);
            out.push(Test { number, id, file, ms: (secs * 1000.0).round() as u64 });
        }
    }
    out
}

/// What a commit needs.
struct Plan {
    commit: String,
    /// (adapter, items, heavy test index, estimated ms)
    adapters: Vec<(String, Vec<String>, usize, u64)>,
    /// indices into tests vec
    run: BTreeSet<usize>,
    /// include without running
    force: BTreeSet<usize>,
    /// files after patching: path -> Some(content) or None (deleted)
    changed: Vec<(String, Option<String>)>,
    ok: bool,
}

struct Ctx {
    src_root: PathBuf,
    tests: Vec<Test>,
    map: Map,
    /// map test index -> tests vec index
    map2t: Vec<Option<usize>>,
    by_file: HashMap<String, Vec<usize>>,
    id2t: HashMap<String, usize>,
}

const ADAPTERS: &[(&str, &str)] = &[
    ("exec_tpcds", "tests/test_executor.py::TestExecutor::test_execute_tpcds"),
    ("exec_tpch", "tests/test_executor.py::TestExecutor::test_execute_tpch"),
    ("opt_tpch", "tests/test_executor.py::TestExecutor::test_optimized_tpch"),
    ("gen_memory", "tests/test_generator.py::TestGenerator::test_generator_memory"),
    ("max_nodes", "tests/test_parser.py::TestParser::test_max_nodes"),
];

fn pack_cost(tests: &[Test], sel: &BTreeSet<usize>) -> u64 {
    if sel.is_empty() {
        return 0;
    }
    let mut items: Vec<u64> = sel.iter().map(|&i| tests[i].ms).collect();
    items.sort_by(|a, b| b.cmp(a));
    let mut best = u64::MAX;
    for k in 1..=8usize.min(items.len()) {
        let mut loads = vec![0u64; k];
        for ms in &items {
            let j = (0..k).min_by_key(|&j| loads[j]).unwrap();
            loads[j] += ms;
        }
        best = best.min(loads.iter().max().unwrap() + 2000 * k as u64);
    }
    best
}

impl Ctx {
    fn add_set(&self, out: &mut BTreeSet<usize>, sid: u32) {
        if let Some(s) = self.map.sets.get(sid as usize) {
            for &mt in s {
                match self.map2t.get(mt as usize) {
                    Some(Some(t)) => {
                        out.insert(*t);
                    }
                    _ => {
                        if self.map.vcost.contains_key(&(mt as usize)) {
                            out.insert(self.tests.len() + mt as usize);
                        }
                    }
                }
            }
        }
    }

    fn file_overhead(file: &str) -> u64 {
        match file {
            "tests/test_executor.py" => 3200,
            "tests/test_optimizer.py" => 400,
            _ => 40,
        }
    }

    /// Turn the raw selection into: tests to run, tests to include unrun, adapter checks.
    fn decide(&self, p: &mut Plan) {
        let n = self.tests.len();
        let heavy_ms: u64 = std::env::var("BR_HEAVY_MS").ok().and_then(|x| x.parse().ok()).unwrap_or(1500);
        let crun: f64 = std::env::var("BR_C").ok().and_then(|x| x.parse().ok()).unwrap_or(1.0);
        let vhits: BTreeSet<usize> = p.run.iter().filter(|&&i| i >= n).map(|&i| i - n).collect();
        let mut real: BTreeSet<usize> = p.run.iter().copied().filter(|&i| i < n).collect();
        let mut forced: BTreeSet<usize> = BTreeSet::new();
        // adapters
        for (name, tid) in ADAPTERS {
            let Some(&t) = self.id2t.get(*tid) else { continue };
            if !real.contains(&t) {
                continue;
            }
            real.remove(&t);
            let d = self.tests[t].ms;
            let mut hits: Vec<(u64, String)> = Vec::new();
            let mut base_hit = false;
            let prefix = format!("ADAPT::{name}::");
            for &v in &vhits {
                let id = &self.map.ids[v];
                if let Some(it) = id.strip_prefix(&prefix) {
                    if it == "base" {
                        base_hit = true;
                    } else {
                        hits.push((self.map.vcost.get(&v).copied().unwrap_or(0), it.to_string()));
                    }
                }
            }
            if *name == "max_nodes" {
                hits = vec![(3000, "proxy".into())];
                base_hit = false;
            }
            let data_change = p.changed.iter().any(|(f, _)| !f.ends_with(".py"));
            let mut exhaustive = false;
            if data_change && hits.is_empty() && !base_hit {
                // a data file the test reads changed: every item is suspect
                for (v, id) in self.map.ids.iter().enumerate() {
                    if let Some(it) = id.strip_prefix(&prefix) {
                        if it != "base" {
                            hits.push((self.map.vcost.get(&v).copied().unwrap_or(0), it.to_string()));
                        }
                    }
                }
                exhaustive = true;
            }
            let setup: u64 = if name.starts_with("exec") || name.starts_with("opt") { 1500 } else { 200 };
            let full: u64 = hits.iter().map(|h| h.0).sum();
            let limit = 0.6 * (d + 2000) as f64;
            if base_hit {
                log!("{}: include {} (setup reached)", p.commit, name);
                forced.insert(t);
            } else if hits.is_empty() {
                // neither the setup nor any item can see the change
                log!("{}: drop {} (no item reached)", p.commit, name);
            } else if ((full + setup) as f64) <= limit {
                log!("{}: adapter {} on all {} hit items (est {}ms)", p.commit, name, hits.len(), full + setup);
                p.adapters.push((name.to_string(), hits.into_iter().map(|h| h.1).collect(), t, full + setup));
            } else if exhaustive {
                log!("{}: include {} (data change, all items too costly)", p.commit, name);
                forced.insert(t);
            } else {
                // too many reachable items: run a cheap sample; a broad breakage shows up in it
                let budget: u64 = std::env::var("BR_SAMPLE_MS").ok().and_then(|x| x.parse().ok()).unwrap_or(600);
                hits.sort();
                let mut cost = 0u64;
                let mut take: Vec<String> = Vec::new();
                for (c, it) in &hits {
                    if take.len() >= 3 && cost + c > budget {
                        break;
                    }
                    cost += c;
                    take.push(it.clone());
                }
                if ((cost + setup) as f64) > limit {
                    log!("{}: include {} (sample too costly)", p.commit, name);
                    forced.insert(t);
                } else {
                    log!("{}: adapter {} on a sample of {}/{} items (est {}ms)", p.commit, name, take.len(), hits.len(), cost + setup);
                    p.adapters.push((name.to_string(), take, t, cost + setup));
                }
            }
        }
        // heavy tests without adapters are included, never run
        let heavy: Vec<usize> = real.iter().copied().filter(|&i| self.tests[i].ms >= heavy_ms).collect();
        for h in heavy {
            real.remove(&h);
            forced.insert(h);
        }
        // light tests: run them, or include them unrun if that is cheaper
        if !real.is_empty() {
            let mut files: HashSet<&str> = HashSet::new();
            let mut sum = 0u64;
            for &i in &real {
                sum += self.tests[i].ms + 1;
                files.insert(self.tests[i].file.as_str());
            }
            let overhead: u64 = 450 + files.iter().map(|f| Self::file_overhead(f)).sum::<u64>();
            let run_cost = (sum as f64 * crun) as u64 + overhead;
            let base = pack_cost(&self.tests, &forced);
            let mut all = forced.clone();
            all.extend(real.iter().copied());
            let inc = pack_cost(&self.tests, &all) - base;
            if run_cost < inc {
                log!("{}: run {} light tests (run {}ms < include {}ms)", p.commit, real.len(), run_cost, inc);
            } else {
                log!("{}: include {} light tests unrun (run {}ms >= include {}ms)", p.commit, real.len(), run_cost, inc);
                forced.extend(real.iter().copied());
                real.clear();
            }
        }
        p.run = real;
        p.force.extend(forced);
    }

    fn all(&self, out: &mut BTreeSet<usize>) {
        for i in 0..self.tests.len() {
            out.insert(i);
        }
    }

    fn plan(&self, commit: &str, text: &str) -> Plan {
        let fps = patch::parse(text);
        let mut p = Plan { commit: commit.to_string(), adapters: Vec::new(), run: BTreeSet::new(), force: BTreeSet::new(), changed: vec![], ok: true };
        let mut seen_paths: HashSet<String> = HashSet::new();
        for fp in &fps {
            let oldp = fp.old_path.clone();
            let newp = fp.new_path.clone();
            // Compute new content
            let old_text = oldp.as_ref().and_then(|o| fs::read_to_string(self.src_root.join(o)).ok());
            if oldp.is_some() && old_text.is_none() {
                log!("{commit}: cannot read old file {:?}", oldp);
            }
            let new_text: Option<String> = if newp.is_none() {
                None
            } else if fp.binary {
                p.ok = false;
                None
            } else if fp.hunks.is_empty() {
                old_text.clone()
            } else {
                match patch::apply(old_text.as_deref().unwrap_or(""), &fp.hunks) {
                    Ok(t) => Some(t),
                    Err(e) => {
                        log!("{commit}: apply failed for {:?}: {e}", newp);
                        p.ok = false;
                        None
                    }
                }
            };
            if let Some(o) = &oldp {
                if newp.as_ref() != Some(o) {
                    p.changed.push((o.clone(), None));
                }
                seen_paths.insert(o.clone());
            }
            if let Some(n) = &newp {
                p.changed.push((n.clone(), new_text.clone()));
                seen_paths.insert(n.clone());
            }
            // Selection
            let path = oldp.clone().or(newp.clone()).unwrap_or_default();
            self.select_file(&mut p, &path, old_text.as_deref(), if newp.is_some() { new_text.as_deref() } else { None }, fp, oldp.is_none(), newp.is_none() || oldp != newp);
        }
        p
    }

    #[allow(clippy::too_many_arguments)]
    fn select_file(&self, p: &mut Plan, path: &str, old: Option<&str>, new: Option<&str>, fp: &patch::FilePatch, added: bool, removed_or_renamed: bool) {
        let name = path.rsplit('/').next().unwrap_or(path);
        // config files that change how pytest runs
        if matches!(name, "conftest.py" | "pytest.ini" | "tox.ini" | "setup.cfg" | "pyproject.toml") && (path == name || path.starts_with("tests/")) {
            log!("{}: config file {path} touched: running everything", p.commit);
            self.all(&mut p.run);
            return;
        }
        let fm = self.map.files.get(path);
        if added || removed_or_renamed {
            self.dir_for(p, path);
            if let Some(n) = &fp.new_path {
                self.dir_for(p, n);
            }
        }
        if added {
            // new file: only matters if something imports or reads it; other hunks cover that.
            if path.ends_with(".py") && (path.starts_with("sqlglot/") || path.starts_with("tests/")) {
                // a new module inside a package can shadow nothing; nothing to do
            }
            return;
        }
        let fm = match fm {
            Some(f) => f,
            None => return, // nobody reads it
        };
        if removed_or_renamed {
            if let Some(d) = fm.dep {
                self.add_set(&mut p.run, d);
            }
            if let Some(s) = fm.src {
                self.add_set(&mut p.run, s);
            }
            self.procs_for(p, fm);
            return;
        }
        if !path.ends_with(".py") {
            if let Some(d) = fm.dep {
                self.add_set(&mut p.run, d);
            }
            return;
        }
        let old = old.unwrap_or("");
        let new = match new {
            Some(n) => n,
            None => {
                // could not apply: be conservative
                if let Some(d) = fm.dep {
                    self.add_set(&mut p.run, d);
                }
                return;
            }
        };
        // Any test reading this file as text sees every change.
        if let Some(s) = fm.src {
            self.add_set(&mut p.run, s);
        }
        let head_changed = old.lines().take(2).ne(new.lines().take(2));
        if pyline::comment_only(old, new) && !(head_changed && (old.lines().take(2).any(|l| l.contains("coding")) || new.lines().take(2).any(|l| l.contains("coding")))) {
            log!("{}: {path}: comment-only change", p.commit);
            return;
        }
        let info = pyline::analyze(old);
        let t = patch::touches(&fp.hunks);
        let mut coarse = false;
        let mut sets: BTreeSet<u32> = BTreeSet::new();
        fn innermost(v: &[(u32, u32, u32)], l: u32) -> Option<(u32, u32, u32)> {
            let mut best: Option<(u32, u32, u32)> = None;
            for &u in v {
                if u.0 <= l && l <= u.1 && best.map_or(true, |b| u.1 - u.0 < b.1 - b.0) {
                    best = Some(u);
                }
            }
            best
        }
        let docs_hit = |l: u32, sets: &mut BTreeSet<u32>| {
            for d in &fm.docs {
                if d.0 <= l && l <= d.1 {
                    sets.insert(d.2);
                }
            }
        };
        for &l in &t.removed {
            let li = info.get(l - 1);
            if li.map_or(false, |x| !x.has_code) {
                continue;
            }
            let l = l as u32;
            docs_hit(l, &mut sets);
            if let Some(&s) = fm.lines.get(&l) {
                sets.insert(s);
            }
            if let Some(u) = innermost(&fm.units, l) {
                sets.insert(u.2);
            } else if let Some(x) = innermost(&fm.stmts, l) {
                sets.insert(x.2);
            } else {
                log!("{}: {path}:{l}: unmapped static line -> module dependents", p.commit);
                coarse = true;
            }
        }
        for (a, newlines) in &t.inserted {
            let ninfo = pyline::analyze(&newlines.join("\n"));
            let code: Vec<&pyline::LineInfo> = ninfo.iter().filter(|x| x.has_code).collect();
            if code.is_empty() {
                continue;
            }
            let ind = code.iter().filter_map(|x| x.indent).min().unwrap_or(0);
            let a = *a as u32;
            docs_hit(a, &mut sets);
            docs_hit(a + 1, &mut sets);
            let indent_of = |line: u32| info.get(line as usize - 1).and_then(|x| x.indent).unwrap_or(0);
            // 1. a function body that encloses the insertion point
            let mut owner: Option<(u32, u32, u32)> = None;
            let mut cands: Vec<(u32, u32, u32)> = fm.units.iter().copied().filter(|u| u.0 <= a && a <= u.1).collect();
            cands.sort_by_key(|u| u.1 - u.0);
            for u in cands {
                if ind > indent_of(u.0) {
                    owner = Some(u);
                    break;
                }
            }
            if let Some(u) = owner {
                sets.insert(u.2);
                continue;
            }
            // 2. inside a (multi-line) static statement
            let stmt = fm.stmts.iter().copied().filter(|x| x.0 <= a && a + 1 <= x.1).min_by_key(|x| x.1 - x.0);
            if let Some(x) = stmt {
                if ind > indent_of(x.0) {
                    sets.insert(x.2);
                    continue;
                }
            }
            // 3. a new statement in a class body
            let mut cls: Vec<(u32, u32, u32)> = fm.classes.iter().copied().filter(|c| c.0 <= a && a <= c.1).collect();
            cls.sort_by_key(|c| c.1 - c.0);
            if let Some(c) = cls.into_iter().find(|c| ind > indent_of(c.0)) {
                sets.insert(c.2);
                // the statement right after might be affected too (e.g. decorators)
                if let Some(x) = innermost(&fm.stmts, a + 1) {
                    sets.insert(x.2);
                }
                continue;
            }
            // 4. module level: new defs/classes/imports are inert until referenced
            let first = code[0].code.trim_start();
            let inert = code.iter().all(|c| c.indent.map_or(true, |i| i > 0))
                && (first.starts_with("def ") || first.starts_with("class ") || first.starts_with("async def ") || first.starts_with('@'));
            let imports = code.iter().filter(|c| c.indent == Some(0)).all(|c| {
                let t = c.code.trim_start();
                t.starts_with("import ") || t.starts_with("from ") || t.starts_with("def ") || t.starts_with("class ") || t.starts_with('@')
            });
            if inert || imports {
                // a new top-level name: only matters if it shadows an existing one
                if let Some(x) = innermost(&fm.stmts, a + 1) {
                    sets.insert(x.2);
                }
                continue;
            }
            log!("{}: {path}: new module-level code after line {a} -> module dependents", p.commit);
            coarse = true;
        }
        for s in sets {
            self.add_set(&mut p.run, s);
        }
        if coarse {
            if let Some(d) = fm.dep {
                self.add_set(&mut p.run, d);
            }
        }
        self.procs_for(p, fm);
    }

    fn procs_for(&self, _p: &mut Plan, _fm: &FileMap) {}

    /// Tests that list the directory of an added/removed file.
    fn dir_for(&self, p: &mut Plan, path: &str) {
        let dir = path.rsplit_once('/').map(|x| x.0).unwrap_or("");
        if let Some(fm) = self.map.files.get(&format!("dir:{dir}")) {
            if let Some(d) = fm.dep {
                self.add_set(&mut p.run, d);
            }
        }
    }
}

// ---------------------------------------------------------------- farm

fn build_farm(src: &Path, dst: &Path, changed: &[(String, Option<String>)]) -> std::io::Result<()> {
    let _ = fs::remove_dir_all(dst);
    fs::create_dir_all(dst)?;
    let rels: Vec<(Vec<String>, Option<&String>)> =
        changed.iter().map(|(p, c)| (p.split('/').map(|s| s.to_string()).collect(), c.as_ref())).collect();
    build_dir(src, dst, &rels, 0)
}

fn build_dir(src: &Path, dst: &Path, rels: &[(Vec<String>, Option<&String>)], depth: usize) -> std::io::Result<()> {
    // names touched at this depth
    let mut touched: HashMap<String, Vec<(Vec<String>, Option<&String>)>> = HashMap::new();
    for (parts, c) in rels {
        if parts.len() > depth {
            touched.entry(parts[depth].clone()).or_default().push((parts.clone(), *c));
        }
    }
    if let Ok(rd) = fs::read_dir(src) {
        for e in rd.flatten() {
            let name = e.file_name().to_string_lossy().to_string();
            if touched.contains_key(&name) {
                continue;
            }
            std::os::unix::fs::symlink(e.path(), dst.join(&name))?;
        }
    }
    for (name, sub) in touched {
        let is_leaf = sub.iter().any(|(p, _)| p.len() == depth + 1);
        if is_leaf {
            if let Some((_, c)) = sub.iter().find(|(p, _)| p.len() == depth + 1) {
                if let Some(c) = c {
                    fs::write(dst.join(&name), c.as_bytes())?;
                }
            }
        } else {
            let d = dst.join(&name);
            fs::create_dir_all(&d)?;
            build_dir(&src.join(&name), &d, &sub, depth + 1)?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------- runners

struct Runner {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

fn spawn_runner(python: &str, script: &Path, src_root: &Path) -> Option<Runner> {
    let mut child = Command::new(python)
        .arg(script)
        .current_dir(src_root)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::inherit())
        .spawn()
        .ok()?;
    let stdin = child.stdin.take()?;
    let mut stdout = BufReader::new(child.stdout.take()?);
    let mut l = String::new();
    stdout.read_line(&mut l).ok()?;
    if l.trim() != "READY" {
        return None;
    }
    Some(Runner { child, stdin, stdout })
}

struct Outcome {
    fail: HashSet<String>,
    pass: HashSet<String>,
    import_fail: Vec<String>,
    adapt_ok: HashSet<String>,
}

#[allow(clippy::too_many_arguments)]
fn run_job(r: &mut Runner, job: usize, root: &Path, base_timeout: f64, items: &[(f64, &str)], pre: &str, probes: &[(String, String)], adapts: &[(String, Vec<String>, usize)]) -> Option<Outcome> {
    let mut s = format!("JOB {job} {} {base_timeout} {pre}\n", root.display());
    for (name, its, _) in adapts {
        s.push_str(&format!("ADAPT {name} {}\n", its.join(",")));
    }
    for (m, rel) in probes {
        s.push_str(&format!("PROBE {} {}\n", if m.is_empty() { "-" } else { m }, rel));
    }
    for (m, id) in items {
        s.push_str(&format!("{m} {id}\n"));
    }
    s.push_str("END\n");
    r.stdin.write_all(s.as_bytes()).ok()?;
    r.stdin.flush().ok()?;
    let mut out = Outcome { fail: HashSet::new(), pass: HashSet::new(), import_fail: Vec::new(), adapt_ok: HashSet::new() };
    let mut started = false;
    loop {
        let mut l = String::new();
        if r.stdout.read_line(&mut l).ok()? == 0 {
            return None;
        }
        let l = l.trim_end_matches('\n');
        if l.starts_with("RES ") {
            started = true;
            continue;
        }
        if !started {
            continue;
        }
        if l == "END" {
            break;
        }
        if let Some(id) = l.strip_prefix("P ") {
            out.pass.insert(id.to_string());
        } else if let Some(id) = l.strip_prefix("F ") {
            out.fail.insert(id.to_string());
        } else if let Some(p) = l.strip_prefix("I ") {
            out.import_fail.push(p.to_string());
        } else if let Some(a) = l.strip_prefix("A ") {
            if let Some(name) = a.strip_suffix(" ok") {
                out.adapt_ok.insert(name.to_string());
            }
        }
    }
    Some(out)
}

// ---------------------------------------------------------------- packing

/// Split failing tests over workers minimising max load + 2s per worker.
fn pack(tests: &[Test], sel: &BTreeSet<usize>) -> Vec<Vec<i64>> {
    if sel.is_empty() {
        return vec![];
    }
    let mut items: Vec<(u64, i64)> = sel.iter().map(|&i| (tests[i].ms, tests[i].number)).collect();
    items.sort_by(|a, b| b.0.cmp(&a.0).then(a.1.cmp(&b.1)));
    let mut best: Option<(u64, Vec<Vec<i64>>)> = None;
    for k in 1..=8usize.min(items.len()) {
        let mut loads = vec![0u64; k];
        let mut ws: Vec<Vec<i64>> = vec![vec![]; k];
        for (ms, n) in &items {
            let (j, _) = loads.iter().enumerate().min_by_key(|(_, l)| **l).unwrap();
            loads[j] += ms;
            ws[j].push(*n);
        }
        let cost = loads.iter().max().unwrap() + 2000 * k as u64;
        if best.as_ref().map_or(true, |b| cost < b.0) {
            best = Some((cost, ws));
        }
    }
    best.unwrap().1
}

fn main() {
    let t0 = Instant::now();
    let data = PathBuf::from(std::env::var("BR_DATA").unwrap_or("/data".into()));
    let app = PathBuf::from(std::env::var("BR_APP").unwrap_or("/app".into()));
    let src_root = PathBuf::from(std::env::var("BR_SRC").unwrap_or(app.join("src").display().to_string()));
    let tmp = PathBuf::from(std::env::var("BR_TMP").unwrap_or("/tmp".into()));
    let python = std::env::var("BR_PYTHON").unwrap_or("python".into());
    let nrun: usize = std::env::var("BR_RUNNERS").ok().and_then(|x| x.parse().ok()).unwrap_or(2);
    let budget: f64 = std::env::var("BR_BUDGET").ok().and_then(|x| x.parse().ok()).unwrap_or(150.0);
    let norun = std::env::var("BR_NORUN").is_ok();

    let tests = load_tests(&data.join("tests.json"));
    let map = load_map(&app.join("map.txt"));
    let id2t: HashMap<&str, usize> = tests.iter().enumerate().map(|(i, t)| (t.id.as_str(), i)).collect();
    let map2t: Vec<Option<usize>> = map.ids.iter().map(|id| id2t.get(id.as_str()).copied()).collect();
    let mut by_file: HashMap<String, Vec<usize>> = HashMap::new();
    for (i, t) in tests.iter().enumerate() {
        by_file.entry(t.file.clone()).or_default().push(i);
    }
    let unmapped: Vec<usize> = (0..tests.len()).filter(|i| !map2t.contains(&Some(*i))).collect();
    if !unmapped.is_empty() {
        log!("{} tests not in map", unmapped.len());
    }
    let id2t_owned: HashMap<String, usize> = id2t.iter().map(|(k, v)| (k.to_string(), *v)).collect();
    drop(id2t);
    let ctx = Ctx { src_root: src_root.clone(), tests, map, map2t, by_file, id2t: id2t_owned };

    let mut commits: Vec<(String, PathBuf)> = Vec::new();
    if let Ok(rd) = fs::read_dir(data.join("commits")) {
        for e in rd.flatten() {
            let p = e.path();
            if p.extension().map_or(false, |x| x == "patch") {
                commits.push((p.file_stem().unwrap().to_string_lossy().to_string(), p));
            }
        }
    }
    commits.sort();

    let mut plans: Vec<Plan> = Vec::new();
    for (c, p) in &commits {
        let text = fs::read(p).map(|b| String::from_utf8_lossy(&b).into_owned()).unwrap_or_default();
        let planned = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
            let mut pl = ctx.plan(c, &text);
            if pl.ok && !norun {
                ctx.decide(&mut pl);
                pl.ok = true;
            }
            pl
        }));
        let mut pl = match planned {
            Ok(p) => p,
            Err(_) => {
                log!("{c}: planning panicked: including every test");
                Plan { commit: c.clone(), adapters: vec![], run: BTreeSet::new(), force: (0..ctx.tests.len()).collect(), changed: vec![], ok: true }
            }
        };
        // unmapped tests: always include when anything python changed (we know nothing about them)
        if !unmapped.is_empty() && pl.changed.iter().any(|(p, _)| p.ends_with(".py")) {
            for &u in &unmapped {
                pl.force.insert(u);
            }
        }
        if !pl.ok {
            // could not build the patched tree: include what we selected without running
            let n = ctx.tests.len();
            let r: Vec<usize> = pl.run.iter().copied().filter(|&i| i < n).collect();
            pl.force.extend(r);
            pl.run.clear();
        } else if !norun {
            // decided above
        } else {
            let n = ctx.tests.len();
            pl.run.retain(|&i| i < n);
        }
        let est: u64 = pl.run.iter().map(|&i| ctx.tests[i].ms).sum();
        log!("{c}: {} to run (~{:.1}s), {} forced", pl.run.len(), est as f64 / 1000.0, pl.force.len());
        plans.push(pl);
    }
    log!("planning done in {:.2}s", t0.elapsed().as_secs_f64());

    let results: Arc<Mutex<Vec<BTreeSet<usize>>>> = Arc::new(Mutex::new(plans.iter().map(|p| p.force.clone()).collect()));
    if !norun {
        let chunk_ms: u64 = std::env::var("BR_CHUNK_MS").ok().and_then(|x| x.parse().ok()).unwrap_or(8000);
        let plans = Arc::new(plans_take(&mut plans));
        let ctx = Arc::new(ctx);
        // round 1: candidates (+ import probes on each plan's first chunk)
        let mut jobs: Vec<Job> = Vec::new();
        for (pi, pl) in plans.iter().enumerate() {
            let probes: Vec<(String, String)> = pl
                .changed
                .iter()
                .filter(|(p, c)| c.is_some() && p.ends_with(".py") && (p.starts_with("sqlglot/") || p.starts_with("tests/")))
                .map(|(p, _)| (modname(p), p.clone()))
                .collect();
            let tests: Vec<usize> = pl.run.iter().copied().collect();
            let first = jobs.len();
            chunk_into(&mut jobs, pi, &tests, &ctx.tests, chunk_ms, probes.clone());
            if !pl.adapters.is_empty() {
                let est: u64 = pl.adapters.iter().map(|a| a.3).sum();
                let adapts: Vec<(String, Vec<String>, usize)> = pl.adapters.iter().map(|(n, its, t, _)| (n.clone(), its.clone(), *t)).collect();
                if jobs.len() > first {
                    // ride along with the commit's first job: one fork, one import
                    jobs[first].adapts = adapts;
                    jobs[first].est += est;
                } else {
                    jobs.push(Job { plan: pi, tests: vec![], est, probes, adapts });
                }
            }
        }
        let total_est: u64 = jobs.iter().map(|j| j.est + 450).sum();
        let nrun = if total_est < 40_000 { 1 } else { nrun };
        log!("estimated work {}ms -> {} runner(s)", total_est, nrun);
        let mut runners: Vec<Option<Runner>> = Vec::new();
        let rs = Arc::new(Mutex::new(Vec::<Option<Runner>>::new()));
        let ifails = run_round(jobs, nrun, &plans, &ctx, &results, &rs, &src_root, &tmp, &python, &app, t0, budget);
        // round 2: modules that fail to import break every test that imports them
        let mut jobs2: Vec<Job> = Vec::new();
        for (pi, files) in ifails.iter().enumerate() {
            if files.is_empty() {
                continue;
            }
            let mut extra: BTreeSet<usize> = BTreeSet::new();
            for f in files {
                log!("{}: {f} fails to import -> its importers", plans[pi].commit);
                if let Some(fm) = ctx.map.files.get(f) {
                    if let Some(d) = fm.dep {
                        ctx.add_set(&mut extra, d);
                    }
                }
                if f.starts_with("tests/") {
                    if let Some(v) = ctx.by_file.get(f) {
                        extra.extend(v.iter().copied());
                    }
                }
            }
            let already = &plans[pi].run;
            let n = ctx.tests.len();
            let mut tests: Vec<usize> = Vec::new();
            for t in extra {
                if t >= n || already.contains(&t) {
                    continue;
                }
                // the module no longer imports: its importers fail, no need to run them
                results.lock().unwrap()[pi].insert(t);
            }
            chunk_into(&mut jobs2, pi, &tests, &ctx.tests, 20000, vec![]);
            // an import failure can also break the heavy tests we planned to check
            for (_, _, t, _) in &plans[pi].adapters {
                if !plans[pi].run.contains(t) {
                    results.lock().unwrap()[pi].insert(*t);
                }
            }
        }
        if !jobs2.is_empty() {
            run_round(jobs2, nrun, &plans, &ctx, &results, &rs, &src_root, &tmp, &python, &app, t0, budget + 15.0);
        }
        for r in rs.lock().unwrap().drain(..) {
            runners.push(r);
        }
        for r in runners.into_iter().flatten() {
            let Runner { mut child, stdin, stdout } = r;
            drop(stdin);
            drop(stdout);
            let _ = child.wait();
        }
        let res = results.lock().unwrap();
        let stdout = std::io::stdout();
        let mut out = stdout.lock();
        for (i, pl) in plans.iter().enumerate() {
            let ws = pack(&ctx.tests, &res[i]);
            let _ = writeln!(out, "{}", fmt_line(&pl.commit, &ws));
        }
    } else {
        let res = results.lock().unwrap();
        let stdout = std::io::stdout();
        let mut out = stdout.lock();
        for (i, pl) in plans.iter().enumerate() {
            let mut sel = res[i].clone();
            sel.extend(pl.run.iter().copied());
            let ws = pack(&ctx.tests, &sel);
            let _ = writeln!(out, "{}", fmt_line(&pl.commit, &ws));
        }
    }
    log!("total {:.2}s", t0.elapsed().as_secs_f64());
}

struct Job {
    plan: usize,
    tests: Vec<usize>,
    est: u64,
    probes: Vec<(String, String)>,
    adapts: Vec<(String, Vec<String>, usize)>,
}

fn modname(p: &str) -> String {
    let m = p.trim_end_matches(".py").replace('/', ".");
    m.strip_suffix(".__init__").map(|x| x.to_string()).unwrap_or(m)
}

fn chunk_into(jobs: &mut Vec<Job>, pi: usize, tests: &[usize], all: &[Test], chunk_ms: u64, probes: Vec<(String, String)>) {
    let mut probes = Some(probes);
    let mut cur: Vec<usize> = Vec::new();
    let mut cur_ms = 0u64;
    for &t in tests {
        let ms = all[t].ms + 2;
        if !cur.is_empty() && cur_ms + ms > chunk_ms {
            jobs.push(Job { plan: pi, tests: std::mem::take(&mut cur), est: cur_ms, probes: probes.take().unwrap_or_default(), adapts: vec![] });
            cur_ms = 0;
        }
        cur.push(t);
        cur_ms += ms;
    }
    let p = probes.take().unwrap_or_default();
    if !cur.is_empty() || !p.is_empty() {
        jobs.push(Job { plan: pi, tests: cur, est: cur_ms, probes: p, adapts: vec![] });
    }
}

#[allow(clippy::too_many_arguments)]
fn run_round(
    mut jobs: Vec<Job>,
    nrun: usize,
    plans: &Arc<Vec<Plan>>,
    ctx: &Arc<Ctx>,
    results: &Arc<Mutex<Vec<BTreeSet<usize>>>>,
    rs: &Arc<Mutex<Vec<Option<Runner>>>>,
    src_root: &Path,
    tmp: &Path,
    python: &str,
    app: &Path,
    t0: Instant,
    budget: f64,
) -> Vec<Vec<String>> {
    // probes first within equal size, longest first overall
    jobs.sort_by_key(|j| std::cmp::Reverse(j.est + if j.probes.is_empty() { 0 } else { 300 }));
    log!("{} jobs", jobs.len());
    let queue = Arc::new(Mutex::new(std::collections::VecDeque::from(jobs)));
    let ifails: Arc<Mutex<Vec<Vec<String>>>> = Arc::new(Mutex::new(vec![Vec::new(); plans.len()]));
    let mut handles = vec![];
    for w in 0..nrun {
        let queue = queue.clone();
        let plans = plans.clone();
        let ctx = ctx.clone();
        let results = results.clone();
        let ifails = ifails.clone();
        let rs = rs.clone();
        let src_root = src_root.to_path_buf();
        let tmp = tmp.to_path_buf();
        let python = python.to_string();
        let script = app.join("runner.py");
        handles.push(std::thread::spawn(move || {
            let mut runner = {
                let mut g = rs.lock().unwrap();
                if g.len() > w { g[w].take() } else { None }
            };
            if runner.is_none() {
                runner = spawn_runner(&python, &script, &src_root);
                if runner.is_none() {
                    log!("runner {w} failed to start");
                }
            }
            let mut jn = 0usize;
            loop {
                let job = queue.lock().unwrap().pop_front();
                let Some(job) = job else { break };
                jn += 1;
                let pl = &plans[job.plan];
                let over = t0.elapsed().as_secs_f64() > budget;
                let mut fails: BTreeSet<usize> = BTreeSet::new();
                let mut ok = false;
                if !over {
                    if let Some(r) = runner.as_mut() {
                        let farm = tmp.join(format!("farm_{w}_{jn}_{}", job.plan));
                        if build_farm(&src_root, &farm, &pl.changed).is_ok() {
                            let items: Vec<(f64, &str)> =
                                job.tests.iter().map(|&i| (ctx.tests[i].ms as f64 / 1000.0, ctx.tests[i].id.as_str())).collect();
                            let ts = Instant::now();
                            let sq: Vec<String> = pl.changed.iter().filter(|(p, _)| p.starts_with("sqlglot/") && p.ends_with(".py")).map(|(p, _)| modname(p)).collect();
                            let other_sq = pl.changed.iter().any(|(p, _)| p.starts_with("sqlglot/") && !p.ends_with(".py"));
                            let pre = if sq.is_empty() && !other_sq {
                                "1".to_string()
                            } else if std::env::var("BR_SELECTIVE").map_or(false, |v| v == "1") && !other_sq {
                                format!("S:{}", sq.join(","))
                            } else {
                                "0".to_string()
                            };
                            let tmo = if job.adapts.is_empty() { 20.0 } else { 20.0 + 3.0 * job.est as f64 / 1000.0 };
                            if let Some(o) = run_job(r, job.plan, &farm, tmo, &items, &pre, &job.probes, &job.adapts) {
                                ok = true;
                                for (name, _, t) in &job.adapts {
                                    if !o.adapt_ok.contains(name) {
                                        log!("{}: adapter {name} failed -> include {}", pl.commit, ctx.tests[*t].id);
                                        fails.insert(*t);
                                    }
                                }
                                for &i in &job.tests {
                                    let id = ctx.tests[i].id.as_str();
                                    if o.fail.contains(id) || !o.pass.contains(id) {
                                        fails.insert(i);
                                    }
                                }
                                if !o.import_fail.is_empty() {
                                    ifails.lock().unwrap()[job.plan].extend(o.import_fail.iter().cloned());
                                }
                                log!("{}: ran {} in {:.2}s -> {} failing", pl.commit, job.tests.len(), ts.elapsed().as_secs_f64(), fails.len());
                            } else {
                                log!("{}: runner died", pl.commit);
                                runner = spawn_runner(&python, &script, &src_root);
                            }
                        }
                        let _ = fs::remove_dir_all(&farm);
                    }
                }
                if !ok {
                    fails.extend(job.tests.iter().copied());
                    fails.extend(job.adapts.iter().map(|a| a.2));
                    if !job.probes.is_empty() {
                        // could not probe: assume the worst for changed modules
                        ifails.lock().unwrap()[job.plan].extend(job.probes.iter().map(|(_, p)| p.clone()));
                    }
                }
                results.lock().unwrap()[job.plan].extend(fails);
            }
            let mut g = rs.lock().unwrap();
            while g.len() <= w {
                g.push(None);
            }
            g[w] = runner;
        }));
    }
    for h in handles {
        let _ = h.join();
    }
    let v = ifails.lock().unwrap().clone();
    v
}

fn plans_take(p: &mut Vec<Plan>) -> Vec<Plan> {
    std::mem::take(p)
}

fn fmt_line(commit: &str, ws: &[Vec<i64>]) -> String {
    let inner: Vec<String> = ws
        .iter()
        .map(|w| format!("[{}]", w.iter().map(|n| n.to_string()).collect::<Vec<_>>().join(", ")))
        .collect();
    format!("{{\"commit\": {}, \"workers\": [{}]}}", json::esc(commit), inner.join(", "))
}
