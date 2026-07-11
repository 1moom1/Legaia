#!/usr/bin/env python3
"""Write a modified PROT.DAT back into the Mode2/2352 raw BIN.

PROT.DAT lives at LBA 246. Each raw sector is 2352 bytes:
  [0:12]    sync
  [12:16]   header (min, sec, frame, mode)
  [16:24]   subheader (2 x 4 bytes)
  [24:2072] 2048 user bytes   <-- we replace these
  [2072:2076] EDC             <-- must be recomputed
  [2076:2352] ECC (Mode 2 Form 1: P/Q parity)  <-- must be recomputed

Mode 2 Form 1 EDC covers bytes [16:2072] (subheader + user data).
ECC P/Q are computed over [12:2076] with the header zeroed for Form 1.
"""
import struct

SRC_BIN = "/mnt/user-data/uploads/Legaia_Densetsu__Japan_.bin"
OUT_BIN = "/home/claude/legaia/build/Legaia_KR_v2.bin"
PROT_NEW = "/home/claude/legaia/build/PROT_v2.DAT"
PROT_LBA = 246
RAW = 2352

# ---------- EDC (CRC-32, poly 0x8001801B reflected) ----------
_edc_table = []
for i in range(256):
    edc = i
    for _ in range(8):
        edc = (edc >> 1) ^ (0xD8018001 if edc & 1 else 0)
    _edc_table.append(edc)


def edc_compute(data: bytes) -> int:
    edc = 0
    for b in data:
        edc = (edc >> 8) ^ _edc_table[(edc ^ b) & 0xFF]
    return edc & 0xFFFFFFFF


# ---------- ECC (Reed-Solomon P/Q parity) ----------
ecc_f_lut = [0] * 256
ecc_b_lut = [0] * 256
j = 0
for i in range(256):
    ecc_f_lut[i] = (i << 1) ^ (0x11D if i & 0x80 else 0)
    ecc_f_lut[i] &= 0xFF
for i in range(256):
    ecc_b_lut[i] = 0
# build log/antilog properly
_exp = [0] * 256
_log = [0] * 256
x = 1
for i in range(255):
    _exp[i] = x
    _log[x] = i
    x <<= 1
    if x & 0x100:
        x ^= 0x11D
_exp[255] = _exp[0]


def _gmul(a, b):
    if a == 0 or b == 0:
        return 0
    return _exp[(_log[a] + _log[b]) % 255]


def _ecc_block(sector: bytearray, major_count, minor_count, major_mult, minor_inc, dest):
    size = major_count * minor_count
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for minor in range(minor_count):
            temp = sector[12 + index]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = ecc_f_lut[ecc_a]
        ecc_a = ecc_a ^ ecc_b
        # divide by x+1 in GF: use b_lut equivalent
        # standard: ecc_a = ecc_b_lut[ecc_f_lut[ecc_a] ^ ecc_b]
        sector[dest + major] = ecc_a & 0xFF
        sector[dest + major + major_count] = (ecc_a ^ ecc_b) & 0xFF


def build_ecc_luts():
    global ecc_f_lut, ecc_b_lut
    ecc_f_lut = [0] * 256
    ecc_b_lut = [0] * 256
    for i in range(256):
        j = (i << 1) ^ (0x11D if (i & 0x80) else 0)
        ecc_f_lut[i] = j & 0xFF
        ecc_b_lut[i ^ (j & 0xFF)] = i


build_ecc_luts()


def ecc_compute_block(sector: bytearray, major_count, minor_count, major_mult, minor_inc, dest):
    size = major_count * minor_count
    for major in range(major_count):
        index = (major >> 1) * major_mult + (major & 1)
        ecc_a = 0
        ecc_b = 0
        for minor in range(minor_count):
            temp = sector[12 + index]
            index += minor_inc
            if index >= size:
                index -= size
            ecc_a ^= temp
            ecc_b ^= temp
            ecc_a = ecc_f_lut[ecc_a]
        ecc_a = ecc_b_lut[ecc_f_lut[ecc_a] ^ ecc_b]
        sector[12 + size + major] = ecc_a & 0xFF
        sector[12 + size + major + major_count] = (ecc_a ^ ecc_b) & 0xFF


def fix_sector(raw: bytearray):
    """Recompute EDC + ECC for a Mode 2 Form 1 sector (2352 bytes)."""
    # EDC over subheader + user data = bytes [16:2072]
    edc = edc_compute(bytes(raw[16:2072]))
    raw[2072:2076] = struct.pack("<I", edc)

    # ECC: header bytes [12:16] must be zero during computation for Form 1
    saved = bytes(raw[12:16])
    raw[12:16] = b"\x00\x00\x00\x00"
    ecc_compute_block(raw, 86, 24, 2, 86, 0)    # P parity
    ecc_compute_block(raw, 52, 43, 86, 88, 0)   # Q parity
    raw[12:16] = saved
    return raw


def main():
    prot = open(PROT_NEW, "rb").read()
    nsec = (len(prot) + 2047) // 2048
    print(f"new PROT.DAT: {len(prot)} bytes = {nsec} sectors")

    print("copying disc image...")
    import shutil
    shutil.copyfile(SRC_BIN, OUT_BIN)

    with open(OUT_BIN, "r+b") as f:
        for i in range(nsec):
            lba = PROT_LBA + i
            f.seek(lba * RAW)
            raw = bytearray(f.read(RAW))
            chunk = prot[i * 2048:(i + 1) * 2048]
            if len(chunk) < 2048:
                chunk = chunk + b"\x00" * (2048 - len(chunk))
            if bytes(raw[24:2072]) == chunk:
                continue  # unchanged, skip
            raw[24:2072] = chunk
            fix_sector(raw)
            f.seek(lba * RAW)
            f.write(bytes(raw))
            if i % 5000 == 0:
                print(f"  sector {i}/{nsec}")
    print(f"wrote {OUT_BIN}")


if __name__ == "__main__":
    main()
