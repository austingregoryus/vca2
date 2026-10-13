# SPDX-License-Identifier: MIT
# RAPL energy sampling for the host and the three card nodes.
#
# Sourced by vca-bench.sh. Everything here is a READ of
# /sys/class/powercap/intel-rapl:*/energy_uj -- nothing writes a power cap.
#
# HONEST LIMITS, because a perf/watt number is only as good as its denominator:
#
#   - The host exposes only `package-0`. No DRAM, no platform domain. Real
#     host wall draw is meaningfully higher than what we measure, so the
#     host's perf/watt here is FLATTERED.
#   - The card is the sum of three node packages. That excludes the two PLX
#     switches, the DDR4, and VRM losses. THERMAL.md measured ~134 W at the
#     card under all-GPU load against a node-package sum well below that, so
#     the card's perf/watt is flattered too.
#   - Both sides are understated, which keeps the comparison roughly fair,
#     but NEITHER is a wall-socket number. Treat the ratio as indicative and
#     do not quote the absolute watts as system power.
#
# RAPL counters are unsigned and wrap. Wrap is handled via max_energy_range_uj.

: "${NODE_PW:=vista1}"

_rapl_read_host() { cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null || echo 0; }
_rapl_max_host()  { cat /sys/class/powercap/intel-rapl:0/max_energy_range_uj 2>/dev/null || echo 0; }

_rapl_read_node() {
    sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        "172.31.$1.1" 'cat /sys/class/powercap/intel-rapl:0/energy_uj 2>/dev/null || echo 0' 2>/dev/null
}
_rapl_max_node() {
    sshpass -p "$NODE_PW" ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 \
        "172.31.$1.1" 'cat /sys/class/powercap/intel-rapl:0/max_energy_range_uj 2>/dev/null || echo 0' 2>/dev/null
}

# Snapshot every counter we care about. Sets POWER_T0 and POWER_E0_*.
power_start() {
    POWER_T0=$(date +%s.%N)
    POWER_E0_HOST=$(_rapl_read_host)
    POWER_MAX_HOST=$(_rapl_max_host)
    local i
    for i in 1 2 3; do
        eval "POWER_E0_N$i=\$(_rapl_read_node $i)"
        eval "POWER_MAX_N$i=\$(_rapl_max_node $i)"
    done
}

# uJ delta with wraparound handling.
_delta() {
    local now="$1" then="$2" max="$3"
    if [ "${now:-0}" -ge "${then:-0}" ] 2>/dev/null; then
        echo $(( now - then ))
    else
        # counter wrapped
        echo $(( max - then + now ))
    fi
}

# Sets POWER_SECS, POWER_J_* (joules -- the honest currency) and POWER_W_*.
#
# WATTS ARE A TRAP HERE. An average-watt figure is energy/duration, so if two
# configurations run for different wall times the comparison is meaningless:
# the longer run dilutes its own average with whatever was idle. The first
# version of this benchmark did exactly that and reported the card adding 50%
# throughput for 1 W, i.e. "57x more efficient than the host", which was pure
# measurement artifact. ENERGY (joules) is additive and duration-independent;
# prefer POWER_J_* and work-per-joule. Workloads should also run for a FIXED
# wall time so the windows match.
power_stop() {
    local t1 e1 d
    t1=$(date +%s.%N)
    POWER_SECS=$(echo "$t1 - $POWER_T0" | bc)

    e1=$(_rapl_read_host)
    d=$(_delta "$e1" "$POWER_E0_HOST" "$POWER_MAX_HOST")
    POWER_J_HOST=$(echo "scale=1; $d / 1000000" | bc)
    POWER_W_HOST=$(echo "scale=1; $POWER_J_HOST / $POWER_SECS" | bc)

    local total=0 i
    for i in 1 2 3; do
        e1=$(_rapl_read_node $i)
        eval "local e0=\$POWER_E0_N$i; local mx=\$POWER_MAX_N$i"
        d=$(_delta "$e1" "$e0" "$mx")
        total=$(( total + d ))
    done
    POWER_J_CARD=$(echo "scale=1; $total / 1000000" | bc)
    POWER_W_CARD=$(echo "scale=1; $POWER_J_CARD / $POWER_SECS" | bc)
    POWER_J_TOTAL=$(echo "scale=1; $POWER_J_HOST + $POWER_J_CARD" | bc)
    POWER_W_TOTAL=$(echo "scale=1; $POWER_W_HOST + $POWER_W_CARD" | bc)
}
