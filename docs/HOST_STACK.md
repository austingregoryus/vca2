<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# The host driver stack — what is actually running

> **Read this before trusting any number in this repository.**
>
> The host does **not** run Intel's VCASS 2.3.26 modules. It runs a
> third-party forward-port, plus a local patch to its most important
> module (first hand-built; recovered byte-exact and DKMS-installed for both
> kernels on 2026-09-21, see below). Every measurement in these docs was produced on that
> stack. Established 2026-09-21 by direct inspection; the repo previously
> implied otherwise.

## What is installed

| | |
|---|---|
| Source | [`chainsx/intel-vca2-dkms`](https://github.com/chainsx/intel-vca2-dkms) at `edb3e51` ("add support for Linux 7.0", 2026-08-08) |
| Local checkout | `/usr/src/intel-vca2-dkms` — verified identical SHA to the public repo's `main` |
| Packages | `daemon-vca`, `vca2-host`, `vca2-vcass-modules-dkms`, all `2.3.26+ubuntu24.04.10`, all `Maintainer: Local VCA Maintainer <root@localhost>` — locally built, never shipped by Intel |
| Built for | `7.0.2-6-pve` (Sep 19 14:31) **and** `7.0.14-17-pve` (Sep 19 19:36), 17 modules each |
| Running | `7.0.14-17-pve`, pinned via `proxmox-boot-tool` since 2026-09-21; `7.0.2-6-pve` stays installed as the fallback ([KERNEL_PORT.md](KERNEL_PORT.md)) |

No trace of a genuine Intel install exists anywhere on the host — no
`/opt/intel/vca*`, no rpm remnants, no vendor `.ko` outside the DKMS tree.

## Why Intel's stock driver was never an option here

Both installed host kernels define `struct dma_map_ops` with `.map_phys` /
`.unmap_phys` and **no** `.map_page` / `.unmap_page`.

`plx87xx` does not merely *call* the DMA API — it **provides** a
`dma_map_ops` table, redirecting the synthetic VOP device's DMA onto the real
PLX PCI device. A designated initializer for a struct member that no longer
exists (`.map_page = fn`) is a hard compile error. So stock `plx87xx` cannot
build against either kernel on this machine.

**Scope matters here, and an earlier version of this claim overstated it.**
`dma_map_page()` survives as a *public API* that dispatches to `map_phys`
internally, so ordinary DMA **consumers** — `vop`, `vca_virtio`, `blockio` —
are unaffected by the rename and would still compile. Only the module that
supplies the ops table is blocked. That is enough, because it is the core
driver, but the rest of the stack is not implicated.

## The reproducibility gap — the important part

> **Closed 2026-09-21** — see [Verified and closed](#verified-and-closed-2026-09-21)
> below. This section records the state as first found.

**The running `plx87xx.ko` is not the module DKMS built.**

```
srcversion   dkms record : 0F994D23ABC944D13CD8204    271,101 bytes  (Sep 19)
             installed   : C7DB21AFCC365566F4BFCAA  4,434,168 bytes  (Sep 20)

undefined symbols present ONLY in the installed module:
    misc_register, misc_deregister, dma_mmap_attrs, snprintf
```

A different `srcversion` means different **source**, not just different build
flags. `misc_register` + `dma_mmap_attrs` is a character device exposing DMA
memory to userspace via `mmap` — which matches the "experimental `plx87xx`,
mbox on all three NTBs" note in [STATUS.md](STATUS.md).

The other **16 modules match DKMS byte-for-byte**. Only this one was rebuilt
and installed by hand, outside `dkms install`, on 2026-09-20. `dkms status`
flags it as `(Differences between built and installed modules)`.

Two consequences:

- **This host cannot be reproduced** from the public repo plus this one. The
  patch that produced the running module is not in either.
- **A kernel update would silently revert it.** `dkms autoinstall` would
  install the 271 KB stock build over it, losing the misc device and
  whatever else differs.

This was the same failure that hand-patching the nodes produced
([IMAGE_BUILDER.md](IMAGE_BUILDER.md)), one layer down and older.

### Recovered, 2026-09-21

**The patch is now in source control** at
[`compute/plx_mbox/`](../compute/plx_mbox/).

The module was built with `-g -gdwarf-5`, and its DWARF `DW_AT_comp_dir`
pointed straight at the build tree, which still existed:
`/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10`. The debug info that made the
module suspiciously large is what made it recoverable.

It is three pieces on top of upstream:

- `plx_mbox.c` — a host ALUT shared-memory mailbox, **compiled into
  `plx87xx`** rather than built as its own module. That is why it changed
  `plx87xx`'s `srcversion` instead of appearing as a separate `.ko`.
- `plx_mbox.h`
- a patch to `plx_debugfs.c` adding `mbox_info` (0444) and `mbox_alloc`
  (0200), plus `plx_mbox_remove()` on teardown — exactly the "`mbox_*`
  debugfs" marker the docs use to tell the experimental build from the
  backup one.

Plus one line in the top-level `Kbuild`:
`plx87xx-objs += vca/plx87xx/plx_mbox.o`.

Files are kept **byte-exact** (no SPDX header added) so that a rebuild
should reproduce `srcversion C7DB21AFCC365566F4BFCAA` and thereby prove the
recovery is complete. Rollback to pristine upstream is
`/root/plx87xx.ko.bak` on the host, which matches the DKMS record
byte-for-byte.

### Verified and closed, 2026-09-21

**The recovery is byte-complete.** Rebuilding from *only* the committed
artifacts on a pristine copy of upstream reproduces the running module's
source hash exactly:

```
rebuilt from committed files : C7DB21AFCC365566F4BFCAA
currently RUNNING            : C7DB21AFCC365566F4BFCAA   -> MATCH
dkms record (upstream only)  : 0F994D23ABC944D13CD8204
```

**The DKMS record is fixed.** `dkms build` + `dkms install --force` for
`7.0.2-6-pve` **and** `7.0.14-17-pve`; both now produce
`C7DB21AFCC365566F4BFCAA` and `dkms status` no longer reports a mismatch. A
kernel update can no longer silently revert the mailbox.

**Verified across a reboot.** The host was rebooted and came back with
`plx87xx` loaded from the DKMS install at the correct `srcversion`, six
`mbox_*` debugfs entries present, and all three nodes `net_device_ready`.

The old hand-built module is preserved at
`/root/plx87xx.ko.handbuilt-20260921`; pristine upstream remains at
`/root/plx87xx.ko.bak`.

> **Build gotcha:** `plx_mbox.c` does not include its own header, so a plain
> `make` fails with `-Werror=missing-prototypes` on `plx_mbox_remove`.
> `dkms.conf` builds with `KERNWARNFLAGS=` (warnings cleared), which is how
> it compiled originally. Use the `dkms.conf` invocation, not a hand-rolled
> one.

## Is the port any good? — independent review

Reviewed 2026-09-21 by diffing every changed file against Intel 2.3.26.
**Yes, it is substantive and competent**, and the author fixed several real
things they never claimed. But there are undisclosed behavioural changes that
matter, and they are running on this card now.

### The blk-mq conversion is genuine

Not `#ifdef` cosmetics. `blk_peek_request` / `blk_start_request` /
`__blk_end_request_all` are **absent from the entire fork tree**; there is a
real `blk_mq_ops.queue_rq`, a properly initialised and freed
`blk_mq_tag_set`, `blk_mq_alloc_disk` with the correct 3-arg (≥6.9) vs 2-arg
split, and correct `blk_status_t` translation.

Ordering rules are respected: `blk_mq_start_request()` is called *before*
publishing to the ring and *not* on the `BLK_STS_RESOURCE` path. The
`disk->minors = VCA_BLK_MINORS` line is load-bearing — `device_add_disk()`
does `if (disk->major) WARN_ON(!disk->minors)`, and `blk_mq_alloc_disk()`
leaves it 0, so without it the disk never appears. That is real porting
knowledge, not search-and-replace.

### Undisclosed changes worth interrogating before trusting it

Ranked. The first two are in data paths this project uses constantly.

**1. `vcablk_bcknd_disk.c:43` — `#define BLOCKIO_FORCE_DMA_SYNC` was
uncommented.** This is a **data-path switch, not a port**. It moves the
BlockIO backend from asynchronous DMA to synchronous DMA, serialising every
chunk in the transfer thread and leaving the async path as dead code. Expect
a throughput cost on the node root filesystem. The fork never mentions it.
The obvious question to ask upstream is *why* — if the answer is "the async
path is broken", that is important in itself.

**2. `vop_common.c:1133` — a semantic change to VOP error recovery, presented
as a brace cleanup.** Intel's original had misleading indentation: the
`if (err) transfer_done(item);` was **not** inside the preceding
`if (!item->status_ready)`. So whenever `vop_wait_for_completion()` returned
`-EBUSY` (the timeout it explicitly logs), an item with `status_ready == true`
had `transfer_done()` called on it — which unconditionally resets the ring
item and clears its in-use flag. The fork confines both branches. Almost
certainly the intended code, but it changes error recovery in the
virtio-net-over-PCIe hot path and needs hardware validation, not a code read.

**3. `vca_mgr_extd_bus.c:198` — userspace-visible sysfs rename.**
`"vca_mgr_extd-dev%u"` → `"vca_mgr_extd-%s-dev%u"`. `ida_simple_get()` already
guaranteed uniqueness, so this is not a collision fix. Anything matching the
old name (udev rules, scripts) breaks at runtime only.

**4. `vca_virtio_net.c:37` — `VCA_VIRTIO_NET_DISABLE_CPUHP`.** Registering the
fixed `CPUHP_VIRT_NET_ONLINE` state collides with in-tree `virtio_net`, so the
fork replaced hotplug notifiers with a one-shot `virtnet_set_affinity()` at
probe. Queue/IRQ affinity is never re-applied on CPU online/offline.
Degraded performance, not a crash. `CPUHP_AP_ONLINE_DYN` would have been the
correct fix.

**5. `vcablk_disk.c:702` — tag-set leak the fork introduced.**
`vcablk_disk_stop()` early-returns on `!dev->gdisk` *before*
`blk_mq_free_tag_set()`. If `blk_mq_alloc_tag_set()` succeeds and
`blk_mq_alloc_disk()` then fails, ~2047 preallocated requests leak per failed
create. Straightforward fix; worth reporting upstream.

**6. `plx_lbp.c:958` — `fallthrough;` blesses a pre-existing bug.** Setting
`i7_cmd_timeout_ms` also overwrites `i7_mac_write_timeout_ms`. Intel's
original had the same missing `break`; the annotation was added to satisfy
`-Wimplicit-fallthrough` under `-Werror`, converting a compiler warning into
sanctioned behaviour.

**7. The Linux 7.x branches are unverifiable by code reading** and untestable
except on this hardware. Treat "7.x support" as unvalidated — which is
precisely the gap this card can close.

### Real bugs the fork fixed and did not claim

- `req_op(req) & (REQ_OP_FLUSH | REQ_SYNC | ...)` — `req_op()` returns a small
  enum, so this was effectively `req_op(req) & 2`: it misclassified DISCARD as
  sync and missed every real `REQ_SYNC`/`REQ_FUA`. Now
  `req_op(req) == REQ_OP_FLUSH || (req->cmd_flags & (...))`.
- **Passthrough requests are now rejected.** The original would have run
  `blk_rq_pos()` on an `SG_IO`/`REQ_OP_DRV_*` request and **written to sector
  0**.
- `plx_main.c`: `vca_mgr_extd_unregister_device()` is now actually called —
  the original registered the device and never unregistered it. `vop` is no
  longer leaked when the handshake never completes.
- `pci_map_single(phys_to_virt(page_to_phys(page)))` → `dma_map_page()`, a
  genuine highmem-safety fix.
- `memcpy_toio` → `memcpy` on a `vmalloc()` buffer; the read direction in the
  original was outright wrong (`memcpy_toio` where `memcpy_fromio` was meant).

### Claims made earlier in this project that the review corrected

Recorded because the errors are instructive:

- **"Mutating `req->bio->bi_iter.bi_sector` was a real latent bug"** —
  *oversold*. `rq_for_each_segment` copies `bi_iter` at inner-loop entry, so
  mutation cannot corrupt iteration; nothing in the driver reads the field
  afterwards. It is an API violation with no functional consequence here.
  Correct to fix, wrong to call a latent data-corruption bug.
- **"`kfree` after a failed `device_register()` is a double-free"** —
  *mischaracterised*. It is a **leak plus a latent use-after-free**: the
  `dev_set_name()` string leaks unconditionally because `kobject_cleanup()`
  never runs, and any reference taken before the failure dangles. Nothing
  double-frees. The fix (`put_device()`) is correct and was applied
  consistently across all five buses.
- **"`BLK_STS_RESOURCE` with no queue kick risks a stall"** — *refuted*. The
  branch is unreachable by construction: `queue_depth = REQ_RING_SIZE - 1 =
  2047` equals the ring's usable capacity, so it cannot fill. And blk-mq
  re-runs the queue itself (3 ms delay) on `BLK_STS_RESOURCE`; only
  `BLK_STS_DEV_RESOURCE` requires a driver-side restart. No hazard.

### Verified clean

`do_exit()` removals are mandatory and strictly better (it was returning a
meaningless value to `kthread_stop()`). Every `static` / `__maybe_unused`
addition was checked against all call sites across the tree. The
`vop_debugfs.c` NULL-check removals are tautologies on flexible array members
and fixed arrays, not regressions. `plx87xx_dma/` is byte-identical to
Intel's. The compat header is force-included via `subdir-ccflags-y -include`,
so surviving `ACCESS_ONCE` uses resolve correctly.

## What this means for the project

- We are the **hardware validation this port does not have**. Its own
  `VALIDATION.md` (in the `chainsx/intel-vca2-dkms` repo, not this one) says
  it is build-tested only; it has in fact been driving card 0 continuously
  since 2026-09-19, and the second card since 2026-09-26.
- Anything reported upstream should name the local `plx87xx` patch, because
  strictly we have validated *the port plus a modification*.
- The three undisclosed changes above are worth raising with the author
  regardless.

See [KERNEL_PORT.md](KERNEL_PORT.md) for what is still open.
