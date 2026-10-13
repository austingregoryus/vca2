#!/bin/bash
# SPDX-License-Identifier: MIT
# SHORT_COMP / LONG_COMP: the Remotion composition IDs of the short and long video.
: "${SHORT_COMP:?set SHORT_COMP to the short video's composition ID}" "${LONG_COMP:?set LONG_COMP to the long video's composition ID}"
# Runs ON a node: time today's real episode like studio/cli.py does it.
cd /opt/csbench/packages/render
OUT=/opt/csbench/results.txt; : > $OUT
now(){ date +%s.%N; }
el(){ echo "$(now) - $1" | bc | cut -d. -f1; }

# 1) vertical short, one pass, concurrency 4
t0=$(now)
npx remotion render src/index.ts "$SHORT_COMP" /dev/shm/cs_short.mp4 \
  --props=/opt/csbench/props_short.json --concurrency=4 </dev/null > /opt/csbench/short.log 2>&1
echo "short rc=$? wall=$(el $t0)s frames=2198" >> $OUT

# 2) main episode, 5 chunks of ~3095 frames, concurrency 4, concat -c copy
T=15474; N=5; step=$(( (T + N - 1) / N )); : > /dev/shm/cs_list.txt
t0=$(now)
for i in $(seq 0 $((N - 1))); do
  a=$(( i * step )); b=$(( a + step - 1 )); [ $b -ge $T ] && b=$((T - 1))
  ti=$(now)
  npx remotion render src/index.ts "$LONG_COMP" /dev/shm/cs_chunk_$i.mp4 \
    --props=/opt/csbench/props_long.json --frames=$a-$b --concurrency=4 </dev/null > /opt/csbench/long_$i.log 2>&1
  echo "  chunk $i frames $a-$b rc=$? $(el $ti)s" >> $OUT
  echo "file '/dev/shm/cs_chunk_$i.mp4'" >> /dev/shm/cs_list.txt
done
ffmpeg -v error -y -f concat -safe 0 -i /dev/shm/cs_list.txt -c copy /dev/shm/cs_long.mp4 </dev/null
echo "long wall=$(el $t0)s frames=$T video=$(ffprobe -v error -show_entries format=duration -of csv=p=0 /dev/shm/cs_long.mp4)s" >> $OUT
echo "peak mem during long: $(grep -h 'peak' /opt/csbench/long_*.log | head -1)" >> $OUT
rm -f /dev/shm/cs_chunk_*.mp4
echo ALL-DONE >> $OUT
