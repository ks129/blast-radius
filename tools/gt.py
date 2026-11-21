"""Ground truth: apply each patch to a temp copy and run the full suite.

usage: gt.py <sqlglot-root> <tests.json> <commits-dir> <answers-out> [workers]
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

root, tests_json, cdir, out = sys.argv[1:5]
nw = sys.argv[5] if len(sys.argv) > 5 else "3"
tests = json.load(open(tests_json))["tests"]
idx = {t["id"]: t["number"] for t in tests}
byfile = {}
for t in tests:
    byfile.setdefault(t["file"], []).append(t["number"])
answers = json.load(open(out)) if os.path.exists(out) else {"commits": {}}
for fn in sorted(os.listdir(cdir)):
    if not fn.endswith(".patch"):
        continue
    cid = fn[:-6]
    if cid in answers["commits"]:
        continue
    tmp = tempfile.mkdtemp(prefix="gt_", dir="/tmp")
    try:
        shutil.rmtree(tmp)
        shutil.copytree(root, tmp, symlinks=False, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        r = subprocess.run(["patch", "-p1", "-s", "-i", os.path.abspath(os.path.join(cdir, fn))], cwd=tmp)
        if r.returncode != 0:
            print("patch failed", cid, file=sys.stderr)
            continue
        env = dict(os.environ, SKIP_INTEGRATION="0", GT_OUT=os.path.join(tmp, "gt.txt"),
                   PYTHONPATH=os.path.expanduser("~/tools"), PYTHONDONTWRITEBYTECODE="1")
        env["PATH"] = os.path.expanduser("~/venv/bin") + ":" + env["PATH"]
        subprocess.run(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "gtplug", "-n", nw,
                        "--timeout=900", "--continue-on-collection-errors", "tests"],
                       cwd=tmp, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        failed = set()
        for l in open(os.path.join(tmp, "gt.txt")).read().split():
            if l.startswith("COLLECT:"):
                f = l[len("COLLECT:"):]
                failed |= set(byfile.get(f, []))
            elif l in idx:
                failed.add(idx[l])
        kindf = os.path.join(cdir, cid + ".kind")
        kind = open(kindf).read().strip() if os.path.exists(kindf) else "?"
        answers["commits"][cid] = {"failing": sorted(failed), "kind": kind}
        print(cid, kind, len(failed), file=sys.stderr)
        json.dump(answers, open(out, "w"))
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
