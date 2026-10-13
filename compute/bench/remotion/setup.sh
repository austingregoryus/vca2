#!/bin/bash
# SPDX-License-Identifier: MIT
# Runs ON a node. Sets up a Remotion render bench in /opt/rbench from /root/rb.tar.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
mkdir -p /opt/rbench && cd /opt/rbench && tar xf /root/rb.tar && rm /root/rb.tar

if ! command -v node >/dev/null || [ "$(node -v | cut -c2-3)" -lt 20 ]; then
  f=$(curl -fsSL https://nodejs.org/dist/latest-v22.x/ | grep -o 'node-v22[^"]*-linux-x64.tar.xz' | head -1)
  curl -fsSL "https://nodejs.org/dist/latest-v22.x/$f" | tar xJ -C /opt
  ln -sfn "/opt/${f%.tar.xz}" /opt/node
  for b in node npm npx corepack; do ln -sf /opt/node/bin/$b /usr/local/bin/$b; done
fi
echo "node $(node -v)"

apt-get update -qq
apt-get install -y -qq --no-install-recommends libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
  libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
  libpango-1.0-0 libcairo2 libxshmfence1 fonts-liberation fonts-noto-color-emoji >/dev/null
echo "chrome libs ok"

corepack enable >/dev/null 2>&1
corepack prepare pnpm@9.9.0 --activate >/dev/null 2>&1
export COREPACK_ENABLE_DOWNLOAD_PROMPT=0
pnpm install --filter @videopipe/remotion --frozen-lockfile --config.confirmModulesPurge=false 2>&1 | tail -3 \
  || pnpm install --filter @videopipe/remotion --no-frozen-lockfile 2>&1 | tail -3
cd packages/remotion
npx remotion versions 2>/dev/null | grep -m1 -i "remotion" || true
npx remotion browser ensure 2>&1 | tail -2
echo SETUP-DONE
