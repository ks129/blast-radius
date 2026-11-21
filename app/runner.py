"""Fork server that runs pytest node ids inside a patched source farm.

Protocol (line based, stdin/stdout):
  in : JSON {"job": int, "root": str, "ids": [str], "timeout": float}
  out: JSON {"job": int, "fail": [str], "pass": [str], "unknown": [str]}
Everything else goes to stderr.
"""
import json
import os
import select
import signal
import sys
import time

os.environ["SKIP_INTEGRATION"] = "0"
for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS"):
    os.environ[_v] = "1"
os.environ["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
sys.dont_write_bytecode = True

# Heavy third-party imports done once, before forking.
import unittest  # noqa: E402,F401
import sqlite3  # noqa: E402,F401
import multiprocessing  # noqa: E402,F401
import concurrent.futures  # noqa: E402,F401
import pytest  # noqa: E402
import _pytest.unittest  # noqa: E402,F401
import _pytest.python  # noqa: E402,F401
import _pytest.subtests  # noqa: E402,F401

for _m in ("duckdb", "pandas", "pandas.testing", "numpy", "dateutil", "pytz"):
    try:
        __import__(_m)
    except Exception:
        pass

PRELOAD = ("sqlglot", "sqlglot.optimizer", "sqlglot.executor", "sqlglot.planner", "sqlglot.serde",
           "sqlglot.diff", "sqlglot.lineage", "sqlglot.schema", "sqlglot.dialects.dialect")
_pre_ok = False
if os.environ.get("BR_PRELOAD", "1") == "1":
    try:
        sys.path.insert(0, os.getcwd())
        for _m in PRELOAD:
            __import__(_m)
        _pre_ok = True
    except Exception:
        _pre_ok = False
    finally:
        sys.path.pop(0)

OUT = sys.stdout
sys.stdout = sys.stderr


class Reporter:
    def __init__(self, fd, wanted):
        self.fd = fd
        self.wanted = set(wanted)

    def _send(self, kind, nodeid):
        os.write(self.fd, (kind + " " + nodeid + "\n").encode())

    def pytest_collectreport(self, report):
        if report.failed:
            self._send("C", report.nodeid)

    def pytest_runtest_logstart(self, nodeid, location):
        self._send("S", nodeid)

    def pytest_runtest_logreport(self, report):
        if report.failed:
            self._send("F", report.nodeid)

    def pytest_runtest_logfinish(self, nodeid, location):
        self._send("E", nodeid)


def probe(root, probes, wfd):
    import importlib
    for mod, rel in probes:
        try:
            with open(os.path.join(root, rel), encoding="utf-8") as fh:
                compile(fh.read(), os.path.join(root, rel), "exec")
            if mod:
                importlib.import_module(mod)
        except BaseException as e:
            if os.environ.get("BR_DEBUG"):
                import traceback
                with open("/tmp/probe_err.txt", "a") as fh:
                    fh.write(rel + "\n" + traceback.format_exc() + "\n")
            os.write(wfd, ("I " + rel + "\n").encode())


def run_adapters(adapts, wfd):
    import adapters

    for name, its in adapts:
        ok = True
        try:
            adapters.setup(name)
            for it in its:
                adapters.run_item(name, it)
        except BaseException as e:
            ok = False
            if os.environ.get("BR_DEBUG"):
                import traceback
                with open("/tmp/adapt_err.txt", "a") as fh:
                    fh.write(name + "\n" + traceback.format_exc() + "\n")
        os.write(wfd, ("A " + name + (" ok" if ok else " fail") + "\n").encode())


SRC = os.path.realpath(os.getcwd())


def _ours(m):
    return m == "sqlglot" or m.startswith("sqlglot.")


def selective_purge(root, changed):
    """Drop changed modules and everything that holds references into them."""
    import types

    loaded = {m: v for m, v in list(sys.modules.items()) if _ours(m) and v is not None}
    purge = {m for m in changed if m in loaded}
    # a changed package __init__ or a brand-new module is handled the same way
    grew = True
    while grew:
        grew = False
        for name, mod in loaded.items():
            if name in purge:
                continue
            for v in list(vars(mod).values()):
                if isinstance(v, types.ModuleType):
                    vm = v.__name__
                else:
                    vm = getattr(v, "__module__", None) if isinstance(v, (type, types.FunctionType)) else None
                if vm in purge:
                    purge.add(name)
                    grew = True
                    break
    for m in purge:
        del sys.modules[m]
    for name, mod in list(sys.modules.items()):
        if _ours(name) and mod is not None and getattr(mod, "__path__", None) is not None:
            mod.__path__ = [p.replace(SRC, root, 1) for p in mod.__path__]
    return len(purge)


def child(root, ids, wfd, pre, probes=(), adapts=()):
    try:
        if not (pre == "1" and _pre_ok):
            done = False
            changed = pre[2:].split(",") if pre.startswith("S:") else None
            if changed is not None and _pre_ok:
                try:
                    selective_purge(root, changed)
                    done = True
                except Exception:
                    done = False
            if not done:
                for m in list(sys.modules):
                    if _ours(m):
                        del sys.modules[m]
        os.chdir(root)
        sys.path.insert(0, root)
        # process pools size themselves from the cpu count: keep them small,
        # and fork them from this (already warm) process instead of spawning.
        os.cpu_count = lambda: 1
        if hasattr(os, "process_cpu_count"):
            os.process_cpu_count = lambda: 1
        _gc = multiprocessing.get_context

        def _get_context(method=None):
            return _gc("fork" if method in (None, "spawn", "forkserver") else method)

        multiprocessing.get_context = _get_context
        import warnings
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        devnull = os.open(os.devnull, os.O_WRONLY)
        os.dup2(devnull, 1)
        os.dup2(devnull, 2)
        if probes:
            probe(root, probes, wfd)
        if adapts:
            run_adapters(adapts, wfd)
        if not ids:
            os._exit(0)
        args = ["-q", "-p", "no:cacheprovider", "-p", "no:randomly", "--rootdir", root, "-o", "addopts="] + list(ids)
        pytest.main(args, plugins=[Reporter(wfd, ids)])
    except BaseException:
        pass
    finally:
        os._exit(0)


def run_batch(root, ids, timeout, pre, probes=(), adapts=(), idle=30.0):
    """Returns (failed set, finished set, culprit or None)."""
    r, w = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(r)
        child(root, ids, w, pre, probes, adapts)
    os.close(w)
    failed, finished = set(), set()
    collect_fail = []
    import_fail = []
    adapt_res = []
    current = None
    started_at = time.monotonic()
    buf = b""
    timed_out = False
    while True:
        limit = timeout(current) if current else idle
        remaining = started_at + limit - time.monotonic()
        if remaining <= 0:
            timed_out = True
            break
        ready, _, _ = select.select([r], [], [], remaining)
        if not ready:
            continue
        data = os.read(r, 65536)
        if not data:
            break
        buf += data
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            kind, _, nodeid = line.decode().partition(" ")
            if kind == "S":
                current = nodeid
                started_at = time.monotonic()
            elif kind == "F":
                failed.add(nodeid.split(" ")[0])
            elif kind == "E":
                finished.add(nodeid)
                current = None
                started_at = time.monotonic()
            elif kind == "C":
                collect_fail.append(nodeid)
            elif kind == "I":
                import_fail.append(nodeid)
            elif kind == "A":
                adapt_res.append(nodeid)
                started_at = time.monotonic()
    if timed_out:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    os.close(r)
    os.waitpid(pid, 0)
    culprit = current if timed_out else None
    return failed, finished, collect_fail, culprit, import_fail, adapt_res


def handle(jobid, root, base, items, pre, probes, adapts=()):
    tmult = dict((i, m) for m, i in items)
    ids = [i for _, i in items]

    def timeout(nodeid):
        return base + 4.0 * tmult.get(nodeid, 0.0)

    todo = list(ids)
    fail, passed, unknown = set(), set(), set()
    ifail = []
    rounds = 0
    ares = []
    if (probes or adapts) and not todo:
        _, _, _, _, ifail, ares = run_batch(root, [], timeout, pre, probes, adapts, idle=base)
    while todo:
        rounds += 1
        if rounds > 60:
            unknown |= set(todo)
            break
        tset = set(todo)
        f, fin, cfail, culprit, ifl, _ = run_batch(root, todo, timeout, pre, probes if rounds == 1 else ())
        if rounds == 1:
            ifail = ifl
        fail |= {x for x in f if x in tset}
        for c in cfail:
            for x in todo:
                if x == c or x.startswith(c + "::") or x.split("::")[0] == c:
                    fail.add(x)
        if culprit:
            fail.add(culprit)
        for x in todo:
            if x in fin and x not in fail:
                passed.add(x)
        nxt = [x for x in todo if x not in fail and x not in passed]
        if not culprit:
            unknown |= set(nxt)
            break
        todo = nxt
    return fail, passed, unknown - fail - passed, ifail, ares


def main():
    OUT.write("READY\n")
    OUT.flush()
    inp = sys.stdin
    while True:
        line = inp.readline()
        if not line:
            break
        line = line.rstrip("\n")
        if not line.startswith("JOB "):
            continue
        _, jobid, root, base, pre = line.split(" ", 4)
        items = []
        probes = []
        adapts = []
        while True:
            l2 = inp.readline().rstrip("\n")
            if l2 == "END" or l2 == "":
                break
            if l2.startswith("ADAPT "):
                _, name, its = l2.split(" ", 2)
                adapts.append((name, its.split(",")))
                continue
            if l2.startswith("PROBE "):
                _, mod, rel = l2.split(" ", 2)
                probes.append((mod if mod != "-" else "", rel))
                continue
            m, _, i = l2.partition(" ")
            items.append((float(m), i))
        t0 = time.monotonic()
        fail, passed, unknown, ifail, ares = handle(jobid, root, float(base), items, pre, probes, adapts)
        out = ["RES " + jobid]
        out += ["F " + x for x in sorted(fail)]
        out += ["P " + x for x in sorted(passed)]
        out += ["U " + x for x in sorted(unknown)]
        out += ["I " + x for x in ifail]
        out += ["A " + x for x in ares]
        out.append("END")
        OUT.write("\n".join(out) + "\n")
        OUT.flush()
        print(f"job {jobid}: {len(items)} tests, {len(fail)} fail, {len(unknown)} unknown, {time.monotonic()-t0:.2f}s", file=sys.stderr)


if __name__ == "__main__":
    main()
