#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Manage the model servers that back the memory engine, and the host API.
#
# Each of the three VCA nodes runs all three services, so every stage of the
# pipeline can use all three nodes:
#
#   :8080  sm-emb  EmbeddingGemma 2 Q8_0     (0.31 GB)  ingest + query
#                  (llama.cpp b11476, its own build; Qwen3-Embedding-0.6B until
#                  2026-10-07, see EMBED_BAKEOFF.md)
#   :8081  sm-rrk  Qwen3-Reranker-0.6B Q8    (0.64 GB)  retrieval precision
#   :8082  sm-llm  gemma-4-E4B-it Q4_K_M     (4.98 GB)  extraction + answers
#
#   ./services.sh install    write the three units onto each node's disk and
#                            enable them -- they then start on every node boot
#   ./services.sh start      (re)start them now, installing first if needed
#   ./services.sh stop | status
#   ./services.sh api | api-stop     the host API (smem-api.service)
#
# The units live in /etc/systemd/system on each node's own disk image, which
# is persistent, so the model servers come back by themselves after any node
# or host reboot. They used to be transient `systemd-run` units, which vanish
# on reboot and had to be recreated by hand.
#
# WHY systemd AND NOT nohup: backgrounding a server over ssh with
# `nohup ... &` does not survive the session closing -- the server silently
# never starts and every later request gets ECONNREFUSED. See docs/GOTCHAS.md.
set -uo pipefail

NODE_PW="${NODE_PW:-vista1}"
# Node indexes = the third octet of each node's IP (172.31.<i>.1). Card 0 is
# 1-3, card 1 (second VCA 2, 2026-09-26) is 4-6. Override to act on a subset:
#   SMEM_NODES="4 5 6" ./services.sh install
SMEM_NODES="${SMEM_NODES:-1 2 3 4 5 6}"
BIN=/root/bin/llama-server
# The embedder runs a newer llama.cpp than the reranker and LLM: EmbeddingGemma 2
# (arch gemma-embedding2) needs b11476+, which the 0.4.1-dev build cannot load.
# The official ubuntu-x64 release needs glibc 2.34 (nodes: 2.35). Its shared
# libraries sit beside the binary, hence LD_LIBRARY_PATH in the unit.
EMB_DIR=/root/llama-b11476
EMB=/root/embeddinggemma-2-Q8_0.gguf
# Copied from here to a node that lacks them (a reflashed or returning node).
EMB_SRC=/tank-red/vmdata/vca/llama-b11476
RRK=/root/qwen3-rerank-06b-q8.gguf
LLM=/root/gemma4.gguf

n(){ sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 "172.31.$1.1" "$2"; }

# SIZING -- read this before changing any of these numbers.
#
# A node has 15.4 GB usable. The three model FILES total only 6.26 GB, which
# makes it look like there is room to spare. There is not: llama-server's
# resident set is dominated by the compute buffer, and that scales with the
# MICRO-batch (-ub), not with the model.
#
# The first configuration used -ub 2048 on the embedder and reranker. Each of
# those 0.64 GB models grew to ~5 GB RSS, and together with the ~7 GB LLM that
# is 17.5 GB on a 15.4 GB node. The kernel OOM-killed the LLM on all three
# nodes at once, mid-benchmark. It survived the preceding 72-minute ingest
# only because the buffers had not yet grown into their worst case -- which is
# exactly why this needs measuring rather than estimating. See measure_rss.sh.
#
# -ub 768 (the RERANKER; the embedder has its own, see install_one) is chosen
# against the real data, not guessed: chunks are capped at
# 1400 chars (~350 tokens) and rerank pairs at ~1000 chars (~250 tokens), so
# nothing legitimate needs a larger micro-batch. The cap matters because
# EMBEDDING MODELS ARE NON-CAUSAL -- a single sequence must fit in one ubatch
# or llama_decode returns -2. -b stays 2048 so sequences still batch together.
UB="${SMEM_UB:-768}"

# Restart=always: an OOM kill once left a dead port and every subsequent
# request failing for the rest of the run. Now it comes back by itself.
#
# MemoryLimit -- NOT MemoryMax. These nodes run kernel 4.14 with cgroup v1,
# where the property is MemoryLimit=. systemd 249 happily ACCEPTS MemoryMax=
# on a v1 host, reports it back when asked, and writes "unlimited" into
# memory.limit_in_bytes. The cap looks applied and does nothing: the reranker
# sailed past a 3G "limit" to 7.4 GB.
#
# The cap exists because llama-server's --reranking mode leaks. The same
# 0.64 GB model that the embedder holds flat at 0.29 GB anonymous grows past
# 7 GB under sustained reranking. Capping the LEAKER means systemd restarts it
# (0.64 GB, ~10 s) instead of the kernel choosing the largest process, which
# is the 5 GB LLM. All three nodes hit the cap within seconds of each other,
# which is why Pool.post() retries with backoff.
#
# The embedder leaks too, only slowly (measured 2026-09-24: 0.3 GB -> 3.0 GB
# over ~1.8 days of normal use). It does NOT get OOM-killed at its cap: it
# sits there reclaiming (failcnt 478k) and a 5-token query embed went from
# 0.03 s to 1.5 s -- every search routed to that node paid it. So the
# embedder is recycled daily (RuntimeMaxSec, Restart=always brings it back in
# ~5 s), staggered 20 min per node so the pool always has two up.
recycle_dropin() {  # node-index
  local secs=$((86400 + ($1 - 1) * 1200))
  cat <<EOF
mkdir -p /etc/systemd/system/sm-emb.service.d
cat > /etc/systemd/system/sm-emb.service.d/recycle.conf <<'UNIT'
[Service]
# Daily restart against a slow leak; see services.sh (recycle_dropin).
RuntimeMaxSec=$secs
UNIT
EOF
}
unit() {  # name description memlimit args...   (UBIN/UENV override binary/env)
  local name=$1 desc=$2 mem=$3; shift 3
  cat <<EOF
[Unit]
Description=smem $desc
After=network-online.target
Wants=network-online.target

[Service]
${UENV:+Environment=$UENV
}ExecStart=${UBIN:-$BIN} $*
Restart=always
RestartSec=5
MemoryLimit=$mem

[Install]
WantedBy=multi-user.target
EOF
}

emb_files() {  # node-index: put the embedder's llama.cpp build and model on the node
  n "$1" "test -x $EMB_DIR/llama-server && test -s $EMB" && return 0
  tar cf - -C "$EMB_SRC" llama-b11476 embeddinggemma-2-Q8_0.gguf \
    | sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no "172.31.$1.1" 'tar xf - -C /root'
}

install_one() {
  local i=$1
  emb_files "$i"
  {
    echo "cat > /etc/systemd/system/sm-emb.service <<'UNIT'"
    # No --pooling: the GGUF carries Gemma's own (mean). -ub 2048 holds any one
    # sequence (models.EMB_CHARS) and is what EMBED_BAKEOFF.md measured; -np 2
    # gives two 2048-token slots. It leaks as Qwen did: cap + daily recycle stay.
    UBIN=$EMB_DIR/llama-server UENV=LD_LIBRARY_PATH=$EMB_DIR \
    unit sm-emb "embedder (EmbeddingGemma 2)" 3G \
      -m $EMB --embedding -t 8 -c 4096 -b 2048 -ub 2048 -np 2 --host 0.0.0.0 --port 8080
    echo "UNIT"
    echo "cat > /etc/systemd/system/sm-rrk.service <<'UNIT'"
    unit sm-rrk "reranker (Qwen3-Reranker-0.6B)" 3G \
      -m $RRK --reranking -t 8 -c 2048 -b 2048 -ub $UB --host 0.0.0.0 --port 8081
    echo "UNIT"
    echo "cat > /etc/systemd/system/sm-llm.service <<'UNIT'"
    unit sm-llm "LLM (gemma-4-E4B-it)" 9G \
      -m $LLM -t 8 -c 3072 -np 1 --host 0.0.0.0 --port 8082
    echo "UNIT"
    # A transient unit of the same name (the old systemd-run style) shadows
    # the file, so stop and clear it before the reload.
    echo "systemctl stop sm-emb sm-rrk sm-llm 2>/dev/null"
    echo "systemctl reset-failed sm-emb sm-rrk sm-llm 2>/dev/null"
    echo "rm -rf /run/systemd/system/sm-llm.service.d"
    recycle_dropin "$i"
    echo "systemctl daemon-reload"
    echo "systemctl enable sm-emb sm-rrk sm-llm >/dev/null 2>&1"
  } | sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=30 \
        "172.31.$i.1" 'bash -s' >/dev/null 2>&1
}

wait_one() {
  local i=$1 port=$2
  for _ in $(seq 1 90); do
    if n "$i" "curl -s -m 2 http://127.0.0.1:$port/health 2>/dev/null | grep -q ok"; then
      echo "ok"; return 0
    fi
    sleep 2
  done
  echo "TIMEOUT"
}

case "${1:-status}" in
  install)
    for i in $SMEM_NODES; do install_one "$i"; printf "  node%d units installed + enabled\n" $((i-1)); done
    for i in $SMEM_NODES; do n "$i" 'systemctl restart sm-emb sm-rrk sm-llm' >/dev/null 2>&1; done
    for i in $SMEM_NODES; do
      printf "  node%d  emb=%s rrk=%s llm=%s\n" $((i-1)) \
        "$(wait_one "$i" 8080)" "$(wait_one "$i" 8081)" "$(wait_one "$i" 8082)"
    done
    ;;
  start)
    echo "=== starting services on nodes: $SMEM_NODES ==="
    for i in $SMEM_NODES; do
      n "$i" 'test -f /etc/systemd/system/sm-llm.service' >/dev/null 2>&1 || install_one "$i"
      n "$i" 'rm -rf /run/systemd/system/sm-llm.service.d; systemctl daemon-reload;
              systemctl restart sm-emb sm-rrk sm-llm' >/dev/null 2>&1
    done
    for i in $SMEM_NODES; do
      printf "  node%d  emb=%s rrk=%s llm=%s\n" $((i-1)) \
        "$(wait_one "$i" 8080)" "$(wait_one "$i" 8081)" "$(wait_one "$i" 8082)"
    done
    ;;
  stop)
    for i in $SMEM_NODES; do
      n "$i" 'systemctl stop sm-emb sm-rrk sm-llm 2>/dev/null; true' >/dev/null 2>&1
    done
    echo "stopped on all nodes (they will start again on the next node boot)"
    ;;
  api)
    # The API runs on the HOST, not the nodes -- it holds the SQLite store and
    # does the numpy cosine scan; only the model calls go over PCIe.
    if [ -f /etc/systemd/system/smem-api.service ]; then
      systemctl restart smem-api
    else
      systemctl stop smem-api 2>/dev/null; systemctl reset-failed smem-api 2>/dev/null
      systemd-run --unit=smem-api --working-directory=/root/supermem \
        /usr/bin/python3 -u -m smem.server \
        --db "${SMEM_DB:-/root/supermem/smem.db}" \
        --container "${SMEM_CONTAINER:-vca2-docs}" --port "${SMEM_PORT:-8900}"
    fi
    for _ in $(seq 1 20); do
      curl -s -m 2 "http://127.0.0.1:${SMEM_PORT:-8900}/v1/containers" \
        >/dev/null 2>&1 && { echo "api up on :${SMEM_PORT:-8900}"; exit 0; }
      sleep 1
    done
    echo "api did not come up; journalctl -u smem-api" >&2; exit 1
    ;;
  api-stop)
    systemctl stop smem-api 2>/dev/null
    echo "api stopped"
    ;;
  status)
    for i in $SMEM_NODES; do
      printf "  node%d  " $((i-1))
      for p in 8080 8081 8082; do
        s=$(n "$i" "curl -s -m 2 http://127.0.0.1:$p/health 2>/dev/null | head -c 20")
        printf "%s=%s " "$p" "$([ -n "$s" ] && echo up || echo DOWN)"
      done
      n "$i" 'printf " enabled=%s" "$(systemctl is-enabled sm-llm 2>/dev/null)";
              free -m | awk "/^Mem:/{printf \"  mem %s MiB free\n\", \$7}"'
    done
    printf "  host   api=%s (%s)\n" "$(systemctl is-active smem-api)" "$(systemctl is-enabled smem-api 2>/dev/null)"
    ;;
  *) echo "usage: $0 install|start|stop|status|api|api-stop" >&2; exit 1 ;;
esac
