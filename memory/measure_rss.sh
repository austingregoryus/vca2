#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Report resident memory for the three model servers on each node, and how
# much headroom is left.
#
# This exists because the sizing cannot be reasoned out from model file sizes.
# The three GGUFs total 6.26 GB on a 15.4 GB node, which looks comfortable;
# the first configuration actually needed ~17.5 GB and the kernel OOM-killed
# the LLM on all three nodes at once. RSS grows with use, so run this AFTER a
# real workload, not right after ./services.sh start.
#
#   ./measure_rss.sh [--watch]
set -uo pipefail
NODE_PW="${NODE_PW:-vista1}"

n(){ sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "172.31.$1.1" "$2" 2>/dev/null; }

one() {
    local i=$1
    printf "node%d\n" $((i-1))
    n "$i" '
        total=$(awk "/MemTotal/{print \$2}" /proc/meminfo)
        avail=$(awk "/MemAvailable/{print \$2}" /proc/meminfo)
        sum=0
        for p in $(pgrep -x llama-server); do
            rss=$(awk "/^VmRSS/{print \$2}" /proc/$p/status 2>/dev/null)
            [ -z "$rss" ] && continue
            # identify which server this is by the model on its command line
            m=$(tr "\0" "\n" < /proc/$p/cmdline | grep -m1 "\.gguf$" | xargs -r basename)
            case "$m" in
                *emb*)    tag="embedder" ;;
                *rerank*) tag="reranker" ;;
                *)        tag="llm     " ;;
            esac
            printf "    %s  %7.2f GB\n" "$tag" "$(echo "$rss/1048576" | bc -l)"
            sum=$((sum + rss))
        done
        printf "    %s  %7.2f GB\n" "TOTAL   " "$(echo "$sum/1048576" | bc -l)"
        printf "    %s  %7.2f GB of %.2f GB\n" "free    " \
            "$(echo "$avail/1048576" | bc -l)" "$(echo "$total/1048576" | bc -l)"
        # A unit that has restarted is the tell that something was killed.
        for u in sm-emb sm-rrk sm-llm; do
            nr=$(systemctl show $u -p NRestarts --value 2>/dev/null)
            [ "${nr:-0}" != "0" ] && printf "    %s restarted %s time(s)\n" "$u" "$nr"
        done
        true
    '
}

if [ "${1:-}" = "--watch" ]; then
    while true; do
        clear; date
        for i in 1 2 3; do one "$i"; done
        sleep 20
    done
else
    for i in 1 2 3; do one "$i"; done
fi
