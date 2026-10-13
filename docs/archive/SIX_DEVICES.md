<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Six devices, one app (first demo)

> **Archived 2026-10-13.** One of the first six-device demos: OpenCL daemons on the three nodes driven over SSH/TCP. It was superseded by the DMA engine ([../DMA.md](../DMA.md)) and the tuned GEMM ([../GEMM_WORKGROUP.md](../GEMM_WORKGROUP.md)). Kept for provenance; the numbers are from the untuned kernels.

Host program `compute/gpu_all3.py` on `vca-host` fires the same OpenCL SAXPY
on all three P580s. Transport is SSH/TCP (virtio-net), not the ALUT
mailbox. That was the point: prove the **shape** before the fast fabric.

## How to run

On the Proxmox host, with nodes `net_device_ready` and `ocl-icd-libopencl1`
installed:

```bash
export SSHPASS=vista1
bash /tmp/vca_compute/_gpu_all3.sh
# or, from the repo copy:
python3 /tmp/vca_compute/gpu_all3.py
```

Guest binary: `/tmp/vca_compute/cl_bench N ITERS 1` (`1` = skip scalar CPU
loop). Sources: `compute/cl_bench.c`, `compute/gpu_all3.py`.

## Result (2026-09-20)

Problem: SAXPY, 8,388,608 floats × 20 kernel iterations **per GPU**
(32 MiB vectors each). Check: `y[0]=1.0`, `y[100]=4.15` on every node.

| | Sequential (one GPU at a time) | Parallel (all three) |
|---|---:|---:|
| Wall clock | 1.281 s | **0.477 s** |
| Speedup | 1× | **2.69×** |
| Per-kernel time | ~0.035 s each | ~0.035 s each (overlapped) |
| Kernel GFLOP/s | 9.70 / 9.50 / 9.28 | 9.66 / 9.73 / 9.47 |
| Sum of kernel GFLOP/s | — | **28.86** |

`SIX_DEVICE_GPU_OK` — all three returned the correct vector.

Wall clock is dominated by **OpenCL context create + SSH**, not by the
0.035 s kernel. The 2.69× (not 3.00×) gap is launch jitter. The kernels
themselves did **not** serialize: three ~9.5 GFLOP/s devices at once.

Earlier run that still included the scalar CPU loop inside `cl_bench`
was exactly **3.00×** (2.770 s → 0.924 s). Same conclusion: the host
overlaps the three nodes.

## Tiled SAXPY (one problem, three GPUs)

`compute/gpu_tile.py` — 24,576,824 floats (96 MiB) split into three
contiguous tiles. `x[i] = 0.001 * global_index`. Host checks `y[0]`,
`y[100]`, `y[last]` against `1 + 21 * 1.5 * x` (20 timed kernels + 1 warmup).

| Tile | Range | GPU GFLOP/s | Check |
|---|---|---:|---|
| node0 | 0 .. 8,388,607 | 9.54 | OK (float error on ylast ~2e-5 rel) |
| node1 | 8,388,608 .. 16,777,215 | 9.57 | OK |
| node2 | 16,777,216 .. 25,165,823 | 9.29 | OK |

Wall **0.436 s**, `TILED_SAXPY_OK`. Same OpenCL-setup-dominated wall as the
non-tiled demo; kernels still ~0.035 s and overlapped.

## CPUs + GPUs in one host process

`compute/sixdev.py` — overlapping:

- 3× mailbox `SUM_U64` of 262,144 ones (BlockIO DMA)
- 3× GPU tiles of the 24M SAXPY

| | ok | time |
|---|---|---:|
| cpu0 / cpu1 / cpu2 | True | 0.24 / 0.32 / 0.31 s |
| gpu0 / gpu1 / gpu2 | True | 0.44 / 0.43 / 0.43 s |
| **wall** | **SIXDEV_OK** | **0.475 s** |

CPU work finished inside the GPU/OpenCL wall. Six devices, one process,
one clock.

## Warm GPU daemons (context kept alive)

`compute/cl_daemon.c` listens on TCP **9200** with the OpenCL context already
created. Host `compute/gpu_warm.py` talks to all three.

Idle ping: 1–9 ms. Parallel tiled SAXPY (same 8M×20 as before):

| | Cold (SSH + `clCreateContext`) | Warm daemon |
|---|---:|---:|
| Parallel wall | 0.477 s | **0.103 s** |
| Kernel | ~0.035 s | 0.034–0.037 s |
| Host RTT | — | 0.088–0.102 s |
| Kernel GFLOP/s | ~9.5 | 9.19–9.76 |

Wall dropped ~4.6×. Remaining ~70 ms is TCP + buffer upload/download, not
device compute. `WARM_SAXPY_OK`.

12 s all-three GPU burn (`BURN`, ~9.36 GFLOP/s sustained): CPU package
temps **idle 33–36 °C → peak 45 °C** (node 0 core 1). Crit is 100 °C.
`vcactl temp` only sees CPU cores, not iGPU junction.

Daemons left running; guest INPUT accepts TCP/9200. Left open on purpose
while experimenting.

## Kernel-only and tiled GEMM (warm session, persistent buffers)

`PREP` uploads once; `KERNEL` / `KERNEL_NR` re-run on device memory.
Host keeps TCP sessions open (`compute/gpu_kern.py`).

| Call | Parallel wall | Device gpu_s | Notes |
|---|---:|---:|---|
| Cold SSH SAXPY | 0.477 s | 0.035 s | new context |
| Warm SAXPY (H2D+kernel+D2H) | 0.103 s | 0.035 s | |
| PREP (upload) | 0.194 s | — | once |
| KERNEL + readback | **0.045 s** | 0.037–0.041 s | |
| KERNEL_NR (no D2H) | **0.039 s** | 0.035–0.038 s | wall ≈ kernel |

The 70 ms tax was almost all buffer copies. Kernel-only host wait is now
inside a few milliseconds of device time.

Tiled **GEMM** 384×384, 128 rows per GPU, A=1, B=1, C=n=384 on every
element (`KERN_GEMM_OK`):

| Node | rows | kernel | GFLOP/s | C check |
|---|---|---:|---:|---|
| 0 | 0–127 | 5.1 ms | 7.43 | 384.0 |
| 1 | 128–255 | 4.9 ms | 7.68 | 384.0 |
| 2 | 256–383 | 5.3 ms | 7.09 | 384.0 |

Parallel wall **0.012 s**. Naive triple-loop GEMM, memory-bound, still
correct and overlapped. This is a real split matrix multiply, not SAXPY.

## 1024×1024 split GEMM (the non-toy)

`compute/gpu_gemm1024.py` — N=1024, row tiles **341 / 341 / 342**, A=1, B=1,
every checked C element **1024.0**. `GEMM1024_OK`.

Naive kernel (one shot, includes a warmup NDRange):

| Node | rows | kernel | GFLOP/s |
|---|---|---:|---:|
| 0 | 0–340 | 12.2 ms | **58.8** |
| 1 | 341–681 | 12.2 ms | **58.5** |
| 2 | 682–1023 | 13.8 ms | **51.9** |

Parallel wall **0.038 s**. Summed ~**169 GFLOP/s**. At 384² we only saw ~7
GFLOP/s — 1024² is large enough that NEO actually feeds the EUs.

Blocked 16×16 local-memory kernel ×20 reps: **18.7–20.1 GFLOP/s** (slower).
Our tiling lost to the compiler on GT4e. Keep the naive kernel until we
write a better one (wider tiles / float4).

Temps: 34–37 °C before, **35–39 °C** after (~+3 °C). GT 1150 MHz. Package
39 °C. Nothing close to 100 °C.

## Scale: 1024 / 1536 / 2048 (naive vs float4)

`compute/gpu_gemm_scale.py`. All sizes: A=1, B=1, C=n on every tile. `SCALE_GEMM_OK`.

| N | Kernel | Per-GPU GFLOP/s | Sum | Parallel wall |
|---:|---|---|---:|---:|
| 1024 | naive | 58.8 / 59.2 / 51.8 | **170** | 0.039 s |
| 1024 | float4 ×10 | 38.9 / 36.6 / 43.4 | 119 | 0.217 s |
| 1536 | naive | 9.2 / 9.0 / 9.0 | **27** | 0.550 s |
| 2048 | naive | 45.7 / 44.9 / 30.6 | **121** | 0.390 s |

Hand-vectorized float4 **lost** to the naive loop, same story as 16×16 tiles.
**1536 is a slow size** (~9 GFLOP/s) even though 1024 and 2048 are fine —
not monotonic in N. Prefer power-of-two N for now. Node 2 is the slow tile
at 2048 — **not** an ALUT/NTB personality (the GEMM is entirely on-device).
`splits()` hands node 2 the remainder, which is always the row count with the
most divisors (684 at 2048, 342 at 1024, 470 at 1408), so it always drew the
worst auto workgroup. With `local={1,128}` all three match: 59.5 / 59.7 /
60.4 at N=2048. See [GEMM_WORKGROUP.md](../GEMM_WORKGROUP.md).

Temps 33–37 °C → **35–39 °C**, package 39 °C, GT 1150. Still cold.

## Size sweep: 384k is poison

> **Superseded 2026-09-21.** Everything in this section reproduces, but the
> explanation is wrong. The cliff is the **implicit local work size**, not
> the matrix, the k-loop or the leading dimension: `n % 384 == 0` is exactly
> when `rows = n/3` is a multiple of 128, and a cleanly-factoring `rows` makes
> NEO pick a wide dim-0 workgroup, which turns `A[r*n+k]` into a stride-n
> gather across SIMD lanes. Passing `local = {1,128}` removes the cliff at
> every size. n=1536 rows=512: **9.3 -> 58.2 GFLOP/s**; n=1408 with rows=512
> collapses to **11.2**, so the "fast size" was never the matrix. Combined
> peak is now **236.7 GFLOP/s**. Read
> [GEMM_WORKGROUP.md](../GEMM_WORKGROUP.md); keep the tables below only as the
> record of what NULL local sizes do. The "pad N to 1544/1600" workaround is
> no longer needed.

`gpu_size_sweep.py`, naive kernel, all C=n, `SWEEP_OK`. Combined GFLOP/s:

| N | Sum GFLOP/s | Notes |
|---:|---:|---|
| 512 | 135 | |
| **768** | **28** | 2×384 |
| 1024 | 160 | |
| **1152** | **30** | 3×384 |
| 1280 | 143 | |
| **1408** | **199** | peak (~74 / 72 / 54 per GPU) |
| 1472 | 149 | |
| **1536** | **27** | 4×384 |
| 1600 | 178 | |
| 1664 | 147 | |
| 1792 | 170 | |
| **1920** | **27** | 5×384 |
| 2048 | 123 | |

Slow cluster is exactly **N multiple of 384** (384 itself was ~7 GFLOP/s/GPU).
Confirmed: 384 → 22 combined; **400 still small-N noisy** (10/10/45); **640 → 202**
(fast); 896 → 149; 1408 → **205**. Use 1024, **640**, or **1408**, not 1536.

Temps during sweep: 33–38 °C → **36–41 °C**.

**Pad workaround:** 1536 → **27.5** GFLOP/s; **1544** (+8) → **148**; 1568 → 133; 1600 → **174**.
If you need ~1536, pad to 1544 or 1600 (~0.5–4% extra flops, ~5× faster).

**The trap is the matrix, not the launch grid.** `GEMMG` keeps n=1536 (same A/B/C, kernel still `c>=n return`) and only pads `global_size[1]`:

| n | g1 | Combined GFLOP/s |
|---:|---:|---:|
| 1536 | 1536 / 1544 / 1600 / 2048 | **27.4–27.6** (still dead) |
| 384 | 384 / 400 / 512 | **23** (still dead) |
| 1408 | 1408 | **196** |
| 1408 | 1536 or 1600 | **172–174** (still fast) |

Dummy extra work-items do not save a 384k matrix. A poison **grid** on a fast 1408 matrix does not kill it.

**Padding B’s leading dim (same n, same FLOPs) also fails.** `GEMMLDA`:

| n | lda | Combined |
|---:|---:|---:|
| 1536 | 1536 / 1544 / 1600 / 2048 | **27–29** |
| 1408 | 1408 | **205** |
| 1408 | 1536 | **141** (hurt, not dead) |
| 384 | 384 / 400 / 512 | **22** |

**Padding A and B strides together still fails.** `GEMMLDAB`, n=1536:

| lda, ldb | Combined |
|---|---:|
| 1536, 1536 | 27 |
| 1600, 1536 (A only) | 31 |
| 1536, 1600 (B only) | 28 |
| **1600, 1600** | **33** |
| 2048, 2048 | 25 |
| 1408 control | 199 |

So the 384 trap is the **k-loop trip count / problem N**, not NDRange and not row-major stride. Workaround is still: **solve a slightly larger N** (1544/1600), which changes the loop bound. Storage padding is not enough.

**Manual k+=16 unroll** (`GEMMK16`) vs naive:

| N | Naive combined | k16 combined |
|---:|---:|---:|
| 384 | 22 | **80** (helps, not fixed) |
| 1024 | 170 | 120 (hurts) |
| 1408 | 198 | 191 |
| 1536 | 27 | **44** (still a cliff) |
| 1600 | 169 | **191** |

Unrolling does not remove the 1536 hole. On fast sizes, leave the kernel naive (or k16 at 1600). Always prefer **pad N** over clever k-loops for 384k.

OpenCL `INFO`: 8 CUs, max workgroup 256, work-item 256³, **preferred float vector width = 1** (matches float4 losing to naive). `glob` ~12.4 GiB. Reported `local=1` is likely a bogus enum read; do not trust it.

## On-device pipeline (no host between multiplies)

`GEMM2`: C = A·B, then D = C·B (i.e. A B²) on the GPU. A=1, B=1 ⇒ D = n².
1024² tiles, D=1,048,576 everywhere. `GEMM2_OK`.

| Node | kernel (2 GEMMs) | GFLOP/s |
|---|---:|---:|
| 0 | 23.5 ms | 60.9 |
| 1 | 24.0 ms | 59.5 |
| 2 | 29.3 ms | 49.0 |

Parallel wall **0.065 s**. Same ~55–60 GFLOP/s as a single GEMM; the second
multiply did not round-trip through the host. Temps unchanged (~36–39 °C).

## Hetero: CPU OpenMP + iGPU on the same node

Daemon compiled `-O3 -fopenmp -mavx2`, `OMP_NUM_THREADS=4`. Blocked 32×32
CPU GEMM.

Calibration (node 0, n=1024, C=1024):

| CPU rows | time | GFLOP/s |
|---:|---:|---:|
| 128 | 22.7 ms | **11.8** |
| 256 | 45.1 ms | **11.9** |

Four Broadwell cores, blocked, ~12 GFLOP/s. GPU on the same tile is ~55.

> **Superseded 2026-09-22.** The conclusion below (HETERO is slower) no longer
> holds. It was measured when the CPU ran at ~36 GFLOP/s on Ubuntu 16.04 and
> was 15 °C hotter. On the 22.04 image the CPU is 1.9x faster, and a sustained
> re-test gives **GPU+CPU 1343 GFLOP/s against GPU-alone 1250 (+7.4%)** —
> heterogeneous is now a win. See [THERMAL.md](../THERMAL.md#hetero-re-test-2026-09-22).
>
> The *reason* also changed: the two do not compete for watts, they compete
> for the shared eDRAM/LLC. Details in THERMAL.md.

`HETERO 1024 256 85` on all three nodes (GPU 256 rows + CPU 85, overlapped):
both `gc0` and `cc0` = 1024. `HETERO_OK`. Device wall ~48 ms, **14.9
GFLOP/s** on that wall — **slower than GPU-only 341 rows (~12 ms, ~59
GFLOP/s)** because we stole rows from the faster device. CPU is ~1/5 the
GPU; extra CPU rows only help if they are **additional** work, not a slice
taken off the iGPU.

Temps: 34–37 °C → node 0 **40 °C** (CPU load), others 34–37 °C. GT 1150.

### Extra CPU work (HADD), not stolen rows

`HADD 1408 <full tile> 80`: GPU does the whole 469/469/470-row tile; CPU
adds 80 independent rows in parallel. Both C=1408. `PAD_HADD_OK`.

| | Host wall | Device wall | Combined GFLOP/s (node0) |
|---|---:|---:|---:|
| GPU-only 1408 | 0.076 s | ~25 ms kernel | ~75 / tile, **198** sum |
| GPU tile + 80 CPU rows | **0.069 s** | 26.5 ms (cpu_s=26.4) | **82** on that wall |

CPU time matched the GPU tile (~26 ms) — extra ~15% FLOPs at **the same or
better wall**. This is the split that helps. Temps 34–37 → **36–41 °C**,
package 40 °C.

Temps after that burst: cores 33–37 °C (node 0 had been 43 °C from earlier
work, came down). GT freq 1150 MHz. No iGPU junction sysfs — only
`x86_pkg_temp`, `coretemp`, ACPI TZ, `gt_cur_freq_mhz`, RAPL `energy_uj`.

## What this is not

- Not the ALUT/doorbell path. GPUs still go over virtio-net SSH; CPUs over
  BlockIO files.
- Not a fused CPU+iGPU matmul on the same node.

## Meaning

The product shape works: **one process on the host, six compute devices
on the card** (3 CPU mailbox + 3 GPU OpenCL). Remaining work is making
the control/data path faster, not discovering whether the devices exist.
