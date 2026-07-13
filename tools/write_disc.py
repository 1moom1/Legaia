#!/usr/bin/env python3
"""빌드된 PROT.DAT / EXE 를 디스크 이미지(.bin)에 써 넣는다.

사용:
    python3 tools/write_disc.py 원본.bin PROT.DAT SCUS_942.54 출력.bin

Mode2/2352 섹터의 USER 영역[24:2072]에 쓰고 EDC/ECC 를 재계산한다.
"""
import os
import sys
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from write_bin import fix_sector   # noqa: E402

RAW, UOFF, USER = 2352, 24, 2048
LBA_EXE = 24
LBA_PROT = 242


def write_file(f, lba, data, label):
    n = (len(data) + USER - 1) // USER
    changed = 0
    for i in range(n):
        chunk = data[i * USER:(i + 1) * USER]
        if len(chunk) < USER:
            chunk = chunk + b"\x00" * (USER - len(chunk))
        f.seek((lba + i) * RAW)
        raw = bytearray(f.read(RAW))
        if bytes(raw[UOFF:UOFF + USER]) == chunk:
            continue
        raw[UOFF:UOFF + USER] = chunk
        fix_sector(raw)
        f.seek((lba + i) * RAW)
        f.write(bytes(raw))
        changed += 1
    print(f"  {label}: {changed}/{n} 섹터 갱신")


def main():
    if len(sys.argv) != 5:
        print(__doc__)
        sys.exit(1)
    src, prot, exe, out = sys.argv[1:5]
    for p in (src, prot, exe):
        if not os.path.exists(p):
            print(f"없음: {p}")
            sys.exit(1)

    size = os.path.getsize(src)
    print(f"원본 복사 ({size:,}B)…")
    shutil.copyfile(src, out)

    with open(out, "r+b") as f:
        write_file(f, LBA_EXE, open(exe, "rb").read(), "EXE ")
        write_file(f, LBA_PROT, open(prot, "rb").read(), "PROT")

    ok = os.path.getsize(out) == size
    print(f"\n완료: {out}")
    print(f"크기 {'OK' if ok else '★불일치!'}")

    cue = os.path.splitext(out)[0] + ".cue"
    with open(cue, "w") as c:
        c.write(f'FILE "{os.path.basename(out)}" BINARY\n')
        c.write("  TRACK 01 MODE2/2352\n")
        c.write("    INDEX 01 00:00:00\n")
    print(f"      {cue}")


if __name__ == "__main__":
    main()
