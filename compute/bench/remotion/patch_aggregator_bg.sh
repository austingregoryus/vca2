#!/bin/bash
# SPDX-License-Identifier: MIT
# Bench copy only: resolve the aggregator's card background through staticFile().
set -e
cd /opt/rbench/packages/remotion
python3 - <<'PY'
p = "src/channel_a/AggregatorShort.tsx"
s = open(p).read()
a = "backgroundImage: `url(${fallbackImage})`"
b = "backgroundImage: `url(${staticFile(fallbackImage)})`"
if a in s:
    open(p, "w").write(s.replace(a, b)); print("patched")
else:
    print("already patched" if b in s else "PATTERN NOT FOUND")
PY
npx remotion still src/index.ts AggregatorShort /dev/shm/s.png --frame=600 \
  --props=/opt/rbench/props.json --log=verbose > /dev/shm/still.log 2>&1
echo "404s: $(grep -c ' 404 ' /dev/shm/still.log)"
ffmpeg -v error -y -i /dev/shm/s.png -vf scale=360:-1 /dev/shm/s2.png
ffmpeg -v error -y -i /dev/shm/pc.png -i /dev/shm/s2.png -filter_complex hstack /dev/shm/both2.png
