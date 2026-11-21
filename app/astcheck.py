"""Is an edit inert? stdin lines: '<old path>\t<new path>' -> SAME | ANNOT | DIFF.

SAME : identical AST (formatting, comments, parentheses, quotes).
ANNOT: identical once function signature/local annotations are dropped, in a module with
       `from __future__ import annotations` (such annotations are never evaluated).
       Class-level annotations (dataclass fields) stay significant.
"""
import ast
import sys


def future_annotations(tree):
    return any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__" and any(a.name == "annotations" for a in n.names)
        for n in tree.body
    )


def strip(tree):
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            if not isinstance(fn, ast.Lambda):
                fn.returns = None
            a = fn.args
            for arg in a.posonlyargs + a.args + a.kwonlyargs + [a.vararg, a.kwarg]:
                if arg is not None:
                    arg.annotation = None
            if isinstance(fn, ast.Lambda):
                continue
            for n in ast.walk(fn):
                if isinstance(n, ast.AnnAssign) and n.simple:
                    n.annotation = ast.Constant(None)
    return tree


def check(old_src, new_src):
    try:
        a, b = ast.parse(old_src), ast.parse(new_src)
    except SyntaxError:
        return "DIFF"
    if ast.dump(a) == ast.dump(b):
        return "SAME"
    if future_annotations(a) and future_annotations(b) and ast.dump(strip(a)) == ast.dump(strip(b)):
        return "ANNOT"
    return "DIFF"


for line in sys.stdin:
    old, _, new = line.rstrip("\n").partition("\t")
    try:
        res = check(open(old, encoding="utf-8").read(), open(new, encoding="utf-8").read())
    except Exception:
        res = "DIFF"
    sys.stdout.write(res + "\n")
    sys.stdout.flush()
