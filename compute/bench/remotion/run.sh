#!/bin/bash
# SPDX-License-Identifier: MIT
# Runs ON a node: full render of the real AggregatorShort run at several
# concurrencies. Writes /opt/rbench/results.txt.
cd /opt/rbench/packages/remotion
OUT=/opt/rbench/results.txt; : > $OUT
for c in ${CONCS:-4 8}; do
  rm -f /dev/shm/out.mp4
  t0=$(date +%s.%N)
  npx remotion render src/index.ts AggregatorShort /dev/shm/out.mp4 \
    --props=/opt/rbench/props.json --concurrency=$c --log=verbose > /opt/rbench/render_c$c.log 2>&1
  rc=$?
  t=$(echo "$(date +%s.%N) - $t0" | bc)
  frames=$(grep -oE 'Rendered [0-9]+/[0-9]+' /opt/rbench/render_c$c.log | tail -1 | cut -d/ -f2)
  dur=$(ffprobe -v error -show_entries format=duration -of csv=p=0 /dev/shm/out.mp4 2>/dev/null)
  echo "concurrency=$c rc=$rc wall=${t}s frames=$frames video=${dur}s fps=$(echo "${frames:-0} / $t" | bc -l | cut -c1-5)" >> $OUT
  grep -iE 'bundl.*(took|ms)|render.*frames.*took|stitch|encod.*took|Total' /opt/rbench/render_c$c.log | tail -4 | sed 's/^/    /' >> $OUT
done
echo ALL-DONE >> $OUT
