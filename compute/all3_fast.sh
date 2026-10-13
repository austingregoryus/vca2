#!/bin/bash
# SPDX-License-Identifier: MIT
IPS=(172.31.1.1 172.31.2.1 172.31.3.1)
S(){ sshpass -p vista1 ssh -o StrictHostKeyChecking=no -o ConnectTimeout=10 "$1" "$2"; }
temps(){ local o=""; for i in 0 1 2; do
  t=$(S "${IPS[$i]}" "sensors|awk '/Package id 0/{print \$4}'"); o="$o n$i=$t"; done; echo "   temps:$o"; }
runset(){ local n=$1 v=$2 lbl=$3
  local a=$((n/3)) b=$((n/3)) c=$((n-2*(n/3)))
  local rows=($a $b $c) off=(0 $a $((a+b)))
  local tmp; tmp=$(mktemp -d); local t0 t1
  t0=$(date +%s.%N)
  for i in 0 1 2; do ( S "${IPS[$i]}" "/tmp/vca_compute/gemm_fast $n ${rows[$i]} $v" >"$tmp/$i" 2>&1 ) & done
  wait; t1=$(date +%s.%N)
  local sum=0 ok=1 d=""
  for i in 0 1 2; do
    g=$(sed -n 's/.*gflops=\([0-9.]*\).*/\1/p' "$tmp/$i")
    grep -qE " ok| VERIFIED" "$tmp/$i" || ok=0
    [ -z "$g" ] && { g=0; ok=0; }
    sum=$(echo "$sum+$g"|bc -l); d="$d $(printf '%.0f' "$g")"
  done
  printf "  %-16s n=%-5d  COMBINED %8.1f GFLOP/s  (%s )  wall %.3fs %s\n" \
    "$lbl" "$n" "$sum" "$d" "$(echo "$t1-$t0"|bc -l)" "$([ $ok -eq 1 ]&&echo ok||echo CHECK_FAIL)"
  rm -rf "$tmp"; }
temps; echo
for n in 1024 1408 1536 2048; do
  runset $n 0 "old naive"
  runset $n 1 "TUNED r8 TK32"
done
echo; temps
