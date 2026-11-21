"""Re-time adapter items without tracing; rewrite cpu in a records file.

usage: adapt_time.py <in.jsonl> <out.jsonl>
"""
import json
import os
import sys
import time

sys.path.insert(0, os.path.expanduser("~/sub/app"))
import adapters  # noqa: E402

recs = [json.loads(l) for l in open(sys.argv[1])]
cpu = {}
for name in dict.fromkeys(r["id"].split("::")[1] for r in recs):
    c0 = time.process_time()
    adapters.setup(name)
    cpu[f"ADAPT::{name}::base"] = time.process_time() - c0
    for it in adapters.items(name):
        c0 = time.process_time()
        try:
            adapters.run_item(name, it)
        except Exception as e:
            print("fail", name, it, e, file=sys.stderr)
        cpu[f"ADAPT::{name}::{it}"] = time.process_time() - c0
with open(sys.argv[2], "w") as fh:
    for r in recs:
        r["cpu"] = cpu.get(r["id"], r["cpu"])
        fh.write(json.dumps(r) + "\n")
print({k: round(v, 2) for k, v in cpu.items() if k.endswith("base")}, round(sum(v for k, v in cpu.items() if "exec_tpcds" in k), 1), file=sys.stderr)
