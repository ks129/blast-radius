"""Record, per comparison site in sqlglot, whether `a is b` and `a == b` ever disagree.

Active when BR_CMP_OUT is set. Rewrites single-operator Compare nodes using is / is not /
== / != in sqlglot modules into a call that evaluates the original operator (result
returned unchanged) and the swapped one, and records disagreement, exceptions or
non-bool results of == as "bad". At exit appends {"seen": [...], "bad": [...]} to BR_CMP_OUT.
"""
import os

_out = os.environ.get("BR_CMP_OUT")
if _out:
    import ast
    import atexit
    import builtins
    import importlib.abc
    import importlib.machinery
    import json
    import sys

    _root = os.path.realpath(os.environ.get("BR_CMP_ROOT", os.getcwd())) + os.sep
    seen, bad = set(), set()

    def _safe_bool(v):
        if isinstance(v, bool):
            return v
        raise TypeError("non-bool")

    def __brcmp__(site, op, a, b):
        if op == 0 or op == 1:  # is / is not
            r = a is b
            try:
                other = _safe_bool(a == b)
                if other != r:
                    bad.add(site)
            except BaseException:
                bad.add(site)
            seen.add(site)
            return r if op == 0 else not r
        r = (a == b) if op == 2 else (a != b)
        try:
            rb = _safe_bool(r)
            ident = a is b
            if (rb if op == 2 else not rb) != ident:
                bad.add(site)
        except BaseException:
            bad.add(site)
        seen.add(site)
        return r

    builtins.__brcmp__ = __brcmp__
    OPS = {ast.Is: 0, ast.IsNot: 1, ast.Eq: 2, ast.NotEq: 3}

    class T(ast.NodeTransformer):
        def __init__(self, rel):
            self.rel = rel

        def visit_Compare(self, node):
            self.generic_visit(node)
            if len(node.ops) != 1 or type(node.ops[0]) not in OPS:
                return node
            site = f"{self.rel}:{node.lineno}:{node.col_offset}"
            call = ast.Call(
                func=ast.Name("__brcmp__", ast.Load()),
                args=[ast.Constant(site), ast.Constant(OPS[type(node.ops[0])]), node.left, node.comparators[0]],
                keywords=[],
            )
            return ast.copy_location(call, node)

    class Loader(importlib.machinery.SourceFileLoader):
        def source_to_code(self, data, path, *, _optimize=-1):
            tree = ast.parse(data, filename=path)
            rel = os.path.realpath(path)[len(_root):]
            tree = ast.fix_missing_locations(T(rel).visit(tree))
            return compile(tree, path, "exec", dont_inherit=True)

        def get_code(self, fullname):  # never use cached bytecode
            path = self.get_filename(fullname)
            return self.source_to_code(self.get_data(path), path)

    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self, fullname, path, target=None):
            if not (fullname == "sqlglot" or fullname.startswith("sqlglot.")):
                return None
            spec = importlib.machinery.PathFinder.find_spec(fullname, path)
            if spec and spec.origin and spec.origin.endswith(".py") and os.path.realpath(spec.origin).startswith(_root):
                spec.loader = Loader(fullname, spec.origin)
                return spec
            return None

    sys.meta_path.insert(0, Finder())

    def _dump():
        try:
            with open(_out, "a") as fh:
                fh.write(json.dumps({"seen": sorted(seen), "bad": sorted(bad)}) + "\n")
        except Exception:
            pass

    atexit.register(_dump)
