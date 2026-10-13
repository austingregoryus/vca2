<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Open questions

Last reviewed 2026-10-13. Struck-through items are answered and kept so the
answer stays findable; the dated detail is in [LOG.md](LOG.md).

## Fabric

- ALUT **doorbell** mailbox is proven on all three nodes (~35 µs).
  Host-push 4 MiB is ~3.7 GiB/s. Still open: persist LUT+db without
  throwaway kmods, a **full-segment** guest buffer (CMA; MAX_ORDER 4 MiB),
  C memcpy vs Python, node↔node BAR routing.
- ~~Never tried: the on-card DMA engine~~ **DONE 2026-09-21, and it was the
  unlock.** Guest pull went 28 MiB/s -> ~4000 MiB/s (**144x**), content-
  verified. Card-side engine is **1.7x faster than the host-side** one, so
  always initiate from the card. (The old "~325 -> ~2200 MiB/s, 6.7x"
  node-to-node claim was withdrawn: 325 was an MTU-1500 figure compared
  against jumbo-era DMA. Fair comparison in [DMA.md](DMA.md).)
- ~~Chunked/overlapped node-to-node~~ **DONE, and closed**: pipelined ring
  reaches **4423 MiB/s** verified on its best direction (`compute/dma/ring.c`).
  **Stop optimising the transport.** All six ring directions are predicted by
  `min(sender's write rate, receiver's read rate)` at 91-99%, **mean 96.0%**,
  so there is essentially no protocol overhead left to remove. Any further
  node-to-node gain must come from the per-node asymmetry below.
- **THE open hardware question: the per-node send/recv asymmetry.** Each
  node's DMA send and recv rate is a property of the *node*, not the pair:
  node 2 writes at ~5642 MiB/s but reads at ~3129; node 1 is the exact
  inverse (~2058 write, ~4849 read); node 0 is ~3997/~3949. So node 2 → node
  1 runs **2.2x** faster than the reverse.

  Ruled out: link width, link speed, MaxPayload, MaxReadReq, ExtTag,
  RelaxOrd, NoSnoop — node 1 and node 2 match on every one and sit at
  opposite ends of the range. **Software is ruled out too**: re-measured
  2026-09-21 with all three nodes on one byte-identical image and the spread
  is unchanged, with the node that *did* change OS moving less than the two
  that did not.

  The only remaining correlate is which **PCI function the PLX DMA engine
  lands on** — `01:00.1` on node 1, `01:00.3` on nodes 0 and 2 — which the
  EEPROM decides. Node 1 exposes no second DMA function, so a different
  channel cannot simply be bound to test it. Open: is channel `.1` inherently
  slower, or is it a credit/tag setting reachable from a register nobody has
  looked at? This is now the single highest-value unknown on the card, since
  the transport itself is done. See [DMA.md](DMA.md).
- ~~node↔node jumbo iperf~~ **DONE 2026-09-21.** 2739–2824 Mbit/s at MTU 1500
  (reproducing the historical 2.6 Gbit/s exactly) against 7954–15934 and
  12971–25583 Mbit/s at 65521 — roughly 3x–9x, but *erratic*, where the DMA
  ring holds within 2%. Distro-independent, confirmed with the same binary on
  both configurations. See [FABRIC.md](FABRIC.md).
- ~~Does the fabric route node-to-node P2P?~~ **Answered: no.** Not ACS, not
  the switch — the TLP reaches the host fine. VT-d blocked it (fixable with
  one identity mapping, `compute/dma/p2pmap.c`), and underneath that the
  E5-2699 v3 **root complex will not reflect** an upstream write back down to
  a peer BAR. Proven with a host-write control. Dead end; use host staging.
- Real transport remains: swap the polled control words for **doorbells**
  (~35 us RTT, already proven), pipeline the DMA submissions instead of
  waiting per slot, support multiple queues, and make it a persistent driver
  rather than throwaway modules.
- Guest CPU **pull** of host RAM via BAR2 is ~28 MiB/s. `setpci` MaxReadReq
  does **not** stick. Host jumbo is **on** (eth0–eth5, MTU 65521). Still
  open: persist `max_mtu` in `vca_virtio_net` so `jumbo_mtu.ko` is not needed.
- ~~Does the PEX 8717 allow node↔node BAR peer routing, or must all bulk
  data bounce through host memory?~~ **Answered: it bounces through host
  memory** (see the P2P item above).
- **Card 1 has never had the DMA matrix run on it.** It is a free test of
  whether the per-node asymmetry follows the silicon or the slot position:
  if card 1's node 4 (the same position as node 1) is also the slow writer,
  it is the slot or the EEPROM, not the part.
- **Cross-card DMA** has never been measured. Card-to-card virtio costs
  nothing measurable over same-card ([FABRIC.md](FABRIC.md)), but the DMA
  engine's reach across cards is untested.
- `VCA_VIRTIO_COPY_DESC` is a stub in 2.3.26 source (`return -EFAULT`).
  `Intel_SOC_VCA_kernel-master.zip` is CentOS RPMs (kernel 3.10 VCA 2.5.11),
  not source — did not search the `.ko` yet.
- BlockIO MEMORY ramdisk vs FILE: is the ramdisk faster, and can we expose
  it to host userspace?

## Compute

- Tiled **matmul** (not SAXPY) with a host-side reduction / next GEMM panel.
- Same-node CPU thread pool + iGPU OpenCL on one tile (hetero per node).
- ~~384k GEMM hole~~ **SOLVED 2026-09-21**: implicit local work size.
  `local={1,128}` removes it at every N. See
  [GEMM_WORKGROUP.md](GEMM_WORKGROUP.md).
- ~~Naive kernel is ~6% of peak~~ **largely solved 2026-09-21.** Register
  blocking (8 rows/work-item) + float2 columns + A staged in `__local` gives
  **453 GFLOP/s per GPU, 1359 combined** = 34% of peak, 5.8x the shipped
  kernel. Shipped as `GEMMT` in `cl_daemon.c`. Remaining upside is maybe
  1.3-1.5x via CLBlast or `cl_intel_subgroups`; no longer the bottleneck.
- Best operating range for the tuned kernel is N = 1024..2048; N=3072 drops
  to 256 GFLOP/s as the working set outgrows the cache. Not investigated.
- **Every GEMM correctness check in the tree is A=1, B=1, C==n**, which cannot
  detect a wrong row index, a `__local` race or a transpose. It passed a
  kernel that corrupted 40% of its output. Use `compute/gemm_verify.c`
  (random data vs a CPU reference) whenever a kernel changes.
- Hetero packing / a second full matrix on CPU while GPU runs 1408 (HADD
  with 80 extra rows already hid ~15% extra FLOPs).
- A kernel that beats naive on Gen9 (NEO might want cl_intel_subgroups).
- ~~llama.cpp MoE that does not fit in 16 GB~~ **dropped.** RPC splits layers,
  so single-stream decode is pipeline-serial: three nodes never beat one on
  latency, only on capacity. The capacity window is 42 GiB (host) to 47 GiB
  (3 x 15.8) and costs 2.4x decode. Use three **independent** workers with
  host-side batching instead.
- KVMGT: still untested on this homelab after the original ChatGPT session.

## Thermal

- ~~Re-run `compute/thermal_run.sh` after fitting a fan shroud.~~ **Done
  2026-09-22**: card 0 with this repo's 120 mm shroud peaks at 75–79 °C on the
  all-CPU AVX2 phase, down from 92–94 °C. See [THERMAL.md](THERMAL.md).
- ~~Is writing the RAPL power limit a safe way to cap the AVX2 case?~~ **Yes.**
  Tested at 45 → 56 W on card 0 (no throughput change), and in production at
  PL1 35 W / PL2 40 W on card 1 since 2026-09-29 (`scripts/card1_power_cap.sh`).
- **What exactly killed node 3?** It throttled, then hit a `plx87xx` DMA hang
  34 s later (2026-09-28), and has stayed `link_down`, switch port at x0,
  through a power cycle, card reset, host power-off, 60 s wall unplug, RAM
  reseat and repaste. Heat is the strong suspect; whether the CPU, the PCIe
  PHY or the node's VRM failed is unknown. Untried: swapping its SO-DIMMs with a working
  sibling's on card 1.
- Does card 1 hold stock 45 W limits once it has a 120 mm shroud like card 0?
- Why are nodes 1 and 2 consistently 4-6 C hotter than node 0? Mounting,
  airflow path, or position relative to the PEX switch.

## Node OS / modernisation

- ~~Ubuntu 22.04 image built and verified; not yet adopted on all three~~
  **DONE 2026-09-21.** All three nodes run one reproducible image
  (`VCA_IMAGE_BUILDER_GIT=cf796ef`), 23/23 on the on-node verifier each, CPU
  GEMM within 0.7% across the fleet. Builder is profile-driven and targets
  other distros: [IMAGE_BUILDER.md](IMAGE_BUILDER.md).
- A newer **kernel** is gated on converting `vcablk` from the legacy
  `blk_init_queue` request API to blk-mq. ~5k lines, and it is the root
  filesystem, so failure is hard to debug (no serial console from the host).
  **Someone may already have done this** — see
  [KERNEL_PORT.md](KERNEL_PORT.md) and [PRIOR_ART.md](PRIOR_ART.md).
- ~~`vca-netcfg.service` had one racy boot out of three~~ **FIXED.** It now
  retries until the interface actually has an IP rather than until
  `net_config` merely exists, and the fixed copy lives in
  `compute/image/overlay/` where every profile gets it.
- ~~Validate `chainsx/intel-vca2-dkms` on real hardware.~~ **It is the
  running host stack**, on 7.0.2-6-pve and 7.0.14-17-pve, with a full
  six-direction DMA matrix and two reboots behind it. Review findings and the
  local patch are in [HOST_STACK.md](HOST_STACK.md). Worth reporting
  upstream.

## Ops

- ~~No boot service.~~ **Done**: `systemd/vca2-card.service` runs
  `scripts/vca2-boot.sh` at host start, skips a dead node, and powers the
  nodes off cleanly at stop.
- **`scripts/vca2-shutdown.sh` fails unsafe.** If `sshpass` is missing or
  the node password has changed, the clean poweroff fails, the script waits
  out its 200 s timeout, and then detaches the disk images from nodes that
  are still running. It should refuse to detach a node that is not off.
  Until then, make sure `NODE_PW` is right for the unit.
- Node 0 `/boot` FAT dirty after the crash reboot; optional `fsck`.
- Host SSH keys are not on the nodes (password `vista1` still).
- Guest firewalld: 9200 (OpenCL) and 50052 (RPC) are **intentionally open**
  for this experiment. 5201 was temporary for iperf. Decide before any
  non-lab exposure.

## Known inconsistencies

Numbers that disagree between documents and have no recorded explanation.
They are listed here rather than reconciled by guesswork.

- **gemma-4-E4B prefill rate**: ~23 tok/s in [`memory/RESULTS.md`](../memory/RESULTS.md)
  and `memory/README.md`, 34.6 tok/s in [`memory/BAKEOFF.md`](../memory/BAKEOFF.md).
  Probably different prompt lengths or batch settings; not checked.
- **GPU-only GEMM**: 1359.5 GFLOP/s (burst, [GEMM_WORKGROUP.md](GEMM_WORKGROUP.md))
  against 1250.6 for the GPU-alone arm of the heterogeneous test
  ([THERMAL.md](THERMAL.md)). The heterogeneous harness runs a different loop,
  and its GPU-only power (57.7 W) is far below the `gpuburn` phase (~45 W per
  node, ~134 W per card). Not reconciled.
- **Render farm replays**: the 2026-10-03 "same episodes replayed" runs have
  2119 / 15,817 frames, against 2198 / 15,474 on 2026-09-26, and the 714 s
  two-node figure is a production run from 2026-10-01. The comparison is
  approximate ([`farm/README.md`](../farm/README.md)).
- **Node RAM** is quoted as 15.4, 15.8, 14.87 GB, 13.8 GiB and ~16 GB. Some of
  that is GB versus GiB and "installed" versus "available to Linux"; not all
  of it.
