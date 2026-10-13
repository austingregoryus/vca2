<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Setting up a VCA 2 the way this repository does

How to bring an Intel Visual Compute Accelerator 2 (VCA1585LMV) up on a
Proxmox host with the scripts in this repository: host driver stack, node
images, first boot, boot at host start, cooling and recovery. Written
2026-10-13 against the scripts as they are on `main`. It replaces
[`archive/SETUP_GUIDE_2026-09.md`](archive/SETUP_GUIDE_2026-09.md), which is
wrong in several places (kernel pin, driver origin, boot script, cooling).

It is for someone who has the card in hand, is comfortable with a root shell
on a Linux host, and wants the configuration every measurement in `docs/` was
taken on. It is one card and one host's experience (two cards since
2026-09-26), not a supported product.

> **Read [`../SAFETY.md`](../SAFETY.md) and [`GOTCHAS.md`](GOTCHAS.md) first.**
> Some files under `/sys/kernel/debug/plx87xx*` are mode `0444` but reading
> them performs DMA, a flush or an interrupt injection. A routine directory
> walk oopsed the host and cost a power cycle. The list is in
> [GOTCHAS.md](GOTCHAS.md#never-cat--read-these).
>
> **Cooling is not optional.** The card is passively cooled and expects
> server-chassis airflow. Do [step 7](#7-cooling-and-power-caps--before-the-first-long-load)
> before you run anything for more than a few minutes.

Where this guide is not sure a step is right, it says so. Those notes are
deliberate: several steps were done once, on 2026-09-19, and never replayed
from scratch.

## Node numbering

Used throughout, and by every script in `scripts/` and `compute/image/`:

| Global node | vcactl | Node IP | Host side | Live image | Host NIC |
|---:|---|---|---|---|---|
| 0 | card 0 cpu 0 | `172.31.1.1` | `172.31.1.254` | `node0-ubuntu-jammy.vcad` | `eth0` |
| 1 | card 0 cpu 1 | `172.31.2.1` | `172.31.2.254` | `node1-ubuntu-jammy.vcad` | `eth1` |
| 2 | card 0 cpu 2 | `172.31.3.1` | `172.31.3.254` | `node2-ubuntu-jammy.vcad` | `eth2` |
| 3 | card 1 cpu 0 | `172.31.4.1` | `172.31.4.254` | `node3-ubuntu-jammy.vcad` | `eth3` |
| 4 | card 1 cpu 1 | `172.31.5.1` | `172.31.5.254` | `node4-ubuntu-jammy.vcad` | `eth4` |
| 5 | card 1 cpu 2 | `172.31.6.1` | `172.31.6.254` | `node5-ubuntu-jammy.vcad` | `eth5` |

Node *n* is card `n / 3`, cpu `n % 3`, IP `172.31.(n+1).1`. The IPs are
vcactl's own defaults. The `.254` host-side addresses are documented for
card 0; card 1 is assumed to follow the same pattern and was not separately
checked. Host NICs are named in the order the nodes register, so a dead node
should shift the names after it (inferred from the boot script, not
observed). The stock Intel images are
`node<n>.vcad`.

Two other numberings exist in the repo, so check which one you are reading:
`monitor/` numbers card nodes 1–6 (0 is the host), and smem's `SMEM_NODES`
goes by the third IP octet (`SMEM_NODES=4` is `172.31.4.1`, global node 3).

The node root password is `vista1`, Intel's documented default for VCA nodes.
Host-side scripts read it from `NODE_PW` and fall back to `vista1`
(`scripts/card1_power_cap.sh` is the exception, see step 7).

## 1. What you need

### Hardware

From Intel's *VCA 2 Product Specification and Hardware Guide* (§3.2–3.4;
not redistributed here, download it from Intel):

- **One PCIe x16 slot.** It supplies 75 W of the card's **235 W** maximum TDP.
- **Both 12 V AUX connectors wired:** a 2×3 (75 W) and a 2×4 (150 W). The
  card ships with no cables. The guide's pinouts put 12 V on pins 1–3 and
  ground on the rest, so check your cable against them. A CPU/EPS 8-pin is
  wired differently. This repo does not record which cables this host uses.
- **The cover, thermal module and duct left on**, and the mechanical support
  bracket (or your own) fitted. Intel says the card overheats without the
  covers and can be damaged unsupported.
- **Airflow through the card**, not past it: Intel specifies 18 CFM at
  0.246 inH₂O for a 35 °C inlet, and 25.5 CFM at 0.476 inH₂O for 45 °C, front
  to back. Cores must stay at or below 96 °C; they throttle at 100 °C. The
  static pressure is the hard part. Ordinary case fans top out near
  0.1 inH₂O ([`../shrouds/README.md`](../shrouds/README.md)).

Measured here, compute loads draw roughly 135–180 W for the whole card
([THERMAL.md](THERMAL.md)). Size the PSU for the 235 W rating anyway.

The card in this host ran BIOS `0ACIE203`. Nothing in this repository flashes
BIOS, EEPROM or switch configuration, and this guide does not either.

### Intel's VCASS 2.3.26 release

Not redistributed here (about 18 GB, and Intel's to distribute).
[SOURCE_MAP.md](SOURCE_MAP.md) lists the archive names but does not record a
download URL; Intel's pages for end-of-life products move, so search Intel's
download centre for *Intel Visual Compute Accelerator 2.3.26*.

Bring-up uses one file from it:

| Archive | Contains | Used for |
|---|---|---|
| `intelvisualcomputeaccelerator_persistent_reference_ubuntu_16.04.3_2.3.26.zip` | `vca_disk_24gb_reference_k4.14.20_ubuntu16.04_2.3.26.vcad.gz` | the stock node image, and the kernel, initramfs and ESP that `build_node_image.sh` copies verbatim |

`intelvisualcomputeaccelerator_sources_2.3.26.zip` is only needed to read
the driver source alongside these docs (unpack into `src/`). The BIOS and
EEPROM zips are not used. Intel's own host packages are not used either; see
step 3.

### Software on the host

- Proxmox VE. This host runs kernel `7.0.14-17-pve` (step 3).
- Kernel headers for the running kernel, for DKMS and for `jumbo_mtu.ko`.
  On Proxmox the package is `proxmox-headers-<version>`; the repo does not
  record exactly which headers package was installed here.
- `sshpass` (the shutdown script, `deploy_node_image.sh --verify`,
  `card1_power_cap.sh`, `compute/dma/preflight.sh`).
- `debootstrap`, `rsync`, `parted` if you build a node image (the builder
  refuses to start without them).
- `iptables`, and `iptables-persistent` if you want the NAT rules saved.

The scripts assume the images live in `/tank-red/vmdata/vca/images`.
`scripts/vca2-boot.sh` hardcodes that path (`IMAGE_BASE`, line 8, no
environment override). The image builder takes `IMG_DIR` and `WORK` from the
environment. Either use the same path or edit `IMAGE_BASE`.

## 2. Read the safety rules

Before touching sysfs, debugfs or `vcactl` on a live card:

- Never `cat`, `grep -r` or tab-preview anything under
  `/sys/kernel/debug/plx87xx*` or `/sys/kernel/debug/vop*` that is not on the
  safe list in [GOTCHAS.md](GOTCHAS.md#safe-to-read-seq_printf-dumps-only).
- Never `rmmod plx87xx` or `plx87xx_dma` on a live card. Reboot the host.
- Never write the 4 GB BAR (`resource2`, `resource2_wc`).
- With two cards, every `vcactl` command that changes state names its card.
  Unscoped commands act on every card.

## 3. Host setup

### Kernel

This host runs `7.0.14-17-pve`, pinned since 2026-09-21, with `7.0.2-6-pve`
kept installed as the fallback. An earlier claim that 7.0.14 breaks the
virtio NICs was wrong and is retracted ([KERNEL_PORT.md](KERNEL_PORT.md)).

```bash
proxmox-boot-tool kernel list
proxmox-boot-tool kernel pin 7.0.14-17-pve && proxmox-boot-tool refresh
# rollback:  proxmox-boot-tool kernel pin 7.0.2-6-pve && proxmox-boot-tool refresh
```

`proxmox-boot-tool kernel pin <ver> --next-boot` boots a kernel once and
falls back on the next reboot. That is the safe way to try another one.

`scripts/vca2-boot.sh` refuses to run on any kernel except those two. Set
`VCA_ALLOW_ANY_KERNEL=1` to test another. Every other kernel is untested here.

### Driver stack: chainsx, not Intel

Intel's stock `plx87xx` does not compile against Linux 7.0. The host runs the
community forward-port
[`chainsx/intel-vca2-dkms`](https://github.com/chainsx/intel-vca2-dkms) at
`edb3e51` ("add support for Linux 7.0", 2026-08-08). It builds three packages
at version `2.3.26+ubuntu24.04.10`:

| Package | Provides |
|---|---|
| `vca2-vcass-modules-dkms` | the host kernel modules, via DKMS |
| `daemon-vca` | `vcactl`, `vcactld`, `vcactl.service`, `/usr/sbin/vca_eth_ifup.sh`, udev rules |
| `vca2-host` | metapackage depending on both |

It also ships `vca2-load-modules` (`debian/vca2-load-modules` in that repo),
which `modprobe`s the modules in the right order. `vca2-boot.sh` calls it at
`/usr/sbin/vca2-load-modules`.
Full provenance and an independent review of the port, including three
undisclosed behaviour changes, are in [HOST_STACK.md](HOST_STACK.md).

Build and install it following that repo's README (`./build_debs.sh`, then
`apt install` the three `.deb` files).

> **Not replayed.** The packages were built on this host on 2026-09-19,
> before this project's log starts. The chainsx README targets Ubuntu 24.04
> and lists Ubuntu build dependencies (`linux-headers-generic` among them).
> What was substituted on Proxmox is not recorded. HOST_STACK.md describes
> the result as found by inspection; nobody has rebuilt this host from zero
> since.

Check that DKMS built for every installed kernel, then load and look:

```bash
dkms status                 # vca2-vcass/2.3.26+ubuntu24.04.10 for each kernel
vca2-load-modules
lsmod | grep -Ei 'vca|vop|plx'
vcactl status               # expect bios_up on every cpu
```

**Straight after loading, `/sys/bus/vca_virtio/devices/` is empty and there
are no `eth*` NICs. That is correct.** VOP devices appear when a node boots,
not when the modules load. Mistaking this for a broken kernel is what
produced the retracted 7.0.14 claim.

### Optional: the `plx_mbox` patch

Every measurement in this repo was taken with a locally patched `plx87xx`
that adds a host A-LUT mailbox ([`../compute/plx_mbox/README.md`](../compute/plx_mbox/README.md)).
Nodes boot without it. You need it only for the `compute/alut/` experiments.
To match this host exactly:

```bash
B=/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10      # the DKMS source tree
cp compute/plx_mbox/plx_mbox.c compute/plx_mbox/plx_mbox.h "$B/vca/plx87xx/"
patch -d "$B/vca/plx87xx" < compute/plx_mbox/plx_debugfs.patch
grep -q plx_mbox.o "$B/Kbuild" || \
    echo 'plx87xx-objs += vca/plx87xx/plx_mbox.o' >> "$B/Kbuild"
for k in 7.0.14-17-pve 7.0.2-6-pve; do
  dkms build   -m vca2-vcass -v 2.3.26+ubuntu24.04.10 -k "$k"
  dkms install -m vca2-vcass -v 2.3.26+ubuntu24.04.10 -k "$k" --force
done
# after the reboot below:
modinfo -F srcversion plx87xx
#   C7DB21AFCC365566F4BFCAA  = patched, as measured here
#   0F994D23ABC944D13CD8204  = upstream chainsx
```

Build through DKMS, not by hand: `plx_mbox.c` does not include its own
header, and only `dkms.conf`'s `KERNWARNFLAGS=` stops
`-Werror=missing-prototypes` failing the build. **Reboot the host** to load
the new module. Do not `rmmod`/`insmod` it on a live card. The DKMS commands
and `srcversion` values are from [HOST_STACK.md](HOST_STACK.md). The tell
that you are on the unpatched build is that `mbox_info` / `mbox_alloc` are
missing from `/sys/kernel/debug/plx87xx/*/` (list the directory; do not read
its other files).

### Jumbo MTU on the host: `jumbo_mtu.ko`

The guests run MTU 65521, but the host's VCA NICs come up at 1500 every boot
and cannot be raised with `ip`. Node-to-node traffic then runs at about
2.7 Gbit/s instead of 16–26, and nothing warns you ([FABRIC.md](FABRIC.md)).
[`../compute/jumbo/jumbo_mtu.c`](../compute/jumbo/jumbo_mtu.c) lifts the
limit. `vca2-boot.sh` loads it from `/root/vca2/jumbo_mtu.ko`. After a kernel
change it runs `make` in that directory, so copy the source and Makefile
there, not just a built `.ko`:

```bash
mkdir -p /root/vca2
cp compute/jumbo/jumbo_mtu.c compute/jumbo/Makefile /root/vca2/
make -C /root/vca2
```

If you run an older copy of this module, `rmmod jumbo_mtu` before resetting
any node. Versions before 2026-09-21 deadlock the host when a node's NIC
re-registers ([GOTCHAS.md](GOTCHAS.md#the-rtnl-self-deadlock-root-cause-of-three-host-wedges-2026-09-21)).

### NAT for the nodes

[`../scripts/vca2-enable-nat.sh`](../scripts/vca2-enable-nat.sh) enables
forwarding (and writes `/etc/sysctl.d/99-vca-forwarding.conf`), then adds
MASQUERADE and FORWARD rules for `172.31.0.0/16` out of **`vmbr0`, hardcoded**
(`OUT_IF`). Edit `OUT_IF` if your uplink is different. It is idempotent, and
saves with `netfilter-persistent` if that is installed.

```bash
apt install iptables-persistent      # optional, to persist the rules
bash scripts/vca2-enable-nat.sh
```

## 4. Node images

Each node boots over BlockIO from its own `.vcad` disk image. It runs Intel's
4.14 kernel (`4.14.20-1.2.3.26.vca`) and Intel's dracut initramfs whatever
the userspace is. That is why the userspace can be replaced.

**Every node needs its own image file.** BlockIO opens it read-write, so two
nodes on one file is two kernels writing one ext4.

### Stock: Ubuntu 16.04

```bash
cd /tank-red/vmdata/vca/images
gunzip -k vca_disk_24gb_reference_k4.14.20_ubuntu16.04_2.3.26.vcad.gz
for n in 0 1 2; do
  cp --sparse=always vca_disk_24gb_reference_k4.14.20_ubuntu16.04_2.3.26.vcad node$n.vcad
done
```

> **Not recorded.** `node{0,1,2}.vcad` on this host predate the log, and how
> they were made is not written down. Copies of Intel's persistent reference
> image are the obvious origin and match everything the docs say about them.
> Keep the gunzipped reference file: the image builder reads it.

The stock image works and is the rollback, but it is gcc 5.4, glibc 2.23 and
systemd 229. On a stock node, `scripts/vca2-guest-hold-sensitive.sh` holds the
packages a canary upgrade showed must not move
([archive guide §10](archive/SETUP_GUIDE_2026-09.md)).

### Built: Ubuntu 22.04 (what this host runs)

[`../compute/image/build_node_image.sh`](../compute/image/build_node_image.sh)
debootstraps a 22.04 root filesystem onto Intel's kernel, initramfs and ESP,
which it copies verbatim. The blocked SGEMM benchmark ran about 1.9× faster
with gcc 11 (n=1024; about 1.7× at n=2048), and kernel modules build on the
node. Only the `ubuntu-jammy` profile is
`proven`. Building, deploying and verifying are covered in
[IMAGE_BUILDER.md](IMAGE_BUILDER.md); what it buys, with measurements, is in
[NODE_IMAGE.md](NODE_IMAGE.md). In short:

Boot the stock images first (step 5, with `node$n.vcad`): the deploy script
works on a running node, powers it off, copies the master and boots the copy.

```bash
cd compute/image
./build_node_image.sh --list
./build_node_image.sh --profile ubuntu-jammy     # ~20 min -> node-ubuntu-jammy.vcad (the master)
# with each node running (on any image):
./deploy_node_image.sh --node 0 --master /tank-red/vmdata/vca/images/node-ubuntu-jammy.vcad --verify
./deploy_node_image.sh --node 1 --master /tank-red/vmdata/vca/images/node-ubuntu-jammy.vcad --verify
./deploy_node_image.sh --node 2 --master /tank-red/vmdata/vca/images/node-ubuntu-jammy.vcad --verify
# second card: --card 1 --node 0..2  ->  node3..5
```

- **The master is a template that nothing boots.** `deploy_node_image.sh`
  copies it per node (reflink on ZFS with `feature@block_cloning`, sparse
  copy elsewhere) and refuses to run while the master is attached. The
  closing message of `build_node_image.sh` and the "Usage" block in
  NODE_IMAGE.md show the master being opened on a node directly. Do not do
  that.
- Do one node at a time and check `vcactl status` between steps. Rapid
  repeated swaps wedged this host on 2026-09-21.
- Each swap changes the node's SSH host key. The deploy script clears the
  host's `known_hosts` entry.
- The root password is set at build time from `ROOT_PW` (default `vista1`).
  If you change it, export the same value as `NODE_PW` for the host scripts.
- On a 22.04 node, a module built with its gcc 11 / binutils 2.38 compiles,
  matches vermagic and still fails with `Unknown rela relocation: 4`. Run
  `plt32-to-pc32 *.ko` before `insmod`
  ([`../compute/image/plt32_to_pc32.py`](../compute/image/plt32_to_pc32.py),
  installed on every built image).

## 5. First boot by hand

Do this once by hand before installing anything that runs at boot. The order
matches [`../scripts/vca2-boot.sh`](../scripts/vca2-boot.sh). Card 0, with
the images from step 4 (use `node$n.vcad` for stock):

```bash
I=/tank-red/vmdata/vca/images
vca2-load-modules                       # if lsmod shows no vop_bus
vcactl status 0                         # every cpu bios_up
vcactl reset 0                          # only when no node on card 0 is up
for n in 0 1 2; do vcactl wait-BIOS 0 $n; done
for n in 0 1 2; do vcactl blockio open 0 $n vcablk0 RW $I/node$n-ubuntu-jammy.vcad; done
vcactl blockio list
for n in 0 1 2; do vcactl boot 0 $n vcablk0; done
for n in 0 1 2; do timeout 300 vcactl wait 0 $n; done
vcactl status 0                         # every cpu net_device_ready
for n in 0 1 2; do /usr/sbin/vca_eth_ifup.sh 0 $n; done
insmod /root/vca2/jumbo_mtu.ko \
  ifnames="$(ls /sys/class/net | grep -E '^eth[0-9]+$' | sort -V | paste -sd, -)" mtu=65521
```

- **Never pass a `.vcad` straight to `vcactl boot`.** It is treated as a
  ramdisk and fails with `LBP_*` errors. Attach it as `vcablk0`, then
  `boot … vcablk0`.
- A node past `booting` (`net_device_up`, `dhcp_*`, `os_ready`) is running
  and just slow. On 2026-09-29 two nodes took 4.7 minutes. Leave it.
- `vcactl reset` cannot wake a powered-off node; see step 8.
- `jumbo_mtu` takes only NICs that exist. `insmod` fails with
  `No such device` if `ifnames` names a missing one. The command above
  assumes the host's own NICs are not called `eth*` (Proxmox names them
  `en*` and bridges them as `vmbr*`). `vca2-boot.sh` makes the same
  assumption.

Instead of the block above, you can run the script itself once, after
setting `IMAGE_BASE` if your path differs. It needs card 0's three images to
exist under the expected names:

```bash
bash scripts/vca2-boot.sh                      # node<n>-ubuntu-jammy.vcad
VCA_IMAGE_SUFFIX= bash scripts/vca2-boot.sh    # stock node<n>.vcad
```

### Verify

```bash
vcactl status                                  # net_device_ready
ls /sys/bus/vca_virtio/devices/                # one entry per booted node
grep . /sys/class/net/eth*/mtu                 # eth0-2 at 65521
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  ping -c 2 -M do -s 20000 -W 3 $ip >/dev/null && echo "$ip jumbo ok" || echo "$ip FAIL"
done
# built images only:
ssh root@172.31.1.1 cat /etc/vca-image-release
ssh root@172.31.1.1 /opt/vca-tools/verify_node_image.sh
bash scripts/vca2-status.sh
```

`vca2-status.sh` prints the kernel, `vcactl status`, temperatures, BlockIO,
modules and virtio devices, but pings only card 0's three IPs.

From a node, `ping 172.31.1.254` tests the host link, `ping 1.1.1.1` tests
NAT and `ping` on a name tests DNS. On a built image, missing DNS with
working routing means `/etc/resolv.conf` is a dangling symlink; the builder
now prevents that ([IMAGE_BUILDER.md](IMAGE_BUILDER.md#dns-on-the-nodes)).

Read temperatures with the field, not a regex. A regex also matches the
`high = +100.0°C` limits on the same line:

```bash
vcactl temp 0 | awk '/^Core/ {t=$3; gsub(/[+]/,"",t); sub(/[^0-9.].*$/,"",t); print t}'
```

`vcactl temp` goes over the agent channel and has been seen failing with
`LBP_IRQ_TIMEOUT` on healthy nodes. `sensors` on the node over SSH still
works.

### A second card

`vca2-boot.sh` brings card 1 up only if `vcactl status` shows it and
`node3`–`node5` images exist. A missing or image-less second card never fails
card 0. Deploy card 1's images with `deploy_node_image.sh --card 1`. Card 1's
host NICs are `eth3`–`eth5`, so `jumbo_mtu` has to be loaded with all six
names. The boot script does that when it loads it, and reloads it when a live
NIC is not at 65521.

## 6. Making it persistent: `vca2-card.service`

Intel's `vcactl.service` (from `daemon-vca`) does not boot the nodes on this
host; per the unit's own comment, auto-boot is off. The node bring-up is
[`../systemd/vca2-card.service`](../systemd/vca2-card.service):

- `ExecStart=/usr/local/sbin/vca2-boot` (`scripts/vca2-boot.sh`)
- `ExecStartPost=-/usr/local/sbin/vca2-enable-nat` (allowed to fail)
- `ExecStop=/usr/local/sbin/vca2-shutdown` (`scripts/vca2-shutdown.sh`):
  `systemctl poweroff` on each attached node over SSH, waits up to 200 s for
  `power_off`, then `blockio close`s every image
- ordered after `vcactl.service`, `network-online.target` and
  `zfs-mount.service`; `TimeoutStartSec=1800`, `TimeoutStopSec=240`

[`../memory/deploy/install_host.sh`](../memory/deploy/install_host.sh)
installs this together with smem (its API unit and model servers on every
node). For the card alone, the four lines that matter, from the repo root:

```bash
install -m 0755 scripts/vca2-boot.sh       /usr/local/sbin/vca2-boot
install -m 0755 scripts/vca2-enable-nat.sh /usr/local/sbin/vca2-enable-nat
install -m 0755 scripts/vca2-shutdown.sh   /usr/local/sbin/vca2-shutdown
install -m 0644 systemd/vca2-card.service  /etc/systemd/system/vca2-card.service
systemctl daemon-reload
systemctl enable vca2-card.service
systemctl start vca2-card.service      # on a live card this only arms ExecStop
```

What the boot script does, so you know what to expect in
`journalctl -u vca2-card`:

- Checks the kernel and loads the modules if `vop_bus` is not loaded.
- Per card: skips it if all three nodes are `net_device_ready`. With no node
  up it resets the card; with some nodes up it resets only the missing ones,
  one at a time.
- Waits for BIOS per cpu. A node that never gets there is logged and
  skipped, and its siblings still boot. A node stuck in `booting` gets one
  power cycle and retry.
- Brings up host networking for each live node, loads or reloads
  `jumbo_mtu`, pings each node with 20000-byte DF frames, prints
  temperatures.
- Exits 0 while any node is up, so one dead node does not fail the unit.
  That matters: a `failed` unit never runs its clean-shutdown `ExecStop`.

Things the unit as shipped does not cover:

- **Use `systemctl start`, never `restart`.** `restart` runs `ExecStop`
  first, which powers every node off.
- **`systemctl start` does nothing if the unit is already active.** To bring
  a missing node back on a running host, run `/usr/local/sbin/vca2-boot`
  directly. It only touches nodes that are not `net_device_ready`.
- **Changed node password:** the unit sets no `NODE_PW`, so `vca2-shutdown`
  uses `vista1`. If that fails, it waits out its 200 s and then detaches the
  images from nodes that are still running. Add a drop-in
  (`systemctl edit vca2-card`, `Environment=NODE_PW=...`). The same happens
  if `sshpass` is not installed.
- **Stock images under the service:** the script boots
  `node<n>-ubuntu-jammy.vcad` unless `VCA_IMAGE_SUFFIX` is set.
  `Environment=VCA_IMAGE_SUFFIX=` (empty) in a drop-in selects `node<n>.vcad`.

> **Not tested under systemd:** both drop-ins above. The script reads both
> variables; nobody has run the unit with them set.

Measured on a real reboot (2026-09-22, one card): host back in about
1.5 minutes, nodes `net_device_ready` about 80 s later.

## 7. Cooling and power caps — before the first long load

The card has no fans. With only the host case's own airflow, an all-node AVX2
load reached **92–94 °C** within minutes, against Intel's 96 °C limit. Cores
went from 66 to 86 °C in six seconds when the load started
([THERMAL.md](THERMAL.md)). The CPU case is the hot one, not the GPU: 39 W in
four cores runs about 20 °C hotter than 35 W across the iGPU. Design and test
cooling against `compute/burn.c`, not a GPU benchmark.

What the two cards in this host have:

| | Cooling | Peak, all-core load on every live node¹ | Package cap |
|---|---|---:|---|
| card 0 | 2×120 mm fans in this repo's printed 120 mm shroud, fitted 2026-09-22 | **75–79 °C** (was 92–94) | stock, PL1 45 W |
| card 1 | 2×80 mm fans | 71 / 80 °C capped; **94 °C** on cpu 2 uncapped | **PL1 35 W, PL2 40 W** |

¹ Card 0: `compute/thermal_run.sh` AVX2 phase, all three nodes. Card 1:
a 180 s all-core load on its two live nodes, after a repaste (2026-09-30);
not the same harness.

- Card 0's cooling took 15 to 18 °C off the CPU case and 16 °C off the GPU
  case. The constraint then moved away from temperature.

  That shroud is the v1 120 mm design from [`../shrouds/`](../shrouds/), the
  only one of the designs that has been printed and measured. The 80 mm and
  92 mm designs have not.

- Card 1 on 2×80 mm fans held the thermal limit under sustained Remotion
  renders. Its cpu 2 logged 6,531 throttle events. Card 1 cpu 0 (node 3)
  throttled on every CPU on 2026-09-28, the host logged a DMA hang 34 s
  later, and that node has not reached BIOS since, through a node power
  cycle, a card reset, a host power-off, a 60 s wall unplug (which rules out
  a latched thermal trip that only clears with standby power removed), a
  repaste and a RAM reseat. Heat damage is the leading suspect
  ([THERMAL.md](THERMAL.md)).

If your card does not have cooling you have measured, cap it before running
anything sustained. [`../scripts/card1_power_cap.sh`](../scripts/card1_power_cap.sh)
writes a `rapl-cap.service` unit onto each node's own disk, so the cap
survives reboots, and sets PL1 35 W / PL2 40 W. Despite the name it works on
any node:

```bash
CARD1_NODES="172.31.1.1 172.31.2.1 172.31.3.1" bash scripts/card1_power_cap.sh
```

It prints each node's PL1, PL2 and unit state. It uses the password `vista1`
directly and ignores `NODE_PW`; edit line 22 if you changed it. With the cap,
a 180 s all-core load on card 1 cpu 2 peaked at 81 °C with no throttle
events. To remove it: `systemctl disable --now rapl-cap` on the node, then
reboot it or write `45000000` / `56250000` back.

Raising PL1 above 45 W bought nothing here (+0.1%, within noise); see
THERMAL.md for why.

**Measure your own card** before trusting it with long jobs.
[`../compute/thermal_run.sh`](../compute/thermal_run.sh) runs idle, GPU,
GPU+CPU and CPU phases on a node. It enforces its trip on the node, so a
dropped SSH session cannot leave the load running. Keep the trip at 85 °C:
the overshoot between 5 s samples was 4–6 °C. The script header lists what it
expects on the node. Afterwards,
[`../monitor/README.md`](../monitor/README.md) (`bash monitor/install_host.sh`)
logs every node's temperature, power and throttle counters every 30 s.

If you print a shroud: push air into the card, do not pull (the v1 profiles
stall as diffusers), seal the cable slots, and use a high static pressure
fan. Leakage and fan choice matter more than the duct profile
([`../shrouds/README.md`](../shrouds/README.md),
[`../shrouds/REFERENCE.md`](../shrouds/REFERENCE.md)).

## 8. Recovery

From [GOTCHAS.md](GOTCHAS.md), which has the full stories. The rules:

- **Read the previous boot's kernel log first.** After any host wedge,
  `journalctl -b -1 -k`. Three power cycles were spent guessing before
  anyone did.
- **`vcactl reset` is refused while a node has RW BlockIO attached**, and
  rightly so. Shut the node down from inside (`systemctl poweroff`) or with
  `vcactl pwrbtn-short <card> <cpu>`. Do not use `--force`.
- **`pwrbtn-short` toggles**, and a press during `powering_down` or
  `link_down` is eaten. Wait for a settled `power_off` and read
  `vcactl status` between presses.
- **A node whose sshd is dead** (pings, resets connections): `pwrbtn-short`
  sends ACPI, which systemd still honours. Wait for `power_off`, then bring it
  back with `/usr/local/sbin/vca2-boot`.
- **A node stuck in `booting`** (`Can not open Dev_page`): `vcactl blockio
  close <card> <cpu> vcablk0`, `pwrbtn-long` to a settled `power_off`,
  `pwrbtn-short` to `bios_up`, then attach and boot again. The boot script
  tries this once on its own.
- **A node parked in `link_down` after a reset while it was powered off**
  never moves on. `pwrbtn-long` → `power_off` → `pwrbtn-short`. The boot
  script does this before any reset.
- **`dma_hang`.** A per-node power cycle does not clear it. GOTCHAS puts the
  flag in the host driver's per-card DMA state, which only a card reset or
  host reboot clears. A card reset reboots its other nodes, so power those
  off cleanly first. Inferred, not traced: card 1 cpu 0 came back from
  neither.
- **The host pings, but SSH never completes a login.** rtnl is stuck. Only a
  power cycle recovers it; `reboot` waits forever on a D-state `rmmod`, and
  sysrq `b` does not always work on this host. The known cause, the old
  `jumbo_mtu`, is fixed.
- **`unregister_netdevice: waiting for ethN`** with `vcactl` in D state: an
  old `jumbo_mtu.ko` is holding the NIC. `rmmod jumbo_mtu` clears it with no
  reboot.
- After a hard host reset, the nodes' `/boot` (FAT) logs "not properly
  unmounted". That is expected and harmless.

Never `rmmod plx87xx`, never read the debugfs files on the list, and with two
cards never issue an unscoped `vcactl reset`.

## 9. What next

| | |
|---|---|
| Is the card worth the slot? | [`../bench/vca-bench.sh`](../bench/vca-bench.sh), [BENCHMARK.md](BENCHMARK.md) |
| A local memory/RAG service on the card | [`../memory/README.md`](../memory/README.md), [`../memory/RESULTS.md`](../memory/RESULTS.md); `memory/deploy/install_host.sh` installs it with the card unit |
| Remotion render farm | [`../farm/README.md`](../farm/README.md) |
| Temperature and power history | [`../monitor/README.md`](../monitor/README.md) |
| The NTB DMA engine, node to node at 4.4 GB/s | [DMA.md](DMA.md), [FABRIC.md](FABRIC.md); the runners call [`../compute/dma/preflight.sh`](../compute/dma/preflight.sh) first |
| OpenCL, Quick Sync, GEMM on the P580s | [GPU.md](GPU.md), [GEMM_WORKGROUP.md](GEMM_WORKGROUP.md) |
| LLMs on the nodes | [LLAMA.md](LLAMA.md), [BENCHMARK.md](BENCHMARK.md) |
| What the host really runs | [HOST_STACK.md](HOST_STACK.md), [KERNEL_PORT.md](KERNEL_PORT.md), [PRIOR_ART.md](PRIOR_ART.md) |
| What is still open | [OPEN_QUESTIONS.md](OPEN_QUESTIONS.md) |
