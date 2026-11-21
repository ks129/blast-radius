#!/bin/bash
# usage: stats.sh <map> ; candidate stats for mut2 + sample recall
cd ~
cp $1 sub/app/map.txt
BR_NORUN=1 BR_DATA=~/mut2data BR_APP=~/sub/app BR_SRC=~/sqlglot ~/sub/selector/target/release/selector > mut2_cand.jsonl 2>mut2_cand.err
python3 - <<PY
import json, collections
t={x["number"]:x for x in json.load(open("starter/sample/tests.json"))["tests"]}
heavy=[n for n,x in t.items() if x["seconds"]>=1.0]
cnt=collections.Counter(); tot=0; n=0; cheap=[]
for l in open("mut2_cand.jsonl"):
    r=json.loads(l); s=set(x for w in r["workers"] for x in w); n+=1
    for h in heavy:
        if h in s: cnt[h]+=1
    cheap.append(sum(t[x]["seconds"] for x in s if t[x]["seconds"]<1.0))
    tot+=sum(t[x]["seconds"] for x in s)
print("commits",n,"avg selected sec",round(tot/n,2), "avg cheap sec", round(sum(cheap)/n,2))
for h,c in cnt.most_common(8): print(c, t[h]["seconds"], t[h]["id"])
PY
BR_NORUN=1 BR_DATA=~/starter/sample BR_APP=~/sub/app BR_SRC=~/sqlglot ~/sub/selector/target/release/selector > s_cand.jsonl 2>/dev/null
python3 tools/recall.py starter/sample/tests.json starter/sample/answers.json s_cand.jsonl | tail -1
for a in mut1; do BR_NORUN=1 BR_DATA=~/${a}data BR_APP=~/sub/app BR_SRC=~/sqlglot ~/sub/selector/target/release/selector > ${a}_cand.jsonl 2>/dev/null; python3 tools/recall.py starter/sample/tests.json ${a}_answers.json ${a}_cand.jsonl | grep -E "MISS|misses"; done
