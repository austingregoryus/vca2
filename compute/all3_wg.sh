#!/bin/bash
# SPDX-License-Identifier: MIT
# Run one GEMM tile per node in parallel, sum the GFLOP/s. Mirrors splits() in
# the gpu_*.py scripts: rows = n/3 with the remainder on node 2.
IPS=(172.31.1.1 172.31.2.1 172.31.3.1)
S() { sshpass -p vista1 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$1" "$2"; }

temps() {
  local out=""
  for i in 0 1 2; do
    t=$(S "${IPS[$i]}" "sensors | awk '/Package id 0/{print \$4}'")
    out="$out n$i=$t"
  done
  echo "   temps:$out"
}

run_set() {
  local n=$1 l0=$2 l1=$3 label=$4
  local a=$((n/3)) b=$((n/3)) c
  c=$((n - a - b))
  local rows=($a $b $c)
  local off=(0 $a $((a+b)))
  local tmp
  tmp=$(mktemp -d)
  local t0 t1
  t0=$(date +%s.%N)
  for i in 0 1 2; do
    ( S "${IPS[$i]}" "/tmp/vca_compute/wg_one $n ${off[$i]} ${rows[$i]} $l0 $l1" > "$tmp/$i" 2>&1 ) &
  done
  wait
  t1=$(date +%s.%N)
  local sum=0 ok=1 detail=""
  for i in 0 1 2; do
    g=$(sed -n 's/.*gflops=\([0-9.]*\).*/\1/p' "$tmp/$i")
    grep -q " ok" "$tmp/$i" || ok=0
    [ -z "$g" ] && { g=0; ok=0; }
    sum=$(echo "$sum + $g" | bc -l)
    detail="$detail $(printf '%.1f' "$g")"
  done
  printf "  %-22s n=%-5d  combined %7.1f GFLOP/s  (%s )  wall %.3fs  %s\n" \
    "$label" "$n" "$sum" "$detail" "$(echo "$t1 - $t0" | bc -l)" \
    "$([ $ok -eq 1 ] && echo ok || echo CHECK_FAILED)"
  rm -rf "$tmp"
}

echo "=== baseline temps ==="
temps
echo
echo "=== local=NULL (what cl_daemon.c does today) ==="
for n in 384 1024 1408 1536 1600 2048; do run_set $n 0 0 "NULL"; done
echo
temps
echo
echo "=== local={1,128} (one-line fix) ==="
for n in 384 1024 1408 1536 1600 2048; do run_set $n 1 128 "{1,128}"; done
echo
temps
