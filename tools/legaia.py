#!/usr/bin/env python3
"""Legaia Densetsu extraction pipeline (validated against LegaiaText reference tool).

PROT.DAT:  u32 version, u32 count, then count u32 sector offsets (<<11 = byte addr)
PACK:      u32 fileCount, u32 totalDecLength,
           then per entry: u24 decLength, u8 fileId, u32 compAddress (rel to pack start)
LZSS:      4096 ring, start 4078 (0xFEE), flag bits LSB-first,
           bit=1 literal; bit=0 -> u16 little: len=((v>>8)&0xF)+3, pos=((v&0xF000)>>4)|(v&0xFF)
           terminates on DECOMPRESSED length (not compressed size!)
Script:    0x22 header, u16 count0/1/2, u24 ptr table at 0x2b (3 bytes each),
           base = 0x2b + count*3
"""
import struct


# ---------------- LZSS ----------------

def lzss_decompress(data: bytes, input_addr: int, dec_length: int) -> bytes:
    out = bytearray(dec_length)
    dic = bytearray(0x1000)
    oa = 0
    da = 4078
    mask = 0x80
    header = 0
    ia = input_addr
    while oa < dec_length:
        mask <<= 1
        if mask == 0x100:
            if ia >= len(data):
                break
            header = data[ia]; ia += 1
            mask = 1
        if header & mask:
            if ia >= len(data):
                break
            b = data[ia]; ia += 1
            dic[da] = b
            out[oa] = b; oa += 1
            da = (da + 1) & 0xFFF
        else:
            if ia + 1 >= len(data):
                break
            v = data[ia] | (data[ia + 1] << 8)
            ia += 2
            length = ((v >> 8) & 0xF) + 3
            pos = ((v & 0xF000) >> 4) | (v & 0xFF)
            for _ in range(length):
                if oa >= dec_length:
                    break
                b = dic[pos]
                dic[da] = b
                out[oa] = b; oa += 1
                da = (da + 1) & 0xFFF
                pos = (pos + 1) & 0xFFF
    return bytes(out)


def lzss_compress(data: bytes) -> bytes:
    """Mirror of the reference C# compressor.

    Key detail: the C# code writes a placeholder header byte lazily. It starts
    with Mask=0x80 so the very first `Mask <<= 1` hits 0x100 and allocates the
    first header byte at position 0. We replicate that exactly.
    """
    out = bytearray()
    dic = bytearray(0x1000)
    da = 4078
    sa = 0
    bits_addr = 0
    mask = 0x80
    header = 0

    while sa < len(data):
        mask <<= 1
        if mask == 0x100:
            # flush previous header byte, allocate a new one
            if len(out) > 0:
                out[bits_addr] = header
            bits_addr = len(out)
            out.append(0)
            header = 0
            mask = 1

        best_len = 2
        best_pos = 0
        n = len(data)
        if sa + 2 < n:
            c0 = data[sa]
            limit = min(18, n - sa)
            # ★ 4096칸을 파이썬 루프로 훑지 않는다.
            #   첫 바이트가 일치하는 위치만 bytearray.find (C 속도) 로 찾아
            #   낮은 idx 부터 순서대로 검사한다. (원본과 동일한 탐색 순서)
            idx = dic.find(c0)
            while idx >= 0:
                # ★ 원본은 매 후보마다 dic(4096B)을 통째로 복사했다 —— 그게 병목.
                #   매칭 도중 링버퍼(da..)에 써 넣는 바이트는 data[sa..] 그 자체이므로,
                #   복사 대신 '이미 쓴 구간이면 data 에서 읽는다'로 대체한다. (출력 동일)
                mlen = 0
                for j in range(limit):
                    pos = (idx + j) & 0xFFF
                    off = (pos - da) & 0xFFF
                    c = data[sa + off] if off < j else dic[pos]
                    if c != data[sa + j]:
                        break
                    mlen += 1
                if mlen > best_len:
                    best_len = mlen
                    best_pos = idx
                    if mlen >= limit:      # 더 길어질 수 없다
                        break
                idx = dic.find(c0, idx + 1)

        if best_len > 2:
            out.append(best_pos & 0xFF)
            nib_lo = (best_len - 3) & 0xF
            nib_hi = (best_pos >> 4) & 0xF0
            out.append(nib_lo | nib_hi)
            length = best_len
        else:
            header |= mask & 0xFF
            out.append(data[sa])
            length = 1

        for _ in range(length):
            dic[da] = data[sa]
            sa += 1
            da = (da + 1) & 0xFFF

    if len(out) > 0:
        out[bits_addr] = header
    return bytes(out)


# ---------------- PROT ----------------

def prot_files(prot: bytes):
    """Yield (index, start, end) byte ranges of each file in PROT.DAT."""
    count = struct.unpack("<I", prot[4:8])[0]
    addrs = [struct.unpack("<I", prot[8 + i * 4:12 + i * 4])[0] << 11 for i in range(count)]
    for i in range(count):
        s = addrs[i]
        e = addrs[i + 1] if i + 1 < count else len(prot)
        yield i, s, e


# ---------------- PACK ----------------

def read_u24(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def pack_parse(data: bytes):
    """Return list of (fileId, decompressed_bytes) or None if not a PACK."""
    if len(data) < 0x10:
        return None
    count = struct.unpack("<I", data[0:4])[0]
    if count == 0 or count > 0x1000:
        return None
    first_addr = 8 + count * 8
    if first_addr + 4 > len(data):
        return None
    # validity check: entry0's address field must equal first_addr
    a0 = struct.unpack("<I", data[12:16])[0]
    if a0 != first_addr:
        return None
    files = []
    for i in range(count):
        o = 8 + i * 8
        dec_len = read_u24(data, o)
        fid = data[o + 3]
        addr = struct.unpack("<I", data[o + 4:o + 8])[0]
        if addr >= len(data) or dec_len > 0x400000:
            return None
        files.append((fid, lzss_decompress(data, addr, dec_len)))
    return files


# ---------------- Script ----------------

def script_parse(data: bytes):
    """Return list of script byte-blobs, or None."""
    if len(data) < 0x2E:
        return None
    c0, c1, c2 = struct.unpack("<HHH", data[0x22:0x28])
    count = c0 + c1 + c2
    if count == 0 or count > 0x4000:
        return None
    base = 0x2B + count * 3
    footer = read_u24(data, 0x28) + base
    if footer >= len(data):
        return None
    scripts = []
    for i in range(count):
        o = 0x2B + i * 3
        if o + 3 > len(data):
            return None
        addr = read_u24(data, o)
        if i == 0 and addr != 0:
            return None
        if i < count - 1:
            nxt = read_u24(data, o + 3)
            length = nxt - addr
        else:
            length = footer - (addr + base)
        if length < 0 or length >= len(data):
            return None
        scripts.append(data[base + addr: base + addr + length])
    return scripts
