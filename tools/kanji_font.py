#!/usr/bin/env python3
"""한자 폰트 TIM 교체 — file876 (PACK) / file894 (raw).

게임은 **일본어 한자 폰트 TIM** 을 VRAM(320,256) 에 올린다.
미국판은 영어를 쓰므로 화면에 쓰지 않지만, 데이터도 로딩도 그대로 살아 있다.
(중국판 패치가 바로 이 자리를 한자로 덮었다)

같은 VRAM 좌표에 폰트를 올리는 파일이 **둘** 있다:

    file894    raw   → PROT 0x05006A2C 에 픽셀이 그대로 (직접 수정)
    file876:3  PACK  → LZSS 압축 안에 있다 (재압축 필요)  ★ 이게 실제로 로드된다

file894 만 바꿨더니 게임에 **일본어 한자가 그대로 나왔다** — file876 이 로드되기
때문이다. 어느 쪽이 언제 로드되는지 확실치 않으므로 **둘 다** 한글로 채운다.

file876 슬롯 여유:
    슬롯      104,448B
    재압축     103,128B  (내용 그대로)
    여유         1,320B
한글 폰트는 일본어 한자보다 획이 단순해 압축이 더 잘 되므로 여유는 늘어난다.
"""
import struct
import sys

import numpy as np

HERE = "/home/claude/legaia/tools"
sys.path.insert(0, HERE)

from legaia import prot_files, pack_parse, lzss_compress_opt   # noqa: E402

# 일본어 한자 폰트는 **두 페이지**다 (일본판이 882자를 쓴다는 뜻)
KANJI_VRAMS = [(320, 256, 64, 256), (384, 256, 64, 256)]

# 한자 TIM 의 원본 잉크색은 0x6739 = RGB(205,205,205) 로 **약간 어둡다**.
# 기존 한글(p1/p2/p4)은 0x7FFF = RGB(255,255,255) 순백이라 색이 달라 보인다.
INK = 3                   # 우리 글리프가 쓰는 팔레트 인덱스 (translate.py 와 동일)
INK_WHITE = 0x7FFF


def find_kanji_tims(blob):
    """한자 폰트 TIM 들을 찾는다 → [(vram, clutoff, pixoff, pixlen), ...]."""
    out = []
    i = 0
    while True:
        k = blob.find(struct.pack("<I", 0x10), i)
        if k < 0 or k > len(blob) - 32:
            return out
        flag = struct.unpack("<I", blob[k + 4:k + 8])[0]
        if flag in (0x08, 0x09):
            pos = k + 8
            clen = struct.unpack("<I", blob[pos:pos + 4])[0]
            if 12 <= clen <= 0x10000:
                pos2 = pos + clen
                if pos2 + 12 <= len(blob):
                    ilen = struct.unpack("<I", blob[pos2:pos2 + 4])[0]
                    xx = struct.unpack("<HHHH", blob[pos2 + 4:pos2 + 12])
                    if xx in KANJI_VRAMS:
                        out.append((xx, pos + 12, pos2 + 12, ilen - 12))
        i = k + 1


def pack_px(P):
    """4bpp 픽셀 배열 → 바이트."""
    f = P.reshape(-1)
    return bytes((f[0::2] & 0xF) | ((f[1::2] & 0xF) << 4))


def _apply(d, pages):
    """blob 안의 한자 TIM 들을 한글로 교체한다.

    🔴 CLUT 은 건드리지 않는다.
       한자 TIM 픽셀은 값0(투명)/값3(잉크)만 쓰고, hook7 이 한자 페이지에도
       원본 텍스트 CLUT(0x7F86+page, 값3=순백)을 지정한다. 그래서 팔레트를
       바꿀 필요가 없다. 오히려 한자 전용 CLUT(0,475)을 순백으로 바꾸면
       그 CLUT 을 함께 쓰는 기호 렌더링이 깨지고, 그 CLUT 을 물려받는
       뒤따르는 영어까지 초록으로 물든다 (둘 다 실제로 겪었다).
    """
    hit = 0
    for vram, clutoff, pixoff, pixlen in find_kanji_tims(bytes(d)):
        px = pages.get(vram)
        if px is None:
            continue
        newpx = pack_px(px)
        assert len(newpx) == pixlen, f"픽셀 크기 불일치 {len(newpx)} != {pixlen}"
        d[pixoff:pixoff + pixlen] = newpx
        hit += 1
    return hit


def rebuild_pack_with_font(raw, pages):
    """PACK 을 풀어 한자 TIM 을 한글로 바꾸고 다시 압축한다.

    PACK 형식:
        u32 fileCount
        u32 totalDecLength
        per entry: u24 decLength, u8 fileId, u32 compAddress
        ... 각 파일의 LZSS 데이터
    """
    ents = pack_parse(raw)
    if not ents:
        return None, 0

    blobs = []
    hit = 0
    for fid, dec in ents:
        d = bytearray(dec)
        hit += _apply(d, pages)
        blobs.append((fid, bytes(d)))

    comp = [lzss_compress_opt(b) for _, b in blobs]
    n = len(blobs)
    first = 8 + n * 8

    out = bytearray()
    out += struct.pack("<I", n)
    out += struct.pack("<I", sum(len(b) for _, b in blobs))
    addr = first
    for (fid, b), c in zip(blobs, comp):
        out += struct.pack("<I", (len(b) & 0xFFFFFF) | (fid << 24))
        out += struct.pack("<I", addr)
        addr += len(c)
    for c in comp:
        out += c
    return bytes(out), hit


def patch(prot, mapping, hook7, glyph, GW=12, GH=12):
    """PROT 에 한글 한자-TIM 폰트를 심는다. prot 는 bytearray.

    한자 폰트 TIM 은 **두 페이지**이고, 그걸 올리는 파일도 **둘**이다:
        file894    raw   (직접 수정)
        file876:3  PACK  (재압축)   ★ 실제로 로드되는 쪽
    어느 쪽이 언제 로드되는지 확실치 않으므로 넷 다 채운다.

    반환: (file876 새 크기, 슬롯 크기, 교체한 TIM 수)
    """
    # VRAM 좌표별 한글 폰트 이미지
    pages = {}
    for vram in KANJI_VRAMS:
        pages[vram] = np.zeros((256, 256), np.uint8)
    for ch, (p, i) in mapping.items():
        buf = hook7.BUF[p]
        if not buf.startswith("kanji"):
            continue
        vram = hook7.KANJI_VRAM[buf]
        U, V = hook7.uv(i, p)
        pages[vram][V:V + GH, U:U + GW] = glyph(ch)

    files = {f[0]: (f[1], f[2]) for f in prot_files(bytes(prot))}
    total_hit = 0

    # --- file894 (raw) ---
    s, e = files[894]
    d = bytearray(prot[s:e])
    total_hit += _apply(d, pages)
    prot[s:e] = d

    # --- file876 (PACK) ---
    s, e = files[876]
    slot = e - s
    newpack, hit = rebuild_pack_with_font(bytes(prot[s:e]), pages)
    if newpack is None:
        raise RuntimeError("file876 PACK 재구성 실패")
    if len(newpack) > slot:
        raise RuntimeError(f"file876 슬롯 초과: {len(newpack):,} > {slot:,}")
    prot[s:s + len(newpack)] = newpack
    prot[s + len(newpack):e] = b"\x00" * (slot - len(newpack))
    total_hit += hit

    if total_hit != 4:
        raise RuntimeError(f"한자 TIM 을 4개 바꿔야 하는데 {total_hit}개만 바꿨다")
    return len(newpack), slot, total_hit
