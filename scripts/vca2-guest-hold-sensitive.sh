#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail

[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 1; }

apt-mark hold \
  grub-common grub2-common grub-efi-amd64-bin \
  initramfs-tools-bin initramfs-tools-core busybox-initramfs \
  systemd systemd-sysv libsystemd0 libpam-systemd \
  udev libudev1 \
  linux-libc-dev \
  libc6 libc-bin libc6-dev libc-dev-bin multiarch-support

echo
echo "Held packages:"
apt-mark showhold

echo
echo "Simulated remaining upgrade:"
apt-get -s upgrade
