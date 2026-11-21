"""Import every sqlglot/tests module under line monitoring.

Prints JSON: {"static_lines": [[file, line]...], "classes": {name: [mro names]}}
"""
import importlib
import json
import os
import pkgutil
import sys

ROOT = os.path.realpath(os.getcwd()) + os.sep
MON = sys.monitoring
lines = set()


def on_line(code, line):
    f = code.co_filename
    if f.startswith(ROOT):
        lines.add((f[len(ROOT):], line))
    return MON.DISABLE


MON.use_tool_id(1, "intro")
MON.register_callback(1, MON.events.LINE, on_line)
MON.set_events(1, MON.events.LINE)

import sqlglot  # noqa: E402

mods = []
for info in pkgutil.walk_packages(sqlglot.__path__, prefix="sqlglot."):
    if info.name == "sqlglot.__main__":
        continue
    try:
        mods.append(importlib.import_module(info.name))
    except Exception as e:
        print("import fail", info.name, e, file=sys.stderr)
MON.set_events(1, 0)
import tests  # noqa: E402

for info in pkgutil.walk_packages(tests.__path__, prefix="tests."):
    try:
        mods.append(importlib.import_module(info.name))
    except Exception as e:
        print("import fail", info.name, e, file=sys.stderr)
mods.append(sqlglot)


def ours(k):
    m = getattr(k, "__module__", "") or ""
    return m.startswith("sqlglot") or m.startswith("tests")


def cname(k):
    return k.__module__ + ":" + k.__qualname__


classes = {}


def visit(obj, modname, depth=0):
    for v in list(vars(obj).values()):
        if isinstance(v, type) and getattr(v, "__module__", None) == modname:
            n = cname(v)
            if n in classes:
                continue
            classes[n] = [cname(k) for k in v.__mro__ if ours(k)]
            if depth < 4:
                visit(v, modname, depth + 1)


for m in list(sys.modules.values()):
    if m is None:
        continue
    name = getattr(m, "__name__", "")
    if name.startswith("sqlglot") or name.startswith("tests"):
        try:
            visit(m, name)
        except Exception:
            pass

json.dump({"static_lines": sorted(lines), "classes": classes}, sys.stdout)
