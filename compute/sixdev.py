#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One host process: 3 CPU mailbox jobs + 3 GPU tiles, overlapped."""
from __future__ import print_function
import os
import struct
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mbox_host import post
from mbox_proto import OP_SUM_U64

MBOXES = [
    "/tank-red/vmdata/vca/mbox0.bin",
    "/tank-red/vmdata/vca/mbox1.bin",
    "/tank-red/vmdata/vca/mbox2.bin",
]
SSH = [
    "sshpass", "-e", "ssh",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=8",
]
IPS = ["172.31.1.1", "172.31.2.1", "172.31.3.1"]
N_GPU = 8388608
ITERS = 20
N_CPU = 256 * 1024  # 2 MiB of uint64s; mailbox is 16 MiB


def cpu_job(i):
    path = MBOXES[i]
    payload = struct.pack("<" + "Q" * N_CPU, *([1] * N_CPU))
    fd = os.open(path, os.O_RDWR)
    t0 = time.time()
    hdr = post(fd, int(time.time() * 1000) + i, OP_SUM_U64, payload)
    dt = time.time() - t0
    os.close(fd)
    ok = hdr["result"] == N_CPU
    return ("cpu%d" % i, ok, hdr["result"], dt)


def gpu_job(i):
    base = i * N_GPU
    cmd = SSH + [
        "root@" + IPS[i],
        "/tmp/vca_compute/cl_bench %d %d 1 %d" % (N_GPU, ITERS, base),
    ]
    t0 = time.time()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    out, _ = p.communicate()
    dt = time.time() - t0
    text = out.decode("utf-8", "replace")
    ok = p.returncode == 0 and "TILE base=%d" % base in text
    return ("gpu%d" % i, ok, dt, text.splitlines()[-3:] if not ok else "")


def main():
    print("=== six devices: 3 CPU SUM_U64 + 3 GPU SAXPY tiles ===")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        cpu_f = [ex.submit(cpu_job, i) for i in range(3)]
        gpu_f = [ex.submit(gpu_job, i) for i in range(3)]
        cpu_r = [f.result() for f in cpu_f]
        gpu_r = [f.result() for f in gpu_f]
    wall = time.time() - t0
    all_ok = True
    for name, ok, result, dt in cpu_r:
        print("%s ok=%s result=%s time=%.3fs" % (name, ok, result, dt))
        all_ok = all_ok and ok
    for name, ok, dt, extra in gpu_r:
        print("%s ok=%s time=%.3fs %s" % (name, ok, dt, extra))
        all_ok = all_ok and ok
    print("sixdev_wall=%.3fs all_ok=%s" % (wall, all_ok))
    print("SIXDEV_OK" if all_ok else "SIXDEV_FAIL")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
