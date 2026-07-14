#!/usr/bin/env python3
"""대사 렌더러 훅 — 한글 글리프를 그린다.

## 폰트 자리 (680칸)

게임은 **일본어 한자 폰트 TIM** 을 VRAM(320,256) 에 올려둔다. 미국판은 그걸
쓰지 않지만 데이터는 그대로 남아 있고, VRAM 에도 실제로 로드된다.
(중국판 패치가 바로 이 자리를 한자로 덮었다 — 그래서 폰트 페이지가 2개로 보였다)

    file0      ASCII TIM (896,  0)  tpage 0x0E   CLUT (0,510)
    file0      숫자  TIM (960,256)  tpage 0x1F   CLUT (0,510)
    file876:3  한자  TIM (320,256)  tpage 0x15   CLUT (0,475)  ★
    file894    한자  TIM (320,256)  tpage 0x15   CLUT (0,475)  ★
      └ 같은 VRAM 좌표. 상황에 따라 둘 중 하나가 로드되므로 **둘 다** 한글로 채운다.

한자 TIM 의 CLUT 은 [투명, 흰색, 투명, 흰색, ...] 이라 우리 글리프 값 3 도 흰색이 된다.

| 페이지 | 버퍼      | V범위    | 칸  |
|--------|-----------|----------|-----|
| p1     | ASCII TIM | 144~215  | 120 |
| p2     | 숫자 TIM  |  84~143  | 100 |
| p4     | 숫자 TIM  | 168~191  |  40 |
| p3     | 한자 TIM  |   0~ 71  | 120 | ★
| p5     | 한자 TIM  |  72~143  | 120 | ★
| p6     | 한자 TIM  | 144~215  | 120 | ★
| p7     | 한자 TIM  | 216~251  |  60 | ★
                                합계 680

🔴 쓰면 안 되는 곳 (전부 실제로 깨져 봤다):
    숫자 TIM V  0~ 79  : 아이콘 TIM 4개가 덮는다 ('얘', '쁘' 가 그림 조각으로)
    ASCII TIM V216~239 : ASCII 글리프가 있다
    숫자 TIM V144~167, V192~255 : 숫자 폰트 (HP/MP/LV/G/TIME)
"""
import struct, sys
sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from mips import hi16, lo16
from asm import Assembler
try:
    from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
    _CAP = True
except ImportError:      # 디스어셈블 검증용일 뿐, 빌드에는 없어도 된다
    _CAP = False

LOAD, HDR = 0x80010000, 0x800
HOOK_ADDR = 0x8007AC00
HOOK_LIMIT = 833
HOOK_POINT = 0x80036960
GLYPH_PATH = 0x80036B24
CE_PATH = 0x80036968
REG_PATH = 0x80036B88
REGFUNC = 0x8003D2C4
LOOP = 0x80036908
PAL_VAR = 0x8007B454

# ★ 0xCE <n> 의 n 은 1바이트. n = HANGUL_MIN + idx 이므로
#    페이지당 최대 칸수 = 256 - HANGUL_MIN.
#    원래 0xCE 인자는 {02,03,0B,0E,21,80} 뿐이라 0x81 이상은 안전.
HANGUL_MIN = 0x81         # -> 페이지당 최대 127칸
GW = GH = 12
WIDTH = 11
INK = 3
TPAGE_ASCII = 0x000E      # ASCII 폰트 TIM (896,0)
TPAGE_NUM = 0x001F        # 숫자 TIM (960,256)
# ★ 일본어 한자 폰트 TIM 은 **두 페이지**다 (일본판이 882자를 쓴다)
TPAGE_K1 = 0x0015         # (320,256)
TPAGE_K2 = 0x0016         # (384,256)
# (한자 TIM 전용 CLUT(0,475)은 쓰지 않는다 — 원본 텍스트 CLUT 로 순백이 나온다)
WIDTH_TBL = 0x6471C

COLS = 20
# p1/p2/p4  : 원본이 비워둔 자리
# p3/p5~p7  : 한자 TIM 1 (320,256)   441칸
# p8~p11    : 한자 TIM 2 (384,256)   441칸
PAGES = (1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11)
VBASE = {1: 144, 2: 84, 3: 0, 4: 168,
         5: 72, 6: 144, 7: 216,
         8: 0, 9: 72, 10: 144, 11: 216}
ROWS  = {1: 6, 2: 5, 3: 6, 4: 2,
         5: 6, 6: 6, 7: 3,
         8: 6, 9: 6, 10: 6, 11: 3}
BUF   = {1: "ascii", 2: "num", 4: "num",
         3: "kanji1", 5: "kanji1", 6: "kanji1", 7: "kanji1",
         8: "kanji2", 9: "kanji2", 10: "kanji2", 11: "kanji2"}
TPAGE = {1: TPAGE_ASCII, 2: TPAGE_NUM, 4: TPAGE_NUM,
         3: TPAGE_K1, 5: TPAGE_K1, 6: TPAGE_K1, 7: TPAGE_K1,
         8: TPAGE_K2, 9: TPAGE_K2, 10: TPAGE_K2, 11: TPAGE_K2}
# 버퍼 이름 → VRAM 좌표 (kanji_font 가 참조)
KANJI_VRAM = {"kanji1": (320, 256, 64, 256), "kanji2": (384, 256, 64, 256)}
# 페이지1 은 원본과 같은 tpage(0x0E) 라 전환 없이 원본 등록 경로를 재활용한다
NEEDS_TPAGE = {p for p in PAGES if p != 1}
TBL_N = 12                # 테이블 크기 (page 0~11)

cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN) if _CAP else None


def r2f(r):
    return r - LOAD + HDR


def capacity():
    return {p: COLS * ROWS[p] for p in PAGES}


def uv(idx, page):
    """파이썬 쪽에서도 같은 계산 (빌더가 폰트를 그 자리에 찍어야 함)"""
    row, r = divmod(idx, COLS)
    U = (r // 5) * 64 + (r % 5) * GW
    V = VBASE[page] + row * GH
    return U, V


def _tables(base):
    """페이지별 VBASE / TPAGE / CLUT 테이블 (u16 x 3 x 8).

    한자 TIM 은 자체 CLUT(0,475) 을 쓰고, ASCII/숫자 TIM 은 원본과 같은
    팔레트 행(0x7F86 + page) 을 쓴다.
    """
    vb = [0] * TBL_N
    tp = [0] * TBL_N
    cl = [0] * TBL_N
    for p in PAGES:
        vb[p] = VBASE[p]
        tp[p] = TPAGE[p]
        # 🔴 모든 폰트 페이지가 **같은 CLUT(0x7F87)** 을 쓴다.
        #    이 행은 값0=투명, 값3=순백이다 (원래 영어/한글이 쓰던 것).
        #
        #    예전엔 page 마다 0x7F86+page 로 다른 행을 줬는데, row 11 이후
        #    행들은 값3 이 초록/검정이라 (숫자 TIM 용 팔레트) 한자 페이지
        #    글자가 페이지마다 다른 색으로 깨졌다.
        #    우리 글리프는 어느 버퍼든 값3(잉크)만 쓰므로, 값3=순백인
        #    CLUT 하나로 통일하면 전부 흰 글자가 된다.
        cl[p] = 0x7F87
    return (struct.pack(f"<{TBL_N}H", *vb)
            + struct.pack(f"<{TBL_N}H", *tp)
            + struct.pack(f"<{TBL_N}H", *cl))


def build(tbl_addr):
    """tbl_addr: VBASE/TPAGE/CLUT 테이블 주소."""
    T_VB = tbl_addr
    T_TP = tbl_addr + TBL_N * 2
    T_CL = tbl_addr + TBL_N * 4

    a = Assembler(HOOK_ADDR)

    # === 디스패치: 0xCE <n>, n >= 0x81 이면 한글 ===
    a.ins(ADDIU("t0", "zero", 0xCE))
    a.bne("v1", "t0", "L_glyph")
    a.ins(NOP())
    a.ins(LBU("t1", 1, "s3"))
    a.ins(ADDIU("t0", "zero", HANGUL_MIN))
    a.ins(SLTU("t2", "t1", "t0"))
    a.bnez("t2", "L_ce")
    a.ins(NOP())
    a.j("L_hangul")
    a.ins(NOP())
    a.label("L_ce")
    a.j(CE_PATH)
    a.ins(NOP())
    a.label("L_glyph")
    a.j(GLYPH_PATH)
    a.ins(NOP())

    # === 한글 ===
    a.label("L_hangul")
    a.ins(ADDIU("t1", "t1", -HANGUL_MIN))     # t1 = idx

    # --- row / r 분해 ---
    a.ins(MOVE("t3", "t1"))
    a.ins(MOVE("t4", "zero"))                 # t4 = row*12
    a.label("L_vloop")
    a.ins(SLTIU("t0", "t3", COLS))
    a.bnez("t0", "L_vdone")
    a.ins(NOP())
    a.ins(ADDIU("t4", "t4", GH))
    a.j("L_vloop")
    a.ins(ADDIU("t3", "t3", -COLS))
    a.label("L_vdone")

    # --- U = (r/5)*64 + (r%5)*12 ---
    a.ins(MOVE("t2", "zero"))
    a.label("L_u5")
    a.ins(SLTIU("t0", "t3", 5))
    a.bnez("t0", "L_u1")
    a.ins(NOP())
    a.ins(ADDIU("t2", "t2", 64))
    a.j("L_u5")
    a.ins(ADDIU("t3", "t3", -5))
    a.label("L_u1")
    a.beqz("t3", "L_udone")
    a.ins(NOP())
    a.ins(ADDIU("t2", "t2", GW))
    a.j("L_u1")
    a.ins(ADDIU("t3", "t3", -1))
    a.label("L_udone")
    # t2 = U, t4 = row*12

    # --- 현재 페이지 → 테이블 인덱스 ---
    a.ins(LUI("t5", hi16(PAL_VAR)))
    a.ins(LHU("t5", lo16(PAL_VAR), "t5"))
    a.ins(NOP())                              # ★ 로드 지연
    a.ins(ADDU("t6", "t5", "t5"))             # t6 = page * 2

    # --- 스프라이트 프리미티브 (20B) ---
    a.ins(LUI("v0", 0x0400))
    a.ins(SW("v0", 0x00, "s0"))               # tag
    a.ins(LUI("v1", 0x6480))
    a.ins(ORI("v1", "v1", 0x8080))
    a.ins(SW("v1", 0x04, "s0"))               # code+color
    a.ins(SH("s2", 0x08, "s0"))               # X
    a.ins(SH("s6", 0x0A, "s0"))               # Y
    a.ins(SB("t2", 0x0C, "s0"))               # U
    a.ins(ADDIU("v0", "zero", GW))
    a.ins(SH("v0", 0x10, "s0"))               # W
    a.ins(ADDIU("v0", "zero", GH))
    a.ins(SH("v0", 0x12, "s0"))               # H

    # CLUT = CL_TBL[page]
    a.ins(LUI("t0", hi16(T_CL)))
    a.ins(ADDIU("t0", "t0", lo16(T_CL)))
    a.ins(ADDU("t0", "t0", "t6"))
    a.ins(LHU("v1", 0, "t0"))
    a.ins(NOP())                              # ★ 로드 지연
    a.ins(SH("v1", 0x0E, "s0"))               # CLUT

    # V = VB_TBL[page] + row*12
    a.ins(LUI("t0", hi16(T_VB)))
    a.ins(ADDIU("t0", "t0", lo16(T_VB)))
    a.ins(ADDU("t0", "t0", "t6"))
    a.ins(LHU("t1", 0, "t0"))
    a.ins(NOP())                              # ★ 로드 지연
    a.ins(ADDU("t4", "t4", "t1"))
    a.ins(SB("t4", 0x0D, "s0"))               # V

    # --- 페이지1: tpage 전환 없이 원본 등록 경로 ---
    a.ins(ADDIU("t0", "zero", 1))
    a.bne("t5", "t0", "L_tpage")
    a.ins(NOP())
    a.ins(MOVE("a1", "s0"))
    a.ins(ADDIU("s3", "s3", 1))               # 원본이 +1 더 함
    a.j(REG_PATH)
    a.ins(NOP())

    # --- tpage 전환 경로 (페이지 2~7) ---
    #   등록 함수는 LIFO -> 복원 먼저 등록, 설정 나중 등록
    a.label("L_tpage")
    a.ins(LUI("a0", 0x1F80))
    a.ins(LW("a0", 0x03F4, "a0"))
    a.ins(NOP())                              # ★ 로드 지연
    a.ins(ADDIU("a0", "a0", 4))               # OT 엔트리

    a.ins(LUI("t3", 0x0100))
    a.ins(SW("t3", 0x14, "s0"))               # 복원 tag
    a.ins(LUI("t3", 0xE100))
    a.ins(ORI("t3", "t3", TPAGE_ASCII))
    a.ins(SW("t3", 0x18, "s0"))               # 복원 cmd

    a.ins(LUI("t3", 0x0100))
    a.ins(SW("t3", 0x1C, "s0"))               # 설정 tag
    # 설정 cmd = 0xE1000000 | TP_TBL[page]
    a.ins(LUI("t0", hi16(T_TP)))
    a.ins(ADDIU("t0", "t0", lo16(T_TP)))
    a.ins(ADDU("t0", "t0", "t6"))
    a.ins(LHU("t1", 0, "t0"))
    a.ins(NOP())                              # ★ 로드 지연
    a.ins(LUI("t3", 0xE100))
    a.ins(OR("t3", "t3", "t1"))
    a.ins(SW("t3", 0x20, "s0"))               # 설정 cmd

    a.ins(ADDIU("a1", "s0", 0x14))
    a.jal(REGFUNC)
    a.ins(NOP())
    a.ins(MOVE("a1", "s0"))
    a.jal(REGFUNC)
    a.ins(NOP())
    a.ins(ADDIU("a1", "s0", 0x1C))
    a.jal(REGFUNC)
    a.ins(NOP())

    a.ins(ADDIU("s0", "s0", 0x24))            # sprite20 + tpage8 + tpage8
    a.ins(ADDIU("s4", "s4", 1))
    a.ins(ADDIU("s2", "s2", GW))
    a.ins(ADDIU("s3", "s3", 2))
    a.j(LOOP)
    a.ins(NOP())

    return a


def patch_exe(exe_bytes, index_bytes):
    """훅 코드 + 페이지 테이블을 EXE 에 심는다 (2패스).

    테이블은 훅 코드 바로 뒤에 놓으므로, 먼저 코드 크기를 재서 주소를 정한다.
    """
    exe = bytearray(exe_bytes)

    # 1패스: 코드 크기 측정 → 테이블 주소 결정
    probe = build(HOOK_ADDR).assemble()
    tbl_addr = (HOOK_ADDR + len(probe) + 3) & ~3

    # 2패스: 확정된 테이블 주소로 다시 어셈블
    a = build(tbl_addr)
    viol = a.check_load_delay()
    if viol:
        for v in viol:
            print("로드지연 위반:", v)
        raise RuntimeError("로드 지연 위반")
    code = a.assemble()
    if len(code) != len(probe):
        raise RuntimeError(f"2패스 크기 불일치: {len(probe)} → {len(code)}")

    tbl = _tables(tbl_addr)
    total = (tbl_addr - HOOK_ADDR) + len(tbl)
    if total > HOOK_LIMIT:
        raise RuntimeError(f"훅+테이블 {total}B > {HOOK_LIMIT}B")

    hoff = r2f(HOOK_ADDR)
    if not all(b == 0 for b in exe[hoff:hoff + total]):
        raise RuntimeError("훅 자리 안 비었음")

    exe[hoff:hoff + len(code)] = code
    toff = r2f(tbl_addr)
    exe[toff:toff + len(tbl)] = tbl
    exe[r2f(HOOK_POINT):r2f(HOOK_POINT) + 4] = J(HOOK_ADDR)
    for c in index_bytes:
        exe[WIDTH_TBL + c] = WIDTH
    return bytes(exe), bytes(code)


if __name__ == "__main__":
    probe = build(HOOK_ADDR).assemble()
    tbl_addr = (HOOK_ADDR + len(probe) + 3) & ~3
    a = build(tbl_addr)
    v = a.check_load_delay()
    print("로드지연 위반:", v if v else "없음")
    code = a.assemble()
    tbl = _tables(tbl_addr)
    total = (tbl_addr - HOOK_ADDR) + len(tbl)
    print(f"훅 {len(code)}B + 테이블 {len(tbl)}B = {total}B  [한계 {HOOK_LIMIT}B]")
    print(f"테이블 주소 0x{tbl_addr:08X}")
    cap = capacity()
    print()
    print(f"용량: {cap}")
    print(f"합계 {sum(cap.values())} 음절")
    print()
    # 각 페이지 마지막 칸이 자기 영역을 안 넘는지
    # 각 페이지의 마지막 칸이 자기 영역을 안 넘는지
    LIMS = {1: 216, 2: 144, 3: 72, 4: 192, 5: 144, 6: 216, 7: 252,
            8: 72, 9: 144, 10: 216, 11: 252}
    for p in PAGES:
        last = cap[p] - 1
        U, V = uv(last, p)
        n = HANGUL_MIN + last
        okv = V + GH <= LIMS[p]
        okn = n <= 0xFF
        cl = 0x7F86 + p          # 한자 페이지도 원본 텍스트 CLUT 을 쓴다
        print(f"  p{p:2d} ({BUF[p]:6s}) idx {last:3d}: U={U:3d} V={V:3d} "
              f"tpage=0x{TPAGE[p]:04X} clut=0x{cl:04X}"
              f"  {'OK' if okv and okn else '★문제'}")
