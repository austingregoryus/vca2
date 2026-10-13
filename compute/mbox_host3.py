#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Run the same CPU job on all three VCA nodes via BlockIO mailboxes."""
import os
import struct
import sys
import time
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mbox_host import post
from mbox_proto import OP_PING, OP_SUM_U64, ST_IDLE, pack_hdr

MBOXES = [
    "/tank-red/vmdata/vca/mbox0.bin",
    "/tank-red/vmdata/vca/mbox1.bin",
    "/tank-red/vmdata/vca/mbox2.bin",
]


def ping_one(path, seq):
    fd = os.open(path, os.O_RDWR)
    t0 = time.time()
    hdr = post(fd, seq, OP_PING)
    dt = time.time() - t0
    os.close(fd)
    return path, hdr["status"], dt


def sum_one(path, seq, payload):
    fd = os.open(path, os.O_RDWR)
    t0 = time.time()
    hdr = post(fd, seq, OP_SUM_U64, payload)
    dt = time.time() - t0
    os.close(fd)
    return path, hdr["result"], dt


def main():
    n = 1024 * 1024
    payload = struct.pack("<" + "Q" * n, *range(n))
    expect_one = (n * (n - 1) // 2) & ((1 << 64) - 1)

    print("=== ping all 3 ===")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        pings = list(ex.map(lambda i: ping_one(MBOXES[i], 10 + i), range(3)))
    print("wall=%.3f ms" % ((time.time() - t0) * 1000.0))
    for path, st, dt in pings:
        print("  %s status=%d rtt=%.2f ms" % (os.path.basename(path), st, dt * 1000.0))

    print("=== parallel sum 8 MiB x 3 CPUs ===")
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        sums = list(ex.map(lambda i: sum_one(MBOXES[i], 20 + i, payload), range(3)))
    wall = time.time() - t0
    total = 0
    for path, result, dt in sums:
        print("  %s result=%d match=%s time=%.3f s" % (
            os.path.basename(path), result, result == expect_one, dt))
        total += result
    print("combined=%d expect=%d match=%s wall=%.3f s  effective=%.1f MiB/s" % (
        total, expect_one * 3, total == expect_one * 3, wall,
        (24.0 / wall) if wall else 0,
    ))


if __name__ == "__main__":
    main()
