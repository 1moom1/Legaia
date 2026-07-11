#!/usr/bin/env python3
"""Core disc access for Legaia Densetsu (Mode2/2352 raw BIN, ISO9660).

Sector layout (Mode 2 Form 1):
  [0:12]   sync
  [12:16]  header (min,sec,frame,mode)
  [16:24]  subheader (2x4 bytes)
  [24:2072] 2048 bytes user data
  [2072:2352] EDC/ECC
"""
import struct

BIN = "/mnt/user-data/uploads/Legaia_Densetsu__Japan_.bin"
RAW = 2352
USER = 2048
UOFF = 24  # user-data offset within a raw sector


def read_user_sector(f, lba):
    f.seek(lba * RAW + UOFF)
    return f.read(USER)


def read_user_range(f, lba, nbytes):
    """Read nbytes of *user* data starting at sector lba (crosses sectors)."""
    out = bytearray()
    while len(out) < nbytes:
        out += read_user_sector(f, lba)
        lba += 1
    return bytes(out[:nbytes])


def read_raw_sector(f, lba):
    f.seek(lba * RAW)
    return f.read(RAW)


# ---------------- ISO 9660 ----------------

class Entry:
    def __init__(self, name, lba, size, is_dir):
        self.name = name
        self.lba = lba
        self.size = size
        self.is_dir = is_dir

    def __repr__(self):
        t = "DIR " if self.is_dir else "FILE"
        return f"<{t} {self.name} lba={self.lba} size={self.size}>"


def parse_dir(f, lba, size):
    """Parse an ISO9660 directory extent -> list of Entry."""
    data = read_user_range(f, lba, size)
    entries = []
    i = 0
    while i < len(data):
        rec_len = data[i]
        if rec_len == 0:
            # advance to next logical sector boundary
            i = (i // USER + 1) * USER
            continue
        rec = data[i:i + rec_len]
        ext_lba = struct.unpack("<I", rec[2:6])[0]
        ext_size = struct.unpack("<I", rec[10:14])[0]
        flags = rec[25]
        name_len = rec[32]
        name = rec[33:33 + name_len]
        is_dir = bool(flags & 0x02)
        if name == b"\x00":
            nm = "."
        elif name == b"\x01":
            nm = ".."
        else:
            nm = name.split(b";")[0].decode("ascii", "replace")
        if nm not in (".", ".."):
            entries.append(Entry(nm, ext_lba, ext_size, is_dir))
        i += rec_len
    return entries


def read_pvd_root(f):
    pvd = read_user_sector(f, 16)
    assert pvd[1:6] == b"CD001", "not ISO9660"
    root_rec = pvd[156:156 + 34]
    root_lba = struct.unpack("<I", root_rec[2:6])[0]
    root_size = struct.unpack("<I", root_rec[10:14])[0]
    return root_lba, root_size


def walk(f, lba=None, size=None, path="/", depth=0, out=None):
    if out is None:
        out = []
    if lba is None:
        lba, size = read_pvd_root(f)
    for e in parse_dir(f, lba, size):
        full = path + e.name
        out.append((full, e))
        if e.is_dir and depth < 8:
            walk(f, e.lba, e.size, full + "/", depth + 1, out)
    return out


if __name__ == "__main__":
    with open(BIN, "rb") as f:
        for full, e in walk(f):
            t = "D" if e.is_dir else " "
            print(f"{t} {full:40s} lba={e.lba:8d} size={e.size:10d}")
