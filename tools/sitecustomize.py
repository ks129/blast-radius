# Child-process coverage: active only when COV_CHILD_OUT is set by covplug.
import os

_out = os.environ.get("COV_CHILD_OUT")
if _out:
    import atexit
    import json
    import sys

    _root = os.path.realpath(os.environ.get("COV_ROOT", os.getcwd())) + os.sep
    _lines = set()
    _mon = sys.monitoring
    _tool = 1
    _ctool = 2
    _classes = set()

    _slines = set()

    def _in_import():
        f = sys._getframe(2)
        while f is not None:
            if f.f_code.co_filename.startswith("<frozen importlib"):
                return True
            f = f.f_back
        return False

    def _on_line(code, line):
        f = code.co_filename
        if f.startswith(_root):
            if code.co_flags & 1 and not _in_import():
                _lines.add((f[len(_root):], line))
                return _mon.DISABLE
            _slines.add((f[len(_root):], line))
            return None
        return _mon.DISABLE

    try:
        _mon.use_tool_id(_tool, "blastcov-child")
        _mon.register_callback(_tool, _mon.events.LINE, _on_line)
        _mon.set_events(_tool, _mon.events.LINE)

        def _on_call(code, off, fn, arg0):
            if not (code.co_flags & 1) or _in_import():
                return
            if isinstance(fn, type):
                _classes.add(fn)
            else:
                s = getattr(fn, "__self__", None)
                if s is not None:
                    _classes.add(s if isinstance(s, type) else type(s))

        _mon.use_tool_id(_ctool, "blastcalls-child")
        _mon.register_callback(_ctool, _mon.events.CALL, _on_call)
        _mon.set_events(_ctool, _mon.events.CALL)
    except Exception:
        pass

    def _dump():
        try:
            _mon.set_events(_tool, 0)
        except Exception:
            pass
        mods = sorted(
            m for m, v in list(sys.modules.items())
            if (m == "sqlglot" or m.startswith("sqlglot.") or m == "tests" or m.startswith("tests.")) and v is not None
        )
        names = {}
        for c in list(_classes):
            m = getattr(c, "__module__", "") or ""
            if not (m.startswith("sqlglot") or m.startswith("tests")):
                continue
            mro = []
            for k in getattr(c, "__mro__", ()):
                mk = getattr(k, "__module__", "") or ""
                if mk.startswith("sqlglot") or mk.startswith("tests"):
                    mro.append(mk + ":" + getattr(k, "__qualname__", k.__name__))
            names[m + ":" + getattr(c, "__qualname__", c.__name__)] = mro
        data = json.dumps({"lines": sorted(_lines), "slines": sorted(_slines), "mods": mods, "classes": names}) + "\n"
        fd = os.open(_out, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.write(fd, data.encode())
        os.close(fd)

    atexit.register(_dump)
