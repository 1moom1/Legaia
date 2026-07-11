#!/usr/bin/env python3
"""1단계: 완성형 음절 한글 PoC (훅 없음, 한 페이지)

원리: (A)방식 팔레트 트릭 그대로. 차이는 자모 조합이 아니라 완성형 음절을
      통째로 폰트에 그림 (품질 우수).

제약: 1바이트 코드 -> 폰트 페이지 224칸이 리밋.
      그중 빈칸(글리프 없음)에만 음절 삽입 가능.
      => 특정 대사에 필요한 고유 음절만 골라 넣는다.

출력: build/PROT_US_KR3.DAT + build/SCUS_KR3.exe
"""
import sys, struct, json, subprocess, tempfile, os
import numpy as np
from PIL import Image, ImageFont, ImageDraw

sys.path.insert(0, "/home/claude/legaia/tools")
from legaia import prot_files, pack_parse, script_parse, read_u24

PROT_US = "/home/claude/legaia/cn/PROT_US.DAT"
EXE_US = "/home/claude/legaia/cn/SCUS_US.exe"
OUT_PROT = "/home/claude/legaia/build/PROT_US_KR3.DAT"
OUT_EXE = "/home/claude/legaia/build/SCUS_KR3.exe"
LZSS_BIN = "/home/claude/legaia/tools/lzss_fast"
UNIFONT = "/usr/share/fonts/opentype/unifont/unifont.otf"

FONT_PIX = 0x7F80
FONT_W = FONT_H = 256
CLUT_OFF = 0x1122C
WIDTH_TBL = 0x6471C
GLYPH_W, GLYPH_H = 14, 15
HANGUL_WIDTH = 13

MAP_FILE, SCRIPT_ID, SCRIPT_IDX = 6, 3, 47
KR_PAL_ARG, KR_PAL_ROW = 0x01, 7
CONTROL = {0x00, 0x7C, 0xCE, 0xCF, 0x20}

# 번역할 대사 (map6 script47, 검증된 것)
JOBS = [
    (b"The Memory Statue will",     "기억의 상은"),
    (b"remember things for you...", "당신을 기억합니다"),
]


def lzss_compress(data):
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


def render_syllable(ch):
    """완성형 음절 -> 14x15, 값 0/2/3 (Unifont 16px)"""
    ft = ImageFont.truetype(UNIFONT, 16)
    img = Image.new("L", (16, 16), 0)
    d = ImageDraw.Draw(img)
    bb = ft.getbbox(ch)
    d.text(((16 - (bb[2] - bb[0])) // 2 - bb[0],
            (16 - (bb[3] - bb[1])) // 2 - bb[1]), ch, font=ft, fill=255)
    # 16x16 -> 13x14 로 살짝 축소해 14x15 셀에 여백 두고 배치
    img = img.resize((13, 14), Image.LANCZOS)
    a = np.asarray(img).astype(float) / 255.0
    out = np.zeros((GLYPH_H, GLYPH_W), np.uint8)
    for yy in range(14):
        for xx in range(13):
            v = a[yy, xx]
            if v > 0.5:
                out[yy, xx] = 1        # 팔레트에서 값1=흰색
            elif v > 0.2:
                out[yy, xx] = 2
    return out


def scan_used(prot):
    # 이 PoC는 map6만 바꾸므로 map6에서 쓰이는 코드만 피하면 안전
    count = struct.unpack("<I", prot[4:8])[0]
    offs = [struct.unpack("<I", prot[8 + 4 * i:12 + 4 * i])[0] for i in range(count)]
    used = set()
    for mid in [MAP_FILE]:
        s, e = offs[mid] * 2048, offs[mid + 1] * 2048
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

    # 필요한 고유 음절
    syllables = []
    for _, ko in JOBS:
        for ch in ko:
            if 0xAC00 <= ord(ch) <= 0xD7A3 and ch not in syllables:
                syllables.append(ch)
    print(f"[1] 필요 음절 {len(syllables)}개: {''.join(syllables)}")

    # 안전 코드: 글리프 없음 + 대사 미사용
    used = scan_used(bytes(prot))

    def has_glyph(c):
        u, v = uv(c)
        if v < 0 or v + GLYPH_H > FONT_H:
            return True
        return bool(np.isin(px[v:v + GLYPH_H, u:u + GLYPH_W], [14, 15]).any())

    safe = [c for c in range(0x21, 0x100)
            if c not in CONTROL and c not in used and not has_glyph(c)]
    print(f"[2] 안전 코드 {len(safe)}개")
    if len(syllables) > len(safe):
        print(f"!! 부족: 음절 {len(syllables)} > 코드 {len(safe)}")
        print(f"   -> 대사를 줄이거나 훅 필요")
        sys.exit(1)

    mapping = {ch: safe[i] for i, ch in enumerate(syllables)}
    print("[3] 배정:")
    for ch, c in mapping.items():
        print(f"    {ch} -> 0x{c:02X}")

    # 폰트에 음절 그리기 (값 1/2)
    for ch, code in mapping.items():
        u, v = uv(code)
        g = render_syllable(ch)
        px[v:v + GLYPH_H, u:u + GLYPH_W] = g
    prot[FONT_PIX:FONT_PIX + FONT_W * FONT_H // 2] = pack_px(px)
    print("[4] 폰트에 완성형 음절 삽입")

    # 폭 패치
    for code in mapping.values():
        exe[WIDTH_TBL + code] = HANGUL_WIDTH
    print(f"[5] 폭 테이블: 음절 코드 -> {HANGUL_WIDTH}px")

    # 팔레트
    def rgb15(r, g, b):
        return ((b // 8) << 10) | ((g // 8) << 5) | (r // 8)
    kp = [0] * 16
    kp[1] = rgb15(248, 248, 248); kp[2] = rgb15(150, 150, 150)
    base = CLUT_OFF + KR_PAL_ROW * 32
    for i in range(16):
        struct.pack_into("<H", prot, base + i * 2, kp[i])
    for row in (6, 10, 11, 12, 13, 15):
        b = CLUT_OFF + row * 32
        for i in (1, 2, 3):
            struct.pack_into("<H", prot, b + i * 2, 0x0000)
    print(f"[6] 팔레트 row{KR_PAL_ROW} 한글전용, 기존 팔레트 값1/2/3 투명화")

    # 텍스트 교체
    _, ms, me = next(f for f in prot_files(bytes(prot)) if f[0] == MAP_FILE)
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

    for orig, ko in JOBS:
        pos = sc.find(orig)
        assert pos >= 0, f"못찾음 {orig}"
        e = enc(ko)
        assert len(e) <= len(orig), f"길이초과 {len(e)}>{len(orig)} ({ko})"
        sc[pos:pos + len(orig)] = e + b"\x20" * (len(orig) - len(e))
        print(f"[7] '{orig.decode()}' -> '{ko}'")
    scripts[SCRIPT_IDX] = bytes(sc)

    # 재구성
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
    assert len(newpack) <= slot
    prot[ms:me] = newpack + b"\x00" * (slot - len(newpack))
    print(f"[8] PACK {slot} -> {len(newpack)}")

    open(OUT_PROT, "wb").write(bytes(prot))
    open(OUT_EXE, "wb").write(bytes(exe))
    json.dump({ch: f"{c:02X}" for ch, c in mapping.items()},
              open("/home/claude/legaia/build/kr_map3.json", "w"),
              ensure_ascii=False, indent=1)
    print(f"[9] 저장 완료")


if __name__ == "__main__":
    main()
