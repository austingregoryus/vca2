#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Guest worker: poll /dev/vcablk1 (BlockIO DMA) and run CPU ops."""
from __future__ import print_function
import fcntl
import os
import sys
import time
import struct

BLKFLSBUF = 0x1261  # flush block device cache so host writes are visible

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from mbox_proto import (
    unpack_hdr, pack_hdr, SECTOR, ST_REQ, ST_DONE, ST_ERR,
    OP_SUM_U64, OP_FILL_U64, OP_PING,
)

DEV = os.environ.get("VCA_MBOX_DEV", "/dev/vcablk1")


def aligned_read(fd, off, n):
    try:
        fcntl.ioctl(fd, BLKFLSBUF)
    except OSError:
        pass
    os.lseek(fd, off, os.SEEK_SET)
    buf = os.read(fd, n)
    if len(buf) != n:
        raise IOError("short read %d/%d @%d" % (len(buf), n, off))
    return buf


def aligned_write(fd, off, data):
    os.lseek(fd, off, os.SEEK_SET)
    w = os.write(fd, data)
    if w != len(data):
        raise IOError("short write")
    os.fsync(fd)


def run_op(fd, hdr, payload):
    op = hdr["op"]
    if op == OP_PING:
        return 0, payload
    if op == OP_SUM_U64:
        n = hdr["nbytes"] // 8
        vals = struct.unpack("<" + "Q" * n, payload[: n * 8])
        return sum(vals) & ((1 << 64) - 1), payload
    if op == OP_FILL_U64:
        n = hdr["nbytes"] // 8
        fill = hdr["seq"]
        out = struct.pack("<" + "Q" * n, *([fill] * n))
        return fill, out
    return 0, payload


def main():
    fd = os.open(DEV, os.O_RDWR)
    print("worker on %s fd=%d" % (DEV, fd), flush=True)
    last_seq = None
    while True:
        try:
            raw = aligned_read(fd, 0, SECTOR)
        except Exception as e:
            print("read hdr failed:", e, flush=True)
            time.sleep(0.05)
            continue
        hdr = unpack_hdr(raw)
        if not hdr or hdr["status"] != ST_REQ or hdr["seq"] == last_seq:
            time.sleep(0.002)
            continue
        try:
            payload = b""
            if hdr["nbytes"]:
                # round up to sector
                n = (hdr["nbytes"] + SECTOR - 1) // SECTOR * SECTOR
                payload = aligned_read(fd, hdr["in_off"], n)[: hdr["nbytes"]]
            result, out = run_op(fd, hdr, payload)
            if hdr["op"] == OP_FILL_U64 and out:
                n = (len(out) + SECTOR - 1) // SECTOR * SECTOR
                aligned_write(fd, hdr["out_off"], out.ljust(n, b"\x00"))
            aligned_write(
                fd,
                0,
                pack_hdr(
                    hdr["op"], ST_DONE, hdr["seq"], hdr["nbytes"],
                    hdr["in_off"], hdr["out_off"], result,
                ),
            )
            last_seq = hdr["seq"]
            print("seq=%d op=%d bytes=%d result=%d" % (
                hdr["seq"], hdr["op"], hdr["nbytes"], result), flush=True)
        except Exception as e:
            print("op failed:", e, flush=True)
            try:
                aligned_write(fd, 0, pack_hdr(hdr["op"], ST_ERR, hdr["seq"], 0))
            except Exception:
                pass
            last_seq = hdr["seq"]


if __name__ == "__main__":
    main()
