#!/usr/bin/env python3
"""패치된 PROT.DAT + EXE 를 US 디스크 이미지에 기록 (EDC/ECC 재계산)"""
import struct, shutil, sys
sys.path.insert(0, "/home/claude/legaia/tools")
from write_bin import fix_sector          # 검증된 EDC/ECC 재계산기

SRC = "/mnt/user-data/uploads/Legend_of_Legaia__USA_.bin"
OUT = "/home/claude/legaia/build/Legaia_US_KR3.bin"
PROT_NEW = "/home/claude/legaia/build/PROT_US_KR3.DAT"
EXE_NEW = "/home/claude/legaia/build/SCUS_KR3.exe"

RAW = 2352
UOFF = 24
USER = 2048
PROT_LBA = 242
EXE_LBA = 24


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
    print(f"  {label}: {n} 섹터 중 {changed}개 변경")


def main():
    print("디스크 이미지 복사 중...")
    shutil.copyfile(SRC, OUT)

    prot = open(PROT_NEW, "rb").read()
    exe = open(EXE_NEW, "rb").read()
    print(f"PROT.DAT {len(prot)} bytes / EXE {len(exe)} bytes")

    with open(OUT, "r+b") as f:
        write_file(f, EXE_LBA, exe, "SCUS_942.54 (EXE)")
        write_file(f, PROT_LBA, prot, "PROT.DAT")

    print(f"\n완료: {OUT}")


if __name__ == "__main__":
    main()
