#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Naive split GEMM size sweep. Find where 1536 falls off a cliff."""
from __future__ import print_function
import socket
import threading
import time

NODES = [("node0", "172.31.1.1"), ("node1", "172.31.2.1"), ("node2", "172.31.3.1")]
SIZES = [512, 768, 1024, 1152, 1280, 1408, 1472, 1536, 1600, 1664, 1792, 1920, 2048]


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


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected", label, se.hello)
        sessions.append((label, se))
    print("n\twall\tg0\tg1\tg2\tsum\tok")
    all_ok = True
    for n in SIZES:
        sp = splits(n)
        got = [None] * 3

        def one(i):
            label, se = sessions[i]
            r0, rows = sp[i]
            t = time.time()
            resp = se.send("GEMM %d %d %d" % (n, r0, rows))
            got[i] = (time.time() - t, resp)

        t0 = time.time()
        ths = [threading.Thread(target=one, args=(i,)) for i in range(3)]
        for th in ths:
            th.start()
        for th in ths:
            th.join()
        wall = time.time() - t0
        gs, ok = [], True
        expect = "expect=%.1f" % float(n)
        for i in range(3):
            resp = got[i][1]
            ok = ok and expect in resp and "c0=" in resp and "ERR" not in resp
            gs.append(float(resp.split("gflops=")[1].split()[0]) if "gflops=" in resp else 0)
        all_ok = all_ok and ok
        print("%d\t%.3f\t%.1f\t%.1f\t%.1f\t%.1f\t%s" % (
            n, wall, gs[0], gs[1], gs[2], sum(gs), "OK" if ok else "FAIL"))
    for _, se in sessions:
        se.close()
    print("SWEEP_OK" if all_ok else "SWEEP_FAIL")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
