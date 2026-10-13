#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One SAXPY of 24M floats, three GPU tiles, host checks each tile."""
from __future__ import print_function
import math
import re
import subprocess
import time

NODES = [
    ("node0", "172.31.1.1"),
    ("node1", "172.31.2.1"),
    ("node2", "172.31.3.1"),
]
N = 8388608  # per tile
ITERS = 20
A = 1.5
XSCALE = 0.001
# GPU does 1 warmup kernel + ITERS timed → ITERS+1 applications
K = ITERS + 1
SSH = [
    "sshpass", "-e", "ssh",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=8",
]


def expected(base, idx):
    x = XSCALE * float(base + idx)
    return 1.0 + K * A * x


def run_tile(ip, base):
    cmd = SSH + [
        "root@" + ip,
        "/tmp/vca_compute/cl_bench %d %d 1 %d" % (N, ITERS, base),
    ]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    return p


def parse(text):
    r = {"raw": text, "ok_parse": False}
    for line in text.splitlines():
        m = re.search(r"gpu_gflops=([0-9.]+)", line)
        if m:
            r["gpu_gflops"] = float(m.group(1))
        m = re.search(r"gpu_kernel_s=([0-9.]+)", line)
        if m:
            r["gpu_s"] = float(m.group(1))
        m = re.search(
            r"TILE base=(\d+) N=(\d+) y0=([0-9.]+) y100=([0-9.]+) ylast=([0-9.]+)",
            line,
        )
        if m:
            r["base"] = int(m.group(1))
            r["N"] = int(m.group(2))
            r["y0"] = float(m.group(3))
            r["y100"] = float(m.group(4))
            r["ylast"] = float(m.group(5))
            r["ok_parse"] = True
    return r


def close(got, exp, rel=1e-4, abs_eps=0.05):
    return abs(got - exp) <= max(abs_eps, rel * abs(exp))


def main():
    print("one problem: %d floats (%.1f MiB) in 3 tiles of %d" % (
        3 * N, 3 * N * 4 / 1024.0 / 1024.0, N))
    t0 = time.time()
    procs = []
    for i, (label, ip) in enumerate(NODES):
        base = i * N
        print("launch %s tile base=%d .. %d" % (label, base, base + N - 1))
        procs.append((label, ip, base, run_tile(ip, base)))
    all_ok = True
    gflops = []
    for label, ip, base, p in procs:
        out, _ = p.communicate()
        text = out.decode("utf-8", "replace")
        r = parse(text)
        e0 = expected(base, 0)
        e100 = expected(base, 100)
        elast = expected(base, N - 1)
        ok = (
            r.get("ok_parse")
            and p.returncode == 0
            and close(r["y0"], e0)
            and close(r["y100"], e100)
            and close(r["ylast"], elast)
        )
        all_ok = all_ok and ok
        gflops.append(r.get("gpu_gflops") or 0)
        print("%s rc=%s parse=%s gpu=%.2f GFLOP/s kernel=%.3fs" % (
            label, p.returncode, r.get("ok_parse"), r.get("gpu_gflops") or 0,
            r.get("gpu_s") or 0))
        print("  y0 got=%.6f exp=%.6f  y100 got=%.6f exp=%.6f  ylast got=%.6f exp=%.6f  %s" % (
            r.get("y0") or 0, e0, r.get("y100") or 0, e100,
            r.get("ylast") or 0, elast, "OK" if ok else "FAIL"))
        if not ok:
            print(text)
    wall = time.time() - t0
    print("=== tile summary ===")
    print("wall=%.3fs sum_kernel_gflops=%.2f all_ok=%s" % (
        wall, sum(gflops), all_ok))
    print("TILED_SAXPY_OK" if all_ok else "TILED_SAXPY_FAIL")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
