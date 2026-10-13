#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Rewrite R_X86_64_PLT32 relocations to R_X86_64_PC32 in an ELF .ko.

Intel's 4.14.20 VCA kernel predates upstream commit b21ebf2fb4cd
("x86: Treat R_X86_64_PLT32 as R_X86_64_PC32", v4.16), so its module loader
rejects anything binutils >= 2.31 produces:

    insmod: ERROR: could not insert module foo.ko: Invalid module format
    dmesg:  module: foo: Unknown rela relocation: 4

binutils 2.31+ emits PLT32 (type 4) where 2.26 emitted PC32 (type 2) for
branches to global symbols. In a kernel module there is no PLT, so the two are
computed identically -- which is precisely why upstream's fix is a one-line
alias. This does the same rewrite in the object file instead of the kernel.

Without it, a modern userspace (Ubuntu 22.04, gcc 11 / binutils 2.38) cannot
load a module it just compiled, even though the compile itself succeeds and
vermagic matches exactly.

    plt32_to_pc32.py module.ko [more.ko ...]        # rewrites in place
"""
import struct
import sys

R_X86_64_PC32 = 2
R_X86_64_PLT32 = 4
SHT_RELA = 4


def patch(path):
    with open(path, "rb") as f:
        b = bytearray(f.read())

    if b[:4] != b"\x7fELF" or b[4] != 2 or b[5] != 1:
        raise SystemExit("%s: not a little-endian 64-bit ELF" % path)

    e_shoff, = struct.unpack_from("<Q", b, 0x28)
    e_shentsize, e_shnum = struct.unpack_from("<HH", b, 0x3A)
    if e_shoff == 0 or e_shnum == 0:
        raise SystemExit("%s: no section headers" % path)

    n = 0
    for i in range(e_shnum):
        sh = e_shoff + i * e_shentsize
        sh_type, = struct.unpack_from("<I", b, sh + 0x04)
        if sh_type != SHT_RELA:
            continue
        sh_offset, sh_size = struct.unpack_from("<QQ", b, sh + 0x18)
        sh_entsize, = struct.unpack_from("<Q", b, sh + 0x38)
        if sh_entsize != 24:          # Elf64_Rela
            continue
        for off in range(sh_offset, sh_offset + sh_size, sh_entsize):
            r_info, = struct.unpack_from("<Q", b, off + 8)
            if r_info & 0xFFFFFFFF == R_X86_64_PLT32:
                struct.pack_into("<Q", b, off + 8,
                                 (r_info & ~0xFFFFFFFF) | R_X86_64_PC32)
                n += 1

    if n:
        with open(path, "wb") as f:
            f.write(b)
    print("%s: %d PLT32 -> PC32" % (path, n))
    return n


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__.strip().splitlines()[-1].strip())
    for p in sys.argv[1:]:
        patch(p)
