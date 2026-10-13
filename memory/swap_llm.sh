#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Restart the generation server on every node with a different model or a
# different thread/batch configuration. Used by bakeoff.py.
#
#   ./swap_llm.sh <model-basename> [threads] [ubatch] [ctx]
#   ./swap_llm.sh gemma4.gguf 8 512 3072
#
# Only sm-llm is touched; the embedder and reranker stay up, so the node is in
# the same state a real ingest would see. `services.sh start` puts production
# back.
set -uo pipefail
NODE_PW="${NODE_PW:-vista1}"
BIN=/root/bin/llama-server

MODEL="${1:?usage: swap_llm.sh <model.gguf> [threads] [ubatch] [ctx]}"
THREADS="${2:-8}"
UBATCH="${3:-512}"
CTX="${4:-3072}"

n(){ sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 "172.31.$1.1" "$2" 2>/dev/null; }

for i in 1 2 3; do
    # A RUNTIME drop-in (/run, not /etc) replaces the model and config of the
    # installed sm-llm.service. It is lost on reboot, and `services.sh start`
    # deletes it, so a model left swapped in after a benchmark can never
    # silently become production. (This used to create a transient unit of
    # the same name, which systemd refuses once a real unit file exists.)
    n "$i" "mkdir -p /run/systemd/system/sm-llm.service.d
            cat > /run/systemd/system/sm-llm.service.d/swap.conf <<'EOF'
[Service]
ExecStart=
ExecStart=$BIN -m /root/$MODEL -t $THREADS -tb $THREADS -c $CTX -b 2048 -ub $UBATCH -np 1 --host 0.0.0.0 --port 8082
MemoryLimit=11G
EOF
            systemctl daemon-reload
            systemctl restart sm-llm" >/dev/null
done

# Wait for all three. A 4.2 GB model off BlockIO takes a while, and three
# nodes pull it over the same host path, so they contend -- 180 s of slack.
fail=0
for i in 1 2 3; do
    ok=""
    for _ in $(seq 1 90); do
        if n "$i" "curl -s -m 2 http://127.0.0.1:8082/health | grep -q ok"; then ok=1; break; fi
        sleep 2
    done
    if [ -n "$ok" ]; then
        printf "  node%d up   " $((i-1))
    else
        printf "  node%d DOWN " $((i-1)); fail=1
    fi
done
echo
exit $fail
