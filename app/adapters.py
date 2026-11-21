"""Partial runs of heavy tests, built from the tests' own helpers.

Each adapter splits one heavy test into items. Offline, every item gets its
own coverage record; at run time only the items a change can reach are run.
If they all pass the heavy test is left out, otherwise it is included.
"""
import pickle

TESTS = {
    "exec_tpcds": "tests/test_executor.py::TestExecutor::test_execute_tpcds",
    "exec_tpch": "tests/test_executor.py::TestExecutor::test_execute_tpch",
    "opt_tpch": "tests/test_executor.py::TestExecutor::test_optimized_tpch",
    "gen_memory": "tests/test_generator.py::TestGenerator::test_generator_memory",
    "max_nodes": "tests/test_parser.py::TestParser::test_max_nodes",
}

_exec = {}


class _LazyTables(dict):
    """Python-side tables, parsed from CSV only when a query touches them."""

    def __init__(self, m, directory, schema):
        super().__init__()
        self.m, self.directory, self.schema = m, directory, schema

    def __missing__(self, table):
        import ast as _ast
        import csv

        m = self.m
        columns = self.schema[table]
        file_name = f"{self.directory}{table}.csv.gz"
        reader = csv.reader(m.open_file(file_name), delimiter="|")
        rows, ctypes = [], []
        next(reader)
        for row in reader:
            if not ctypes:
                for v in row:
                    try:
                        ctypes.append(type(_ast.literal_eval(v)))
                    except (ValueError, SyntaxError):
                        ctypes.append(str)
            rows.append(tuple(None if (t is not str and v == "") else t(v) for t, v in zip(ctypes, row)))
        value = m.Table(columns=columns, rows=rows)
        self[table] = value
        return value

    def __contains__(self, key):
        return key in self.schema


def _executor():
    """Same state as TestExecutor.setUpClass, with tables parsed lazily."""
    if "m" not in _exec:
        import duckdb
        import tests.test_executor as m

        cls = m.TestExecutor
        cls.tpch_conn = duckdb.connect()
        cls.tpcds_conn = duckdb.connect()
        for conn, directory, schema in ((cls.tpch_conn, m.DIR_TPCH, m.TPCH_SCHEMA), (cls.tpcds_conn, m.DIR_TPCDS, m.TPCDS_SCHEMA)):
            for table, columns in schema.items():
                file_name = f"{directory}{table}.csv.gz"
                conn.execute(
                    f"""
                CREATE VIEW {table} AS
                SELECT *
                FROM READ_CSV('{file_name}', delim='|', header=True, columns={columns})
                """
                )
        cls.tpch_tables = _LazyTables(m, m.DIR_TPCH, m.TPCH_SCHEMA)
        cls.tpcds_tables = _LazyTables(m, m.DIR_TPCDS, m.TPCDS_SCHEMA)
        cls.cache = {}
        cls.tpch_sqls = list(m.load_sql_fixture_pairs("optimizer/tpc-h/tpc-h.sql"))
        cls.tpcds_sqls = list(m.load_sql_fixture_pairs("optimizer/tpc-ds/tpc-ds.sql"))
        inst = cls("test_execute_tpch")
        _exec["m"] = m
        _exec["inst"] = inst
        _exec["init"] = None
    return _exec["m"], _exec["inst"]


def items(name):
    if name in ("exec_tpcds", "exec_tpch", "opt_tpch"):
        m, inst = _executor()
        n = len(inst.tpcds_sqls) if name == "exec_tpcds" else len(inst.tpch_sqls)
        return [str(i) for i in range(n)]
    if name == "gen_memory":
        from sqlglot.dialects import Dialects

        return [str(d.value) for d in Dialects]
    if name == "max_nodes":
        return ["proxy"]
    return []


def auto_items(name):
    """Items whose fixture entry differs from the pinned tree (for fixture edits)."""
    import os

    import tests.helpers as h

    m, inst = _executor()
    fname = "optimizer/tpc-ds/tpc-ds.sql" if name == "exec_tpcds" else "optimizer/tpc-h/tpc-h.sql"
    new = inst.tpcds_sqls if name == "exec_tpcds" else inst.tpch_sqls
    src = os.environ.get("BR_SRC_DIR")
    old_dir = h.FIXTURES_DIR
    try:
        h.FIXTURES_DIR = os.path.join(src, "tests", "fixtures")
        old = list(h.load_sql_fixture_pairs(fname))
    finally:
        h.FIXTURES_DIR = old_dir
    return [str(i) for i, entry in enumerate(new) if i >= len(old) or tuple(entry) != tuple(old[i])]


def setup(name):
    if name in ("exec_tpcds", "exec_tpch", "opt_tpch"):
        _executor()


def run_item(name, item):
    if name in ("exec_tpcds", "exec_tpch"):
        m, inst = _executor()
        tpch = name == "exec_tpch"
        sqls = inst.tpch_sqls if tpch else inst.tpcds_sqls
        schema = m.TPCH_SCHEMA if tpch else m.TPCDS_SCHEMA
        if _exec["init"] != tpch:
            tables = inst.tpch_tables if tpch else inst.tpcds_tables
            m.initializer(schema, tables)
            _exec["init"] = tpch
        i = int(item)
        args, sql, _ = sqls[i]
        expression = pickle.loads(pickle.dumps(m.parse_one(sql)))
        if args.get("execute"):
            # the pool ships tables pickled; do the same for the ones this query reads
            tables = m._tables
            for t in m.find_tables(expression):
                if t.name not in dict.keys(tables):
                    tables[t.name] = pickle.loads(pickle.dumps(tables[t.name]))
        table = m.mp_execute(expression, args)
        if table is not None:
            table = pickle.loads(pickle.dumps(table))
            inst.subtestHelper(i, table, tpch=tpch)
        return
    if name == "opt_tpch":
        m, inst = _executor()
        i = int(item)
        _, sql, optimized = inst.tpch_sqls[i]
        a = inst.cached_execute(sql, tpch=True)
        b = inst.tpch_conn.execute(m.transpile(optimized, write="duckdb")[0]).fetchdf()
        inst.rename_anonymous(b, a)
        m.assert_frame_equal(a, b)
        return
    if name == "gen_memory":
        import gc
        import tracemalloc

        from sqlglot.dialects import Dialect, Dialects

        dialect = Dialect.get_or_raise(Dialects(item))
        for _ in range(100):
            dialect.generator()
        gc.collect()
        tracemalloc.start()
        try:
            before = tracemalloc.get_traced_memory()[0]
            for _ in range(1000):
                dialect.generator()
            gc.collect()
            after = tracemalloc.get_traced_memory()[0]
        finally:
            tracemalloc.stop()
        assert after - before < 1024, (item, after - before)
        return
    if name == "max_nodes":
        from sqlglot import exp, parse_one
        from sqlglot.errors import ParseError

        assert isinstance(parse_one("SELECT 1", max_nodes=1_000_000), exp.Select)
        cols = [f"col{i}" for i in range(500)]
        assert isinstance(parse_one(f"SELECT {','.join(cols)} FROM t", max_nodes=1_000_000), exp.Select)
        # the original uses width 10000 and limits 1_000_000 / 50_000
        width, depth = 300, 100
        expr = "x" + "=x" * depth
        sql = f"SELECT {','.join([expr for _ in range(width)])}"
        try:
            parse_one(sql, max_nodes=30_000)
            raise AssertionError("no error")
        except ParseError as e:
            assert "Maximum number of AST nodes" in str(e), str(e)
        try:
            parse_one(sql, max_nodes=5_000)
            raise AssertionError("no error")
        except ParseError as e:
            assert "(5000)" in str(e), str(e)
        try:
            parse_one("SELECT " + ",".join(f"x={i}" for i in range(100)), max_nodes=5)
            raise AssertionError("no error")
        except ParseError:
            pass
        assert isinstance(parse_one("SELECT 1, 2, 3"), exp.Select)
        # a full-width parse without a limit hit, cheap enough to keep
        assert isinstance(parse_one(f"SELECT {','.join([expr for _ in range(60)])}"), exp.Select)
        return
    raise KeyError(name)
