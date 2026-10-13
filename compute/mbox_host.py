#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Host coordinator: post jobs through a BlockIO-backed mailbox file."""
import argparse
import os
import struct
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mbox_proto import (
    pack_hdr, unpack_hdr, SECTOR, PAYLOAD_OFF,
    ST_IDLE, ST_REQ, ST_DONE, ST_ERR,
    OP_SUM_U64, OP_FILL_U64, OP_PING,
)

DEFAULT_MBOX = "/tank-red/vmdata/vca/mbox0.bin"


def sector_pad(n):
    return (n + SECTOR - 1) // SECTOR * SECTOR


def write_at(fd, off, data):
    os.pwrite(fd, data, off)
    os.fsync(fd)


def read_at(fd, off, n):
    return os.pread(fd, n, off)


def wait_done(fd, seq, timeout=10.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        hdr = unpack_hdr(read_at(fd, 0, SECTOR))
        if hdr and hdr["seq"] == seq and hdr["status"] in (ST_DONE, ST_ERR):
            return hdr
        time.sleep(0.002)
    return None


def post(fd, seq, op, payload=b"", out_off=None):
    nbytes = len(payload)
    in_off = PAYLOAD_OFF
    if out_off is None:
        out_off = PAYLOAD_OFF
    if payload:
        write_at(fd, in_off, payload.ljust(sector_pad(nbytes), b"\x00"))
    write_at(fd, 0, pack_hdr(op, ST_REQ, seq, nbytes, in_off, out_off))
    hdr = wait_done(fd, seq)
    if not hdr:
        raise TimeoutError("seq %d timed out" % seq)
    if hdr["status"] == ST_ERR:
        raise RuntimeError("worker error seq %d" % seq)
    return hdr


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--mbox", default=DEFAULT_MBOX)
    args = p.parse_args()
    fd = os.open(args.mbox, os.O_RDWR)
    write_at(fd, 0, pack_hdr(0, ST_IDLE, 0, 0))

    print("=== ping ===")
    t0 = time.time()
    hdr = post(fd, 1, OP_PING)
    dt = time.time() - t0
    print("ping seq=1 status=%d round-trip=%.3f ms" % (hdr["status"], dt * 1000.0))

    print("=== sum 1M uint64 (8 MiB) ===")
    n = 1024 * 1024
    payload = struct.pack("<" + "Q" * n, *range(n))
    expect = (n * (n - 1) // 2) & ((1 << 64) - 1)
    t0 = time.time()
    hdr = post(fd, 2, OP_SUM_U64, payload)
    dt = time.time() - t0
    print("sum result=%d expect=%d match=%s time=%.3f s  %.1f MiB/s" % (
        hdr["result"], expect, hdr["result"] == expect, dt,
        (len(payload) / dt) / (1024 * 1024) if dt else 0,
    ))

    print("=== fill 1M uint64 ===")
    t0 = time.time()
    hdr = post(fd, 3, OP_FILL_U64, b"\x00" * (n * 8))
    dt = time.time() - t0
    out = read_at(fd, PAYLOAD_OFF, 16)
    first = struct.unpack("<Q", out[:8])[0]
    print("fill seq-as-value=%d first_u64=%d time=%.3f s" % (hdr["result"], first, dt))
    print("OK")


if __name__ == "__main__":
    main()
