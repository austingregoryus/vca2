<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Safety

Read this before running anything in this repository on your own hardware.

Everything here was measured on **two VCA 2 cards in one host**. The code is
not a product. Several pieces load kernel modules that interact with a live
PCIe fabric while the vendor driver is using it. Nothing here writes
firmware, EEPROM or switch configuration, so no *software* in this repository
can brick the card. Plenty of it can hang a node, hang the host, or take VMs
down with it.

**Heat can physically damage it, though.** The card is passively cooled and
designed for server-chassis airflow. One node here (node 3, on a card cooled
by 2 × 80 mm fans) throttled under a sustained render, hung, and has not come
back through any power cycle, reseat or repaste. Sort out cooling before you
run anything long. See [Heats the card](#heats-the-card).

## The one thing to internalise

**Some debugfs files under `plx87xx` and `plx87xx_dma` are mode `0444` but
reading them performs DMA, a flush, or an interrupt injection.** A routine
directory walk — `grep -r`, a file manager, tab completion with a preview —
is enough to oops the host kernel and leave the card in `dma_hang`.

The list of files to never read is in
[`docs/GOTCHAS.md`](docs/GOTCHAS.md#never-cat--read-these). Read that before
you explore sysfs or debugfs on this card. It cost a host power cycle to
learn.

## Blast radius, worst first

### Can take down the host (and any VMs on it)

- **`rmmod plx87xx` or `rmmod plx87xx_dma` while the card is live.** Leads to
  `dma_hang` on the next boot, and the `rmmod` itself can wedge in D state so
  even a polite `reboot` waits forever. Bounce the host, never the module.
- **Reading the forbidden debugfs files** (above).
- **Writing the 4 GB BAR** (`resource2` / `resource2_wc`) at an offset you do
  not own. It is an A-LUT-translated window onto live virtio rings, device
  pages and DMA buffers.

### Can corrupt host memory

- **Unload order.** A guest A-LUT window points at a host buffer's *bus
  address*. If you free the host buffer while the guest window is still
  programmed, the card can write into whatever the kernel recycles that memory
  for. **Always drop the guest window first.** Every runner script here does
  this via a shell `trap`; if you drive the modules by hand, do it yourself.
- **A-LUT windows are wider than your buffer.** `plx_add_a_lut_entry()` masks
  the address down to a segment boundary, so a 4 MiB buffer exposes the whole
  aligned 32 MiB segment containing it (16 MiB on node 2). Your buffer is
  protected by convention, not by hardware.

### Can hang a node

- **The DMA modules share the channel the vendor driver owns.** The engine has
  one channel per function and `plx87xx` claims it at probe. VOP already
  shares that channel between its instances, so sharing is the driver's normal
  mode — but these modules add a *third* user. It held across many runs here.
  **It was never tested under heavy concurrent virtio-net or BlockIO load.**
- **Reads to an unrouted address.** The root port's completion timeout is
  50 µs–50 ms; a DMA read that nothing claims can wedge the engine. This is
  why the P2P probe is deliberately **write-only** — a posted write that goes
  nowhere is dropped harmlessly. Do not add a read to that path.

### Changes system security posture

- **`compute/dma/p2pmap.c` adds an identity IOMMU mapping** for an entire
  IOMMU group, letting those devices write a range they otherwise could not.
  It exists only to prove a negative (see below) and should not be run as a
  tool. On this card the group contains only VCA devices; on another system,
  check what is in the group first.

### Heats the card

- **Sustained load without real airflow can kill a node.** That is what
  happened to node 3: under a multi-minute render on 2 × 80 mm fans, card 1's
  nodes logged thousands of throttle events, and node 3 hung 34 s after it
  started throttling. It is still `link_down` after a host power-off, a 60 s
  wall unplug, a RAM reseat and a repaste. Card 0, with this repo's 120 mm
  shroud, has never throttled. A card with weak cooling can be capped
  instead: `scripts/card1_power_cap.sh` sets PL1 35 W / PL2 40 W per node
  (81 °C, zero throttles here). See [`docs/THERMAL.md`](docs/THERMAL.md).
- **`compute/thermal_run.sh` deliberately drives the card toward its thermal
  limit.** On stock airflow an all-node AVX2 load reached **92–94 °C** against
  Intel's 96 °C core limit (75–79 °C with the 120 mm shroud). There is a trip
  that kills the load, enforced **on the node** so a dropped SSH cannot leave
  it running. The observed overshoot between 5 s samples was 4–6 °C, so set
  the trip at **85 °C**, not 88. If your card is cooled worse than this one,
  start lower.
- **`vcamon`** ([`monitor/`](monitor/)) logs every node's temperature and
  throttle events every 30 s. Run it before a long workload, not after.

## What is safe

- **Reading the documentation.** All of `docs/`.
- **The OpenCL and GEMM work** (`compute/*.c`, `compute/gemm_*`,
  `compute/wg_*`). Pure userspace on the guest nodes. Touches no VCA driver,
  no debugfs, no BAR.
- **`compute/dma/hostbuf.c`.** Deliberately written to touch no VCA driver at
  all — it is just `dma_alloc_coherent` plus a miscdev — specifically so the
  DMA experiments need neither a patched `plx87xx` nor a host reboot.

## p2pmap: a proved dead end, not a tool

`p2pmap.ko` was written to test whether node-to-node P2P could work. The
answer is **no**, and the module is kept only because the reasoning is worth
preserving:

1. ACS is not the blocker — the TLP reaches the host fine.
2. VT-d *was* a blocker, and this module fixes it.
3. Underneath that, the root complex will not reflect an upstream write back
   down to a peer BAR. Not fixable in software.

Full evidence in [`docs/DMA.md`](docs/DMA.md). Loading it buys you nothing but
a weakened IOMMU. **That conclusion is specific to this host** — an
E5-2699 v3 root complex. A different platform could behave differently, and
if yours does, that is a genuinely interesting result worth reporting.

## If you hang it

From [`docs/GOTCHAS.md`](docs/GOTCHAS.md), the recovery that actually worked:

1. A polite `reboot` will hang if an `rmmod` is stuck in D state.
2. sysrq `b` sometimes works, sometimes does not on this box.
3. A real power cycle always does.
4. Then the normal BlockIO boot: `reset` → wait for BIOS → `blockio open` →
   `boot vcablk0`.

Guest `/boot` is FAT and will log "not properly unmounted" afterwards; that is
expected and harmless.

Two traps once the card is under `vca2-card.service`, both in
[`docs/GOTCHAS.md`](docs/GOTCHAS.md#a-second-card-learned-2026-09-26):

- **With more than one card, name the card** in every `vcactl` command. An
  unscoped command acts on every card.
- **Use `systemctl start vca2-card`, never `restart`.** A `vcactl reset` on a
  powered-off node parks it in `link_down`, and only a
  `pwrbtn-long` → `pwrbtn-short` sequence recovers it.

## Scope of the measurements

Two cards, one host, 2026-09-20 to 2026-10-09:

- Host: Proxmox, Xeon E5-2699 v3, kernel `7.0.14-17-pve` (most of the
  fabric work was first measured on `7.0.2-6-pve`, then re-checked), driver
  stack `chainsx/intel-vca2-dkms` plus a local patch
  ([`docs/HOST_STACK.md`](docs/HOST_STACK.md))
- Cards: VCA1585LMV, BIOS `0ACIE203`, VCASS 2.3.26 userspace
- Nodes: Intel kernel `4.14.20-1.2.3.26.vca`, Ubuntu 22.04 userspace from
  [`compute/image/`](compute/image/) (16.04 for the earliest measurements)

Results that are likely to generalise: the GEMM and workgroup findings, the
DMA engine throughput, the thermal behaviour.

Results that may not: **anything about root-complex or IOMMU behaviour**,
which depends on your CPU and firmware, and the exact A-LUT geometry, which
depends on how many NTBs your card presents.
