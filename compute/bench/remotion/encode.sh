#!/bin/bash
# SPDX-License-Identifier: MIT
# Runs ON a node. 30 s of 1080x1920 (vertical) 30 fps test video, encoded
# several ways. Reports fps and x-realtime. Source is generated in RAM.
set -u
cd /dev/shm
lscpu | grep -E "Model name|^CPU\(s\)" | sed 's/  */ /g'
ffmpeg -hide_banner -loglevel error -y -f lavfi -i "testsrc2=size=1080x1920:rate=30,format=yuv420p" -t 30 -c:v rawvideo -f nut src.nut
ls -lh src.nut | awk '{print "source:", $5}'

run(){ # label, ffmpeg output args...
  local label="$1"; shift
  local t0=$(date +%s.%N)
  if ffmpeg -hide_banner -loglevel error -y "$@" 2>err.txt; then
    local t=$(echo "$(date +%s.%N) - $t0" | bc)
    printf '%-34s %6.1f fps  %5.2fx realtime\n' "$label" "$(echo "900 / $t" | bc -l)" "$(echo "30 / $t" | bc -l)"
  else
    printf '%-34s FAILED: %s\n' "$label" "$(tail -c 150 err.txt | tr '\n' ' ')"
  fi
}

run "H.264 Quick Sync (VA-API)" -vaapi_device /dev/dri/renderD128 -i src.nut -vf 'format=nv12,hwupload' -c:v h264_vaapi -b:v 8M -f null -
run "HEVC Quick Sync (VA-API)"  -vaapi_device /dev/dri/renderD128 -i src.nut -vf 'format=nv12,hwupload' -c:v hevc_vaapi -b:v 6M -f null -
run "H.264 Quick Sync (QSV)"    -init_hw_device qsv=hw -filter_hw_device hw -i src.nut -vf 'hwupload=extra_hw_frames=64,format=qsv' -c:v h264_qsv -b:v 8M -f null -
run "H.264 CPU x264 veryfast"   -i src.nut -c:v libx264 -preset veryfast -crf 20 -f null -
run "H.264 CPU x264 medium"     -i src.nut -c:v libx264 -preset medium -crf 20 -f null -
rm -f src.nut err.txt
