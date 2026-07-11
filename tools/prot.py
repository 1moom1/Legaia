#!/usr/bin/env python3
"""PROT.DAT archive access for Legaia Densetsu.

Top-level layout:
  u32 [0]      = 0
  u32 [1]      = entry count (1235)
  u32 [2..]    = sector offsets (each *2048 = byte offset into PROT.DAT)
  file i spans sectors [offs[i], offs[i+1]); last file to archive end.

PROT.DAT itself lives at disc LBA 246; we read its user bytes via disc.py.
"""
import sys, struct
sys.path.insert(0, "/home/claude/legaia/tools")
from disc import BIN, read_user_range

PROT_LBA = 246
PROT_SIZE = 121815040
ARCH_SECTORS = PROT_SIZE // 2048  # 59480


def load_index(f):
    head = read_user_range(f, PROT_LBA, 2048 * 8)
    count = struct.unpack("<I", head[4:8])[0]
    offs = [struct.unpack("<I", head[8 + 4 * i:12 + 4 * i])[0] for i in range(count)]
    return offs  # sector offsets


def file_span(offs, i):
    """Return (start_sector, end_sector) for archive file i."""
    start = offs[i]
    end = offs[i + 1] if i + 1 < len(offs) else ARCH_SECTORS
    return start, end


def read_file(f, offs, i):
    start, end = file_span(offs, i)
    nbytes = (end - start) * 2048
    # read from PROT.DAT: sector `start` within archive => disc lba = PROT_LBA + start
    return read_user_range(f, PROT_LBA + start, nbytes)


if __name__ == "__main__":
    with open(BIN, "rb") as f:
        offs = load_index(f)
        print(f"entries: {len(offs)}")
        # monotonic check
        mono = all(offs[i] <= offs[i + 1] for i in range(len(offs) - 1))
        print(f"monotonic: {mono}")
        # size histogram of first 40 files
        for i in range(40):
            s, e = file_span(offs, i)
            print(f"file {i:4d}: sectors {s:6d}..{e:6d}  ({(e-s)*2048:9d} bytes)")
