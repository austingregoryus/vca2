# SPDX-License-Identifier: MIT
"""Shared mailbox header for VCA BlockIO DMA channel."""
import struct

MAGIC = b"VCAM"
HDR_FMT = "<4sIIQQQQQ"
HDR_SIZE = struct.calcsize(HDR_FMT)
assert HDR_SIZE <= 512
SECTOR = 512
PAYLOAD_OFF = 4096

ST_IDLE, ST_REQ, ST_DONE, ST_ERR = 0, 1, 2, 3
OP_SUM_U64, OP_FILL_U64, OP_PING = 1, 2, 3


def pack_hdr(op, status, seq, nbytes, in_off=PAYLOAD_OFF, out_off=PAYLOAD_OFF, result=0):
    raw = struct.pack(HDR_FMT, MAGIC, op, status, seq, nbytes, in_off, out_off, result)
    return raw.ljust(SECTOR, b"\x00")


def unpack_hdr(buf):
    magic, op, status, seq, nbytes, in_off, out_off, result = struct.unpack(HDR_FMT, buf[:HDR_SIZE])
    if magic != MAGIC:
        return None
    return {
        "op": op,
        "status": status,
        "seq": seq,
        "nbytes": nbytes,
        "in_off": in_off,
        "out_off": out_off,
        "result": result,
    }
