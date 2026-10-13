# SPDX-License-Identifier: MIT
# Concurrent LLM serving capacity: aggregate tokens/sec under many requests.
#
# WHY THIS EXISTS: the docs asserted for a long time that the card wins at
# "many concurrent requests -- three independent workers, roughly 3x
# aggregate" while only ever having measured the opposite case (single-stream
# latency, where three nodes are 2.4x SLOWER than one because llama.cpp RPC
# splits layers and every token traverses the pipeline serially). This
# measures the claim instead of repeating it.
#
# The distinction that matters:
#   - RPC layer-splitting = one model spread across nodes = pipeline-serial,
#     strictly worse with more nodes. Already measured, see docs/LLAMA.md.
#   - INDEPENDENT WORKERS = a whole model per node, requests distributed =
#     embarrassingly parallel. That is what is measured here.
#
# METHOD: fixed wall-clock window, workers loop generations, count tokens.
# `--ignore-eos` forces exactly LLM_TOKENS per request so the token accounting
# is exact rather than depending on where the model chooses to stop.
#
# FAIRNESS: host and card run llama-cli built from the SAME source commit
# (ce8caa6); the node binaries are a native gcc 11 build because the host's is
# linked against glibc 2.41 and the nodes are 2.35. Same model file, same
# prompt, same token count, same thread count per worker.

WL_DESCRIPTION="concurrent LLM serving (aggregate tok/s, TinyLlama-1.1B Q4)"
WL_UNIT="tok/s"

LLM_RUN_SECS="${LLM_RUN_SECS:-60}"
LLM_TOKENS="${LLM_TOKENS:-128}"
LLM_THREADS="${LLM_THREADS:-8}"
LLM_HOST_JOBS="${LLM_HOST_JOBS:-4}"      # 4 x 8 = 32 of the host's 36 threads
LLM_NODE_JOBS="${LLM_NODE_JOBS:-1}"      # one whole model per node (4c/8t)
LLM_PROMPT="${LLM_PROMPT:-Write a detailed explanation of how a PCIe non-transparent bridge works}"

LLM_HOST_BIN=/tank-red/vmdata/vca/llama-build-host/bin/llama-cli
LLM_HOST_MODEL=/tank-red/vmdata/vca/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf
LLM_NODE_BIN=/root/bin/llama-cli
LLM_NODE_MODEL=/root/tinyllama.gguf

wl_prepare() {
    [ -x "$LLM_HOST_BIN" ]   || { echo "    missing $LLM_HOST_BIN"; return 1; }
    [ -f "$LLM_HOST_MODEL" ] || { echo "    missing $LLM_HOST_MODEL"; return 1; }
    local i
    for i in 1 2 3; do
        node "$i" "test -x $LLM_NODE_BIN && test -f $LLM_NODE_MODEL" 2>/dev/null || {
            echo "    node $((i-1)) missing llama-cli or model"; return 1; }
    done
    # Worker loop lives on each machine so the window is real work, not ssh
    # round-trips. Each completed request contributes exactly LLM_TOKENS.
    local loop='#!/bin/bash
BIN=$1; MODEL=$2; SECS=$3; TOK=$4; THR=$5; ID=$6; PROMPT=$7
end=$(( $(date +%s) + SECS ))
n=0
while [ $(date +%s) -lt $end ]; do
  "$BIN" -m "$MODEL" -p "$PROMPT" -n "$TOK" -t "$THR" -st --simple-io \
         --ignore-eos --no-warmup >/dev/null 2>&1 && n=$(( n + 1 ))
done
echo "$n" > /tmp/llmreq_$ID'
    printf '%s\n' "$loop" > /tmp/llmloop.sh; chmod +x /tmp/llmloop.sh
    for i in 1 2 3; do
        sshpass -p "$NODE_PW" scp -o StrictHostKeyChecking=no -q /tmp/llmloop.sh "172.31.$i.1:/tmp/llmloop.sh" 2>/dev/null
        node "$i" "chmod +x /tmp/llmloop.sh" 2>/dev/null
    done
    return 0
}

wl_run() {
    local cfg="$1" i j reqs=0
    rm -f /tmp/llmreq_*
    for i in 1 2 3; do node "$i" "rm -f /tmp/llmreq_*" 2>/dev/null; done

    local t0; t0=$(date +%s.%N)
    case "$cfg" in host|both)
        for j in $(seq 1 "$LLM_HOST_JOBS"); do
            ( /tmp/llmloop.sh "$LLM_HOST_BIN" "$LLM_HOST_MODEL" "$LLM_RUN_SECS" \
                "$LLM_TOKENS" "$LLM_THREADS" "h$j" "$LLM_PROMPT" ) >/dev/null 2>&1 &
        done ;;
    esac
    case "$cfg" in card|both)
        for i in 1 2 3; do
            for j in $(seq 1 "$LLM_NODE_JOBS"); do
                ( node "$i" "/tmp/llmloop.sh $LLM_NODE_BIN $LLM_NODE_MODEL $LLM_RUN_SECS $LLM_TOKENS $LLM_THREADS n${i}_$j '$LLM_PROMPT'" ) >/dev/null 2>&1 &
            done
        done ;;
    esac
    wait
    local t1; t1=$(date +%s.%N)
    local secs; secs=$(echo "$t1 - $t0" | bc)

    case "$cfg" in host|both)
        for j in $(seq 1 "$LLM_HOST_JOBS"); do
            reqs=$(( reqs + $(cat "/tmp/llmreq_h$j" 2>/dev/null || echo 0) ))
        done ;;
    esac
    case "$cfg" in card|both)
        for i in 1 2 3; do
            for j in $(seq 1 "$LLM_NODE_JOBS"); do
                reqs=$(( reqs + $(node "$i" "cat /tmp/llmreq_n${i}_$j 2>/dev/null || echo 0") ))
            done
        done ;;
    esac
    echo "scale=1; $reqs * $LLM_TOKENS / $secs" | bc
}

wl_cleanup() {
    local i
    rm -f /tmp/llmreq_*
    for i in 1 2 3; do node "$i" "rm -f /tmp/llmreq_*" 2>/dev/null; done
}
