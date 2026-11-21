"""Mutations of uppercase string-keyed dict entries. usage: gen_keyed.py <root> <out> <n> <seed>"""
import difflib, os, random, re, sys
root, out, n, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); os.makedirs(out, exist_ok=True)
pat = re.compile(r'^(\s+)"([A-Z][A-Z0-9_ ]{2,})":\s*(.+)$')
cands = []
for d, _, fs in os.walk(os.path.join(root, "sqlglot")):
    for f in fs:
        if f.endswith(".py"):
            p = os.path.join(d, f)
            for i, l in enumerate(open(p, encoding="utf-8").read().split("\n")):
                if pat.match(l):
                    cands.append((os.path.relpath(p, root), i))
rng.shuffle(cands)
made = 0
for path, i in cands:
    if made >= n: break
    old = open(os.path.join(root, path), encoding="utf-8").read()
    lines = old.split("\n")
    m = pat.match(lines[i]); ind, key, val = m.groups()
    r = rng.random()
    if r < 0.4:
        lines[i] = f'{ind}"{key[:-1]}": {val}'
    elif r < 0.7 and "." in val:
        toks = re.findall(r"\.([A-Z_]{3,})", val)
        if not toks: continue
        tk = rng.choice(toks); lines[i] = lines[i].replace("." + tk, "." + tk[:-1] if len(tk) > 4 else ".VAR", 1)
    else:
        if lines[i].rstrip().endswith(","): del lines[i]
        else: continue
    new = "\n".join(lines)
    body = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + path, "b/" + path))
    if not body: continue
    open(os.path.join(out, f"k{seed}_{made:03d}.patch"), "w").write(f"diff --git a/{path} b/{path}\nindex 5bdce0a..4e0576d 100644\n" + body)
    made += 1
print("made", made)
