"""usage: excess.py <tests.json> <answers.json> <out.jsonl> — per-commit cost over ideal."""
import json, sys
t = {x["number"]: round(x["seconds"] * 1000) for x in json.load(open(sys.argv[1]))["tests"]}
a = json.load(open(sys.argv[2]))["commits"]
def cost(ws): return (max(sum(t[x] for x in w) for w in ws) + 2000 * len(ws)) / 1000 if ws else 0
def ideal(f):
    f = sorted((t[n] for n in f), reverse=True)
    if not f: return 0
    best = 1e9
    for k in range(1, 9):
        l = [0] * k
        for d in f:
            i = l.index(min(l)); l[i] += d
        best = min(best, (max(l) + 2000 * k) / 1000)
    return best
tot = 0
rows = []
for line in open(sys.argv[3]):
    r = json.loads(line); c = r["commit"]; ws = [w for w in r["workers"] if w]
    co, idl = cost(ws), ideal(a[c]["failing"])
    tot += co - idl
    if co - idl > 0.3: rows.append((co - idl, c, a[c]["kind"], co, idl, sum(len(w) for w in ws)))
for e, c, k, co, idl, n in sorted(rows, reverse=True)[:15]:
    print(f"{c} {k:8} excess {e:5.1f} cost {co:5.1f} ideal {idl:5.1f} sel {n}")
print("total excess", round(tot, 1))
