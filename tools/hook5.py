#!/usr/bin/env python3
"""훅 v5 — 12x12 글리프 + 팔레트 기반 3페이지 (tpage 전환)

인코딩:
  0xCF <p>   팔레트 겸 페이지 선택 (게임 원래 기능이 [0x8007B454]=p 를 저장)
     p=1 -> 페이지0 : tpage 0x000E (ASCII 페이지), V = 144 + (idx>>4)*12   [144칸]
     p=2 -> 페이지1 : tpage 0x000D (디버그TIM 재활용), V =       (idx>>4)*12 [160칸]
     p=3 -> 페이지2 : tpage 0x000D, V = 128 + (idx>>4)*12                   [160칸]
  0xCE <n>   음절, idx = n - 0x90

  U = (idx & 0x0F) * 16   (16열, 나눗셈 불필요)
  글리프 12x12 (W=12, H=12), 폭테이블 = 11 -> X 전진 12

페이지1/2 는 tpage 0x000D 이므로 DR_TPAGE 프리미티브 삽입 필요.
LIFO 등록: 복원tpage -> sprite -> 설정tpage  =>  실행: 설정 -> sprite -> 복원

CLUT = [0x8007B454] + 0x7F86  (p=1,2,3 -> row 7,8,9)
  => 세 팔레트를 모두 동일한 한글 색상으로 채울 것
"""
import struct, sys
sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from mips import hi16, lo16
from asm import Assembler
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

LOAD, HDR = 0x80010000, 0x800
HOOK_ADDR = 0x8007AC00
HOOK_POINT = 0x80036960
GLYPH_PATH = 0x80036B24
CE_PATH = 0x80036968
REG_PATH = 0x80036B88        # 원본 등록/전진
REGFUNC = 0x8003D2C4
LOOP = 0x80036908
PAL_VAR = 0x8007B454         # 팔레트/페이지 변수

HANGUL_MIN = 0x90
GW, GH = 12, 12              # 글리프 크기
WIDTH = 11                   # 폭테이블 값 -> X 전진 = 11+1 = 12
TPAGE_ASCII = 0x000E
TPAGE_P1 = 0x001F        # TIM 0x11218 (960,256) — 원위치 유지!
WIDTH_TBL = 0x6471C

# 페이지별 V base
#  p1: ASCII 폰트 TIM (896,0) tpage 0x000E, V144~255 (9행)
#  p2: TIM 0x11218  (960,256) tpage 0x001F, V80~143 (5행)
#      ⚠️ 이 TIM 의 V144~255 는 '숫자 폰트' — 절대 건드리지 말 것!
#         V0~79 은 개발 메모. 쓸 수 있는 건 V80~143 뿐.
#  p3: 숫자 TIM (960,256) tpage 0x001F, V0~79 (개발 메모 영역, 6행)
VBASE = {1: 144, 2: 80, 3: 0}
COLS = 16
ROWS = {1: 9, 2: 5, 3: 6}

cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)


def r2f(r):
    return r - LOAD + HDR


def capacity():
    return {p: COLS * ROWS[p] for p in (1, 2, 3)}


def slot_uv(page, idx):
    """빌더용: 페이지/인덱스 -> (U, V)"""
    U = (idx & 0x0F) * 16
    V = VBASE[page] + (idx >> 4) * GH
    return U, V


def build():
    a = Assembler(HOOK_ADDR)

    # === 디스패치 ===
    a.ins(ADDIU("t0", "zero", 0xCE))
    a.bne("v1", "t0", "L_glyph")
    a.ins(NOP())
    a.ins(LBU("t1", 1, "s3"))                 # t1 = n
    a.ins(ADDIU("t0", "zero", HANGUL_MIN))
    a.ins(SLTU("t2", "t1", "t0"))
    a.bnez("t2", "L_ce")                      # n < 0x90 -> 원래 CE
    a.ins(NOP())
    a.j("L_hangul")
    a.ins(NOP())
    a.label("L_ce")
    a.j(CE_PATH); a.ins(NOP())
    a.label("L_glyph")
    a.j(GLYPH_PATH); a.ins(NOP())

    # === L_hangul ===
    a.label("L_hangul")
    a.ins(ADDIU("t1", "t1", -HANGUL_MIN))     # t1 = idx
    # 페이지 = [PAL_VAR]
    # ★ LHU offset 은 부호있는 16비트! hi16() 로 LUI 보정 필수.
    #   (0xB454 는 음수 -0x4BAC 이므로 LUI 는 0x8008 이어야 함)
    a.ins(LUI("t5", hi16(PAL_VAR)))
    a.ins(LHU("t5", lo16(PAL_VAR), "t5"))     # t5 = 페이지 (1,2,3)
    a.ins(NOP())                              # 로드 지연

    # 공통: sprite 프리미티브 필드 (U, W, H, X, Y, tag, color, CLUT)
    _sprite_common(a)

    # V 계산: t3 = idx >> 4,  t4 = t3 * 12
    a.ins(SRL("t3", "t1", 4))
    a.ins(SLL("t4", "t3", 3))                 # t3*8
    a.ins(SLL("t6", "t3", 2))                 # t3*4
    a.ins(ADDU("t4", "t4", "t6"))             # t4 = t3*12
    # 페이지별 V base
    a.ins(ADDIU("t0", "zero", 2))
    a.beq("t5", "t0", "L_p2")                 # page==2 -> V80, tpage 전환
    a.ins(NOP())
    a.ins(ADDIU("t0", "zero", 3))
    a.beq("t5", "t0", "L_p3")                 # page==3 -> V0,  tpage 전환
    a.ins(NOP())
    # page==1 (기본): V base 144, tpage 전환 없음
    a.ins(ADDIU("t4", "t4", 144))
    a.ins(SB("t4", 0x0D, "s0"))
    a.ins(MOVE("a1", "s0"))
    a.ins(ADDIU("s3", "s3", 1))               # 원본이 +1 더
    a.j(REG_PATH)                             # 원본 등록/전진 재활용
    a.ins(NOP())

    # --- 페이지3: V base 0 (개발메모 영역), tpage 0x001F ---
    a.label("L_p3")
    a.ins(SB("t4", 0x0D, "s0"))               # V = t4 + 0
    a.j("L_tpage")
    a.ins(NOP())

    # --- 페이지2: V base 80, tpage 0x001F ---
    a.label("L_p2")
    a.ins(ADDIU("t4", "t4", 80))
    a.ins(SB("t4", 0x0D, "s0"))

    # --- tpage 전환 경로 (페이지2/3 공통) ---
    a.label("L_tpage")
    # OT 엔트리
    a.ins(LUI("a0", 0x1F80))
    a.ins(LW("a0", 0x03F4, "a0"))
    a.ins(NOP())                              # 로드 지연
    a.ins(ADDIU("a0", "a0", 4))
    # 복원 tpage(0x000E) @ s0+0x14
    a.ins(LUI("t3", 0x0100))
    a.ins(SW("t3", 0x14, "s0"))
    a.ins(LUI("t3", 0xE100))
    a.ins(ORI("t3", "t3", TPAGE_ASCII))
    a.ins(SW("t3", 0x18, "s0"))
    # 설정 tpage(0x000D) @ s0+0x1C
    a.ins(LUI("t3", 0x0100))
    a.ins(SW("t3", 0x1C, "s0"))
    a.ins(LUI("t3", 0xE100))
    a.ins(ORI("t3", "t3", TPAGE_P1))
    a.ins(SW("t3", 0x20, "s0"))
    # 등록 (LIFO): 복원 -> sprite -> 설정
    a.ins(ADDIU("a1", "s0", 0x14))
    a.jal(REGFUNC); a.ins(NOP())
    a.ins(MOVE("a1", "s0"))
    a.jal(REGFUNC); a.ins(NOP())
    a.ins(ADDIU("a1", "s0", 0x1C))
    a.jal(REGFUNC); a.ins(NOP())
    # 전진
    a.ins(ADDIU("s0", "s0", 0x24))            # sprite20 + tpage8 + tpage8
    a.ins(ADDIU("s4", "s4", 1))
    a.ins(ADDIU("s2", "s2", GW))              # X += 12
    a.ins(ADDIU("s3", "s3", 2))
    a.j(LOOP)
    a.ins(NOP())

    return a


def _sprite_common(a):
    """sprite 프리미티브의 V 이외 필드"""
    a.ins(LUI("v0", 0x0400))
    a.ins(SW("v0", 0x00, "s0"))               # tag
    a.ins(LUI("v1", 0x6480))
    a.ins(ORI("v1", "v1", 0x8080))
    a.ins(SW("v1", 0x04, "s0"))               # cmd+color
    a.ins(SH("s2", 0x08, "s0"))               # X
    a.ins(SH("s6", 0x0A, "s0"))               # Y
    # U = (idx & 0xF) * 16
    a.ins(ANDI("t2", "t1", 0x0F))
    a.ins(SLL("t2", "t2", 4))
    a.ins(SB("t2", 0x0C, "s0"))
    # W, H
    a.ins(ADDIU("v0", "zero", GW))
    a.ins(SH("v0", 0x10, "s0"))
    a.ins(ADDIU("v0", "zero", GH))
    a.ins(SH("v0", 0x12, "s0"))
    # CLUT = [PAL_VAR] + 0x7F86   (t5 에 이미 로드됨)
    a.ins(ADDIU("v1", "t5", 0x7F86))
    a.ins(SH("v1", 0x0E, "s0"))


def patch_exe(exe_bytes, index_bytes):
    exe = bytearray(exe_bytes)
    a = build()
    viol = a.check_load_delay()
    if viol:
        for v in viol:
            print("로드지연:", v)
        raise RuntimeError("로드 지연 슬롯 위반")
    hook = a.assemble()
    hoff = r2f(HOOK_ADDR)
    if len(hook) > 833:
        raise RuntimeError(f"훅 너무 큼: {len(hook)}B")
    if not all(b == 0 for b in exe[hoff:hoff + len(hook)]):
        raise RuntimeError("훅 자리 안 비었음")
    exe[hoff:hoff + len(hook)] = hook
    exe[r2f(HOOK_POINT):r2f(HOOK_POINT) + 4] = J(HOOK_ADDR)
    for code in index_bytes:
        exe[WIDTH_TBL + code] = WIDTH
    return bytes(exe), hook


if __name__ == "__main__":
    a = build()
    v = a.check_load_delay()
    print("로드지연 위반:", v if v else "없음")
    h = a.assemble()
    print(f"훅 {len(h)}B ({len(h)//4} 명령)  [한계 833B]")
    print(f"용량: {capacity()}  합계 {sum(capacity().values())} 음절")
