# SPDX-License-Identifier: MIT
# Sourced by the run_*.sh drivers. Checks that the environment actually looks
# like the one these experiments were written for, and fails with something
# readable instead of a confusing insmod error thirty lines later.
#
#   source "$(dirname "$0")/preflight.sh"
#   preflight "host:hostbuf.ko" "node:1:alut_gmap.ko,ring.ko" "node:2:ring.ko"
#
# NODE_PW may be set in the environment; it defaults to Intel's documented
# card-node password. Host credentials are never stored here.

: "${NODE_PW:=vista1}"
: "${CD:=/tmp/vca_compute/dma}"

_pf_die() { echo "preflight: $*" >&2; exit 1; }

_pf_ssh() {
    sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no \
        -o ConnectTimeout=10 -o BatchMode=no "root@172.31.$1.1" "$2"
}

preflight() {
    local spec kind idx mods m

    command -v sshpass >/dev/null 2>&1 || _pf_die "sshpass not installed (apt install sshpass)"
    command -v vcactl  >/dev/null 2>&1 || _pf_die "vcactl not found -- run these on the Proxmox HOST, not a node"

    # The card must be up, or every later failure is a red herring.
    local st
    st=$(vcactl status 2>/dev/null | grep -c net_device_ready)
    [ "$st" -ge 1 ] || _pf_die "no node is net_device_ready. Boot the card first (scripts/vca2-boot.sh, or systemctl start vca2-card)"

    # The DMA measurements were taken on 7.0.2-6-pve and re-run on
    # 7.0.14-17-pve, the pinned kernel since 2026-09-21 (docs/KERNEL_PORT.md).
    # The old "NICs never appear on 7.0.14" claim was retracted (GOTCHAS).
    case "$(uname -r)" in
        7.0.2-6-pve|7.0.14-17-pve) : ;;
        *) echo "preflight: WARNING kernel is $(uname -r), measurements were taken on 7.0.2-6-pve and 7.0.14-17-pve" >&2 ;;
    esac

    for spec in "$@"; do
        kind=${spec%%:*}
        case "$kind" in
        host)
            mods=${spec#host:}
            for m in ${mods//,/ }; do
                [ -f "$CD/$m" ] || _pf_die "missing $CD/$m -- build it: cd compute/dma && cp Makefile.host Makefile && make"
            done
            ;;
        node)
            spec=${spec#node:}
            idx=${spec%%:*}
            mods=${spec#*:}
            _pf_ssh "$idx" true >/dev/null 2>&1 \
                || _pf_die "node $((idx-1)) (172.31.$idx.1) not reachable over SSH"
            for m in ${mods//,/ }; do
                _pf_ssh "$idx" "test -f $CD/$m" \
                    || _pf_die "node $((idx-1)) missing $CD/$m -- build it there: cd $CD && cp Makefile.guest Makefile && make"
            done
            ;;
        *)
            _pf_die "bad preflight spec '$spec'"
            ;;
        esac
    done

    # Nothing from a previous aborted run should still be loaded; a stale
    # A-LUT window pointing at a freed host buffer is the dangerous case.
    local stale
    stale=$(lsmod | awk 'NR>1{print $1}' | grep -xE 'hostbuf|dmabench|dmabench2|alut_hmap|p2pmap' | tr '\n' ' ')
    [ -z "$stale" ] || _pf_die "stale host modules loaded: $stale -- rmmod them first (see SAFETY.md on unload order)"

    echo "preflight: ok"
}
