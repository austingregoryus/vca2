#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""CPU OpenMP GEMM + iGPU GEMM overlapped; confirm 384 vs 400."""
from __future__ import print_function
import socket
import threading
import time

NODES = [("node0", "172.31.1.1"), ("node1", "172.31.2.1"), ("node2", "172.31.3.1")]


class Sess(object):
    def __init__(self, ip):
        self.s = socket.create_connection((ip, 9200), timeout=10)
        self.s.settimeout(180)
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


def par_gemm(sessions, n):
    a, b = n // 3, n // 3
    sp = [(0, a), (a, b), (a + b, n - a - b)]
    got = [None] * 3

    def one(i):
        t = time.time()
        r0, rows = sp[i]
        resp = sessions[i][1].send("GEMM %d %d %d" % (n, r0, rows))
        got[i] = (time.time() - t, resp)

    t0 = time.time()
    ths = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    gs = []
    for i in range(3):
        resp = got[i][1]
        gs.append(float(resp.split("gflops=")[1].split()[0]) if "gflops=" in resp else 0)
    return time.time() - t0, gs, got


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected", label, se.hello)
        sessions.append((label, se))

    print("=== 384-rule check ===")
    for n in (384, 400, 640, 896, 1408):
        wall, gs, got = par_gemm(sessions, n)
        ok = all("expect=%.1f" % float(n) in g[1] for g in got)
        print("n=%d wall=%.3f gflops=%.1f/%.1f/%.1f sum=%.1f %s" % (
            n, wall, gs[0], gs[1], gs[2], sum(gs), "OK" if ok else "FAIL"))

    print("=== CPUGEMM calibrate node0 n=1024 rows=128 ===")
    print(sessions[0][1].send("CPUGEMM 1024 128"))
    print("=== CPUGEMM node0 n=1024 rows=256 ===")
    print(sessions[0][1].send("CPUGEMM 1024 256"))

    print("=== HETERO per node: GPU 256 + CPU 85 of 1024-row tile ===")
    got = [None] * 3

    def het(i):
        t = time.time()
        resp = sessions[i][1].send("HETERO 1024 256 85")
        got[i] = (time.time() - t, resp)

    t0 = time.time()
    ths = [threading.Thread(target=het, args=(i,)) for i in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    wall = time.time() - t0
    ok = True
    print("hetero_wall=%.3fs" % wall)
    for i, (label, _) in enumerate(NODES):
        dt, resp = got[i]
        this = "expect=1024.0" in resp and "gc0=1024.0" in resp and "cc0=1024.0" in resp
        ok = ok and this
        print(label, "%.3fs" % dt, "OK" if this else "FAIL", resp)
    print("HETERO_OK" if ok else "HETERO_FAIL")
    for _, se in sessions:
        se.close()
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
