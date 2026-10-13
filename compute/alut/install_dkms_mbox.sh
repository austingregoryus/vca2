#!/bin/bash
# SPDX-License-Identifier: MIT
# Patch DKMS tree and BUILD only (does not unload running plx87xx).
set -euo pipefail
SRC=/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10
KVER=$(uname -r)
cp -a /tmp/vca_compute/alut/plx_mbox.c "$SRC/vca/plx87xx/plx_mbox.c"
cp -a /tmp/vca_compute/alut/plx_mbox.h "$SRC/vca/plx87xx/plx_mbox.h"

if ! grep -q plx_mbox.o "$SRC/Kbuild"; then
  sed -i '/plx_hw_ops_vop.o/a plx87xx-objs += vca/plx87xx/plx_mbox.o' "$SRC/Kbuild"
fi

# debugfs: include + create files + remove hook
if ! grep -q plx_mbox.h "$SRC/vca/plx87xx/plx_debugfs.c"; then
  sed -i '/#include "plx_hw.h"/a #include "plx_mbox.h"' "$SRC/vca/plx87xx/plx_debugfs.c"
fi
python3 - <<'PY'
from pathlib import Path
p = Path("/usr/src/vca2-vcass-2.3.26+ubuntu24.04.10/vca/plx87xx/plx_debugfs.c")
t = p.read_text()
if "plx_mbox.h" not in t:
    t = t.replace('#include "plx_hw.h"\n', '#include "plx_hw.h"\n#include "plx_mbox.h"\n')
if "mbox_alloc" not in t:
    needle = '\tdebugfs_create_file("alm_state", 0444, xdev->dbg_dir, xdev,\n\t\t\t    &alm_ops);'
    add = needle + '''
	debugfs_create_file("mbox_info", 0444, xdev->dbg_dir, xdev,
			    &plx_mbox_info_ops);
	debugfs_create_file("mbox_alloc", 0200, xdev->dbg_dir, xdev,
			    &plx_mbox_alloc_ops);'''
    if needle not in t:
        raise SystemExit('alm_state needle missing')
    t = t.replace(needle, add, 1)
if "plx_mbox_remove" not in t:
    needle = 'void plx_delete_debug_dir(struct plx_device *xdev)\n{\n\tif (!xdev->dbg_dir)\n\t\treturn;'
    add = 'void plx_delete_debug_dir(struct plx_device *xdev)\n{\n\tplx_mbox_remove(xdev);\n\tif (!xdev->dbg_dir)\n\t\treturn;'
    if needle not in t:
        raise SystemExit('delete_debug_dir needle missing')
    t = t.replace(needle, add, 1)
p.write_text(t)
print('debugfs patched')
PY

echo '=== Kbuild snippet ==='
grep mbox "$SRC/Kbuild"
echo '=== debugfs grep mbox ==='
grep mbox "$SRC/vca/plx87xx/plx_debugfs.c"

echo '=== dkms build (no install) ==='
dkms build -m vca2-vcass -v 2.3.26+ubuntu24.04.10 -k "$KVER"
echo BUILD_OK
