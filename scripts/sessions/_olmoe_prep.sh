#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
MOD=/tank-red/vmdata/vca/models
RPCBIN=/tank-red/vmdata/vca/llama-build-vca/bin/ggml-rpc-server
mkdir -p "$MOD"
F="$MOD/OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf"
if [ ! -f "$F" ] || [ "$(stat -c%s "$F")" -lt 1000000000 ]; then
  echo '=== download OLMoE Q4_K_M ==='
  wget -O "$F" \
    'https://huggingface.co/bartowski/OLMoE-1B-7B-0924-Instruct-GGUF/resolve/main/OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf'
fi
ls -lh "$F"

echo '=== rpc servers ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  sshpass -e scp -o StrictHostKeyChecking=no "$RPCBIN" root@$ip:/tmp/ggml-rpc-server
  $SSH root@$ip 'bash -s' <<'EOF'
chmod +x /tmp/ggml-rpc-server
iptables -C INPUT -p tcp --dport 50052 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 50052 -j ACCEPT
if [ -f /tmp/rpc.pid ] && kill -0 "$(cat /tmp/rpc.pid)" 2>/dev/null; then
  echo already_up pid=$(cat /tmp/rpc.pid)
else
  /tmp/ggml-rpc-server -H 0.0.0.0 -p 50052 -t 4 >/tmp/rpc.log 2>&1 &
  echo $! > /tmp/rpc.pid
  sleep 0.5
  echo started
fi
ss -lntp | grep 50052 || echo NOT_LISTEN
tail -3 /tmp/rpc.log
EOF
done
echo '=== temps ==='
vcactl temp 2>/dev/null | grep 'Core 0'
