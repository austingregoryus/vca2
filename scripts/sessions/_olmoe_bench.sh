#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
RPCBIN=/tank-red/vmdata/vca/llama-build-vca/bin/ggml-rpc-server
CLI=/tank-red/vmdata/vca/llama-build-host/bin/llama-cli
MODEL=/tank-red/vmdata/vca/models/OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf
PROMPT='The capital of France is'
ls -lh "$MODEL"

echo '=== restart rpc (cannot scp over running binary) ==='
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  $SSH root@$ip 'if [ -f /tmp/rpc.pid ]; then kill $(cat /tmp/rpc.pid) 2>/dev/null || true; fi; killall ggml-rpc-server 2>/dev/null || true; sleep 0.3'
  sshpass -e scp -o StrictHostKeyChecking=no "$RPCBIN" root@$ip:/tmp/ggml-rpc-server
  $SSH root@$ip 'chmod +x /tmp/ggml-rpc-server
    iptables -C INPUT -p tcp --dport 50052 -j ACCEPT 2>/dev/null || iptables -I INPUT 1 -p tcp --dport 50052 -j ACCEPT
    /tmp/ggml-rpc-server -H 0.0.0.0 -p 50052 -t 4 >/tmp/rpc.log 2>&1 &
    echo $! > /tmp/rpc.pid
    sleep 0.4
    ss -lntp | grep 50052'
done

run_one() {
  local tag=$1
  shift
  echo
  echo "======== $tag ========"
  echo "temps before:"
  vcactl temp 2>/dev/null | grep 'Core 0'
  local t0
  t0=$(date +%s)
  timeout 120 "$CLI" -m "$MODEL" "$@" \
    -t 4 -n 16 \
    -p "$PROMPT" \
    --single-turn --simple-io --no-display-prompt \
    -lv 1 -fit off \
    >/tmp/olmoe_"$tag".log 2>&1
  local rc=$?
  echo "exit=$rc wall=$(( $(date +%s) - t0 ))s"
  grep -E 'Prompt:|Generation:|error|Error|failed' /tmp/olmoe_"$tag".log | tail -10
  tail -15 /tmp/olmoe_"$tag".log | head -20
  echo "temps after:"
  vcactl temp 2>/dev/null | grep 'Core 0'
}

# Host only: no --rpc, all local CPU (E5-2699 v3)
run_one host_only -ngl 0

# 1 VCA node via RPC; -ngl 99 to prefer RPC devices
run_one rpc1 --rpc 172.31.1.1:50052 -ngl 99

# 3 VCA nodes
run_one rpc3 --rpc 172.31.1.1:50052,172.31.2.1:50052,172.31.3.1:50052 -ngl 99

echo
echo '=== SUMMARY ==='
for tag in host_only rpc1 rpc3; do
  echo "-- $tag --"
  grep -E 'Prompt:|Generation:' /tmp/olmoe_"$tag".log | tail -3
done
