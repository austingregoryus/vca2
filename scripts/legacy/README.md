<!-- SPDX-License-Identifier: CC-BY-4.0 -->
# scripts/legacy

The first boot path, written in September 2026 for **card 0 only**. It is
superseded by [`../vca2-boot.sh`](../vca2-boot.sh) run from
[`../../systemd/vca2-card.service`](../../systemd/vca2-card.service), which
handles two cards, dead nodes and a clean shutdown.

| File | What it was |
|---|---|
| `vca2-start.sh` | Boots card 0's three nodes: checks the kernel, loads modules, resets, attaches the images and boots, waits, then loads `jumbo_mtu`. Aborts the whole card if any node fails to come up. |
| `vca2-stop.sh` | Asks card 0's nodes to shut down and closes their blockio disks. |
| `vca2-autoboot.service` | The systemd unit that ran `vca2-start` at host boot. Replaced by `vca2-card.service`. |

They are kept because [`docs/archive/SETUP_GUIDE_2026-09.md`](../../docs/archive/SETUP_GUIDE_2026-09.md)
documents them. Do not use them with a second card installed.
