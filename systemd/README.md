<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# systemd/ — host unit for the card

`vca2-card.service` boots the VCA 2 nodes when the host starts and powers
them off cleanly when it stops. Intel's `vcactl.service` only starts the
`vcactld` daemon (auto-boot is off), so without this unit nothing boots the
nodes.

| Unit line | Runs | Installed from |
|---|---|---|
| `ExecStart` | `/usr/local/sbin/vca2-boot` | [`scripts/vca2-boot.sh`](../scripts/vca2-boot.sh) |
| `ExecStartPost` (may fail) | `/usr/local/sbin/vca2-enable-nat` | [`scripts/vca2-enable-nat.sh`](../scripts/vca2-enable-nat.sh) |
| `ExecStop` | `/usr/local/sbin/vca2-shutdown` | [`scripts/vca2-shutdown.sh`](../scripts/vca2-shutdown.sh) |

`vca2-boot` brings up card 0 (required) and card 1 (only if present and its
images exist) on the per-node Ubuntu 22.04 images, skips a node that never
reaches BIOS instead of failing the unit, loads `jumbo_mtu` on the host NICs
and checks each live node end to end. `TimeoutStartSec=1800` covers the
retries a dead node costs on a cold boot.

Install: [`memory/deploy/install_host.sh`](../memory/deploy/install_host.sh)
copies the three scripts and this unit into place and enables it alongside
`smem-api.service`. It replaces `vca2-autoboot.service` and `vca2-start.sh`,
kept in [`scripts/legacy/`](../scripts/legacy/).

Re-running `systemctl start vca2-card` on a live system only touches nodes
that are not up. Use `start`, never `restart`: `restart` runs `ExecStop`
first, powering every node off, and once left all six stranded in
`link_down` (the boot script now recovers from that, but it costs the
outage). See [GOTCHAS.md](../docs/GOTCHAS.md) and [SAFETY.md](../SAFETY.md).
