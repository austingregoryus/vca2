<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# The node image builder

`compute/image/build_node_image.sh` builds a bootable VCA 2 node image with a
modern Linux userspace, from a **distro profile**. It is not specific to
Ubuntu 22.04 — that is just the profile that has been proven on hardware.

For what a modern image *buys you* (measured), see
[NODE_IMAGE.md](NODE_IMAGE.md). This file is about the builder itself.

## Why a profile-driven builder is even possible

The boot path lives entirely inside Intel's **dracut initramfs**
(`vca_initramfs.img` on the ESP). It carries every VCA module — `plx87xx`,
`vcablkfe`, `vop`, `vca_virtio*` — brings up the NTB, mounts
`/dev/vcablk0p2` and `switch_root`s into it.

That initramfs is **kernel-specific but completely distro-independent**. It
does not care what is in the rootfs beyond `/sbin/init` existing.

> Keep Intel's kernel, initramfs and ESP **verbatim**. Replace only the root
> filesystem.

That one property is what turns "port a distro to this card" into "run
debootstrap and copy some files in". Everything else in this document is
detail.

## Usage

```bash
# on the Proxmox host, as root
./build_node_image.sh --list
./build_node_image.sh --profile ubuntu-jammy
./build_node_image.sh --profile debian-bookworm --out /path/to/img.vcad
```

Roughly 20 minutes and ~750 MB sparse for a first build. The debootstrap
rootfs is cached per profile at `$WORK/rootfs-<profile>/`; delete that
directory to force a clean bootstrap.

It only ever **reads** the Intel reference image and **writes** the output
file. `node{0,1,2}.vcad` are never touched, so rolling back is just pointing
a node at its old file again.

## Layout

```
compute/image/
  build_node_image.sh      the driver
  deploy_node_image.sh     safely puts a built image on one node
  verify_node_image.sh     runs ON a node, checks the image is what it claims
  plt32_to_pc32.py         installed as /usr/local/sbin/plt32-to-pc32
  profiles/*.conf          what distro, which mirrors, which packages
  overlay/common/          files dropped into every rootfs, whatever the distro
```

The **overlay** is the VCA-specific layer. It is a real directory tree rather
than a pile of heredocs so that it is reviewable as files, diffable, and
provably identical across every profile:

| Path | Why |
|---|---|
| `usr/local/sbin/vca-netcfg.sh` | applies the host's `net_config`; replaces the udev rule modern systemd breaks |
| `etc/systemd/system/vca-netcfg.service` | runs it, with a timeout long enough for the retry loop |
| `etc/sysctl.d/60-vca.conf` | persists `vm.min_free_kbytes` |
| `etc/modules-load.d/vca-extras.conf` | container prerequisites (all `=m`, just never loaded) |
| `etc/udev/rules.d/97-eth_up.rules` | kept for parity with Intel's layout; does not actually work on systemd ≥ 249 |
| `etc/vca-notes.txt` | the "checked, not possible, do not retry" list, on the node itself |

## Adding a distro

Copy a profile and change the fields. Nothing in the driver is
Ubuntu-specific.

```bash
cp profiles/ubuntu-jammy.conf profiles/ubuntu-plucky.conf
$EDITOR profiles/ubuntu-plucky.conf
./build_node_image.sh --profile ubuntu-plucky
```

Fields:

| Field | Notes |
|---|---|
| `DISTRO`, `SUITE` | `debootstrap` suite name |
| `SUPPORT` | `proven` \| `untested` \| `expected-broken` — see below |
| `DESCRIPTION` | one line, shown by `--list` |
| `MIRROR`, `COMPONENTS` | `main restricted universe multiverse` for Ubuntu; `main contrib non-free non-free-firmware` for Debian 12 |
| `SECURITY_MIRROR`, `SECURITY_SUITE` | **these differ by distro.** Ubuntu serves security from the same host; Debian uses `security.debian.org` |
| `UPDATES_SUITE` | `<suite>-updates` on both |
| `KEYRING` | archive keyring for `debootstrap` |
| `PKGS_BASE` | comma-separated, passed to `debootstrap --include` |
| `PKGS_TOOLCHAIN` | space-separated; **must include `libelf-dev`** |
| `PKGS_OPENCL`, `PKGS_OPENCL_RUNTIME` | loader is required, runtime may fail and fall back |
| `PKGS_MEDIA`, `PKGS_MEDIA_FALLBACK` | second is tried if the first fails |

### The `SUPPORT` field

This exists so the repo cannot quietly imply a profile works when nobody has
booted it.

- **`proven`** — built, booted and benchmarked on this hardware. Only
  `ubuntu-jammy` is.
- **`untested`** — complete and plausible, never built here. The builder
  prints a notice and continues. Do not quote numbers from one of these
  without measuring.
- **`expected-broken`** — there is a specific known reason it should fail,
  written at the top of the profile. The builder **refuses** unless you pass
  `--force-support`.

`ubuntu-noble` is `expected-broken`: 24.04 ships systemd 255, and systemd
dropped support for kernels older than 4.15 in v254. This card is permanently
on 4.14.20 — the kernel cannot move without porting the out-of-tree `vcablk`
block driver off the legacy request API, and that driver *is* the root
filesystem. See [NODE_IMAGE.md](NODE_IMAGE.md).

## Two things every profile must carry

Both were discovered the hard way and both are invisible until a module
build fails.

**1. `libelf-dev`.** This kernel is `CONFIG_UNWINDER_ORC=y`, so objtool runs
on every object and needs libelf. Without it, module builds die before
compiling a line:

```
Makefile:961: *** "Cannot generate ORC metadata for CONFIG_UNWINDER_ORC=y,
                   please install libelf-dev ...".  Stop.
```

The stock 16.04 image happens to ship it; a minimal debootstrap does not.

**2. `plt32-to-pc32`.** Modern binutils (≥ 2.31) emits `R_X86_64_PLT32` where
2.26 emitted `R_X86_64_PC32`. Upstream taught the module loader to accept it
in v4.16 (`b21ebf2fb4cd`); Intel's 4.14.20 predates that. The result is a
module that compiles cleanly, matches vermagic exactly, and still refuses to
load:

```
insmod: ERROR: could not insert module x.ko: Invalid module format
dmesg:  module: x: Unknown rela relocation: 4
```

Run `plt32-to-pc32 *.ko` after `make`. A module has no PLT, so the two
relocation types are computed identically — this is the same rewrite upstream
does, applied to the object instead of the loader.

The builder **fails hard** if `plt32_to_pc32.py` is missing, rather than
warning. A warning here produces an image where nothing can build modules and
nobody finds out for a week.

## `/etc/vca-image-release`

Every built image records what it is:

```
VCA_IMAGE_PROFILE=ubuntu-jammy
VCA_IMAGE_DISTRO=ubuntu
VCA_IMAGE_SUITE=jammy
VCA_IMAGE_SUPPORT=proven
VCA_IMAGE_KERNEL=4.14.20-1.2.3.26.vca
VCA_IMAGE_BUILT=2026-09-21T22:03:11Z
VCA_IMAGE_BUILDER_GIT=663d3c5
VCA_IMAGE_HOST=vca-host
VCA_IMAGE_REF=vca_disk_24gb_reference_k4.14.20_ubuntu16.04_2.3.26.vcad
```

**This is not decoration.** It exists because of a specific failure: on
2026-09-21 two nodes were hand-patched live — `libelf-dev` installed via
`dpkg -i`, `plt32-to-pc32` copied in by `scp`, `iperf3` added — to finish a
benchmark, and nothing recorded that they no longer matched the build script.
A node with no `VCA_IMAGE_BUILDER_GIT` is a node whose configuration is
unknown.

## Verification

Two layers, because "the build succeeded" and "the image works" are different
claims.

**Build-time self-check** — the builder asserts against the finished
filesystem before it will call the image good: `/sbin/init`,
`/etc/vca-image-release`, both `/usr/local/sbin` tools, both wanted-unit
symlinks, the kernel `build` symlink, `/usr/include/libelf.h`, `/etc/fstab`,
and `vca_initramfs.img` on the ESP. Any miss is fatal — it refuses to hand
you an image it knows is broken.

**Run-time verification** — ships inside the image:

```bash
ssh root@172.31.N.1 /opt/vca-tools/verify_node_image.sh
```

It checks identity, toolchain, network bring-up, OpenCL, media and tuning —
and, crucially, **actually builds a kernel module, rewrites its relocations
and loads it**. Everything else is necessary; only that is sufficient. Exit
0 means the image is what it claims to be.

## Deploying

Use `deploy_node_image.sh`. It encodes the whole sequence and all three traps:

```bash
./deploy_node_image.sh --node 1 --master /tank-red/vmdata/vca/images/node-ubuntu-jammy.vcad --verify
# second card: --card 1 --node 0..2  ->  node3..5, 172.31.4-6.1
./deploy_node_image.sh --card 1 --node 0 --master /tank-red/vmdata/vca/images/node-ubuntu-jammy.vcad --verify
```

### The master is a template. Nothing boots it.

**BlockIO opens an image read-write.** Point two nodes at one file and two
kernels write one ext4 — guaranteed corruption. Point a *node* at the
builder's output and the next build writes into a disk a node has mounted.

So the build output is a **golden master** and every node gets its own copy
(`node0-ubuntu-jammy.vcad`, `node1-…`, `node2-…`). The deploy script
**refuses to run** if the master is attached anywhere.

On ZFS with `feature@block_cloning` — check with
`zpool get feature@block_cloning <pool>` — the copy is a `cp --reflink=always`:
instant, and it shares blocks with the master rather than duplicating them.
Anywhere else it falls back to a sparse copy. This is the one good idea taken
from `spoto-team/vca2-control-panel` ([PRIOR_ART.md](PRIOR_ART.md)), which
does the same thing with btrfs.

### The two power traps

- **`pwrbtn-short` is a toggle, and a press during `powering_down` is eaten.**
  `link_down` is **not** off — it is on the way down. Press then and nothing
  visible happens; you sit watching `power_off` wondering why the node will
  not start. Wait for a *settled* `power_off`. This bit during this very
  deployment.
- Each image swap changes the node's SSH host key, so expect
  `REMOTE HOST IDENTIFICATION HAS CHANGED`. The script clears it.

Roll back by pointing a node at `nodeN.vcad`. The stock images are never
modified.

## DNS on the nodes

Worth its own section, because the failure is a good trap.

Nodes route off-card fine — the host masquerades `172.31.0.0/16` out of
`vmbr0`, so a node can ping `1.1.1.1` and anything on the LAN. But **names**
did not resolve, and `apt` on a node therefore failed. That is what pushes
you into fetching `.deb` files on the host and hand-patching nodes, which is
exactly what this builder exists to prevent.

The cause was not routing and not the firewall. Ubuntu ships
`/etc/resolv.conf` as a symlink:

```
/etc/resolv.conf -> ../run/systemd/resolve/stub-resolv.conf
```

The builder deliberately **disables `systemd-resolved`** — nothing should race
the VCA agent for `eth0` — so that stub file is never created and the symlink
dangles. Perfect routing, zero name resolution, and an `-e` test on the build
host still passes because the symlink itself exists.

The builder now writes a **regular file**, and both the build-time self-check
and the on-node verifier specifically assert it is not a symlink.

Nameservers default to whatever the **build host** uses
(`awk '/^nameserver/' /etc/resolv.conf`), which is almost always correct: the
host is the nodes' default gateway and already masquerades for them, so
anything it can resolve, they can. Deriving it also keeps site-specific
addresses out of this repository. Override explicitly if you need to:

```bash
NODE_DNS="10.0.0.1 10.0.0.2" NODE_DNS_SEARCH=lan ./build_node_image.sh -p ubuntu-jammy
```

The value used is recorded in `/etc/vca-image-release` as `VCA_IMAGE_DNS`.

With this working, **`apt install` works on a node**, which removes the whole
reason to ever patch one by hand.

## Known gaps

- The builder assumes the Intel reference image is present and is the only
  source of the kernel, headers and VCA userspace.
- `ubuntu-focal` and `debian-bookworm` are written but unbuilt.
- The rootfs cache is keyed on the profile name, not on the package lists.
  Change `PKGS_*` in a profile and you must delete
  `$WORK/rootfs-<profile>/` to get a clean bootstrap — otherwise only the
  chroot stage re-runs. That is usually what you want (it makes rebuilds
  fast), but it is a footgun if you edit `PKGS_BASE`.
