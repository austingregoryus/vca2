#!/bin/bash
# SPDX-License-Identifier: MIT
set -euo pipefail
cd /usr/src/vca2-vcass-2.3.26+ubuntu24.04.10
make VCA_BUILD_ROLE=host VCA_CARD_ARCH=l1om \
  KERNEL_VERSION="$(uname -r)" \
  KERNEL_SRC="/lib/modules/$(uname -r)/build" \
  KERNWARNFLAGS= \
  modules 2>&1 | tail -80
ls -l plx87xx.ko vca/plx87xx/plx_mbox.o 2>/dev/null || true
echo MAKE_DONE
