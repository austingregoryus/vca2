#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Hit three warm OpenCL daemons at once (TCP 9200)."""
from __future__ import print_function
import socket
import time

NODES = [
    ("node0", "172.31.1.1"),
    ("node1", "172.31.2.1"),
    ("node2", "172.31.3.1"),
]
PORT = 9200
N = 8388608
ITERS = 20


def cmd(ip, line, timeout=30):
    s = socket.create_connection((ip, PORT), timeout=5)
    s.settimeout(timeout)
    f = s.makefile("rw")
    hello = f.readline()
    f.write(line if line.endswith("\n") else line + "\n")
    f.flush()
    resp = f.readline()
    f.write("QUIT\n")
    f.flush()
    s.close()
    return hello.strip(), resp.strip()


def parallel_saxpy():
    import threading
    out = {}

    def one(label, ip, base):
        t0 = time.time()
        hello, resp = cmd(ip, "SAXPY %d %d %d" % (N, ITERS, base))
        out[label] = (time.time() - t0, hello, resp)

    threads = []
    t0 = time.time()
    for i, (label, ip) in enumerate(NODES):
        th = threading.Thread(target=one, args=(label, ip, i * N))
        th.start()
        threads.append(th)
    for th in threads:
        th.join()
    return time.time() - t0, out


def main():
    print("=== ping warm daemons ===")
    for label, ip in NODES:
        t0 = time.time()
        hello, resp = cmd(ip, "PING")
        print("%s rtt=%.1fms %s %s" % (label, (time.time() - t0) * 1000, hello, resp))

    print("=== parallel SAXPY on warm contexts ===")
    wall, out = parallel_saxpy()
    for label, ip in NODES:
        dt, hello, resp = out[label]
        print("%s host_rtt=%.3fs %s" % (label, dt, resp))
    print("warm_parallel_wall=%.3fs" % wall)
    print("WARM_SAXPY_OK")


if __name__ == "__main__":
    main()
