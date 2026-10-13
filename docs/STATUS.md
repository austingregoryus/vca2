<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Status snapshot (2026-10-13)

One page: what runs, the numbers that matter, and what is open. Details live
in the other files, and [LOG.md](LOG.md) is the dated record. **Read
[GOTCHAS.md](GOTCHAS.md) before you touch sysfs or debugfs.**

## The setup

| | |
|---|---|
| Host | Proxmox, Xeon E5-2699 v3 (18C/36T), kernel **`7.0.14-17-pve`** (7.0.2-6-pve kept as a fallback) |
| Host driver stack | [`chainsx/intel-vca2-dkms`](https://github.com/chainsx/intel-vca2-dkms), a community forward-port of Intel's 2.3.26 drivers, plus this repo's `plx_mbox` patch to `plx87xx`, DKMS-built for both kernels. **Not** Intel's stock host drivers, which do not build on 7.0. See [HOST_STACK.md](HOST_STACK.md). |
| Cards | **Two VCA 2 (VCA1585LMV).** Card 0 is nodes 0–2 (x8 link in this host); card 1 is nodes 3–5 (x16). |
| Nodes | Intel's `4.14.20-1.2.3.26.vca` kernel with the **Ubuntu 22.04** userspace from [IMAGE_BUILDER.md](IMAGE_BUILDER.md), on all six. The stock 16.04 images are kept for rollback. |
| Cooling | Card 0: this repo's printed 120 mm shroud. Card 1: 2 × 80 mm fans, **capped at PL1 35 W / PL2 40 W** per node. |
| Alive | **5 of 6 nodes.** Node 3 (card 1, CPU 0, `172.31.4.1`) has been dead since 2026-09-28; see [Thermal](#thermal). |
| Boot | `vca2-card.service` runs [`scripts/vca2-boot.sh`](../scripts/vca2-boot.sh) at host start (it skips a dead node) and powers the nodes off cleanly at stop. |

### Node numbering

Docs use global node numbers. Some code and older commits use the IP octet,
and `vcamon` counts the host as 0.

| Node | Card, CPU | IP | `SMEM_NODES` / octet | vcamon | Role |
|---:|---|---|---:|---:|---|
| 0 | card 0, cpu 0 | 172.31.1.1 | 1 | 1 | smem interactive; render farm |
| 1 | card 0, cpu 1 | 172.31.2.1 | 2 | 2 | smem interactive; render farm |
| 2 | card 0, cpu 2 | 172.31.3.1 | 3 | 3 | smem interactive, **never lent** to the farm |
| 3 | card 1, cpu 0 | 172.31.4.1 | 4 | 4 | **dead** |
| 4 | card 1, cpu 1 | 172.31.5.1 | 5 | 5 | smem background; render farm |
| 5 | card 1, cpu 2 | 172.31.6.1 | 6 | 6 | smem background; render farm |

## What runs on it

- **smem** ([`memory/`](../memory/)): a local Supermemory-shaped memory
  service with an HTTP API, a web console and an MCP server. Embedder
  (EmbeddingGemma 2 since 2026-10-07), reranker (Qwen3) and extraction LLM
  (gemma-4-E4B) all run on the nodes in two lanes: card 0 handles interactive
  search, card 1 handles background ingest and extraction.
- **Render farm** ([`farm/`](../farm/)): Remotion renders split across up to
  four nodes (card 1's live pair plus card 0 nodes 0–1), borrowed from smem
  per job and handed back.
- **vcamon** ([`monitor/`](../monitor/)): temperature, power and throttle
  events for every node and the host, every 30 s, into SQLite.

## The numbers that matter

| Path | Result |
|---|---|
| **GEMM, one P580, tuned kernel** | **457 GFLOP/s**; sustained 474.0 over 30 s on the 22.04 image, no throttling |
| **GEMM, three P580s (one card)** | **1359.5 GFLOP/s** at N=1408, 34% of FP32 peak, 5.8× the shipped kernel |
| GEMM as shipped | ~200 combined, with a cliff to 27 when N is a multiple of 384 (the local work size) |
| CPU GEMM per node | ~71 GFLOP/s on 22.04 (36 on the stock 16.04 image) |
| GPU + CPU together | **1343 GFLOP/s** vs 1250 GPU-alone, **+7.4%**. The contention is shared eDRAM/LLC, not watts. |
| **DMA, node reads host RAM** | 28 → **~4000 MiB/s** (144×) |
| DMA, card-side vs host-side engine | ~3900–4800 vs ~2300 MiB/s: drive it from the card |
| **DMA node-to-node, pipelined ring** | **4423 MiB/s** best direction (node 2 → 1); 2020 MiB/s worst (1 → 2); 4462 in the 7.0.14 matrix |
| Ring rate model | `min(sender send, receiver recv)` × **96%** mean across all six directions |
| Per-node DMA send / recv | n0 ~3997 / ~3949, **n1 ~2058** / ~4849, **n2 ~5642** / ~3129 MiB/s |
| Two DMA engines at once | 3939 + 4179 = **8118 MiB/s**, no contention |
| Node-to-node **P2P** | **impossible on this host**: the root complex will not reflect ([DMA.md](DMA.md)) |
| Virtio-net host ↔ node, MTU 65521 | 13.5–14.6 / 15.8–28.6 Gbit/s |
| Virtio-net node ↔ node, MTU 65521 | 8–26 Gbit/s and **erratic** (2.7–2.8 at MTU 1500) |
| Card ↔ card (virtio) | 0.50–0.53 ms ping, 8–27 Gbit/s per stream: **crossing cards costs nothing measurable** |
| A-LUT + doorbell round trip | **~35 µs** (all three nodes on card 0) |
| PCIe MaxReadReq | 128 B both ends; a `setpci` write is ignored |
| **LLM serving, one card vs the host** | **88.4 vs 82.9 tok/s**, 0.81 vs 0.53 tok/J (TinyLlama Q4) |
| LLM serving, host + card | 172.5 tok/s (+108%) |
| Independent workers, 1/2/3 nodes | 29.7 / 58.6 / 88.0 tok/s: **2.96×** |
| One model split across 3 nodes (RPC) | 12.7 vs 30.2 tok/s on one node: **2.4× slower** (OLMoE Q4) |
| Transcode, host + card | +444 fps (**+65%**) for +36 W |
| Render farm, one card (3 nodes) | 15,474-frame episode in **363 s**; the author's PC takes ~15 min |
| smem retrieval (2026-09-22, Qwen3 embedder) | **1,232 tokens of context at 15/15 recall**: a 98.83% reduction over 105,729 tokens of docs |
| smem retrieval (now, EmbeddingGemma 2) | 15/15 in 21.3 s reranked; 12/15 in 0.11 s without the rerank |

## Thermal

| Card 0, all three nodes | Package power | Hottest core |
|---|---:|---:|
| idle | ~12 W | 35–37 °C |
| all three iGPUs | | 59–60 °C |
| all three CPUs, AVX2, stock airflow | ~130 W | 92–94 °C |
| **all three CPUs, AVX2, 120 mm shroud** | ~121 W | **75–79 °C** |

Intel's limit is 96 °C. The shroud took 15–18 °C off and left 17–21 °C of
headroom. The 45 W package cap is writable, but raising it buys nothing on
card 0: a real GPU+CPU load draws 42.6 W per node and never reaches it.

**Card 1 is a different story.** On 2 × 80 mm fans, its nodes logged 3, 6,531
and ~1,890 throttle events under render load. On 2026-09-28 node 3 throttled
and then hit a `plx87xx` DMA hang 34 s later. It has stayed `link_down`, with
its switch port at x0, through a per-node power cycle, a card reset, a host
power-off, a 60 s wall unplug (which rules out a latched thermal trip), a RAM
reseat and a repaste. The likely cause is heat damage. At PL1 35 W / PL2 40 W
the surviving nodes reach 81 °C with zero throttle events; at stock limits,
even after the repaste, they reached 94 °C. The cap stays until card 1 gets a
120 mm shroud. See [THERMAL.md](THERMAL.md).

## Still open

The full list is in [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md). The ones that
matter most:

1. **Why DMA send and receive rates are a property of the node.** The only
   remaining correlate is which PCI function the DMA engine lands on. Card 1
   is a free test of silicon versus slot, and nobody has run the DMA matrix
   on it yet.
2. **A persistent transport**: A-LUT window plus DMA bulk plus doorbell
   notify, as a real driver instead of throwaway modules. All three pieces
   are measured.
3. **Node 3**: swap its SO-DIMMs with a working sibling's on card 1, or write it off.
4. **A 120 mm shroud for card 1**, then lift its power cap.

**Closed as not worth doing:** splitting one model across nodes with
llama.cpp RPC (run independent workers instead); more SAXPY; padding N to
dodge the 384 "cliff".
