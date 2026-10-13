#!/bin/bash
# SPDX-License-Identifier: MIT
#
# VCA 2 additive-capacity benchmark.
#
# THE QUESTION THIS ANSWERS:
#   "Does adding this card to the machine get more total work done per watt,
#    or should it come out of the slot?"
#
# It is deliberately NOT a head-to-head. The host in this machine has an RTX
# 3070 and 18 Xeon cores; the card will lose most direct comparisons and that
# is not interesting. What IS interesting is the MARGIN: with the host already
# busy, what does the card add, and what does it cost in watts?
#
# So every workload runs in three configurations:
#
#   host   host working alone
#   card   the three card nodes working alone
#   both   both at once -- the number that actually matters
#
# and the verdict is computed from the margin:
#
#   marginal throughput = both - host
#   marginal watts      = both_W - host_W
#
# A card that adds 60% more throughput for 40% more watts is worth keeping.
# One that adds 10% for 40% is not. Read lib/power.sh before quoting any
# watt figure -- both sides are understated and neither is wall power.
#
#   ./vca-bench.sh --workload transcode --config all
#   ./vca-bench.sh --workload all --reps 3
#   ./vca-bench.sh --list
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "${SCRIPT_DIR}/lib/power.sh"

WORKLOAD="all"
CONFIG="all"
REPS=1
OUTDIR="${SCRIPT_DIR}/results"
NODE_PW="${NODE_PW:-vista1}"

usage() { sed -n '3,30p' "${BASH_SOURCE[0]}" | sed 's/^# \?//'; exit "${1:-0}"; }

while [ $# -gt 0 ]; do
    case "$1" in
        --workload|-w) WORKLOAD="$2"; shift 2 ;;
        --config|-c)   CONFIG="$2"; shift 2 ;;
        --reps|-r)     REPS="$2"; shift 2 ;;
        --out|-o)      OUTDIR="$2"; shift 2 ;;
        --list|-l)     ls "${SCRIPT_DIR}/workloads/" | sed 's/\.sh$//' | sed 's/^/  /'; exit 0 ;;
        --help|-h)     usage 0 ;;
        *) echo "unknown argument: $1" >&2; usage 1 ;;
    esac
done

say()  { printf '\n=== %s ===\n' "$*"; }
die()  { echo "ERROR: $*" >&2; exit 1; }
node() { sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=20 "172.31.$1.1" "$2"; }

[ "$(id -u)" -eq 0 ] || die "must run as root (RAPL + vcactl)"
command -v vcactl  >/dev/null || die "vcactl not found -- run this on the host"
command -v sshpass >/dev/null || die "apt install sshpass"
command -v bc      >/dev/null || die "apt install bc"

# A benchmark against a half-up card is worse than no benchmark.
ready=$(vcactl status 2>/dev/null | grep -c net_device_ready)
[ "$ready" = "3" ] || die "only $ready/3 nodes are net_device_ready -- bring the card up first"

mkdir -p "$OUTDIR"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
JSON="${OUTDIR}/bench-${STAMP}.json"

WORKLOADS=()
if [ "$WORKLOAD" = "all" ]; then
    for f in "${SCRIPT_DIR}"/workloads/*.sh; do WORKLOADS+=("$(basename "$f" .sh)"); done
else
    WORKLOADS=("$WORKLOAD")
fi

CONFIGS=()
if [ "$CONFIG" = "all" ]; then CONFIGS=(host card both); else CONFIGS=("$CONFIG"); fi

say "VCA2 additive-capacity benchmark"
echo "  host    : $(hostname), $(nproc) threads, $(lscpu | awk -F: '/^Model name/{gsub(/^ +/,"",$2); print $2; exit}')"
echo "  nodes   : 3 x $(node 1 'lscpu | awk -F: "/^Model name/{gsub(/^ +/,\"\",\$2); print \$2; exit}"' 2>/dev/null)"
echo "  workloads: ${WORKLOADS[*]}"
echo "  configs  : ${CONFIGS[*]}   reps: ${REPS}"
echo "  results  : ${JSON}"

printf '{\n  "stamp": "%s",\n  "host": "%s",\n  "runs": [\n' "$STAMP" "$(hostname)" > "$JSON"
first=1

for w in "${WORKLOADS[@]}"; do
    WL="${SCRIPT_DIR}/workloads/${w}.sh"
    [ -f "$WL" ] || { echo "  skip: no such workload '$w'"; continue; }
    # shellcheck disable=SC1090
    . "$WL"
    say "workload: ${w}  --  ${WL_DESCRIPTION:-no description}"
    type wl_prepare >/dev/null 2>&1 && { echo "  preparing..."; wl_prepare || { echo "  PREPARE FAILED, skipping"; continue; }; }

    for cfg in "${CONFIGS[@]}"; do
        for rep in $(seq 1 "$REPS"); do
            printf '  %-5s rep%-2s ' "$cfg" "$rep"
            power_start
            RESULT="$(wl_run "$cfg" 2>/dev/null)"
            power_stop
            # wl_run prints a single number: the throughput in WL_UNIT
            THR="${RESULT:-0}"
            PW=$(echo "scale=2; if ($POWER_W_TOTAL > 0) $THR / $POWER_W_TOTAL else 0" | bc 2>/dev/null)
            printf '%10s %-12s  %6s W  (host %s + card %s)  %s/W\n' \
                "$THR" "${WL_UNIT:-units}" "$POWER_W_TOTAL" "$POWER_W_HOST" "$POWER_W_CARD" "$PW"
            [ "$first" = "1" ] || printf ',\n' >> "$JSON"
            first=0
            printf '    {"workload":"%s","config":"%s","rep":%s,"throughput":%s,"unit":"%s","secs":%s,"w_host":%s,"w_card":%s,"w_total":%s,"j_host":%s,"j_card":%s,"j_total":%s}' \
                "$w" "$cfg" "$rep" "${THR:-0}" "${WL_UNIT:-units}" "${POWER_SECS:-0}" \
                "${POWER_W_HOST:-0}" "${POWER_W_CARD:-0}" "${POWER_W_TOTAL:-0}" \
                "${POWER_J_HOST:-0}" "${POWER_J_CARD:-0}" "${POWER_J_TOTAL:-0}" >> "$JSON"
        done
    done
    type wl_cleanup >/dev/null 2>&1 && wl_cleanup
done

printf '\n  ]\n}\n' >> "$JSON"

say "Verdict"
"${SCRIPT_DIR}/verdict.sh" "$JSON"
echo
echo "raw: $JSON"
