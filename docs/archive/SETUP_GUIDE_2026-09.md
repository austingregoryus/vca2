<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# Intel VCA2 Homelab Guide

> **Archived 2026-10-13.** The setup guide as it stood on 2026-09-19/21, with one card. It is stale on the kernel (7.0.14 works), the node OS (22.04 now), the boot unit (`vca2-card.service`, not `vca2-autoboot.service`), one card versus two, and cooling. Read [../SETUP.md](../SETUP.md) instead. The start and stop scripts it installs are kept in [../../scripts/legacy/](../../scripts/legacy/).

Exploration notes (PCIe fabric, mailbox PoC, Quick Sync, llama.cpp, debugfs
landmines) are in [`docs/README.md`](../README.md). Snapshot:
[`docs/STATUS.md`](../STATUS.md). Read [`docs/GOTCHAS.md`](../GOTCHAS.md)
before poking sysfs/debugfs.

This guide documents a working Intel Visual Compute Accelerator 2 (VCA2 / VCA1585LMV) homelab setup on a Proxmox host, including the problems encountered, the fixes that worked, persistent disk boot, networking/NAT, cooling, package updates, and automation scripts.

The configuration documented here uses one VCA2 card with all three onboard compute nodes running Linux from persistent `.vcad` BlockIO images.

## 1. Working configuration

### Proxmox host

Known-good host kernel:

```text
7.0.2-6-pve
```

A newer kernel was tested:

```text
7.0.14-17-pve
```

The VCA2 DKMS driver compiled successfully on `7.0.14-17-pve`, but the VOP / virtio network path did not work correctly. The nodes booted Linux, but remained in `net_device_down`, `/sys/bus/vca_virtio/devices/` stayed empty, and no VCA virtual NICs appeared.

Booting back into `7.0.2-6-pve` restored normal operation and all three nodes reached:

```text
net_device_ready
```

For this setup, keep `7.0.2-6-pve` pinned unless the VCA host driver is patched and validated against a newer kernel.

### VCA2 guest nodes

Observed guest kernel:

```text
Linux 4.14.20-1.2.3.26.vca
```

Guest OS is based on Ubuntu 16.04 Xenial with Intel's VCA-specific kernel and software stack.

### Persistent node disks

```text
/tank-red/vmdata/vca/images/node0.vcad
/tank-red/vmdata/vca/images/node1.vcad
/tank-red/vmdata/vca/images/node2.vcad
```

Each image is attached to the corresponding node as `vcablk0`.

### Network addressing

| VCA node | Node IP | Host-side IP / gateway |
|---|---:|---:|
| Card 0 CPU 0 | 172.31.1.1 | 172.31.1.254 |
| Card 0 CPU 1 | 172.31.2.1 | 172.31.2.254 |
| Card 0 CPU 2 | 172.31.3.1 | 172.31.3.254 |

SSH commands:

```bash
ssh root@172.31.1.1
ssh root@172.31.2.1
ssh root@172.31.3.1
```

## 2. Host driver stack

A healthy host showed modules similar to:

```text
vca_mgr_extd
vca_mgr
vca_csm
vcablk_bckend
vcablkfe
vca_csa_bus
vca_mgr_extd_bus
vca_mgr_bus
vca_csm_bus
vop
vca_vringh
vca_virtio_net
vca_virtio
vca_virtio_ring
vop_bus
plx87xx
```

Useful checks:

```bash
lsmod | grep -Ei 'vca|vop|plx'
vcactl status
```

If `vcactl status` reports:

```text
ERROR: Count ready devices failed!
```

first verify whether the VCA modules are loaded.

A custom helper is present on this host:

```bash
/usr/sbin/vca2-load-modules
```

Run:

```bash
/usr/sbin/vca2-load-modules
lsmod | grep -Ei 'vca|vop|plx'
vcactl status
```

## 3. Kernel mismatch problem

After a Proxmox reboot, the host booted `7.0.14-17-pve`, while DKMS initially showed the VCA driver installed only for:

```text
7.0.2-6-pve
```

The immediate symptom was:

```text
modprobe: FATAL: Module vop_bus not found in directory /lib/modules/7.0.14-17-pve
```

The VCA driver was later rebuilt for the newer kernel and loaded successfully, but runtime virtio networking still failed.

The important lesson is that a successful DKMS compile is not proof of runtime compatibility.

### Check current kernel and DKMS state

```bash
uname -r
dkms status
find /lib/modules/$(uname -r) -type f | grep -Ei 'vca|vop|plx'
```

### Pin the known-good Proxmox kernel

If this host uses `proxmox-boot-tool`:

```bash
proxmox-boot-tool kernel list
proxmox-boot-tool kernel pin 7.0.2-6-pve
proxmox-boot-tool kernel list
```

Then reboot and verify:

```bash
uname -r
```

Expected:

```text
7.0.2-6-pve
```

If the host uses GRUB instead, select `7.0.2-6-pve` under Advanced Options during boot and configure GRUB appropriately before making it permanent.

## 4. VCA node state troubleshooting

Common states encountered:

### `link_down`

The host driver can see the card, but the node link is not established or the node is not ready.

Try:

```bash
vcactl reset
vcactl wait-BIOS
vcactl status
```

Expected next states are `bios_up`, `bios_ready`, or `waiting_for_boot`.

### `bios_down`

The node has not reached usable BIOS state yet.

Try:

```bash
vcactl reset
vcactl wait-BIOS
vcactl status
```

### `net_device_down`

This was especially important during troubleshooting.

On the incompatible/newer host kernel, all three nodes successfully booted their guest Linux kernel but remained at:

```text
net_device_down
```

At the same time:

```bash
ls -la /sys/bus/vop_bus/devices/
```

showed:

```text
vop-dev00
vop-dev01
vop-dev02
```

but:

```bash
ls -la /sys/bus/vca_virtio/devices/
```

was empty.

There were also no VCA NICs under `/sys/class/net`.

That isolated the failure to the VOP / VCA virtio device creation path rather than BlockIO or guest boot.

On `7.0.2-6-pve`, all nodes reached `net_device_ready` normally.

## 5. Persistent BlockIO boot

Do not pass a `.vcad` disk file directly to `vcactl boot`.

This is incorrect for a persistent BlockIO disk:

```bash
vcactl boot 0 0 /tank-red/vmdata/vca/images/node0.vcad
```

It can cause LBP errors such as:

```text
LBP_BOOT_RAMDISK returned with LBP_INTERNAL_ERROR
LBP_HANDSHAKE returned with LBP_IRQ_TIMEOUT
```

The correct process is:

1. Reset and wait for BIOS.
2. Attach the `.vcad` file to `vcablk0`.
3. Boot the node from `vcablk0`.

### Manual boot sequence

```bash
vcactl reset
vcactl wait-BIOS

vcactl blockio open 0 0 vcablk0 RW /tank-red/vmdata/vca/images/node0.vcad
vcactl blockio open 0 1 vcablk0 RW /tank-red/vmdata/vca/images/node1.vcad
vcactl blockio open 0 2 vcablk0 RW /tank-red/vmdata/vca/images/node2.vcad

vcactl blockio list

vcactl boot 0 0 vcablk0
vcactl boot 0 1 vcablk0
vcactl boot 0 2 vcablk0

vcactl wait
vcactl status
```

Expected final state:

```text
Card: 0 Cpu: 0 STATE: net_device_ready
Card: 0 Cpu: 1 STATE: net_device_ready
Card: 0 Cpu: 2 STATE: net_device_ready
```

## 6. Host-side VCA networking

The VCA helper script is:

```bash
/usr/sbin/vca_eth_ifup.sh
```

It accepts card ID and CPU ID:

```bash
/usr/sbin/vca_eth_ifup.sh 0 0
/usr/sbin/vca_eth_ifup.sh 0 1
/usr/sbin/vca_eth_ifup.sh 0 2
```

It locates the VCA virtual NIC based on expected MAC addresses:

```text
CPU 0: fe:00:00:00:01:01
CPU 1: fe:00:00:00:01:02
CPU 2: fe:00:00:00:01:03
```

Then it assigns host-side addresses, sets MTU 65535, brings the interface up, and configures routes.

If it reports:

```text
Cannot determine card's dev
```

check whether the VCA virtio devices actually exist:

```bash
ls -la /sys/bus/vca_virtio/devices/
ip -br link
```

If the virtio bus is empty, the problem is earlier in the VOP/virtio stack, not in this script.

## 7. Internet access for VCA nodes

The VCA nodes route through the Proxmox host.

Enable forwarding persistently:

```bash
cat >/etc/sysctl.d/99-vca-forwarding.conf <<'EOF2'
net.ipv4.ip_forward=1
EOF2

sysctl --system
```

Verify:

```bash
sysctl net.ipv4.ip_forward
```

Expected:

```text
net.ipv4.ip_forward = 1
```

### NAT and forwarding rules

Assuming the host's outbound interface is `vmbr0`:

```bash
iptables -t nat -A POSTROUTING -s 172.31.0.0/16 -o vmbr0 -j MASQUERADE
iptables -A FORWARD -s 172.31.0.0/16 -o vmbr0 -j ACCEPT
iptables -A FORWARD -d 172.31.0.0/16 -i vmbr0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

Check the actual default route first:

```bash
ip route | grep default
```

### Persist iptables rules

Install:

```bash
apt install iptables-persistent
```

Save:

```bash
netfilter-persistent save
```

Verify:

```bash
cat /etc/iptables/rules.v4
systemctl is-enabled netfilter-persistent
systemctl status netfilter-persistent --no-pager
```

The saved rules should include:

```text
-A POSTROUTING -s 172.31.0.0/16 -o vmbr0 -j MASQUERADE
-A FORWARD -s 172.31.0.0/16 -o vmbr0 -j ACCEPT
-A FORWARD -d 172.31.0.0/16 -i vmbr0 -m conntrack --ctstate RELATED,ESTABLISHED -j ACCEPT
```

### Connectivity test from a node

Node 0:

```bash
ping -c 3 172.31.1.254
ping -c 3 1.1.1.1
ping -c 3 google.com
```

Interpretation:

- gateway fails: VCA host-side networking problem
- gateway works, `1.1.1.1` fails: host routing/NAT problem
- `1.1.1.1` works, DNS name fails: DNS problem

## 8. Temperature monitoring

From the Proxmox host:

```bash
vcactl temp
```

Continuous watch:

```bash
watch -n 1 vcactl temp
```

or:

```bash
watch -n 2 vcactl temp
```

## 9. Cooling solution tested

The VCA2 was fitted with custom ducts using:

- one 80 mm intake fan pushing air into the card
- one 80 mm exhaust fan pulling air from the card
- factory VCA2 shroud retained as the main internal airflow path

This push/pull arrangement increases pressure across the restrictive heatsink path.

### Measured result

All three nodes were run simultaneously with:

```bash
stress-ng --cpu 8 --cpu-load 40 --timeout 15m --metrics-brief
```

The hottest core observed across the full test was:

```text
68 C
```

This was a much more realistic service-load test than pinning every CPU thread at 100 percent.

An earlier uncapped CPU stress test reached about 94 C after only a few minutes, showing how much more aggressive synthetic 100 percent load is compared with the intended service workload.

### Suggested realistic load tests

Moderate:

```bash
stress-ng --cpu 8 --cpu-load 40 --timeout 15m --metrics-brief
```

Heavier sustained service load:

```bash
stress-ng --cpu 8 --cpu-load 60 --timeout 15m --metrics-brief
```

CPU plus some memory activity:

```bash
stress-ng \
  --cpu 4 --cpu-load 35 \
  --vm 1 --vm-bytes 512M \
  --timeout 30m \
  --metrics-brief
```

For Python/Go web scraping and service workloads, sustained 40 to 60 percent synthetic CPU load is generally a more representative thermal test than continuous 100 percent CPU saturation.

## 10. Updating packages inside the VCA nodes

The guest nodes run an old Ubuntu 16.04 environment with Intel-specific boot/kernel components. Treat them more like appliances than ordinary Ubuntu servers.

`apt update` is fine, but do not blindly upgrade every package.

### Packages held during the successful canary upgrade

```bash
apt-mark hold \
  grub-common grub2-common grub-efi-amd64-bin \
  initramfs-tools-bin initramfs-tools-core busybox-initramfs \
  systemd systemd-sysv libsystemd0 libpam-systemd \
  udev libudev1 \
  linux-libc-dev \
  libc6 libc-bin libc6-dev libc-dev-bin multiarch-support
```

Then simulate:

```bash
apt-get -s upgrade
```

The canary node upgraded 101 userland packages while leaving 18 sensitive packages held.

After upgrading, verify:

```bash
apt-mark showhold
uname -a
systemctl --failed
```

The canary remained on:

```text
4.14.20-1.2.3.26.vca
```

and had zero failed systemd units.

The upgraded DBus package requested a reboot, so the final validation should include rebooting one node and confirming that it returns to `net_device_ready`, then checking SSH, routing, DNS, and failed services before applying the same upgrade to the other nodes.

## 11. Useful troubleshooting commands

### Host

```bash
uname -r
dkms status
vcactl status
vcactl temp
vcactl blockio list
lsmod | grep -Ei 'vca|vop|plx'
ip -br link
ip route
```

### Inspect VOP / virtio state

```bash
ls -la /sys/bus/vop_bus/devices/
ls -la /sys/bus/vop_bus/drivers/vop/
ls -la /sys/bus/vca_virtio/devices/
ls -la /sys/bus/vca_virtio/drivers/vca_virtio_net/
```

### VCA-specific dmesg filtering

```bash
dmesg | grep -Ei 'vca|vop|virtio|vring|dev_page|device page|plx' | tail -300
```

### Internet/NAT

```bash
sysctl net.ipv4.ip_forward
iptables -t nat -L POSTROUTING -n -v
iptables -L FORWARD -n -v
```

### Guest

```bash
uname -a
ip addr
ip route
ping -c 3 1.1.1.1
ping -c 3 google.com
systemctl --failed
```

## 12. Automation files included with this guide

This bundle contains:

```text
scripts/legacy/vca2-start.sh
scripts/legacy/vca2-stop.sh
scripts/vca2-status.sh
scripts/vca2-enable-nat.sh
scripts/vca2-guest-hold-sensitive.sh
scripts/legacy/vca2-autoboot.service
```

Review each script before installing it. Paths and interface names are configured for the setup documented above.

### Suggested installation

```bash
install -m 0755 scripts/legacy/vca2-start.sh /usr/local/sbin/vca2-start
install -m 0755 scripts/legacy/vca2-stop.sh /usr/local/sbin/vca2-stop
install -m 0755 scripts/vca2-status.sh /usr/local/sbin/vca2-status
install -m 0755 scripts/vca2-enable-nat.sh /usr/local/sbin/vca2-enable-nat

install -m 0644 scripts/legacy/vca2-autoboot.service /etc/systemd/system/vca2-autoboot.service
systemctl daemon-reload
systemctl enable vca2-autoboot.service
```

Before enabling automatic boot, run the start script manually once:

```bash
/usr/local/sbin/vca2-start
```

Then verify:

```bash
/usr/local/sbin/vca2-status
```

Only enable the systemd unit after the manual run succeeds reliably.

## 13. Important operational notes

1. Keep a backup or storage snapshot of the `.vcad` images before major guest changes.
2. Do not copy a live RW `.vcad` image as a backup unless the storage layer provides a consistent snapshot.
3. Keep the known-good Proxmox kernel installed even if newer kernels are tested.
4. Do not assume a successful DKMS build means the VCA driver is runtime-compatible.
5. Keep GRUB, initramfs, systemd, udev, glibc, and the guest kernel protected unless a controlled canary test proves an update is safe.
6. Treat node 0 as the canary for guest OS changes before changing nodes 1 and 2.
7. Keep temperature monitoring available from the host with `vcactl temp`.

