#!/usr/bin/env python3
"""Legaia Densetsu LZSS decompressor.

Reverse-engineered from SCPS_100.59 @ RAM 0x8001A15C (the routine that prints "decode").

  ring buffer: 4096 bytes, zero-filled, write ptr starts at 0xFEE
  flags: read one byte, bits consumed LSB-first; the `ori $t0,$v0,0xff00`
         trick means after 8 shifts the high marker bit (0x100) clears and a
         new flag byte is fetched.
  bit==1 -> literal: copy 1 byte from src to dst and into ring[ptr]
  bit==0 -> match: read 2 bytes b0,b1
              offset = b0 | ((b1 & 0xF0) << 4)      (12-bit)
              count  = (b1 & 0x0F) + 2              (so length = count+1 bytes)
            copy bytes ring[(offset+i) & 0xFFF] for i in 0..count

  The outer loop decrements `remaining` (s2) once per *compressed-input group*
  as the asm does, so the caller passes the compressed size.
"""


def decompress(src: bytes, comp_size: int = None) -> bytes:
    if comp_size is None:
        comp_size = len(src)
    ring = bytearray(4096)
    ptr = 0xFEE
    dst = bytearray()
    s = 0            # src cursor
    remaining = comp_size
    flags = 0

    while remaining > 0:
        flags >>= 1
        if (flags & 0x100) == 0:
            if s >= len(src):
                break
            flags = src[s] | 0xFF00
            s += 1
        if flags & 1:
            # literal
            if s >= len(src):
                break
            b = src[s]; s += 1
            remaining -= 1
            dst.append(b)
            ring[ptr] = b
            ptr = (ptr + 1) & 0xFFF
        else:
            # match
            if s + 1 >= len(src):
                break
            b0 = src[s]; b1 = src[s + 1]; s += 2
            remaining -= 1
            offset = b0 | ((b1 & 0xF0) << 4)
            count = (b1 & 0x0F) + 2
            for i in range(count + 1):
                b = ring[(offset + i) & 0xFFF]
                dst.append(b)
                ring[ptr] = b
                ptr = (ptr + 1) & 0xFFF
    return bytes(dst)


if __name__ == "__main__":
    import sys
    data = open(sys.argv[1], "rb").read()
    off = int(sys.argv[2], 0) if len(sys.argv) > 2 else 0
    size = int(sys.argv[3], 0) if len(sys.argv) > 3 else len(data) - off
    out = decompress(data[off:off + size], size)
    sys.stdout.buffer.write(out)
