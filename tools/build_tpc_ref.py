"""DuckDB reference results for the TPC queries at the pinned commit.
usage (cwd = sqlglot root): build_tpc_ref.py <out.pkl>"""
import pickle
import sys

import duckdb

import tests.test_executor as m

ref = {}
for directory, schema, fixture in (
    (m.DIR_TPCH, m.TPCH_SCHEMA, "optimizer/tpc-h/tpc-h.sql"),
    (m.DIR_TPCDS, m.TPCDS_SCHEMA, "optimizer/tpc-ds/tpc-ds.sql"),
):
    conn = duckdb.connect()
    for table, columns in schema.items():
        conn.execute(f"CREATE VIEW {table} AS SELECT * FROM READ_CSV('{directory}{table}.csv.gz', delim='|', header=True, columns={columns})")
    for meta, sql, _ in m.load_sql_fixture_pairs(fixture):
        try:
            t = m.transpile(sql, write="duckdb")[0]
            ref[sql] = (t, conn.execute(t).fetchdf())
        except Exception as e:
            print("skip", e, file=sys.stderr)
    conn.close()
with open(sys.argv[1], "wb") as fh:
    pickle.dump(ref, fh, protocol=pickle.HIGHEST_PROTOCOL)
print(len(ref), "queries", file=sys.stderr)
