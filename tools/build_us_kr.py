#!/usr/bin/env python3
"""US판 기반 한글 PoC (A방식 — ASM 훅 없음)

핵심 원리 (전부 디버거/디스어셈블로 검증됨):
  대사 렌더러 = 0x80036888
    U = (code & 0x0F) * 16
    V = (code & 0xF0) - 0x20        # 글리프 14x15px
    CLUT = [0x8007B454] + 0x7F86    # 팔레트 = 변수
    0xCF <n>  ->  팔레트 변수 = n   (게임이 이미 쓰는 제어코드)
    폭 = [0x80073F1C + code]        # 가변폭 테이블

  US 폰트는 잉크로 니블 값 14,15 만 사용.
  값 1,2,3 은 224개 셀 전부에서 완전히 비어 있음 (검증됨).
  => 같은 셀에 한글을 값 1/2 로 겹쳐 그린다. ASCII와 충돌 없음.

  팔레트 row 7 (= 0xCF 01) 은 대사에서 한 번도 안 쓰임.
  이걸 "한글 팔레트"로 재정의: 값 1,2 는 흰색, 값 14,15 는 투명.
  => 0xCF 01 로 전환하면 한글만 보이고, 0xCF 00 으로 되돌리면 ASCII만 보인다.

출력: build/PROT_US_KR.DAT  (그리고 write_bin 으로 디스크에 기록)
"""
import sys, struct, json, subprocess, tempfile, os
import numpy as np

sys.path.insert(0, "/home/claude/legaia/tools")
from legaia import prot_files, pack_parse, script_parse, read_u24
from hangul import render_glyph_pixel

PROT_US = "/home/claude/legaia/cn/PROT_US.DAT"
OUT = "/home/claude/legaia/build/PROT_US_KR.DAT"
LZSS_BIN = "/home/claude/legaia/tools/lzss_fast"

FONT_PIX = 0x7F80          # 폰트 픽셀 데이터 (PROT 절대 오프셋)
FONT_W = 256
FONT_H = 256
CLUT_OFF = 0x1122C         # TIM 0x11218 의 CLUT 데이터 (16 팔레트 x 16색)
WIDTH_TBL = None           # 폭 테이블은 EXE 안 (0x80073F1C) — 아래에서 처리

GLYPH_W = 14
GLYPH_H = 15

MAP_FILE = 6
SCRIPT_ID = 3
SCRIPT_IDX = 47

KR_PALETTE_ARG = 0x01      # 0xCF 01 -> CLUT row 7
KR_PALETTE_ROW = 7

# 한글에 배정할 코드: 대사에서 안 쓰이는 코드를 우선 쓰되,
# 겹쳐넣기 방식이라 ASCII 코드도 재사용 가능(팔레트로 분리되므로).
# 안전하게 '문자로 렌더되는' 코드 중 제어코드가 아닌 것을 순서대로 사용.
CONTROL = {0x00, 0x7C, 0xCE, 0xCF}


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


def font_pixels(prot):
    a = np.frombuffer(prot[FONT_PIX:FONT_PIX + FONT_W * FONT_H // 2], dtype=np.uint8)
    lo = a & 0xF; hi = (a >> 4) & 0xF
    p = np.empty(a.size * 2, np.uint8)
    p[0::2] = lo; p[1::2] = hi
    return p.reshape(FONT_H, FONT_W)


def pack_pixels(px):
    flat = px.reshape(-1)
    packed = (flat[0::2] & 0xF) | ((flat[1::2] & 0xF) << 4)
    return packed.astype(np.uint8).tobytes()


def main():
    prot = bytearray(open(PROT_US, "rb").read())

    # ---------- 1) 한글 팔레트 만들기 (row 7) ----------
    # 값 1 = 흰색, 값 2 = 회색(안티에일리어싱), 값 14/15 = 투명(검정)
    def rgb15(r, g, b):
        return ((b // 8) << 10) | ((g // 8) << 5) | (r // 8)

    newpal = [0] * 16
    newpal[0] = 0x0000                 # 투명
    newpal[1] = rgb15(248, 248, 248)   # 한글 본체 (흰색)
    newpal[2] = rgb15(160, 160, 160)   # 한글 안티에일리어싱
    newpal[3] = rgb15(96, 96, 96)
    # 14,15 = ASCII 잉크 -> 이 팔레트에선 투명하게
    newpal[14] = 0x0000
    newpal[15] = 0x0000
    for i in (4, 5, 6, 7, 8, 9, 10, 11, 12, 13):
        newpal[i] = 0x0000

    base = CLUT_OFF + KR_PALETTE_ROW * 16 * 2
    for i in range(16):
        struct.pack_into("<H", prot, base + i * 2, newpal[i])
    print(f"[1] 한글 팔레트 작성: CLUT row {KR_PALETTE_ROW} (0xCF {KR_PALETTE_ARG:02X})")
    print(f"    값1=흰색, 값2=회색, 값14/15=투명")

    # 중요: 기본 대사 팔레트(row 6)와 다른 사용중 팔레트에서는
    # 값 1/2/3 을 투명(검정)으로 만들어야 한글이 안 보인다.
    # (원본 row6 은 값1=흰색이라, 그대로 두면 한글이 영어 대사에도 나타남)
    for row in (6, 10, 11, 12, 13, 15):     # 대사에서 실제 사용되는 팔레트들
        b = CLUT_OFF + row * 16 * 2
        for i in (1, 2, 3):
            struct.pack_into("<H", prot, b + i * 2, 0x0000)
    print(f"    사용중 팔레트(6,10,11,12,13,15)의 값1/2/3 -> 투명 처리")

    # ---------- 2) 번역할 대사 ----------
    # 검증된 대사 (map6 script47)
    ORIG = b"The Memory Statue will"
    ORIG2 = b"remember things for you..."
    KO1 = "기억의 상이"
    KO2 = "기억해 줍니다"

    text_all = KO1 + KO2
    hangul = []
    for ch in text_all:
        if 0xAC00 <= ord(ch) <= 0xD7A3 and ch not in hangul:
            hangul.append(ch)
    print(f"\n[2] 필요한 한글 음절: {len(hangul)}자 -> {''.join(hangul)}")

    # ---------- 3) 한글에 코드 배정 ----------
    codes = [c for c in range(0x20, 0x100) if c not in CONTROL]
    # 0x20(스페이스)은 렌더러가 특수처리하므로 제외
    codes = [c for c in codes if c != 0x20]
    if len(hangul) > len(codes):
        print("코드 부족"); sys.exit(1)
    mapping = {ch: codes[i] for i, ch in enumerate(hangul)}
    print("\n[3] 한글 -> 코드 배정:")
    for ch, c in mapping.items():
        u, v = uv(c)
        print(f"    {ch} -> 0x{c:02X}  (U={u}, V={v})")

    # ---------- 4) 폰트에 한글 그리기 (값 1/2 사용) ----------
    px = font_pixels(bytes(prot)).copy()
    for ch, code in mapping.items():
        u, v = uv(code)
        g = render_glyph_pixel(ch, 12)      # 12x12, 값 0/2/3
        # 12x12 글리프를 14x15 셀 안에 중앙 배치
        ox = (GLYPH_W - 12) // 2
        oy = (GLYPH_H - 12) // 2
        for yy in range(12):
            for xx in range(12):
                val = g[yy, xx]
                if val == 0:
                    continue
                # 3계조(2,3) -> 팔레트 값 1(본체) / 2(연한부분)
                newv = 1 if val == 3 else 2
                Y = v + oy + yy
                X = u + ox + xx
                if 0 <= Y < FONT_H and 0 <= X < FONT_W:
                    # 기존 ASCII 잉크(14/15)는 건드리지 않고 덮어씀
                    px[Y, X] = newv
    prot[FONT_PIX:FONT_PIX + FONT_W * FONT_H // 2] = pack_pixels(px)
    print(f"\n[4] 폰트에 한글 {len(mapping)}자 삽입 (값 1/2 사용, ASCII 값14/15는 보존)")

    # ---------- 5) 텍스트 교체 ----------
    files = list(prot_files(bytes(prot)))
    _, ms, me = next(f for f in files if f[0] == MAP_FILE)
    ents = pack_parse(bytes(prot[ms:me]))
    script_blob = [d for f, d in ents if f == SCRIPT_ID][0]
    scripts = script_parse(script_blob)
    sc = bytearray(scripts[SCRIPT_IDX])

    def encode_ko(s):
        out = bytearray()
        out += bytes([0xCF, KR_PALETTE_ARG])     # 한글 팔레트로 전환
        for ch in s:
            if ch in mapping:
                out.append(mapping[ch])
            elif ch == " ":
                out.append(0x20)
            else:
                out.append(0x20)
        out += bytes([0xCF, 0x00])               # 기본 팔레트 복귀
        return bytes(out)

    for orig, ko in ((ORIG, KO1), (ORIG2, KO2)):
        pos = sc.find(orig)
        if pos < 0:
            print(f"!! 원문 못 찾음: {orig}"); sys.exit(1)
        enc = encode_ko(ko)
        if len(enc) > len(orig):
            print(f"!! 인코딩이 원문보다 김: {len(enc)} > {len(orig)}")
            sys.exit(1)
        # 남는 자리는 스페이스로 패딩 (길이 유지 -> 포인터 재계산 불필요)
        enc = enc + b"\x20" * (len(orig) - len(enc))
        sc[pos:pos + len(orig)] = enc
        print(f"\n[5] 교체: {orig.decode()!r}")
        print(f"    -> {ko!r}  ({len(enc)} bytes, 원문과 동일 길이)")

    scripts[SCRIPT_IDX] = bytes(sc)

    # ---------- 6) 스크립트 재구성 ----------
    header = script_blob[:0x22]
    c0, c1, c2 = struct.unpack("<HHH", script_blob[0x22:0x28])
    count = c0 + c1 + c2
    sbase = 0x2B + count * 3
    footer = script_blob[read_u24(script_blob, 0x28) + sbase:]

    def wu24(b, v):
        b.append(v & 0xFF); b.append((v >> 8) & 0xFF); b.append((v >> 16) & 0xFF)

    offs = []; cur = 0
    for s_ in scripts:
        offs.append(cur); cur += len(s_)
    nb = bytearray()
    nb += header
    nb += struct.pack("<HHH", c0, c1, c2)
    wu24(nb, cur)
    for o in offs:
        wu24(nb, o)
    for s_ in scripts:
        nb += s_
    nb += footer
    new_script = bytes(nb)
    assert script_parse(new_script), "스크립트 재구성 실패"

    # ---------- 7) 재압축 ----------
    new_ents = [(f, (new_script if f == SCRIPT_ID else d)) for f, d in ents]
    n = len(new_ents)
    table = bytearray(); body = bytearray()
    addr = 8 + n * 8; total = 0
    for fid, dec in new_ents:
        comp = lzss_compress(dec)
        wu24(table, len(dec)); table.append(fid)
        table += struct.pack("<I", addr)
        body += comp; addr += len(comp); total += len(dec)
    newpack = struct.pack("<II", n, total) + bytes(table) + bytes(body)
    slot = me - ms
    print(f"\n[6] PACK 재압축: {slot} -> {len(newpack)} bytes")
    if len(newpack) > slot:
        print("!! 슬롯 초과"); sys.exit(1)
    prot[ms:me] = newpack + b"\x00" * (slot - len(newpack))

    open(OUT, "wb").write(bytes(prot))
    json.dump({ch: f"{c:02X}" for ch, c in mapping.items()},
              open("/home/claude/legaia/build/kr_us_map.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"\n[7] 저장: {OUT}")

    # ---------- 8) 최종 검증 ----------
    p2 = bytes(prot)
    _, s2, e2 = next(f for f in prot_files(p2) if f[0] == MAP_FILE)
    e2ents = pack_parse(p2[s2:e2])
    sb2 = [d for f, d in e2ents if f == SCRIPT_ID][0]
    sc2 = script_parse(sb2)[SCRIPT_IDX]
    ok = bytes([0xCF, KR_PALETTE_ARG]) in sc2
    print(f"[8] 검증: 팔레트 전환코드 존재 = {ok}")
    px2 = font_pixels(p2)
    ink = sum(int((px2[uv(c)[1]:uv(c)[1]+15, uv(c)[0]:uv(c)[0]+14] == 1).sum())
              for c in mapping.values())
    print(f"    한글 글리프 잉크픽셀(값1): {ink}  (>0 이면 삽입 성공)")


if __name__ == "__main__":
    main()
