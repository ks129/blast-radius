"""Run every item of an adapter in the CWD tree; print failing items.
usage (cwd = patched tree): item_fail.py <name>
"""
import os, sys, time
sys.path.insert(0, os.getcwd())
sys.path.insert(1, os.path.expanduser("~/sub/app"))
import adapters
name = sys.argv[1]
adapters.setup(name)
fails = []
for it in adapters.items(name):
    try:
        adapters.run_item(name, it)
    except Exception as e:
        fails.append(it)
print(name, "failing items:", len(fails), fails[:40])
