"""Per-item coverage records for adapters (run from the sqlglot root).

usage: adapt_cov.py <out.jsonl> <name>...
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/sub/app"))
ROOT = os.path.realpath(os.getcwd()) + os.sep
MON = sys.monitoring
lines, slines, classes = set(), set(), set()


def on_line(code, line):
    f = code.co_filename
    if f.startswith(ROOT):
        (lines if code.co_flags & 1 else slines).add((f[len(ROOT):], line))
    return MON.DISABLE


def on_call(code, off, fn, arg0):
    if isinstance(fn, type):
        classes.add(fn)
    else:
        s = getattr(fn, "__self__", None)
        if s is not None:
            classes.add(s if isinstance(s, type) else type(s))


def ours(k):
    m = getattr(k, "__module__", "") or ""
    return m.startswith("sqlglot") or m.startswith("tests")


def cname(k):
    return k.__module__ + ":" + getattr(k, "__qualname__", k.__name__)


MON.use_tool_id(1, "a")
MON.register_callback(1, MON.events.LINE, on_line)
MON.use_tool_id(2, "b")
MON.register_callback(2, MON.events.CALL, on_call)


def start():
    lines.clear()
    slines.clear()
    classes.clear()
    MON.restart_events()
    MON.set_events(1, MON.events.LINE)
    MON.set_events(2, MON.events.CALL)


def stop(rid, out, t0, c0):
    MON.set_events(1, 0)
    MON.set_events(2, 0)
    mods = sorted(m for m, v in list(sys.modules.items()) if v is not None and (m.startswith("sqlglot") or m.startswith("tests")))
    rec = {
        "id": rid, "lines": sorted(lines), "slines": sorted(slines), "opens": [], "pre_opens": [], "mods": mods,
        "procs": False, "classes": {cname(c): [cname(k) for k in c.__mro__ if ours(k)] for c in classes if ours(c)},
        "dur": time.perf_counter() - t0, "cpu": time.process_time() - c0,
    }
    out.write(json.dumps(rec) + "\n")
    out.flush()


import adapters  # noqa: E402

out = open(sys.argv[1], "a")
import sqlglot  # noqa: E402,F401
import sqlglot.dialects  # noqa: E402,F401
import tests.test_executor  # noqa: E402,F401
import tests.test_generator  # noqa: E402,F401
import tests.test_parser  # noqa: E402,F401

for name in sys.argv[2:]:
    t0, c0 = time.perf_counter(), time.process_time()
    its = adapters.items(name)
    t0, c0 = time.perf_counter(), time.process_time()
    start()
    adapters.setup(name)
    if name.startswith("fx_"):
        adapters.run_item(name, "base")  # the test's non-fixture part
    stop(f"ADAPT::{name}::base", out, t0, c0)
    for it in its:
        t0, c0 = time.perf_counter(), time.process_time()
        start()
        ok = True
        try:
            adapters.run_item(name, it)
        except Exception as e:
            ok = False
            print("item failed on clean tree:", name, it, repr(e), file=sys.stderr)
        stop(f"ADAPT::{name}::{it}", out, t0, c0)
