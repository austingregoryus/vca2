<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Prior art — other people working on the VCA 2

> **Status, corrected 2026-09-21.** This file began as "notes on third-party
> work, none of it run on this card". That was wrong about the first entry:
> **`chainsx/intel-vca2-dkms` has been driving this card since 2026-09-19**
> and produced every measurement in this repository. The other two entries
> remain assessments from reading, not measurement. Where a repo states its
> own limitations, those are quoted rather than paraphrased.

Surveyed 2026-09-21. There is very little public VCA2 work, so it is worth
knowing what exists.

---

## `chainsx/intel-vca2-dkms` — **we are already running it**

> **Correction, 2026-09-21.** This section originally described the repo as
> promising-but-unvalidated work we might one day try. It was **already
> installed on this host** and had been driving the card since 2026-09-19.
> Every number in this repository was measured on it. Full detail, including
> an independent review of the diff, is in [HOST_STACK.md](HOST_STACK.md).

**Intel's 2.3.26 drivers, forward-ported to modern kernels and DKMS-packaged
for Ubuntu 24.04.** GPL-2.0, same provenance as the `src/` tree these docs
cite (Intel's VCASS 2.3.26 sources, unpacked locally and not redistributed
here; see [SOURCE_MAP.md](SOURCE_MAP.md)).

Builds three Debian packages: DKMS kernel modules, userspace control
programs, and a metapackage. Layout is `apps/`, `debian/`, `modules/`, plus
`README.md`, `NODE_DKMS.md`, `VALIDATION.md`, `build_debs.sh`.

### What it claims to fix

| Kernel | API change handled |
|---|---|
| 6.11 | `struct bus_type.match` takes a const driver pointer |
| 6.14 | `device_find_child()` callback constified; `BLK_MQ_F_SHOULD_MERGE` removed |
| 6.18 | IDA allocator API |
| **7.0** | page-based `dma_map_ops` callbacks replaced with **physical-address** variants |

Selected at compile time, so one tree covers several kernel lines. It builds
**both roles**: 18 host-role and 15 node-role modules, including `vcablkfe`
and `vcablk_bckend`.

### Their caveat — which we can now answer

`VALIDATION.md` is refreshingly direct:

> "This does **not** establish runtime compatibility on a physical VCA2 node."

It is **build-tested only** — 15 node-role and 18 host-role modules compiled
against Linux 7.0.0-28-generic headers, plus shell syntax checks. They state
that node-image boot, CSA/VOP handshake, BlockIO, network and reboot
validation on target hardware **did not happen**.

Credit for saying so — and **it now has.** All of those paths have been
exercised continuously on this card since 2026-09-19: nodes boot, BlockIO
roots them, virtio networking carries jumbo frames, and the PLX DMA engine
sustains 4423 MiB/s node-to-node. With one honest caveat: the running
`plx87xx.ko` is locally patched, so strictly we have validated *the port plus
a modification*. See [HOST_STACK.md](HOST_STACK.md).

Also flagged in their README: **do not load `vca_csa` on the host**, and the
node-side package does not by itself make Ubuntu bootable — it has to be
integrated into a node image.

### Two hypotheses, since resolved

**1. "It might unpin the host kernel." — FALSIFIED as stated.** The guess was
that their Linux 7.0 `dma_map_ops` work explained why virtio NICs vanish on
`7.0.14-17-pve`. It does not: **both** installed kernels present the same
`.map_phys` shape with no `.map_page`, so the ABI change cannot be what
distinguishes the working kernel from the broken one. The modules are built
and installed for 7.0.14 too. ~~Cause still unknown — now the sharpest open
question.~~ **Resolved 2026-09-21:** the NICs do not vanish on 7.0.14; the
nodes had not been booted when it was observed, and `7.0.14-17-pve` is now the
pinned kernel ([KERNEL_PORT.md](KERNEL_PORT.md) Open 1).

**2. "It might remove the gate on a newer node kernel." — SUPPORTED.**
[NODE_IMAGE.md](NODE_IMAGE.md) assessed `vcablk`'s legacy request API as
*the* blocker. Independent review confirms the blk-mq conversion is genuine
and structural, not `#ifdef` cosmetics ([HOST_STACK.md](HOST_STACK.md)). The
gate has plausibly moved — though a new node kernel still requires
rebuilding Intel's dracut initramfs, which is its own project.

**We had the hardware and did not realise we were already using their work.**
What remains is to report back. (An earlier version also listed working out
why `7.0.14-17-pve` failed to bring up virtio NICs; that failure was retracted
on 2026-09-21 and is not an upstream issue.) See
[KERNEL_PORT.md](KERNEL_PORT.md).

---

## `spoto-team/vca2-control-panel` — one idea worth taking

A Python/Flask web UI wrapping `vcactl`, with a systemd unit. Power
button (short/long), RAMDisk vs BlockIO boot selection, block device
attach/detach, network status, node status visualisation.

The UI itself is redundant for us — we drive `vcactl` directly and scripted.

**The idea worth stealing: copy-on-write image management.** They use btrfs
COW so node images are cheap to clone. We make full sparse copies (~750 MB
each, and there are already five in `images/`). We are on **ZFS**, so
`cp --reflink=always` or a `zfs clone` would make image provisioning
effectively instant and near-free.

> **Tried, and it works.** `tank-red` has `feature@block_cloning` **active**,
> so `cp --reflink=always` succeeds and each node's image shares blocks with
> the golden master. This is now how `deploy_node_image.sh` provisions nodes
> ([IMAGE_BUILDER.md](IMAGE_BUILDER.md)). It falls back to a sparse copy
> where reflink is unavailable, since `--reflink=always` fails loudly rather
> than silently degrading.

Their README also documents **CentOS 7.6** as the host OS, which is a useful
reminder that Intel's supported host was never Proxmox. That is context for
why host kernel compatibility keeps biting.

---

## `Lulu-Grant/IntelVCA2` — documentation, defers elsewhere

A bilingual (Chinese/English) documentation repo: hardware overview,
compatibility, host deployment, node images, boot modes, networking,
troubleshooting. It explicitly defers to the "official materials package"
(distributed via Mega) for actual drivers, images and commands.

Little here we do not already have — we have VCASS 2.3.26 and have gone
considerably further on measurement.

One fact worth recording: it describes official node images for **Ubuntu,
CentOS, Debian and Windows**. Multi-distro node images were always a
supported concept, which is mild external validation that the profile-driven
builder in [IMAGE_BUILDER.md](IMAGE_BUILDER.md) is a normal shape for this
hardware rather than something exotic.

---

## Summary

| Repo | Verdict |
|---|---|
| `chainsx/intel-vca2-dkms` | **Already in production here.** Modern-kernel forward-port, GPL-2.0, honest about being build-tested only — and this card is the hardware validation it lacks. See [HOST_STACK.md](HOST_STACK.md). |
| `spoto-team/vca2-control-panel` | COW-image idea **adopted** (ZFS reflink); UI skipped. |
| `Lulu-Grant/IntelVCA2` | Nothing actionable. |
