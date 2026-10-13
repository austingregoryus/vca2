<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Modern kernels on the VCA 2 — what is done and what is left

> **Corrected 2026-09-21.** An earlier version of this file proposed
> "validating a third-party forward-port" as a future project. That premise
> was wrong: **the port was already installed on this host and had been
> driving the card since 2026-09-19.** See [HOST_STACK.md](HOST_STACK.md).
>
> What follows is what remains genuinely open.

## Status: the host side is done

The host runs `chainsx/intel-vca2-dkms` on **Linux 7.0**, and every
measurement in this repository was taken on it — the 144x DMA unlock, 4423
MiB/s node-to-node, jumbo, BlockIO, the six-direction matrix. Intel's stock
`plx87xx` cannot even compile against this kernel (it supplies a
`dma_map_ops` table and `.map_page` no longer exists as a member), so the
fork is load-bearing, not optional.

An independent review of the diff found the port substantive and competent,
with several unclaimed bug fixes — and three undisclosed behavioural changes
that are worth interrogating. All of that is in
[HOST_STACK.md](HOST_STACK.md).

## ~~Open 1 — why do the virtio NICs vanish on 7.0.14-17-pve?~~ ANSWERED: THEY DO NOT

**Tested 2026-09-21. The premise was false.** `7.0.14-17-pve` brings up all
three virtio NICs, carries jumbo frames, and runs the DMA fabric at
**4581/4487 MiB/s** — marginally faster than the pinned kernel.

The original observation was almost certainly made **before the nodes were
booted**. `vop` devices, and therefore the NICs, are created when a node
boots — not when the modules load. Immediately after a host reboot the card
sits at `bios_up` with an empty `/sys/bus/vca_virtio/devices/` on *any*
kernel, which is indistinguishable from a broken one if you do not check
`vcactl status`.

One caveat, stated honestly: this test also ran with the patched `plx87xx`
on 7.0.14 for the first time, where previously it had the stock DKMS build.
That is a second changed variable. It is *unlikely* to be the explanation —
`plx_mbox` only adds a debugfs/misc interface and is not involved in `vop`
device creation — but it has not been separately eliminated.

**Consequence: the host kernel pin is unnecessary.** See below for what is
left before removing it.

### Adopted 2026-09-21 — `7.0.14-17-pve` is now the pinned kernel

Validated before pinning:

- **Two reboots**, the second unaided on the permanent pin.
- **Full six-direction DMA matrix**, every run `VERIFIED`, no direction
  slower than on 7.0.2-6-pve:

  | direction | 7.0.2 rx | 7.0.14 rx | |
  |---|---:|---:|---:|
  | n0 -> n1 | 3725 | **3835** | +3.0% |
  | n1 -> n0 | 2039 | 2043 | +0.2% |
  | n0 -> n2 | 3093 | 3092 | -0.0% |
  | n2 -> n0 | 3769 | 3774 | +0.1% |
  | n1 -> n2 | 2020 | **2069** | +2.4% |
  | n2 -> n1 | 4423 | **4462** | +0.9% |

- Jumbo 65521 on eth0/1/2 with 20000-byte DF pings to all three nodes.
- `_boot_after_reboot.sh` (now `scripts/vca2-boot.sh`) end-to-end: correct
  kernel guard, correct images, all three nodes to `net_device_ready`.

`7.0.2-6-pve` stays installed. Rolling back is
`proxmox-boot-tool kernel pin 7.0.2-6-pve && proxmox-boot-tool refresh` plus
a reboot.

**Per-kernel modules are the papercut.** `jumbo_mtu.ko` and `hostbuf.ko` are
built against the running kernel and are rejected on vermagic after a switch.
For `jumbo_mtu` that failure is silent and expensive — the card looks
healthy and node-to-node quietly runs at ~2.7 Gbit/s instead of ~16-26 — so
the boot script (`_boot_after_reboot.sh` then, `scripts/vca2-boot.sh` now)
detects the mismatch and rebuilds it automatically.

### The old Open 1 notes, kept for the record

Both kernels present the same `dma_map_ops` shape (`.map_phys`, no
`.map_page`), so the Linux 7.0 ABI change was never the discriminator — that
hypothesis was tested and falsified before this one was.

Measured on 7.0.14-17-pve, all three nodes booted:

| | 7.0.2-6-pve | 7.0.14-17-pve |
|---|---:|---:|
| `/sys/bus/vca_virtio/devices/` | 3 | **3** |
| host NICs | eth0/1/2 | **eth0/1/2** |
| jumbo 65521, DF 20000 to all nodes | OK | **OK** |
| DMA ring node2 -> node1 | 4515 / 4423 | **4581 / 4487 MiB/s** |
| VCA modules loaded | 17 | **17** |
| errors/warnings in dmesg | none | **none** |

`proxmox-boot-tool kernel pin <ver> --next-boot` is the right tool for this
test: it boots the other kernel exactly once and any later reboot returns to
the pinned one automatically, so a failure self-heals.

## ~~Open 2 — recover the local `plx87xx` patch~~ RECOVERED 2026-09-21

Done. The module's own DWARF `DW_AT_comp_dir` led back to the build tree,
and the patch — `plx_mbox.c`, `plx_mbox.h`, a `plx_debugfs.c` change and one
`Kbuild` line — is now in [`compute/plx_mbox/`](../compute/plx_mbox/).

**Fully closed.** A rebuild from only the committed artifacts reproduces
`srcversion C7DB21AFCC365566F4BFCAA` exactly, DKMS has been rebuilt and
reinstalled for **both** kernels, `dkms status` is clean, and the whole thing
survived a reboot. See [HOST_STACK.md](HOST_STACK.md).

## Open 3 — a newer kernel on the *nodes*

Still the risky one, and now better understood rather than closer.

The fork's `vcablk` blk-mq conversion is real (verified by review), which was
the blocker recorded in [NODE_IMAGE.md](NODE_IMAGE.md). The repo also ships a
node-side DKMS package. So the gate has plausibly moved.

What has **not** changed:

- `vcablkfe` *is* the node's root filesystem. If it fails the node does not
  boot, and there is no serial console exposed from the host — diagnosis
  means powering off, mounting the image and reading logs.
- A new node kernel means **rebuilding Intel's dracut initramfs** with the
  VCA modules in it. The image builder deliberately keeps Intel's ESP
  verbatim precisely to avoid this ([IMAGE_BUILDER.md](IMAGE_BUILDER.md)),
  so it is the single biggest change to the builder's assumptions.
- `BLOCKIO_FORCE_DMA_SYNC` is enabled in the fork, so the node root
  filesystem is already on synchronous DMA. Understand that before adding
  more variables.

Mitigations are unchanged: stock `node{0,1,2}.vcad` are never modified, one
node can be tried while two stay known-good, and node 0 holds nothing
precious.

~~**Do not start this before Open 1 is understood.**~~ Open 1 was answered
2026-09-21 (above), so this gate no longer applies.

## Open 4 — report back upstream

The author states plainly in the fork's own `VALIDATION.md` (in
`chainsx/intel-vca2-dkms`, not this repo) that the work is build-tested
only and has never run on a physical VCA2. It has — here, continuously,
since 2026-09-19, across a fairly demanding workload.

Worth sending, with the caveat that we validated *the port plus a local
`plx87xx` modification*:

- It works on real hardware on Linux 7.0: nodes boot, BlockIO roots, virtio
  networking carries jumbo frames, the PLX DMA engine sustains 4423 MiB/s
  node-to-node.
- The tag-set leak on the `vcablk_disk_create()` error path (HOST_STACK.md).
- Three undisclosed behaviour changes that deserve a line in the README:
  `BLOCKIO_FORCE_DMA_SYNC`, the `vop_common.c` error-path change, and the
  `vca_mgr_extd` sysfs rename.
- ~~That `7.0.14-17-pve` does not bring up virtio NICs while `7.0.2-6-pve`
  does.~~ **Retracted 2026-09-21:** it does bring them up; the nodes had not
  been booted (Open 1 above). Nothing to report.

## What would make the remaining work not worth doing

- ~~Open 1 turns out to be the local `plx87xx` patch rather than the kernel —
  then there is no 7.0.14 bug to fix, only a build to redo.~~ Moot: there was
  no 7.0.14 bug (Open 1).
- Any change costs the fabric throughput already measured. A newer kernel is
  not worth losing 4423 MiB/s node-to-node.
- The node-side initramfs rebuild turns out to be a project of its own.

The card is genuinely useful today: 22.04 userspace on every node image
(five nodes alive across two cards; node 3 dead since 2026-09-28), and,
measured on card 0, 1.36 TFLOP/s GPU, ~210 GFLOP/s CPU, 4423 MiB/s
node-to-node. Everything here is an
upside-only bet and should be dropped the moment it stops being one.
