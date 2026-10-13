#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""n=1536 GEMM with padded B leading dimension. Same FLOPs, different stride."""
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


def par(sessions, n, lda):
    sp = splits(n)
    got = [None] * 3

    def one(i):
        r0, rows = sp[i]
        t = time.time()
        resp = sessions[i][1].send("GEMMLDA %d %d %d %d" % (n, r0, rows, lda))
        got[i] = (time.time() - t, resp)

    t0 = time.time()
    ths = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    gs, ok = [], True
    for i in range(3):
        resp = got[i][1]
        print(" ", sessions[i][0], resp)
        ok = ok and ("expect=%.1f" % float(n) in resp) and "ERR" not in resp
        gs.append(float(resp.split("gflops=")[1].split()[0]) if "gflops=" in resp else 0)
    return time.time() - t0, gs, ok


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected", label, se.hello)
        sessions.append((label, se))
    tests = [
        (1536, 1536, "trap lda=n"),
        (1536, 1544, "lda=1544"),
        (1536, 1600, "lda=1600"),
        (1536, 2048, "lda=2048"),
        (1408, 1408, "fast control"),
        (1408, 1536, "fast n, poison lda"),
        (384, 384, "384 lda=n"),
        (384, 400, "384 lda=400"),
        (384, 512, "384 lda=512"),
    ]
    print("n\tlda\twall\tsum\tnote")
    all_ok = True
    for n, lda, note in tests:
        wall, gs, ok = par(sessions, n, lda)
        all_ok = all_ok and ok
        print("%d\t%d\t%.3f\t%.1f\t%s %s" % (
            n, lda, wall, sum(gs), "OK" if ok else "FAIL", note))
    for _, se in sessions:
        se.close()
    print("LDA_OK" if all_ok else "LDA_FAIL")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
