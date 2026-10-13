#!/bin/bash
# SPDX-License-Identifier: MIT
#
# VCA 2 node image builder.
#
# Builds a bootable node rootfs with a MODERN Linux userspace on Intel's
# 4.14 kernel, from a distro profile. Everything this project learned about
# making a non-2016 userspace work on this card is baked in.
#
# ---------------------------------------------------------------------------
# WHY THIS WORKS AT ALL
#
# The boot path lives entirely inside Intel's dracut initramfs
# (vca_initramfs.img on the ESP). That image carries every VCA module --
# plx87xx, vcablkfe, vop, vca_virtio* -- brings up the NTB, mounts
# /dev/vcablk0p2 and switch_roots into it.
#
# It is kernel-specific but COMPLETELY DISTRO-INDEPENDENT. It does not care
# what is in the rootfs beyond /sbin/init existing. So:
#
#     Keep Intel's kernel, initramfs and ESP VERBATIM.
#     Replace only the root filesystem.
#
# Boot then follows exactly the same path as the stock image. That single
# property is what makes this a *distro* builder rather than a port.
#
# ---------------------------------------------------------------------------
# STRUCTURE
#
#   profiles/<name>.conf   what distro, which mirrors, which packages
#   overlay/common/        files dropped into every rootfs, whatever the
#                          distro -- the VCA-specific layer
#   plt32_to_pc32.py       installed as /usr/local/sbin/plt32-to-pc32
#
# To add a distro: copy a profile, change the fields, build it. Nothing in
# this script is Ubuntu-specific; Debian differs only in mirror layout and
# component names, and both are profile fields.
#
# ---------------------------------------------------------------------------
# USAGE
#
#   ./build_node_image.sh --list
#   ./build_node_image.sh --profile ubuntu-jammy
#   ./build_node_image.sh --profile debian-bookworm --out /path/to/img.vcad
#   ./build_node_image.sh --profile ubuntu-noble --force-support
#
# SAFETY: only ever READS the reference image and WRITES the output file.
# node{0,1,2}.vcad are never touched. To try the result, point one node at the
# new file; to undo, point it back.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROFILE_DIR="${SCRIPT_DIR}/profiles"
OVERLAY_DIR="${SCRIPT_DIR}/overlay/common"

PROFILE="${PROFILE:-ubuntu-jammy}"
FORCE_SUPPORT=0
OUT=""

usage() {
    sed -n '3,48p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'
    exit "${1:-0}"
}

list_profiles() {
    printf "%-22s %-16s %s\n" PROFILE SUPPORT DESCRIPTION
    printf "%-22s %-16s %s\n" "------" "-------" "-----------"
    for f in "${PROFILE_DIR}"/*.conf; do
        [ -e "$f" ] || continue
        ( # subshell: do not leak one profile's vars into the next
          SUPPORT=?; DESCRIPTION=""
          # shellcheck disable=SC1090
          . "$f"
          printf "%-22s %-16s %s\n" "$(basename "$f" .conf)" "$SUPPORT" "$DESCRIPTION"
        )
    done
    exit 0
}

while [ $# -gt 0 ]; do
    case "$1" in
        --profile|-p)    PROFILE="$2"; shift 2 ;;
        --out|-o)        OUT="$2"; shift 2 ;;
        --list|-l)       list_profiles ;;
        --force-support) FORCE_SUPPORT=1; shift ;;
        --help|-h)       usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 1 ;;
    esac
done

say(){ echo -e "\n=== $* ==="; }
die(){ echo "ERROR: $*" >&2; exit 1; }

# ------------------------------------------------------------------- profile
PROFILE_FILE="${PROFILE_DIR}/${PROFILE}.conf"
[ -f "${PROFILE_FILE}" ] || die "no such profile '${PROFILE}'. Try --list"
# shellcheck disable=SC1090
. "${PROFILE_FILE}"

for v in DISTRO SUITE SUPPORT MIRROR COMPONENTS SECURITY_MIRROR SECURITY_SUITE \
         UPDATES_SUITE PKGS_BASE PKGS_TOOLCHAIN; do
    [ -n "${!v:-}" ] || die "profile ${PROFILE} is missing ${v}"
done

case "${SUPPORT}" in
    proven)   ;;
    untested)
        echo "NOTE: profile '${PROFILE}' is UNTESTED on this hardware."
        echo "      It should work, but do not quote numbers from it without"
        echo "      verifying. Continuing."
        ;;
    expected-broken)
        if [ "${FORCE_SUPPORT}" -ne 1 ]; then
            die "profile '${PROFILE}' is marked expected-broken:
    ${DESCRIPTION}
  Read the reasoning at the top of ${PROFILE_FILE}.
  Pass --force-support to build it anyway."
        fi
        echo "WARNING: building an expected-broken profile at your request."
        ;;
    *) die "profile ${PROFILE} has unknown SUPPORT='${SUPPORT}'" ;;
esac

# ------------------------------------------------------------------- settings
VCA_KVER="${VCA_KVER:-4.14.20-1.2.3.26.vca}"
IMG_DIR="${IMG_DIR:-/tank-red/vmdata/vca/images}"
REF_IMG="${REF_IMG:-${IMG_DIR}/vca_disk_24gb_reference_k4.14.20_ubuntu16.04_2.3.26.vcad}"
WORK="${WORK:-/tank-red/vmdata/vca/build}"
OUT="${OUT:-${IMG_DIR}/node-${PROFILE}.vcad}"
SIZE_MB="${SIZE_MB:-24576}"
ESP_MB=100
ROOT_PW="${ROOT_PW:-vista1}"
# Extra sources copied in so a fresh node can rebuild the benchmarks locally.
VCA_TOOLS_SRC="${VCA_TOOLS_SRC:-/tmp/vca_compute}"

# Resolvers for the finished node. Defaults to whatever the BUILD HOST uses,
# which is almost always right: the host is the node's default gateway and
# already masquerades 172.31.0.0/16, so anything it can resolve, they can.
# Deriving it rather than hardcoding also keeps site-specific addresses out
# of the repository. Override with NODE_DNS="10.0.0.1 10.0.0.2".
NODE_DNS="${NODE_DNS:-$(awk '/^nameserver/{print $2}' /etc/resolv.conf 2>/dev/null | tr '\n' ' ')}"
NODE_DNS_SEARCH="${NODE_DNS_SEARCH:-$(awk '/^search/{$1=""; print substr($0,2)}' /etc/resolv.conf 2>/dev/null | head -1)}"

ROOTFS="${WORK}/rootfs-${PROFILE}"
REFMNT="${WORK}/refmnt"; REFESP="${WORK}/refesp"
NEWMNT="${WORK}/newmnt"; NEWESP="${WORK}/newesp"

LOOPS=()
cleanup(){
    set +e
    umount -R "${ROOTFS}/dev" 2>/dev/null
    umount "${ROOTFS}/proc" "${ROOTFS}/sys" 2>/dev/null
    for m in "${NEWESP}" "${NEWMNT}" "${REFESP}" "${REFMNT}"; do
        mountpoint -q "$m" 2>/dev/null && umount -R "$m"
    done
    for l in "${LOOPS[@]:-}"; do [ -n "$l" ] && losetup -d "$l" 2>/dev/null; done
}

# Preconditions BEFORE the trap is armed: a failed check must not run teardown
# on state this script did not create.
[ "$(id -u)" -eq 0 ] || die "must run as root"
[ -f "${REF_IMG}" ]  || die "reference image not found: ${REF_IMG}"
[ -d "${OVERLAY_DIR}" ] || die "overlay not found at ${OVERLAY_DIR}"
command -v debootstrap >/dev/null || die "apt install debootstrap"
command -v rsync       >/dev/null || die "apt install rsync"
command -v parted      >/dev/null || die "apt install parted"

trap cleanup EXIT

say "Profile: ${PROFILE}  (${DISTRO} ${SUITE}, support=${SUPPORT})"
echo "${DESCRIPTION}"
echo "output: ${OUT}"

mkdir -p "${WORK}" "${REFMNT}" "${REFESP}" "${NEWMNT}" "${NEWESP}"

# ------------------------------------------------------------------ reference
say "Mounting Intel reference image (read-only)"
REFLOOP=$(losetup --find --show --read-only --partscan "${REF_IMG}")
LOOPS+=("${REFLOOP}"); sleep 1
mount -o ro "${REFLOOP}p2" "${REFMNT}"
mount -o ro "${REFLOOP}p1" "${REFESP}"
[ -d "${REFMNT}/lib/modules/${VCA_KVER}" ] || die "kernel ${VCA_KVER} not in reference"
[ -f "${REFESP}/vca_initramfs.img" ]      || die "vca_initramfs.img not on reference ESP"
echo "ok: kernel ${VCA_KVER} + dracut initramfs present"

# --------------------------------------------------------------------- rootfs
if [ ! -e "${ROOTFS}/.debootstrap-done" ]; then
    say "debootstrap ${DISTRO} ${SUITE} (downloads a few hundred MB)"
    rm -rf "${ROOTFS}"; mkdir -p "${ROOTFS}"
    KEYRING_ARG=""
    [ -n "${KEYRING:-}" ] && [ -f "${KEYRING}" ] && KEYRING_ARG="--keyring=${KEYRING}"
    debootstrap ${KEYRING_ARG} --arch=amd64 --variant=minbase \
        --include="${PKGS_BASE}" \
        "${SUITE}" "${ROOTFS}" "${MIRROR}" || die "debootstrap failed"
    touch "${ROOTFS}/.debootstrap-done"
else
    say "reusing existing rootfs at ${ROOTFS}"
    echo "(delete it to force a clean debootstrap)"
fi

# ------------------------------------------------------------------ configure
say "Base configuration"
# Ubuntu serves security from the same host as everything else; Debian uses a
# separate host and a different path. Both are profile fields, so this one
# block covers either.
{
    echo "deb ${MIRROR} ${SUITE} ${COMPONENTS}"
    echo "deb ${MIRROR} ${UPDATES_SUITE} ${COMPONENTS}"
    echo "deb ${SECURITY_MIRROR} ${SECURITY_SUITE} ${COMPONENTS}"
} > "${ROOTFS}/etc/apt/sources.list"

echo "vca-node" > "${ROOTFS}/etc/hostname"
printf '127.0.0.1\tlocalhost\n127.0.1.1\tvca-node\n' > "${ROOTFS}/etc/hosts"
cat > "${ROOTFS}/etc/fstab" <<'EOF'
# <device>       <dir>  <type> <options>         <dump> <fsck>
/dev/vcablk0p2   /      ext4   defaults,noatime  0      1
/dev/vcablk0p1   /boot  vfat   defaults,noatime  0      2
EOF

say "VCA kernel modules, headers and userspace from the reference image"
mkdir -p "${ROOTFS}/lib/modules" "${ROOTFS}/usr/src"
rm -rf "${ROOTFS}/lib/modules/${VCA_KVER}"
cp -a "${REFMNT}/lib/modules/${VCA_KVER}" "${ROOTFS}/lib/modules/"
# Headers let VCA/DMA modules (ring.ko, dmabench.ko, alut_gmap.ko) build here.
if [ -d "${REFMNT}/usr/src/linux-headers-${VCA_KVER}" ]; then
    rm -rf "${ROOTFS}/usr/src/linux-headers-${VCA_KVER}"
    cp -a "${REFMNT}/usr/src/linux-headers-${VCA_KVER}" "${ROOTFS}/usr/src/"
    ln -sfn "/usr/src/linux-headers-${VCA_KVER}" "${ROOTFS}/lib/modules/${VCA_KVER}/build"
    echo "  + kernel headers (on-node module builds)"
fi
for f in /usr/sbin/vca_agent.sh /usr/sbin/vca_setup.sh /usr/sbin/vca_elog.py \
         /usr/sbin/vca_eth_cfg.sh /usr/sbin/vca_uninstall.sh /sbin/vca_agent.sh \
         /etc/vca_config /etc/modprobe.d/vca.conf \
         /lib/systemd/system/vca_agent.service ; do
    [ -e "${REFMNT}${f}" ] || continue
    mkdir -p "${ROOTFS}$(dirname "$f")"
    # rm first: the rootfs is cached between runs, and cp refuses to write
    # through a dangling symlink left by a previous build. Makes rebuilds
    # idempotent instead of failing on the second run.
    rm -f "${ROOTFS}${f}"
    cp -a "${REFMNT}${f}" "${ROOTFS}${f}"; echo "  + ${f}"
done
# Without the agent the node boots but never reaches net_device_ready.
mkdir -p "${ROOTFS}/etc/systemd/system/multi-user.target.wants"
ln -sf /lib/systemd/system/vca_agent.service \
    "${ROOTFS}/etc/systemd/system/multi-user.target.wants/vca_agent.service"

say "Baking in Intel's legacy GPU stack as a fallback"
# Intel's 2018 NEO + Media SDK, kept under /opt/intel-legacy. The modern
# distro packages are preferred, but these are the proven Gen9 path and the
# only option on a distro that does not carry intel-opencl-icd.
mkdir -p "${ROOTFS}/opt/intel-legacy"
[ -f "${REFMNT}/usr/local/lib64/libigdrcl.so" ] && {
    mkdir -p "${ROOTFS}/opt/intel-legacy/neo"
    cp -a "${REFMNT}/usr/local/lib64/libigdrcl.so" "${ROOTFS}/opt/intel-legacy/neo/"
    echo "  + legacy NEO OpenCL driver"; }
[ -d "${REFMNT}/opt/intel/mediasdk" ] && {
    cp -a "${REFMNT}/opt/intel/mediasdk" "${ROOTFS}/opt/intel-legacy/"
    echo "  + Media SDK 2018 (71 MB)"; }
cat > "${ROOTFS}/opt/intel-legacy/use-legacy-opencl.sh" <<'EOF'
#!/bin/sh
# Switch OpenCL back to Intel's 2018 NEO driver (proven on Gen9 here).
mkdir -p /etc/OpenCL/vendors
echo /opt/intel-legacy/neo/libigdrcl.so > /etc/OpenCL/vendors/intel-legacy.icd
echo "legacy ICD installed; remove /etc/OpenCL/vendors/intel.icd to force it"
EOF
chmod +x "${ROOTFS}/opt/intel-legacy/use-legacy-opencl.sh"

# ------------------------------------------------------------------- overlay
# Everything VCA-specific and distro-independent lives in overlay/common, so
# it is reviewable as files rather than buried in heredocs, and so every
# profile provably gets the identical copy.
say "Applying overlay: ${OVERLAY_DIR}"
rsync -a "${OVERLAY_DIR}/" "${ROOTFS}/"
chmod 0755 "${ROOTFS}/usr/local/sbin/vca-netcfg.sh"

# Intel's udev rule calls /sbin/vca_eth_cfg.sh by absolute path, but the file
# lands in /usr/sbin. On a pre-usrmerge distro a symlink bridges that.
#
# On a MERGED distro (every modern Ubuntu/Debian) /sbin IS /usr/sbin, so this
# symlink resolves to ITSELF and replaces the real script with a self-
# referential link. The first build silently ships a broken file; the second
# fails outright with "cp: not writing through dangling symlink". Only make
# the link when the two directories are genuinely different.
SBIN_REAL="$(readlink -f "${ROOTFS}/sbin" 2>/dev/null || echo x)"
USBIN_REAL="$(readlink -f "${ROOTFS}/usr/sbin" 2>/dev/null || echo y)"
if [ "${SBIN_REAL}" != "${USBIN_REAL}" ] && [ -e "${ROOTFS}/usr/sbin/vca_eth_cfg.sh" ]; then
    ln -sf /usr/sbin/vca_eth_cfg.sh "${ROOTFS}/sbin/vca_eth_cfg.sh"
    echo "  + /sbin/vca_eth_cfg.sh -> /usr/sbin/vca_eth_cfg.sh"
else
    echo "  (usrmerge: /sbin is /usr/sbin, no compat symlink needed)"
fi
ln -sf /etc/systemd/system/vca-netcfg.service \
    "${ROOTFS}/etc/systemd/system/multi-user.target.wants/vca-netcfg.service"
find "${OVERLAY_DIR}" -type f -printf '  + /%P\n' | sort

# binutils >= 2.31 emits R_X86_64_PLT32 where 2.26 emitted R_X86_64_PC32. This
# kernel predates upstream b21ebf2fb4cd (v4.16), so its module loader rejects
# anything a modern toolchain links:
#   insmod: ERROR: could not insert module x.ko: Invalid module format
#   dmesg:  module: x: Unknown rela relocation: 4
# It compiles, vermagic matches exactly, and it still will not load.
say "Installing plt32-to-pc32 (modern binutils vs the 4.14 module loader)"
[ -f "${SCRIPT_DIR}/plt32_to_pc32.py" ] \
    || die "plt32_to_pc32.py missing from ${SCRIPT_DIR} -- modules built on the node will not load"
install -Dm755 "${SCRIPT_DIR}/plt32_to_pc32.py" "${ROOTFS}/usr/local/sbin/plt32-to-pc32"

# ------------------------------------------------------------------- packages
say "chroot: packages and services"
mount --bind /dev "${ROOTFS}/dev"; mount --bind /dev/pts "${ROOTFS}/dev/pts"
mount -t proc proc "${ROOTFS}/proc"; mount -t sysfs sys "${ROOTFS}/sys"
cp /etc/resolv.conf "${ROOTFS}/etc/resolv.conf"

chroot "${ROOTFS}" /bin/bash -eu <<CHROOT
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq

# Toolchain. A modern gcc is where most of the CPU win comes from -- it
# auto-vectorises into AVX2 FMA where gcc 5.4 does not.
#
# libelf-dev is NOT optional. Without it every out-of-tree module build dies
# before compiling a line, because this kernel has CONFIG_UNWINDER_ORC=y and
# objtool needs libelf to generate ORC metadata:
#   Makefile:961: *** "Cannot generate ORC metadata for CONFIG_UNWINDER_ORC=y,
#                      please install libelf-dev ...".  Stop.
# The stock 16.04 image happens to ship it; a minimal debootstrap does not.
apt-get install -y -qq --no-install-recommends ${PKGS_TOOLCHAIN} \
    || echo "WARN: toolchain install had failures"

# OpenCL: an ICD LOADER is what the stock image lacks. Without it nothing can
# use the NEO driver that is already on disk.
apt-get install -y -qq --no-install-recommends ${PKGS_OPENCL:-} \
    || echo "WARN: OpenCL loader install had failures"
apt-get install -y -qq --no-install-recommends ${PKGS_OPENCL_RUNTIME:-} \
    || echo "NOTE: ${PKGS_OPENCL_RUNTIME:-runtime} unavailable; use /opt/intel-legacy"

# VA-API / Quick Sync with a modern driver instead of the 2018 Media SDK.
apt-get install -y -qq --no-install-recommends ${PKGS_MEDIA:-} \
    || apt-get install -y -qq --no-install-recommends ${PKGS_MEDIA_FALLBACK:-} \
    || echo "NOTE: VA-API stack unavailable"

echo "root:${ROOT_PW}" | chpasswd
sed -i 's/^#\?PermitRootLogin.*/PermitRootLogin yes/' /etc/ssh/sshd_config
sed -i 's/^#\?PasswordAuthentication.*/PasswordAuthentication yes/' /etc/ssh/sshd_config
systemctl enable ssh 2>/dev/null || true
# Matches the grub cmdline console=ttyS0,115200n8.
systemctl enable serial-getty@ttyS0.service 2>/dev/null || true
# The VCA agent configures eth0 with plain ip(8); nothing else should race it.
systemctl disable systemd-networkd systemd-resolved 2>/dev/null || true
depmod -a ${VCA_KVER} 2>/dev/null || true
apt-get clean
rm -rf /var/lib/apt/lists/*
CHROOT

umount -R "${ROOTFS}/dev" 2>/dev/null || true
umount "${ROOTFS}/proc" "${ROOTFS}/sys" 2>/dev/null || true

# ------------------------------------------------------------------------ DNS
# Ubuntu ships /etc/resolv.conf as a symlink to
# ../run/systemd/resolve/stub-resolv.conf. We deliberately disable
# systemd-resolved above (nothing should race the VCA agent for eth0), so that
# stub file is never created and the symlink dangles -- the node ends up with
# NO name resolution at all.
#
# This is subtle because routing is fine: the host masquerades 172.31.0.0/16,
# so a node can ping 1.1.1.1 and its own LAN happily. Only names fail. The
# visible symptom is "apt does not work on a node", which is exactly the thing
# that tempts you into hand-patching nodes instead of rebuilding the image.
#
# Replace the symlink with a real file.
say "DNS"
if [ -n "${NODE_DNS// /}" ]; then
    rm -f "${ROOTFS}/etc/resolv.conf"
    {
        echo "# Written by build_node_image.sh. A real file, not a symlink:"
        echo "# systemd-resolved is disabled on VCA nodes, so its stub never appears."
        [ -n "${NODE_DNS_SEARCH}" ] && echo "search ${NODE_DNS_SEARCH}"
        for ns in ${NODE_DNS}; do echo "nameserver ${ns}"; done
    } > "${ROOTFS}/etc/resolv.conf"
    chmod 0644 "${ROOTFS}/etc/resolv.conf"
    sed 's/^/  /' "${ROOTFS}/etc/resolv.conf"
else
    echo "  WARN: NODE_DNS is empty and the build host has no nameservers."
    echo "        The node will route fine but resolve nothing. Set NODE_DNS."
fi

# ------------------------------------------------------------------- manifest
# Record exactly what this is. Without it there is no way to tell a
# built-from-source node from one somebody patched by hand at 2am -- which
# has already happened once on this card.
say "Writing /etc/vca-image-release"
# The builder often runs from a copy on the host rather than a git checkout,
# so allow the revision to be passed in. Without this it records "unknown",
# which defeats the whole point of the manifest.
if [ -n "${VCA_BUILDER_GIT:-}" ]; then
    GIT_REV="${VCA_BUILDER_GIT}"; GIT_DIRTY=""
else
    GIT_REV="$(git -C "${SCRIPT_DIR}" rev-parse --short HEAD 2>/dev/null || echo unknown)"
    GIT_DIRTY=""
    git -C "${SCRIPT_DIR}" diff --quiet 2>/dev/null || GIT_DIRTY=" (dirty)"
fi
cat > "${ROOTFS}/etc/vca-image-release" <<EOF
VCA_IMAGE_PROFILE=${PROFILE}
VCA_IMAGE_DISTRO=${DISTRO}
VCA_IMAGE_SUITE=${SUITE}
VCA_IMAGE_SUPPORT=${SUPPORT}
VCA_IMAGE_KERNEL=${VCA_KVER}
VCA_IMAGE_BUILT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
VCA_IMAGE_BUILDER_GIT=${GIT_REV}${GIT_DIRTY}
VCA_IMAGE_HOST=$(hostname)
VCA_IMAGE_REF=$(basename "${REF_IMG}")
VCA_IMAGE_DNS=${NODE_DNS}
EOF
cat "${ROOTFS}/etc/vca-image-release" | sed 's/^/  /'

# Project tooling, so a fresh node can rebuild the benchmarks locally.
if [ -d "${VCA_TOOLS_SRC}" ]; then
    say "Copying project tools into /opt/vca-tools"
    mkdir -p "${ROOTFS}/opt/vca-tools"
    rsync -a --exclude='*.ko' --exclude='*.o' --exclude='*.mod*' \
          "${VCA_TOOLS_SRC}/" "${ROOTFS}/opt/vca-tools/" 2>/dev/null || true
fi
# The verifier ships with the image so it can be run on the node with no
# host-side setup: ssh node /opt/vca-tools/verify_node_image.sh
[ -f "${SCRIPT_DIR}/verify_node_image.sh" ] && \
    install -Dm755 "${SCRIPT_DIR}/verify_node_image.sh" \
        "${ROOTFS}/opt/vca-tools/verify_node_image.sh"

# ---------------------------------------------------------------------- image
say "Creating ${SIZE_MB} MiB image at ${OUT}"
rm -f "${OUT}"; truncate -s "${SIZE_MB}M" "${OUT}"
parted -s "${OUT}" mklabel gpt
parted -s "${OUT}" mkpart primary fat32 1MiB "${ESP_MB}MiB"
parted -s "${OUT}" set 1 boot on
parted -s "${OUT}" set 1 esp on
parted -s "${OUT}" mkpart primary ext4 "${ESP_MB}MiB" 100%
NEWLOOP=$(losetup --find --show --partscan "${OUT}")
LOOPS+=("${NEWLOOP}"); sleep 1
mkfs.vfat -F32 -n VCAESP "${NEWLOOP}p1" >/dev/null
mkfs.ext4 -q -L vcaroot "${NEWLOOP}p2"
mount "${NEWLOOP}p2" "${NEWMNT}"; mount "${NEWLOOP}p1" "${NEWESP}"

say "Copying rootfs into the image"
rm -f "${ROOTFS}/.debootstrap-done"
rsync -aHAX --numeric-ids \
      --exclude='/proc/*' --exclude='/sys/*' --exclude='/dev/*' \
      --exclude='/tmp/*' --exclude='/run/*' --exclude='/boot/*' \
      "${ROOTFS}/" "${NEWMNT}/"
touch "${ROOTFS}/.debootstrap-done"
mkdir -p "${NEWMNT}"/{proc,sys,dev,tmp,run,boot}; chmod 1777 "${NEWMNT}/tmp"

say "Copying Intel's ESP verbatim (kernel + dracut initramfs + grub)"
cp -a "${REFESP}/." "${NEWESP}/"
ls "${NEWESP}"

# --------------------------------------------------------------- self-checks
# Cheap assertions against the finished filesystem. Every one of these has
# been a real failure at some point.
say "Self-check"
fail=0
# -e FOLLOWS symlinks. The systemd .wants entries are symlinks to ABSOLUTE
# paths (/etc/systemd/system/..., /lib/systemd/system/...), which resolve
# against the BUILD HOST's filesystem, not the mounted image -- so a perfectly
# correct symlink fails -e and the self-check reports a phantom failure.
# Accept a dangling symlink here; `ls -la` is the way to read these, not -e.
chk(){
    if [ -e "${NEWMNT}$1" ] || [ -L "${NEWMNT}$1" ]; then
        echo "  ok   $1"
    else
        echo "  MISS $1"; fail=1
    fi
}
chk /sbin/init
chk /etc/vca-image-release
chk /usr/local/sbin/vca-netcfg.sh
chk /usr/local/sbin/plt32-to-pc32
chk /etc/systemd/system/multi-user.target.wants/vca-netcfg.service
chk /etc/systemd/system/multi-user.target.wants/vca_agent.service
chk "/lib/modules/${VCA_KVER}/build"
chk /usr/include/libelf.h
chk /etc/fstab
# Specifically a REGULAR FILE: a dangling symlink to systemd-resolved's stub
# passes an -e test on the build host and gives the node no DNS at all.
if [ -f "${NEWMNT}/etc/resolv.conf" ] && [ ! -L "${NEWMNT}/etc/resolv.conf" ] \
   && grep -q '^nameserver' "${NEWMNT}/etc/resolv.conf"; then
    echo "  ok   /etc/resolv.conf ($(grep -c '^nameserver' "${NEWMNT}/etc/resolv.conf") nameserver(s), real file)"
else
    echo "  MISS /etc/resolv.conf is not a real file with nameservers"; fail=1
fi
[ -e "${NEWESP}/vca_initramfs.img" ] && echo "  ok   ESP/vca_initramfs.img" \
    || { echo "  MISS ESP/vca_initramfs.img"; fail=1; }
sync
[ "$fail" -eq 0 ] || die "self-check failed -- do not boot this image"

say "Done"
echo "image:   ${OUT}"
echo "profile: ${PROFILE} (${DISTRO} ${SUITE})"
echo "size:    $(du -h --apparent-size "${OUT}" | cut -f1) apparent / $(du -h "${OUT}" | cut -f1) on disk"
cat <<EOS

Deploy to one node (N = 0, 1 or 2). The node MUST be powered off first --
see docs/GOTCHAS.md, getting this sequence wrong has wedged the host.

  vcactl pwrbtn-short 0 N      # wait for a SETTLED power_off, then:
  vcactl blockio close 0 N vcablk0
  vcactl blockio open  0 N vcablk0 RW ${OUT}
  vcactl pwrbtn-short 0 N
  vcactl wait-BIOS 0 N && vcactl boot 0 N vcablk0
  vcactl status

Verify it afterwards:
  ssh root@172.31.\$((N+1)).1 /opt/vca-tools/verify_node_image.sh

Roll back (the stock images are never modified):
  ...same sequence, pointed at ${IMG_DIR}/nodeN.vcad
EOS
