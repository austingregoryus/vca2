#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Scale split GEMM: naive vs float4 at 1024, then 1536 and 2048 naive."""
from __future__ import print_function
import socket
import threading
import time

NODES = [("node0", "172.31.1.1"), ("node1", "172.31.2.1"), ("node2", "172.31.3.1")]


def splits(n):
    a, b = n // 3, n // 3
    c = n - a - b
    return [(0, a), (a, b), (a + b, c)]


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


def run_cmd(sessions, cmd_fmt, n, reps, tag):
    sp = splits(n)
    got = [None] * 3

    def one(i):
        label, se = sessions[i]
        r0, rows = sp[i]
        t = time.time()
        resp = se.send(cmd_fmt % (n, r0, rows, reps) if reps is not None
                       else cmd_fmt % (n, r0, rows))
        got[i] = (label, time.time() - t, resp, r0, rows)

    t0 = time.time()
    ths = [threading.Thread(target=one, args=(i,)) for i in range(3)]
    for th in ths:
        th.start()
    for th in ths:
        th.join()
    wall = time.time() - t0
    ok = True
    expect = "expect=%.1f" % float(n)
    c0 = "c0=%.1f" % float(n)
    print("=== %s n=%d reps=%s wall=%.3fs ===" % (tag, n, reps, wall))
    gsum = 0.0
    for label, dt, resp, r0, rows in got:
        this_ok = expect in resp and c0 in resp and "ERR" not in resp
        ok = ok and this_ok
        gflops = None
        if "gflops=" in resp:
            gflops = float(resp.split("gflops=")[1].split()[0])
            gsum += gflops
        print("%s rows=%d+%d %.3fs %s %s" % (
            label, r0, rows, dt, "OK" if this_ok else "FAIL", resp))
    print("sum_gflops=%.1f all_ok=%s" % (gsum, ok))
    return ok, wall, gsum


def main():
    sessions = []
    for label, ip in NODES:
        se = Sess(ip)
        print("connected", label, se.hello)
        sessions.append((label, se))

    all_ok = True
    # naive 1024 one-shot (GEMM has implicit 1 timed kernel)
    ok, _, _ = run_cmd(sessions, "GEMM %d %d %d", 1024, None, "naive-1024")
    all_ok = all_ok and ok
    ok, _, _ = run_cmd(sessions, "GEMMF %d %d %d %d", 1024, 10, "float4-1024-x10")
    all_ok = all_ok and ok
    ok, _, _ = run_cmd(sessions, "GEMM %d %d %d", 1536, None, "naive-1536")
    all_ok = all_ok and ok
    ok, _, _ = run_cmd(sessions, "GEMM %d %d %d", 2048, None, "naive-2048")
    all_ok = all_ok and ok

    for _, se in sessions:
        se.close()
    print("SCALE_GEMM_OK" if all_ok else "SCALE_GEMM_FAIL")
    if not all_ok:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
