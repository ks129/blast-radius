#!/bin/bash
# usage: cgrun.sh <data-dir> [answers] -- run selector in its own cgroup scope, report total CPU like the scorer
DATA=${1:-$HOME/starter/sample}
ANS=${2:-$DATA/answers.json}
export BR_DATA=$DATA BR_APP=$HOME/sub/app BR_SRC=$HOME/sqlglot BR_TMP=/tmp/blast_$$ BR_PYTHON=$HOME/venv/bin/python
export PATH=$HOME/venv/bin:$PATH SKIP_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1 HOME_OUT=$HOME
mkdir -p $BR_TMP
systemd-run --user --scope -q -p AllowedCPUs=${CORES:-6,7} bash -c '
  CG=/sys/fs/cgroup$(cut -d: -f3 /proc/self/cgroup)
  /usr/bin/time -o /tmp/time_'$$' -f "%U %S %e" $HOME_OUT/sub/selector/target/release/selector > /tmp/out_'$$'.jsonl 2> /tmp/err_'$$'.log
  sleep 1
  echo "leftover procs: $(cat $CG/cgroup.procs | wc -l)"
  grep usage_usec $CG/cpu.stat
'
read U S E < /tmp/time_$$
echo "time(1) CPU $(python3 -c "print(round($U+$S,1))") wall $E"
python3 $HOME/starter/score.py $DATA $ANS /tmp/out_$$.jsonl 2>&1 | grep -E "Simulated|Misses|missed"
cp /tmp/out_$$.jsonl $HOME/last_out.jsonl; cp /tmp/err_$$.log $HOME/last_err.log
rm -rf $BR_TMP /tmp/out_$$.jsonl /tmp/err_$$.log /tmp/time_$$
