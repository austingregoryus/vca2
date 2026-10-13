#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Prepare one VCA node for the render farm. Idempotent. Run from the host:
#
#   for ip in 172.31.1.1 172.31.2.1 172.31.4.1 172.31.5.1 172.31.6.1; do
#     sshpass -p vista1 ssh root@$ip 'bash -s' < farm/node_setup.sh
#   done
#
# Node 22 (Remotion 4 needs >= 16; jammy's apt nodejs is 12), the shared
# libraries chrome-headless-shell needs, pnpm via corepack (the client's
# packageManager field pins the version), and /opt/farm. The render
# package itself, its node_modules and Chrome arrive per code version.
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive

if ! command -v node >/dev/null || [ "$(node -v | cut -c2-3)" -lt 20 ]; then
  f=$(curl -fsSL https://nodejs.org/dist/latest-v22.x/ | grep -o 'node-v22[^"]*-linux-x64.tar.xz' | head -1)
  curl -fsSL "https://nodejs.org/dist/latest-v22.x/$f" | tar xJ -C /opt
  ln -sfn "/opt/${f%.tar.xz}" /opt/node
fi
for b in node npm npx corepack; do ln -sf /opt/node/bin/$b /usr/local/bin/$b; done
corepack enable --install-directory /usr/local/bin </dev/null

apt-get install -y -qq --no-install-recommends </dev/null \
  libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
  libxcomposite1 libxdamage1 libxfixes3 libxrandr2 libgbm1 libasound2 \
  libpango-1.0-0 libcairo2 libxshmfence1 fonts-liberation fonts-noto-color-emoji >/dev/null

mkdir -p /opt/farm/code /opt/farm/public /opt/farm/work
echo "$(hostname -I | cut -d' ' -f1): node $(node -v), pnpm shim $(command -v pnpm), ffmpeg $(command -v ffmpeg >/dev/null && echo yes || echo NO)"
