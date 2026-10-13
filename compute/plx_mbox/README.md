<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# `plx_mbox` — the local patch to the host `plx87xx` driver

**Recovered 2026-09-21 from the host's build tree.** Until then this code
existed only as a compiled `.ko` on one machine and in no version control
anywhere. See [`docs/HOST_STACK.md`](../../docs/HOST_STACK.md) for how that
happened and why it mattered.

## What it is

A host-side ALUT shared-memory mailbox, **compiled into `plx87xx` itself**
rather than built as a separate module. It allocates one A-LUT segment of
DMA-coherent memory so the card can see it, and exposes it through debugfs
and a misc character device.

Its own header comment:

> Host ALUT shared-memory mailbox. Compiled into plx87xx. Allocates one
> A-LUT segment (32 MiB on node 0/1) of DMA memory so the card can see it.
> Does NOT touch DMA debugfs. Polling only (no doorbell).

The "does NOT touch DMA debugfs" note is load-bearing — walking
`plx87xx_dma` debugfs is what oopsed the host early in this project
([`docs/GOTCHAS.md`](../../docs/GOTCHAS.md)).

## Why this matters more than it looks

This patch is the reason the running `plx87xx.ko` differs from the one DKMS
builds:

| | srcversion | size |
|---|---|---:|
| DKMS build (upstream only) | `0F994D23ABC944D13CD8204` | 271,101 |
| **running** (upstream + this) | `C7DB21AFCC365566F4BFCAA` | 4,434,168 |

The extra undefined symbols in the running module — `misc_register`,
`misc_deregister`, `dma_mmap_attrs` — are all from this file. The size
difference is mostly debug info (`-g -gdwarf-5`); it was that debug info's
`DW_AT_comp_dir` that led back to the build tree and made recovery possible.

**A `dkms autoinstall`, triggered by any kernel update, silently replaces the
running module with the upstream-only build** and this mailbox disappears.
`vcactl` and the nodes keep working; the `mbox_*` debugfs files just stop
existing. That is the "backup `plx87xx` (no `mbox_*` debugfs)" state
referred to elsewhere in the docs.

## The patch

Three pieces on top of `chainsx/intel-vca2-dkms` at `edb3e51`:

1. **`plx_mbox.c`** — new file (GPL-2.0, matching the driver it links into).
2. **`plx_mbox.h`** — new file. Declares `plx_mbox_info_ops`,
   `plx_mbox_alloc_ops`, `plx_mbox_remove()`.
3. **`plx_debugfs.patch`** — adds the include, registers two debugfs files in
   `plx_create_debug_dir()`, and calls `plx_mbox_remove()` from
   `plx_delete_debug_dir()`:

   | file | mode | purpose |
   |---|---|---|
   | `mbox_info` | `0444` | read the mailbox's DMA address and size |
   | `mbox_alloc` | `0200` | write to allocate/free the segment |

Plus one line in the top-level `Kbuild`:

```make
plx87xx-objs += vca/plx87xx/plx_mbox.o
```

## Rebuilding it

```bash
# on the host, as root
B=/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10       # the DKMS source tree
cp plx_mbox.c plx_mbox.h "$B/vca/plx87xx/"
patch -d "$B/vca/plx87xx" < plx_debugfs.patch
grep -q plx_mbox.o "$B/Kbuild" || \
    echo 'plx87xx-objs += vca/plx87xx/plx_mbox.o' >> "$B/Kbuild"
```

Then rebuild. **Note the host's currently-installed module was built by hand,
not by `dkms install`** — which is exactly why it drifted. Prefer
`dkms build`/`dkms install` so DKMS's record matches what is on disk and
`dkms status` stops reporting *"Differences between built and installed
modules"*.

Rollback: `/root/plx87xx.ko.bak` on the host is the pristine upstream build
(271,101 bytes, Sep 19), which matches the DKMS record byte-for-byte.

## Caveats

- Unloading and reloading `plx87xx` on a live card causes `dma_hang` on the
  next boot ([`docs/GOTCHAS.md`](../../docs/GOTCHAS.md)). Installing a
  rebuilt module needs a **host reboot**, not `rmmod`/`insmod`.
- The A-LUT window this sets up aliases **guest kernel RAM** at each host
  `peer_off`. Do not write to those offsets.
- **These files are kept byte-exact, with no SPDX header added.** That is
  deliberate: `srcversion` is a hash of the source, so adding even a comment
  changes it. Left untouched, a rebuild should reproduce
  `srcversion C7DB21AFCC365566F4BFCAA` — which is the proof the recovery is
  complete rather than approximately right. Verify with
  `modinfo -F srcversion` before trusting it. Add headers only once that
  check has passed and been recorded.
- Authorship is not recorded anywhere; it predates this session and was
  presumably written during the earlier ALUT work
  ([`docs/ALUT_MBOX.md`](../../docs/ALUT_MBOX.md)). It is GPL-2.0 by
  linkage — it compiles into `plx87xx`, which is GPL-2.0, and could not
  legally be otherwise.
