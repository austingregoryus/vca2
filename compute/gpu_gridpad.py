#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Is the 384 trap the matrix size or the NDRange? Pad global dim 1 only."""
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


def par_gemmg(sessions, n, g1):
    sp = splits(n)
    got = [None] * 3

    def one(i):
        r0, rows = sp[i]
        t = time.time()
        resp = sessions[i][1].send("GEMMG %d %d %d %d" % (n, r0, rows, g1))
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
        ok = ok and ("expect=%.1f" % float(n) in resp) and "ERR" not in resp
        gs.append(float(resp.split("gflops=")[1].split()[0]) if "gflops=" in resp else 0)
        print(" ", sessions[i][0], resp)
    return time.time() - t0, gs, ok


def rapl():
    # host cannot read guest RAPL; caller may SSH. placeholder
    return None


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected", label, se.hello)
        print(" ", se.send("INFO"))
        sessions.append((label, se))

    tests = [
        (1536, 1536, "unpadded trap"),
        (1536, 1544, "g1=1544"),
        (1536, 1600, "g1=1600"),
        (1536, 2048, "g1=2048"),
        (384, 384, "384 unpadded"),
        (384, 400, "384 g1=400"),
        (384, 512, "384 g1=512"),
        (1408, 1408, "1408 control"),
        (1408, 1536, "1408 g1=1536 poison grid?"),
        (1408, 1600, "1408 g1=1600"),
    ]
    print("n\tg1\twall\tsum\tnote")
    all_ok = True
    for n, g1, note in tests:
        wall, gs, ok = par_gemmg(sessions, n, g1)
        all_ok = all_ok and ok
        print("%d\t%d\t%.3f\t%.1f\t%s %s" % (
            n, g1, wall, sum(gs), "OK" if ok else "FAIL", note))
    for _, se in sessions:
        se.close()
    print("GRIDPAD_OK" if all_ok else "GRIDPAD_FAIL")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
