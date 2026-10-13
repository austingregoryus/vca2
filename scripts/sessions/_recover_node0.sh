#!/bin/bash
# SPDX-License-Identifier: MIT
set -x
export SSHPASS=vista1
SSH='sshpass -e ssh -o StrictHostKeyChecking=no -o ConnectTimeout=8'

echo '=== shutdown nodes 1 and 2 from guest OS ==='
$SSH root@172.31.2.1 'sync; nohup shutdown -h now >/dev/null 2>&1 &' || true
$SSH root@172.31.3.1 'sync; nohup shutdown -h now >/dev/null 2>&1 &' || true
sleep 15
vcactl status

echo '=== os-shutdown via vcactl for 1 and 2 ==='
vcactl os-shutdown 0 1 || true
vcactl os-shutdown 0 2 || true
sleep 10
vcactl status

echo '=== close blockio 1 and 2 ==='
vcactl --force blockio close 0 1 vcablk0 || true
vcactl --force blockio close 0 2 vcablk0 || true
vcactl --force blockio close 0 0 vcablk0 || true
sleep 2
vcactl blockio list

echo '=== reset all three --force ==='
vcactl --force reset 0 0 || true
vcactl --force reset 0 1 || true
vcactl --force reset 0 2 || true
sleep 5
vcactl status
