#!/bin/bash
# SPDX-License-Identifier: MIT
# Install experimental plx87xx and reboot the HOST (do not rmmod).
set -euo pipefail
NEW=/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10/plx87xx.ko
DEST=/lib/modules/$(uname -r)/updates/dkms/plx87xx.ko
test -f "$NEW"
test -f /root/plx87xx.ko.bak
echo "kernel=$(uname -r)"
ls -l "$NEW" "$DEST" /root/plx87xx.ko.bak
cp -a "$NEW" "$DEST"
depmod -a
echo "installed new plx87xx.ko, rebooting"
sync
sleep 1
systemctl reboot
