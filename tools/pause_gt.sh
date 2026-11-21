#!/bin/bash
# pause/resume background labelling: pause_gt.sh STOP|CONT  (matches by parent tree of gt_cand.py)
sig=$1
for root in $(pgrep -u blast -f "tools/gt_cand\.py"); do
  pids="$root $(pgrep -P $root)"
  for p in $(pgrep -P $root); do pids="$pids $(pgrep -P $p)"; for q in $(pgrep -P $p); do pids="$pids $(pgrep -P $q)"; done; done
  kill -$sig $pids 2>/dev/null
done
