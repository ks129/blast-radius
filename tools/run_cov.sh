#!/bin/bash
# usage: run_cov.sh <ids-file> <out-dir> <parallel>
set -u
IDS=$1; OUT=$2; PAR=${3:-3}
mkdir -p $OUT
export SKIP_INTEGRATION=0 COV_ROOT=$HOME/sqlglot PYTHONPATH=$HOME/tools:$HOME/sqlglot PATH=$HOME/venv/bin:$PATH
cd $HOME/sqlglot
run_one() {
  id="$1"; n=$(echo "$id" | md5sum | cut -c1-12)
  COV_OUT=$OUT/$n.jsonl timeout 600 python -m pytest -q -p no:cacheprovider -p covplug "$id" > $OUT/$n.log 2>&1
  echo "$? $id" >> $OUT/status.txt
}
export -f run_one; export OUT
cat $IDS | xargs -P $PAR -I{} bash -c 'run_one "$@"' _ {}
