#!/bin/bash
# SPDX-License-Identifier: MIT
# thermal_run.sh  -- runs the whole thermal/power profile locally on a VCA
# node and writes /tmp/thermal.csv.
#
# Safety is enforced ON THE NODE, not by the host: if any core reaches
# TRIP degC the sampler kills every load immediately. That holds even if the
# controlling SSH session dies. Everything is also wrapped in `timeout`.
#
# Phases: idle -> GPU only -> GPU+CPU(AVX2) -> CPU only -> cooldown
# No bc on these guests; all float math is awk.

TRIP=85
IDLE=40
LOAD=120
COOL=90
OUT=/tmp/thermal.csv
CD=/tmp/vca_compute
RAPL=/sys/class/powercap/intel-rapl:0

rd(){ cat "$1" 2>/dev/null || echo 0; }
kill_loads(){ pkill -f gpuloop.sh 2>/dev/null; pkill -f gemm_fast 2>/dev/null; pkill -f "$CD/burn" 2>/dev/null; }
trap 'kill_loads; exit 0' TERM INT EXIT

echo "t_s,phase,pkg_w,core_w,uncore_w,dram_w,tmax_c,tpkg_c,gt_mhz" > "$OUT"

phase=idle
t_start=$(date +%s)
p_e=$(rd $RAPL/energy_uj); c_e=$(rd $RAPL:0/energy_uj)
u_e=$(rd $RAPL:1/energy_uj); d_e=$(rd $RAPL:2/energy_uj)
p_t=$(date +%s%N)

sample(){
  local np nc nu nd nt tmax tpkg gt line
  np=$(rd $RAPL/energy_uj); nc=$(rd $RAPL:0/energy_uj)
  nu=$(rd $RAPL:1/energy_uj); nd=$(rd $RAPL:2/energy_uj)
  nt=$(date +%s%N)
  tmax=$(sensors | awk '/^Core /{gsub(/[+C]/,"",$3); if($3+0>m) m=$3+0} END{printf "%d", m+0}')
  tpkg=$(sensors | awk '/Package id 0/{gsub(/[+C]/,"",$4); printf "%d", $4+0}')
  gt=$(rd /sys/class/drm/card0/gt_cur_freq_mhz)
  line=$(awk -v np=$np -v pe=$p_e -v nc=$nc -v ce=$c_e \
             -v nu=$nu -v ue=$u_e -v nd=$nd -v de=$d_e \
             -v nt=$nt -v pt=$p_t -v t=$(( $(date +%s) - t_start )) \
             -v ph="$phase" -v tm=$tmax -v tp=$tpkg -v g=$gt 'BEGIN{
      dt=(nt-pt)/1e9; if(dt<=0) dt=1;
      printf "%d,%s,%.1f,%.1f,%.1f,%.1f,%d,%d,%s", t, ph,
        (np-pe)/1e6/dt, (nc-ce)/1e6/dt, (nu-ue)/1e6/dt, (nd-de)/1e6/dt, tm, tp, g }')
  echo "$line" >> "$OUT"
  p_e=$np; c_e=$nc; u_e=$nu; d_e=$nd; p_t=$nt
  if [ "$tmax" -ge "$TRIP" ] 2>/dev/null; then
    echo "TRIP ${tmax}C -- killing loads" >> "$OUT"; kill_loads; return 1
  fi
  return 0
}

run_phase(){
  phase=$1; local dur=$2; local i=0
  while [ $i -lt $dur ]; do
    sleep 5; i=$((i+5))
    sample || { phase=tripped; return 1; }
  done
  return 0
}

run_phase idle $IDLE || exit 0

timeout $((LOAD+20)) bash $CD/gpuloop.sh $LOAD >/dev/null 2>&1 &
run_phase gpu $LOAD || exit 0
wait 2>/dev/null

timeout $((LOAD+20)) bash $CD/gpuloop.sh $LOAD >/dev/null 2>&1 &
timeout $((LOAD+20)) $CD/burn $LOAD 8 >/dev/null 2>&1 &
run_phase gpu+cpu $LOAD || exit 0
wait 2>/dev/null

timeout $((LOAD+20)) $CD/burn $LOAD 8 >/dev/null 2>&1 &
run_phase cpu $LOAD || exit 0
wait 2>/dev/null

run_phase cool $COOL
echo "COMPLETE" >> "$OUT"
