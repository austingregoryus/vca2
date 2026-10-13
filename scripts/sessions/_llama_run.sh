#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
SCP='sshpass -e scp -o StrictHostKeyChecking=no'
RPC=/tank-red/vmdata/vca/llama-build-vca/bin/ggml-rpc-server
CLI=/tank-red/vmdata/vca/llama-build-host/bin/llama-cli
MODEL=/tank-red/vmdata/vca/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf

echo '=== temps BEFORE ==='
vcactl temp 2>/dev/null | grep Core

echo '=== deploy rpc-server ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SCP "$RPC" root@$ip:/tmp/ggml-rpc-server
  $SSH root@$ip 'bash -s' <<'EOF'
set -e
chmod +x /tmp/ggml-rpc-server
ldd /tmp/ggml-rpc-server || true
/tmp/ggml-rpc-server --help | head -15
iptables -C INPUT -p tcp --dport 50052 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 50052 -j ACCEPT
if [ -f /tmp/rpc.pid ]; then kill "$(cat /tmp/rpc.pid)" 2>/dev/null || true; sleep 0.2; fi
/tmp/ggml-rpc-server -H 0.0.0.0 -p 50052 -t 4 >/tmp/rpc.log 2>&1 &
echo $! > /tmp/rpc.pid
sleep 0.6
cat /tmp/rpc.log
EOF
done

echo '=== llama-cli RPC TinyLlama 32 tokens ==='
"$CLI" -m "$MODEL" \
  --rpc 172.31.1.1:50052,172.31.2.1:50052,172.31.3.1:50052 \
  -ngl 99 -t 4 -n 32 --no-display-prompt \
  -p "The capital of France is" \
  2>&1 | tee /tmp/llama_rpc_run.log | tail -60

echo '=== temps AFTER ==='
vcactl temp 2>/dev/null | grep Core
echo '=== rpc logs ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "-- $ip --"
  $SSH root@$ip 'tail -20 /tmp/rpc.log'
done
