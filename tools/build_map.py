"""Build the selection map from per-test coverage records.

usage: build_map.py <sqlglot-root> <tests.json> <cov-dir> <introspect.json> <out-map>
"""
import ast
import glob
import json
import os
import sys
from collections import defaultdict

root, tests_json, cov_dir, intro_json, out = sys.argv[1:6]
extra = sys.argv[6] if len(sys.argv) > 6 else None
tests = json.load(open(tests_json))["tests"]
real_n = len(tests)
virtual = []
if extra:
    for line in open(extra):
        r = json.loads(line)
        virtual.append(r)
for k, r in enumerate(virtual):
    tests.append({"number": real_n + k, "id": r["id"], "file": "ADAPT", "seconds": r.get("cpu", 0.0)})
idx = {t["id"]: t["number"] for t in tests}
intro = json.load(open(intro_json))
static_exec = set((f, l) for f, l in intro["static_lines"])

recs = {}
for f in glob.glob(os.path.join(cov_dir, "*.jsonl")):
    for line in open(f):
        r = json.loads(line)
        if r["id"] in idx and "slines" in r:
            recs[idx[r["id"]]] = r
for r in virtual:
    recs[idx[r["id"]]] = r
missing = [t["id"] for t in tests if t["number"] not in recs]
print("tests", len(tests), "records", len(recs), "missing", len(missing), file=sys.stderr)

line_tests = defaultdict(int)
mod_tests = defaultdict(int)
open_tests = defaultdict(int)
src_read = defaultdict(int)
users = defaultdict(int)  # concrete class -> tests (bitmask)
mro = dict(intro["classes"])
for n, r in recs.items():
    bit = 1 << n
    for f, l in r["lines"]:
        line_tests[(f, l)] |= bit
    for m in r["mods"]:
        mod_tests[m] |= bit
    for p in r["opens"]:
        if p.endswith(".pyc"):
            continue
        if p.endswith(".py"):
            src_read[p] |= bit
        else:
            open_tests[p] |= bit
    for p in r["pre_opens"]:
        if p.endswith(".pyc") or p.endswith(".py"):
            continue
        open_tests[p] |= bit
    for c, m in r.get("classes", {}).items():
        users[c] |= bit
        mro.setdefault(c, m)

subs = defaultdict(set)  # class -> classes having it in their mro (incl itself)
for k, m in mro.items():
    subs[k].add(k)
    for b in m:
        subs[b].add(k)

tests_by_class_id = defaultdict(int)  # "file::Class" -> tests (pytest ids)
for t in tests:
    parts = t["id"].split("::")
    if len(parts) >= 3:
        tests_by_class_id[(t["file"], parts[1])] |= 1 << t["number"]

ALL = set(range(len(tests)))


def modname(path):
    p = path[:-3].replace("/", ".")
    if p.endswith(".__init__"):
        p = p[: -len(".__init__")]
    return p


def runtime_lines(path, a, b):
    s = 0
    for l in range(a, b + 1):
        s |= line_tests.get((path, l), 0)
    return s


# ---------------------------------------------------------------- AST indexing
files = sorted(
    set(glob.glob("sqlglot/**/*.py", root_dir=root, recursive=True))
    | set(glob.glob("tests/**/*.py", root_dir=root, recursive=True))
)
trees = {}
for path in files:
    try:
        trees[path] = ast.parse(open(os.path.join(root, path), encoding="utf-8").read())
    except SyntaxError:
        pass


def idents(node):
    s = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name):
            s.add(n.id)
        elif isinstance(n, ast.Attribute):
            s.add(n.attr)
        elif isinstance(n, ast.alias):
            s.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and len(n.value) < 80:
            v = n.value
            if v.isidentifier() and len(v) > 2:
                s.add(v)
    return s


def load_idents(node):
    """Identifiers read (not assigned) anywhere in node."""
    s = set()
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and not isinstance(n.ctx, ast.Store):
            s.add(n.id)
        elif isinstance(n, ast.Attribute) and not isinstance(n.ctx, ast.Store):
            s.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.isidentifier() and len(n.value) > 2:
            s.add(n.value)
    return s


def attr_chains(node):
    """(attr, owner-last-ident, owner-prev-ident) for every Attribute."""
    out = []
    for n in ast.walk(node):
        if isinstance(n, ast.Attribute):
            v = n.value
            last = v.id if isinstance(v, ast.Name) else (v.attr if isinstance(v, ast.Attribute) else None)
            prev = None
            if isinstance(v, ast.Attribute):
                vv = v.value
                prev = vv.id if isinstance(vv, ast.Name) else (vv.attr if isinstance(vv, ast.Attribute) else None)
            out.append((n.attr, last, prev))
    return out


def bound_names(stmt):
    s = set()
    for n in ast.walk(stmt):
        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Store):
            s.add(n.id)
        elif isinstance(n, ast.alias):
            s.add((n.asname or n.name).split(".")[0])
        elif isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            s.add(n.name)
    return s


def mutated_roots(stmt):
    """For `X.pop(..)`, `X[k] = v`, `X.update(..)` style statements: the root names."""
    s = set()
    for n in ast.walk(stmt):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and isinstance(n.func.value, ast.Name):
            s.add(n.func.value.id)
        if isinstance(n, ast.Subscript) and isinstance(n.ctx, (ast.Store, ast.Del)) and isinstance(n.value, ast.Name):
            s.add(n.value.id)
    return s


func_units = []  # (path, node, qual-class or None, start, end)
ident_funcs = defaultdict(list)  # ident -> unit indices
class_defs = []  # (path, node, cname, outer_name)
class_stmt_refs = defaultdict(set)  # ident -> class names whose static body references it
class_chain_refs = []  # (cname, attr, last, prev, bound names of stmt)
module_stmts = []  # (path, stmt)
ident_modstmts = defaultdict(list)


def walk_body(path, body, qual, cls):
    for st in body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef)):
            add_func(path, st, cls)
        elif isinstance(st, ast.ClassDef):
            q = (qual + "." if qual else "") + st.name
            cn = modname(path) + ":" + q
            class_defs.append((path, st, cn, cls))
            # decorators/bases referencing idents
            walk_body(path, st.body, q, cn)
            for n in st.decorator_list + st.bases + [k.value for k in st.keywords]:
                for i in idents(n):
                    class_stmt_refs[i].add(cn)
        else:
            if cls is None:
                module_stmts.append((path, st))
                for i in idents(st):
                    ident_modstmts[i].append(len(module_stmts) - 1)
            else:
                for i in load_idents(st):
                    class_stmt_refs[i].add(cls)
                for a, last, prev in attr_chains(st):
                    class_chain_refs.append((cls, a, last, prev, bound_names(st) | mutated_roots(st)))
            # functions / lambdas nested in statements
            for n in ast.walk(st):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    add_func(path, n, cls)
            for n in ast.walk(st):
                if isinstance(n, ast.Lambda):
                    add_lambda(path, n, cls)


def add_func(path, node, cls):
    start = min([node.lineno] + [d.lineno for d in node.decorator_list])
    func_units.append((path, node, cls, start, node.end_lineno))
    k = len(func_units) - 1
    for i in idents(node):
        ident_funcs[i].append(k)
    # nested functions/lambdas
    for n in ast.walk(node):
        if n is node:
            continue
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            s2 = min([n.lineno] + [d.lineno for d in n.decorator_list])
            func_units.append((path, n, cls, s2, n.end_lineno))
        elif isinstance(n, ast.Lambda):
            func_units.append((path, n, cls, n.lineno, n.end_lineno))


def add_lambda(path, node, cls):
    func_units.append((path, node, cls, node.lineno, node.end_lineno))


for path, tree in trees.items():
    walk_body(path, tree.body, "", None)

# dedupe units by (path,start,end)
seen_units = {}
for k, (path, node, cls, s, e) in enumerate(func_units):
    seen_units.setdefault((path, s, e), k)

unit_set_cache = {}


def unit_tests(k):
    if k not in unit_set_cache:
        path, node, cls, s, e = func_units[k]
        if isinstance(node, ast.Lambda):
            body_start = node.lineno
        else:
            body_start = node.body[0].lineno if node.body else node.lineno
            # a docstring is not executed; the first real statement may follow it
        unit_set_cache[k] = runtime_lines(path, body_start, e)
    return unit_set_cache[k]


_cu = {}


def class_users(cn):
    if cn in _cu:
        return _cu[cn]
    s = 0
    for k in subs.get(cn, {cn}):
        s |= users.get(k, 0)
    # test classes: pytest ids carry the class name
    mod, _, q = cn.partition(":")
    if mod.startswith("tests"):
        path = mod.replace(".", "/") + ".py"
        for k in subs.get(cn, {cn}):
            kmod, _, kq = k.partition(":")
            kp = kmod.replace(".", "/") + ".py"
            s |= tests_by_class_id.get((kp, kq.split(".")[-1]), 0)
    _cu[cn] = s
    return s


unit_chains = {}
chain_func = defaultdict(list)  # (attr,last) -> units
for k, (path, node, cls, st_, en_) in enumerate(func_units):
    for aa, last, prev in attr_chains(node):
        chain_func[(aa, last)].append(k)
class_chain_idx = defaultdict(list)
for (d, a, last, prev, bnames) in class_chain_refs:
    class_chain_idx[(a, last)].append((d, tuple(sorted(bnames))))


def chain_refs(cn, attrs, depth=0, seen=None):
    """Tests affected through other classes / functions referencing <cn>.attr."""
    if seen is None:
        seen = set()
    key = (cn, tuple(sorted(attrs)))
    if key in seen or depth > 4:
        return 0
    seen.add(key)
    simple = cn.split(":")[1].split(".")[-1]
    out = 0
    for a in attrs:
        for k in chain_func.get((a, simple), ()):
            out |= unit_tests(k)
        for d, bnames in set(class_chain_idx.get((a, simple), ())):
            du = class_users(d)
            rd = readers(set(bnames) or {a})
            if rd is not None:
                du &= rd
            out |= du
            out |= chain_refs(d, set(bnames) or {a}, depth + 1, seen)
    return out


_rd = {}


def func_static(k):
    path, node, cls, s_, e_ = func_units[k]
    if isinstance(node, ast.Lambda):
        return False
    start = node.body[0].lineno if node.body else node.lineno
    return any((path, l) in static_exec for l in range(start, e_ + 1))


ident_lines = defaultdict(set)  # ident -> (path, line) inside function bodies


def _index_lines():
    for k, (path, node, cls, s_, e_) in enumerate(func_units):
        body = node.body if isinstance(node.body, list) else [node.body]
        for b in body:
            for n in ast.walk(b):
                ln = getattr(n, "lineno", None)
                if ln is None:
                    continue
                if isinstance(n, ast.Name):
                    ident_lines[n.id].add((path, ln))
                elif isinstance(n, ast.Attribute):
                    # the attribute name sits on the last line of the node
                    ident_lines[n.attr].add((path, getattr(n, "end_lineno", ln)))
                elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value.isidentifier():
                    ident_lines[n.value].add((path, ln))


_index_lines()


def readers(names):
    """Tests running code that mentions any of the names; None = unknown/everyone."""
    key = tuple(sorted(names))
    if key in _rd:
        return _rd[key]
    out = 0
    for n in names:
        for k in ident_funcs.get(n, []):
            if func_static(k):
                if os.environ.get("DEBUG_READERS") and n in os.environ["DEBUG_READERS"].split(","):
                    fu = func_units[k]
                    print("readers: static func", n, fu[0], fu[3], fu[4], getattr(fu[1], "name", "lambda"), file=sys.stderr)
                _rd[key] = None
                return None
        for pl in ident_lines.get(n, ()):
            out |= line_tests.get(pl, 0)
        for cn in class_stmt_refs.get(n, set()):
            out |= class_users(cn)
    _rd[key] = out
    return out


_nc = {}


def name_closure(names, depth=0, seen=None, funcs_at_top=True):
    key = (tuple(sorted(names)), funcs_at_top)
    if depth == 0 and key in _nc:
        return _nc[key]
    if seen is None:
        seen = set()
    out = 0
    todo = set(names) - seen
    if not todo or depth > 4:
        return out
    seen |= todo
    nxt = set()
    for n in todo:
        if funcs_at_top or depth > 0:
            for k in ident_funcs.get(n, []):
                out |= unit_tests(k)
        for cn in class_stmt_refs.get(n, set()):
            out |= class_users(cn)
        for si in ident_modstmts.get(n, []):
            path, st = module_stmts[si]
            if isinstance(st, (ast.Import, ast.ImportFrom)):
                # an import rebinds the same object; only an alias is a new name
                for al in st.names:
                    if al.name.split(".")[-1] == n and al.asname and al.asname != n:
                        nxt.add(al.asname)
                continue
            b = bound_names(st)
            if n in b and len(b) == 1:
                continue
            nxt |= b
            out |= runtime_lines(path, st.lineno, st.end_lineno)
    res = out | name_closure(nxt, depth + 1, seen)
    if depth == 0:
        _nc[key] = res
    return res


# ---------------------------------------------------------------- sets
sets = {}
set_list = []


def bits(m):
    out = []
    i = 0
    while m:
        if m & 1:
            out.append(i)
        m >>= 1
        i += 1
    return out


def sid(s):
    if isinstance(s, int):
        key = tuple(bits(s))
    else:
        key = tuple(sorted(s))
    if key not in sets:
        sets[key] = len(set_list)
        set_list.append(key)
    return sets[key]


docs_tests = sum(1 << t["number"] for t in tests if t["file"] == "tests/test_docs.py")


def docstring_ranges(tree):
    out = []
    for n in ast.walk(tree):
        if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            b = n.body
            if b and isinstance(b[0], ast.Expr) and isinstance(b[0].value, ast.Constant) and isinstance(b[0].value.value, str):
                out.append((b[0].lineno, b[0].end_lineno))
    return out


out_lines = [f"T {len(tests)}"] + [f"I {t['number']} {t['id']}" for t in tests]
out_lines += [f"V {t['number']} {round(t['seconds'] * 1000)}" for t in tests[real_n:]]
file_sections = defaultdict(list)

# function units
for (path, s, e), k in sorted(seen_units.items()):
    path_, node, cls, s_, e_ = func_units[k]
    st = unit_tests(k)
    # executed at import time? then whoever uses what it built is affected
    if not isinstance(node, ast.Lambda):
        body_lines = range(node.body[0].lineno if node.body else node.lineno, e + 1)
        if any((path, l) in static_exec for l in body_lines):
            if cls is None:
                # runtime callers are covered by their own lines; what matters is what import built
                st |= name_closure({node.name}, funcs_at_top=False)
            else:
                st |= class_users(cls)
    file_sections[path].append(f"U {s} {e} {sid(st)}")

# classes: header + body ranges
for path, node, cn, outer in class_defs:
    cu = class_users(cn) | chain_refs(cn, {node.name}) | name_closure({node.name})
    hs = min([node.lineno] + [d.lineno for d in node.decorator_list])
    he = node.body[0].lineno - 1 if node.body else node.end_lineno
    if he < hs:
        he = hs
    file_sections[path].append(f"X {hs} {he} {sid(cu | runtime_lines(path, hs, he))}")
    body_set = class_users(cn)
    file_sections[path].append(f"C {node.lineno} {node.end_lineno} {sid(body_set)}")
    for st in node.body:
        if isinstance(st, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        names = bound_names(st) | mutated_roots(st)
        cu = class_users(cn)
        if names:
            rd = readers(names)
            if rd is not None:
                cu &= rd
        else:
            names = idents(st)
        s = cu | chain_refs(cn, names) | runtime_lines(path, st.lineno, st.end_lineno)
        file_sections[path].append(f"X {st.lineno} {st.end_lineno} {sid(s)}")

# module-level statements
coarse_kinds = (ast.Expr, ast.If, ast.Try, ast.For, ast.While, ast.With)
for path, st in module_stmts:
    if isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant):
        continue  # docstring / bare constant: docstring rule handles it
    if isinstance(st, coarse_kinds) and not (isinstance(st, ast.If) and "TYPE_CHECKING" in ast.unparse(st.test)):
        continue  # no X entry: Rust falls back to module dependents
    names = bound_names(st)
    s = name_closure(names) | runtime_lines(path, st.lineno, st.end_lineno)
    file_sections[path].append(f"X {st.lineno} {st.end_lineno} {sid(s)}")

for path, tree in trees.items():
    for a, b in docstring_ranges(tree):
        file_sections[path].append(f"Z {a} {b} {sid(docs_tests)}")
    src = open(os.path.join(root, path), encoding="utf-8").read()
    nlines = src.count("\n") + 1
    for l in range(1, nlines + 1):
        s = line_tests.get((path, l), 0)
        if s:
            file_sections[path].append(f"L {l} {sid(s)}")
    file_sections[path].append(f"D {sid(mod_tests.get(modname(path), 0))}")
    if path in src_read:
        file_sections[path].append(f"R {sid(src_read[path])}")

for p, s in sorted(open_tests.items()):
    file_sections[p].append(f"D {sid(s)}")

# test method source ranges (for key-text matching)
for t in tests[:real_n]:
    parts = t["id"].split("::")
    tree = trees.get(t["file"])
    if tree is None or len(parts) < 2:
        continue
    found = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and len(parts) >= 3 and node.name == parts[1]:
            for f in node.body:
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) and f.name == parts[2]:
                    found = f
        elif len(parts) == 2 and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == parts[1]:
            found = node
    if found is not None:
        start = min([found.lineno] + [d.lineno for d in found.decorator_list])
        out_lines.append(f"M {t['number']} {start} {found.end_lineno}")

for i, s in enumerate(set_list):
    out_lines.append(f"S {i} " + " ".join(map(str, s)))
for path in sorted(file_sections):
    out_lines.append(f"F {path}")
    out_lines.extend(file_sections[path])
with open(out, "w") as fh:
    fh.write("\n".join(out_lines) + "\n")
print("sets", len(set_list), "bytes", os.path.getsize(out), file=sys.stderr)
