#!/usr/bin/env python3
"""US판 한글 PoC v2 — 글자 겹침 수정

v1 문제: 한글에 배정한 코드(0x21~)의 '폭'이 원래 기호 폭(3~9px)이라 글자가 겹침.
         렌더러: X += 폭테이블[code] + 1     (폭테이블 = RAM 0x80073F1C = EXE 0x6471C)

v2 해결:
  1) 한글은 폰트에 글리프가 없고 대사에서도 안 쓰는 '안전 코드'에 배정
  2) 그 코드들의 폭을 13px 로 패치 (EXE 0x6471C + code)
     -> 영어에서 쓰는 기호(!,',...)의 폭은 건드리지 않음
  3) EXE 도 디스크에 기록 (LBA 24~, 폭 테이블은 LBA 224)

출력: build/PROT_US_KR2.DAT + build/SCUS_KR.exe
"""
import sys, struct, json, subprocess, tempfile, os
import numpy as np

sys.path.insert(0, "/home/claude/legaia/tools")
from legaia import prot_files, pack_parse, script_parse, read_u24
from hangul import render_glyph_pixel

PROT_US = "/home/claude/legaia/cn/PROT_US.DAT"
EXE_US = "/home/claude/legaia/cn/SCUS_US.exe"
OUT_PROT = "/home/claude/legaia/build/PROT_US_KR2.DAT"
OUT_EXE = "/home/claude/legaia/build/SCUS_KR.exe"
LZSS_BIN = "/home/claude/legaia/tools/lzss_fast"

FONT_PIX = 0x7F80
FONT_W = FONT_H = 256
CLUT_OFF = 0x1122C
WIDTH_TBL = 0x6471C          # EXE 파일 오프셋. width = tbl[code]
GLYPH_W, GLYPH_H = 14, 15
HANGUL_WIDTH = 13            # 한글 글자 폭(자간 +1 은 렌더러가 더함)

MAP_FILE, SCRIPT_ID, SCRIPT_IDX = 6, 3, 47
KR_PAL_ARG = 0x01
KR_PAL_ROW = 7

CONTROL = {0x00, 0x7C, 0xCE, 0xCF, 0x20}


def lzss_compress(data: bytes) -> bytes:
    with tempfile.NamedTemporaryFile(delete=False) as f:
        f.write(data); src = f.name
    dst = src + ".lz"
    subprocess.run([LZSS_BIN, "c", src, dst], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = open(dst, "rb").read()
    os.unlink(src); os.unlink(dst)
    return out


def uv(code):
    return (code & 0x0F) * 16, (code & 0xF0) - 0x20


def font_px(prot):
    a = np.frombuffer(prot[FONT_PIX:FONT_PIX + FONT_W * FONT_H // 2], dtype=np.uint8)
    lo = a & 0xF; hi = (a >> 4) & 0xF
    p = np.empty(a.size * 2, np.uint8)
    p[0::2] = lo; p[1::2] = hi
    return p.reshape(FONT_H, FONT_W)


def pack_px(px):
    f = px.reshape(-1)
    return ((f[0::2] & 0xF) | ((f[1::2] & 0xF) << 4)).astype(np.uint8).tobytes()


def scan_used_codes(prot):
    """대사 텍스트런에서 실제로 등장하는 코드 (2바이트 제어코드 소비 반영)"""
    count = struct.unpack("<I", prot[4:8])[0]
    offs = [struct.unpack("<I", prot[8 + 4 * i:12 + 4 * i])[0] for i in range(count)]
    used = set()
    for mid in range(count - 1):
        s, e = offs[mid] * 2048, offs[mid + 1] * 2048
        if e - s < 16:
            continue
        ents = pack_parse(prot[s:e])
        if not ents:
            continue
        sb = [d for f, d in ents if f == SCRIPT_ID]
        if not sb:
            continue
        ss = script_parse(sb[0])
        if not ss:
            continue
        for sc in ss:
            i = 0
            while i < len(sc):
                if sc[i] == 0x1F:
                    j = sc.find(b"\x00", i + 1)
                    if j < 0:
                        break
                    run = sc[i + 1:j]
                    k = 0
                    while k < len(run):
                        b = run[k]
                        if b in (0xCE, 0xCF):
                            k += 2
                            continue
                        used.add(b)
                        k += 1
                    i = j + 1
                else:
                    i += 1
    return used


def main():
    prot = bytearray(open(PROT_US, "rb").read())
    exe = bytearray(open(EXE_US, "rb").read())

    px = font_px(bytes(prot))

    # ---- 안전 코드 선별: 폰트에 글리프(값14/15) 없고, 대사에서 미사용 ----
    print("[1] 코드 공간 분석 중...")
    used = scan_used_codes(bytes(prot))

    def has_glyph(c):
        u, v = uv(c)
        if v < 0 or v + GLYPH_H > FONT_H:
            return True
        return bool(np.isin(px[v:v + GLYPH_H, u:u + GLYPH_W], [14, 15]).any())

    safe = [c for c in range(0x21, 0x100)
            if c not in CONTROL and c not in used and not has_glyph(c)]
    print(f"    안전 코드(글리프 없음 + 대사 미사용): {len(safe)}개")
    print(f"    {' '.join(f'{c:02X}' for c in safe)}")

    # ---- 번역 ----
    KO1 = "기억의 상"
    KO2 = "기억의 상"
    ORIG1 = b"The Memory Statue will"
    ORIG2 = b"remember things for you..."

    hangul = []
    for ch in KO1 + KO2:
        if 0xAC00 <= ord(ch) <= 0xD7A3 and ch not in hangul:
            hangul.append(ch)
    print(f"\n[2] 필요 음절 {len(hangul)}자: {''.join(hangul)}")

    if len(hangul) > len(safe):
        print(f"!! 안전 코드 부족 ({len(safe)} < {len(hangul)})")
        sys.exit(1)

    mapping = {ch: safe[i] for i, ch in enumerate(hangul)}
    print("\n[3] 코드 배정:")
    for ch, c in mapping.items():
        u, v = uv(c)
        print(f"    {ch} -> 0x{c:02X}  U={u:3d} V={v:3d}  (원래 폭 {exe[WIDTH_TBL + c]}px)")

    # ---- 폰트에 한글 그리기 (값 1/2) ----
    for ch, code in mapping.items():
        u, v = uv(code)
        g = render_glyph_pixel(ch, 12)
        ox, oy = (GLYPH_W - 12) // 2, (GLYPH_H - 12) // 2
        for yy in range(12):
            for xx in range(12):
                val = g[yy, xx]
                if val == 0:
                    continue
                px[v + oy + yy, u + ox + xx] = 1 if val == 3 else 2
    prot[FONT_PIX:FONT_PIX + FONT_W * FONT_H // 2] = pack_px(px)
    print(f"\n[4] 폰트에 한글 삽입 (값1/2, ASCII의 값14/15는 무손상)")

    # ---- 폭 테이블 패치 (한글 코드만!) ----
    for ch, code in mapping.items():
        exe[WIDTH_TBL + code] = HANGUL_WIDTH
    print(f"[5] 폭 테이블 패치: 한글 코드 {len(mapping)}개 -> {HANGUL_WIDTH}px")
    print(f"    (영어가 쓰는 코드의 폭은 그대로)")

    # ---- 팔레트 ----
    def rgb15(r, g, b):
        return ((b // 8) << 10) | ((g // 8) << 5) | (r // 8)

    kp = [0] * 16
    kp[1] = rgb15(248, 248, 248)
    kp[2] = rgb15(160, 160, 160)
    kp[3] = rgb15(96, 96, 96)
    base = CLUT_OFF + KR_PAL_ROW * 32
    for i in range(16):
        struct.pack_into("<H", prot, base + i * 2, kp[i])
    # 사용중 팔레트에서 값1/2/3 투명화 (한글이 영어 대사에 안 나오게)
    for row in (6, 10, 11, 12, 13, 15):
        b = CLUT_OFF + row * 32
        for i in (1, 2, 3):
            struct.pack_into("<H", prot, b + i * 2, 0x0000)
    print(f"[6] 팔레트: row{KR_PAL_ROW}=한글전용(0xCF {KR_PAL_ARG:02X}), 기존 팔레트 값1/2/3 투명화")

    # ---- 텍스트 교체 ----
    files = list(prot_files(bytes(prot)))
    _, ms, me = next(f for f in files if f[0] == MAP_FILE)
    ents = pack_parse(bytes(prot[ms:me]))
    sblob = [d for f, d in ents if f == SCRIPT_ID][0]
    scripts = script_parse(sblob)
    sc = bytearray(scripts[SCRIPT_IDX])

    def enc(s):
        o = bytearray([0xCF, KR_PAL_ARG])
        for ch in s:
            o.append(mapping[ch] if ch in mapping else 0x20)
        o += bytes([0xCF, 0x00])
        return bytes(o)

    for orig, ko in ((ORIG1, KO1), (ORIG2, KO2)):
        pos = sc.find(orig)
        assert pos >= 0, f"원문 못찾음 {orig}"
        e = enc(ko)
        assert len(e) <= len(orig), f"길이초과 {len(e)}>{len(orig)}"
        sc[pos:pos + len(orig)] = e + b"\x20" * (len(orig) - len(e))
        print(f"\n[7] '{orig.decode()}' -> '{ko}'")
    scripts[SCRIPT_IDX] = bytes(sc)

    # ---- 스크립트/PACK 재구성 ----
    hdr = sblob[:0x22]
    c0, c1, c2 = struct.unpack("<HHH", sblob[0x22:0x28])
    cnt = c0 + c1 + c2
    sbase = 0x2B + cnt * 3
    footer = sblob[read_u24(sblob, 0x28) + sbase:]

    def wu24(b, v):
        b += bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])

    offs = []; cur = 0
    for s_ in scripts:
        offs.append(cur); cur += len(s_)
    nb = bytearray(hdr) + struct.pack("<HHH", c0, c1, c2)
    wu24(nb, cur)
    for o in offs:
        wu24(nb, o)
    for s_ in scripts:
        nb += s_
    nb += footer
    newscript = bytes(nb)
    assert script_parse(newscript)

    newents = [(f, (newscript if f == SCRIPT_ID else d)) for f, d in ents]
    n = len(newents)
    tbl = bytearray(); body = bytearray()
    addr = 8 + n * 8; tot = 0
    for fid, dec in newents:
        comp = lzss_compress(dec)
        wu24(tbl, len(dec)); tbl.append(fid)
        tbl += struct.pack("<I", addr)
        body += comp; addr += len(comp); tot += len(dec)
    newpack = struct.pack("<II", n, tot) + bytes(tbl) + bytes(body)
    slot = me - ms
    print(f"\n[8] PACK: {slot} -> {len(newpack)}")
    assert len(newpack) <= slot, "슬롯 초과"
    prot[ms:me] = newpack + b"\x00" * (slot - len(newpack))

    open(OUT_PROT, "wb").write(bytes(prot))
    open(OUT_EXE, "wb").write(bytes(exe))
    json.dump({ch: f"{c:02X}" for ch, c in mapping.items()},
              open("/home/claude/legaia/build/kr_map2.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"\n[9] 저장: {OUT_PROT}")
    print(f"          {OUT_EXE}")


if __name__ == "__main__":
    main()
