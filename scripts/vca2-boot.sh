#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
set -euo pipefail
# Kernels this card is known to work on. 7.0.14-17-pve was verified
# 2026-09-21 (NICs, jumbo and the DMA fabric all fine) -- the earlier claim
# that it broke virtio NICs was a measurement made before the nodes booted.
KNOWN_GOOD_KERNELS="7.0.2-6-pve 7.0.14-17-pve"
IMAGE_BASE="/tank-red/vmdata/vca/images"
# The live images. These are per-node reflink copies of a golden master built
# by compute/image/build_node_image.sh -- see docs/IMAGE_BUILDER.md.
# The stock Intel 16.04 images (node{0,1,2}.vcad) are never modified and
# remain the rollback: set VCA_IMAGE_SUFFIX="" to boot them instead.
#
# Getting this wrong is silent: the card comes up healthy on the WRONG OS and
# nothing complains. It pointed at the 16.04 images for a while after all
# three nodes had been moved to 22.04.
IMAGE_SUFFIX="${VCA_IMAGE_SUFFIX-"-ubuntu-jammy"}"
#
# Nodes are numbered globally: card C, cpu N is node C*3+N at 172.31.(C*3+N+1).1
# with image node<C*3+N>. Card 0 is required. Card 1 (second VCA 2, added
# 2026-09-26) is brought up only when it is present AND its three images
# exist -- a missing or image-less second card must never fail card 0.
IMAGES=()
for _n in 0 1 2 3 4 5; do IMAGES+=("${IMAGE_BASE}/node${_n}${IMAGE_SUFFIX}.vcad"); done
for _n in 0 1 2; do
  [ -f "${IMAGES[$_n]}" ] || { echo "ERROR: image not found: ${IMAGES[$_n]}" >&2; exit 1; }
done

log() { printf '[vca-boot] %s\n' "$*"; }

running_kernel="$(uname -r)"
log "kernel=$running_kernel"
case " $KNOWN_GOOD_KERNELS " in
  *" $running_kernel "*) ;;
  *) echo "ERROR: kernel is $running_kernel, known good: $KNOWN_GOOD_KERNELS" >&2
     echo "       override with VCA_ALLOW_ANY_KERNEL=1 if you are testing one" >&2
     [ "${VCA_ALLOW_ANY_KERNEL:-0}" = "1" ] || exit 1 ;;
esac

if ! lsmod | grep -q '^vop_bus'; then
  log "loading modules"
  /usr/sbin/vca2-load-modules
  sleep 2
fi

lsmod | grep -Ei 'plx|vop|^vca' | awk '{print $1,$3}'
log "status before reset"
vcactl status || true

# Idempotent per card: if a card's three nodes are already up, do NOT reset
# them. This runs as vca2-card.service at host boot, where the nodes are
# always down -- but starting the service on a live card (to arm its
# clean-shutdown ExecStop) must not yank running nodes out from under their
# RW disk images.
#
# Every vcactl call is scoped to its card. Unscoped `vcactl wait` once waited
# on an image-less second card, timed out and failed the unit -- and a failed
# unit never runs its clean-shutdown ExecStop.
# Detected here, after the modules are loaded -- earlier, vcactl sees no cards.
CARDS=(0)
if vcactl status 2>/dev/null | grep -q '^Card: 1 '    && [ -f "${IMAGES[3]}" ] && [ -f "${IMAGES[4]}" ] && [ -f "${IMAGES[5]}" ]; then
  CARDS+=(1)
fi
log "cards: ${CARDS[*]}"
st() { vcactl status "$1" "$2" 2>/dev/null | sed 's/.*STATE: //'; }
is_ready() { [ "$(st "$1" "$2")" = net_device_ready ]; }

# Turn a node on from whatever off-ish state it is in. `vcactl reset` cannot
# wake a node that is fully powered off (after a clean ExecStop, e.g.
# `systemctl restart vca2-card`) -- it sits in link_down and wait-BIOS times
# out. link_down / powering_down are on the way DOWN and a press then is eaten,
# so wait for a settled power_off before pressing.
power_on() { # card cpu
  local card=$1 cpu=$2 s
  for _ in $(seq 1 12); do
    s="$(st "$card" "$cpu")"
    case "$s" in link_down*|powering_down*) sleep 5 ;; *) break ;; esac
  done
  # A node that was reset while powered off parks in link_down for good
  # (seen 2026-09-26). Only a long press gets it to a real power_off.
  if [[ "$s" == link_down* || "$s" == powering_down* ]]; then
    log "card $card cpu $cpu stuck in $s -- long press to power it off"
    vcactl pwrbtn-long "$card" "$cpu" >/dev/null 2>&1 || true
    for _ in $(seq 1 12); do
      s="$(st "$card" "$cpu")"
      [[ "$s" == power_off* ]] && break
      sleep 5
    done
    sleep 5
  fi
  if [[ "$s" == power_off* ]]; then
    log "card $card cpu $cpu is power_off -- pressing power"
    vcactl pwrbtn-short "$card" "$cpu" >/dev/null 2>&1 || true
    for _ in $(seq 1 30); do
      case "$(st "$card" "$cpu")" in bios_up*|bios_ready*) break ;; esac
      sleep 5
    done
  fi
}

attach_and_boot() { # card cpu
  local card=$1 cpu=$2 img="${IMAGES[$(( $1 * 3 + $2 ))]}"
  if vcactl blockio list "$card" "$cpu" 2>/dev/null | grep -qE '^\s*'"$card"'\s+'"$cpu"'\s+vcablk0\s+active'; then
    log "card $card cpu $cpu vcablk0 already active"
  else
    log "open $img on card $card cpu $cpu"
    vcactl blockio open "$card" "$cpu" vcablk0 RW "$img" || true
  fi
  log "boot card $card cpu $cpu"
  vcactl boot "$card" "$cpu" vcablk0 || true
}

# A node can hang in `booting` for good (`Can not open Dev_page`; seen on a
# first boot and at the 2026-09-29 cold boot). The fix that worked both times:
# blockio close, long press to power_off, press to bios_up, attach, boot.
retry_stuck() { # card cpu
  local card=$1 cpu=$2
  log "card $card cpu $cpu did not come up ($(st "$card" "$cpu")) -- power-cycling it once"
  vcactl blockio close "$card" "$cpu" vcablk0 >/dev/null 2>&1 || true
  # (if the close did not take, attach_and_boot finds vcablk0 still there and
  # its open fails harmlessly -- "already exist" -- before the boot.)
  vcactl pwrbtn-long "$card" "$cpu" >/dev/null 2>&1 || true
  for _ in $(seq 1 24); do [[ "$(st "$card" "$cpu")" == power_off* ]] && break; sleep 5; done
  sleep 5
  power_on "$card" "$cpu"
  case "$(st "$card" "$cpu")" in
    bios_up*|bios_ready*) attach_and_boot "$card" "$cpu"
                          timeout 300 vcactl wait "$card" "$cpu" || true ;;
  esac
}

# Idempotent per NODE, and one dead node must not strand its siblings
# (2026-09-29: card 1 cpu 0 never reaches BIOS; the old card-wide wait-BIOS
# under set -e exited there, leaving cpus 1-2 unbooted, jumbo_mtu unloaded,
# NAT unset and the unit failed -- so its clean-shutdown ExecStop never ran).
#
# - A card with no node up gets the card-wide reset, as always.
# - A card with some nodes up is never reset (that would reboot the live ones
#   under their RW images); only its missing nodes are reset, one by one.
# - A node that never reaches BIOS is logged and skipped.
DEAD=()
for card in "${CARDS[@]}"; do
  ready="$(vcactl status "$card" 2>/dev/null | grep -c 'STATE: net_device_ready' || true)"
  if [ "$ready" = "3" ]; then
    log "card $card: all three nodes already net_device_ready -- skipping"
    continue
  fi
  todo=()
  for cpu in 0 1 2; do is_ready "$card" "$cpu" || todo+=("$cpu"); done
  log "card $card: $ready of 3 up; bringing up cpu ${todo[*]}"
  for cpu in "${todo[@]}"; do power_on "$card" "$cpu"; done
  if [ "$ready" = "0" ]; then
    log "card $card: reset"
    vcactl reset "$card" || true
  else
    for cpu in "${todo[@]}"; do
      log "card $card cpu $cpu: reset (card left alone, it has live nodes)"
      vcactl reset "$card" "$cpu" || true
    done
  fi
  booted=()
  for cpu in "${todo[@]}"; do
    if vcactl wait-BIOS "$card" "$cpu"; then
      booted+=("$cpu")
    else
      log "WARN: card $card cpu $cpu never reached BIOS ($(st "$card" "$cpu")) -- skipping it"
      DEAD+=("$card/$cpu")
    fi
  done
  for cpu in "${booted[@]}"; do attach_and_boot "$card" "$cpu"; done
  for cpu in "${booted[@]}"; do
    # `vcactl wait` gives up after ~200 s. A node past `booting` (net_device_up,
    # dhcp_*, os_ready) is running and just slow to be called ready -- on
    # 2026-09-29 both card-1 nodes took 4.7 min, and power-cycling one there was
    # wrong. Only the real hang -- still `booting` -- gets the retry.
    if ! timeout 300 vcactl wait "$card" "$cpu"; then
      for _ in $(seq 1 84); do
        case "$(st "$card" "$cpu")" in net_device_ready*|booting*) break ;; esac
        sleep 5
      done
      [[ "$(st "$card" "$cpu")" == booting* ]] && retry_stuck "$card" "$cpu"
    fi
    is_ready "$card" "$cpu" || { log "WARN: card $card cpu $cpu still $(st "$card" "$cpu")"; DEAD+=("$card/$cpu"); }
  done
done
vcactl status

NODE_IPS=()
for card in "${CARDS[@]}"; do
  for cpu in 0 1 2; do
    is_ready "$card" "$cpu" || continue
    /usr/sbin/vca_eth_ifup.sh "$card" "$cpu" || true
    NODE_IPS+=("172.31.$((card * 3 + cpu + 1)).1")
  done
done
[ "${#NODE_IPS[@]}" -gt 0 ] || { echo "ERROR: no VCA node came up" >&2; exit 1; }
# Host-side NICs: one ethN per live node (eth0-2 card 0, then card 1). A dead
# node has none, and insmod fails with ENODEV if ifnames names a missing NIC.
JUMBO_IFS="$(ls /sys/class/net | grep -E '^eth[0-9]+$' | sort -V | paste -sd, -)"

# Host VCA NICs come back at MTU 1500 every boot: PVE defaults max_mtu to
# 1500 and vca_virtio_net never sets it. Without this the card looks totally
# healthy and node<->node quietly runs at ~2.7 Gbit/s instead of ~16-26.
# Nothing warns you. See docs/FABRIC.md.
JUMBO_KO="${JUMBO_KO:-/root/vca2/jumbo_mtu.ko}"
if [ -f "$JUMBO_KO" ]; then
  # Loaded but not covering every live NIC (a node came back, or card 1 was
  # brought up by hand): reload with the current list.
  if lsmod | grep -q '^jumbo_mtu'; then
    short=""
    for i in ${JUMBO_IFS//,/ }; do
      [ "$(cat /sys/class/net/$i/mtu 2>/dev/null)" = 65521 ] || short="$short $i"
    done
    if [ -n "$short" ]; then
      log "jumbo_mtu loaded but${short} not at MTU 65521 -- reloading"
      rmmod jumbo_mtu || log "WARN: rmmod jumbo_mtu failed"
    fi
  fi
  if lsmod | grep -q '^jumbo_mtu'; then
    log "jumbo_mtu already loaded"
  else
    # jumbo_mtu.ko is built per-kernel. After a kernel change the old .ko is
    # rejected on vermagic and, without this, the host silently stays at MTU
    # 1500 -- node<->node quietly drops from ~16-26 Gbit/s to ~2.7 and
    # nothing complains. Rebuild rather than warn.
    ko_ver="$(modinfo -F vermagic "$JUMBO_KO" 2>/dev/null | awk '{print $1}')"
    if [ "$ko_ver" != "$running_kernel" ]; then
      log "jumbo_mtu built for ${ko_ver:-unknown}, running $running_kernel -- rebuilding"
      if ( cd "$(dirname "$JUMBO_KO")" && make -s clean >/dev/null 2>&1; make -s >/dev/null 2>&1 ); then
        log "  rebuilt"
      else
        log "  WARN: rebuild failed; host NICs will stay at MTU 1500"
      fi
    fi
    log "loading jumbo_mtu (MTU 65521 on $JUMBO_IFS)"
    insmod "$JUMBO_KO" ifnames="$JUMBO_IFS" mtu=65521 || log "WARN: jumbo_mtu insmod failed"
    sleep 2
  fi
  for i in ${JUMBO_IFS//,/ }; do
    printf '[vca-boot] %s mtu=%s\n' "$i" "$(cat /sys/class/net/$i/mtu 2>/dev/null)"
  done
else
  log "WARN: $JUMBO_KO not found -- host NICs stay at MTU 1500"
fi

log "ping"
for ip in "${NODE_IPS[@]}"; do
  ping -c 2 -W 2 "$ip" || true
done

log "jumbo end-to-end check (DF ping 20000)"
for ip in "${NODE_IPS[@]}"; do
  if ping -c 2 -M do -s 20000 -W 3 "$ip" >/dev/null 2>&1; then
    log "  $ip jumbo OK"
  else
    log "  WARN: $ip does NOT pass 20000-byte DF frames"
  fi
done

log "temps"
# Informational only. vcactl temp goes over the host<->node agent channel,
# which has been seen failing with LBP_IRQ_TIMEOUT while the nodes are
# otherwise perfectly healthy -- that must not fail the whole bring-up.
for card in "${CARDS[@]}"; do vcactl temp "$card" || log "WARN: vcactl temp failed (agent channel); node coretemp is still readable over ssh"; done

if [ "${#DEAD[@]}" -gt 0 ]; then
  log "WARN: nodes not up (card/cpu): ${DEAD[*]} -- the rest are running"
fi
exit 0
