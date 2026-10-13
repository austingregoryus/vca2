<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# PCIe fabric (what the card actually is)

## Topology

The VCA2 is three independent computers on one PCIe card. This host has two
cards: card 0 (nodes 0–2) and card 1 (nodes 3–5); the topology below is one
card's. There is **no QPI,
no NVLink, no coherent shared memory** between the E3s. Each node has its own
BIOS, DRAM, iGPU, and NTB endpoint. The host sees three NTB functions; each
node sees one.

It is **two cascaded switches, not one** — corrected 2026-09-21 against
Intel's block diagram (`VCA2_arch.jpg`, in Intel's VCA 2 product
documentation; not included here) and confirmed live. This matters,
because it is why node 2 behaves differently from nodes 0 and 1:

```
host ──PCIe Gen3 x16*── PEX 8749  "1:3 mux"  (48-lane, 18-port, w/ DMA)
                            ├── x8 ── NTB 8086:2954  node 0  Xeon + P580
                            ├── x2 ── M.2 riser  (present, UNPOPULATED)
                            ├── x8 ── NTB 8086:2955  node 1  Xeon + P580
                            └── x8 ── PEX 8717  "1:1 mux"  (16-lane, 8-port)
                                        └── x8 ── NTB 8086:2956  node 2
```

\* The card's uplink is x16-*capable*. In this host card 0 negotiates **x8**
and card 1 x16 — see [Uplink widths](#card--card-second-vca-2-measured-2026-09-26)
below.

Every NTB link is Gen3 x8 (~7.9 GB/s raw, ~63 Gbit/s). All three nodes on a
card share that card's single uplink to the host.

**Node 2 is one store-and-forward hop further from host RAM than nodes 0 and
1.** That single structural fact explains its measured behaviour: slowest DMA
*reads* (latency-bound, non-posted) but fastest DMA *writes* (posted, so the
hop is nearly free). See [DMA.md](DMA.md) for the numbers.

Node-to-node IP still goes **host hairpin** today. Node-to-node P2P via the
root complex is disproven here: a node's write aimed at a peer's BAR
crosses both switches and reaches the host, and the root complex does not
reflect it back down ([DMA.md](DMA.md)). Forwarding NT-to-NT *inside* the PEX
was never attempted (it needs undocumented PLX routing registers).

PCIe payload programming (lspci, all three nodes, 2026-09-20):

| Side | Link | MaxPayload (programmed) | MaxReadReq | DevCap payload |
|---|---|---:|---:|---:|
| Guest NTB, node 0 | 8 GT/s ×8 | **512 B** | **128 B** | 2048 B |
| Guest NTB, nodes 1 & 2 | 8 GT/s ×8 | **256 B** | **128 B** | 2048 B |
| Guest DMA fn, node 0 | 8 GT/s ×8 | 512 B | 512 B | 2048 B |
| Guest DMA fn, nodes 1 & 2 | 8 GT/s ×8 | 256 B | 512 B | 2048 B |
| Host NTB | 8 GT/s ×8 | **512 B** | **128 B** | 2048 B |

Re-measured 2026-09-21: node 0 negotiates **512 B** where nodes 1 and 2 get
256 B — the earlier "256 B on all three nodes" line was wrong. MaxPayload does
**not** track DMA throughput, though: nodes 1 and 2 share 256 B and sit at
opposite ends of the performance range ([DMA.md](DMA.md)).

128-byte MaxReadReq on both ends is a plausible reason virtio-net tops out
near 5 Gbit/s (small PCIe reads). **Tried 2026-09-20 on node 0 only:**
`setpci CAP_EXP+8.w` MaxReadReq bits **do not stick** (write 512/2048,
immediate readback still 128 on host `05:00.0` and guest `01:00.0`).
ALUT guest pull stayed **~28 MiB/s**. iperf moved only within prior noise
(host→n0 ~4.5–4.9 Gbit/s; n0→host 2.6–3.7 looks like warmup toward the
old ~3.5). Restored original DevCtl. Hardware/firmware hard-wires MRR.

## Two data paths we have measured

### 1. Virtio-net (VOP) — TCP/IP

Kernel backend `vca_virtio_net` + guest virtio-net. Host NICs `eth0/1/2`
(eth0–eth5 with both cards).
PVE 7 defaults `max_mtu` to 1500 (`ip link set mtu 2048` → *mtu greater
than device maximum*). The driver already has `MAX_MTU 65535` and
`ndo_change_mtu`; it never sets `dev->max_mtu`. Guest is already **65521**.

**Jumbo works without reloading VOP.** `compute/jumbo/jumbo_mtu.c` sets
`eth0->max_mtu = 65535` then `dev_set_mtu`. `rmmod` restores 1500. Proven
2026-09-20, node 0 only, DF pings at 9000 and 20 kB:

| MTU | Host → node 0 | Node 0 → host | Notes |
|---:|---:|---:|---|
| 1500 | ~4.5–4.9 Gbit/s | ~2.6–3.7 Gbit/s | earlier ~4.7 / 3.5 |
| **9000** | **14.1 Gbit/s** | **13.9 Gbit/s** | 0 retr; DF ping 8972 OK |
| **65521** n0 | **13.8 Gbit/s** | **28.6 Gbit/s** | 0 retr; DF ping 20000 OK |
| **65521** n1 | **13.5 Gbit/s** | **15.8 Gbit/s** | 0 retr; DF ping 20000 OK |
| **65521** n2 | **14.6 Gbit/s** | **25.6 Gbit/s** | 0 retr; DF ping 20000 OK |

~3× at 9000. Guest→host at 64 KiB frames is about **half of Gen3 x8**.
TSO/GSO likely helps the 28 Gbit/s direction.

**Left on (2026-09-20):** `jumbo_mtu.ko` loaded, host eth0/1/2 **65521**
to match the guests. DF ping 8000 to all three nodes OK. `rmmod jumbo_mtu`
restores 1500. `vca2-boot` (`scripts/vca2-boot.sh`) insmods
`/root/vca2/jumbo_mtu.ko` after `net_device_ready`; with both cards up it now
covers host eth0–eth5.

TCP iperf3 at **MTU 1500** (5 s, IPv4, earlier session):

| Path | Gbit/s | Notes |
|---|---:|---|
| Host → node 0 | 4.70 | 0 retr |
| Host → node 1 | ~5.0 | 0 retr |
| Node → host | 3.4–3.5 | 0 retr |
| Node 0 → 1 | 2.6 | 32 retr, via host |
| Node 1 → 2 | 2.6 | 32 retr |
| Node 0 → 2 | 1.8 | 32 retr; extra hop through the PEX |

At 1500 this is about **8% of x8**. At jumbo, host↔node 0 is a real
fabric. Node-node IP is still a host hairpin — IP through the host, not a
switch fabric.

### Node↔node at jumbo (measured 2026-09-21)

This was the outstanding gap above. Measured on node 1 ↔ node 2 with both
nodes on Ubuntu 22.04, iperf3, 10 s, 3 reps each — then the **same pair
dropped to MTU 1500** as a control and put back:

| node 1 ↔ node 2 | node 1 → node 2 | node 2 → node 1 |
|---|---|---|
| MTU 1500 (control) | 2810 / 2739 / 2767 Mbit/s | 2660 / 2768 / 2824 Mbit/s |
| MTU 65521 | 15934 / 7991 / 7954 | 25469 / 24819 / 12971 |

Two things fall out of that:

**1. The MTU-1500 control reproduces the historical number.** 2.74–2.82
Gbit/s today against the 2.6 Gbit/s recorded in the earlier session — same
pair, different OS, different iperf3 version. The old measurement was sound.

**2. Jumbo is worth roughly 3x–9x on node↔node IP**, but it is *erratic*:
7954–15934 Mbit/s one way and 12971–25583 the other, run to run, on an idle
card. MTU 1500 by contrast varied by under 3%. Jumbo lifts the ceiling a long
way and makes the floor unpredictable — a single TCP stream at 65521 is
CPU-bound on the sender and sensitive to how the host's forwarding path is
scheduled at that moment. Parallel streams did not stabilise it (`-P 4` gave
16262 and 9040 Mbit/s on the two directions).

If you need a *predictable* node-to-node rate, the DMA ring is the answer, not
jumbo: it held within 2% across runs ([DMA.md](DMA.md)).

### Card ↔ card (second VCA 2, measured 2026-09-26)

Card 0 = nodes 0-2, card 1 = nodes 3-5. Same method as above: iperf3 3.9,
one TCP stream, 10 s, 3 reps each way, MTU 65521, both cards idle.

| Pair | Gbit/s (all runs, both directions) | ping avg |
|---|---|---|
| same card, n1 ↔ n2 | 7.9 – 25.2 (median ~14) | 0.51 ms |
| same card, n4 ↔ n5 | 7.9 – 27.4 | 0.53 ms |
| cross, n0 ↔ n3 | 9.6 – 19.2 | 0.51 ms |
| cross, n1 ↔ n4 | 8.1 – 16.7 | 0.50 ms |
| cross, n2 ↔ n5 | 12.0 – 23.8 | 0.52 ms |

**Crossing cards costs nothing measurable.** Same latency, same erratic
8-27 Gbit/s band, retransmits 0 everywhere. That is expected once you see the
path: there is no node-to-node P2P even *within* a card -- every packet goes
node → NTB → host RAM → host routing → NTB → node, so "same card" and "other
card" differ only in which PCIe root port the second hop leaves by. The runs
cluster at ~8, ~9.6, ~12, ~16, ~19 and ~23-27 Gbit/s whatever the pair, which
points at per-stream CPU/scheduling on the sending node and the host, not the
links.

All three cross-card pairs at once: **32-35 Gbit/s** card 0 → card 1 and
**32-43 Gbit/s** card 1 → card 0 in aggregate (~4-5 GB/s), unevenly split
between pairs (6-21 each). Still well under card 0's x8 uplink (63 Gbit/s
raw).

**Uplink widths (corrects the x16 claim at the top).** Card 0 sits behind CPU
root port 00:01.0, which is only x8 (`LnkSta x8 (downgraded)`); card 1 is on
00:02.0 at a full x16. Kernel logs from before the second card show card 0
was already on an x8 link then (root port 00:03.x, bifurcated), so the
"single Gen3 x16 uplink" was the card's capability, never what it got in this
board.

### The distro is not a variable here

Both endpoints were swapped from Ubuntu 16.04 to 22.04 and re-measured. As a
control, node 0's own xenial `iperf3` binary was staged onto both 22.04 nodes
and run there, so the *same executable* measured both configurations:

| node 1 → node 2 | node 2 → node 1 | |
|---:|---:|---|
| 15.8 Gbit/s | 26.3 Gbit/s | native jammy iperf3 3.9 |
| 16.0 Gbit/s | 26.3 Gbit/s | xenial iperf3 binary on both jammy ends |

Identical. That is expected and worth stating plainly: **the transport is
`vca_virtio_net` in Intel's 4.14 kernel, which the new image does not
replace.** Userspace modernisation cannot move this number, and it didn't.
Compare the CPU GEMM result in [NODE_IMAGE.md](NODE_IMAGE.md), where the same
image change was worth ~1.9x — because that one *was* userspace.

> Cross-version gotcha: iperf3 3.0.11 (xenial) and 3.9 (jammy) are **not**
> wire-compatible. A 3.9 client against a 3.0 server fails with
> `unable to send control message: Bad file descriptor`, which looks like a
> network fault and is not one. Use the same binary on both ends.

VOP kvec stats (host, node 0, idle-ish after recovery) showed ring 0 very
hot (`prepare` 4.6M / `used` 3.4M) — that is virtio-net + BlockIO traffic.

### 2. BlockIO — virtio-blk DMA

This is how the OS disks work, and it is the only **non-IP** path we have
used successfully from userspace so far.

Host: `vcactl blockio open 0 N vcablk1 RW /path/file`
Guest: `/dev/vcablkN` appears live (no reboot). `vcactl blockio list` may
show `inactive` for a moment; the guest still got `vcablk_disk_start`.

Backend (`vcablk_bcknd_media.c`): FILE type does `kernel_read` / `kernel_write`
on the host file, then DMA to the card. Extra copy. MEMORY type is `vmalloc`
on the host (not mmapable from userspace without a new ioctl).

See [archive/COMPUTE_MAILBOX.md](archive/COMPUTE_MAILBOX.md) for the 3-CPU PoC on this path.

## ALUT (A-LUT) — how the 4 GB BAR is really used

Source: `src/vca_modules/vca/plx87xx/plx_alm.c`.

- Hardware has **256** ALUT slots, divided by `num_ntbs`.
- `segment_size = aper_len / segments_num`.
- Live dumps (`docs/artifacts/vca_fabric_dump/alm_*.txt`, 2026-09-20):

  | Host NTB | Node | Mask | Segment | Slots |
  |---|---|---|---:|---:|
  | `05:00.0` | 0 | `…fe000000` | **32 MB** | 128 (`00`–`7f`) |
  | `07:00.0` | 1 | `…fe000000` | **32 MB** | 128 |
  | `0a:00.0` | 2 | `…ff000000` | **16 MB** | **256** (`00`–`ff`) |

  `segments_num = 256 / num_ntbs`. This is **explicit in the driver**, not a
  mystery. `plx_hw.c` switch on PCI ID:

  - `0x2954` / `0x2955` (nodes 0/1) → `num_ntbs = 2` → 128 × 32 MB, ALUT
    base `0x38000` (port 0) or `0x3a000` (port 1)
  - `0x2956` (node 2) → `num_ntbs = 1` → 256 × 16 MB, ALUT base `0x38000`

  Confirmed in `dmesg` after the recovery reboot:

  ```
  0000:05:00.0  segment 2000000  segments:80  ntbs:2  base 38000
  0000:07:00.0  segment 2000000  segments:80  ntbs:2  base 3a000
  0000:0a:00.0  segment 1000000  segments:100 ntbs:1  base 38000
  ```

  Node 2 is a different NTB function on the PEX (single-NTB, not a pair).
  A buffer allocator must use that node’s `segment_size`, not assume 32 MB.
  Raw dumps: `docs/artifacts/`.
- `plx_ioremap(xdev, pa, len)` / `plx_add_a_lut_entry(xdev, pa)` program
  **outbound** translation: local BAR2 + offset reaches **peer physical**
  `pa`. Host mapping `0x4c000000` made host BAR2+`0x22000000` read guest
  DRAM at that PA (`0x5a5a5a5a`). Guest mapping the host DMA address made
  guest BAR2+`0x8000000` show the host mailbox magic `ALUT`. Each side
  must add its own LUT entry to reach the other. All three nodes ping-pong
  this way. Host-push into guest RAM is ~3.7 GiB/s; guest pull of host RAM
  is ~28 MiB/s. See [ALUT_MBOX.md](ALUT_MBOX.md).
- `ALUT_UNMAP_LAZY`: `ref_cnt == 1` means leftover, not in active use.
  Observed ~26 occupied slots, of which only a couple had `ref_cnt` 3 or 5
  (live virtio/BlockIO). The rest of the table is free windows (32 MB on
  nodes 0/1, 16 MB on node 2).

That is the on-ramp for a real doorbell+buffer transport: call
`plx_alm_add_entry()`, do not mmap `resource2` and guess.

## Doorbells

`PLX_MAXDB 16` (`plx_hw.h`). `plx_send_intr(xdev, db)` writes peer `DBIS`.
Doorbell **0** is LBP. VOP/BlockIO consume low numbers (`plx_next_db` on
node 0 returned 8). We used **15**. MSI off — INTx.

**Proven 2026-09-20:** host `alut_hdb` kick of db 15 + guest `/dev/vca_gdb`
+ ALUT header ack. RTT **~35 µs** on all three nodes. Do **not** cat
`plx87xx/*/intr_send` (fires all 16). See [ALUT_MBOX.md](ALUT_MBOX.md).

Scratchpads (8 × 32-bit), read-only dump is safe. Node 0 example after
recovery:

```
0: fffff000     1: 00000000
2: 00000200     3: 00000002
4: 00000000     5: 77ad1000
6: 00000000     7: 00000000
```

SPAD 5 has held BIOS device-page addresses (`77ad1000`). Linux later used
`0x36400000` in dmesg for the BlockIO device page.

Device page size: `VCA_DP_SIZE 4096` (`vca_dev_common.h`). Bootparam also
has leftover SCIF fields and `test_host_dma_addr` / `test_card_dma_addr`
used by the debugfs DMA tests — do not poke those.

## RID LUT (not node↔node P2P)

`plx_program_rid_lut` and `plx_program_rid_lut_dma` (`plx_hw.c`) write the
PEX requester-ID lookup so the **host root complex** and the **on-card DMA
engine** are allowed through the NTB. Bus numbers for the DMA engine are
hard-coded (“not expected to change”). This is host↔card DMA enablement.

It is **not** a program of peer routing between node 0 and node 1. NT0 vs
NT1 offsets exist because nodes 0/1 are a two-NTB pair; node 2 is the
single-NTB function. Peer routing was tested on 2026-09-21
([DMA.md](DMA.md)): a node's write to a peer's BAR crosses the switches and
reaches the host, where the root complex does not reflect it. Forwarding
inside the PEX was not attempted.

## What is stubbed vs real

| Mechanism | Status |
|---|---|
| VOP virtio-net + DMA | Real, ~5 Gbit/s TCP at MTU 1500; 13.5–28.6 Gbit/s host↔node at 65521 |
| BlockIO file DMA | Real, extra copy, used for OS + mailbox |
| `VCA_VIRTIO_ADD_DEVICE` | Real (creates guest virtio-net) |
| `VCA_VIRTIO_COPY_DESC` | **Stub `-EFAULT`** in `vop_vringh.c` |
| CSA (`csa_mem`, `net_config`) | Real, agent/config only |
| Userspace mmap of BAR2 | Exposed, **unsafe** without ALUT ownership |
| Host↔card doorbell from userspace | Not exposed as a simple ioctl we have used |
| Peer-to-peer node↔node BAR | **No** via the root complex (DMA.md); PEX-internal forwarding not attempted |

Plan 2’s first real kernel milestone was: a new non-net VOP endpoint
(command queue + doorbell + DMA buffer) or a small module that allocates one
free ALUT window and mmap’s it. The BlockIO mailbox was the userspace
stand-in. The second option was built (`plx_mbox`, [ALUT_MBOX.md](ALUT_MBOX.md)),
and the on-card DMA engine ([DMA.md](DMA.md)) has since superseded it for bulk
transfer.
