#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pad 1536 off the 384 trap; GPU full tile + extra CPU GEMM at 1408."""
from __future__ import print_function
import socket
import threading
import time

NODES = [("node0", "172.31.1.1"), ("node1", "172.31.2.1"), ("node2", "172.31.3.1")]


def splits(n):
    a, b = n // 3, n // 3
    return [(0, a), (a, b), (a + b, n - a - b)]


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
    sp = splits(n)
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
    ok = True
    for i in range(3):
        resp = got[i][1]
        ok = ok and ("expect=%.1f" % float(n) in resp)
        gs.append(float(resp.split("gflops=")[1].split()[0]) if "gflops=" in resp else 0)
    return time.time() - t0, gs, ok


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected", label, se.hello)
        sessions.append((label, se))

    print("=== pad workaround (1536 is 4*384) ===")
    for n in (1536, 1544, 1568, 1600):
        wall, gs, ok = par_gemm(sessions, n)
        print("n=%d wall=%.3f sum=%.1f %s  (%s)" % (
            n, wall, sum(gs), "OK" if ok else "FAIL",
            "/".join("%.1f" % x for x in gs)))

    print("=== GPU-only 1408 baseline ===")
    wall, gs, ok = par_gemm(sessions, 1408)
    print("gpu_only_1408 wall=%.3f sum=%.1f %s" % (wall, sum(gs), ok))

    sp = splits(1408)
    print("=== HADD 1408: GPU full tile + extra CPU 80 rows ===")
    got = [None] * 3

    def hadd(i):
        _, gpu_rows = sp[i]
        t = time.time()
        resp = sessions[i][1].send("HADD %d %d %d" % (1408, gpu_rows, 80))
        got[i] = (time.time() - t, resp, gpu_rows)

    t0 = time.time()
    ths = [threading.Thread(target=hadd, args=(i,)) for i in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    hwall = time.time() - t0
    hok = True
    print("hadd_wall=%.3fs" % hwall)
    for i, (label, _) in enumerate(NODES):
        dt, resp, grows = got[i]
        this = "expect=1408.0" in resp and "gc0=1408.0" in resp and "cc0=1408.0" in resp
        hok = hok and this
        print(label, "gpu_rows=%d" % grows, "%.3fs" % dt, "OK" if this else "FAIL", resp)
    print("PAD_HADD_OK" if hok else "PAD_HADD_FAIL")
    for _, se in sessions:
        se.close()
    if not hok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
