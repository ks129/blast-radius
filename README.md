# Blast Radius entry

A Rust selector that decides, for each sqlglot patch, which tests can fail, and
proves most of it by running a few of them against the patched tree.

## How it works

1. **Static map** (`app/map.txt`, built once from the pinned commit by
   `tools/`): per-test line coverage (function code only), modules imported,
   fixture files read, directories listed, and the sqlglot classes each test
   instantiates or calls into. Child processes (spawned pools, `python -c`
   subprocesses) are covered too.
   From it the builder derives, for every function, lambda, class-level and
   module-level statement and docstring, the set of tests a change there can
   reach:
   * code inside a function → tests that execute that function;
   * a class attribute → tests that use the class or a subclass *and* run code
     that reads that attribute name, plus classes that copy it
     (`{**Base.TRANSFORMS, ...}`);
   * a module constant → closure over everything that references the name;
   * a docstring → the doctest runner; a fixture → the tests that read it.
2. **Patch handling** (Rust, no crates): unified-diff parser/applier, a
   string-aware Python line classifier (comment-only edits are inert), and an
   indentation rule that places inserted lines in their enclosing function or
   statement.
3. **Running** (`app/runner.py`): two fork servers with pytest, duckdb and
   pandas preloaded. Each job runs in a forked child against a symlink farm of
   the patched tree in `/tmp`. Changed modules are compile+import probed; a
   module that no longer imports pulls in every test that imports it.
   Process pools inside tests are forked from the warm child (1 worker).
4. **Heavy tests** are never run in full. Adapters split them into items with
   their own offline coverage (TPC-DS/TPC-H queries, dialects for the memory
   test, a scaled-down `test_max_nodes`), and only the items a change reaches
   are run. If they pass the heavy test is left out.
5. **Cost model**: running costs CPU seconds; including a test unrun costs
   makespan over up to 8 workers plus 2 s per worker. Light candidate sets are
   run only when that is cheaper than including them; failures are packed
   with LPT over the best worker count.

## Layout

* `selector/` – the Rust program (`/app/selector`).
* `app/runner.py`, `app/adapters.py` – the Python side, `app/map.txt.gz` – the map.
* `tools/` – offline map building (coverage plugin, introspection, builder) and
  evaluation helpers (mutation generator, ground truth, scoring wrappers).
