#!/bin/bash
# SPDX-License-Identifier: MIT
#
# Verify a VCA node image ON THE NODE. Ships inside the image at
# /opt/vca-tools/verify_node_image.sh, so:
#
#     ssh root@172.31.N.1 /opt/vca-tools/verify_node_image.sh
#
# This exists because of a specific mistake: two nodes were hand-patched live
# (libelf-dev installed by dpkg, plt32-to-pc32 scp'd in, iperf3 added) to get
# a benchmark finished, and nothing recorded that they no longer matched the
# build script. A fleet that reports its own state does not drift silently.
#
# Exit 0 = everything the builder promised is actually here.
# Exit 1 = at least one FAIL. WARN does not fail the run.
set -u

PASS=0; FAIL=0; WARN=0
ok()   { printf '  \033[32mok\033[0m    %s\n' "$*"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %s\n' "$*"; FAIL=$((FAIL+1)); }
warn() { printf '  \033[33mwarn\033[0m  %s\n' "$*"; WARN=$((WARN+1)); }
hdr()  { printf '\n=== %s ===\n' "$*"; }

hdr "Image identity"
if [ -r /etc/vca-image-release ]; then
    sed 's/^/  /' /etc/vca-image-release
    ok "/etc/vca-image-release present"
else
    bad "/etc/vca-image-release MISSING -- this node was not built by build_node_image.sh,
        or it predates the manifest. Treat its configuration as unknown."
fi
echo "  running: $(grep -oP '(?<=^PRETTY_NAME=").*(?="$)' /etc/os-release 2>/dev/null)"
echo "  kernel:  $(uname -r)"

hdr "Toolchain"
if command -v gcc >/dev/null; then ok "gcc $(gcc -dumpversion)"; else bad "no gcc"; fi
if command -v make >/dev/null; then ok "make"; else bad "no make"; fi
# The one that silently breaks every module build on this kernel.
[ -e /usr/include/libelf.h ] && ok "libelf.h (objtool needs it: CONFIG_UNWINDER_ORC=y)" \
                             || bad "libelf.h MISSING -- module builds will die at
        'Cannot generate ORC metadata'. Install libelf-dev."
[ -e /usr/local/sbin/plt32-to-pc32 ] && ok "plt32-to-pc32" \
    || bad "plt32-to-pc32 MISSING -- modules will compile but not load
        ('Unknown rela relocation: 4'). See docs/NODE_IMAGE.md."

KB="/lib/modules/$(uname -r)/build"
[ -d "$KB" ] && ok "kernel headers at $KB" || bad "no kernel headers at $KB"

hdr "Kernel module build (the real test)"
# Everything above is necessary; only this proves sufficiency.
T=$(mktemp -d)
cat > "$T/vcaprobe.c" <<'EOF'
#include <linux/module.h>
#include <linux/kernel.h>
static int __init p_init(void) { pr_info("vcaprobe: ok\n"); return 0; }
static void __exit p_exit(void) { pr_info("vcaprobe: bye\n"); }
module_init(p_init); module_exit(p_exit);
MODULE_LICENSE("GPL");
MODULE_DESCRIPTION("build+load self-test");
EOF
cat > "$T/Makefile" <<'EOF'
obj-m := vcaprobe.o
KDIR ?= /lib/modules/$(shell uname -r)/build
all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules
EOF
if make -C "$T" >"$T/build.log" 2>&1 && [ -f "$T/vcaprobe.ko" ]; then
    ok "module compiles"
    if [ -x /usr/local/sbin/plt32-to-pc32 ]; then
        N=$(/usr/local/sbin/plt32-to-pc32 "$T/vcaprobe.ko" 2>&1 | grep -oE '[0-9]+ PLT32' | head -1)
        ok "relocations rewritten (${N:-0 PLT32})"
    fi
    if insmod "$T/vcaprobe.ko" 2>"$T/ins.log"; then
        ok "module LOADS"
        rmmod vcaprobe 2>/dev/null
    else
        bad "module built but will NOT load: $(tr -d '\n' < "$T/ins.log")
        dmesg: $(dmesg 2>/dev/null | tail -1)"
    fi
else
    bad "module build FAILED: $(grep -m1 -iE 'error|\*\*\*' "$T/build.log" 2>/dev/null)"
fi
rm -rf "$T"

hdr "Network bring-up"
if systemctl list-unit-files 2>/dev/null | grep -q '^vca-netcfg.service'; then
    S=$(systemctl is-active vca-netcfg.service 2>/dev/null)
    [ "$S" = active ] && ok "vca-netcfg.service active" || warn "vca-netcfg.service is '$S'"
else
    bad "vca-netcfg.service not installed -- on systemd >= 249 the stock udev
        rule does not configure the interface and the node hangs at
        net_device_no_ip"
fi
IP=$(ip -4 -br addr show dev eth0 2>/dev/null | awk '{print $3}')
[ -n "$IP" ] && ok "eth0 $IP" || bad "eth0 has no IPv4 address"
MTU=$(cat /sys/class/net/eth0/mtu 2>/dev/null)
[ "${MTU:-0}" -ge 65521 ] 2>/dev/null && ok "eth0 MTU $MTU (jumbo)" \
                                      || warn "eth0 MTU ${MTU:-?} (host-side jumbo_mtu.ko may not be loaded)"

# Routing and name resolution fail independently here, and the symptoms look
# alike, so check them separately. The classic failure is a dangling
# /etc/resolv.conf symlink to systemd-resolved's stub (resolved is disabled on
# VCA nodes): routing is perfect, names resolve to nothing.
if [ -L /etc/resolv.conf ]; then
    bad "/etc/resolv.conf is a SYMLINK -> $(readlink /etc/resolv.conf)
        systemd-resolved is disabled here, so its stub is never created and
        the node has no DNS. It should be a regular file."
elif [ -s /etc/resolv.conf ] && grep -q '^nameserver' /etc/resolv.conf; then
    ok "resolv.conf: $(awk '/^nameserver/{printf "%s ", $2}' /etc/resolv.conf)"
else
    bad "/etc/resolv.conf has no nameserver"
fi
ping -c1 -W3 1.1.1.1 >/dev/null 2>&1 && ok "routing off-card (host masquerade works)" \
                                     || warn "cannot reach 1.1.1.1 (host NAT/forwarding?)"
if getent hosts archive.ubuntu.com >/dev/null 2>&1; then
    ok "DNS resolves -- apt works on this node"
else
    bad "DNS does not resolve. apt will fail here, which is what pushes people
        into hand-patching nodes instead of rebuilding the image."
fi

hdr "GPU / OpenCL"
if command -v clinfo >/dev/null; then
    # clinfo pads its output into columns; it is NOT colon-separated. Parsing
    # it with -F: silently yields an empty field and reports "no OpenCL
    # device" on a node whose GPU is working perfectly. Take the last field
    # for the number, and everything after the two-word label for the name.
    CLI=$(clinfo 2>/dev/null)
    CU=$(printf '%s\n' "$CLI" | awk '/Max compute units/{print $NF; exit}')
    DEV=$(printf '%s\n' "$CLI" | awk '/Device Name/{$1="";$2="";sub(/^ +/,""); print; exit}')
    if [ -n "${CU:-}" ]; then
        ok "OpenCL: ${DEV:-device}, ${CU} compute units"
        [ "$CU" -lt 20 ] 2>/dev/null && warn "only $CU CUs -- the P580 should report 72"
    else
        bad "clinfo found no OpenCL device (ICD loader present but no usable driver?)"
    fi
else
    warn "clinfo not installed"
fi
[ -d /opt/intel-legacy ] && ok "/opt/intel-legacy fallback present" \
                         || warn "no /opt/intel-legacy fallback"

hdr "Media"
if command -v vainfo >/dev/null; then
    EP=$(vainfo 2>/dev/null | grep -c VAProfileH264)
    [ "${EP:-0}" -gt 0 ] && ok "VA-API: $EP H.264 entrypoints" || warn "vainfo found no H.264 entrypoints"
else
    warn "vainfo not installed"
fi
command -v ffmpeg >/dev/null && ok "ffmpeg ($(ffmpeg -hide_banner -hwaccels 2>/dev/null | tail -n +2 | tr '\n' ' '))" \
                             || warn "no ffmpeg"

hdr "Tuning"
MF=$(sysctl -n vm.min_free_kbytes 2>/dev/null)
[ "${MF:-0}" -ge 524288 ] 2>/dev/null && ok "vm.min_free_kbytes=$MF" || warn "vm.min_free_kbytes=${MF:-?} (expected >= 524288)"
for m in overlay veth bridge; do
    lsmod 2>/dev/null | grep -q "^$m " && ok "module $m loaded" || warn "module $m not loaded"
done

printf '\n=== %d passed, %d warnings, %d FAILED ===\n' "$PASS" "$WARN" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
