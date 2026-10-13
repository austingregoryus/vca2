<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# compute/alut/ — A-LUT mailbox and doorbell

The 2026-09-20 experiments that first moved data between host and nodes
without virtio-net: a host DMA buffer mapped through each node's A-LUT
window, doorbell 15 as an interrupt, and host-push into guest RAM. Bulk
transfer is now done with the DMA engine ([compute/dma/](../dma/README.md),
[docs/DMA.md](../../docs/DMA.md)); this directory remains the reference for
the doorbell mailbox. Write-up: [docs/ALUT_MBOX.md](../../docs/ALUT_MBOX.md).

| File | Side | Role |
|---|---|---|
| `alut_hmap.c` → `.ko` | host | A-LUT entry for a guest buffer (kprobe lookup of `plx_add_a_lut_entry`) |
| `alut_hdb.c` → `.ko` | host | Kick a node's doorbell (kprobe lookup of `plx_send_intr`) |
| `alut_gmap.c` → `.ko` | node | A-LUT entry onto a host buffer; optional `/dev/vca_gbuf` |
| `alut_gdb.c` → `.ko` | node | Wait on doorbell 15; `/dev/vca_gdb` |
| `alut_host.c`, `alut_host_bar.c`, `alut_guest.c` | host / node | Userspace mmap and ping-pong |
| `plx_mbox.c`, `plx_mbox.h` | host | Copies of the `plx87xx` patch **with an SPDX line added** |
| `install_dkms_mbox.sh`, `make_mbox.sh` | host | Patch the DKMS tree with `plx_mbox` and build it |
| `bounce_load.sh`, `restore_boot.sh`, `boot_nodes.sh` | host | Swap the running `plx87xx.ko`, roll back, re-boot the nodes |

Build: `cp Makefile.host Makefile && make` on the host;
`cp Makefile.guest Makefile && make` on a node (then `plt32-to-pc32 *.ko`).

**Canonical `plx_mbox` source is [`../plx_mbox/`](../plx_mbox/README.md)**,
not this directory. The copies here differ only by their added
`// SPDX-License-Identifier: GPL-2.0` line, but that changes `srcversion`, so
building from them (as `install_dkms_mbox.sh` does) will not reproduce the
verified `C7DB21AFCC365566F4BFCAA` module. Since 2026-09-21 the patch is
DKMS-installed for both host kernels; these scripts are historical.

**Hazards.** `bounce_load.sh` and `restore_boot.sh` unload `plx87xx` on a
live card; on 2026-09-20 one such reload ended in a `dma_hang` and a host
power cycle ([LOG.md](../../docs/LOG.md)). Do not cat
`plx87xx/*/intr_send` (it fires all 16 doorbells). Read
[SAFETY.md](../../SAFETY.md) and [GOTCHAS.md](../../docs/GOTCHAS.md) first.
