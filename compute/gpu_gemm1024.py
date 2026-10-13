#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""1024x1024 GEMM split across 3 P580s (blocked kernel). Watch nothing here; caller logs temps."""
from __future__ import print_function
import socket
import threading
import time

NODES = [("node0", "172.31.1.1"), ("node1", "172.31.2.1"), ("node2", "172.31.3.1")]
N = 1024
# 341+341+342 = 1024
ROWS = [341, 341, 342]
REPS = 20


class Sess(object):
    def __init__(self, ip):
        self.s = socket.create_connection((ip, 9200), timeout=10)
        self.s.settimeout(120)
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


def main():
    sessions = []
    row0 = 0
    splits = []
    for i, (label, ip) in enumerate(NODES):
        se = Sess(ip)
        print("connected", label, se.hello)
        sessions.append((label, se, row0, ROWS[i]))
        splits.append((row0, ROWS[i]))
        row0 += ROWS[i]
    print("N=%d splits=%s reps=%d flops_total=%.2e" % (
        N, splits, REPS, 2.0 * N * N * N * REPS))

    print("=== naive GEMM once (warmup/compare) ===")
    t0 = time.time()
    naive = []
    ths = []

    def nai(i):
        label, se, r0, rows = sessions[i]
        t = time.time()
        resp = se.send("GEMM %d %d %d" % (N, r0, rows))
        naive.append((label, time.time() - t, resp))

    for i in range(3):
        th = threading.Thread(target=nai, args=(i,))
        th.start()
        ths.append(th)
    for th in ths:
        th.join()
    print("naive_wall=%.3fs" % (time.time() - t0))
    for label, dt, resp in naive:
        print(label, "%.3fs" % dt, resp)

    print("=== blocked GEMMB x%d ===" % REPS)
    blocked = []

    def blk(i):
        label, se, r0, rows = sessions[i]
        t = time.time()
        resp = se.send("GEMMB %d %d %d %d" % (N, r0, rows, REPS))
        blocked.append((label, time.time() - t, resp))

    t0 = time.time()
    ths = []
    for i in range(3):
        th = threading.Thread(target=blk, args=(i,))
        th.start()
        ths.append(th)
    for th in ths:
        th.join()
    wall = time.time() - t0
    all_ok = True
    print("blocked_wall=%.3fs" % wall)
    for label, dt, resp in blocked:
        ok = "expect=1024.0" in resp and "c0=1024.0" in resp and "ERR" not in resp
        all_ok = all_ok and ok
        print(label, "%.3fs" % dt, "OK" if ok else "FAIL", resp)
    print("GEMM1024_OK" if all_ok else "GEMM1024_FAIL")
    for _, se, _, _ in sessions:
        se.close()
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
