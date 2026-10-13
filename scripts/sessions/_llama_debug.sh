#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
pkill -f 'llama-cli -m' 2>/dev/null || true
sleep 0.5
CLI=/tank-red/vmdata/vca/llama-build-host/bin/llama-cli
MODEL=/tank-red/vmdata/vca/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
echo '=== verbose one-node RPC, 15s then we watch ==='
# unbuffered, 8 token, verbose
timeout 45 stdbuf -oL -eL "$CLI" \
  -m "$MODEL" \
  --rpc 172.31.1.1:50052 \
  -ngl 99 -t 2 -n 8 \
  -p "Hi" \
  --verbose \
  2>&1 | stdbuf -oL tee /tmp/llama_dbg.log | tail -80
echo EXIT:$?
echo '=== log head ==='
head -40 /tmp/llama_dbg.log
echo '=== log grep rpc/error ==='
grep -iE 'rpc|error|fail|device|backend' /tmp/llama_dbg.log | head -40
export SSHPASS=vista1
sshpass -e ssh -o StrictHostKeyChecking=no root@172.31.1.1 'echo --- rpc log ---; tail -30 /tmp/rpc.log'
