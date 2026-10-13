#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Sweep mailbox payload sizes on node 0. Worker must already be running."""
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mbox_host import post
from mbox_proto import OP_SUM_U64, OP_PING, ST_IDLE, pack_hdr, PAYLOAD_OFF

MBOX = "/tank-red/vmdata/vca/mbox0.bin"
# leave room for 4K header; 16 MiB disk
MAX_PAYLOAD = 12 * 1024 * 1024


def main():
    fd = os.open(MBOX, os.O_RDWR)
    os.pwrite(fd, pack_hdr(0, ST_IDLE, 0, 0), 0)
    os.fsync(fd)

    print("bytes\trtt_ms\tMiB/s\tmatch")
    seq = int(time.time())
    t0 = time.time()
    post(fd, seq, OP_PING)
    print("0\t%.2f\t-\tping" % ((time.time() - t0) * 1000.0))

    for nbytes in (4096, 64 * 1024, 256 * 1024, 1024 * 1024, 4 * 1024 * 1024, 8 * 1024 * 1024):
        if nbytes > MAX_PAYLOAD:
            break
        n = nbytes // 8
        payload = struct.pack("<" + "Q" * n, *([1] * n))
        expect = n
        seq += 1
        t0 = time.time()
        hdr = post(fd, seq, OP_SUM_U64, payload)
        dt = time.time() - t0
        mibs = (nbytes / dt) / (1024 * 1024) if dt else 0
        print("%d\t%.2f\t%.1f\t%s" % (nbytes, dt * 1000.0, mibs, hdr["result"] == expect))


if __name__ == "__main__":
    main()
