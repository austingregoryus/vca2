<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Documentation

The measurements and the reverse engineering of the Intel VCA 2
(VCA1585LMV): two cards, six nodes, three Xeon E3-1585L v5 + three Iris Pro
P580 each. The summary is in [`../README.md`](../README.md); the one-page
state of things is [STATUS.md](STATUS.md).

## Start here

| File | What it is |
|---|---|
| [STATUS.md](STATUS.md) | One-page snapshot: the setup, node numbering, the numbers that matter, what is open. |
| [SETUP.md](SETUP.md) | Bringing a card up: host kernel and drivers, node images, boot service, cooling. |
| [GOTCHAS.md](GOTCHAS.md) | **Read before touching sysfs or debugfs.** What hangs the card, and how to recover. |
| [HOST_STACK.md](HOST_STACK.md) | What the host actually runs: a community forward-port plus a local patch, not Intel's drivers. Read before trusting or reproducing any number. |

## Findings

| File | What it is |
|---|---|
| [GEMM_WORKGROUP.md](GEMM_WORKGROUP.md) | The tuned GEMM (1.36 TFLOP/s per card), and why "8 CUs" and the "384 cliff" were bugs. |
| [DMA.md](DMA.md) | The DMA engine: 144× on node reads, node-to-node at 4.4 GB/s, the per-node asymmetry, and why P2P is impossible on this host. |
| [FABRIC.md](FABRIC.md) | PCIe topology, NTB BARs, A-LUT, doorbells, virtio-net vs BlockIO, jumbo MTU, card-to-card. |
| [THERMAL.md](THERMAL.md) | Power and temperature, the 120 mm shroud, the power cap, cache contention, and the node lost to heat. |
| [BENCHMARK.md](BENCHMARK.md) | Is the card worth the slot? What it adds to a busy host, per joule: transcode, LLM serving, embeddings. |
| [NODE_IMAGE.md](NODE_IMAGE.md) | Ubuntu 22.04 on the nodes instead of 16.04, and what it measurably buys. |
| [IMAGE_BUILDER.md](IMAGE_BUILDER.md) | Building your own node image, for this or another distro. |
| [GPU.md](GPU.md) | i915, Media SDK, Quick Sync and OpenCL on the P580s. |
| [ALUT_MBOX.md](ALUT_MBOX.md) | The A-LUT shared-memory window and doorbell mailbox (~35 µs). |
| [LLAMA.md](LLAMA.md) | llama.cpp RPC across the three Xeons, and why it loses to independent workers. |

Application results live with their code:
[`../memory/RESULTS.md`](../memory/RESULTS.md) (smem),
[`../farm/README.md`](../farm/README.md) (render farm),
[`../compute/bench/remotion/README.md`](../compute/bench/remotion/README.md)
(render bench).

## Reference

| File | What it is |
|---|---|
| [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) | What has not been proven yet. |
| [KERNEL_PORT.md](KERNEL_PORT.md) | Modern kernels on the nodes: a proposal, not a result. |
| [PRIOR_ART.md](PRIOR_ART.md) | Other public VCA 2 work, and what is worth taking from it. |
| [SOURCE_MAP.md](SOURCE_MAP.md) | Where the VCASS 2.3.26 sources live and what they implement. |
| [LOG.md](LOG.md) | The dated lab notebook. Append-only: retractions are new entries, not edits. |
| [artifacts/](artifacts/) | Raw dumps (A-LUT, scratchpads, VOP stats) and thermal CSVs. |
| [archive/](archive/) | Superseded documents kept for provenance: the original research plans, the September setup guide, the first six-device demos. |

## Conventions

- **Node numbers are global, 0–5.** Node *n* is card *n*÷3, CPU *n* mod 3, at
  `172.31.(n+1).1`. The mapping to the IP-octet numbering some code uses is
  in [STATUS.md](STATUS.md#node-numbering).
- **Dates are when it was measured.** If two documents disagree, the later
  entry in [LOG.md](LOG.md) wins, and the disagreement is a bug worth
  reporting.
- MiB/s for DMA, Gbit/s for networking, GFLOP/s for FP32.
- Commit hashes cited in the docs (mostly in [LOG.md](LOG.md)) refer to the
  private development history, 2026-09-20 to 2026-10-13. The public
  repository starts from a single snapshot of it.
