<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# compute/ — experiments run on the card

Everything measured on the nodes lives here. Most top-level files are
single-purpose experiments from 2026-09-20/21, kept because the docs cite
them. Node scripts assume the files have been copied to `/tmp/vca_compute/`
on each node; the host-side scripts address card 0 (nodes 0–2,
`172.31.1-3.1`).

| Subdirectory | What | Doc |
|---|---|---|
| [`dma/`](dma/README.md) | PLX DMA engine benchmarks, host-staged node-to-node ring, the P2P negative result | [DMA.md](../docs/DMA.md) |
| [`alut/`](alut/README.md) | A-LUT mailbox and doorbell modules (host and guest) | [ALUT_MBOX.md](../docs/ALUT_MBOX.md) |
| [`plx_mbox/`](plx_mbox/README.md) | The recovered local patch to `plx87xx`, byte-exact | [HOST_STACK.md](../docs/HOST_STACK.md) |
| [`image/`](image/README.md) | Node image builder (Ubuntu 22.04 on Intel's 4.14 kernel) | [IMAGE_BUILDER.md](../docs/IMAGE_BUILDER.md) |
| `jumbo/` | `jumbo_mtu.ko`: lifts host VCA NIC `max_mtu` so jumbo frames work | [FABRIC.md](../docs/FABRIC.md) |
| `hetero/` | `hetero_pl1.sh`: GPU+CPU at PL1 45 vs 56 W | [THERMAL.md](../docs/THERMAL.md) |
| [`bench/remotion/`](bench/remotion/README.md) | Remotion render on the nodes; groundwork for `farm/` | — |

Top-level files, by purpose:

| Purpose | Files | Doc |
|---|---|---|
| OpenCL probe and SAXPY | `cl_probe.c`, `cl_bench.c` (both `dlopen` libOpenCL, no headers needed) | [GPU.md](../docs/GPU.md) |
| Warm OpenCL daemon (TCP 9200); host-side drivers that split work across the three P580s | `cl_daemon.c`; `gpu_*.py`, `sixdev.py` (some talk to the daemon, some run `cl_bench` over SSH) | [GPU.md](../docs/GPU.md), [SIX_DEVICES.md](../docs/archive/SIX_DEVICES.md) (archived) |
| GEMM tuning | `gemm_ladder.c`, `gemm_ladder2.c`, `gemm_fast.c`, `gemm_verify.c`, `wg_test.c`, `wg_sweep.c`, `wg_one.c`, `all3_wg.sh`, `all3_fast.sh` | [GEMM_WORKGROUP.md](../docs/GEMM_WORKGROUP.md) |
| CPU GEMM | `cpu_gemm.c` (OpenMP SGEMM) | [NODE_IMAGE.md](../docs/NODE_IMAGE.md) |
| Burn and thermal | `burn.c` (AVX2 FMA), `gpuburn.c`, `gpuloop.sh`, `thermal_run.sh` | [THERMAL.md](../docs/THERMAL.md) |
| BlockIO mailbox (superseded by DMA) | `mbox_proto.py`, `mbox_worker.py`, `mbox_host.py`, `mbox_host3.py`, `mbox_bench.py` | [COMPUTE_MAILBOX.md](../docs/archive/COMPUTE_MAILBOX.md) (archived) |

Build OpenCL programs on a node, e.g. `gcc -O2 -o wg_test wg_test.c -lOpenCL -lrt`;
`burn.c` and `cpu_gemm.c` need `-fopenmp` (and `-mavx2 -mfma` for `burn`).

**Hazards.** `burn` and `thermal_run.sh` are deliberate worst-case loads;
`thermal_run.sh` enforces its temperature trip on the node. `cl_daemon`
opens TCP 9200 through the node firewall. Kernel modules in `dma/`, `alut/`
and `jumbo/` load into a live host or node: read [SAFETY.md](../SAFETY.md)
and [GOTCHAS.md](../docs/GOTCHAS.md) first.
