#!/bin/bash
# SPDX-License-Identifier: MIT
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'
echo '=== host ==='
hostname; gcc --version | head -1; g++ --version | head -1
command -v cmake; cmake --version 2>/dev/null | head -1
command -v git; command -v curl; command -v wget
df -h / /tank-red 2>/dev/null | head
free -h | head -2
echo
for ip in 172.31.1.1 172.31.2.1 172.31.3.1; do
  echo "==== $ip ===="
  $SSH root@$ip 'gcc --version | head -1; g++ --version | head -1; cmake --version 2>/dev/null | head -1; command -v git; command -v curl; command -v wget; df -h / | tail -1; free -h | head -2; nproc; grep -m1 "model name" /proc/cpuinfo; ls /usr/bin/g++* 2>/dev/null'
done
echo '=== temps ==='
vcactl temp 2>/dev/null | grep Core | head
