<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Building a modern node image

The stock node image is **Ubuntu 16.04** with glibc 2.23, gcc 5.4 and
systemd 229 — roughly ten years old, and past end of standard support. That
toolchain is the root of most friction in this project: it is why `llama.cpp`
had to be cross-compiled with Zig to target glibc 2.23, why there is no
`ffmpeg`, and why nothing could use the OpenCL driver that was already on
disk.

**Ubuntu 22.04 runs on this card.** Built, booted and verified on **all
three nodes**, 2026-09-21, from one reproducible image. The builder is
profile-driven and targets other distros too — see
[IMAGE_BUILDER.md](IMAGE_BUILDER.md) for building, deploying and verifying.

## Why it is possible at all

The boot path lives entirely inside Intel's **dracut initramfs**
(`vca_initramfs.img` on the ESP). That image carries every VCA module —
`plx87xx`, `vcablkfe`, `vop`, `vca_virtio*` — brings up the NTB, mounts
`/dev/vcablk0p2` and `switch_root`s into it.

It is kernel-specific but **completely distro-independent**. It does not care
what is in the rootfs beyond `/sbin/init` existing. So:

> Keep Intel's kernel, initramfs and ESP **verbatim**. Replace only the root
> filesystem.

Boot then follows exactly the same path as the stock image.

## What you get

Measured on node 2 after boot:

| | stock 16.04 | this image |
|---|---|---|
| userspace | Ubuntu 16.04 | **Ubuntu 22.04 LTS** |
| gcc / glibc | 5.4 / 2.23 | **11 / 2.35** |
| systemd | 229 | **249** |
| OpenCL | 2.1 NEO (2018) | **3.0 NEO, 72 CUs** |
| VA-API | iHD 16.9 (2018) | **iHD 22.3.1** |
| ffmpeg | absent | **present, `vaapi` + `qsv`** |
| kernel | 4.14.20-vca | 4.14.20-vca *(unchanged)* |

Plus: VCA kernel headers installed, so `ring.ko`, `dmabench.ko` and friends
build **on the node**; container prerequisites auto-loaded; `vm.min_free_kbytes`
persisted.

## Measured impact (A/B on identical hardware)

Node 2 on 22.04 against nodes 0/1 on the stock 16.04 image. Same silicon,
**same 4.14 kernel**, same benchmark source — so every difference here is
userspace: compiler and OpenCL runtime.

### CPU: blocked OpenMP SGEMM (`compute/cpu_gemm.c`)

Re-measured 2026-09-21 with **two** nodes on 22.04, so the fast side is no
longer a single observation:

| n | node 0 (16.04 / gcc 5.4) | node 1 (22.04) | node 2 (22.04) | |
|---:|---:|---:|---:|---:|
| 1024 | 36.48 | **70.59** | **69.10** | **~1.9x** |
| 2048 | 32.38 | **53.75** | **55.66** | **~1.7x** |

**This is the headline.** A ~1.9x speedup from the compiler alone — gcc 11
auto-vectorises the inner loop into AVX2 FMA where gcc 5.4 does not. Every
result is verified against a reference dot product, so the work is real and
not optimised away.

> **Correction.** This table first recorded **83.04** at n=1024 for a 2.28x
> claim. That was one run on one node and it does not reproduce: re-running
> *the same binary* on *the same node* gave 69.10, and node 1 independently
> gave 70.59. Treat ~70 as the number. The n=2048 row held (1.65x recorded,
> ~1.7x on re-measure), which is the usual pattern — the smaller problem fits
> in cache and is noisier.

### GPU: tuned GEMM (`compute/gemm_fast.c`, identical kernel source)

| n | 16.04 / OpenCL 2.1 NEO | 22.04 / OpenCL 3.0 NEO | |
|---:|---:|---:|---:|
| 1024 | 400.4 | **413.4** | +3.2% |
| 1408 | 449.1 | **470.8** | +4.8% |
| 2048 | 411.1 | 408.6 | -0.6% |
| sustained 30 s | 455.3 | **474.0** | +4.1% |

Modest but consistent. No throttling on either image.

### What the asymmetry means

**The GPU was already near its ceiling; the CPU was leaving half its
performance on the floor.** The P580s were fine on the 2018 runtime. The
Xeons were being crippled by a 2016 compiler. Across three nodes that is
roughly **109 -> 210 GFLOP/s** of aggregate CPU capability for free.

It also changes the power-budget picture in [THERMAL.md](THERMAL.md): CPU and
GPU compete for the same 45 W package cap, and the `HETERO` experiment
concluded that giving the CPU work made things slower. At ~70 GFLOP/s instead
of 36, the CPU is a much better use of those watts than it appeared. **That
conclusion is worth re-testing on this image.**

Combined across three nodes at n=1408: **~1412 GFLOP/s GPU + ~210 GFLOP/s
CPU**.

### Media

`ffmpeg -hwaccels` on 22.04: `vdpau cuda vaapi qsv drm opencl`, with 9 H.264
encode entrypoints in `vainfo`. The stock image ships no ffmpeg at all.

### Fabric: unchanged, as expected

Node 1 was moved to 22.04 as well so node↔node paths could be re-measured with
both ends modern. **Nothing moved, and that is the correct result** — the
transport is `vca_virtio_net` and the PLX DMA engine, both in Intel's 4.14
kernel, which this image deliberately keeps.

| | 16.04 baseline | re-measured with 22.04 |
|---|---:|---:|
| DMA ring, node 0 → node 1 | 3812 MiB/s | **3801 MiB/s** |
| DMA serial, node 0 → node 1 | 3970 / 4832 | **3847 / 4853** |
| virtio-net node↔node | *(see below)* | same within noise |

Reproducing 3812 as 3801 on a rebuilt toolchain, a different distro and
freshly compiled modules is a useful check on the whole measurement rig.

The virtio number was confirmed distro-independent by running node 0's own
xenial `iperf3` binary on both 22.04 nodes — details in
[FABRIC.md](FABRIC.md). What *did* move node↔node IP was jumbo MTU, which is
a host-side change and was already in place.

So the scoreboard for this image is: **userspace CPU compute ~1.9x faster, fabric
unchanged, and kernel modules now buildable on the node itself.**

## Why jammy and not noble

Ubuntu 24.04 ships systemd 255, which expects kernel ≥ 4.15 features this
kernel does not have. **22.04 (systemd 249) is the newest that is safe on
4.14.** 20.04 is the conservative fallback.

## Building kernel modules on the node

This is the part that does not "just work", and both failures are misleading
enough to be worth writing down. Both are handled by
`compute/image/build_node_image.sh`; this section is for when you hit them on
an image that predates the fix, or on any other modern distro over an old
kernel.

### 1. No libelf → the build dies before it compiles anything

```
Makefile:961: *** "Cannot generate ORC metadata for CONFIG_UNWINDER_ORC=y,
                   please install libelf-dev ...".  Stop.
```

This kernel has `CONFIG_UNWINDER_ORC=y`, so objtool runs on every object and
objtool needs libelf. The stock 16.04 image happens to ship `libelf-dev`; a
minimal debootstrap ships only the runtime `libelf1`. Fix: install
`libelf-dev`. The image now does.

### 2. binutils 2.38 emits a relocation this loader has never heard of

Harder, because everything *looks* right:

```
$ make                      # succeeds, no errors
$ modinfo x.ko | grep vermagic
vermagic: 4.14.20-1.2.3.26.vca SMP mod_unload modversions     # exact match
$ insmod x.ko
insmod: ERROR: could not insert module x.ko: Invalid module format
$ dmesg | tail -1
module: x: Unknown rela relocation: 4
```

Relocation type 4 is `R_X86_64_PLT32`. **binutils ≥ 2.31 emits it where 2.26
emitted `R_X86_64_PC32`** for calls to global symbols. Upstream taught the
module loader to accept it in commit `b21ebf2fb4cd` ("x86: Treat
R_X86_64_PLT32 as R_X86_64_PC32", v4.16) — Intel's 4.14.20 is from early 2018
and predates it. Nothing about vermagic, gcc version or `CONFIG_MODVERSIONS`
is involved, which is why it sends you the wrong way.

A kernel module has no PLT, so the two relocations are computed identically.
The upstream fix is literally a one-line alias. `plt32-to-pc32` does the same
rewrite in the object file instead, which is the only end we can change:

```bash
make && plt32-to-pc32 *.ko && insmod ./x.ko
```

Source: `compute/image/plt32_to_pc32.py`, installed as `/usr/local/sbin/
plt32-to-pc32`. Typical counts for these modules are 27–44 relocations each.

**Verified working:** `alut_gmap.ko`, `n2n.ko` and `ring.ko` built on node 1
and node 2 with gcc 11 / binutils 2.38, patched, loaded, and used to drive the
full DMA matrix in [DMA.md](DMA.md) — including a new best node-to-node result
of 4423 MiB/s. Modules built this way are not second-class.

## Two things a modern distro breaks

### 1. The udev rule that applies the network config

The stock image applies the host-supplied `net_config` from a udev rule:

```
SUBSYSTEM=="net", ACTION=="add", KERNEL=="eth*", RUN+="/sbin/vca_eth_cfg.sh $name"
```

On systemd 249 that rule **does** fire — but the RUN program exits 1, because
modern `systemd-udevd` sandboxes and time-limits `RUN+=` programs and the
2016-era script does not survive it:

```
systemd-udevd[497]: eth0: Process '/sbin/vca_eth_cfg.sh eth0' failed with exit code 1.
```

With only the udev rule the node boots fine and sits at **`net_device_no_ip`
forever**. Replaced with a plain oneshot unit (`vca-netcfg.service`) which
works first time.

That unit **logs to `/var/log/vca-netcfg.log`** on purpose: there is no serial
console exposed from the host, so if bring-up fails, that file — read by
mounting the image afterwards — is the only way in.

### 2. The agent does not configure networking

`vca_agent.sh` only *reports* state; it never applies `net_config`. It waits
for an IP to appear and then flips the node to `net_device_ready`. Something
else has to do the actual configuration. Easy to misread when debugging.

## Checked and NOT possible — do not retry

- **`cma=` to lift the 4 MiB allocation limit.** `CONFIG_CMA=y` **but
  `CONFIG_DMA_CMA is not set`**, so `dma_alloc_coherent` will not use a CMA
  area. Verified against `/boot/config-4.14.20-1.2.3.26.vca`. A `memmap=`
  reservation is the only route, and `CONFIG_STRICT_DEVMEM=y` blocks
  `/dev/mem` access to it.
- **MaxReadReq > 128 B.** Hardwired; `setpci` writes are ignored.

## What Intel actually shipped

`intelvisualcomputeaccelerator_build_scripts_2.3.26.zip` contains the whole
image pipeline, GPL-2.0: `create_rootfs.sh`, `create_image.sh`,
`create_archive.sh`, grub configs, kickstart files and a debootstrap-based
bootstrap. The node image is a documented recipe, not a mystery blob.

The only gate on a newer distro is a hardcoded table in
`library_image_creation.sh`:

```bash
declare -gr -A _CONST_DISTROS=( ["16.04"]="xenial" ["16.04.3"]="xenial"
                                ["16.04.4"]="xenial" ["16.10"]="yakkety")
```

Anything else calls `die`. Everything downstream is already parameterised.
Our build script bypasses the pipeline entirely and debootstraps directly,
which is simpler.

## The kernel patch set (for a future newer kernel)

`intelvisualcomputeaccelerator_kernels_development_package_2.3.26.zip` holds
`vca_patches_kernel_4.14.20_2.3.26_src.tar.gz` — 16 patches. Most are not
code:

| Patches | Content |
|---|---|
| 0002, 0004, 0006, 0007, 0009, 0010, 0012 | `.config` only |
| 0003, 0008 | cosmetic / empty |
| 0011, 0013 | **12.5k lines of Media Server Studio i915 work** — unnecessary on a modern kernel, Skylake media is upstream |
| **0001, 0005, 0014, 0015, 0016** | **the real VCA delta, ~250 lines** |

The genuinely VCA-specific changes touch only `drivers/pci/quirks.c`,
`drivers/iommu/intel-iommu.c` and `net/core/dev.c`. Two are worth knowing:

**0016 explains the phantom requester ID** seen during the P2P probe
([DMA.md](DMA.md)):

```c
/* VCA NTB forwards PCIe traffic using multiple alien RIDs. They have to
 * be added as aliases to the DMA device in order to allow buffer access
 * when IOMMU is enabled. Following devfns have to match RIT-LUT table
 * programmed in the EEPROM. */
for (slot = 0; slot < 0x20; slot++)
    pci_add_dma_alias(pdev, PCI_DEVFN(slot, 0x0..0x4));
```

`05:02.3` is slot 2, function 3 — inside that range. The host runs a stock
Proxmox kernel without this quirk, which is why the IOMMU mapping had to be
added by hand.

**0005 explains jumbo**: Intel simply *deletes* the min/max validation from
`dev_set_mtu()`. That is why guests do 65521 natively and the stock host
refuses, needing `jumbo_mtu.ko`.

## What a newer *kernel* would take

Assessed, not attempted. The out-of-tree modules are 35k lines across 47 `.c`
files, and the API exposure is smaller than expected:

| Concern | Finding |
|---|---|
| `vringh` (400 hits) | **Self-contained fork.** Zero `#include <linux/vringh.h>`. No upstream dependency. |
| `kallsyms_lookup_name`, `ioremap_nocache`, `setup_timer`, `access_ok(VERIFY_*)`, `PDE_DATA` | **Absent.** |
| `do_gettimeofday`, `dma_zalloc_coherent` | 2 hits each — trivial. |
| **`vcablk` block driver** | **The gate.** Uses `blk_init_queue` (removed 5.0), `alloc_disk` (removed 5.15), `genhd.h` (removed 5.18) — the legacy request API. Needs a blk-mq conversion. |

`vcablkfe` *is* the root filesystem, so if it fails the node will not boot and
there is no console to debug from. That is the risk, not the line count.

## Usage

The builder is profile-driven and works for other distros too — the full
reference is [IMAGE_BUILDER.md](IMAGE_BUILDER.md). Quick version:

```bash
# on the host, as root
cd compute/image
./build_node_image.sh --list                  # what profiles exist, and which are proven
./build_node_image.sh --profile ubuntu-jammy  # ~20 min, ~750 MB sparse

# try it on one node (others keep their known-good images)
vcactl pwrbtn-short 0 2                   # wait for a SETTLED power_off
vcactl blockio close 0 2 vcablk0
vcactl blockio open  0 2 vcablk0 RW /tank-red/vmdata/vca/images/node-ubuntu-jammy.vcad
vcactl pwrbtn-short 0 2
vcactl wait-BIOS 0 2 && vcactl boot 0 2 vcablk0

# then confirm it is actually what it claims to be
ssh root@172.31.3.1 /opt/vca-tools/verify_node_image.sh
```

Rolling back is the same commands pointed at `node2.vcad`. The stock images
are never modified.

> **Read [GOTCHAS.md](GOTCHAS.md) on image swapping before doing this.**
> Getting the power/blockio sequence wrong wedged the host badly enough to
> need a power cycle.

Each image swap changes the node's SSH host key (it is a different OS), so
expect `REMOTE HOST IDENTIFICATION HAS CHANGED` and clear it with
`ssh-keygen -R 172.31.N.1`.
