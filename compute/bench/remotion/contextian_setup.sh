#!/bin/bash
# SPDX-License-Identifier: MIT
# SHORT_COMP / LONG_COMP: the Remotion composition IDs of the short and long video.
: "${SHORT_COMP:?set SHORT_COMP to the short video's composition ID}" "${LONG_COMP:?set LONG_COMP to the long video's composition ID}"
# Runs ON a node: ContextianStudioClaude render bench in /opt/csbench.
set -euo pipefail
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
rm -rf /opt/csbench && mkdir -p /opt/csbench && cd /opt/csbench
tar xf /root/cs.tar && rm /root/cs.tar
pnpm install --filter @csc/render --frozen-lockfile </dev/null 2>&1 | tail -1
cd packages/render
npx remotion browser ensure </dev/null >/dev/null 2>&1
npx remotion versions </dev/null 2>/dev/null | grep -m1 "^remotion " || true
for c in "$SHORT_COMP props_short.json 600" "$LONG_COMP props_long.json 3000"; do
  set -- $c
  npx remotion still src/index.ts $1 /dev/shm/cs_$1.png --frame=$3 --props=/opt/csbench/$2 --log=verbose </dev/null > /dev/shm/cs_still_$1.log 2>&1 \
    && echo "$1 still ok, 404s: $(grep -c ' 404 ' /dev/shm/cs_still_$1.log)" \
    || { echo "$1 still FAILED"; tail -5 /dev/shm/cs_still_$1.log; }
  grep ' 404 ' /dev/shm/cs_still_$1.log | grep -oE 'localhost:[0-9]+/[^]]+' | sort -u | head -5
done
