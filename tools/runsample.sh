#!/bin/bash
# usage: runsample.sh <data-dir> [answers]  -- runs selector like the scorer (2 cores), prints score
DATA=${1:-$HOME/starter/sample}
ANS=${2:-$DATA/answers.json}
export BR_DATA=$DATA BR_APP=$HOME/sub/app BR_SRC=$HOME/sqlglot BR_TMP=/tmp/blast_$$ BR_PYTHON=$HOME/venv/bin/python
export PATH=$HOME/venv/bin:$PATH SKIP_INTEGRATION=0 PYTHONDONTWRITEBYTECODE=1
mkdir -p $BR_TMP
/usr/bin/time -o /tmp/time_$$ -f "%U %S %e" taskset -c ${CORES:-6,7} $HOME/sub/selector/target/release/selector > /tmp/out_$$.jsonl 2> /tmp/err_$$.log
read U S E < /tmp/time_$$
python3 $HOME/starter/score.py $DATA $ANS /tmp/out_$$.jsonl > /tmp/score_$$ 2>&1
cat /tmp/score_$$ | grep -E "Simulated|Misses|missed|Rejected"
SIM=$(grep Simulated /tmp/score_$$ | awk '{print $2}')
python3 -c "print(f'CPU {float(\"$U\")+float(\"$S\"):.1f}s  wall {float(\"$E\"):.1f}s  TOTAL {float(\"$SIM\" or 0)+float(\"$U\")+float(\"$S\"):.1f}')"
cp /tmp/out_$$.jsonl $HOME/last_out.jsonl; cp /tmp/err_$$.log $HOME/last_err.log
rm -rf $BR_TMP /tmp/out_$$.jsonl /tmp/err_$$.log /tmp/time_$$ /tmp/score_$$
