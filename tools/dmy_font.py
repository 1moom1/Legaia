#!/usr/bin/env python3
"""DMY.DAT 영역에 맵별 폰트를 써 넣는다.

DMY.DAT (LBA 180228, 36MB) 는 게임이 읽지 않는 더미 파일이다.
여기에 맵별 폰트를 배치하고, CD 로딩 훅이 맵 로드 시 읽어 온다.

레이아웃:
    DMY 섹터 0            : 매직 + 맵 개수 + (맵번호, 음절수) 목록
    DMY 섹터 FONT_BASE~   : 맵별 폰트 (각 SECS 섹터, raw 4bpp)

폰트 포맷 (맵당):
    660자 x 72B(12x12 4bpp) = 47,520B = 24섹터
    글리프는 20열 배치와 무관하게 '음절 순서대로' 저장.
    VRAM 배치는 로딩 훅이 함.
"""
import os
import sys
import json
import struct
import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

SEC = 2048
DMY_LBA = 180228
RAW = 2352
UOFF = 24
USER = 2048

FONT_SYL = 660
GLYPH = 72                       # 12x12 4bpp
MAP_BYTES = FONT_SYL * GLYPH     # 47,520
MAP_SECS = (MAP_BYTES + SEC - 1) // SEC   # 24
FONT_BASE = 1                    # 섹터 0 은 인덱스


def render_font(syllables):
    """음절 리스트 -> raw 4bpp 폰트 (FONT_SYL x 72B)."""
    from PIL import Image, ImageFont, ImageDraw
    ft = ImageFont.truetype(os.path.join(HERE, "Galmuri11.ttf"), 12)
    out = bytearray(MAP_BYTES)
    for i, ch in enumerate(syllables[:FONT_SYL]):
        img = Image.new("L", (16, 16), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=ft, fill=255)
        a = np.asarray(img)
        ys, xs = np.nonzero(a)
        cell = np.zeros((12, 12), np.uint8)
        if len(ys):
            y0, x0 = ys.min(), xs.min()
            for y in range(12):
                for x in range(12):
                    if y + y0 < 16 and x + x0 < 16 and a[y + y0, x + x0] > 128:
                        cell[y, x] = 3
        # 4bpp 패킹 (2px/byte)
        flat = cell.reshape(-1)
        packed = bytes((flat[0::2] & 0xF) | ((flat[1::2] & 0xF) << 4))
        out[i * GLYPH:(i + 1) * GLYPH] = packed
    return bytes(out)


def build_dmy_payload(font_map):
    """font_map: {map_id: [음절,...]}  ->  (payload_bytes, index)."""
    maps = sorted(font_map)
    # 인덱스 섹터
    idx = bytearray(SEC)
    struct.pack_into("<4sHH", idx, 0, b"KFNT", len(maps), MAP_SECS)
    for i, m in enumerate(maps):
        struct.pack_into("<HH", idx, 8 + i * 4, m, len(font_map[m]))
    payload = bytearray(idx)
    # 맵별 폰트
    for m in maps:
        payload += render_font(font_map[m])
        # 섹터 정렬
        while len(payload) % SEC:
            payload.append(0)
    return bytes(payload), {m: FONT_BASE + i * MAP_SECS for i, m in enumerate(maps)}


def write_to_disc(disc_path, payload):
    """DMY.DAT 영역(LBA 180228~)에 payload 를 써 넣는다."""
    sys.path.insert(0, HERE)
    from write_bin import fix_sector
    n = (len(payload) + USER - 1) // USER
    with open(disc_path, "r+b") as f:
        for i in range(n):
            chunk = payload[i * USER:(i + 1) * USER]
            if len(chunk) < USER:
                chunk = chunk + b"\x00" * (USER - len(chunk))
            lba = DMY_LBA + i
            f.seek(lba * RAW)
            raw = bytearray(f.read(RAW))
            raw[UOFF:UOFF + USER] = chunk
            fix_sector(raw)
            f.seek(lba * RAW)
            f.write(bytes(raw))
    return n


if __name__ == "__main__":
    # 테스트: map6 에 자주 쓰는 음절 660개
    import json
    from collections import Counter
    d = json.load(open(os.path.join(HERE, "..", "build", "text_dump.json")))
    ko = "".join(e["ko"] for e in d if e.get("ko"))
    freq = Counter(c for c in ko if 0xAC00 <= ord(c) <= 0xD7A3)
    syls = [c for c, _ in freq.most_common()]
    extra = "가나다라마바사아자차카타파하거너더러머버서어저처커터퍼허고노도로모보소오조초코토포호구누두루무부수우주추쿠투푸후"
    for c in extra:
        if c not in syls:
            syls.append(c)
    payload, offsets = build_dmy_payload({6: syls[:FONT_SYL]})
    print(f"payload {len(payload):,}B ({len(payload)//SEC}섹터)")
    print(f"맵6 폰트 LBA = {DMY_LBA + offsets[6]}")
    print(f"음절 오프셋: {offsets}")
