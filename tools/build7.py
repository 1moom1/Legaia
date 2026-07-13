#!/usr/bin/env python3
"""통합 빌더 v2 — Galmuri11 12x12, 3페이지, tpage 전환

폰트:
  Galmuri11 @ size=12  (픽셀폰트, 안티앨리어싱 0%)
  글리프 12x12, 값 1 (한 색)

페이지 배치:
  페이지1 (0xCF 01): ASCII폰트 TIM(0x7F80), V144~255, tpage 0x000E  -> 144칸
  페이지2 (0xCF 02): 디버그 TIM(0x11438), V0~127,   tpage 0x000D  -> 160칸
  페이지3 (0xCF 03): 디버그 TIM(0x11438), V128~255, tpage 0x000D  -> 160칸
  총 464 음절

디버그 TIM 재활용:
  IMG VRAM (960,256) -> (832,0)   [ix@0x11430, iy@0x11432]
  픽셀 데이터(0x11438~) 전체를 한글로 교체
  ★ CLUT 블록(0x1122C)은 대사 팔레트이므로 절대 건드리지 말 것

팔레트:
  row7,8,9 (= 0xCF 1,2,3 -> CLUT arg+0x7F86) 를 모두 동일한 한글 색상으로
  기존 사용 팔레트의 값1 은 투명화 (한글이 영어 대사에 새는 것 방지)

인코딩:
  0xCF <p> 페이지 선택, 0xCE <n> 음절 (idx = n - 0x90)
"""
import struct, sys, json, subprocess, tempfile, os
import numpy as np
from PIL import Image, ImageFont, ImageDraw
sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from legaia import prot_files, pack_parse, script_parse, read_u24
import hook7
from protected import assert_protected

PROT_US = "/home/claude/legaia/cn/PROT_US.DAT"
EXE_US = "/home/claude/legaia/cn/SCUS_US.exe"
OUT_PROT = "/home/claude/legaia/build/PROT_KR7.DAT"
OUT_EXE = "/home/claude/legaia/build/SCUS_KR7.exe"
LZSS_BIN = "/home/claude/legaia/tools/lzss_fast"
FONT = "/home/claude/legaia/tools/Galmuri11.ttf"

ASCII_PIX = 0x7F80        # ASCII 폰트 TIM 픽셀
DBG_PIX = 0x11438         # 디버그 TIM 픽셀 (재활용)
DBG_IX = 0x11430          # 디버그 TIM IMG ix
DBG_IY = 0x11432          # 디버그 TIM IMG iy
CLUT_OFF = 0x1122C        # 대사 CLUT (건드리지 말 것)

HANGUL_MIN = hook7.HANGUL_MIN
GW = GH = 12
INK = 3          # 한글 잉크 니블값 (ASCII 폰트가 안 쓰는 값: 3~13)
MAP_FILE, SCRIPT_ID, SCRIPT_IDX = 6, 3, 47

# 페이지 정의: p -> (픽셀버퍼, V base, 행수)
# ⚠️ TIM 0x11218 은 '숫자 폰트'다! (V144~255)
#    옮기거나 지우면 메뉴의 HP/MP 숫자가 사라진다.
#    V0~79 = 개발 메모, V80~143 = 빈 영역 (여기만 사용)
# ★ 배치는 hook7 이 정의 (BUF/VBASE/ROWS/COLS)
BUF = hook7.BUF
COLS = hook7.COLS

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


_ft = None
def glyph12(ch):
    """Galmuri11 12x12 픽셀 완벽 렌더 -> 값 1"""
    global _ft
    if _ft is None:
        _ft = ImageFont.truetype(FONT, 12)
    img = Image.new("L", (16, 16), 0)
    ImageDraw.Draw(img).text((0, 0), ch, font=_ft, fill=255)
    a = np.asarray(img)
    ys, xs = np.nonzero(a)
    out = np.zeros((GH, GW), np.uint8)
    if len(ys) == 0:
        return out
    y0, x0 = ys.min(), xs.min()
    for y in range(GH):
        for x in range(GW):
            sy, sx = y + y0, x + x0
            if sy < 16 and sx < 16 and a[sy, sx] > 128:
                out[y, x] = INK
    return out


def unpack_px(buf, off):
    a = np.frombuffer(buf[off:off + 32768], dtype=np.uint8)
    lo = a & 0xF; hi = (a >> 4) & 0xF
    p = np.empty(a.size * 2, np.uint8); p[0::2] = lo; p[1::2] = hi
    return p.reshape(256, 256)


def pack_px(px):
    f = px.reshape(-1)
    return ((f[0::2] & 0xF) | ((f[1::2] & 0xF) << 4)).astype(np.uint8).tobytes()


def main():
    prot = bytearray(open(PROT_US, "rb").read())
    exe = open(EXE_US, "rb").read()

    # 1) 음절 수집
    syls = []
    for _, ko in JOBS:
        for ch in ko:
            if 0xAC00 <= ord(ch) <= 0xD7A3 and ch not in syls:
                syls.append(ch)
    print(f"[1] 음절 {len(syls)}개: {''.join(syls)}")

    # 2) 페이지/인덱스 배정
    #    테스트를 위해 일부를 페이지2(tpage 전환)에 강제 배치
    cap = hook7.capacity()
    print(f"    용량: {cap} = 총 {sum(cap.values())}")
    mapping = {}   # ch -> (page, idx)
    # ★ 페이지 전환 코드(0xCF <p>, 2바이트)가 붙으므로 전환을 최소화해야 한다.
    #   빈도 높은 음절부터 페이지1을 채우고, 넘치면 2 -> 3 순서로.
    #   (실전에서는 음절 빈도순으로 정렬해 배치)
    order = [1, 2, 3, 4]
    mapping = {}
    pi = 0
    used = {p: 0 for p in order}
    # ★ 검증 모드: 페이지1/2 를 일부러 작게 잡아 페이지3(개발메모 영역)까지 쓰게 한다
    import os as _os
    if _os.environ.get("KR_TEST_PAGES"):
        cap = {1: 3, 2: 3, 3: 3, 4: 80}
        print(f"    [검증모드] cap={cap} -> 페이지3 강제 사용")
    for ch in syls:
        while pi < len(order) and used[order[pi]] >= cap[order[pi]]:
            pi += 1
        assert pi < len(order), "음절 용량 초과!"
        p = order[pi]
        mapping[ch] = (p, used[p])
        used[p] += 1
    for ch, (p, i) in mapping.items():
        n = HANGUL_MIN + i
        U, V = hook7.uv(i, p)
        print(f"    {ch} -> p{p} idx{i} (0xCE {n:02X}) U={U} V={V}")

    # 3) 폰트 삽입
    ascii_px = unpack_px(bytes(prot), ASCII_PIX)
    num_px = unpack_px(bytes(prot), DBG_PIX)     # ★ 기존 내용 보존 (숫자 폰트!)
    before = int((num_px[144:] != 0).sum())
    # 페이지3 영역(V0~79, 개발 메모)을 먼저 클리어 — 안 지우면 한글과 겹친다
    if any(p == 3 for p, _ in mapping.values()):
        num_px[0:80] = 0
    for ch, (p, i) in mapping.items():
        buf = BUF[p]
        U, V = hook7.uv(i, p)              # ★ 훅과 동일한 20열 계산
        assert U + GW <= 256, f"U 초과 {U}"
        assert V + GH <= (144 if buf == "num" else 256), f"영역 초과 {buf} V={V}"
        g = glyph12(ch)
        tgt = ascii_px if buf == "ascii" else num_px
        tgt[V:V + GH, U:U + GW] = g
    after = int((num_px[144:] != 0).sum())
    assert before == after, f"숫자 폰트 훼손! {before} -> {after}"
    prot[ASCII_PIX:ASCII_PIX + 32768] = pack_px(ascii_px)
    prot[DBG_PIX:DBG_PIX + 32768] = pack_px(num_px)
    print(f"[2] 폰트 삽입 (ASCII V144~ / 숫자TIM V80~143). 숫자폰트 보존 확인 ✓")

    # 4) ★ TIM 0x11218 의 VRAM 좌표는 건드리지 않는다 (960,256 유지)
    #    옮기면 숫자 폰트의 UV 가 전부 깨진다.
    print(f"[3] 숫자폰트 TIM: VRAM(960,256) 유지, tpage 0x001F (빈영역 V80~143만 사용)")

    # 5) 팔레트: ★ 최소 개입 원칙
    #    한글 잉크 = 니블값 3 (ASCII 폰트가 안 쓰는 값).
    #    한글 팔레트(row7,8,9)의 '값3' 한 칸만 흰색으로.
    #
    #    ⚠️ 다른 팔레트의 값3 을 투명화하면 안 된다!
    #       UI 폰트 TIM(0x18E0)이 값3 을 2072px 쓰고 있어서
    #       메뉴 숫자(HP/MP 등)가 통째로 사라진다.
    #    ⚠️ 투명화는 애초에 불필요:
    #       한글 글리프는 V144~255 / 디버그TIM 에만 있고,
    #       영어 대사는 U/V 가 ASCII 영역(V0~143)만 가리키므로
    #       한글이 영어 대사에 나타날 수 없다.
    def rgb15(r, g, b):
        return ((b // 8) << 10) | ((g // 8) << 5) | (r // 8)
    for row in (7, 8, 9, 10):
        base = CLUT_OFF + row * 32
        struct.pack_into("<H", prot, base + INK * 2, rgb15(248, 248, 248))
    print(f"[4] 팔레트: row7,8,9,10 의 값{INK} = 흰색 (그 외 전부 원본 보존)")

    # 6) 훅 심기 + 폭 테이블
    idx_bytes = sorted({HANGUL_MIN + i for _, i in mapping.values()})
    exe2, hook = hook7.patch_exe(exe, idx_bytes)
    print(f"[5] 훅 {len(hook)}B, 폭테이블 {len(idx_bytes)}개 -> {hook7.WIDTH}px")

    # 7) 텍스트 인코딩
    _, ms, me = next(f for f in prot_files(bytes(prot)) if f[0] == MAP_FILE)
    ents = pack_parse(bytes(prot[ms:me]))
    sblob = [d for f, d in ents if f == SCRIPT_ID][0]
    scripts = script_parse(sblob)
    sc = bytearray(scripts[SCRIPT_IDX])

    def enc(s):
        o = bytearray()
        cur = None
        for ch in s:
            if ch == " ":
                o.append(0x20); continue
            p, i = mapping[ch]
            if p != cur:
                o += bytes([0xCF, p])      # 페이지 전환
                cur = p
            o += bytes([0xCE, HANGUL_MIN + i])
        return bytes(o)

    for orig, ko in JOBS:
        pos = sc.find(orig)
        assert pos >= 0, orig
        e = enc(ko)
        if len(e) > len(orig):
            print(f"!! 인코딩({len(e)}) > 원문({len(orig)})"); sys.exit(1)
        sc[pos:pos + len(orig)] = e + b"\x20" * (len(orig) - len(e))
        print(f"[6] '{ko}' ({len(e)}B) {e.hex()}")
    scripts[SCRIPT_IDX] = bytes(sc)

    # 8) 재구성
    hdr = sblob[:0x22]
    c0, c1, c2 = struct.unpack("<HHH", sblob[0x22:0x28])
    cnt = c0 + c1 + c2; sbase = 0x2B + cnt * 3
    footer = sblob[read_u24(sblob, 0x28) + sbase:]
    def wu24(b, v): b += bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])
    offs = []; cur = 0
    for s_ in scripts: offs.append(cur); cur += len(s_)
    nb = bytearray(hdr) + struct.pack("<HHH", c0, c1, c2)
    wu24(nb, cur)
    for o in offs: wu24(nb, o)
    for s_ in scripts: nb += s_
    nb += footer
    assert script_parse(bytes(nb))
    newents = [(f, (bytes(nb) if f == SCRIPT_ID else d)) for f, d in ents]
    n = len(newents); tbl = bytearray(); body = bytearray(); addr = 8 + n * 8; tot = 0
    for fid, dec in newents:
        comp = lzss_compress(dec)
        wu24(tbl, len(dec)); tbl.append(fid); tbl += struct.pack("<I", addr)
        body += comp; addr += len(comp); tot += len(dec)
    newpack = struct.pack("<II", n, tot) + bytes(tbl) + bytes(body)
    slot = me - ms
    assert len(newpack) <= slot, f"PACK 초과 {len(newpack)}>{slot}"
    prot[ms:me] = newpack + b"\x00" * (slot - len(newpack))
    print(f"[7] PACK {slot} -> {len(newpack)}")

    # 🔴 보호 구역 검증 (숫자 폰트 / TIM 좌표 / 팔레트)
    orig_prot = open(PROT_US, "rb").read()
    assert_protected(orig_prot, bytes(prot))
    print("[8] 🔴 보호구역 검증 통과 (숫자폰트/TIM좌표/팔레트)")

    open(OUT_PROT, "wb").write(bytes(prot))
    open(OUT_EXE, "wb").write(exe2)
    json.dump({ch: [p, i] for ch, (p, i) in mapping.items()},
              open("/home/claude/legaia/build/kr7_map.json", "w"),
              ensure_ascii=False, indent=1)
    print("[8] 저장 완료")


if __name__ == "__main__":
    main()
