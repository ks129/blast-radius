"""Compare a selector output (candidates or final) against answers.

usage: recall.py <tests.json> <answers.json> <output.jsonl>
"""
import json
import sys

tests = json.load(open(sys.argv[1]))["tests"]
ans = json.load(open(sys.argv[2]))["commits"]
sec = {t["number"]: t["seconds"] for t in tests}
name = {t["number"]: t["id"] for t in tests}
tot_sel = 0
miss = 0
for line in open(sys.argv[3]):
    r = json.loads(line)
    c = r["commit"]
    sel = set(x for w in r["workers"] for x in w)
    if c not in ans:
        continue
    f = set(ans[c]["failing"])
    m = f - sel
    s = sum(sec[x] for x in sel)
    tot_sel += s
    flag = "MISS" if m else ""
    print(f"{c} {ans[c]['kind']:8} fail={len(f):4} sel={len(sel):5} selsec={s:7.1f} {flag} {[name[x] for x in sorted(m)][:4]}")
    miss += bool(m)
print("misses", miss, "selected seconds", round(tot_sel, 1))
