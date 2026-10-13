<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# llama.cpp RPC on the VCA nodes

> **Historical (2026-09-20).** Card 0 only, nodes on the stock Ubuntu 16.04
> image. The approach here — one model split across nodes by llama.cpp RPC —
> was measured *slower* than a single node and is not used. The card's LLM
> use is one independent model server per node; see
> [BENCHMARK.md](BENCHMARK.md) and [`memory/`](../memory/README.md).

Real workload: host `llama-cli` + three `ggml-rpc-server` processes on the
Xeons. Same llama.cpp commit `ce8caa6` (0.4.1-dev / ggml 0.24.0).

## Layout

| Role | Where | Binary |
|---|---|---|
| Coordinator | the host (gcc 14) | `/tank-red/vmdata/vca/llama-build-host/bin/llama-cli` |
| RPC workers | nodes 0–2 | `/tmp/ggml-rpc-server` (Zig `x86_64-linux-gnu.2.23`, AVX2) |
| Model | host ZFS | `/tank-red/vmdata/vca/models/tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf` (638 MiB, Q4_K_M) |

Xenial has gcc 5.4 — too old for C++17 llama.cpp. Workers were
cross-compiled on the host with Zig 0.13 so they link only glibc 2.23
(`libm`, `libpthread`, `libc`, `libdl`). Confirmed `ldd` on the guests.

RPC listen: `-H 0.0.0.0 -p 50052 -t 4`. Guest INPUT accepts 50052 (left
open with 9200). Default bind is 127.0.0.1; that would not work.

## Command that worked (2026-09-20)

```bash
llama-cli -m tinyllama-1.1b-chat-v1.0.Q4_K_M.gguf \
  --rpc 172.31.1.1:50052,172.31.2.1:50052,172.31.3.1:50052 \
  -ngl 99 -t 4 -n 16 \
  -p "The capital of France is" \
  --single-turn --simple-io --no-display-prompt \
  -lv 1 -fit off
```

`--single-turn` is required: without it the new `llama-cli` stays in a
chat TUI and spins (`>` forever, 99% CPU, ~70 MB RSS, model never loaded).
Do not use `--verbose` (it set verbosity to INT_MAX and wrote a 129 MB log).

## Result

- Prompt seen; completion: **“The capital of France is Paris.”**
- **Prompt 44.6 t/s | Generation 14.5 t/s**
- All three RPC servers **Accepted client connection**
- RPC0 advertised **15810 MiB** free on node 0 at handshake
- Temps **~36–38 °C** (TinyLlama is a small load)
- Host is also an E5-2699 v3 with 42 GiB; this 1.1B model fits locally.
  RPC was used (connections on all three nodes) but we did **not** prove a
  model that *only* fits when split. Next: a GGUF larger than one node’s
  15 GiB if we want a hard split test.

> **Read with [BENCHMARK.md](BENCHMARK.md) — this file measures the wrong
> thing for judging the card.** Everything below is *single-stream latency*
> with RPC layer-splitting, where three nodes are inherently worse than one.
> Measured 2026-09-22, the same hardware running **independent workers**
> scales **2.96x** and out-serves the host outright. The card is a
> throughput device; these numbers are the latency case.

## OLMoE-1B-7B Instruct Q4_K_M (real MoE, 2026-09-20)

File: `OLMoE-1B-7B-0924-Instruct-Q4_K_M.gguf` **3.9 GiB** (1B active / 7B total).
Same prompt, 16 tokens, `-t 4`, `--single-turn --simple-io -fit off`.

| Setup | Prompt t/s | Gen t/s | Wall | VCA temps (core 0) |
|---|---:|---:|---:|---|
| Host only (E5-2699 v3, no RPC) | 63.4 | **33.4** | 21 s | unchanged ~38–40 °C |
| RPC → node 0 only | 62.5 | **30.2** | 21 s | node0 **49 °C**, others ~38 |
| RPC → all 3 nodes | 52.3 | **12.7** | 21 s | 42 / 39 / 39 °C |

All three said Paris. **One VCA node ≈ host at 4 threads.** Spreading this MoE across three RPC links **cut decode to 38%** of single-node. The model fits in 16 GB; extra hops are a 5 Gbit tax, not extra experts-on-CPUs.

llama.cpp still split **layers**, not expert_id → node. `--n-cpu-moe` is GPU-vs-CPU, unused here.

Hottest LLM load so far: **49 °C** on node 0. Still far from 100.

## Rebuild notes

Host: `cmake -DGGML_RPC=ON -DGGML_RPC_RDMA=OFF -DBUILD_SHARED_LIBS=OFF`
→ `llama-cli`, `ggml-rpc-server`.

VCA: same flags + Zig wrappers `-target x86_64-linux-gnu.2.23`,
`-DGGML_NATIVE=OFF -DGGML_AVX2=ON -DGGML_FMA=ON -DGGML_OPENMP=OFF`.
