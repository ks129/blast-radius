"""Generate synthetic single-edit mutations of sqlglot as git-style patches.

usage: gen_mut.py <sqlglot-root> <out-dir> <count> <seed>
"""
import difflib
import os
import random
import re
import sys

root, outdir, count, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed)
os.makedirs(outdir, exist_ok=True)

py_files = []
for d, _, fs in os.walk(os.path.join(root, "sqlglot")):
    for f in fs:
        if f.endswith(".py"):
            py_files.append(os.path.relpath(os.path.join(d, f), root))
fix_files = []
for d, _, fs in os.walk(os.path.join(root, "tests", "fixtures")):
    for f in fs:
        if f.endswith(".sql"):
            fix_files.append(os.path.relpath(os.path.join(d, f), root))
py_files.sort()
fix_files.sort()

SWAPS = [
    (r"\band\b", "or"), (r"\bor\b", "and"), (r"==", "!="), (r"!=", "=="), (r"\bis not\b", "is"),
    (r"\bis\b(?! not)", "is not"), (r"\bTrue\b", "False"), (r"\bFalse\b", "True"), (r"<=", "<"),
    (r">=", ">"), (r"(?<![<>=!])<(?![<=])", "<="), (r"(?<![<>=!-])>(?![>=])", ">="), (r"\bnot ", ""),
    (r"\b(\d+)\b", None), (r'"([A-Z_]{2,})"', None), (r"\bif\b", "if not"), (r" \+ ", " - "),
    (r"\bNone\b", "0"), (r"\bin\b", "not in"),
]


def mutate_line(line):
    opts = []
    for pat, rep in SWAPS:
        for m in re.finditer(pat, line):
            opts.append((m, pat, rep))
    if not opts:
        return None
    m, pat, rep = rng.choice(opts)
    if rep is None:
        g = m.group(1)
        if g.isdigit():
            new = str(int(g) + rng.choice([1, -1, 2]))
        else:
            new = g[:-1] if len(g) > 2 else g + "X"
        a, b = m.span(1)
        return line[:a] + new + line[b:]
    a, b = m.span()
    return line[:a] + rep + line[b:]


def make_patch(path, old, new):
    d = difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + path, "b/" + path, n=3)
    body = "".join(d)
    if not body:
        return None
    return f"diff --git a/{path} b/{path}\nindex 5bdce0a..4e0576d 100644\n" + body


made = 0
attempts = 0
while made < count and attempts < count * 50:
    attempts += 1
    kind = rng.random()
    if kind < 0.12 and fix_files:
        path = rng.choice(fix_files)
        old = open(os.path.join(root, path), encoding="utf-8").read()
        lines = old.split("\n")
        cand = [i for i, l in enumerate(lines) if l.strip() and not l.startswith("#") and not l.startswith("--")]
        if not cand:
            continue
        i = rng.choice(cand)
        l = lines[i]
        toks = l.split(" ")
        j = rng.randrange(len(toks))
        toks[j] = toks[j] + rng.choice(["x", " 1", ""]) if rng.random() < 0.7 else toks[j][::-1]
        lines[i] = " ".join(toks)
        tag = "fixture"
    else:
        path = rng.choice(py_files)
        old = open(os.path.join(root, path), encoding="utf-8").read()
        lines = old.split("\n")
        cand = [i for i, l in enumerate(lines) if l.strip() and not l.strip().startswith("#") and not l.strip().startswith('"""')]
        if not cand:
            continue
        i = rng.choice(cand)
        if kind < 0.17:
            ind = re.match(r"\s*", lines[i]).group(0)
            lines.insert(i, ind + rng.choice(["# Cheap check first", "# Intentionally not cached", "# NOTE: keep in sync"]))
            tag = "comment"
        elif kind < 0.22 and not lines[i].rstrip().endswith((":", ",", "(", "[", "{")):
            del lines[i]
            tag = "delete"
        else:
            nl = mutate_line(lines[i])
            if nl is None or nl == lines[i]:
                continue
            lines[i] = nl
            tag = "code"
    new = "\n".join(lines)
    p = make_patch(path, old, new)
    if not p:
        continue
    name = f"m{seed}_{made:03d}"
    open(os.path.join(outdir, name + ".patch"), "w").write(p)
    open(os.path.join(outdir, name + ".kind"), "w").write(tag)
    made += 1
print("made", made, file=sys.stderr)
