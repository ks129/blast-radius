"""is <-> == swap mutants. usage: gen_swaps.py <root> <out-dir> <n> <seed>"""
import difflib, os, random, re, sys
root, out, n, seed = sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4])
rng = random.Random(seed); os.makedirs(out, exist_ok=True)
SW = [(" is not ", " != "), (" != ", " is not "), (" is ", " == "), (" == ", " is ")]
cands = []
for d, _, fs in os.walk(os.path.join(root, "sqlglot")):
    for f in fs:
        if not f.endswith(".py"):
            continue
        p = os.path.join(d, f)
        for i, l in enumerate(open(p, encoding="utf-8").read().split("\n")):
            s = l.strip()
            if s.startswith("#") or '"' in l or "'" in l:
                continue
            for a, b in SW:
                if l.count(a) == 1 and not (a == " is " and " is not " in l):
                    cands.append((os.path.relpath(p, root), i, a, b))
rng.shuffle(cands)
made = 0
for path, i, a, b in cands:
    if made >= n:
        break
    old = open(os.path.join(root, path), encoding="utf-8").read()
    lines = old.split("\n")
    lines[i] = lines[i].replace(a, b, 1)
    new = "\n".join(lines)
    body = "".join(difflib.unified_diff(old.splitlines(True), new.splitlines(True), "a/" + path, "b/" + path))
    open(os.path.join(out, f"q{seed}_{made:03d}.patch"), "w").write(f"diff --git a/{path} b/{path}\nindex 5bdce0a..4e0576d 100644\n" + body)
    made += 1
print("made", made, "of", len(cands), "candidates")
