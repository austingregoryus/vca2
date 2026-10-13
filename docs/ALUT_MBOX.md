<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# ALUT mailbox (3-node doorbell + host-push)

> **Historical (2026-09-20).** Superseded by [DMA.md](DMA.md) for bulk
> transfer; still the reference for the doorbell mailbox.

Host DMA buffer plus a **guest** A-LUT entry so each node mmap's BAR2.
Host kicks doorbell 15; guest IRQ wakes a waiter that acks the header.
Host can also **push** into a guest buffer via host BAR2. No virtio-net,
no DMA debugfs, no `plx87xx/intr_*`.

## Code

| Path | Role |
|---|---|
| `compute/alut/plx_mbox.c` | Host: `dma_alloc_coherent` one **full ALUT segment**, `plx_add_a_lut_entry`, miscdevice mmap |
| `compute/alut/plx_mbox.h` | debugfs fops |
| `compute/alut/alut_host.c` | mmap `/dev/vca_alut_*`, write ping |
| `compute/alut/alut_guest.c` | mmap guest `resource2` at `peer_off`, ack ping |
| `compute/alut/alut_gmap.c` | Guest kmod: LUT map of host DMA; optional `/dev/vca_gbuf` |
| `compute/alut/alut_hmap.c` | Host kmod: kprobe-lookup `plx_add_a_lut_entry` for a guest PA |
| `compute/alut/alut_gdb.c` | Guest kmod: `plx_request_threaded_irq` on db 15, `/dev/vca_gdb` |
| `compute/alut/alut_hdb.c` | Host kmod: kprobe `plx_send_intr`; `/sys/kernel/debug/alut_hdb/kick` |
| `compute/alut/Makefile.guest` | `alut_gmap.ko` + `alut_gdb.ko` on Xenial 4.14 |
| `compute/alut/Makefile.host` | `alut_hmap.ko` + `alut_hdb.ko` on `7.0.2-6-pve` |
| `compute/alut/install_dkms_mbox.sh` | Patch DKMS tree + build |
| `/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10/` | Live DKMS copy on the host (patched) |

Must allocate a **whole segment**. A 4 KiB buffer in a 32 MiB window would
alias other host RAM.

`mbox_alloc` is **0200 (write-only)** so `cat` cannot trigger it.
`mbox_info` is 0444 seq_printf only.

## What we did (2026-09-20)

1. `plx_mbox.o` **compiled** into `plx87xx.ko` (`MAKE_DONE`).
2. Loaded the new `.ko`. Debugfs appeared:
   `/sys/kernel/debug/plx87xx/0000:05:00.0/mbox_alloc`
   `/sys/kernel/debug/plx87xx/0000:05:00.0/mbox_info`
3. **Did not write `mbox_alloc`.** Nodes never reached BIOS after the
   module swap (`wait-BIOS TIMEOUT`, `link_down`).
4. Restored `/root/plx87xx.ko.bak`. Boot then stuck
   `booting, dma_hang` (same class of hang as the debugfs DMA incident:
   unloading `plx87xx_dma` is toxic while the card is live).
5. sysrq-b did **not** reboot (uptime kept climbing). Host later **pinged
   but SSH stopped accepting** (load was ~30). Needs a **power cycle** of
   the host, then the usual BlockIO boot with the **backup** `.ko`.

## Live test after clean reboot (2026-09-20 ~22:40)

Loaded experimental `plx87xx.ko` via **host reboot** (no `rmmod`). All three
nodes reached `net_device_ready`. `mbox_info` showed `unallocated`.

**`echo 1 > mbox_alloc` on 05:00.0 succeeded:**

```
ALUT mbox vca_alut_0000_05_00_0 dma=0x4c000000 peer_off=0x22000000
size=0x2000000 (32 MiB) seg=17 magic=0x414c5554
```

| View | Result |
|---|---|
| Host `/dev/vca_alut_0000_05_00_0` mmap | **Works.** Magic `ALUT`, seq increments on write. |
| Guest `resource2` mmap at `0x22000000` | Maps, does **not** hang. Reads **`0xffffffff`** (no decode). |
| Host `resource2` mmap at `0x22000000` | Maps. Reads `0x5a5a5a5a` (not our buffer). |
| Virtio-net | Still `net_device_ready`, ping 0.2 ms |

So: one 32 MiB A-LUT segment is programmed and the host DMA buffer is real.
Guest BAR2 at the **host** `peer_off` is the wrong window (see NTB direction).

## NTB direction (2026-09-20 ~22:50)

`plx_add_a_lut_entry(xdev, pa)` programs **outbound** translation: local
BAR2 + `peer_off` reaches **peer physical** `pa`. Both sides set
`a_lut=1 a_lut_peer=1` (`VCA_ALUT_HOST_SIDE` and `VCA_ALUT_CARD_SIDE`
are both defined). Guest node 0: `link_side 1`, 128 × 32 MiB, ALUT base
`0x39000`.

Proof on node 0:

| Access | Result |
|---|---|
| Guest DRAM at `0x4c000000` (kernel `phys_to_virt`) | `0x5a5a5a5a` |
| Host BAR2 at host `peer_off` `0x22000000` | **same** `0x5a5a5a5a` |
| Guest `alut_gmap.ko do_map=1` (`plx_add_a_lut_entry(0x4c000000)`) | guest `peer_off=0x8000000` (slot 4) |
| Guest BAR2 / `/dev/mem` at `0x3e08000000` | magic **`0x414c5554` `ALUT`** |

Host `/dev/vca_alut_*` is the DMA buffer in **host RAM** (no NTB). Guest
sees that same buffer only after the **guest** LUT maps `host_dma`.

Header ping-pong (seq 42, cmd 1, guest status 2) succeeded. Nodes stayed
`net_device_ready`, ping ~0.2 ms. `rmmod alut_gmap` deleted the guest
entry.

Python memcpy (one sample, includes interpreter):

| Path | Rate | Notes |
|---|---:|---|
| Guest CPU **read** of host buffer (BAR2) | **~28 MiB/s** | PCIe read; MaxReadReq stuck at 128 B (`setpci` ignored) |
| Guest CPU **write** of 1 MiB into host buffer | **~4.8 GiB/s** | posted writes; host saw `0xa5` immediately |
| Host local read of its DMA buffer | ~1.5 GiB/s | DRAM, not PCIe |

Bulk **host→guest** should be host CPU (or DMA) **pushing** into a guest
buffer via host BAR2, not the guest pulling. Headers are fine either way.

**Loaded gun:** host LUT seg 17 still maps host BAR2+`0x22000000` onto
**guest kernel RAM** at `0x4c000000`. Do **not** write that host BAR2
offset. Direct `/dev/vca_alut_*` is the mailbox.

Doorbell still unused. Guest LUT is not persistent (`rmmod` drops it).

## All three nodes (2026-09-20 ~23:10)

`mbox_alloc` on `07:00.0` and `0a:00.0` (node 2 is **16 MiB**). Then
`alut_gmap.ko do_map=1` on each guest. Header ping-pong **PINGPONG_OK**
on all three. Nodes stayed `net_device_ready`.

| Node | Guest DID | Host BDF | Host DMA | Seg size | Host LUT `peer_off` | Guest LUT `peer_off` | seq |
|---|---|---|---|---:|---|---|---:|
| 0 | `8086:2958` | `05:00.0` | `0x4c000000` | 32 MiB | `0x22000000` | `0x8000000` | 101 |
| 1 | `8086:2959` | `07:00.0` | `0x48000000` | 32 MiB | `0x22000000` | `0x4000000` | 102 |
| 2 | `8086:295a` | `0a:00.0` | `0x47000000` | **16 MiB** | `0x16000000` | `0x2000000` | 103 |

Guest BAR2 is `0x3e00000000` on every node. Host `/dev/vca_alut_0000_{05,07,0a}_00_0`.

## Host-push into guest RAM (node 0)

Xenial **MAX_ORDER=11** → largest buddy alloc is **4 MiB** (order 10).
`dma_alloc_coherent` / `__get_free_pages` of a full 32 MiB segment
returns `-ENOMEM` even with 15 GiB free. CMA is 0.

Workaround: guest `__get_free_pages` **4 MiB**, host `add_entry(guest_pa, 4 MiB)`
(still one 32 MiB hardware slot; only mmap the 4 MiB at `peer_off`).
`alut_hmap.ko` finds `plx_add_a_lut_entry` via kprobe (not exported on PVE 7).

| | |
|---|---|
| Guest buffer | PA `0x45b800000`, `/dev/vca_gbuf`, order 10 |
| Host LUT | `peer_off=0x29800000` on `05:00.0` BAR2 |
| Header | seq 201, guest ack status 2 **HOSTPUSH_OK** |
| Host BAR2 write 4 MiB | **~3700 MiB/s** (Python) |
| Guest local read 4 MiB | ~2350 MiB/s, pattern `0x3c` visible |

Compare guest **pull** of host RAM: ~28 MiB/s. Bulk host→guest is a **push**.

The hardware slot still translates a full 32 MiB aligned region. Do not mmap
more than the 4 MiB we own. A full-segment guest buffer needs CMA or a
guest reboot with a reserved range.

`rmmod alut_hmap` / `rmmod alut_gmap` after the test. Do **not** write host
BAR2 at `0x22000000` / `0x16000000` (those still alias guest kernel RAM).

## Doorbell (all 3 nodes, 2026-09-20 ~23:20)

Do **not** cat `plx87xx/*/intr_send` (it fires all 16 doorbells). Use
`alut_hdb` which calls `plx_send_intr` for **one** db. Doorbell **0** is
LBP. Node 0 `plx_next_db` returned 8 (VOP/BlockIO already hold 1–7);
we used **15**. INTx path (MSI off).

Host writes the ALUT header, kicks db 15, spins on `status==2`. Guest
`/dev/vca_gdb` blocks until the IRQ, then acks BAR2.

| Node | Iters | min | avg | max |
|---|---:|---:|---:|---:|
| 0 | 50 | **0.033 ms** | **0.038 ms** | 0.088 ms |
| 1 | 10 | 0.033 ms | 0.042 ms | 0.084 ms |
| 2 | 10 | 0.034 ms | 0.044 ms | 0.080 ms |

All `DOORBELL_OK`. Nodes stayed `net_device_ready`. Compare BlockIO
mailbox ping **~8–11 ms** (~250×) and virtio ICMP ~0.2 ms.

Modules unloaded after the test (`alut_gdb` / `alut_hdb` / `alut_gmap`).

## After power cycle (2026-09-20 ~22:33)

Host back: `7.0.2-6-pve`, load ~0, **backup** `plx87xx` (no `mbox_*` debugfs).
All three nodes `net_device_ready`, ping ~0.2–0.45 ms. VMs 100/101 running.
Hottest core after boot **53 °C** (node 0). Experimental `.ko` still only under
`/usr/src/vca2-vcass-.../plx87xx.ko` — not loaded.

## After power cycle (procedure)

```bash
# confirm backup module
ls /root/plx87xx.ko.bak
# boot nodes
systemctl start vca2-card    # runs /usr/local/sbin/vca2-boot (scripts/vca2-boot.sh)
# do NOT load the new plx87xx.ko until we know why BIOS stalled
```

New `.ko` still on disk:
`/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10/plx87xx.ko`

Retry only after nodes are healthy on the backup driver. Next test is
`echo 1 > mbox_alloc` on **node 0 only**, then guest mmap. Doorbell still
unwired (polling first).

## Do not

- `cat mbox_alloc` (write-only anyway)
- `rmmod plx87xx_dma` on a live card
- mmap/write host `resource2` at `0x22000000` (aliases guest RAM)
- mmap `resource2` at a random offset
