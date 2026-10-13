#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Persistent TCP sessions: PREP once, then KERNEL/KERNEL_NR/GEMM on all 3."""
from __future__ import print_function
import socket
import threading
import time

NODES = [("node0", "172.31.1.1"), ("node1", "172.31.2.1"), ("node2", "172.31.3.1")]
N = 8388608
ITERS = 20
GEMM_N = 384
GEMM_ROWS = 128  # 3*128 = 384 square


class Sess(object):
    def __init__(self, ip):
        self.s = socket.create_connection((ip, 9200), timeout=10)
        self.s.settimeout(60)
        self.f = self.s.makefile("rw")
        self.hello = self.f.readline().strip()

    def send(self, line):
        self.f.write(line + "\n")
        self.f.flush()
        return self.f.readline().strip()

    def close(self):
        try:
            self.send("QUIT")
        except Exception:
            pass
        self.s.close()


def parallel(fn):
    out = {}
    ths = []

    def wrap(label, ip, i):
        out[label] = fn(label, ip, i)

    t0 = time.time()
    for i, (label, ip) in enumerate(NODES):
        th = threading.Thread(target=wrap, args=(label, ip, i))
        th.start()
        ths.append(th)
    for th in ths:
        th.join()
    return time.time() - t0, out


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected %s %s" % (label, se.hello))
        sessions.append((label, ip, se))

    print("=== PREP tiles ===")
    t0 = time.time()
    for i, (label, ip, se) in enumerate(sessions):
        print(label, se.send("PREP %d %d" % (N, i * N)))
    print("prep_wall=%.3fs" % (time.time() - t0))

    print("=== KERNEL with readback ===")
    def kern(label, ip, i):
        se = sessions[i][2]
        t = time.time()
        r = se.send("KERNEL %d %d" % (N, ITERS))
        return time.time() - t, r
    wall, out = parallel(kern)
    for label, ip in NODES:
        print(label, "%.3fs" % out[label][0], out[label][1])
    print("kernel_readback_wall=%.3fs" % wall)

    print("=== KERNEL_NR no readback ===")
    def kernnr(label, ip, i):
        se = sessions[i][2]
        t = time.time()
        r = se.send("KERNEL_NR %d %d" % (N, ITERS))
        return time.time() - t, r
    wall, out = parallel(kernnr)
    for label, ip in NODES:
        print(label, "%.3fs" % out[label][0], out[label][1])
    print("kernel_nr_wall=%.3fs" % wall)

    print("=== tiled GEMM 384x384, 128 rows each, A=1 B=1 C=384 ===")
    def gemm(label, ip, i):
        se = sessions[i][2]
        t = time.time()
        r = se.send("GEMM %d %d %d" % (GEMM_N, i * GEMM_ROWS, GEMM_ROWS))
        return time.time() - t, r
    wall, out = parallel(gemm)
    all_ok = True
    for label, ip in NODES:
        dt, r = out[label]
        ok = "expect=384.0" in r and "c0=384.0" in r
        all_ok = all_ok and ok
        print(label, "%.3fs" % dt, "OK" if ok else "FAIL", r)
    print("gemm_wall=%.3fs all_ok=%s" % (wall, all_ok))
    for _, _, se in sessions:
        se.close()
    print("KERN_GEMM_OK" if all_ok else "KERN_GEMM_FAIL")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
