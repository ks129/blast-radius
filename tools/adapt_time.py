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
    its_all = adapters.items(name)
    if name.startswith("fx_"):
        c0 = time.process_time()
        adapters.run_item(name, "base")
        cpu[f"ADAPT::{name}::base"] = time.process_time() - c0
    base_cost = cpu.get(f"ADAPT::{name}::base", 0.0) if name.startswith("fx_") else 0.0
    for it in its_all:
        # cost with a cold table cache: lazily loaded tables are part of an item's price
        if name.startswith("exec") or name == "opt_tpch":
            inst = adapters._exec["inst"]
            for tb in (inst.tpch_tables, inst.tpcds_tables):
                dict.clear(tb)
            adapters._exec["init"] = None
        c0 = time.process_time()
        try:
            adapters.run_item(name, it)
        except Exception as e:
            print("fail", name, it, e, file=sys.stderr)
        cpu[f"ADAPT::{name}::{it}"] = max(0.0, time.process_time() - c0 - base_cost)
with open(sys.argv[2], "w") as fh:
    for r in recs:
        r["cpu"] = cpu.get(r["id"], r["cpu"])
        fh.write(json.dumps(r) + "\n")
print({k: round(v, 2) for k, v in cpu.items() if k.endswith("base")}, round(sum(v for k, v in cpu.items() if "exec_tpcds" in k), 1), file=sys.stderr)
