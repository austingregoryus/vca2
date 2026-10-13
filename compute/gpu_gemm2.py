#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""On-device C=A*B then D=C*B (A B^2), no host round-trip between multiplies."""
from __future__ import print_function
import socket
import threading
import time

NODES = [("node0", "172.31.1.1"), ("node1", "172.31.2.1"), ("node2", "172.31.3.1")]
N = 1024
ROWS = [341, 341, 342]


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


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected", label, se.hello)
        sessions.append((label, se))
    got = [None] * 3

    def one(i):
        label, se = sessions[i]
        r0, rows = (0 if i == 0 else 341 if i == 1 else 682), ROWS[i]
        t = time.time()
        resp = se.send("GEMM2 %d %d %d" % (N, r0, rows))
        got[i] = (label, time.time() - t, resp)

    t0 = time.time()
    ths = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    wall = time.time() - t0
    ok = True
    print("gemm2_wall=%.3fs" % wall)
    for i in range(3):
        label, dt, resp = got[i]
        this = "expect=1048576.0" in resp and "d0=1048576.0" in resp
        ok = ok and this
        print(label, "%.3fs" % dt, "OK" if this else "FAIL", resp)
    for _, se in sessions:
        se.close()
    print("GEMM2_OK" if ok else "GEMM2_FAIL")
    if not ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
