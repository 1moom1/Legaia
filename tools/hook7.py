#!/usr/bin/env python3
"""훅 v7 — 일본판 방식 20열 배치 (400 음절)

일본판 렌더러(0x80037A00)를 해독해 얻은 배치:
    r = idx % 20
    U = (r / 5) * 64 + (r % 5) * 12
    V = vbase + (idx / 20) * 12
    글리프 12 x 12

16열(U = idx*16)이면 글자당 4px 를 버린다.
20열이면 U 가 0~240 에 촘촘히 들어가 페이지당 글자 수가 25% 늘어난다.

MIPS 에 나눗셈이 느리므로 일본판처럼 **반복 뺄셈**으로 계산.

용량:
    페이지1 (ASCII TIM  V144~255, tpage 0x000E): 9행 x 20 = 180
    페이지2 (숫자 TIM   V 80~143, tpage 0x001F): 5행 x 20 = 100
    페이지3 (숫자 TIM   V  0~ 79, tpage 0x001F): 6행 x 20 = 120
                                              합계  400
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
WIDTH_TBL = 0x6471C

# ★ 일본판 방식: 20열
#   인덱스 바이트 제약(127칸/페이지) 때문에 ASCII TIM 을 두 페이지로 쪼갠다.
#   p1/p4 는 같은 tpage(0x000E), 팔레트 row 만 다르다.
COLS = 20
#   p1 을 크게(120칸) 잡아 빈도 상위 음절을 최대한 담는다 → 페이지 전환 감소
VBASE = {1: 144, 2: 80, 3: 0, 4: 216}
ROWS  = {1: 6,   2: 5,  3: 6, 4: 3}
BUF   = {1: "ascii", 2: "num", 3: "num", 4: "ascii"}
# tpage 전환이 필요한 페이지 (숫자 TIM 쪽)
NEEDS_TPAGE = {2, 3}

cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN) if _CAP else None


def r2f(r):
    return r - LOAD + HDR


def capacity():
    return {p: COLS * ROWS[p] for p in (1, 2, 3, 4)}


def uv(idx, page):
    """파이썬 쪽에서도 같은 계산 (빌더가 폰트를 그 자리에 찍어야 함)"""
    row, r = divmod(idx, COLS)
    U = (r // 5) * 64 + (r % 5) * GW
    V = VBASE[page] + row * GH
    return U, V


def build():
    a = Assembler(HOOK_ADDR)

    # === 디스패치: 0xCE <n>, n >= 0x90 이면 한글 ===
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

    # --- V offset 과 r 계산 (반복 뺄셈) ---
    #   t3 = idx 사본, t4 = V offset
    a.ins(MOVE("t3", "t1"))
    a.ins(MOVE("t4", "zero"))
    a.label("L_vloop")
    a.ins(SLTIU("t0", "t3", COLS))
    a.bnez("t0", "L_vdone")
    a.ins(NOP())
    a.ins(ADDIU("t4", "t4", GH))              # V += 12
    a.j("L_vloop")
    a.ins(ADDIU("t3", "t3", -COLS))           # idx -= 20  (딜레이 슬롯)
    a.label("L_vdone")
    # 여기서 t3 = r (0~19), t4 = row*12

    # --- U 계산: (r/5)*64 + (r%5)*12 ---
    a.ins(MOVE("t2", "zero"))
    a.label("L_u5")
    a.ins(SLTIU("t0", "t3", 5))
    a.bnez("t0", "L_u1")
    a.ins(NOP())
    a.ins(ADDIU("t2", "t2", 64))              # U += 64
    a.j("L_u5")
    a.ins(ADDIU("t3", "t3", -5))
    a.label("L_u1")
    a.beqz("t3", "L_udone")
    a.ins(NOP())
    a.ins(ADDIU("t2", "t2", GW))              # U += 12
    a.j("L_u1")
    a.ins(ADDIU("t3", "t3", -1))
    a.label("L_udone")
    # t2 = U (0~240), t4 = V offset

    # --- 페이지 판정 ---
    a.ins(LUI("t5", hi16(PAL_VAR)))
    a.ins(LHU("t5", lo16(PAL_VAR), "t5"))
    a.ins(NOP())                              # ★ 로드 지연

    # --- 스프라이트 프리미티브 (20B) ---
    a.ins(LUI("v0", 0x0400))
    a.ins(SW("v0", 0x00, "s0"))               # tag
    a.ins(LUI("v1", 0x6480))
    a.ins(ORI("v1", "v1", 0x8080))
    a.ins(SW("v1", 0x04, "s0"))               # code+color
    a.ins(SH("s2", 0x08, "s0"))               # X
    a.ins(SH("s6", 0x0A, "s0"))               # Y
    a.ins(SB("t2", 0x0C, "s0"))               # U  ← 20열 계산 결과
    a.ins(ADDIU("v0", "zero", GW))
    a.ins(SH("v0", 0x10, "s0"))               # W
    a.ins(ADDIU("v0", "zero", GH))
    a.ins(SH("v0", 0x12, "s0"))               # H
    a.ins(ADDIU("v1", "t5", 0x7F86))
    a.ins(SH("v1", 0x0E, "s0"))               # CLUT

    # --- 페이지별 V base + tpage ---
    a.ins(ADDIU("t0", "zero", 2))
    a.beq("t5", "t0", "L_p2")
    a.ins(NOP())
    a.ins(ADDIU("t0", "zero", 3))
    a.beq("t5", "t0", "L_p3")
    a.ins(NOP())
    a.ins(ADDIU("t0", "zero", 4))
    a.beq("t5", "t0", "L_p4")
    a.ins(NOP())

    # 페이지1: V = 144 + off, tpage 전환 없음 → 원본 등록 경로 재활용
    a.ins(ADDIU("t4", "t4", VBASE[1]))
    a.label("L_noswitch")
    a.ins(SB("t4", 0x0D, "s0"))
    a.ins(MOVE("a1", "s0"))
    a.ins(ADDIU("s3", "s3", 1))               # 원본이 +1 더 함
    a.j(REG_PATH)
    a.ins(NOP())

    # 페이지4: V = 204 + off, 같은 tpage(0x000E) → 전환 없음
    a.label("L_p4")
    a.ins(ADDIU("t4", "t4", VBASE[4]))
    a.j("L_noswitch")
    a.ins(NOP())

    # 페이지3: V = 0 + off
    a.label("L_p3")
    a.ins(SB("t4", 0x0D, "s0"))
    a.j("L_tpage")
    a.ins(NOP())

    # 페이지2: V = 80 + off
    a.label("L_p2")
    a.ins(ADDIU("t4", "t4", VBASE[2]))
    a.ins(SB("t4", 0x0D, "s0"))

    # --- tpage 전환 경로 (페이지2/3) ---
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
    a.ins(LUI("t3", 0xE100))
    a.ins(ORI("t3", "t3", TPAGE_NUM))
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
    exe = bytearray(exe_bytes)
    a = build()
    viol = a.check_load_delay()
    if viol:
        for v in viol:
            print("로드지연 위반:", v)
        raise RuntimeError("로드 지연 위반")
    # 훅 코드는 고정이므로 바이트 수로 무결성을 확인한다
    _EXPECT = 416
    code_chk = a.assemble()
    if len(code_chk) != _EXPECT:
        print(f"⚠ 훅 크기가 예상과 다름: {len(code_chk)}B (기대 {_EXPECT}B)")
    code = a.assemble()
    if len(code) > HOOK_LIMIT:
        raise RuntimeError(f"훅 초과: {len(code)} > {HOOK_LIMIT}")
    hoff = r2f(HOOK_ADDR)
    if not all(b == 0 for b in exe[hoff:hoff + len(code)]):
        raise RuntimeError("훅 자리 안 비었음")
    exe[hoff:hoff + len(code)] = code
    exe[r2f(HOOK_POINT):r2f(HOOK_POINT) + 4] = J(HOOK_ADDR)
    for c in index_bytes:
        exe[WIDTH_TBL + c] = WIDTH
    return bytes(exe), bytes(code)


if __name__ == "__main__":
    a = build()
    v = a.check_load_delay()
    print("로드지연 위반:", v if v else "없음")
    code = a.assemble()
    print(f"훅 {len(code)}B ({len(code)//4} 명령)  [한계 {HOOK_LIMIT}B]")
    cap = capacity()
    print(f"용량: {cap}  합계 {sum(cap.values())} 음절  (기존 16열: 320)")
    print()
    print("U/V 배치 검증 (페이지1):")
    for i in (0, 4, 5, 19, 20, 179):
        U, V = uv(i, 1)
        ok = "OK" if U + GW <= 256 and V + GH <= 256 else "★초과"
        print(f"  idx {i:3d}: U={U:3d} V={V:3d}  {ok}")
    print()
    LIMS = {1: 216, 2: 144, 3: 80, 4: 256}
    for p in (1, 2, 3, 4):
        last = cap[p] - 1
        U, V = uv(last, p)
        n = HANGUL_MIN + last
        okv = V + GH <= LIMS[p]
        okn = n <= 0xFF
        print(f"  페이지{p} ({BUF[p]:5s}) 마지막 idx {last:3d}: U={U:3d} V={V:3d} "
              f"코드=0xCE {n:02X}  {'OK' if okv and okn else '★문제'}")
