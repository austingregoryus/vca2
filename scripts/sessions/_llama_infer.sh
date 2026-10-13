#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
killall -9 llama-cli 2>/dev/null || true
sleep 0.5
CLI=/tank-red/vmdata/vca/llama-build-host/bin/llama-cli
MODEL=/tank-red/vmdata/vca/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
echo '=== temps BEFORE ==='
vcactl temp 2>/dev/null | grep 'Core 0'
echo '=== 3-node RPC single-turn 16 tokens ==='
timeout 90 "$CLI" \
  -m "$MODEL" \
  --rpc 172.31.1.1:50052,172.31.2.1:50052,172.31.3.1:50052 \
  -ngl 99 -t 4 -n 16 \
  -p "The capital of France is" \
  --single-turn --simple-io --no-display-prompt \
  -lv 1 -fit off \
  >/tmp/llama_infer.log 2>&1
echo infer_exit:$?
echo '=== last 80 lines ==='
tail -80 /tmp/llama_infer.log
echo '=== timing/tok ==='
grep -iE 'tok/s|eval time|prompt eval|sampling|RPC|device' /tmp/llama_infer.log | head -40
echo '=== temps AFTER ==='
vcactl temp 2>/dev/null | grep Core
export SSHPASS=vista1
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "-- $ip --"
  sshpass -e ssh -o StrictHostKeyChecking=no root@$ip 'ps -o pcpu,cmd -C ggml-rpc-server | head -2; tail -3 /tmp/rpc.log'
done
