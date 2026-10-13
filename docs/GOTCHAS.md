<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Gotchas — how we hung the card, and how not to again

Proven 2026-09-20 on the Proxmox host (kernel `7.0.2-6-pve`).

## Debugfs is not “read-only”

Many `/sys/kernel/debug/plx87xx*` and `/sys/kernel/debug/vop*` files are mode
`0444` but **running the file is the API**. `open()`/`read()` performs DMA,
flush, interrupt injection, or a crash.

### Never cat / read these

Under `/sys/kernel/debug/vop1/` (and vop2/vop3):

- `dma_test`, `dma_test_mix100GB`, `dma_test_read100GB`, `dma_test_write100GB`
- `dma_test_run_node_memcpy_task`
- `crash_me`, `crash_node`, `panic`

Under `/sys/kernel/debug/plx87xx_dma/<bdf>/`:

- `flush` — DMA flush; this is what oopsed `plx_dma_cleanup`
- `cleanup`
- `intr_test`
- `issue_pending`

Under `/sys/kernel/debug/plx87xx/<bdf>/` (verified in `plx_debugfs.c` as
side-effecting even at 0444):

- `intr_send` (fires **all 16** doorbells), `intr_req`, `intr_free`
- `memory_write`, `bar_write` (`#if PLX_MEM_DEBUG`, may be compiled out)
- `gpio` (0644)

### Safe to read (seq_printf dumps only)

Confirmed by reading the `*_show` functions in
`src/vca_modules/vca/plx87xx/plx_debugfs.c` and `vca/vop/vop_debugfs.c`:

- `/sys/kernel/debug/vopN/stats`
- `/sys/kernel/debug/vopN/vdev_info`
- `/sys/kernel/debug/vopN/dp` (device page; large; cap the read)
- `/sys/kernel/debug/plx87xx/<bdf>/spad`
- `/sys/kernel/debug/plx87xx/<bdf>/alm_state`
- `/sys/kernel/debug/plx87xx/<bdf>/msi_irq_info`

If a file is not on this list, **do not cat it** until the `show`/`write`
function in source has been read.

## What actually happened (2026-09-20)

A directory walk of `plx87xx_dma/0000:03:00.2/` read `flush` / `cleanup` /
`intr_test` / `issue_pending`. Host kernel oopsed in `plx_dma_cleanup` /
`dmatest_callback` (NULL deref). Node 0 went `net_device_ready, dma_hang`
and stopped answering ping. Nodes 1–2 were still up.

A later `rmmod plx87xx` stuck in **D state** (refcount `-1`). `vcactl status`
then timed out on modprobe. `vcactl --force reset 0 0` and `pwrbtn-long`
hung. Proxmox VMs 100/101 kept running.

**Recovery that worked:** sysrq `b` hard reboot of the host, then the normal
BlockIO boot (`reset → wait-BIOS → blockio open → boot vcablk0`). A polite
`reboot` will wait forever on D-state `rmmod`.

Node 0’s `.vcad` had an unclean stop (Alpine VM was running). ext4 remounted
clean. `/boot` FAT on all three nodes logged “not properly unmounted”.

## A dma_hang with nothing poked (2026-09-28)

Card 1 cpu 0 hung on its own about 4 minutes into a Remotion render on the farm:
`plx87xx_dma 0000:09:00.2: plx_dma_thread_fn dma hang detect 0x10009`. The state
was then `link_down, dma_hang`. Its siblings kept running.

A per-node power cycle does **not** clear it. The sequence was long press
(`power_off, dma_hang` in 10 s), `blockio close`, short press, `reset 1 0`,
`wait-BIOS 1 0`. The node went back to `link_down, dma_hang`, and `wait-BIOS`
timed out after 120 s. Every vcactl call returned cleanly with no D-state,
unlike 2026-09-20. The flag appears to live in the host driver's DMA state for the card,
so clearing it takes a card reset or a host reboot. There is no vcactl command
for it, and the `plx87xx_dma` sysfs files are the ones that oopsed the host above.

## Swapping node images (learned 2026-09-21, cost a power cycle)

The sequence matters and the tooling only protects you partway.

- **`vcactl reset` is refused while a node has an active RW blockio device**,
  and that refusal is correct — it would risk corrupting the `.vcad`:

  ```
  ERROR: VCA Card 0 CPU 2 has an active blockio device in RW mode
  Reset the node in a graceful way (from OS level), or invoke reset with --force
  ```

  Shut the node down from inside (`systemctl poweroff`) and wait for
  `power_off`/`link_down` first.

- **But the refused reset still starts a VOP teardown.** It left
  `vcactl reset` alive in `futex_do_wait` with a thread stuck at
  `flush_work` → `vop_virtio_del_card_device`, and a later `vcactl boot`
  queued behind it in D state. See the `jumbo_mtu` entry below for why the
  work never completed.

- Image swapping itself turned out **not** to be the cause of the
  2026-09-21 host wedges. See the rtnl deadlock below — the real trigger was
  a node NIC re-registering while a buggy `jumbo_mtu` was loaded, which any
  node reboot does. Three power cycles were spent blaming the wrong thing.

- **`pwrbtn-short` toggles.** After a poweroff the first press often leaves
  the node in `power_off`; a second brings it to `link_down` → `bios_down` →
  `bios_up`. Blindly issuing two presses turns it back off. Always read
  `vcactl status` between presses rather than assuming.

- **Swapping images changes the node's SSH host key** (it is a different OS
  install). Expect `REMOTE HOST IDENTIFICATION HAS CHANGED` and clear it with
  `ssh-keygen -f /root/.ssh/known_hosts -R 172.31.N.1`.

## The rtnl self-deadlock (root cause of three host wedges, 2026-09-21)

**Symptom:** host pings, TCP 22 and 8006 accept, Proxmox web serves HTTP 200,
VM guests are fine — but **SSH never completes a login**. Eventually the web
UI goes too. Only a power cycle recovers it.

**Cause:** `register_netdevice()` invokes the netdev notifier chain with
**rtnl already held**. `rtnl_lock()` is not recursive, so a `NETDEV_REGISTER`
handler that takes rtnl self-deadlocks:

```
rtnl_lock
jumbo_netdev_event [jumbo_mtu]
notifier_call_chain
register_netdevice+0x6ff
virtnet_probe [vca_virtio_net]
vop_bh_handler [vop]
INFO: task kworker/31:1:279 is blocked on a mutex likely owned by
      task kworker/31:1:279.
```

It deadlocks **inside the VOP workqueue** and leaves rtnl held forever, so
everything needing rtnl piles up behind it:

```
INFO: task sshd-session:8835 is blocked on a mutex likely owned by
      task kworker/30:1:282.
```

That is exactly why sshd stops completing logins while the kernel still
answers pings.

**Why it only bit during image work:** the handler fires when a NIC
*registers* while the module is loaded — i.e. when a node reboots.
Benchmarking never reboots a node, which is why hours of GPU/CPU benchmarking
never tripped it.

**Fixed 2026-09-21.** `apply_dev()` now takes an explicit `rtnl_held` flag and
uses `netif_set_mtu()` (the rtnl-held variant) instead of `dev_set_mtu()`
(which acquires rtnl). Verified by rebooting a node with the module loaded:
release + re-apply both logged, zero `blocked for more than` lines, zero
D-state, host healthy.

> **The lesson that actually matters: the host journal is persistent.**
> `journalctl -b -1 -k` shows the previous boot's kernel log, including the
> stack trace that preceded the wedge. Three power cycles were spent guessing
> at causes before anyone looked. **Read the previous boot's journal first.**

## jumbo_mtu and the netdev reference hang

**Symptom:**

```
unregister_netdevice: waiting for eth2 to become free. Usage count = 2
```

repeating forever, with `vcactl` stuck in D state inside
`vop_virtio_del_card_device()` → `flush_work()`.

**Cause:** `jumbo_mtu.ko` took a `dev_get_by_name()` reference on each VCA NIC
and held it until `rmmod`. Resetting a node while the module was loaded meant
the netdev could never be unregistered.

**Recovery, no reboot needed:** `rmmod jumbo_mtu` drops the references, the
unregister completes, and the stuck `vcactl` unwinds. This worked; the D-state
process cleared and nodes 0/1 were never affected.

**Fixed 2026-09-21.** The module now registers a netdev notifier: it releases
the reference on `NETDEV_UNREGISTER` and re-applies jumbo on
`NETDEV_REGISTER`, so it is also self-healing across node reboots — previously
a returning NIC silently came back at MTU 1500. Verified against the exact
failure:

```
jumbo_mtu: releasing eth2 (device going away)
```

no unregister spin, no D-state, eth0/eth1 untouched. **If you are running an
older `jumbo_mtu.ko`, rmmod it before resetting any node.**

## A second card (learned 2026-09-26)

A second VCA 2 went in beside the first. vcactl calls it card 1; its nodes
default to `172.31.4-6.1` and the host sees their NICs as `eth3-5`. Nodes are
numbered globally, card C cpu N = node C*3+N, image `node<C*3+N>-ubuntu-jammy.vcad`.

- **Unscoped vcactl commands act on every card.** `vcactl wait` waited on the
  new, image-less card, timed out and failed `vca2-card.service`. A failed
  unit skips its `ExecStartPost` (NAT), everything after the wait (jumbo MTU:
  card 0 silently back at 1500), and never runs its clean-shutdown
  `ExecStop`. Every call in the boot/shutdown scripts now names its card.
- **`jumbo_mtu` only covers the NICs named at insmod.** Card 1's host side sat
  at 1500. The boot script now passes `eth0..eth5` when card 1 is up; on a live
  host, `rmmod jumbo_mtu && insmod jumbo_mtu.ko ifnames=eth0,...,eth5 mtu=65521`
  (card 0 drops to 1500 for about a second).
- **A node can hang in `booting` for good.** BIOS read the image, then the
  kernel handoff failed (`Reinit device page not set` / `Can not open
  Dev_page`); blockio stayed `inactive`, so the OS never mounted the disk.
  `pwrbtn-short` is ignored in `booting`. What worked: `blockio close`, then
  `pwrbtn-long` to a settled `power_off`, `pwrbtn-short` to `bios_up`, and
  redeploy. First seen on one of three first boots; again on card 1 cpu 2 at
  the 2026-09-29 cold boot, fixed the same way (and `vcactl boot` then
  reached `net_device_ready` in 20 s).
- **One dead node strands its card's siblings at boot.** The boot script is
  `set -e`, and `vcactl wait-BIOS <card>` waits on all three cpus. With card 1
  cpu 0 unable to reach BIOS (2026-09-29), the script exited right there.
  Cpus 1 and 2 sat in `bios_up` without a disk or boot. Everything after it
  was skipped too: `jumbo_mtu` was never loaded (every node at MTU 1500) and
  NAT was never set up. The unit is left `failed`, so its clean-shutdown
  `ExecStop` will not run. **Fixed the same day:** the script now waits for BIOS
  per cpu, logs and skips a node that never gets there, and never resets a card
  that has live nodes (only its missing ones). It retries a node stuck in
  `booting` once, reloads `jumbo_mtu` when any live NIC is short, and exits 0
  while any node is up. Re-running `systemctl start vca2-card` on a live system
  is now safe: it only touches nodes that are not up.
- **`insmod jumbo_mtu` fails with `No such device` if any NIC in `ifnames`
  is missing.** A dead node has no `ethN`, so pass only the NICs that exist:
  `ls /sys/class/net | grep -E '^eth[0-9]+$'`.
- **A node that comes back from repair still runs the OLD smem embedder.**
  The units live on each node's own image. smem switched to EmbeddingGemma 2
  (768 dims) on 2026-10-07 while card 1 cpu 0 (172.31.4.1) was dead, so its
  image still starts Qwen3-Embedding (1024 dims) on :8080, and it is in smem's
  background lane. Before smem uses it: `SMEM_NODES=4 ./services.sh install`
  (smem numbers nodes by IP octet, so 4 is node 3; `start` is not enough -- it
  only installs when `sm-llm.service`, a unit `memory/services.sh` writes, is
  missing).
  `Models._embed_batch` now refuses a wrong-size vector loudly, so the failure
  is an error, not a store of mixed sizes. Same for any reflashed node.
- **`vcactl reset` cannot wake a powered-off node -- it parks it in
  `link_down` forever.** Hit by `systemctl restart vca2-card` (ExecStop
  powered all six off cleanly, then ExecStart's reset stranded them; smem down
  ~10 min). `link_down` here is not "on the way down": it never moves. Only
  `pwrbtn-long` -> `power_off` -> `pwrbtn-short` recovers it. The boot script
  now does exactly that before any reset. To re-arm the unit on a live card,
  use `systemctl start`, never `restart`.

## `/tank-red/vmdata` lives on tank-purple (since 2026-09-28)

One of tank-red's WD30EFRX drives failed (SMART long test read failure, then
FAULTED by ZFS). The node images, the render farm state and everything else
under `vmdata` were moved to the tank-purple mirror (2× WD101PURP) with
`zfs send`. The path was kept:

| Dataset | Mountpoint |
|---|---|
| `tank-purple/vmdata` | `/tank-red/vmdata` (live) |
| `tank-red/vmdata` | `/tank-red/vmdata-old` (read-only, the pre-move copy; snapshots `@copy-20260928`, `@final-20260928`) |

So nothing that hardcodes `/tank-red/vmdata` had to change: boot scripts, the
farm and the image builder all still use that path. It mounts correctly at boot
because `zfs mount -a` mounts parents first, and `vca2-card` is ordered after
`zfs-mount`. Check with `grep vmdata /proc/mounts`: `findmnt` once showed the
old source straight after the swap. tank-red was rebuilt onto a WD40EFAX, an SMR
drive the owner already had (137 GB in 26 min, 0 errors), and holds nothing
critical now.

## Other landmines

- **Do not mmap/write the 4 GB BAR** (`resource2` / `resource2_wc`) on host
  or guest. It is an ALUT-translated window onto live virtio rings, device
  pages, and DMA buffers. Blind writes = same class of hang.
- Host A-LUT is **outbound**. After `mbox_alloc`, host BAR2 at that NTB's
  `peer_off` (`0x22000000` on nodes 0/1, `0x16000000` on node 2) is
  **guest kernel RAM** at the host DMA address, not the mailbox. Do not
  write those offsets. The mailbox is `/dev/vca_alut_*`. Guest BAR2 sees
  host RAM only after a **guest** `plx_add_a_lut_entry(host_dma)`.
- Guest 4.14 **MAX_ORDER=11**: largest buddy alloc is **4 MiB**. A 32 MiB
  physically contiguous guest buffer will `ENOMEM`. A 4 MiB host-push still
  occupies a 32 MiB hardware LUT slot; mmap only the 4 MiB you own.
- Doorbell **0** is LBP. Kick only a known-free db (we used **15**) via
  `alut_hdb`, never `plx87xx/*/intr_send`.
- **Do not `pkill -f <string>`** from a remote command line that itself
  contains `<string>`. It kills the shell. Match a pid file instead.
- **Do not pass a `.vcad` to `vcactl boot` as a ramdisk.** Attach as
  `vcablk0`, then `boot … vcablk0`.
- ~~**Host kernel `7.0.14-17-pve`:** virtio NICs never appear~~
  **THIS WAS WRONG. Retracted 2026-09-21 after actually testing it.**
  `7.0.14-17-pve` works: 3 `vca_virtio` devices, `eth0/1/2` present, jumbo
  65521 with 20000-byte DF pings to all three nodes, and the DMA ring at
  **4581/4487 MiB/s** — marginally *faster* than 7.0.2-6-pve.

  The trap that produced the original claim: **`vop` devices are created when
  a node BOOTS, not when the modules load.** Straight after a host reboot the
  card sits at `bios_up`, `/sys/bus/vca_virtio/devices/` is empty and there
  are no `eth*` NICs — on *any* kernel. Check before running the boot
  sequence and it looks exactly like a broken kernel. On the working kernel
  the `vop-dev0*` probe lines land ~35 s after the PLX links come up.
  **If the NICs are missing, check `vcactl status` first: `bios_up` means
  nobody booted the nodes.**
- **Fixed 2026-09-21: the patched `plx87xx.ko` is now what DKMS installs.**
  It was hand-built at first, with a different `srcversion` and extra
  `misc_register` / `dma_mmap_attrs` symbols, so a kernel update that ran
  `dkms autoinstall` would have silently replaced it with the stock build.
  The patch was recovered byte-exact into `compute/plx_mbox/`, and DKMS was
  rebuilt and `install --force`d for both `7.0.2-6-pve` and `7.0.14-17-pve`;
  `dkms status` is clean (LOG, 2026-09-21 "recovery proven, DKMS record
  fixed"; [HOST_STACK.md](HOST_STACK.md)). The tell that you are on the
  stock build is still that `mbox_info` / `mbox_alloc` are missing from
  `/sys/kernel/debug/plx87xx/*/`.
- The host stack is **`chainsx/intel-vca2-dkms`, not Intel's**. Intel's stock
  `plx87xx` cannot compile against this kernel — it supplies a `dma_map_ops`
  table and `.map_page` no longer exists as a member. Do not try to "restore
  the official driver" as a debugging step; there is nothing to restore to.
- Guest firewalld `REJECT` on INPUT. Outbound (node→host) works; inbound
  (host→node iperf, node↔node) returns `No route to host` until you allow
  the port. Remove the hole after the test.
- Guest `iperf3 -s` without `-4` bound IPv6 only (`:::5201`).
- Host VCA virtio NIC **defaults to max_mtu 1500**. Live jumbo is
  `jumbo_mtu.ko` (`/root/vca2/jumbo_mtu.ko`, loaded by `vca2-boot`,
  `scripts/vca2-boot.sh`). Guests are already 65521. **`jumbo_mtu` is
  currently loaded** (eth0–eth5 = 65521). `rmmod jumbo_mtu` restores 1500.
  Do not reload `plx87xx`.
- Mailbox workers must not reuse a `seq` the worker already completed
  (`last_seq` skip). Use timestamps.
- `VCA_VIRTIO_COPY_DESC` ioctl returns **`-EFAULT`**. It is a stub. Do not
  design userspace DMA around it.
- Do not raise MaxReadReq / MaxPayload with `setpci` on all three nodes at
  once. Node 0 only: MaxReadReq bits **do not stick** (write ignored,
  stays 128 B). Do not bother repeating on 1/2 unless BIOS/EEPROM changes.
- OpenCL daemons (if left up) listen on guest TCP **9200** with an INPUT
  accept rule. That is a hole in the node firewalld REJECT policy.
- **Parsing `vcactl temp` correctly is fiddlier than it looks, and getting it
  wrong reports a terrifying 100 °C.** Each line is
  `Core 0:  +36.0°C  (high = +100.0°C, crit = +100.0°C)`, so a regex for
  `+NN.0` matches the limits too. Filtering to `^Core` lines is **not
  sufficient** — `grep -oE` still returns all three matches *on that line*.
  Take the field:

  ```bash
  vcactl temp | awk '/^Core/ {t=$3; gsub(/[+]/,"",t); sub(/[^0-9.].*$/,"",t); print t}'
  ```

  The earlier version of this entry just said "match Core lines only", which
  was not specific enough to stop the mistake being made twice in a row.
- New `llama-cli` stays in a chat TUI without `--single-turn --simple-io`.
  Bare `--verbose` sets INT_MAX logging (100+ MB of `>`).
- Cannot `scp` over a **running** `ggml-rpc-server` (`ETXTBSY`). Kill, copy,
  restart.
- **Unloading `plx87xx` / `plx87xx_dma` on a live card** leads to
  `dma_hang` on next boot. Bounce the **host**, not `rmmod`.
- sysrq-b may not reboot this box; use a real power cycle if SSH wedges
  (host still pings, sshd does not).
- **A kernel module can compile cleanly, match vermagic exactly, and still
  refuse to load.** On the 22.04 node image:
  `insmod: Invalid module format` + `dmesg: Unknown rela relocation: 4`.
  Type 4 is `R_X86_64_PLT32`, which binutils >= 2.31 emits and this 4.14.20
  loader predates (upstream `b21ebf2fb4cd`, v4.16). Nothing to do with gcc
  version, vermagic or MODVERSIONS — all of which look fine, which is what
  makes it expensive. Fix: `plt32-to-pc32 *.ko` before `insmod`
  (`compute/image/plt32_to_pc32.py`). See NODE_IMAGE.md.
- **A minimal debootstrap has no `libelf-dev`**, so every out-of-tree module
  build dies at `Makefile:961: *** "Cannot generate ORC metadata for
  CONFIG_UNWINDER_ORC=y"` before compiling a line. The stock 16.04 image
  happens to ship it. Now installed by `build_node_image.sh`.
- **iperf3 3.0.11 (xenial) and 3.9 (jammy) are not wire-compatible.** A 3.9
  client against a 3.0 server fails `unable to send control message: Bad file
  descriptor`, which reads like a network fault and is not one. When
  comparing across images, stage one binary onto both ends.
- Do not set a global `LD_LIBRARY_PATH` in a test harness that runs binaries
  from **both** images. Pointing it at a directory holding xenial's
  `libiperf.so.0` made jammy's native iperf3 die with
  `undefined symbol: iperf_delete_pidfile`. Scope the variable to the one
  binary that needs it.
- Backgrounding a server over `ssh` with `nohup ... &` silently loses the
  rest of the command and can drop the session (exit 255). Use
  `systemd-run --unit=NAME ...`, which returns immediately and is stoppable
  by name.
- When reading results out of `dmesg` across several runs, **grep for the
  run's own tag**, not `tail -n`. Stale lines from a previous pair otherwise
  get attributed to the current one — it produced a completely wrong node-to-
  node table before it was caught.
- **Hand-patching a running node is how a fleet silently stops matching its
  build script.** On 2026-09-21 two nodes got `libelf-dev` via `dpkg -i`,
  `plt32-to-pc32` via `scp` and `iperf3` installed, all live, to finish a
  benchmark. The build script was updated afterwards but the *nodes* were
  never rebuilt, so for a while "what the script produces" and "what is
  running" were two different things and nothing recorded it. Every image now
  writes `/etc/vca-image-release`, and
  `/opt/vca-tools/verify_node_image.sh` will tell you if a node is not what
  it claims. If you must patch live, treat it as a temporary state and fold
  it into `compute/image/` the same day.
- **"The nodes have no DNS" was a dangling symlink, not a network problem.**
  The host masquerades `172.31.0.0/16` out of `vmbr0`, so nodes could always
  ping `1.1.1.1` and the LAN — only *names* failed, which looks like a
  firewall or routing fault and is not one. Ubuntu ships
  `/etc/resolv.conf -> ../run/systemd/resolve/stub-resolv.conf`, the image
  build disables `systemd-resolved` (nothing should race the VCA agent for
  eth0), so the stub is never created. An `-e` test on the symlink still
  passes. Fixed in `build_node_image.sh`, which now writes a real file and
  asserts it is not a symlink; `verify_node_image.sh` resolves a name to
  prove it. With DNS working, `apt` works on a node and the reason to
  hand-patch one disappears.
- **`/sbin` IS `/usr/sbin` on any modern distro (usrmerge).** Creating a
  compat symlink `/sbin/x -> /usr/sbin/x` there produces a symlink pointing
  at *itself*, silently replacing the real file. The first build ships a
  broken file and looks fine; the second fails with `cp: not writing through
  dangling symlink`. Compare `readlink -f` of both directories before making
  the link.
- **`[ -e path ]` FOLLOWS symlinks.** Checking a systemd `.wants` entry
  inside a *mounted image* with `-e` tests whether the link's ABSOLUTE
  target exists on the **build host**, not in the image — so a perfectly
  correct symlink reports missing. Use `-e ... || -L ...`, or just read
  `ls -la`. Cost a false "the build is broken" panic.
- **`clinfo` output is column-padded, not colon-separated.** Parsing it with
  `awk -F:` yields an empty field and makes a working P580 look like "no
  OpenCL device". Take `$NF`.
- **Process-per-request benchmarking silently penalises large models**, and on
  this card the penalty is shared: each re-launch reloads the model over the
  *same* host BlockIO path, so three nodes contend. It made a 0.6B embedder
  read 5.67 docs/s instead of 6.90, and scaling 2.47x instead of 2.92x. The
  tell that a result is load-dominated is **scaling above 3.0x on three
  nodes** — physically impossible for independent work. Use a persistent
  `llama-server`, not one process per request.
- **Embedding models are non-causal: the whole batch must fit one ubatch.**
  A 256-line batch against an 8 GB model OOM'd a 16 GB node and the OOM
  killer took `sshd` — the node pinged but reset every connection. Keep
  batches to ~32 docs at 2048 context. `llama_decode: failed to decode,
  ret = -2` is the polite version of this failure.
- **Recovering a node whose sshd is dead: do NOT reach for `vcactl reset
  --force`.** `vcactl` refuses a plain reset while BlockIO is mounted RW and
  says why ("may lead to data corruption"). `vcactl pwrbtn-short` sends ACPI,
  which systemd honours even with sshd gone, giving a clean shutdown. Wait
  for a settled `power_off`, then close/open blockio and boot. No filesystem
  damage.
- **Verify copied models by SIZE, not existence.** A failed `scp` leaves a
  truncated file, and `test -f` happily reports it present — a 8.05 GB model
  silently became 3.20 GB and would have failed much later, confusingly.
  Compare `stat -c%s` against the source.
- **Decode and prefill obey OPPOSITE scaling rules on this card, and mixing
  them up gives the wrong model choice.** Text generation (decode) reads each
  weight once per token: bandwidth-bound, speed tracks *file size*, so
  quantisation helps a lot (gemma-4: Q3 9.2 / Q4 7.3 / Q8 4.9 tok/s).
  Embeddings and prompt processing (prefill) do full matmuls against every
  weight for every token: compute-bound, speed tracks *parameter count*, and
  quantisation barely helps. Measured: a 0.6B embedder does 6.90 docs/s
  against an 8B's 0.54 — 12.8x for a 13.3x parameter ratio. Do not pick an
  embedding model by file size.

- **A reasoning model returns an EMPTY STRING, not an error, when it runs out
  of budget thinking.** gemma-4-E4B is a reasoning model. llama-server splits
  its chain of thought into `message.reasoning_content` and the reply into
  `message.content`. On any prompt long enough to need real thought it spent
  the whole `max_tokens` budget on reasoning, returned
  `finish_reason: "length"`, and handed back `content: ""`. HTTP 200, no
  warning. A full 22-document ingest ran to completion reporting success with
  **zero facts extracted**. The working switch is
  `"chat_template_kwargs": {"enable_thinking": false}`. `reasoning_budget`
  and `thinking_budget` are silently ignored by this build, and
  `"reasoning_format": "none"` only stops the server *splitting* the
  channels — the model still thinks and leaves raw `<|channel>thought`
  markers in `content`. Corollary: the 21.8 tok/s aggregate measured for
  gemma-4 in [BENCHMARK.md](BENCHMARK.md) was largely reasoning tokens.
- **Constrain one-word classifiers with a GBNF grammar.** Asked in free text
  to reply `DUPLICATE` / `UPDATE` / `DISTINCT`, gemma-4 returned an empty
  string for precisely the `DUPLICATE` cases — which fell through to the
  default and stored the duplicate. `root ::= "DUPLICATE" | "UPDATE" |
  "DISTINCT"` via the `grammar` field makes the wrong answer unrepresentable;
  accuracy on a 12-pair labelled set went from unusable to 12/12.
- **A heading path that starts with a filename reads as a file reference.**
  Chunks embed much better with their heading path prepended, but handing a
  chunk beginning `THERMAL.md > Thermal and power envelope` to an extraction
  prompt made the model reply *"Please provide the content of THERMAL.md so I
  can extract the durable facts."* Wrap the body in explicit
  `<document source="...">` tags so the text cannot be read as a pointer to
  text.
