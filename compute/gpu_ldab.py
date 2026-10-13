#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Pad BOTH A and B leading dims; n stays 1536 (same FLOPs)."""
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


def par(sessions, n, lda, ldb):
    sp = splits(n)
    got = [None] * 3

    def one(i):
        r0, rows = sp[i]
        t = time.time()
        resp = sessions[i][1].send(
            "GEMMLDAB %d %d %d %d %d" % (n, r0, rows, lda, ldb))
        got[i] = resp

    t0 = time.time()
    ths = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    gs, ok = [], True
    for i in range(3):
        resp = got[i]
        print(" ", sessions[i][0], resp)
        ok = ok and ("expect=%.1f" % float(n) in resp)
        gs.append(float(resp.split("gflops=")[1].split()[0]) if "gflops=" in resp else 0)
    return time.time() - t0, gs, ok


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected", label, se.hello)
        sessions.append((label, se))
    tests = [
        (1536, 1536, 1536, "both n"),
        (1536, 1600, 1536, "A padded"),
        (1536, 1536, 1600, "B padded"),
        (1536, 1600, 1600, "both 1600"),
        (1536, 2048, 2048, "both 2048"),
        (1408, 1408, 1408, "control"),
    ]
    print("n\tlda\tldb\twall\tsum")
    ok_all = True
    for n, lda, ldb, note in tests:
        wall, gs, ok = par(sessions, n, lda, ldb)
        ok_all = ok_all and ok
        print("%d\t%d\t%d\t%.3f\t%.1f\t%s %s" % (
            n, lda, ldb, wall, sum(gs), "OK" if ok else "FAIL", note))
    for _, se in sessions:
        se.close()
    print("LDAB_OK" if ok_all else "LDAB_FAIL")
    if not ok_all:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
