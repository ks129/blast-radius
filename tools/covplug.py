"""pytest plugin: per-test line coverage, imported modules, opened files.

Writes one JSON line per test to $COV_OUT.
"""
import json
import os
import sys
import time

_t0 = [0.0]
_c0 = [0.0]

ROOT = os.path.realpath(os.environ.get("COV_ROOT", os.getcwd())) + os.sep
OUT = os.environ["COV_OUT"]
MON = sys.monitoring
TOOL = MON.COVERAGE_ID

_lines = set()
_opens = set()
_procs = [False]
_active = [False]


_slines = set()


def _on_line(code, line):
    f = code.co_filename
    if f.startswith(ROOT):
        if code.co_flags & 1:  # CO_OPTIMIZED: function / lambda / comprehension
            _lines.add((f[len(ROOT):], line))
        else:  # module or class body: import-time execution
            _slines.add((f[len(ROOT):], line))
    return MON.DISABLE


def _audit(event, args):
    if event == "open":
        p = args[0]
        if isinstance(p, (str, bytes)) and not isinstance(p, int):
            if isinstance(p, bytes):
                p = p.decode(errors="replace")
            try:
                rp = os.path.realpath(p)
            except Exception:
                return
            if rp.startswith(ROOT):
                _opens.add(rp[len(ROOT):])
    elif event in ("os.listdir", "os.scandir", "glob.glob"):
        p = args[0] if args else None
        if isinstance(p, (str, bytes)):
            if isinstance(p, bytes):
                p = p.decode(errors="replace")
            try:
                rp = os.path.realpath(p if event != "glob.glob" else os.path.dirname(p) or ".")
            except Exception:
                return
            if rp.startswith(ROOT) or rp + os.sep == ROOT:
                _opens.add("dir:" + rp[len(ROOT):])
    elif event in ("subprocess.Popen", "os.fork", "os.posix_spawn", "os.spawn", "os.exec", "os.system"):
        _procs[0] = True


_classes = set()
_ctype = type


def _on_call(code, off, fn, arg0):
    if isinstance(fn, _ctype):
        _classes.add(fn)
    else:
        s = getattr(fn, "__self__", None)
        if s is not None and isinstance(s, _ctype):
            _classes.add(s)
        elif s is not None:
            _classes.add(_ctype(s))


def _cname(k):
    return (getattr(k, "__module__", "") or "") + ":" + getattr(k, "__qualname__", getattr(k, "__name__", "?"))


def _ours(k):
    m = getattr(k, "__module__", "") or ""
    return m.startswith("sqlglot") or m.startswith("tests")


def class_names(classes):
    """concrete class names -> list of MRO names (ours only)."""
    out = {}
    for c in list(classes):
        try:
            mro = c.__mro__
        except Exception:
            continue
        if not _ours(c):
            continue
        out[_cname(c)] = [_cname(k) for k in mro if _ours(k)]
    return out


sys.addaudithook(_audit)
CTOOL = MON.PROFILER_ID
MON.use_tool_id(CTOOL, "blastcalls")
MON.register_callback(CTOOL, MON.events.CALL, _on_call)
MON.set_events(CTOOL, MON.events.CALL)
MON.use_tool_id(TOOL, "blastcov")
MON.register_callback(TOOL, MON.events.LINE, _on_line)

_pre_opens = None
_pre_classes = None


def pytest_runtest_protocol(item, nextitem):
    global _pre_opens
    if _pre_opens is None:
        _pre_opens = sorted(_opens)
    _lines.clear()
    _slines.clear()
    _opens.clear()
    _procs[0] = False
    global _pre_classes
    if _pre_classes is None:
        _pre_classes = class_names(_classes)
        _classes.clear()
    os.environ["COV_CHILD_OUT"] = OUT + ".child"
    try:
        os.unlink(OUT + ".child")
    except FileNotFoundError:
        pass
    MON.restart_events()
    MON.set_events(TOOL, MON.events.LINE)
    _t0[0] = time.perf_counter()
    _c0[0] = time.process_time()


def pytest_runtest_logfinish(nodeid, location):
    MON.set_events(TOOL, 0)
    mods = sorted(
        m for m, v in list(sys.modules.items())
        if (m == "sqlglot" or m.startswith("sqlglot.") or m == "tests" or m.startswith("tests.")) and v is not None
    )
    os.environ.pop("COV_CHILD_OUT", None)
    child_lines = set()
    child_slines = set()
    child_mods = set()
    child_classes = {}
    try:
        for cl in open(OUT + ".child"):
            c = json.loads(cl)
            child_lines.update(tuple(x) for x in c["lines"])
            child_slines.update(tuple(x) for x in c.get("slines", []))
            child_mods.update(c["mods"])
            child_classes.update(c.get("classes", []))
        os.unlink(OUT + ".child")
    except FileNotFoundError:
        pass
    mods = sorted(set(mods) | child_mods)
    rec = {
        "id": nodeid,
        "lines": sorted(_lines | child_lines),
        "nchild": len(child_lines),
        "slines": sorted(_slines | child_slines),
        "dur": time.perf_counter() - _t0[0],
        "cpu": time.process_time() - _c0[0],
        "classes": {**child_classes, **class_names(_classes)},
        "pre_classes": _pre_classes or {},
        "opens": sorted(_opens),
        "pre_opens": _pre_opens or [],
        "mods": mods,
        "procs": _procs[0],
    }
    with open(OUT, "a") as fh:
        fh.write(json.dumps(rec) + "\n")
