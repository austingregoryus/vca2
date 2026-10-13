#!/bin/bash
# SPDX-License-Identifier: MIT
# SAFE: alm_state, spad, msi_irq_info, vcactl only.
echo '=== kernel ==='
uname -r
echo '=== dkms ==='
dkms status
ls -d /usr/src/vca* 2>/dev/null
ls /usr/src/vca2-vcass-*/plx87xx 2>/dev/null | head
echo '=== temps ==='
vcactl temp 2>/dev/null | grep 'Core 0'
echo '=== node0 alm_state empty vs used ==='
# only seq_printf dump
python3 - <<'PY'
import re
p='/sys/kernel/debug/plx87xx/0000:05:00.0/alm_state'
text=open(p).read()
print(text.split('\n',1)[0])
used=empty=lazy=0
first_empty=None
for line in text.splitlines():
    m=re.search(r'\[([0-9a-f]+)\]', line)
    if not m: continue
    sid=m.group(1)
    if 'empty' in line:
        empty+=1
        if first_empty is None:
            first_empty=sid
    else:
        used+=1
        rc=re.search(r'ref_cnt:(\d+)', line)
        if rc and rc.group(1)=='1':
            lazy+=1
print('used_lines', used, 'empty', empty, 'lazy_ref1', lazy, 'first_empty', first_empty)
PY
echo '=== modules ==='
lsmod | awk '/^plx87xx /{print}'
echo '=== symbols plx_alm ==='
grep -E 'plx_alm_add_entry|plx_ioremap|plx_send_intr' /proc/kallsyms | head
