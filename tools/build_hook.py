#!/usr/bin/env python3
"""1b 통합 빌드: 훅 + 음절폰트 + 테스트 텍스트

- EXE에 1b 훅 심기 (hook1b.py 로직)
- 음절 폰트를 ASCII 페이지 V144~255 영역에 삽입 (값 1/2)
- 한글 팔레트(row7) 설정
- 대사를 0x92 <n> 2바이트 코드로 교체
"""
import sys, struct, json, subprocess, tempfile, os
import numpy as np
from PIL import Image, ImageFont, ImageDraw
sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from legaia import prot_files, pack_parse, script_parse, read_u24
import hook3

PROT_US = "/home/claude/legaia/cn/PROT_US.DAT"
EXE_US = "/home/claude/legaia/cn/SCUS_US.exe"
OUT_PROT = "/home/claude/legaia/build/PROT_KR_HOOK.DAT"
OUT_EXE = "/home/claude/legaia/build/SCUS_KR_HOOK.exe"
LZSS_BIN = "/home/claude/legaia/tools/lzss_fast"
UNIFONT = "/usr/share/fonts/opentype/unifont/unifont.otf"

FONT_PIX = 0x7F80
CLUT_OFF = 0x1122C
KR_PAL_ROW = 7
KR_PAL_ARG = 0x01
MARKER = 0xCE
HANGUL_MIN = 0x90   # 0xCE <n>, n>=0x90 이면 한글 (원래 CE기능과 분리)
MAP_FILE, SCRIPT_ID, SCRIPT_IDX = 6, 3, 47

# 음절 폰트 배치 (V144~255)
V_BASE = 144

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


def syllable_uv(n):
    # 훅과 동일: idx = n - HANGUL_MIN
    idx = n - HANGUL_MIN
    U = (idx & 0x0F) * 16
    V = V_BASE + (idx >> 4) * 15
    return U, V


def render_syllable(ch):
    ft = ImageFont.truetype(UNIFONT, 16)
    img = Image.new("L", (16, 16), 0)
    d = ImageDraw.Draw(img)
    bb = ft.getbbox(ch)
    d.text(((16 - (bb[2]-bb[0]))//2 - bb[0], (16 - (bb[3]-bb[1]))//2 - bb[1]),
           ch, font=ft, fill=255)
    img = img.resize((13, 14), Image.LANCZOS)
    a = np.asarray(img).astype(float) / 255.0
    out = np.zeros((15, 14), np.uint8)
    for yy in range(14):
        for xx in range(13):
            v = a[yy, xx]
            if v > 0.5: out[yy, xx] = 1
            elif v > 0.2: out[yy, xx] = 2
    return out


def font_px(prot):
    a = np.frombuffer(prot[FONT_PIX:FONT_PIX+32768], dtype=np.uint8)
    lo = a & 0xF; hi = (a >> 4) & 0xF
    p = np.empty(a.size*2, np.uint8); p[0::2]=lo; p[1::2]=hi
    return p.reshape(256, 256)


def pack_px(px):
    f = px.reshape(-1)
    return ((f[0::2] & 0xF) | ((f[1::2] & 0xF) << 4)).astype(np.uint8).tobytes()


def main():
    prot = bytearray(open(PROT_US, "rb").read())
    exe = bytearray(open(EXE_US, "rb").read())

    # 1) 훅 심기
    hook = hook3.assemble_hook()
    hoff = hook3.r2f(hook3.HOOK_ADDR)
    assert all(b == 0 for b in exe[hoff:hoff+len(hook)])
    exe[hoff:hoff+len(hook)] = hook
    poff = hook3.r2f(hook3.HOOK_POINT)
    exe[poff:poff+4] = J(hook3.HOOK_ADDR)
    print(f"[1] 훅 심기: {len(hook)}B @ 0x{hook3.HOOK_ADDR:08X}")

    # 2) 음절 수집 + 인덱스 배정
    syllables = []
    for _, ko in JOBS:
        for ch in ko:
            if 0xAC00 <= ord(ch) <= 0xD7A3 and ch not in syllables:
                syllables.append(ch)
    # 0xCE <n> 에서 n >= 0x90 이면 한글. 음절 i -> n = 0x90 + i
    mapping = {ch: HANGUL_MIN + i for i, ch in enumerate(syllables)}
    print(f"[2] 음절 {len(syllables)}개: {''.join(syllables)}")
    for ch, n in mapping.items():
        U, V = syllable_uv(n)
        print(f"    {ch} -> n={n} (U={U},V={V})")

    # 3) 음절 폰트 삽입
    px = font_px(bytes(prot))
    for ch, n in mapping.items():
        U, V = syllable_uv(n)
        g = render_syllable(ch)
        px[V:V+15, U:U+14] = g
    prot[FONT_PIX:FONT_PIX+32768] = pack_px(px)
    print(f"[3] 음절 폰트 삽입 (V{V_BASE}~)")

    # 4) 한글 팔레트
    def rgb15(r,g,b): return ((b//8)<<10)|((g//8)<<5)|(r//8)
    kp=[0]*16; kp[1]=rgb15(248,248,248); kp[2]=rgb15(150,150,150)
    base=CLUT_OFF+KR_PAL_ROW*32
    for i in range(16): struct.pack_into("<H",prot,base+i*2,kp[i])
    for row in (6,10,11,12,13,15):
        b=CLUT_OFF+row*32
        for i in (1,2,3): struct.pack_into("<H",prot,b+i*2,0x0000)
    print(f"[4] 한글 팔레트 row{KR_PAL_ROW}")

    # ★ 폭 테이블: 원본 등록코드가 [s3]=인덱스바이트 로 폭을 조회하므로
    #   인덱스 바이트들의 폭을 13 으로 (X 전진 = 13+1 = 14)
    WIDTH_TBL = 0x6471C
    for n in mapping.values():
        exe[WIDTH_TBL + n] = 13
    print(f"[4b] 폭테이블: 인덱스바이트 {len(mapping)}개 -> 13px")

    # 5) 텍스트 교체: 0xCF 01 + (0x92 n)* + 0xCF 00
    _, ms, me = next(f for f in prot_files(bytes(prot)) if f[0]==MAP_FILE)
    ents = pack_parse(bytes(prot[ms:me]))
    sblob = [d for f,d in ents if f==SCRIPT_ID][0]
    scripts = script_parse(sblob)
    sc = bytearray(scripts[SCRIPT_IDX])

    def enc(s):
        # ★ 끝에 0xCF 00 을 두면 안 됨!
        #   전처리가 0xCF 를 2바이트로 처리하며 뒤의 0x00(문자열 종료마커)을
        #   인자로 삼켜버림 -> 종료를 못 찾고 버퍼 오버런 -> 렌더 실패.
        #   팔레트 복귀는 하지 않는다. (다음 런에서 필요시 다시 설정)
        o = bytearray([0xCF, KR_PAL_ARG])
        for ch in s:
            if ch in mapping:
                o += bytes([MARKER, mapping[ch]])
            elif ch == " ":
                o.append(0x20)
        return bytes(o)

    for orig, ko in JOBS:
        pos = sc.find(orig)
        assert pos >= 0
        e = enc(ko)
        if len(e) > len(orig):
            print(f"!! 인코딩({len(e)}) > 원문({len(orig)}): {ko}")
            sys.exit(1)
        sc[pos:pos+len(orig)] = e + b"\x20"*(len(orig)-len(e))
        print(f"[5] '{orig.decode()}' -> '{ko}' ({len(e)}B)")
    scripts[SCRIPT_IDX] = bytes(sc)

    # 6) 재구성
    hdr = sblob[:0x22]
    c0,c1,c2 = struct.unpack("<HHH", sblob[0x22:0x28])
    cnt=c0+c1+c2; sbase=0x2B+cnt*3
    footer = sblob[read_u24(sblob,0x28)+sbase:]
    def wu24(b,v): b += bytes([v&0xFF,(v>>8)&0xFF,(v>>16)&0xFF])
    offs=[]; cur=0
    for s_ in scripts: offs.append(cur); cur+=len(s_)
    nb=bytearray(hdr)+struct.pack("<HHH",c0,c1,c2)
    wu24(nb,cur)
    for o in offs: wu24(nb,o)
    for s_ in scripts: nb+=s_
    nb+=footer
    assert script_parse(bytes(nb))
    newents=[(f,(bytes(nb) if f==SCRIPT_ID else d)) for f,d in ents]
    n=len(newents); tbl=bytearray(); body=bytearray(); addr=8+n*8; tot=0
    for fid,dec in newents:
        comp=lzss_compress(dec)
        wu24(tbl,len(dec)); tbl.append(fid); tbl+=struct.pack("<I",addr)
        body+=comp; addr+=len(comp); tot+=len(dec)
    newpack=struct.pack("<II",n,tot)+bytes(tbl)+bytes(body)
    slot=me-ms
    assert len(newpack)<=slot
    prot[ms:me]=newpack+b"\x00"*(slot-len(newpack))
    print(f"[6] PACK {slot}->{len(newpack)}")

    open(OUT_PROT,"wb").write(bytes(prot))
    open(OUT_EXE,"wb").write(bytes(exe))
    json.dump({ch:n for ch,n in mapping.items()},
              open("/home/claude/legaia/build/kr_hook_map.json","w"),
              ensure_ascii=False, indent=1)
    print("[7] 저장 완료")


if __name__ == "__main__":
    main()
