"""Label patches by running a candidate superset. usage: gt_cand.py <commits-dir> <cand.jsonl> <tests.json> <out.json>"""
import json, os, shutil, subprocess, sys, tempfile
cdir, cand, tj, out = sys.argv[1:5]
tests = {t["number"]: t["id"] for t in json.load(open(tj))["tests"]}
idx = {v: k for k, v in tests.items()}
res = json.load(open(out)) if os.path.exists(out) else {"commits": {}}
root = os.path.expanduser("~/sqlglot")
for line in open(cand):
    r = json.loads(line); c = r["commit"]
    if c in res["commits"]: continue
    ids = [tests[x] for w in r["workers"] for x in w]
    failed = set()
    if ids:
        tmp = tempfile.mkdtemp(dir="/tmp", prefix="gk_"); shutil.rmtree(tmp)
        shutil.copytree(root, tmp, ignore=shutil.ignore_patterns("__pycache__", ".git"))
        subprocess.run(["patch", "-s", "-p1", "-i", os.path.abspath(f"{cdir}/{c}.patch")], cwd=tmp)
        env = dict(os.environ, SKIP_INTEGRATION="0", GT_OUT=tmp + "/gt.txt", PYTHONPATH=os.path.expanduser("~/tools"), PYTHONDONTWRITEBYTECODE="1")
        env["PATH"] = os.path.expanduser("~/venv/bin") + ":" + env["PATH"]
        open(tmp + "/ids.txt", "w").write("\n".join(ids))
        subprocess.run(["python", "-m", "pytest", "-q", "-p", "no:cacheprovider", "-p", "gtplug", "-n", "3", "--timeout=900",
                        "--continue-on-collection-errors"] + ids, cwd=tmp, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for l in open(tmp + "/gt.txt").read().split():
            if l in idx: failed.add(idx[l])
        shutil.rmtree(tmp, ignore_errors=True)
    res["commits"][c] = {"failing": sorted(failed), "kind": "keyed"}
    json.dump(res, open(out, "w"))
    print(c, len(ids), len(failed), flush=True)
