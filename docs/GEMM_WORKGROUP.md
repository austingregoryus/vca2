<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# The 384 trap was the workgroup shape

Measured 2026-09-21 on node 0 (and confirmed on all three). This supersedes
the "384k is poison" conclusion in [SIX_DEVICES.md](archive/SIX_DEVICES.md).

## What it actually is

Every GEMM in `compute/cl_daemon.c` enqueued with **`local_work_size = NULL`**
and `g[0] = rows`, `g[1] = n`. In the kernel, `r = get_global_id(0)`.

Dim 0 is the SIMD-adjacent dimension, so adjacent lanes differ in **r**, not
in c. That makes `A[r * n + k]` a **stride-n gather across the SIMD width**,
while `B[k * n + c]` is a broadcast. It is the worst of the two mappings.

With `local = NULL`, NEO picks a workgroup shape from how the global size
factors. `splits()` gives `rows = n/3`, so:

> **n divisible by 384 ⟺ rows = n/3 divisible by 128.**

When `rows` factors cleanly, NEO picks a wide dim-0 group and the gather
happens. When `rows` is 469 (7×67) or 341 (11×31) it is *forced* into a
narrow dim-0 group and fills the workgroup from dim 1, which coalesces.

The cliff was never about N, the k-loop, the NDRange, or the leading
dimension. It was about the divisors of `rows`.

## The decisive pair

Same n, same k-loop trip count, same lda/ldb. One fewer row:

| Test | GFLOP/s |
|---|---:|
| n=1536 **rows=512** local=NULL | **9.27** |
| n=1536 **rows=511** local=NULL | **50.28** |
| n=1536 rows=513 local=NULL | 45.94 |

5.4× from removing one row. That rules out every data-dependent explanation.

The converse holds too — their "peak" size is not magic:

| Test | GFLOP/s |
|---|---:|
| n=1408 rows=469 local=NULL (the documented peak) | 76.52 |
| n=1408 **rows=512** local=NULL | **11.15** |

n=1408 collapses when you hand it a clean row count. The trap follows
`rows`, not `N`.

## Forced local sizes at the dead size (n=1536, rows=512)

| local | GFLOP/s |
|---|---:|
| NULL (today) | 9.27 |
| **{1,128}** | **58.24** |
| {1,256} | 45.43 |
| {1,32} | 50.8 |
| {2,128} | 44.48 |
| {16,16} | 9.57 |
| {256,1} (deliberately worst) | 9.30 |

Monotonic in dim 0: the more lanes vary in `r`, the slower. `{16,16}` being
slow is also why the hand-tiled `gemm_blocked` kernel only ever reached
~19 GFLOP/s — it pinned dim 0 to 16 and ate the same gather.

## Local-size sweep (node 0, rows = n/3)

| local | n=384 | n=1024 | n=1408 | n=1536 | n=1600 | n=2048 |
|---|---:|---:|---:|---:|---:|---:|
| NULL | 7.6 | 60.7 | 76.1 | **9.1** | 64.4 | 45.1 |
| {1,32} | 47.4 | 62.4 | 61.8 | 50.8 | 62.1 | 56.5 |
| {1,64} | 51.4 | 64.0 | 63.5 | 53.1 | 64.4 | 55.9 |
| **{1,128}** | **51.6** | **65.3** | **76.4** | **54.1** | **65.7** | **59.5** |
| {1,256} | 54.0 | 60.8 | 64.4 | 29.4 | 63.4 | 52.4 |
| {2,128} | 36.0 | 53.4 | 58.5 | 40.5 | 57.8 | 45.5 |
| {4,64} | 25.1 | 34.9 | 37.2 | 23.1 | 37.2 | 31.3 |
| {8,32} | 13.9 | 20.3 | 20.8 | 17.2 | 20.8 | 19.4 |
| {16,16} | 7.7 | 10.9 | 11.1 | 9.5 | 11.0 | 10.5 |
| {32,8} | 7.8 | 11.2 | 11.3 | 9.4 | 11.1 | 10.1 |
| {64,4} | 7.8 | 11.2 | 11.2 | 9.3 | 11.1 | 9.7 |

`{1,128}` matches or beats NULL at **every** size and the size dependence is
gone. There is no remaining reason to pad N to 1544/1600.

## All three GPUs, combined

`splits(n)` as the `gpu_*.py` scripts use it, run in parallel from the host:

| N | NULL (today) | {1,128} | gain |
|---:|---:|---:|---:|
| 384 | 23.5 | **161.9** | 6.9× |
| 1024 | 174.5 | 196.3 | 1.13× |
| 1408 | 216.5 | **236.7** | 1.09× |
| 1536 | 28.0 | **174.7** | 6.2× |
| 1600 | 186.1 | 198.7 | 1.07× |
| 2048 | 123.8 | 179.7 | 1.45× |

New combined peak **236.7 GFLOP/s** at N=1408. Worst case rises from 23.5 to
161.9. Temps over the whole run: 37→43 °C package, GT 1150 MHz.

### Node 2 is not slow

With `local=NULL`, node 2 got the remainder row count from `splits()` (684 at
N=2048, 342 at 1024, 470 at 1408) — always the one with the most divisors,
so always the slowest tile. With `{1,128}` all three match:

| N | NULL (n0 / n1 / n2) | {1,128} (n0 / n1 / n2) |
|---:|---|---|
| 1408 | 78.5 / 79.4 / **58.5** | 78.5 / 79.2 / **79.1** |
| 2048 | 45.6 / 46.6 / **31.5** | 59.5 / 59.7 / **60.4** |

SIX_DEVICES.md attributed this to "that node's ALUT/NTB personality." It was
the row count. The three P580s are identical.

## The fix

`compute/cl_daemon.c`, `do_gemm()` — `gemm_local()` sets `{1,128}` and rounds
the global size up. The kernel already guards `r >= rows || c >= n`.

Verified through the daemon protocol on node 0:

```
GEMM 1536 0 512  -> gflops=58.69   (was 9.2)
GEMM 384  0 128  -> gflops=51.69   (was ~7.5)
GEMM 1408 0 469  -> gflops=75.00   (unchanged)
GEMM 2048 0 682  -> gflops=60.62   (was ~45)
```

Only `do_gemm` is patched. `do_gemm_lda`, `do_gemm_ldab`, `do_gemmg`,
`do_gemm_f4` and `do_gemm_k16` still pass NULL — they exist only to
investigate the trap and are now obsolete. `do_gemm_b` (the 16×16 blocked
kernel) sets `{16,16}` explicitly, which is why it was always mediocre.

## Two enum bugs found alongside

`cl_probe.c` hand-defines the OpenCL constants, and `cl_daemon.c do_info()`
passes raw hex. Two were off:

| Query | Was | Returns | Correct | Returns |
|---|---|---:|---|---:|
| `MAX_COMPUTE_UNITS` | `0x1021` (= `MAX_CONSTANT_ARGS`) | **8** | `0x1002` | **72** |
| `LOCAL_MEM_SIZE` | `0x1022` (= `LOCAL_MEM_TYPE`) | **1** | `0x1023` | **65536** |

So the P580 reports **72 compute units**, exactly matching the datasheet's 72
EUs. GPU.md's paragraph about "8 CUs vs 72 EUs, NEO's CU count is not 1:1
(likely subslices)" was reading the wrong field. STATUS/SIX_DEVICES's
`local=1` note ("likely a bogus enum read; do not trust it") was right about
the symptom — it is `LOCAL_MEM_TYPE = CL_LOCAL`. Real local memory is 64 KB.

`pref_float = 1` is a genuine read (`0x100A` is correct) and does explain why
the hand-vectorized float4 kernel lost.

Both fixed. Confirmed live: `OK INFO cu=72 max_wg=256 wis=256,256,256
local=65536 glob=13262938112 pref_float=1`.

## What this means for headroom

72 CUs × 8 lanes × 2 flop × 1150 MHz ≈ **1325 GFLOP/s** FP32 per P580,
~4.0 TFLOP/s across the card. At 79 GFLOP/s per GPU the naive kernel is at
**~6% of peak**, so the remaining upside is large and it is a kernel problem,
not a fabric problem. Next levers, in order:

1. **CLBlast** — has Gen9-tuned GEMM, drop-in, almost certainly beats any
   hand kernel here.
2. `cl_intel_subgroups` + `intel_sub_group_block_read` — the NEO-native way
   to get coalesced loads without fighting the auto workgroup shape.
3. Register blocking (each work-item computes 4–8 output columns) — the
   standard next step once the access pattern is right.

## Reproduce

Standalone, no daemon, guest-local, touches no VCA driver or BAR:

```bash
# on a node, needs opencl-headers (node 0 has them; copy the binary to 1/2)
gcc -O2 -o wg_test wg_test.c -lOpenCL -lrt && ./wg_test
gcc -O2 -o wg_sweep wg_sweep.c -lOpenCL -lrt && ./wg_sweep
```

Sources in `compute/wg_test.c`, `compute/wg_sweep.c`, `compute/wg_one.c`,
host runner `compute/all3_wg.sh`.

---

# Part 2: how far the kernel can actually go

Fixing the workgroup shape got the naive kernel to ~79 GFLOP/s, which is only
~6% of the P580's real 1325 GFLOP/s peak. That is not a hardware limit, it is
an arithmetic-intensity limit: every work-item computed one C element with 2n
loads for 2n flops, i.e. **one flop per four bytes**. Purely memory bound.

`pref_float = 1` already told us vector width was not the answer (float4 lost
to naive). The answer is **register blocking**: make each work-item compute
several outputs so one load feeds several FMAs.

## The ladder (node 0, n=1536, rows=512)

| Kernel | best GFLOP/s |
|---|---:|
| `naive_rc`, r in dim 0 (what shipped) | 9.4 |
| `rb1`, c in dim 0, 1 output/work-item | 56.4 |
| `rb4`, 4 rows/work-item | 117.5 |
| `rb8`, 8 rows/work-item | 124.3 |
| `rb16`, 16 rows/work-item | 135.3 |
| `lm4`, 4 rows + A in `__local` | 216.7 |
| `lm8`, 8 rows + A in `__local` | 316.9 |
| `w2r8`, 8 rows x float2 columns | 204.6 |
| **`l2r8`, 8 rows x float2 + A in `__local`** | **450.9** |

Register blocking and local-memory staging are multiplicative: 8 rows alone
gives 124, local staging alone gives ~217, both together give 451.

Note float2 **does** help here (204 vs 124) even though `pref_float=1` and
float4 lost on the naive kernel. Once the access pattern is coalesced, a
2-wide load halves the number of B fetches; the earlier float4 experiment was
vectorizing the wrong operand in an already-broken access pattern.

## Final kernel

8 rows per work-item, 2 columns (float2), A staged through a 32-deep
`__local` tile, dim 0 = column, **local = {256, 1}**:

| N | naive (fixed) | tuned | gain |
|---:|---:|---:|---:|
| 1024 | 65.7 | **408.4** | 6.2x |
| 1408 | 78.7 | **456.7** | 5.8x |
| 1536 | 56.6 | **444.5** | 7.9x |
| 2048 | 60.2 | **428.1** | 7.1x |
| 3072 | 53.4 | 255.7 | 4.8x |

3072 falls off — the working set (A+B+C ~ 60 MB) stops fitting the cache
hierarchy well. Best operating range is roughly N = 1024..2048.

### All three GPUs

| N | old naive | TUNED | gain |
|---:|---:|---:|---:|
| 1024 | 197.2 | **1178.2** | 6.0x |
| 1408 | 234.6 | **1359.5** | 5.8x |
| 1536 | 164.4 | **1335.3** | 8.1x |
| 2048 | 180.0 | **1287.0** | 7.2x |

**The card does 1.36 TFLOP/s FP32**, 34% of its 4.0 TFLOP/s paper peak. That
is a reasonable place for a hand-written GEMM; CLBlast or
`cl_intel_subgroups` might find another 1.3-1.5x, but the big win is taken.

Temperatures during the all-three tuned run: 38 -> 40 C. The tuned kernel
finishes ~6x sooner, so it integrates *less* heat than the slow one.

### Sustained, not just burst

`gpuburn` held **455.1 / 455.2 / 455.4 / 455.5 GFLOP/s** over four
consecutive 10 s windows, GT pinned at 1150 MHz, package at its 45 W limit.
No throttling. See [THERMAL.md](THERMAL.md).

## A correctness trap worth knowing

Every GEMM check in `cl_daemon.c` fills A=1, B=1 and asserts C==n. With all
values identical that cannot detect reading the **wrong row** of A, a race on
a `__local` tile, or a transposed access. It only catches a wrong k-loop
length.

It matters here. The `__local` kernels are only correct when the local size
in **dim 1 is 1**: `As[][]` is per-workgroup but the row block is
`get_global_id(1) * R`, so a dim-1 local size > 1 puts two different row
blocks on one tile. Validated against a CPU reference with random data:

| config | GFLOP/s | max rel err | bad elements |
|---|---:|---:|---:|
| `l2r8 TK32 local={256,1}` | 273.1 | 0.00e+00 | 0 |
| `l2r8 TK64 local={256,1}` | 227.6 | 0.00e+00 | 0 |
| `l2r8 TK64 local={128,2}` | 223.3 | 1.04e+03 | **82365** |
| `l2r8 TK64 local={64,4}` | 223.6 | 9.23e+02 | **83912** |

The broken configs ran at full speed and would have passed the shipped check
cleanly. `compute/gemm_verify.c` does the real comparison — use it whenever a
kernel changes.

## In the daemon

`cl_daemon.c` gained a **`GEMMT n row0 rows`** command running the tuned
kernel. `GEMM` is unchanged, so the two can be compared live. The tuned
kernel is built as its own `cl_program`, so a build failure leaves the rest
of the daemon working (`GEMMT` just returns `ERR gemmt`).

Measured through the protocol on node 0:

```
GEMM  1408 0 469  -> gflops=78.72
GEMMT 1408 0 469  -> gflops=439.31
GEMM  1536 0 512  -> gflops=61.53
GEMMT 1536 0 512  -> gflops=446.59
GEMMT 2048 0 682  -> gflops=441.67
GEMMT  384 0 128  -> gflops=138.79   (too small to fill the GPU: 16 row blocks)
```

Sources: `compute/gemm_ladder.c` (round 1), `compute/gemm_ladder2.c`
(round 2), `compute/gemm_verify.c` (CPU-reference validation),
`compute/gemm_fast.c` (standalone tuned), `compute/gpuburn.c` (sustained),
`compute/all3_fast.sh` (three-GPU runner).
