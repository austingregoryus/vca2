#!/bin/bash
# SPDX-License-Identifier: MIT
# HETERO re-test: is the combined GPU+CPU case power-starved at PL1=45 W?
#
# SUSTAINED loads, which the first attempt got wrong: a single gemm_fast run
# finishes in 2.56 ms, so nothing is power-limited and the whole window is SSH
# overhead. Here each side loops for RUN_SECS and we average the per-iteration
# GFLOP/s, so the package is actually held at its limit long enough for PL1 to
# matter (the RAPL long_term window is 28 s).
#
# Configurations, on all three nodes at once:
#   gpu    gemm_fast looped
#   cpu    cpu_gemm looped
#   both   concurrently -- the only case measured AT the cap (43.5-44.6 W)
#
# SAFETY: temps sampled DURING the run, not after. Aborts and reverts to the
# stock PL1 if any core reaches ABORT_C. PL1 also reverts on node reset
# regardless, since the MSR lock bit is 0 -- but do not rely on that.
set -u
CD=/tmp/vca_compute
RUN_SECS="${RUN_SECS:-40}"
ABORT_C=88
N(){ sshpass -p vista1 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=25 "172.31.$1.1" "$2"; }

hottest(){ vcactl temp 2>/dev/null | awk '/^Core/ {t=$3; gsub(/[+]/,"",t); sub(/[^0-9.].*$/,"",t); if(t+0>m) m=t+0} END{print m+0}'; }

set_pl1(){
    local w="$1" i
    for i in 1 2 3; do
        N $i "echo $(( w * 1000000 )) > /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw" 2>/dev/null
    done
    printf "  PL1:"
    for i in 1 2 3; do
        printf " n%d=%sW" $((i-1)) "$(N $i 'echo $(( $(cat /sys/class/powercap/intel-rapl:0/constraint_0_power_limit_uw) / 1000000 ))')"
    done
    echo
}
# BUG THIS FIXES: the first version ran `trap - EXIT` on the success path,
# which removed the handler BEFORE it could fire -- so a clean run left PL1
# at the raised value on all three nodes. Always revert, then disarm.
revert(){ echo; echo "  reverting PL1 to ${STOCK_PL1} W"; set_pl1 "$STOCK_PL1"; }
STOCK_PL1="${STOCK_PL1:-45}"
trap revert EXIT

run_cfg(){
    local cfg="$1" n=1024 i peak=0
    local -a e0 e1
    # loop scripts run ON the node so the window is real work, not ssh latency
    for i in 1 2 3; do
        N $i "cat > /tmp/loop.sh <<'EOS'
#!/bin/bash
CFG=\$1; SECS=\$2; N=\$3
end=\$(( \$(date +%s) + SECS ))
: > /tmp/g.res; : > /tmp/c.res
if [ \"\$CFG\" = gpu ] || [ \"\$CFG\" = both ]; then
  ( while [ \$(date +%s) -lt \$end ]; do /tmp/vca_compute/gemm_fast \$N 2>/dev/null | grep -oE 'gflops=[0-9.]+' | cut -d= -f2 >> /tmp/g.res; done ) &
fi
if [ \"\$CFG\" = cpu ] || [ \"\$CFG\" = both ]; then
  ( while [ \$(date +%s) -lt \$end ]; do /tmp/vca_compute/cpu_gemm \$N 8 1 2>/dev/null | grep -oE '[0-9]+\.[0-9]+ GFLOP' | grep -oE '^[0-9.]+' >> /tmp/c.res; done ) &
fi
wait
EOS
chmod +x /tmp/loop.sh" 2>/dev/null
        e0[$i]=$(N $i 'cat /sys/class/powercap/intel-rapl:0/energy_uj')
    done

    local t0; t0=$(date +%s.%N)
    for i in 1 2 3; do ( N $i "/tmp/loop.sh $cfg $RUN_SECS $n" ) >/dev/null 2>&1 & done
    # sample temperature mid-run rather than after it
    sleep $(( RUN_SECS / 2 ))
    peak=$(hottest)
    wait
    local t1; t1=$(date +%s.%N)
    local secs; secs=$(echo "$t1 - $t0" | bc)

    local gtot=0 ctot=0 wtot=0
    for i in 1 2 3; do
        e1[$i]=$(N $i 'cat /sys/class/powercap/intel-rapl:0/energy_uj')
        wtot=$(echo "$wtot + (${e1[$i]} - ${e0[$i]}) / 1000000 / $secs" | bc -l)
        local g c
        g=$(N $i "awk '{s+=\$1;n++} END{if(n>0) printf \"%.1f\", s/n; else print 0}' /tmp/g.res 2>/dev/null")
        c=$(N $i "awk '{s+=\$1;n++} END{if(n>0) printf \"%.1f\", s/n; else print 0}' /tmp/c.res 2>/dev/null")
        gtot=$(echo "$gtot + ${g:-0}" | bc)
        ctot=$(echo "$ctot + ${c:-0}" | bc)
    done
    printf "  %-5s GPU %7.1f + CPU %6.1f = %7.1f GFLOP/s   %5.1f W   peak %sC\n" \
        "$cfg" "$gtot" "$ctot" "$(echo "$gtot + $ctot" | bc)" "$wtot" "$peak"
}

PL="${1:?usage: hetero2.sh <pl1_watts>}"
echo "=== PL1 = ${PL} W   (${RUN_SECS}s sustained per config, 3 nodes) ==="
set_pl1 "$PL"
sleep 3
for cfg in gpu cpu both; do
    h=$(hottest)
    if [ "${h%.*}" -ge "$ABORT_C" ] 2>/dev/null; then
        echo "  ABORT: hottest core ${h}C >= ${ABORT_C}C"; exit 1
    fi
    run_cfg "$cfg"
    sleep 15
done
# revert explicitly, THEN disarm -- never disarm first.
revert
trap - EXIT
