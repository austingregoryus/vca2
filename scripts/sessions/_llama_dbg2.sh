#!/bin/bash
# SPDX-License-Identifier: MIT
set -u
killall -9 llama-cli 2>/dev/null || true
sleep 1
CLI=/tank-red/vmdata/vca/llama-build-host/bin/llama-cli
MODEL=/tank-red/vmdata/vca/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
echo '=== which timeout ==='
command -v timeout || echo NO_TIMEOUT
echo '=== run 40s max, log to file ==='
timeout 40 "$CLI" -m "$MODEL" --rpc 172.31.1.1:50052 -ngl 99 -t 2 -n 8 -p Hi --verbose >/tmp/llama_dbg2.log 2>&1
echo timeout_exit:$?
echo '=== bytes ==='
wc -c /tmp/llama_dbg2.log
echo '=== first 80 lines ==='
head -80 /tmp/llama_dbg2.log
echo '=== last 40 ==='
tail -40 /tmp/llama_dbg2.log
export SSHPASS=vista1
sshpass -e ssh -o StrictHostKeyChecking=no root@172.31.1.1 'tail -15 /tmp/rpc.log; echo ---; ps -o pcpu,cmd -C ggml-rpc-server'
echo '=== temps ==='
vcactl temp 2>/dev/null | grep 'Core 0'
