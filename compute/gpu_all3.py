#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""One host process, three P580s: same OpenCL SAXPY, sequential vs parallel."""
from __future__ import print_function
import re
import subprocess
import time

NODES = [
    ("node0", "172.31.1.1"),
    ("node1", "172.31.2.1"),
    ("node2", "172.31.3.1"),
]
N = 8388608
ITERS = 20
# argv3=1 → skip the scalar CPU loop so wall clock is GPU+OpenCL setup
BENCH = "/tmp/vca_compute/cl_bench %d %d 1" % (N, ITERS)
SSH = [
    "sshpass", "-e", "ssh",
    "-o", "StrictHostKeyChecking=no",
    "-o", "ConnectTimeout=8",
]


def run_one(ip):
    p = subprocess.Popen(
        SSH + ["root@" + ip, BENCH],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=None,  # inherit SSHPASS
    )
    return p


def parse(label, out):
    text = out.decode("utf-8", "replace")
    gpu = cpu = gtime = None
    y0 = y100 = None
    for line in text.splitlines():
        m = re.search(r"cpu_s=([0-9.]+)\s+gpu_kernel_s=([0-9.]+)", line)
        if m:
            cpu, gtime = float(m.group(1)), float(m.group(2))
        m = re.search(r"cpu_gflops=([0-9.]+)\s+gpu_gflops=([0-9.]+)", line)
        if m:
            gpu = float(m.group(2))
        m = re.search(r"y\[0\]=([0-9.]+)\s+y\[100\]=([0-9.]+)", line)
        if m:
            y0, y100 = float(m.group(1)), float(m.group(2))
    return {
        "label": label,
        "raw": text.strip(),
        "gpu_gflops": gpu,
        "cpu_s": cpu,
        "gpu_s": gtime,
        "y0": y0,
        "y100": y100,
        "ok": y0 == 1.0 and abs(y100 - 4.15) < 0.001,
    }


def collect(proc):
    out, _ = proc.communicate()
    return out, proc.returncode


def main():
    print("N=%d iters=%d floats/node=%.1f MiB" % (N, ITERS, N * 4 / 1024.0 / 1024.0))
    print("=== sequential (one GPU at a time) ===")
    seq_results = []
    t0 = time.time()
    for label, ip in NODES:
        p = run_one(ip)
        out, rc = collect(p)
        r = parse(label, out)
        r["rc"] = rc
        seq_results.append(r)
        print("%s rc=%s ok=%s gpu=%.2f GFLOP/s kernel=%.3fs" % (
            label, rc, r["ok"], r["gpu_gflops"] or 0, r["gpu_s"] or 0))
        if not r["ok"]:
            print(r["raw"])
    seq_wall = time.time() - t0

    print("=== parallel (all three at once) ===")
    t0 = time.time()
    procs = [(label, ip, run_one(ip)) for label, ip in NODES]
    par_results = []
    for label, ip, p in procs:
        out, rc = collect(p)
        r = parse(label, out)
        r["rc"] = rc
        par_results.append(r)
        print("%s rc=%s ok=%s gpu=%.2f GFLOP/s kernel=%.3fs y0=%.4f y100=%.4f" % (
            label, rc, r["ok"], r["gpu_gflops"] or 0, r["gpu_s"] or 0,
            r["y0"] or 0, r["y100"] or 0))
        if not r["ok"]:
            print(r["raw"])
    par_wall = time.time() - t0

    flops_one = 2.0 * N * ITERS
    par_ok = all(r["ok"] and r["rc"] == 0 for r in par_results)
    seq_ok = all(r["ok"] and r["rc"] == 0 for r in seq_results)
    print("=== summary ===")
    print("sequential_wall=%.3fs  parallel_wall=%.3fs  ratio=%.2fx" % (
        seq_wall, par_wall, seq_wall / par_wall if par_wall else 0))
    print("effective_parallel_gflops=%.2f  (3 devices, wall clock)" % (
        3 * flops_one / par_wall / 1e9 if par_wall else 0))
    print("sum_of_kernel_gflops=%.2f" % sum(r["gpu_gflops"] or 0 for r in par_results))
    print("all_ok sequential=%s parallel=%s" % (seq_ok, par_ok))
    if not par_ok:
        raise SystemExit(1)
    print("SIX_DEVICE_GPU_OK")


if __name__ == "__main__":
    main()
