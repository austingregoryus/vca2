<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Session log

Append-only. Newest at the bottom.

## 2026-09-20 — map, hang, recover, mailbox, Quick Sync

- Read setup guide, ChatGPT recap, KT (fan shrouds), VCASS 2.3.26 sources.
- SSH to `vca-host` and all three nodes. All `net_device_ready`. Temps ~33–48 °C.
- **Hang:** directory-walk of `plx87xx_dma` debugfs ran `flush`/`cleanup`.
  Node 0 `dma_hang`. `rmmod plx87xx` D-state. Recovered with sysrq-b host
  reboot + BlockIO boot. VMs 100/101 restarted. See GOTCHAS.md.
- Virtio-net TCP ~4.7–5.0 Gbit/s host→node, ~3.5 Gbit/s node→host, ~1.8–2.6
  Gbit/s node↔node (hairpin). Host MTU 1500 max. Guest firewall REJECT hid
  inbound tests.
- Hot-added 16 MB BlockIO `vcablk1` mailboxes. Host Python coordinator +
  guest workers. All three CPUs summed 8 MiB of uint64s in parallel,
  combined result correct, 0.37 s wall.
- ALUT dump: 32 MB segments, mostly free/lazy. Do not write BAR2.
- Media SDK `sample_encode h264` on node 0: **hw** impl, 30 frames,
  iGPU 350→1150 MHz.
- Extracted `src/vca_modules` and `src/vca_apps` from the 2.3.26 zip.
- Started `docs/` so the next session does not re-learn this the hard way.

## 2026-09-20 (continued) — ALUT IDs, MTU, OpenCL SAXPY

- Node 2 ALUT size is by design: `plx_hw.c` sets `num_ntbs = 1` for PCI
  `0x2956` and `2` for `0x2954`/`0x2955`. dmesg matches (16 MB×256 vs
  32 MB×128).
- Host virtio-net jumbo: driver `MAX_MTU 65535` but never sets
  `netdev->max_mtu`, so PVE 7 keeps 1500. `vca_eth_ifup.sh` still tries
  65535 and fails.
- Node 0 OpenCL: `libigdrcl.so` is not a loader. After `apt install
  ocl-icd-libopencl1`, `compute/cl_probe.c` ran SAXPY on
  **Intel Gen9 HD Graphics NEO, OpenCL 2.1, 8 CUs, ~12.4 GiB**.

## 2026-09-20 (continued) — all 3 GPUs, PCIe payload, RID LUT

- Installed ICD loader on nodes 1 and 2. SAXPY OK on all three P580s.
- `cl_bench` 8M floats × 20: ~9.3–9.6 GPU GFLOP/s vs ~0.7 scalar CPU
  (~14×). Clocks at 1150 MHz. Combined ~28 GFLOP/s if fed in parallel.
- MaxReadReq **128 B** on host and guest NTBs; MaxPayload 256 B guest /
  512 B host; link 8 GT/s ×8 everywhere. DevCap 2048 B unused.
- RID LUT programs host RC + DMA engine through the NTB, not node↔node P2P.
- COPY_DESC: stub only in 2.3.26 source. The “SOC kernel” zip is RPMs.

## 2026-09-20 — six-device GPU demo

- `compute/gpu_all3.py`: one host process, same OpenCL SAXPY on all three
  P580s over SSH.
- Sequential wall 1.281 s vs parallel **0.477 s (2.69×)**. Kernels ~0.035 s
  each, **28.86 GFLOP/s** summed, all checksums OK (`SIX_DEVICE_GPU_OK`).
- Wall is OpenCL setup + SSH; kernels overlapped. See `docs/SIX_DEVICES.md`.

## 2026-09-20 — tiled SAXPY + six-device overlap

- `gpu_tile.py`: one 24M-float SAXPY, 3 tiles, host-verified endpoints.
  `TILED_SAXPY_OK`, wall 0.436 s, ~28.4 kernel GFLOP/s summed.
- `sixdev.py`: 3 CPU mailbox sums + 3 GPU tiles in one process.
  `SIXDEV_OK`, wall 0.475 s (CPUs hid under GPU/OpenCL setup).

## 2026-09-20 — warm daemons + thermal

- Idle temps 32–36 °C all cores.
- `cl_daemon` on :9200. Warm parallel SAXPY wall **0.103 s** (was 0.477 s
  cold). Kernels still ~0.035 s. `WARM_SAXPY_OK`.
- 12 s 3-GPU burn: ~9.36 GFLOP/s each, wall 12.1 s. Hottest CPU core **45 °C**
  (from 36 °C). False `THERMAL_HOT` from regex matching `high = +100.0 C`;
  real peak 45. `vcactl temp` is CPU only.

## 2026-09-20 — kernel-only + tiled GEMM

- No iGPU temp node. Guests have `x86_pkg_temp`, coretemp, acpitz (~28 °C),
  i915 `gt_cur_freq_mhz`, RAPL energy. Use pkg+cores+GT freq.
- `PREP` + `KERNEL_NR`: parallel wall **0.039 s** vs kernel 0.035 s.
- Tiled GEMM 384², 128 rows×3, C=384 everywhere, wall 0.012 s, ~7.1–7.7
  GFLOP/s naive. `KERN_GEMM_OK`. Temps 33–37 °C after.

## 2026-09-20 — 1024² GEMM

- Split 341/341/342. Naive kernel **52–59 GFLOP/s**/GPU, wall 0.038 s,
  C=1024 everywhere. `GEMM1024_OK`. Combined ~169 GFLOP/s.
- Hand-tiled 16×16 local mem ×20: only 19 GFLOP/s — worse than naive on
  Gen9. Temps 34–37 → 35–39 °C.

## 2026-09-20 — GEMM scale 1536/2048 + float4

- float4 kernel at 1024: 37–43 GFLOP/s, worse than naive ~55–59.
- 1536² naive: **~9 GFLOP/s** (pathological size). 2048² naive: **31–46
  GFLOP/s**, wall 0.390 s, C=2048. `SCALE_GEMM_OK`. Temps ≤39 °C.

## 2026-09-20 — size sweep + GEMM2 pipeline

- Sweep 512–2048: **N multiple of 384 is ~9 GFLOP/s/GPU**; others 45–74.
  Peak **1408² ~199 GFLOP/s** combined. Temps ≤41 °C.
- `GEMM2` on-device A B², D=n²=1048576, ~50–61 GFLOP/s, wall 0.065 s.
  `GEMM2_OK`.

## 2026-09-20 — 384 confirm + hetero CPU/GPU

- 384 → 22 GFLOP/s combined (slow). 640 → **202**. 1408 → **205**. 400 is
  small-N noisy. Rule still: avoid k×384 at useful sizes.
- CPU blocked OpenMP GEMM **~12 GFLOP/s** ×4 threads, C=1024.
- `HETERO_OK` (GPU 256 + CPU 85 rows) but **slower than GPU-only** — do not
  take rows from the iGPU. Temps node0 40 °C.

## 2026-09-20 — pad 1536 + extra-CPU HADD

- 1536=27 GFLOP/s; pad to **1544 → 148**, 1600 → 174. Workaround: +8 cols.
- `HADD` 1408 full GPU tile + 80 CPU rows: wall 0.069 s vs GPU-only 0.076 s,
  node0 **82 GFLOP/s** on that wall, C=1408. Extra work hid under the GPU.
  Temps ≤41 °C. `PAD_HADD_OK`.

## 2026-09-20 — 384 trap is data N, not NDRange

- Pad only `global_size[1]` on 1536: still ~9 GFLOP/s. 1408 with g1=1536
  still ~174. Trap is inner-loop/leading-dim N, not the grid.
- INFO: 8 CUs, max_wg=256, pref_float=1. Temps 36–39 → **39–43 °C**.
  Node0 RAPL +113 J over the whole suite, GT 350→1150, pkg 40→42 °C.
  `GRIDPAD_OK`.

## 2026-09-20 — lda pad does not fix 1536

- B lda 1544/1600/2048 with n=1536: still ~9 GFLOP/s. 1408 with lda=1536
  stays fast-ish (141). `LDA_OK`.
- A+B strides 1600/1600: **33** GFLOP/s combined (tiny bump, not the 5×).
  Trap is **k-loop N**, not layout. Temps ≤44 °C. `LDAB_OK`.

## 2026-09-20 — k+=16 unroll

- 384: 22 → **80**. 1536: 27 → **44** (still bad). 1024 hurts (170→120).
  1600: 169 → **191**. `K16_OK`. Temps ≤41 °C. Pad N still the real fix.

## 2026-09-20 — llama.cpp RPC TinyLlama

- Host `llama-cli` + Zig glibc-2.23 `ggml-rpc-server` on all 3 nodes.
- TinyLlama 1.1B Q4_K_M: **“Paris.”** Prompt 44.6 t/s, gen **14.5 t/s**.
  `--single-turn` required (else chat TUI spin). Temps ~38 °C. RPC
  connections on all three nodes. See `docs/LLAMA.md`.

## 2026-09-20 — OLMoE 1B-7B Q4 host vs RPC

- 3.9 GiB GGUF. Host 33.4 t/s, 1-node RPC **30.2**, 3-node RPC **12.7**.
  Node0 hit **49 °C** on 1-node run. Splitting a fitting MoE made it slower.
  Experts were not pinned per CPU.

## 2026-09-20 — ALUT mbox compile; DMA hang on module bounce

- `plx_mbox.c` built into `plx87xx.ko`. `mbox_alloc`/`mbox_info` appeared.
  Did **not** write alloc. After `rmmod`/reload, BIOS timeout then
  `dma_hang`. Backup `.ko` restored. Host later pingable, SSH dead.
  Needs power cycle. See `docs/ALUT_MBOX.md`.

## 2026-09-20 — host power cycle; nodes restored

- `vca-host` up 7 min, `7.0.2-6-pve`, backup driver, no mbox debugfs.
  All 3 nodes `net_device_ready`. ubuntu-1 + signal-web-dad running.
  Max 53 °C on node 0 after boot. Experimental ALUT `.ko` not loaded.

## 2026-09-20 — ALUT mbox live: host mmap OK, guest BAR 0xff

- Clean reboot with new `plx87xx.ko`. Nodes `net_device_ready` (BIOS
  failure was from `rmmod`, not this code).
- Node0 alloc: dma `0x4c000000` peer `0x22000000` seg 17, 32 MiB.
  Host `/dev/vca_alut_*` mmap works. Guest BAR2 at peer_off is `0xffffffff`.
  Nodes stayed up. See `docs/ALUT_MBOX.md`.

## 2026-09-20 — ALUT NTB direction; node 0 ping-pong OK

- Host ALUT is **outbound**: BAR2+`0x22000000` reads guest PA
  `0x4c000000` (`0x5a5a5a5a`). Guest `alut_gmap.ko` called
  `plx_add_a_lut_entry(0x4c000000)` → guest `peer_off=0x8000000`.
  Guest BAR2 then shows host magic `ALUT`. Header ping-pong seq 42
  status 2 **PINGPONG_OK**. Guest pull ~28 MiB/s, guest push ~4.8 GiB/s
  (Python). `rmmod alut_gmap` cleaned the guest slot. Nodes still
  `net_device_ready`. Do **not** write host BAR2 at `0x22000000`.

## 2026-09-20 — ALUT all 3 nodes + host-push

- `mbox_alloc` on `07:00.0` (32 MiB dma `0x48000000`) and `0a:00.0`
  (16 MiB dma `0x47000000`). Guest LUT ping-pong seq 101/102/103
  **PINGPONG_OK** on nodes 0/1/2 (`2958`/`2959`/`295a`).
- Guest 32 MiB `dma_alloc` / order-13 pages **ENOMEM** (MAX_ORDER 4 MiB).
  4 MiB guest buffer at PA `0x45b800000`; host `alut_hmap` kprobe LUT
  `peer_off=0x29800000`. **HOSTPUSH_OK**. Host BAR2 write ~3700 MiB/s.
  Modules unloaded. Nodes still `net_device_ready`.

## 2026-09-20 — ALUT doorbell on all 3 nodes

- Doorbell **15** (not 0, not `intr_send`). Guest `/dev/vca_gdb` + host
  `alut_hdb/kick`. Header ping-pong RTT **~35 µs** (node 0: 50/50
  min 0.033 / avg 0.038 / max 0.088 ms; nodes 1–2 similar on 10 iters).
  **DOORBELL_OK**. Nodes still `net_device_ready`. ~250× vs BlockIO
  mailbox poll. Modules unloaded.

## 2026-09-20 — MaxReadReq setpci on node 0 does not stick

- Host `05:00.0` DevCtl `085e`, guest `01:00.0` `083f` (MRR 128).
  `setpci` write 512/2048 accepted then **readback still 128**.
  ALUT pull 27–28 MiB/s at 128/512/2048. iperf host→n0 4.49 / 4.70 /
  4.86 Gbit/s; n0→host 2.60 / 3.01 / 3.67 (warmup vs old ~3.5).
  Restored. Nodes `net_device_ready`. MRR is hard-wired.

## 2026-09-20 — jumbo MTU on host eth0

- `jumbo_mtu.ko` set `eth0` `max_mtu=65535` live (no VOP reload).
  DF ping 8972 at MTU 9000 **OK**. iperf3 5 s: host→n0 **14.1 Gbit/s**,
  n0→host **13.9**. At 65521: host→n0 **13.8**, n0→host **28.6**, DF
  ping 20000 OK. Restored 1500. Nodes `net_device_ready`. eth1/eth2
  still 1500.

## 2026-09-20 — jumbo left on all three host VCA NICs

- `jumbo_mtu.ko` ifnames=eth0,eth1,eth2 mtu=65521. DF ping 8000 to
  172.31.{1,2,3}.1 OK. Ko at `/root/vca2/jumbo_mtu.ko`; `vca2-start`
  loads it. `rmmod jumbo_mtu` restores 1500.

## 2026-09-20 — jumbo 65521 confirmed on all 3

- DF ping 20000 all nodes OK. iperf 5 s: n1 13.5 / 15.8 Gbit/s,
  n2 14.6 / 25.6 Gbit/s (host→node / node→host). n0 was 13.8 / 28.6.
  Operational MTU is **65521**.

## 2026-09-21 — the 384 trap was the workgroup shape

- Reviewed the whole `docs/` set against the 2.3.26 source. Hypothesis: the
  "N multiple of 384" cliff is the implicit OpenCL local work size, because
  every GEMM passes `local=NULL` with `g[0]=rows`, dim 0 is SIMD-adjacent,
  and `n % 384 == 0` is exactly when `rows = n/3` is a multiple of 128.
- **Decisive test on node 0** (`compute/wg_test.c`, pure userspace OpenCL,
  no driver/debugfs/BAR): n=1536 **rows=512 -> 9.27** GFLOP/s,
  **rows=511 -> 50.28**, rows=513 -> 45.94. Same n, same k-loop, same lda.
  Converse: n=1408 with rows=512 collapses to **11.15** (the documented
  "peak size" is not a property of the matrix).
- Forced local at n=1536 rows=512: `{1,128}` **58.24**, `{1,256}` 45.4,
  `{16,16}` 9.57, `{256,1}` 9.30. Monotonic in dim 0.
- Sweep (`compute/wg_sweep.c`): `{1,128}` matches or beats NULL at every N
  and the size dependence disappears. `SWEEP` cliff is gone.
- **All three GPUs** (`compute/all3_wg.sh`): N=384 23.5 -> **161.9**,
  N=1536 28.0 -> **174.7**, N=1408 216.5 -> **236.7** combined GFLOP/s.
  Node 2 stops being the slow tile (it just always drew the remainder row
  count, which has the most divisors). Temps 37 -> 43 C, GT 1150.
- **Two enum bugs**: `MAX_COMPUTE_UNITS` was `0x1021` = `MAX_CONSTANT_ARGS`
  (hence "8 CUs" — it is **72**), and `LOCAL_MEM_SIZE` was `0x1022` =
  `LOCAL_MEM_TYPE` (hence the suspicious `local=1` — it is **64 KB**).
  Fixed in `cl_probe.c` and `cl_daemon.c`. Real FP32 peak ~1325 GFLOP/s per
  P580, so the naive kernel is ~6% of peak.
- Patched `do_gemm()` in `cl_daemon.c` with `gemm_local()` = `{1,128}`.
  Verified through the daemon protocol on node 0: GEMM 1536 -> 58.69,
  384 -> 51.69, 1408 -> 75.00, 2048 -> 60.62, all checks OK.
- Cleanup: daemon stopped, the temporary INPUT accept for 9200 removed, all
  three nodes back to `net_device_ready` and 35-37 C idle. `plx87xx` never
  touched; `jumbo_mtu` left loaded as found.
- See [GEMM_WORKGROUP.md](GEMM_WORKGROUP.md).

## 2026-09-21 (continued) — tuned GEMM, and the real thermal envelope

Overnight session. Rule for the night: **userspace on the guests only** — no
module load/unload, no debugfs writes, no BAR mmap, nothing touching
`plx87xx`. That rules out every hang mode in GOTCHAS.

### GEMM: 200 -> 1359 combined GFLOP/s

- After the workgroup fix the naive kernel was ~79 GFLOP/s/GPU, ~6% of the
  P580's real 1325 GFLOP/s peak. The limit is arithmetic intensity: one flop
  per four bytes loaded.
- Kernel ladder (`compute/gemm_ladder.c`, `gemm_ladder2.c`) at n=1536:
  naive 9.4, c-major 56, +8 rows/work-item 124, +16 rows 135, +`__local` A
  staging 317, **+float2 columns 451**.
- Final: 8 rows/work-item x float2 columns, A in a 32-deep `__local` tile,
  dim 0 = column, **local={256,1}**. Per GPU: 1024 **408**, 1408 **457**,
  1536 **445**, 2048 **428**. N=3072 falls to 256 (working set).
- All three GPUs: 1024 **1178**, 1408 **1359.5**, 1536 **1335**, 2048 **1287**
  combined GFLOP/s. **The card does 1.36 TFLOP/s FP32**, 34% of paper peak.
  Temps during the run only went 38 -> 40 C — the fast kernel integrates less
  heat because it finishes 6x sooner.
- `gpuburn` sustained 455.1/455.2/455.4/455.5 over four 10 s windows at
  1150 MHz. **No throttling**; burst == sustained.
- Shipped as a new **`GEMMT n row0 rows`** command in `cl_daemon.c`, built as
  its own `cl_program` so a failure cannot break the rest. `GEMM` untouched.
  Live: `GEMMT 1408 0 469 -> 439.31` vs `GEMM 1408 0 469 -> 78.72`.
- **Correctness trap:** the A=1/B=1/C==n check used everywhere cannot detect a
  wrong row index, a `__local` race or a transpose. `local={128,2}` ran at
  398 GFLOP/s with **196449 wrong elements** and passed it. Caught only by
  `compute/gemm_verify.c` (random data vs CPU reference). The `__local`
  kernels are correct **only** with local dim1 = 1.

### Thermal: the CPU is the problem, not the GPU

First real power numbers (`compute/thermal_run.sh`, `burn.c`, `gpuburn.c`;
RAPL sampled every 5 s; trip enforced on the node so a dropped SSH cannot
leave load running). Full writeup in `docs/THERMAL.md`, raw CSVs in
`docs/artifacts/thermal/`.

- All three nodes, steady state: idle ~12 W card, all-GPU ~134 W / 76 C,
  GPU+CPU ~123 W / 92 C, **all-CPU AVX2 ~130 W / 92-94 C**.
- **Trip fired at 92 C (node 1) and 94 C (node 2)** in the CPU phase against
  an 88 C setpoint — the ramp is ~3 C/s, so 5 s sampling overshoots by 4-6 C.
  Loads were killed automatically; card stayed `net_device_ready` throughout.
- 39 W in four cores runs ~20 C hotter than 35 W across the iGPU at the same
  package power. Every earlier thermal note here was a GPU/LLM burst topping
  out at 49-54 C, which is why this never showed.
- **Each node's package is capped at 45 W and the cap binds.** GPU-only:
  core 3.2 W + iGPU 35.2 W. Add CPU: core 9.5 W + iGPU **25.1 W**. Adding CPU
  work does not add power, it reallocates it. That is the physical reason the
  `HETERO` experiment was slower than GPU-only, and why `HADD` only worked
  while the CPU slice was small.
- One loaded node vs three costs only **+4-6 C**, so the dominant thermal
  resistance is die-to-heatsink per node, not heatsink-to-air. Static
  pressure through the fins matters more than bulk CFM.
- Nodes 1 and 2 run 4-6 C hotter than node 0 in every phase.
- `shrouds/REFERENCE.md` updated with the measured heat load and a test
  procedure: re-run the profile with a fan fitted and compare the `cpu` peak.

### Left as found

Daemons stopped, temporary INPUT accept for 9200 removed, no load processes,
all three nodes `net_device_ready` at 38-39 C, `jumbo_mtu` still loaded,
`plx87xx` never touched.

## 2026-09-21 (continued) — the DMA engine, and it was the unlock

Goal for this pass: understand the fabric completely before building on it.
Rule: documented `dmaengine` client API only, never the `plx87xx_dma` debugfs
test files that caused the 09-20 hang.

### Source reading first (no hardware touched)

- `plx87xx_dma` is a real dmaengine device: `DMA_MEMCPY | DMA_PRIVATE`,
  `dma_async_device_register`, `device_prep_dma_memcpy = plx_dma_prep_memcpy_lock`.
- **One channel per function**, already claimed by `plx87xx` at probe, so
  `dma_request_channel()` from another module cannot work. But every VOP
  instance already shares that one channel, so sharing is the normal mode.
- Reached it without `struct plx_device`: `plx_pci.c` does
  `pci_set_drvdata(pdev, plx_dma_dev)` and that struct starts
  `{ struct pci_dev *; struct dma_device; }` — both standard types. Validated
  `d->pdev`, `dd->dev`, `c->device` before use, so a wrong guess gives
  `-EINVAL` not an oops.
- Peer addressing confirmed from `vop_common.c:922`:
  `dst = aper->pa + (remapped - aper->va)`, i.e. BAR2 phys + peer_off. 64 B
  alignment (`PLX_DMA_ALIGN_SHIFT 6`), 128 MiB max per descriptor.
- **Trap:** `prep_lock` is taken in prep and released in `tx_submit`. A
  successful prep MUST be followed by submit or the channel deadlocks.

### Results

- **Card-side engine, guest <-> host: ~3900-4000 MiB/s both directions.**
  The guest **pull** was **28 MiB/s** by CPU — a **144x** improvement. Cause
  was never tunable: CPU reads are non-posted and MRR is hard-wired at 128 B.
- Verified by content, not timing: the host buffer's magic changed from
  `HBUF` to the guest's pattern, and a later round trip came back
  `0/1024 words differ`.
- **Host-side engine is 1.7x SLOWER** (~2300 MiB/s) for the identical
  transfer. Pipelining barely helps it, so it is path-limited.
  **Rule: always initiate bulk DMA from the card side.**
- **Pipelining fixes small transfers**: 4 KiB went 886 -> **3200** MiB/s at
  queue depth 16; 4 MiB is already at the ~4 GB/s ceiling at depth 1.
- **Node to node, staged through one shared host buffer**, each node using its
  own engine: n0->n1 send 3970 / recv **4832**, n0->n2 send 3968 / recv
  **3361**. Both **VERIFIED 0/1048576 words bad** with a content-addressed
  pattern. Effective end-to-end ~2180 and ~1830 MiB/s vs ~325 MiB/s for the
  virtio hairpin — **6.7x and 8x**, CPU free.
- **Node 2 is genuinely slower on the fabric** (3361 vs 4832). Topology dump
  shows two cascaded PLX switches and node 2 is behind the second one. This
  is a real hardware fact, unlike the GEMM "node 2 is slow" artifact.

### P2P recon (read-only)

`04:11.0` and root port `00:03.2` both carry `ACSCtl: ReqRedir+ CmpltRedir+`,
so peer-to-peer TLPs are forced upstream to the root complex. True NT-to-NT
P2P would need ACS disabled plus PEX routing registers that are not in the
Intel source (PLX 8700 spec under NDA). Whole-card blast radius and a
plausible EEPROM-persistence brick mode. Not attempted, not recommended.
Root port has `DevCtl: UnsupReq-`, so a stray UR is at least unlikely to
escalate to a fatal AER event.

### State

All test modules unloaded on host and all three nodes, `plx87xx` never
touched, `jumbo_mtu` still loaded as found, all three nodes
`net_device_ready` throughout. New code in `compute/dma/`, writeup in
[DMA.md](DMA.md).

## 2026-09-21 (continued) — P2P settled, pipelined ring at 3812 MiB/s

### Node-to-node P2P: definitively NO, but the reason moved twice

Probed by pointing node 0's A-LUT window at the host-domain address of node
1's BAR2 (`0x383d1b000000`) — only an address argument to
`plx_add_a_lut_entry`, no switch registers, nothing persistent, write-only so
an unclaimed posted write is dropped rather than hitting a completion timeout.

1. **ACS was not the blocker.** The first run produced a *DMAR* fault, which
   means the TLP travelled from node 0's NT port through both switches to the
   host IOMMU. Routing was never the problem.
2. **VT-d was a blocker, and is fixable.** `05:02.3` is a phantom requester ID
   from the NTB's RID LUT with no host `struct device`, but fault reason 0x05
   means the IOMMU did resolve page tables for it. All NTBs + DMA engines
   share IOMMU group 35, so one identity map in the NTB's domain
   (`compute/dma/p2pmap.c`) fixed it: DMAR faults stopped and throughput went
   **260 -> 3945 MiB/s**.
3. **Root complex reflection is the real wall.** Even with the IOMMU happy the
   data never arrives. Controlled experiment, same target address, two
   writers: **host** engine -> 1048576/1048576 words land; **node 0** engine ->
   0/1048576. The window is provably good, the IOMMU provably permits it, node
   0 issues at full speed — the E5-2699 v3 root complex simply does not
   reflect an upstream write back down to a peer BAR. Not fixable in software.

Only remaining route would be PEX NT-to-NT routing registers: NDA, whole-card
blast radius, possible EEPROM brick. **Not attempted.** All three probe runs
were harmless — no AER errors, no hang, faults stopped cleanly at 9.

### The engines do not contend

node 0 push + node 1 pull simultaneously: 3939 + 4179 = **8118 MiB/s**. node 0
lost nothing, node 1 lost 8%. Separate PCI functions, separate NT ports.

### Pipelined ring: 325 -> 3812 MiB/s (11.7x)

`compute/dma/ring.c`: 4 x 512 KiB slots in one shared host buffer, prod/cons
counters, sender flags only after `dma_sync_wait` so the CPU-written flag
cannot overtake the DMA payload. 128 iterations, `sampled_bad=0`, last slot
fully verified.

Two bugs worth remembering:

- **Never assume a shared buffer starts zeroed.** `prod[0]` sat at control
  offset 0 where `hostbuf.c` stamps its `HBUF` magic. The receiver read
  `0x48425546`, thought slot 0 was ready, raced ahead writing `cons[]`, and
  the sender's init then zeroed them — deadlock. Fixed with a ready-flag
  handshake (clear ready, zero counters, publish ready last) and fields kept
  off offset 0. Diagnosed with `ctrlpoke.ko` + `hostpeek`, which proved
  cross-node shared memory was fine and the bug was mine.
- **The payload fill was the bottleneck, not the fabric.** First working
  version hit only 2025 MiB/s because the sender refilled 512 KiB per
  iteration (~50 us vs a ~134 us DMA). Fill once, stamp three signature words
  per iteration -> **3833**. Verification is no weaker.

### State

All modules unloaded on host and all three nodes, `plx87xx` untouched,
`jumbo_mtu` as found, all nodes `net_device_ready` throughout.

## 2026-09-21 (continued) — Ubuntu 22.04 on the card; two self-inflicted hangs

### Result: modern userspace works

`compute/image/build_node_image.sh` builds a node image with Ubuntu 22.04 on
Intel's 4.14 kernel. Booted on node 2, verified live:

- Ubuntu 22.04 LTS, glibc 2.35, **gcc 11**, systemd 249
- `net_device_ready`, eth0 172.31.3.1/24 **mtu 65521**, 15 VCA modules
- **OpenCL 3.0 NEO**, Iris Pro P580, **72 CUs** (stock: OpenCL 2.1, 2018)
- **VA-API Intel iHD 22.3.1** (stock: iHD 16.9, 2018)
- ffmpeg with `vaapi` + `qsv`; C++17 compiles natively; kernel modules build
  on the node against the installed VCA headers

Works because Intel's dracut initramfs carries every VCA module and mounts
root itself — kernel-specific but distro-independent. Keep the kernel,
initramfs and ESP verbatim; replace only the rootfs. Full writeup in
[NODE_IMAGE.md](NODE_IMAGE.md).

Findings from Intel's own build scripts and kernel patches:

- They shipped the whole image pipeline (GPL-2.0). The only gate on a newer
  distro is a hardcoded `{16.04, 16.10}` table in `library_image_creation.sh`.
- The kernel patch set is 16 patches but mostly `.config` plus 12.5k lines of
  Media Server Studio i915 work. **The real VCA delta is ~250 lines** across
  `quirks.c`, `intel-iommu.c` and `net/core/dev.c`.
- `quirk_plx_vca_alias()` **explains the phantom requester ID `05:02.3`** from
  the P2P probe: the NTB forwards traffic as "alien RIDs" across devfn slots
  0-31, functions 0-4. The host runs a stock kernel without this quirk, which
  is why the IOMMU mapping had to be added manually.
- The jumbo mystery is just Intel deleting `dev_set_mtu()`'s validation.

A newer *kernel* was assessed, not attempted. `vca_vringh` is a self-contained
fork (no upstream dependency), and `kallsyms_lookup_name` / `ioremap_nocache` /
`setup_timer` are all absent. **The gate is `vcablk`**, which uses the legacy
`blk_init_queue` request API removed in 5.0 and needs a blk-mq conversion —
and it *is* the root filesystem, so a failure leaves nothing to debug with.

Two modern-distro breakages, both handled: `systemd-udevd` sandboxes and
time-limits `RUN+=`, so the stock udev rule that applies `net_config` fires
but exits 1 (node sits at `net_device_no_ip` forever) — replaced with a
oneshot unit that logs, since no serial console is exposed. And `vca_agent.sh`
only *reports* state, it never applies `net_config`; something else must.

### Two hangs, both mine

**First: the `jumbo_mtu` bug from the day-one review.** Resetting node 2 with
the module loaded gave `unregister_netdevice: waiting for eth2 to become free`
and wedged `vcactl` in D state at `flush_work()`. `rmmod jumbo_mtu` recovered
it with no reboot. Now **fixed properly** with a netdev notifier that releases
on `NETDEV_UNREGISTER` and re-applies on `NETDEV_REGISTER` (so jumbo also
survives node reboots, which it previously did not). Verified against the
exact failure.

**Second: too many disruptive cycles.** After the 22.04 boot succeeded I kept
power-cycling node 2, closing/opening blockio and loop-mounting the live image
to chase two cosmetic issues. The host ended up pingable with TCP accepting
but SSH never completing — the signature already in GOTCHAS. Proxmox web still
answered HTTP 200 and the VM guests were fine; only host userspace touching
the VCA stack was stuck. Needed a power cycle.

The lesson, recorded in GOTCHAS: **do one image swap at a time and verify
state between every step.** The main result was already in hand; the extra
cycles bought nothing.

### State after recovery

Host back on `7.0.2-6-pve`, all three nodes booted from their original 16.04
images and `net_device_ready`, host eth0/1/2 at 65521 with the fixed
`jumbo_mtu`, temps 38-42 C, no D-state, no kernel errors. `node-jammy.vcad`
preserved alongside the untouched stock images.

## 2026-09-21 (continued) — the host wedges: found, fixed, and my fault

Three host wedges today had one cause, and the persistent journal had the
evidence the whole time.

**Root cause: rtnl self-deadlock in my own `jumbo_mtu` notifier.**
`register_netdevice()` calls the netdev notifier chain with **rtnl already
held**. rtnl is not recursive, so the `NETDEV_REGISTER` handler I added to
re-apply jumbo called `rtnl_lock()` and deadlocked against itself:

```
rtnl_lock
jumbo_netdev_event [jumbo_mtu]
notifier_call_chain
register_netdevice+0x6ff
virtnet_probe [vca_virtio_net]
vop_bh_handler [vop]
INFO: task kworker/31:1:279 is blocked on a mutex likely owned by
      task kworker/31:1:279.
```

It deadlocks inside the VOP workqueue holding rtnl forever, so everything
needing rtnl stacks up — including sshd:

```
INFO: task sshd-session:8835 is blocked on a mutex likely owned by
      task kworker/30:1:282.
```

which is precisely the "pings but SSH never completes" signature. Confirmed
identical on two separate boots (`journalctl -b -1 -k`, `-b -2`).

**Why benchmarking never triggered it:** the handler only fires when a NIC
*registers* while the module is loaded, i.e. when a node reboots. Hours of
GPU/CPU benchmarking never reboot a node.

**Fix:** `apply_dev()` takes an explicit `rtnl_held` flag and uses
`netif_set_mtu()` (rtnl-held variant) rather than `dev_set_mtu()` (acquires
rtnl). Verified against the exact scenario — rebooted node 2 with the module
loaded:

```
jumbo_mtu: releasing eth2 (device going away)
jumbo_mtu: eth2 mtu 1500 -> 65521 (max_mtu 1500 -> 65521)
```

0 `blocked for more than` lines, 0 D-state, node back to `net_device_ready`,
host healthy.

**Second, smaller bug found while verifying:** setting `max_mtu = 65535` meant
the host's `vca_eth_ifup.sh` — which FABRIC.md notes "still tries 65535 and
fails" — now *succeeded* when a link re-established, leaving host eth2 at
65535 against a guest at 65521. Silent mismatch that would break jumbo on that
link. Fixed by capping `max_mtu` at the configured MTU. All three links now
65521/65521, DF ping 20000 passes 0% loss on all three.

**Process lesson, recorded in GOTCHAS:** the host journal is persistent.
`journalctl -b -1 -k` had the stack trace after the very first wedge. Three
power cycles were spent theorising (and one wrong cause was committed to
GOTCHAS and has now been retracted) before anyone read it. Read the previous
boot's journal first.

### 16.04 benchmark baseline (before the last wedge)

All three nodes, stock image, for comparison against the 22.04 image later:

| | node0 | node1 | node2 |
|---|---:|---:|---:|
| CPU GEMM n=1024 (gcc 5.4, 8 threads) | 36.50 | 36.29 | 36.14 GFLOP/s |
| CPU GEMM n=2048 | 33.62 | 32.47 | 32.25 GFLOP/s |
| GPU GEMM n=1024 | 399.3 | 397.7 | 396.5 GFLOP/s |
| GPU GEMM n=1408 | 449.2 | 459.4 | 454.7 GFLOP/s |
| GPU GEMM n=2048 | 439.8 | 433.8 | 416.8 GFLOP/s |
| GPU sustained 30 s | 455.3 GFLOP/s, flat | | |

All three nodes identical within noise, independently reconfirming that the
old "node 2 is slow" GEMM claim was an artifact. Note the CPU number:
`compute/cpu_gemm.c` (blocked OpenMP SGEMM) reaches **36 GFLOP/s**, about 3x
the ~12 GFLOP/s recorded in SIX_DEVICES.md for the earlier CPU GEMM.

## 2026-09-21 (continued) — node 1 to 22.04, and the direction asymmetry

Node 1 moved to the jammy image so node↔node paths could be measured with both
ends modern. Nodes 1 and 2 on 22.04, node 0 left on stock 16.04 as the control.

### Two things blocked building modules on the node

- **`libelf-dev` missing.** `CONFIG_UNWINDER_ORC=y` means objtool runs on every
  object and needs libelf. A minimal debootstrap does not ship it; the stock
  16.04 image happens to. Now in `build_node_image.sh`.
- **binutils 2.38 emits `R_X86_64_PLT32`**; this 4.14.20 loader predates
  upstream `b21ebf2fb4cd` (v4.16) and rejects it as `Invalid module format` /
  `Unknown rela relocation: 4` — *after* a clean compile with an exactly
  matching vermagic. Wrote `compute/image/plt32_to_pc32.py`, installed as
  `/usr/local/sbin/plt32-to-pc32`. 27–44 relocations rewritten per module.
  With that, gcc 11 builds `alut_gmap`/`n2n`/`ring` natively and they drive
  the full DMA matrix. **Modules built this way are not second-class.**

### virtio-net: the image changed nothing, jumbo changed everything

- Same pair (node 1 ↔ node 2), MTU 1500 control: **2739–2824 Mbit/s**, which
  reproduces the historical 2.6 Gbit/s almost exactly. The old number was good.
- MTU 65521: **7954–15934** one way, **12971–25583** the other. 3x–9x, but
  *erratic* — 1500 varied under 3%, jumbo varies 2x run to run. `-P 4` did not
  stabilise it.
- Distro-independence proven by staging node 0's **xenial iperf3 binary** onto
  both jammy nodes: 16.0 / 26.3 Gbit/s versus 15.8 / 26.3 native. Identical.
  Expected — the transport is `vca_virtio_net` in the 4.14 kernel, which this
  image deliberately keeps.
- This closes the "node↔node jumbo iperf" open question from FABRIC.md.

### DMA: baseline reproduced, then a 2.1x asymmetry nobody had looked for

Ring, node 0 → node 1: **3801 MiB/s** against the recorded 3812 — on a
different distro, a different toolchain and freshly built modules. Good check
on the whole rig.

Then all six ordered pairs, which had never been done (everything before used
node 0 as sender). **Send and recv rates are a property of the node, not the
pair:**

| | send | recv |
|---|---:|---:|
| node 0 | 3847, 4003 | 3977, 3845 |
| node 1 | 2123, 2124 | **4853, 4857** |
| node 2 | **5813, 5709** | 3137, 3175 |

Pipelined ring: n0→n1 3801, n1→n2 2087, **n2→n1 4373** — a new best, 15% above
the old record and **2.1x** the reverse direction over identical hardware.
Every run `VERIFIED`, sender and receiver within 2%.

Practical rule: **route bulk node-to-node traffic node 2 → node 1.**

### What the block diagram explains (and does not)

Austin produced Intel's `VCA2_arch.jpg` mid-session. The hardware matches it
exactly: **PEX 8749 (1:3, x16 to the edge) with nodes 0 and 1 and an
unpopulated M.2 riser hanging off it, cascading over x8 into a PEX 8717 (1:1)
that carries node 2 alone.** FABRIC.md had this as a single 8717; corrected.

- **It explains node 2.** One extra store-and-forward hop from host RAM.
  Non-posted reads are latency-bound so they suffer (worst reader, 3156);
  posted writes are not so they don't (best writer, 5760).
- **It does not explain node 1.** Nodes 0 and 1 are structurally identical in
  the diagram, yet node 1 writes at half node 0's rate. Ruled out by direct
  comparison against node 2: link width, link speed, MaxPayload, MaxReadReq,
  ExtTag, RelaxOrd, NoSnoop — node 1 and node 2 match on all of them. The only
  correlate left is the **PCI function the PLX DMA engine landed on**
  (`01:00.1` on node 1, `01:00.3` on nodes 0 and 2), which the EEPROM decides.
  Node 1 exposes no second DMA function, so this cannot be tested by binding a
  different channel. Logged as open.

### Corrections made to earlier docs

- DMA.md compared DMA against "~325 MiB/s virtio". That was an **MTU-1500**
  figure compared against jumbo-era DMA. Against jumbo virtio the ring is
  ~1.4x–2.2x, not 6.7x–11.7x. The durable wins are that it costs **no CPU**
  and that it is **consistent** (±2% vs 2x).
- FABRIC.md said MaxPayload was 256 B on all three nodes. Node 0 is **512 B**.
- FABRIC.md's topology diagram showed one switch. It is two, cascaded.

### State

All modules unloaded on host and all three nodes. Guest MTUs restored to
65521 after the 1500 control. `jumbo_mtu` loaded as found. All three nodes
`net_device_ready`, host zero D-state, 37 °C throughout.

## 2026-09-21 (continued) — CPU A/B re-measured on two nodes; 2.28x was optimistic

With node 1 also on 22.04 the CPU benchmark could be run on two independent
jammy nodes instead of one. It does not fully reproduce.

| n | node 0 (16.04) | node 1 (22.04) | node 2 (22.04) |
|---:|---:|---:|---:|
| 1024 | 36.48 | 70.59 | 69.10 |
| 2048 | 32.38 | 53.75 | 55.66 |

The recorded figure was **83.04** at n=1024 for a 2.28x claim. Re-running **the
same binary on the same node** (node 2, `/root/bench/cpu_gemm`, untouched)
gave 69.10 today, and node 1 independently gave 70.59. So 83.04 was a lucky
run, not a build or flag difference.

**Corrected: ~1.9x, not 2.3x.** n=2048 held (1.65x recorded, ~1.7x now) —
the usual pattern, the smaller problem fits in cache and is noisier. Aggregate
across three nodes corrected from "99 -> 250" to **~109 -> ~210 GFLOP/s**.

The conclusion is unchanged in direction and still the largest single win from
the image: the GPU was near its ceiling, the CPU was leaving nearly half its
performance on the floor to a 2016 compiler. Propagated to NODE_IMAGE.md,
STATUS.md and FABRIC.md.

Lesson: a single benchmark run on a single node became a headline number in
three files. Two nodes agreeing is the minimum bar for one of these.

## 2026-09-21 (continued) — reproducible images, whole fleet on one build

### The drift got fixed properly, not documented around

Nodes 1 and 2 were running images that could not be reproduced from the repo:
`libelf-dev` installed with `dpkg -i`, `plt32-to-pc32` copied in by `scp`,
`iperf3` added — all live, to finish the DMA matrix. Rebuilt the builder
around that failure rather than writing it down and moving on.

`compute/image/` is now a profile-driven builder: `profiles/*.conf` for the
distro, `overlay/common/` for the VCA layer as reviewable files,
`deploy_node_image.sh`, `verify_node_image.sh`. Profiles for jammy (proven),
focal and bookworm (untested), noble (expected-broken, refuses without
`--force-support`). Debian needed no code changes, only different fields.

**Found while reading the old script:** it still embedded the *original racy*
`vca-netcfg.sh` — the one that waits for `net_config` to exist rather than for
an IP, and boots to `net_device_no_ip` roughly one time in three. The fixed
version existed only on the hand-patched nodes. Anyone running that script
would have hit the same intermittent failure again.

### DNS: a dangling symlink, not a network problem

"The nodes have no DNS" had been assumed to be routing or firewall. It was
neither. The host already masquerades `172.31.0.0/16`, both LAN resolvers
answer, nodes could ping `1.1.1.1` all along. Only *names* failed.

Ubuntu ships `/etc/resolv.conf -> ../run/systemd/resolve/stub-resolv.conf`,
and the image build disables `systemd-resolved` (nothing should race the VCA
agent for eth0), so the stub is never created. `[ -e ]` on the symlink still
passes. Builder now writes a real file; `apt install htop` works on a node.

That matters because apt failing is exactly what caused the hand-patching.

### Four of my own bugs, found by actually running it

- **usrmerge.** `/sbin` *is* `/usr/sbin`, so `ln -sf /usr/sbin/vca_eth_cfg.sh
  $ROOTFS/sbin/vca_eth_cfg.sh` pointed at itself and replaced the real script.
  First build shipped it broken and looked fine; the cached rebuild failed.
- **`[ -e ]` follows symlinks.** The self-check tested systemd `.wants`
  entries, which are absolute symlinks resolving against the *build host*, so
  correct links reported missing and a good image failed its own check.
- **`clinfo` is column-padded, not colon-separated.** `awk -F:` reported "no
  OpenCL device" on a node whose P580 was fine at 72 CUs. This was the single
  FAIL in the first verifier run — a false negative.
- **Accepted `link_down` as powered off** during deployment. It is
  transitional; the power press got eaten and node 0 sat at `power_off`
  through a whole wait loop. Now waits for a *settled* `power_off`.

### Master-and-copies, with reflink

BlockIO opens images RW, so three nodes cannot share one file — and a node
booted off the builder's output means the next build writes into a mounted
disk. The build output is now a golden master nothing boots, with a per-node
copy each; `deploy_node_image.sh` refuses to run while the master is attached.

`tank-red` has `feature@block_cloning` **active**, so the copies are
`cp --reflink=always`: instant and block-sharing. That is the one idea worth
taking from `spoto-team/vca2-control-panel`, which does it with btrfs.

### Result: all three nodes identical

All report `VCA_IMAGE_BUILT=2026-09-21T22:35:50Z`,
`VCA_IMAGE_BUILDER_GIT=cf796ef`, and **23 passed / 0 failed** on the verifier
— including compiling a kernel module, rewriting its relocations and loading
it, which is the only check that proves sufficiency.

CPU GEMM n=1024, all three now on jammy:

| | node 0 | node 1 | node 2 |
|---|---:|---:|---:|
| n=1024 | 71.00 | 71.13 | 71.50 |
| n=2048 | 54.53 | 53.80 | 53.82 |

Within 0.7% of each other. Node 0 was 36.48 on 16.04 this morning. The
three-node fleet is now a uniform test bed, which it has never been.

Host healthy, zero D-state, 37–40 °C.

### Prior art surveyed

`docs/PRIOR_ART.md` and `docs/KERNEL_PORT.md` added, both marked untested.
The find is `chainsx/intel-vca2-dkms`: Intel 2.3.26 forward-ported to Linux
6.8/6.17/7.x including blk-mq for `vcablk`, GPL-2.0 — and honest in its own
`VALIDATION.md` that it is **build-tested only**, never run on a card. It
plausibly addresses both of our kernel pins. We have the hardware; they do
not. Nothing attempted.

## 2026-09-21 (continued) — DMA matrix on a uniform fleet

Re-ran the whole thing with all three nodes on one byte-identical image, so
the only remaining variable is silicon. All 12 runs `VERIFIED`.

### The distro is provably irrelevant to the fabric

Node 0 was the only node that changed OS between rounds (16.04 → 22.04):

| | OS changed? | send before → after | recv before → after |
|---|---|---|---|
| node 0 | **yes** | 3925 → 3997 (+1.8%) | 3911 → 3949 (+1.0%) |
| node 1 | no | 2124 → 2058 (−3.1%) | 4855 → 4849 (−0.1%) |
| node 2 | no | 5761 → 5642 (−2.1%) | 3156 → 3129 (−0.9%) |

**The node whose OS changed moved less than the nodes whose OS did not.**
Expected for a hardware DMA engine on an unchanged 4.14 kernel, but now
measured on a single-variable test rather than assumed.

It also closes the last soft explanation for the node 1 anomaly. All three run
one image; the 2.7x spread in write rate is unchanged. Whatever it is, it is
below the OS.

### All six ring directions

| Path | tx | rx |
|---|---:|---:|
| node 0 → node 1 | 3745 | 3725 |
| node 1 → node 0 | 2047 | 2039 |
| node 0 → node 2 | 3161 | 3093 |
| node 2 → node 0 | 3875 | 3769 |
| node 1 → node 2 | 2029 | 2020 |
| **node 2 → node 1** | **4515** | **4423** |

New best **4423 MiB/s**, and a 2.2x spread between best and worst direction.

### The fabric is two numbers per node

Every ring result is predicted by `min(sender's write, receiver's read)`:

| Path | predicted | ring | efficiency |
|---|---:|---:|---:|
| n0 → n1 | 3997 | 3725 | 93.2% |
| n1 → n0 | 2058 | 2039 | 99.1% |
| n0 → n2 | 3129 | 3093 | 98.8% |
| n2 → n0 | 3949 | 3769 | 95.4% |
| n1 → n2 | 2058 | 2020 | 98.2% |
| n2 → n1 | 4849 | 4423 | 91.2% |

**Mean 96.0%.** Six numbers measured once predict any pair. The two worst rows
are the two most mismatched pairs, where the faster side waits longest —
which is the expected shape.

Consequence worth acting on: **the transport has nothing left to give.** Any
further node-to-node gain has to come from the per-node asymmetry (the DMA
engine's PCI function, still unexplained), not from the ring protocol. Stop
optimising the protocol.

Host clean throughout, 35–37 °C.

## 2026-09-21 (continued) — the host was never running Intel's driver

Phase 0 of the "validate the DKMS port" plan was supposed to be reading a
diff. It ended the plan instead, because the port was already installed.

### The finding

`/usr/src/intel-vca2-dkms` is on the host at `edb3e51` — the same commit as
the public repo's `main`, confirmed against the GitHub API. Packages
`daemon-vca` / `vca2-host` / `vca2-vcass-modules-dkms`, all
`2.3.26+ubuntu24.04.10`, all `Maintainer: Local VCA Maintainer`, installed
2026-09-19. 17 modules built for **each** of `7.0.2-6-pve` and
`7.0.14-17-pve`. No trace of a genuine Intel install anywhere on the host.

So **every measurement in this repository** — the 144x DMA unlock, 4423
MiB/s node-to-node, jumbo, BlockIO, today's six-direction matrix — was
produced by a community forward-port, not by Intel's modules. The repo
implied otherwise throughout. Corrected in
[HOST_STACK.md](HOST_STACK.md), STATUS, LIVE_MAP, GOTCHAS and PRIOR_ART.

Intel's stock `plx87xx` could never have run here: it *provides* a
`dma_map_ops` table and `.map_page` no longer exists as a member of that
struct on either installed kernel.

### The bigger finding: it is not reproducible

The running `plx87xx.ko` is **not** the module DKMS built.

| | srcversion | size | built |
|---|---|---:|---|
| DKMS record | `0F994D23ABC944D13CD8204` | 271,101 | Sep 19 |
| **installed** | `C7DB21AFCC365566F4BFCAA` | 4,434,168 | Sep 20 |

Different `srcversion` means different *source*. The installed module pulls
in `misc_register` / `misc_deregister` / `dma_mmap_attrs` that the DKMS build
does not — a character device exposing DMA memory via mmap, matching the
"experimental plx87xx, mbox on all three NTBs" note already in STATUS.md.
The other 16 modules match byte-for-byte.

**A `dkms autoinstall` triggered by any kernel update will silently replace
it.** Recovering that patch into source control is now the highest-value
housekeeping outstanding. It is the same unreproducible-hand-patch problem
we spent the evening fixing on the nodes, one layer down and older.

### Independent review of the port

Two agents were asked to falsify the above rather than confirm it. Both the
host facts and the code claims were checked; several of my claims did not
survive, which is the point of asking.

**Held:** the blk-mq conversion is genuine and structural (`blk_peek_request`
/ `blk_start_request` / `__blk_end_request_all` are absent from the entire
fork tree), the `kfree` → `put_device` fix is real and applied consistently
across all five buses.

**Corrected:**
- "Mutating `bi_iter.bi_sector` was a real latent bug" — **oversold.**
  `rq_for_each_segment` copies `bi_iter` at inner-loop entry, so mutation
  cannot corrupt iteration, and nothing reads the field afterwards. An API
  violation with no functional consequence here.
- "`kfree` after failed `device_register()` is a double-free" —
  **mischaracterised.** It is a leak plus a latent UAF; nothing double-frees.
- "`BLK_STS_RESOURCE` with no queue kick risks a stall" — **refuted.** The
  branch is unreachable by construction (`queue_depth` = ring capacity), and
  blk-mq re-runs the queue itself on that status anyway.
- My host-side narrative "7.0.14 tried first, fell back to 7.0.2" was
  **backwards**: 7.0.2 built first (Sep 19 14:31), 7.0.14 second (19:36).

**Found that I missed, ranked by how much they matter here:**
1. `BLOCKIO_FORCE_DMA_SYNC` was **uncommented** — a data-path switch, not a
   port. The BlockIO backend runs synchronous DMA, async path is dead code.
   Undocumented by the fork, and live on the node root filesystem now.
2. `vop_common.c:1133` — an error-recovery semantic change in the
   virtio-net-over-PCIe hot path, presented as a brace cleanup. Intel's
   original had misleading indentation and could reset a ring item on the
   `-EBUSY` timeout path.
3. `vca_mgr_extd` sysfs device rename — breaks anything matching the old
   name, at runtime only.
4. CPU-hotplug affinity disabled in `vca_virtio_net` (collides with in-tree
   `virtio_net`); affinity set once at probe, never re-applied.
5. A tag-set leak the fork introduced on the `vcablk_disk_create()` error
   path. Worth reporting upstream.

The review also found real bugs the fork fixed and never claimed: the
`req_op(req) & (REQ_OP_FLUSH | ...)` mask was effectively `& 2` (misclassified
DISCARD as sync, missed every real `REQ_SYNC`/`REQ_FUA`); passthrough requests
would have **written to sector 0**; `vca_mgr_extd_unregister_device()` was
never called at all.

### What changed in the plan

`KERNEL_PORT.md` was rewritten. Phase 1 is done and has been in production
for days. The sharp remaining question is why `7.0.14-17-pve` still fails to
bring up virtio NICs when the same modules are built for it — and the first
thing to rule out is that the local `plx87xx` patch only exists for 7.0.2,
so the comparison was never like-for-like.

## 2026-09-21 (continued) — recovered the missing plx87xx patch

The unreproducible module from the previous entry turned out to be fully
recoverable, and the thing that made it recoverable was the thing that made
it look suspicious.

It was built with `-g -gdwarf-5`, which is most of why it is 4.4 MB against
DKMS's 271 KB. Its DWARF `DW_AT_comp_dir` named the build tree —
`/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10` — which still existed, still
modified, dated Sep 20 21:21. The debug info led straight back to the source.

The patch, on top of `chainsx/intel-vca2-dkms` @ `edb3e51`:

- `plx_mbox.c` (4671 B) — a host ALUT shared-memory mailbox, **compiled into
  `plx87xx`** rather than built as its own module, which is why it changed
  that module's `srcversion` instead of showing up as a separate `.ko`. Its
  header comment: *"Allocates one A-LUT segment (32 MiB on node 0/1) of DMA
  memory so the card can see it. Does NOT touch DMA debugfs. Polling only
  (no doorbell)."*
- `plx_mbox.h` (226 B)
- `plx_debugfs.c` — adds `mbox_info` (0444) and `mbox_alloc` (0200) and calls
  `plx_mbox_remove()` on teardown. That is precisely the "`mbox_*` debugfs"
  marker the docs already used to distinguish the experimental build from the
  backup one — the two halves of the story were in the repo all along,
  unconnected.
- one line in the top-level `Kbuild`:
  `plx87xx-objs += vca/plx87xx/plx_mbox.o`

`misc_register` / `misc_deregister` / `dma_mmap_attrs` — the symbols that
first flagged the module as different — all come from this file.

Committed to `compute/plx_mbox/`, verified byte-exact against the host by
md5. **Deliberately left without SPDX headers**: `srcversion` hashes the
source, so adding even a comment changes it. Untouched, a rebuild should
reproduce `C7DB21AFCC365566F4BFCAA`, which is the proof the recovery is
complete rather than merely plausible. Headers go on after that check passes.

Also found: `/root/plx87xx.ko.bak`, 271,101 bytes, Sep 19 — the pristine
upstream build, matching the DKMS record byte-for-byte. A clean rollback
existed the whole time.

**Residual:** the installed module was built by hand, not `dkms install`, so
`dkms status` still reports a mismatch and a kernel update still reverts the
mailbox. Rebuilding through DKMS closes that. The difference is that it can
now be put back.

Incidental: Git Bash mangles leading-slash paths inside `plink` arguments
(`/usr/src/...` became `C:/Program Files/Git/usr/src/...`). Writing the script
to a file and `pscp`-ing it — the pattern used all session — avoids it.

## 2026-09-21 (continued) — recovery proven, DKMS record fixed, reboot survived

### The recovery is byte-complete, not approximately right

Rebuilt `plx87xx` from **only** the committed artifacts on a pristine copy of
upstream `chainsx/intel-vca2-dkms`:

```
rebuilt from committed files : C7DB21AFCC365566F4BFCAA
currently RUNNING            : C7DB21AFCC365566F4BFCAA   -> MATCH
dkms record (upstream only)  : 0F994D23ABC944D13CD8204
```

`srcversion` hashes the source text, so an exact match means the recovered
source is the source. That is why the files are kept byte-exact with no SPDX
header added.

First attempt failed to build: `-Werror=missing-prototypes` on
`plx_mbox_remove`, because `plx_mbox.c` does not include its own header. It
is not a defect in the recovery — `dkms.conf` builds with `KERNWARNFLAGS=`
(warnings cleared), which is how it compiled originally. Recorded in the
`compute/plx_mbox/README.md` rebuild recipe, since building it by hand
without that flag fails.

### DKMS record fixed, for both kernels

`dkms build` + `dkms install --force` for `7.0.2-6-pve` **and**
`7.0.14-17-pve`. Both now produce `C7DB21AFCC365566F4BFCAA`, and
`dkms status` no longer reports *"Differences between built and installed
modules"*. A kernel update can no longer silently revert the mailbox.

Side benefit: **7.0.14-17-pve now carries the patched module too**, which
removes the one uncontrolled variable from the "why do NICs vanish on
7.0.14" question ([KERNEL_PORT.md](KERNEL_PORT.md) Open 1). That comparison
was never like-for-like before.

Old hand-built module preserved at `/root/plx87xx.ko.handbuilt-20260921`.

### Rebooted to prove it, and found two more boot-path gaps

Host back in ~60 s. `plx87xx` loaded from the DKMS install with
`srcversion C7DB21AFCC365566F4BFCAA`, 6 `mbox_*` debugfs entries present,
zero D-state, all three nodes `net_device_ready` on the correct
`node{0,1,2}-ubuntu-jammy.vcad` images reporting `builder=cf796ef`.

Two things the boot path got wrong, both **silent**:

1. **`_boot_after_reboot.sh` and `vca2-start.sh` still pointed at the stock
   16.04 images.** Any reboot would have quietly brought all three nodes back
   on the old OS — healthy, working, and wrong. Caught before rebooting, not
   after. Now uses `node{0,1,2}-ubuntu-jammy.vcad` via an
   overridable `VCA_IMAGE_SUFFIX`, and hard-fails if an image is missing.
2. **Jumbo did not come back.** Host NICs return at MTU 1500 every boot and
   nothing reloads `jumbo_mtu`, so node↔node would have run at ~2.7 Gbit/s
   instead of ~16–26 with no warning at all. The boot script now loads it and
   verifies with a 20000-byte DF ping to each node.

Both are the same shape as the drift found earlier today: a working-looking
system that is quietly not the one you measured.

### State

Host healthy, `7.0.2-6-pve` pinned, jumbo on (eth0/1/2 at 65521, DF 20000 to
all three nodes OK), 36–40 °C, no stale modules anywhere.

## 2026-09-21 (continued) — the 7.0.14 kernel pin was never necessary

Booted `7.0.14-17-pve` with `proxmox-boot-tool kernel pin ... --next-boot`,
which boots the other kernel exactly once so any failure self-heals on the
following reboot. Good tool for this; worth remembering.

**The NICs work fine.** The documented constraint was wrong.

| | 7.0.2-6-pve | 7.0.14-17-pve |
|---|---:|---:|
| `/sys/bus/vca_virtio/devices/` | 3 | **3** |
| host NICs | eth0/1/2 | **eth0/1/2** |
| jumbo 65521, DF 20000 all nodes | OK | **OK** |
| DMA ring node2 -> node1 | 4515 / 4423 | **4581 / 4487 MiB/s** |
| modules loaded / dmesg errors | 17 / none | **17 / none** |

### The trap that produced the false claim

**`vop` devices are created when a node BOOTS, not when the modules load.**

Immediately after a host reboot the card sits at `bios_up`,
`/sys/bus/vca_virtio/devices/` is empty and there are no `eth*` NICs — on
*any* kernel. That is indistinguishable from a broken kernel unless you check
`vcactl status`. On the working kernel the `vop-dev0*` probe lines land ~35 s
after the PLX links come up, i.e. only once `vcactl boot` has run.

First diagnostic pass here showed exactly that state and it looked like a
confirmation of the old claim. What gave it away: **zero** `vop`/`vca_virtio`
lines in dmesg — not a failed probe, no probe at all — while `plx87xx`
reported "Node link established" for all three. Booting the nodes produced
all three NICs immediately.

### Honest caveat

This run also had the patched `plx87xx` on 7.0.14 for the first time (the
DKMS rebuild earlier today), where previously it had the stock build. That is
a second changed variable. It is unlikely to be the explanation — `plx_mbox`
only adds a debugfs/misc interface and is nowhere near `vop` device creation
— but it was not separately eliminated.

### Corrected

`GOTCHAS.md` (retracted with the reasoning kept), `LIVE_MAP.md` ("required"
→ "pinned, not required"), `STATUS.md`, `KERNEL_PORT.md` (Open 1 closed), and
`_boot_after_reboot.sh`, whose `KNOWN_GOOD_KERNEL` guard now accepts both and
has a `VCA_ALLOW_ANY_KERNEL=1` escape hatch for testing.

`jumbo_mtu.ko` and `hostbuf.ko` are per-kernel and were rebuilt for 7.0.14;
both work. Guest-side modules are unaffected — the node kernel did not change.

### State

Left on the `--next-boot` pin, so the **next reboot returns to 7.0.2-6-pve**
automatically. Nothing permanent changed. All three nodes `net_device_ready`,
jumbo on, no stale modules, 37 °C.

Not yet done before unpinning for good: repeat boots (the original symptom
may have been intermittent rather than imagined), and a full six-direction
matrix rather than one pair.

## 2026-09-21 (continued) — 7.0.14-17-pve adopted as the pinned kernel

Austin's call, after the test above. Validated before pinning, not after.

- **Two reboots**, the second unaided on the permanent pin — came up on
  7.0.14 with the patched `plx87xx` (`srcversion C7DB21AFCC365566F4BFCAA`).
- **Full six-direction DMA matrix**, every run `VERIFIED`, nothing slower:

  | direction | 7.0.2 rx | 7.0.14 rx | |
  |---|---:|---:|---:|
  | n0 → n1 | 3725 | **3835** | +3.0% |
  | n1 → n0 | 2039 | 2043 | +0.2% |
  | n0 → n2 | 3093 | 3092 | −0.0% |
  | n2 → n0 | 3769 | 3774 | +0.1% |
  | n1 → n2 | 2020 | **2069** | +2.4% |
  | n2 → n1 | 4423 | **4462** | +0.9% |

  Mean ~+1.1%. Read that as "no regression", not "faster" — it is within
  run-to-run spread.

- Jumbo 65521 with 20000-byte DF pings to all three nodes.
- `_boot_after_reboot.sh` end-to-end on the new kernel: guard accepts it,
  correct jammy images, all three to `net_device_ready`.

`7.0.2-6-pve` stays installed. Rollback is one `proxmox-boot-tool kernel pin`
plus a reboot.

### Per-kernel modules are the remaining papercut

`jumbo_mtu.ko` and `hostbuf.ko` build against the running kernel and are
rejected on vermagic after a switch. For `jumbo_mtu` the failure is **silent
and expensive**: the card looks completely healthy and node-to-node quietly
runs at ~2.7 Gbit/s instead of ~16–26. `_boot_after_reboot.sh` now compares
`modinfo -F vermagic` against `uname -r` and rebuilds rather than warning.

That is the third variant of the same failure today — a system that looks
right and is quietly not the one you measured. The other two were the
hand-patched nodes and the hand-patched `plx87xx`.

### State

Host on `7.0.14-17-pve` (pinned), jumbo on, all three nodes
`net_device_ready` on `builder=cf796ef` images, no stale modules on host or
nodes, zero D-state, 36–38 °C.

## 2026-09-22 — additive-capacity benchmark: the card earns its slot on transcode

Built `bench/` to answer "is this hardware worth it" as an *additive* question
rather than a head-to-head. The host has an **RTX 3070** (found while probing —
on `nouveau`, so NVENC is unavailable), so "is a 2016 accelerator faster than a
modern GPU" is not the interesting question. "With the host already busy, what
does the card add and what does it cost?" is.

### Transcode, 1080p30 H.264, 32 s fixed window

| config | aggregate fps | energy (J) | frames/J |
|---|---:|---:|---:|
| host alone (4x libx264 veryfast) | 687.6 | 5239 | 4.21 |
| card alone (3x h264_vaapi) | 438.5 | 2258 | 6.19 |
| **both** | **1132.1** | 6392 | 5.67 |

**+444 fps (+65%) for +36 W.** Marginal efficiency 12.35 frames/J against the
host's own 4.21 — **2.9x better per joule** for the work it adds. Cross-checks:
the card went 11.8 W idle -> 48 W loaded (+36 W), and the host's share under
`both` is 1132 - 438 = 694 fps against 688 alone, i.e. **no contention at all**.

Verdict: keep it for transcode. Asterisk recorded prominently — the host is
encoding in *software* only because nouveau exposes no NVENC, and the
proprietary driver would probably invert this.

### Three wrong benchmarks before the right one

Worth writing down, because each failure looked plausible:

1. **Variable-length runs.** Average watts is energy/duration, so comparing
   configs of different wall length is meaningless — the longer run dilutes
   its own average with idle time. Produced a confident "+50% throughput for
   1 W, 57x the host's efficiency". Pure artifact. Caught by noticing host
   power *fell* from 121 W to 100 W when given more work.
2. **`ffmpeg -t`.** Caps *media* duration, not wall clock; each side still ran
   at its own speed (7.0 s vs 8.2 s). Caught by the guard added after (1).
3. **`timeout -s INT`** is the fix — real wall-clock limit, SIGINT so ffmpeg
   flushes its final `frame=` line. All three configs then ran 32.0 s exactly.

`verdict.sh` now refuses to trust a margin when windows differ by >15% and
says so rather than printing a number. It caught (2) unaided, which is the
whole point of building the check.

Energy is reported alongside watts because energy is additive and
duration-independent. Both sides are RAPL package domains only — neither is
wall power, and `bench/lib/power.sh` says so at the top.

### The NVLink ambition, closed

Revisited because it was the original hope. Already answered in LLAMA.md:
host 33.4 t/s, one node 30.2, three nodes **12.7**. llama.cpp RPC splits
*layers*, so at batch 1 every token traverses the pipeline serially — more
nodes is structurally worse.

**Not a fabric limitation.** The 4462 MiB/s CPU-free ring would not move it;
the problem is serialization. Tensor parallelism *would* suit the fabric well
(activations ~8 KB/token/layer, latency-bound, ~35 µs doorbell implies a
~450 tok/s ceiling) but llama.cpp has no tensor-parallel RPC backend. That is
writing a distributed inference engine, not configuring one.

One model at low latency: no, permanently. Many concurrent requests: yes,
three independent workers, ~3x aggregate.

## 2026-09-22 — cooling upgrade: -15 to -18 C, and the constraint moved

Austin fitted active cooling. Re-ran `compute/thermal_run.sh` on all three
nodes against the stock-airflow baseline.

| phase | baseline peak | after | delta |
|---|---:|---:|---:|
| idle | ~40 °C | 35-37 °C | −4 |
| gpu (all 3) | 76 °C | **59-60 °C** | **−16** |
| gpu+cpu | 92 °C | **79-80 °C** | **−12** |
| **cpu, AVX2 (all 3)** | **92-94 °C** | **75-79 °C** | **−15 to −18** |

Headroom against Intel's 96 °C limit: **2-4 °C → 17-21 °C**.

Package power for identical work fell ~130 W → **121.4 W**. Not an artifact —
cooler silicon leaks less, so ~7% of the old draw was static power burned
because it was hot.

### The binding constraint is now the 45 W cap, not temperature

| | |
|---|---|
| RAPL, every node | long_term 45 W / 28 s, short_term 56 W, `max_power` **45 W** |
| cpu phase | 39.2 / 40.3 / 41.9 W — **under** the cap |
| gpu+cpu | 43.5 / 44.0 / 44.6 W — **at** the cap |
| GT clock, gpu phase | 1150 MHz, min = avg = max, **no throttling** |

That inverts THERMAL.md's original headline. Raising the cap is now
*thermally* plausible, but `constraint_0_max_power_uw` advertises 45 W as the
maximum and the card's VRMs were designed around it — sustained operation
above rated package power degrades silicon and regulators over months, and no
temperature reading tells you the VRMs are happy. Recorded as an option with
risks stated, **not** a recommendation. The payoff is small anyway: the CPU
case already runs below the cap.

### Two methodology errors on the way, both mine

1. **Staggered launch.** The first run started nodes sequentially over SSH and
   node 0 *completed* before node 2 began — so its 76 °C peak was measured
   while the others were idle. That is not the all-three-loaded condition the
   baseline used. Fixed with a synchronized epoch start; the corrected run has
   all three at identical row counts in the same phase.
2. **Walked into my own documented gotcha, twice.** GOTCHAS says: *"When
   parsing `vcactl temp`, do not regex `+NN.0 C` — that also hits
   `high = +100.0 C`. Match `Core` lines only."* I did exactly that and got a
   terrifying "hottest core: 100 C". Matching `^Core` was not enough either,
   because `grep -oE` returns every match on the line. The correct parse is
   the third whitespace field of `^Core` lines:
   `awk '/^Core/ {t=$3; gsub(/[+]/,"",t); sub(/[^0-9.].*$/,"",t); print t}'`
   Added to GOTCHAS with the working one-liner, since "match Core lines only"
   was evidently not specific enough guidance to stop me.

### State

All three nodes `net_device_ready` on 7.0.14-17-pve, jumbo on, idle 35-38 °C,
no stale modules.

## 2026-09-22 — corrected: the power cap is not locked, and not the limit either

Austin pushed back on the power-cap caution, asking whether the card is not
rated for 250 W. He was right to, and the caution was wrong in its specifics.

Our own docs already had the card figure: **235 W card TDP** from Intel's
guide, against ~135-180 W measured for compute loads. There is card-level
headroom.

Checked the MSRs directly rather than inferring from a sysfs field:

```
MSR 0x610 (PKG_POWER_LIMIT)  PL1 = 45.00 W  PL2 = 56.25 W  LOCK bit 63 = 0
MSR 0x614 (PKG_POWER_INFO)   TDP = 45.00 W  MIN = 0 W  MAX = 0 W
sysfs constraint_0_power_limit_uw: no-op write ACCEPTED
```

So the earlier claim that `constraint_0_max_power_uw` reporting 45 W meant
"the interface advertises no headroom" was **wrong**. That field is the RAPL
driver echoing TDP; `PKG_POWER_INFO` reports MAX = 0, meaning the package
declares no maximum at all. The lock bit is clear, so PL1 is changeable at
runtime and reverts on reset.

### The conclusion survives, for a better reason

Measured on one node under sustained AVX2:

| | |
|---|---|
| core clock | **3400 MHz** (base 3.0, single-core turbo 3.7) |
| package power | **39.0 W** of 45 W — 6 W unused |
| hottest core | **62 °C** of 96 — 34 °C unused |

**The CPU is neither power- nor thermally limited.** It sits at its all-core
turbo multiplier, which lives in the firmware turbo table and is not a RAPL
setting. Raising PL1 cannot move it.

Only **GPU+CPU together** is cap-bound (43.5-44.6 W against 45 W) — the
`HETERO` case SIX_DEVICES found unproductive, measured back when the CPU was
1.9x slower and 15 °C hotter. If PL1 is ever raised, the sane envelope is
~56 W (the board's existing PL2, which the VRMs already support, rather than
an invented number) and it belongs with a HETERO re-run so there is a reason
for the watts.

Lesson: I reasoned about a hardware limit from a sysfs field instead of
reading the MSR that defines it, and was confidently wrong in both directions
— first that it was capped and risky, when it is neither capped nor where the
actual limit lies.

## 2026-09-22 — HETERO re-test: it is a win now, and the cap was never the limit

Ran the experiment the cooling upgrade made worth asking about. Sustained 40 s
loads on all three nodes, `gemm_fast` (iGPU) + `cpu_gemm` (AVX2).

### 1. Heterogeneous is now a win

| config | GPU | CPU | combined | power | peak |
|---|---:|---:|---:|---:|---:|
| GPU only | 1250.6 | — | 1250.6 | 57.7 W | 53 °C |
| CPU only | — | 205.1 | 205.1 | 135.1 W | 69 °C |
| **both** | 1240.5 | 102.6 | **1343.1** | 127.8 W | 70 °C |

**+92 GFLOP/s (+7.4%) over GPU-alone**, and the GPU gives up only 0.8%. That
reverses SIX_DEVICES' `HETERO` conclusion, which was measured when the CPU ran
at ~36 GFLOP/s on 16.04.

### 2. Raising PL1 45 -> 56 W did nothing

| config | 45 W | 56 W | delta |
|---|---:|---:|---:|
| GPU only | 1250.6 | 1249.9 | −0.1% |
| CPU only | 205.1 | 205.4 | +0.1% |
| both | 1343.1 | 1344.8 | +0.1% |

All noise. `both` draws **42.6 W/node against a 45 W cap — it never reaches
it.** Raising a limit nothing is hitting changes nothing. PL1 reverted to 45 W
and confirmed at the MSR, not just sysfs.

### 3. The contention is cache, not power

If not power, what costs the CPU half its throughput? Working-set test — a
bandwidth story predicts the cache-resident case is largely immune:

| CPU working set | alone | with iGPU | loss |
|---|---:|---:|---:|
| n=512 → 3 MiB (fits the 8 MiB LLC) | 48.86 | 11.20 | **−77%** |
| n=1024 → 12 MiB (exceeds it) | 72.19 | 33.62 | −53% |

The cache-resident case degrades **worse** — the opposite of bandwidth
contention. The iGPU evicts the CPU's working set from the shared eDRAM/LLC.

**This corrects a claim carried in THERMAL.md and README**: "CPU and GPU
compete for the same 45 W" holds only for a *pure-ALU* load. `burn` has no
memory traffic and does pin the package at 43.5-44.6 W; anything touching
memory hits the cache wall first and never reaches the cap. Plan the split as
a **cache budget**, not a power budget.

### Harness bug worth recording

`hetero.sh` ran `trap - EXIT` on the success path, which removed the revert
handler *before* it could fire — so a clean run left PL1 at 56 W on all three
nodes. Caught only because the next probe printed the PL1 values. Fixed in
`compute/hetero/hetero_pl1.sh`: revert explicitly, *then* disarm. A cleanup
trap that a successful run disables is not a cleanup trap.

Also: the first attempt measured bursts, not sustained load — a single
`gemm_fast` run finishes in 2.56 ms, so the window was almost entirely SSH
latency and nothing was near any limit. Sustained loops were needed before
any of this meant anything.

## 2026-09-22 — NVENC comparison attempted, blocked by driver/kernel incompatibility

Tried to settle the biggest asterisk on the transcode result: the host encodes
in software only because its RTX 3070 is on `nouveau`.

**Blocked.** Debian trixie ships NVIDIA 550.163.01; it will not build against
kernel 7.0.14-17-pve:

```
nvidia-uvm/uvm_ats_sva.c:313: error: 'IOMMU_DEV_FEAT_SVA' undeclared
nvidia-uvm/uvm_ats_sva.c:328: implicit declaration of 'iommu_dev_disable_feature'
```

Linux reworked the IOMMU SVA API and removed those symbols. Driver 550
predates it. Hard version incompatibility, not a misconfiguration.

Checked before touching anything: no VM had the GPU passed through (no
`hostpci`/`vga`, `vfio-pci` not bound), nothing was using nouveau, host is
headless. So the attempt could not break a guest.

**Reverted completely:** all `nvidia-*` purged, DKMS entry removed,
`debian.sources` restored from backup so `non-free` is gone again, package
count back to its starting 1225, nouveau still bound, VCA stack untouched
(both DKMS entries, all three nodes `net_device_ready`).

Recorded in BENCHMARK.md with what would be needed — a 5xx/6xx-era driver,
not in trixie. Explicitly rejected: pinning the host back to an older kernel
to suit the driver, which would trade a working validated 7.0.14 setup for one
benchmark row.

**Does not change the verdict.** The card still adds +65% transcode throughput
for +36 W at 2.9x the host's per-frame energy efficiency. What NVENC would
change is the *alternative* — whether the 3070 could absorb that work better —
and that stays unknown. The transcode result is measured against the host as
it actually is, which is honest, but not against the host as it could be.

Lesson on scope: I stopped after the dependency failure and the build failure
rather than escalating to NVIDIA's .run installer. That would have meant a
much larger footprint on someone else's hypervisor, re-run on every kernel
update, for a single benchmark row.

## 2026-09-22 — concurrent LLM serving measured: the card wins outright

The last unmeasured assertion in the repo. BENCHMARK.md claimed the card wins
at "many concurrent requests -- three independent workers, roughly 3x
aggregate" while only ever having measured the opposite case. Now measured.

Built llama.cpp natively on the nodes (gcc 11, glibc 2.35 -- the host binary
is glibc 2.41 so it cannot be reused), same source commit `ce8caa6` on both
sides. TinyLlama-1.1B Q4, 60 s window, 128 tokens/request with `--ignore-eos`
so token accounting is exact.

| config | aggregate tok/s | energy (J) | tok/J |
|---|---:|---:|---:|
| host alone (E5-2699 v3, 18c/36t) | 82.9 | 10068 | 0.53 |
| **card alone** (3x E3-1585L v5) | **88.4** | 6964 | **0.81** |
| **both** | **172.5** | 14804 | 0.74 |

**The card beats the host outright** -- 88.4 vs 82.9 tok/s, on less energy
(1.5x the tokens per joule). Not an additive-capacity argument; a straight
win. Together: **+108%** for +47% energy, marginal 1.21 tok/J against the
host's 0.53 (**2.28x**).

### Independent workers scale 2.96x

| workers | tok/s | scaling |
|---|---:|---:|
| 1 node | 29.7 | 1.00x |
| 2 nodes | 58.6 | 1.97x |
| 3 nodes | 88.0 | **2.96x** |

### The contrast is the finding

Same hardware, same fabric, opposite outcomes:

- **RPC layer-split**, one model across 3 nodes: **2.6x SLOWER** than one node
- **Independent workers**, one model per node: **2.96x FASTER**

Layer-splitting makes every token traverse the pipeline serially. Independent
workers share nothing. The way to lose with this card is to try to make three
computers behave like one -- which is exactly what the original NVLink-style
ambition would have required.

LLAMA.md now carries a banner saying so, because on its own it reads as "the
card is bad at LLMs" when it actually measures the one case the card cannot
win.

### Fixed while here

`verdict.sh` hardcoded "frames/J" in its output, so every non-transcode
workload was mislabelled. Now uses the workload's own unit, and the NVENC
caveat only prints for the transcode workload. Also added an explicit callout
when the card beats the host alone -- which had no code path before because
it had not happened until now.

### Caveats recorded

TinyLlama is small enough to fit comfortably in 15 GB, so this measures
scheduling and compute rather than memory pressure; OLMoE-1B-7B Q4 per node
is the obvious next test. No continuous batching -- each worker is a separate
`llama-cli` at batch size 1, and a real server would likely improve both
sides. The host is a 2014 Haswell-EP part.

## 2026-09-22 — MoE on the card: 91 tok/s aggregate, and a sizing formula

Austin asked whether a more modern MoE could run given 16 GB per node.
Measured the two numbers that decide it, then validated a formula.

Per node: **13.8 GiB usable**, **27.5 GB/s** raw read bandwidth, **~19 GB/s**
effective in llama.cpp (72% of raw, normal).

MoE only reads its *active* parameters, so
`tok/s ≈ 19 / (active_params × bytes_per_param)`.

### Validated on OLMoE-1B-7B Q4 (7B total, 1B active)

| | per node | aggregate | scaling |
|---|---:|---:|---:|
| measured | 30.4 | **91.3 tok/s** | **2.83x** |
| formula | 31.7 | 95.0 | — |

4% error. And **7.1x the RPC layer-split number for the same model** (91.3 vs
12.7 t/s) — the serving architecture, not the model, decided that.

### Sizing other models

| model | active | file | fits? | x3 nodes |
|---|---:|---:|:---:|---:|
| OLMoE-1B-7B Q4 | 1.0B | 4.2 GB | yes | 95 (measured 91) |
| DeepSeek-V2-Lite Q4 | 2.4B | 10.4 GB | yes | 37 |
| Qwen3-30B-A3B IQ3_XXS | 3.0B | 12.2 GB | yes | 47 |
| gpt-oss-20b MXFP4 | 3.6B | 12.8 GB | tight | 26 |
| Qwen3-30B-A3B Q4_K_M | 3.0B | 18.6 GB | no | — |

**RAM is the binding constraint, not compute.** All of these would run
acceptably; most do not fit. Predictions only — nothing but OLMoE downloaded.

### Harness artifact, caught

First attempt showed 17.9 tok/s/node and apparently poor scaling. Artifact:
the loop launches a fresh `llama-cli` per request, so each of 7 requests
re-loaded 4.2 GB. TinyLlama (668 MB) barely noticed; OLMoE was dominated by
it. Measuring one long generation per node (`-n 512`), where llama-cli's
reported Generation rate excludes load, gives the steady-state a real server
would see.

**Process-per-request benchmarking silently penalises large models.** If a
bigger model appears to scale worse, suspect the harness first. I nearly
recorded 1.67x scaling as a hardware property.

## 2026-09-22 — gemma-4-E4B measured: 21.8 tok/s aggregate, and the fit argument

Austin found `unsloth/gemma-4-E4B-it-GGUF`, which turned out to be a better
candidate than anything my own search surfaced.

Measured, three independent workers:

| | per node | aggregate | scaling |
|---|---:|---:|---:|
| gemma-4-E4B-it Q4_K_M (4.98 GB) | 7.3 | **21.8 tok/s** | **2.98x** |

`google/gemma-4-E4B-it` is official, instruct-tuned, multimodal, 4.4M
downloads. Config is gated so the architecture could not be read remotely --
measured instead, which was better anyway.

**"E4B" = effective params, not MoE.** It reads **2.61 GB/token, 53% of the
file**. A truly dense 4.98 GB model would read all of it and manage 3.8
tok/s; this gets 7.3. So MatFormer + per-layer embeddings genuinely halves
traffic, but nowhere near an MoE ratio -- OLMoE reads 0.6 GB/token and is 4x
faster despite being a larger model.

### It beats gpt-oss-20b on the argument that matters

Similar throughput (21.8 measured vs ~25 predicted) at a quarter the memory:
4.98 GB vs 12.11 GB, leaving 9.9 GB free per node instead of 1.3. That
headroom is the whole point -- gpt-oss-20b caps context hard, and **Q8_0
gemma-4-E4B (8.19 GB) also fits**, offering near-lossless quality at an
estimated ~13 tok/s aggregate. No other candidate can trade that way.

### Search corrections along the way

- My four guesses at a `Qwen3-15B-A2B` repo name all 404'd. HF returns **401**
  for repos that do not exist, and I wrote into a script that uniform 401s
  meant "gated, needs a token". The control repos returned 200, disproving
  it. The model does exist -- as `TroyDoesAI/Qwen3-15B-A2B-Base`, a community
  prune with no instruct variant.
- I had a **units error**, comparing GB file sizes against a GiB budget, which
  made everything look tighter than it is. Correct budget is **14.87 GB**
  usable (14181 MiB), ~13.4 GB for the model after runtime and a modest KV.
- Verified rather than estimated: Qwen3-30B-A3B Q4_K_M is **18.56 GB** (does
  not fit), gpt-oss-20b quantizes almost flat (Q2_K 11.47, Q4_0 11.50, native
  MXFP4 12.11) because the expert weights are already MXFP4 -- requantizing
  it saves ~5% and costs quality for nothing.

### The ranking rule for this card

Throughput is decided by **bytes read per token = active params x bytes per
param**, then by whether it fits in 14.87 GB. Total parameter count barely
matters otherwise: a 7B MoE with 1B active is 4x faster here than a
4B-effective model.

## 2026-09-22 — embeddings measured, and a recommendation retracted

Persistent-server measurements, 256 docs in batches of 32:

| model | file | docs/s/node | x3 | scaling |
|---|---:|---:|---:|---:|
| Qwen3-Embedding-0.6B Q8_0 | 0.64 GB | **6.90** | **20.18** | 2.92x |
| Qwen3-Embedding-8B Q8_0 | 8.05 GB | 0.54 | 1.59 | 2.94x |

### Retraction

I recommended the 8B embedder on the reasoning that most deployments are
VRAM-limited to 300M-600M models and this card has 14.87 GB per node. The
measurement says that is wrong: **12.8x slower for a 13.3x parameter
increase**, i.e. 17.5 hours to embed 100k documents against 1.4 hours for
the 0.6B.

### Why — decode and prefill scale oppositely

- **Decode** reads each weight once per token: bandwidth-bound, speed tracks
  *file size*, quantisation helps a lot (gemma-4: Q3 9.2 / Q4 7.3 / Q8 4.9).
- **Prefill / embedding** does full matmuls against every weight for every
  token: compute-bound, speed tracks *parameter count*, quantisation barely
  helps.

Applying the decode rule (which this project established first) to embeddings
gives exactly the wrong model choice. Added to GOTCHAS.

### Also corrected

I attributed the 8B's earlier 0.45 docs/s to the process-per-request load
artifact. The persistent server gives 0.54 — the number was essentially
right. The artifact inflated the *scaling* figure to an impossible 3.20x, but
it did not meaningfully depress throughput for that model. The artifact was
real for the 0.6B (5.67 -> 6.90, 2.47x -> 2.92x); I over-applied it to the 8B.

Use the 0.6B or 4B embedder. The RAM headroom buys context and batch size,
not a usefully-runnable large embedder. A reranker (0.64 GB) still fits
alongside, so a full retrieval stack per node works fine.

## 2026-09-22 — smem: a local Supermemory on the card

Built a full memory layer (`memory/`) served entirely by the three nodes:
container tags, chunk + fact extraction, temporal truth, soft-delete
forgetting, recency decay, cross-encoder rerank, CLI, and an HTTP API shaped
after Supermemory's routes. Ingested this repo's own `docs/` as the corpus.
Numbers in [../memory/RESULTS.md](../memory/RESULTS.md).

**Ingest:** 22 docs / 296,040 chars / 105,729 tokens -> 448 chunks, 748
memories, **71.9 min**. Node dispatch 145/145/144. Prefill-bound: an
extraction call is ~760 tokens in for ~130 out, so ~33 s of each ~43 s call is
prompt processing.

**Retrieval (15 labelled questions):**

| configuration | recall@8 | latency | context |
|---|---|---|---|
| memories + chunks, rerank 40 | 15/15 | 16.3 s | 1,232 tok |
| memories + chunks, no rerank | 13/15 | **0.18 s** | 417 tok |
| chunks only, rerank 40 | 15/15 | 28.0 s | 2,845 tok |
| memories only, k=15 | 13/15 | 10.9 s | 661 tok |

The extracted facts earn their cost: same 100% recall as chunks alone, at
**2.3x fewer context tokens and 1.7x faster** — shorter prompt *and* less text
for the cross-encoder to score. 105,729 -> 1,232 tokens is a **98.83%
reduction**. End-to-end answers 12/15, median 55.7 s; all three misses audited
in RESULTS.md, and one of them is a wrong test label (4581/4487 MiB/s really
is faster than the 4423 the label expected).

### Three failures that returned HTTP 200

- **gemma-4-E4B is a reasoning model.** It fills `reasoning_content` before
  `content`, so every prompt long enough to need thought hit
  `finish_reason=length` with `content: ""`. A complete 22-document ingest ran
  green with **zero facts extracted**. Fix is
  `chat_template_kwargs {enable_thinking: false}`; `reasoning_budget` and
  `thinking_budget` are ignored by this build. **Corollary: gemma-4's 21.8
  tok/s in BENCHMARK.md was largely reasoning tokens.**
- **A free-text one-word classifier returns `""`** for precisely the cases it
  should answer DUPLICATE, which fell through to the default and stored the
  duplicate. A GBNF grammar makes the wrong answer unrepresentable.
- **`MemoryMax=` is silently ignored on cgroup v1.** Kernel 4.14 means v1,
  where the property is `MemoryLimit=`. systemd 249 accepts `MemoryMax=`,
  reports it back when queried, and writes `unlimited`.

### Temporal truth was eating facts

The classifier scored 12/12 on synthetic pairs while the real corpus lost
measurements. **50 of 73 supersede decisions were wrong** — collapsing
"MTU 1500 -> 2810 Mbit/s" into "MTU 65521 -> 15934 Mbit/s", "62 °C on one
node" into "92–94 °C on all three", "GEMM N=1408" into "N=1536". Both members
of each pair are true; none is a correction.

One rule fixes it: **can A and B both be true at once? then never UPDATE.**
The labelled set now carries the real failures rather than only the cases I
thought of first — 1/10 before, 8/10 after, 19/22 overall.
`reconcile_repair.py` re-judged the store in ~70 short calls (instead of a
72-minute re-ingest) and restored 48 facts.

### Memory sizing cannot be reasoned from model size

Three GGUFs totalling 6.26 GB needed **17.5 GB on a 15.4 GB node** and the
kernel OOM-killed the LLM on all three at once, mid-benchmark. RSS is
dominated by the compute buffer, which scales with the **micro-batch** (`-ub`),
not the model: `-ub 2048 -> 768` took two 0.64 GB models from ~5 GB each to
0.90 GB. It survived the 72-minute ingest and died later because the buffers
had not yet grown into their worst case.

`--reranking` in this llama.cpp build **leaks**: 0.29 GB anonymous for the
embedder against 7.4 GB for the reranker, same model, same flags. Worked
around, not understood — `MemoryLimit=3G` on the leaker so systemd restarts a
0.64 GB model in ~10 s instead of the kernel picking the 5 GB LLM,
`Restart=always`, and pool retries with backoff because all three nodes hit
the cap within seconds of each other. The final eval completed with the
reranker restarting twice per node and identical numbers.

Not implemented, and said so rather than stubbed: profile buckets, knowledge
graph. Vector search is a brute-force numpy scan.

## 2026-09-22 (afternoon) — extraction bake-off settled, ingest queue

**Bake-off verdict: stay on gemma-4-E4B.** Added *coverage* (fraction of the
source's 153 distinct numbers that appear in some extracted fact): gemma-4
44.4%, OLMoE 26.1%, Llama-3.2-1B 11.8%, MiniCPM5-2B 9.8%. Three prompt
variants trying to raise MiniCPM5's fact count made it produce fewer (29 -> 23
-> 9). A 3.2x faster ingest that captures a fifth of the content is not a
trade worth making. See ../memory/BAKEOFF.md.

**The ingest was a third idle.** Rebuilt the 72-minute ingest from the three
nodes' journals (`memory/timeline.py`): all three were working only 47% of the
time; the 145 node-minutes of work was 48 minutes if spread evenly. Causes were
scheduling, not the model: per-document batching, round-robin dispatch sending
requests to busy nodes, and per-document embedding. A shared longest-first
queue with least-in-flight dispatch took the same work (143.7 node-min) from
72.2 to **59.8 min**, with all three nodes busy 94.2% of the extraction phase
(46.0 min vs 45.1 ideal). Remaining gap: reconcile (9.7 min, serial) and
embedding (4.1 min).

## 2026-09-22 (evening) — console, MCP, reboot survival

Added the Supermemory product surface to smem: a web console at
`http://vca-host:8900/` (overview with live node telemetry, memories with
version history, documents, search/ask, a memory graph, activity) and an MCP
server (`memory/mcp_smem.py`) registered with Claude Code at user scope.

Made it survive reboots: `vca2-card.service` boots the nodes at host start and
powers them off cleanly at stop; the model servers are now enabled units on
each node's disk; `smem-api.service` runs the API. Verified with a real host
reboot — card up ~80 s after the host, model servers 8 s after each node, and
the previous boot's journal shows the clean node poweroff.

`vcactl temp` had been failing with `LBP_IRQ_TIMEOUT` for hours while the
nodes were otherwise healthy; the reboot cleared it. The boot script no longer
treats that failure as fatal, and the console reads temperatures from each
node's coretemp over ssh either way.


## 2026-09-23 (overnight) — smem v0.3: console redesign, dreaming, profiles

Rebuilt the smem console to track Supermemory's more closely, around the card
itself: a left rail with a space switcher and a live substrate strip (per-node
package temperature, memory, worker state). Views: Home (static/dynamic
profile, counts, type mix), Memories (type/status/document filters, relations,
version timeline, update/forget/restore), Documents (live processing queue;
add by text, public URL or file; hard delete), Memory graph (legend with
counts, extends and similar-document edges, highlight), Chat with inline
citations, Activity (with dreaming pause), Connect. Checked in a browser at
1440 px and 390 px, dark and light.

Backend: ingest is now a background job (`POST /v3/documents` → 202 + job
id); URL ingest sits behind an SSRF guard (verified: refuses the Proxmox UI
and localhost). When the card is idle the worker "dreams":

- **Typing**: all 854 live memories typed in ~40 min of idle time — 12/12 on
  the labelled set. On the corpus, about 4 in 5 PREFERENCE labels are
  defensible (rules and decisions count); the rest are facts or events.
- **Extends**: 849 memories checked in ~30 min, **138 extends links**. A
  random audit of 20: 11 genuine extends, 6 near-duplicate restatements
  from different documents linked as extends (the ingest-time dedup only
  sees the top-1 neighbour), 3 weak same-topic links, **0 links between
  different subjects**.

`GET /v4/profile` builds static (preferences, then facts, by degree) and
dynamic (episodes and updates, 14 days) profiles; MCP gained `get_profile`
(tested end to end over stdio). `PATCH /v3/memories/{id}` stores a new
version (409 on a non-current one).

Not done: derives (inferred facts), profile buckets, connectors, per-key API
keys. Jobs are in memory and a restart forgets the queue.

## 2026-09-24 — search latency: a leaking embedder and a slow default

An agent wiring smem into Hermes (on .59) measured search at 8-9 s against
the documented "0.1-1 s". Two separate causes:

- **The HTTP API reranked by default.** `POST /v4/search` ran the
  cross-encoder unless told not to: 5-17 s on these CPUs. The MCP tool
  already defaulted to vector-only, so only direct API callers paid it.
  Reranking is now opt-in everywhere (`rerank: true`).
- **Node 1's embedder had leaked to its 3 GB cap.** At the cap it is not
  OOM-killed; it sits there reclaiming (cgroup failcnt 478k) and a 5-token
  query embed took 1.5 s instead of 0.03 s, so ~1 search in 10 was slow.
  All three embedders had grown from ~0.3 GB to 2.7-3.0 GB in ~1.8 days.
  Restarted one node at a time; each embedder is now recycled daily
  (`RuntimeMaxSec`, 24 h / 24 h 20 m / 24 h 40 m, so two are always up).

Search through the MCP bridge after the fix, 30 calls: median 0.15 s,
max 0.21 s (was median 0.08 s, p90 1.47 s, max 1.99 s). The agent
instructions at /connect/ now give measured numbers and say when rerank is
worth its cost.

**Follow-up, same day: "search returns a constant".** The agent then saw six
unrelated queries return the same memory at the same score (0.6682 in
`hermes`, 0.6422 in `vca2-docs`) and suspected the embedder restarts. The
embedders were fine (distinct vectors on all three nodes, with and without
the query prefix, cached or not). The agent was calling `POST /v4/search`
with `{"query": ...}`; the field is `q`, and the API silently searched for
an empty string -- the embedding of the instruction prefix alone, hence one
fixed vector and one fixed answer. Reproduced to the same four decimals.
`query` is now accepted as an alias, and a missing or blank query is a 400
instead of a plausible-looking wrong answer. The MCP tools were never
affected (they always sent `q`). My own latency check after the embedder fix
timed 30 searches without looking at the results -- it could not have caught
a wrong answer; a speed test on a retrieval path needs a correctness check
next to it.

## 2026-09-26 → 10-09 — second card, render farm, node 3, embedder (backfilled 2026-10-13 from commits)

This entry was written after the fact. (The hashes in parentheses are from
the private development history; the public repository starts from a single
snapshot.) The work below was committed between
the 09-24 and 10-11 entries but never logged, so it is reconstructed from the
commit history and the docs those commits touched (FABRIC, THERMAL, GOTCHAS,
`farm/README.md`, the Remotion bench, `memory/EMBED_BAKEOFF.md`,
`monitor/README.md`). Numbers are the ones those sources give; exact times of
day were mostly not logged. Nodes are numbered 0-5 globally (node n = card
n//3 cpu n%3, IP `172.31.(n+1).1`); a few commit messages and the vcamon and
bake-off docs use the IP-octet numbering (1-6), translated here.

**2026-09-26 -- second card**

- Second VCA 2 card installed: nodes 3-5 at `172.31.4-6.1`, on the jammy
  image. `deploy_node_image.sh` takes `--card`; the nodes are reflink copies
  of the same golden master, each 23/0 on the verifier. Both cards survive a
  reboot (0252273).
- Boot/shutdown scoped per card. An unscoped `vcactl wait` also waited on the
  new, image-less card 1, timed out and failed `vca2-card.service`, which
  skipped jumbo MTU and NAT; a failed unit never runs its clean-shutdown
  `ExecStop` (e09f211). `vca2-boot` then brings up card 1 when present with
  images, powers on fully-off nodes (long-pressing ones parked in
  `link_down`) before any reset, and loads `jumbo_mtu` for eth0-5;
  `vca2-shutdown` powers off and detaches whatever is attached on either card
  (0252273).
- Card-to-card fabric, iperf3 3.9, one stream, 10 s, MTU 65521, both cards
  idle (7ea3dcf): same-card pairs 7.9-27.4 Gbit/s, cross-card pairs 8.1-23.8
  Gbit/s, ping ~0.5 ms, 0 retransmits everywhere. Crossing cards costs
  nothing measurable; every packet hairpins through host RAM either way. All
  three cross pairs at once: 32-35 Gbit/s card 0 to card 1, 32-43 Gbit/s the
  other way (~4-5 GB/s). Uplinks: card 0 sits behind root port 00:01.0, only
  x8 (`LnkSta x8 (downgraded)`); card 1 is on 00:02.0 at a full x16. Card 0
  was already on x8 before the second card, so the "single x16 uplink" in
  FABRIC was the card's capability, not what this board gave it.
- Remotion render bench (331fae5, 0ba0871). First pass, VideoPipeNew's
  `AggregatorShort` (74 s, 2220 frames, 1080x1920) on one node: 86 s wall,
  25.8 fps at `--concurrency=4`, 26.4 fps at 8 (CPU-bound). Encoding is noise
  (Quick Sync H.264 188 fps); frame drawing in Chrome is the whole cost.
  Then a real ContextianStudio episode on card 1: short
  (2198 frames) 146 s on one node, 60 s on three; long (15474 frames) 1081 s
  on one node, 363 s on three. One node is ~0.8x the PC, one card ~2.5x, and
  the PC stays free.
- smem in two lanes (c406f1a): search/ask stay on card 0; the ingest and
  dreaming worker gets its own pool on card 1 (model servers installed there
  too) and falls back to card 0 when card 1 is penalised, absent or lent out
  through `/run/smem/paused.json` (`{nodes, until}`; `until` is mandatory so a
  crashed holder cannot keep nodes). `/v1/lanes` shows lanes, paused nodes and
  in-flight counts; `services.sh` takes `SMEM_NODES`. Verified: an ingest ran
  entirely on card 1; with card 1 paused the next one sent 0 requests there.
- Render farm on card 1 (494d877): `renderfarm.service` on the host (:8910,
  bearer token), content-addressed code and asset uploads, jobs split across
  nodes 3-5 as h264-mkv plus PCM parts, joined sample-exactly and AAC-encoded
  once; each job borrows card 1 from smem through the pause file. Real
  episode through the real client: long form 7 min 16 s against ~15 min on the
  PC, short 70 s. Audio sits at a constant +42.6 ms against the source
  narration; the PC's chunked render drifts to +261 ms by the end.
- Farm hardening and DocuGen (16c3792, b141f47): `code` must be a 64-hex
  hash and every value interpolated into a node command is shlex-quoted (the
  old `code` string became part of the ssh command run on the nodes; token
  gated, but still command injection, flagged by the automated security
  review). Per-app asset namespaces (`default` / `docugen`), per-job
  concurrency 1-8. DocuGen: 8.4 min long form in 9 min 50 s, 54 s short in
  88 s; a farm frame matched a single-pass render at 41.9 dB PSNR.

**2026-09-28 -- node 3 lost**

- Node 3 (card 1 cpu 0, 172.31.4.1) hit a `dma_hang` about 4 minutes into a
  render on the farm; its siblings kept running. The farm had required all
  three nodes and failed every job, so jobs now probe the nodes, run on those
  that answer, and requeue a lost node's parts (finished ones too) to the
  survivors; `/health` reports `down`. Verified with simulated loss and a real
  short video (2160 frames) on 2 nodes in 116 s (13189a1).
- Node logs, read back from the disk images, show every CPU on node 3
  throttling at 13:46:48; the host logged `plx87xx_dma ... dma hang detect
  0x10009` 34 s later (773176c, THERMAL). Kernel throttle events since 09-21:
  card 0 all three nodes 0; card 1 cpu 1 3, cpu 2 6,531, cpu 0 ~1,890 then
  dead. Card 1 has 2x80 mm fans against card 0's 2x120 mm shroud.
- A per-node power cycle does not clear it: long press, `blockio close`,
  short press, `reset 1 0`, `wait-BIOS 1 0` ended at `link_down, dma_hang` and
  `wait-BIOS` timed out after 120 s (cb2f335). The fault's location was
  inferred, not traced (5127986). Also tried and failed: a card reset, a host
  power-off, a 60 s wall unplug (09-29 22:42, which rules out a thermal-trip
  latch that only clears with standby power removed), and a RAM reseat plus
  repaste (09-30). The node's PCIe link on switch port `0a:08` stays at x0 and
  it never reaches BIOS; it was still dead at the end of this period.
- `/tank-red/vmdata` moved to the tank-purple mirror (2x WD101PURP) after a
  tank-red WD30EFRX failed (SMART long-test read failure, then FAULTED by
  ZFS); done with `zfs send`, same path kept so boot scripts, the farm and
  the image builder needed no change. Old copy at `/tank-red/vmdata-old`
  (read-only; snapshots `@copy-20260928`, `@final-20260928`) (ba779c4).

**2026-09-29 / 09-30 -- power cap and boot with a dead node**

- Card 1 capped at PL1 35 W / PL2 40 W through a persistent `rapl-cap.service`
  on each node, set by `scripts/card1_power_cap.sh`. A 180 s all-core load on
  cpu 2: package max 81 C, 37 W average, 0 throttle events (773176c). After
  the repaste and RAM reseat, both live nodes loaded together for 180 s: cpu 1
  71 C, cpu 2 80 C at 35 W; 83 C and 94 C at the stock 45 W / 56 W. No
  throttle events either way; the cap stays until card 1 gets the 120 mm
  shroud (b84b7cd).
- Boot with a dead node (7a84544): the card-wide wait-BIOS under `set -e`
  exited when node 3 would not reach BIOS, leaving nodes 4-5 unbooted,
  `jumbo_mtu` unloaded, NAT unset and the unit failed. Now per node: wait-BIOS
  per cpu and skip the dead; a card with live nodes is never reset, only its
  missing nodes; a node stuck in `booting` gets one power-cycle retry;
  `jumbo_mtu` covers the NICs that exist and reloads when one is short; exit 0
  while any node is up; start timeout 15 to 30 min. Tested live with 5 nodes
  up and node 3 dead.
- Tuned after the 09-30 boot (b84b7cd): two card-1 nodes sat in
  `net_device_up` for 4.7 min with the OS already running, `vcactl wait` gave
  up at 200 s, and the retry power-cycled a node that was nearly ready. Now a
  node past `booting` gets up to 7 more minutes; only one still in `booting`
  is retried.

**2026-10-03 / 10-04 -- farm on card 0, hash checks, smem lockout**

- Render farm adds card 0 nodes 0-1 (0d267da); node 2 is never lent, so smem
  searches keep running (0.05-0.07 s measured mid-render). Replayed real
  episodes: short 116 s to 83 s, long 714 s to 424 s. Per part card 0 renders
  13.5-14.6 fps against card 1's 10.9-12.5 fps (35 W cap); card 0 peaked at
  78 C, no throttling.
- Catch-up and fallback (5467cf5): `GET /v1/code` reports ready only when the
  package is kept on the host, so a client re-uploads a pre-retention version
  and the farm installs it on nodes that lack it. A part failing for lack of
  WebGL (three.js scenes) is retried with `--gl=swangle`; DocuGen's 3D beats
  now render on the farm at ~1 fps instead of falling back to a local render.
- `PUT /v1/code/<hash>` recomputes the client's content hash and returns 400
  on a mismatch before installing or keeping the package, since kept packages
  are installed on nodes caught up later (b9846aa; raised by the automated
  security review).
- smem calibration lockout (d269a23): the background refit re-linked past
  "extends" decisions, three of which named memories since deleted, so an
  INSERT failed on a foreign key, nothing rolled back, and the worker's
  connection held the write lock. Every API write (13 of 13) then failed with
  "database is locked" after the 15 s busy timeout. The refit now skips gone
  memories and rolls back on any error, as does the worker after any refit or
  dream error; `test_smem` reproduces the old failure.

**2026-10-07 -- embedder switch**

- Bake-off, EmbeddingGemma 2 (Q8_0) against Qwen3-Embedding-0.6B over 30
  questions (62e41c1): recall@15 90% vs 87%, recall@40 97% vs 100%, MRR 0.601
  vs 0.594, so a tie on quality. Bulk embed 3.08 vs 1.16 texts/s (2.6x), one
  query embed 36 ms vs 73 ms, on node 5 under the 35 W cap (the doc's "node
  6"). Qwen on the same llama.cpp b11476 measured 1.11 texts/s, so the speed
  is the model, not the build. It needs llama.cpp b11476+; the nodes' build
  cannot load it. Bulk runs against the live embedder OOM-killed it twice at
  its 3 GB cgroup cap.
- Switched (046d04e): 768 dims, Gemma's query/document prompts, cosine
  thresholds re-derived for its score range (NEAR 0.89, SAME 0.99, EXT_LOW
  0.875, doc_sim 0.90, forget floor 0.55), and memories and chunks get an
  equal share of the rerank pool because Gemma scores memories higher. `smem
  reembed` re-embedded 1467 vectors in 79 s with none left at the old size;
  eval.py rerank-40 15/15.
- Models now refuses a vector that is not `DIM`, with an error naming the fix,
  so node 3, whose image still has the old embedder, fails loudly if it
  returns instead of storing mixed sizes. Before smem uses a repaired or
  reflashed node: `SMEM_NODES=4 ./services.sh install` (smem's own numbering
  for node 3) (4dbd8bc).

**2026-10-09 -- vcamon**

- `vcamon` on the host logs every node's temperature, power and throttle
  events every 30 s into SQLite, one SSH session per node, power averaged
  from the RAPL energy counters and throttle columns as event deltas so
  nothing between samples is missed; unreachable nodes get `up=0` rows
  (f382eef). Started after a week in which the only evidence of how hot the
  cards got was the kernel's throttle counters.
- The node password is now passed to `sshpass` through the environment rather
  than argv (d9ea9ed).

## 2026-10-11 — 92 mm shroud v2: bell loft

New shrouds were wanted for 92 mm fans. The existing 92 mm shroud was never
printed and had two known profile problems (README problems 3 and 4), so this
is a v2 rather than a reprint.

- `vca2_fan_shroud.scad` gains `loft="bell"`: a 48-slice loft with a uniform
  2 mm wall. Width and height are linear after a 15 % C1 ease-in at the
  socket, and the corner radius follows `1-(1-t)^2`. Default stays
  `"straight"`; the re-rendered v1 92 mm STL matches the committed one
  (same volume, bounds, face count).
- Loft length 110 mm (1.2 D, up from 0.6 D), so 138.5 mm overall. Equivalent-cone
  half-angle 6.8° (was ~19°), wide-face wall 13.3° (was ~24° with a sharp
  corner). `√t` corner rounding was tried first: the same airflow numbers, but it
  made a 67° overhang in the corners, so it was dropped.
- `vca2_92mm_shroud_v2{,_faceexit,_bracket}.stl`: all watertight, worst loft
  overhang 17.5° printed flange-down, 77-79 cm³.
- In bell mode the face-exit window is cut from the centreline. Cutting at
  x = 19 like v1 left a feather-thin sliver at the window's far corners,
  because the rounding corner curves inside that plane by z = 30-40.
- **Not done:** never printed, never fitted, never measured. Pull mode is
  improved, not shown to work (13° per side is still past planar first stall).
  Cable slots and `bracket_w` are still the unverified v1 values.
