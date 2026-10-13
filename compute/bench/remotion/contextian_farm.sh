#!/bin/bash
# SPDX-License-Identifier: MIT
# SHORT_COMP / LONG_COMP: the Remotion composition IDs of the short and long video.
: "${SHORT_COMP:?set SHORT_COMP to the short video's composition ID}" "${LONG_COMP:?set LONG_COMP to the long video's composition ID}"
# Runs on the HOST: render one composition split across nodes 3-5 (card 1),
# gather the parts on node 3, concat -c copy. Times the whole thing.
SP="sshpass -p vista1 ssh -n -o StrictHostKeyChecking=no"
SPI="sshpass -p vista1 ssh -o StrictHostKeyChecking=no"
NODES=(172.31.4.1 172.31.5.1 172.31.6.1)
now(){ date +%s.%N; }

farm(){ # comp props total_frames out
  local comp=$1 props=$2 T=$3 out=$4 n=${#NODES[@]}
  local step=$(( (T + n - 1) / n )) t0=$(now) i
  for i in 0 1 2; do
    local a=$(( i * step )) b=$(( (i + 1) * step - 1 )); [ $b -ge $T ] && b=$((T - 1))
    $SP root@${NODES[$i]} "cd /opt/csbench/packages/render && npx remotion render src/index.ts $comp /dev/shm/part.mp4 --props=/opt/csbench/$props --frames=$a-$b --concurrency=4 >/dev/shm/part.log 2>&1; echo \"  node ${NODES[$i]} frames $a-$b rc=\$?\"" &
  done
  wait
  local tr=$(echo "$(now) - $t0" | bc)
  $SP root@${NODES[0]} "cp /dev/shm/part.mp4 /dev/shm/p0.mp4"
  for i in 1 2; do $SP root@${NODES[$i]} "cat /dev/shm/part.mp4" | $SPI root@${NODES[0]} "cat > /dev/shm/p$i.mp4"; done
  $SP root@${NODES[0]} "cd /dev/shm && printf \"file '/dev/shm/p%s.mp4'\n\" 0 1 2 > plist.txt && ffmpeg -v error -y -f concat -safe 0 -i plist.txt -c copy $out && rm -f p0.mp4 p1.mp4 p2.mp4"
  local tt=$(echo "$(now) - $t0" | bc)
  local dur=$($SP root@${NODES[0]} "ffprobe -v error -show_entries format=duration -of csv=p=0 $out")
  printf '%s: render %.0fs, total with gather+concat %.0fs, %d frames, video %.1fs, %.1f fps\n' "$comp" "$tr" "$tt" "$T" "$dur" "$(echo "$T / $tt" | bc -l)"
}

farm "$SHORT_COMP" props_short.json 2198 /dev/shm/farm_short.mp4
farm "$LONG_COMP" props_long.json 15474 /dev/shm/farm_long.mp4
echo FARM-DONE
