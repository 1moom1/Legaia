#!/usr/bin/env python3
"""디스크 이미지에서 PROT.DAT 와 EXE 를 뽑아낸다.

사용:
    python3 tools/extract.py "Legend of Legaia (USA).bin" out/
"""
import os
import sys

RAW, UOFF, USER = 2352, 24, 2048
FILES = [
    ("SCUS_942.54", 24, 442368),
    ("PROT.DAT", 242, 121253888),
]


def read_file(f, lba, size):
    out = bytearray()
    n = (size + USER - 1) // USER
    for i in range(n):
        f.seek((lba + i) * RAW + UOFF)
        out += f.read(USER)
    return bytes(out[:size])


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    src, outdir = sys.argv[1], sys.argv[2]
    if not os.path.exists(src):
        print(f"없음: {src}")
        sys.exit(1)
    size = os.path.getsize(src)
    if size != 466714416:
        print(f"⚠ 크기가 다릅니다 ({size:,}B). USA 판이 맞는지 확인하세요.")
        print("  기대: 466,714,416B")
    os.makedirs(outdir, exist_ok=True)
    with open(src, "rb") as f:
        for name, lba, sz in FILES:
            data = read_file(f, lba, sz)
            p = os.path.join(outdir, name)
            open(p, "wb").write(data)
            print(f"  {name}: {len(data):,}B -> {p}")
    print("\n완료. 이제 번역 툴을 실행하세요:")
    print("  python3 tools/translator_gui.py")


if __name__ == "__main__":
    main()
