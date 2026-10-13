<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# scripts

Host-side scripts for running the VCA 2 cards. They are written for the
author's Proxmox host (images under `/tank-red/vmdata/vca/`, node IPs
`172.31.(n+1).1`), so read one before running it. Nodes are numbered 0-5
globally: node n is card n//3, cpu n%3.

| Script | Where / who | What it does |
|---|---|---|
| [`vca2-boot.sh`](vca2-boot.sh) | host, root | The real boot path. Installed as `/usr/local/sbin/vca2-boot` and run by [`../systemd/vca2-card.service`](../systemd/vca2-card.service) at host boot (formerly `scripts/_boot_after_reboot.sh`, the name [`docs/LOG.md`](../docs/LOG.md) uses). Checks the kernel against a known-good list, loads the VCA modules, brings up card 0 and, when present with images, card 1, node by node (a dead node is logged and skipped, a stuck-in-`booting` node gets one power-cycle retry), then loads `jumbo_mtu` for MTU 65521 on the host NICs and ping-checks every live node. Safe to re-run: a card whose nodes are all up is left alone. |
| [`vca2-shutdown.sh`](vca2-shutdown.sh) | host, root | The `ExecStop` of `vca2-card.service`. SSHes `sync; systemctl poweroff` to every attached node on both cards, waits for `power_off`, then closes their blockio disk images so they are not left mounted read-write. Uses `NODE_PW`. |
| [`vca2-enable-nat.sh`](vca2-enable-nat.sh) | host, root | Turns on IPv4 forwarding and adds the MASQUERADE and FORWARD rules for `172.31.0.0/16` out of `vmbr0` (edit `OUT_IF` if yours differs), saving them with `netfilter-persistent` if installed. Idempotent; the unit runs it after boot as belt and braces. |
| [`vca2-status.sh`](vca2-status.sh) | host | Read-only snapshot: kernel, `vcactl status` and `temp`, blockio list, VCA/VOP modules, virtio devices, and a ping of nodes 0-2 (card 0 only; it predates the second card). |
| [`card1_power_cap.sh`](card1_power_cap.sh) | host, runs over SSH on nodes | Caps package power on card 1's live nodes at PL1 35 W / PL2 40 W through a persistent `rapl-cap.service` on each node's disk. Defaults to nodes 4 and 5 (node 3 has been dead since 2026-09-28; override with `CARD1_NODES`). See [`docs/THERMAL.md`](../docs/THERMAL.md). Its password is hardcoded to `vista1`. |
| [`vca2-guest-hold-sensitive.sh`](vca2-guest-hold-sensitive.sh) | **on a node**, root | `apt-mark hold`s the boot, init, systemd, udev and libc packages on a node's Ubuntu image so an `apt upgrade` cannot replace them, then prints the held list and a simulated upgrade. |

`NODE_PW` is the node root password used by the scripts that SSH into nodes
through `sshpass`. It defaults to `vista1`, Intel's documented default for the
VCA images; set it in the environment if you changed it.

Everything that touches `vcactl`, modules, iptables or `/sys` needs root on
the host. Only the guest-hold script runs on a node.

## Other directories

- [`sessions/`](sessions/README.md): the 75 one-off scripts from the
  exploration sessions, kept for provenance. Not maintained.
- [`legacy/`](legacy/README.md): the first boot path (card 0 only),
  superseded by `vca2-boot.sh`.
