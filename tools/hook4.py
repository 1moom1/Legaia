#!/usr/bin/env python3
"""훅 v4 — tpage 전환 다중 페이지 (2패스 어셈블러 사용)

인덱스로 페이지 구분:
  idx 0~111 : 페이지0 (896,0) tpage 0x000E, V = 144 + (idx>>4)*15
  idx 112+  : 페이지1 (832,0) tpage 0x000D, V = (idx'>>4)*15   (idx'=idx-112)

페이지0: 기존 방식 (tpage 전환 불필요, ASCII 와 같은 tpage).
페이지1: DR_TPAGE 프리미티브로 tpage 전환.

LIFO 등록으로 실행순서 보장:
  등록순서: 복원tpage(0x0E) -> sprite -> 설정tpage(0x0D)
  실행순서: 설정(0x0D) -> sprite -> 복원(0x0E)

프리미티브 버퍼 s0 사용:
  sprite 20B + tpage 8B + tpage 8B = 36B (페이지1)
  등록 후 s0 를 올바르게 전진시켜야 다음 문자와 안 겹침.
"""
import struct, sys
sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from asm import Assembler
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

LOAD, HDR = 0x80010000, 0x800
HOOK_ADDR = 0x8007AC00
HOOK_POINT = 0x80036960
GLYPH_PATH = 0x80036B24
CE_PATH = 0x80036968
REG_PATH = 0x80036B88       # 원본 등록/전진 (페이지0용)
REGFUNC = 0x8003D2C4

HANGUL_MIN = 0x90
V_BASE = 144
PAGE0_CAP = 112
TPAGE_ASCII = 0x000E
TPAGE_PAGE1 = 0x000D
WIDTH_TBL = 0x6471C

cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)


def r2f(r):
    return r - LOAD + HDR


def build():
    a = Assembler(HOOK_ADDR)

    # === 디스패치 ===
    a.ins(ADDIU("t0", "zero", 0xCE))
    a.bne("v1", "t0", "L_glyph")          # v1 != 0xCE -> 글리프
    a.ins(NOP())
    a.ins(LBU("t1", 1, "s3"))             # t1 = 인덱스 바이트
    a.ins(ADDIU("t0", "zero", HANGUL_MIN))
    a.ins(SLTU("t2", "t1", "t0"))
    a.bnez("t2", "L_ce")                  # < 0x90 -> 원래 CE
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
    a.ins(ADDIU("t0", "zero", PAGE0_CAP))
    a.ins(SLTU("t2", "t1", "t0"))
    a.bnez("t2", "L_page0")                   # idx < 112 -> 페이지0
    a.ins(NOP())

    # ---- 페이지1 (tpage 0x000D) ----
    # idx' = idx - 112
    a.ins(ADDIU("t1", "t1", -PAGE0_CAP))      # t1 = idx' (페이지1 내)
    _emit_sprite(a, page1=True)               # sprite 필드 채우기 (V부터 0)
    # a1 = s0 (sprite), 등록 준비
    # OT 엔트리 로드
    a.ins(LUI("a0", 0x1F80))
    a.ins(LW("a0", 0x03F4, "a0"))
    a.ins(NOP())                              # 로드 지연
    a.ins(ADDIU("a0", "a0", 4))               # a0 = OT 엔트리

    # (c) 설정 tpage(0x000D): sprite 뒤 메모리에 만들고 '마지막' 등록 -> 먼저 실행
    #     하지만 LIFO 위해선 등록 순서를 잘 잡아야.
    #     순서: 복원(0x0E) 등록 -> sprite 등록 -> 설정(0x0D) 등록
    #     메모리: sprite [s0..s0+20], tpageA [s0+20..+28], tpageB [s0+28..+36]
    #     tpageA = 복원(0x0E), tpageB = 설정(0x0D)

    # 복원 tpage(0x0E) 프리미티브 @ s0+0x14
    a.ins(LUI("t3", 0x0100))                  # tag = 0x01000000
    a.ins(SW("t3", 0x14, "s0"))
    a.ins(LUI("t3", 0xE100))
    a.ins(ORI("t3", "t3", TPAGE_ASCII))       # 0xE100000E
    a.ins(SW("t3", 0x18, "s0"))
    # 설정 tpage(0x0D) 프리미티브 @ s0+0x1C
    a.ins(LUI("t3", 0x0100))
    a.ins(SW("t3", 0x1C, "s0"))
    a.ins(LUI("t3", 0xE100))
    a.ins(ORI("t3", "t3", TPAGE_PAGE1))       # 0xE100000D
    a.ins(SW("t3", 0x20, "s0"))

    # 등록 1: 복원 tpage (s0+0x14)  a1 = s0+0x14
    a.ins(ADDIU("a1", "s0", 0x14))
    a.jal(REGFUNC)
    a.ins(NOP())
    # 등록 2: sprite (s0)  a1 = s0
    a.ins(MOVE("a1", "s0"))
    a.jal(REGFUNC)
    a.ins(NOP())
    # 등록 3: 설정 tpage (s0+0x1C)  a1 = s0+0x1C
    a.ins(ADDIU("a1", "s0", 0x1C))
    a.jal(REGFUNC)
    a.ins(NOP())

    # s0 전진: sprite20 + tpage8 + tpage8 = 36 (0x24)
    a.ins(ADDIU("s0", "s0", 0x24))
    # 카운터/좌표 전진
    a.ins(ADDIU("s4", "s4", 1))               # 글자수
    a.ins(ADDIU("s2", "s2", 14))              # X += 14
    a.ins(ADDIU("s3", "s3", 2))               # 2바이트 소비
    a.j(0x80036908)                           # 루프 복귀
    a.ins(NOP())

    # ---- 페이지0 (tpage 0x000E, 기존) ----
    a.label("L_page0")
    _emit_sprite(a, page1=False)              # V = 144 + (idx>>4)*15
    a.ins(MOVE("a1", "s0"))
    a.ins(ADDIU("s3", "s3", 1))               # 원본이 +1 더
    a.j(REG_PATH)                             # 원본 등록/전진 재활용
    a.ins(NOP())

    return a


def _emit_sprite(a, page1):
    """sprite 프리미티브 필드 채우기. t1 = idx (page1이면 idx').
    로드 지연 슬롯 준수. CLUT 로드 후 nop 필수."""
    # tag
    a.ins(LUI("v0", 0x0400))
    a.ins(SW("v0", 0x00, "s0"))
    a.ins(LUI("v1", 0x6480))
    a.ins(ORI("v1", "v1", 0x8080))
    a.ins(SW("v1", 0x04, "s0"))
    # X, Y
    a.ins(SH("s2", 0x08, "s0"))
    a.ins(SH("s6", 0x0A, "s0"))
    # U = (idx & 0x0F) * 16
    a.ins(ANDI("t2", "t1", 0x0F))
    a.ins(SLL("t2", "t2", 4))
    a.ins(SB("t2", 0x0C, "s0"))
    # V = base + (idx >> 4) * 15
    a.ins(SRL("t3", "t1", 4))
    a.ins(SLL("t4", "t3", 4))
    a.ins(SUBU("t4", "t4", "t3"))             # t4 = (idx>>4)*15
    if page1:
        a.ins(ADDIU("t4", "t4", 0))           # V base = 0 (페이지1 전체 사용)
    else:
        a.ins(ADDIU("t4", "t4", V_BASE))      # V base = 144
    a.ins(SB("t4", 0x0D, "s0"))
    # W, H
    a.ins(ADDIU("v0", "zero", 0x0E))
    a.ins(SH("v0", 0x10, "s0"))
    a.ins(ADDIU("v0", "zero", 0x0F))
    a.ins(SH("v0", 0x12, "s0"))
    # CLUT = [0x8007B454] + 0x7F86  (로드 지연 슬롯!)
    a.ins(LUI("v0", 0x8008))
    a.ins(LHU("v1", 0xB454, "v0"))
    a.ins(NOP())                              # ★ 로드 지연
    a.ins(ADDIU("v1", "v1", 0x7F86))
    a.ins(SH("v1", 0x0E, "s0"))


def patch_exe(exe_bytes, index_bytes):
    """index_bytes: 폭 13 으로 패치할 인덱스 바이트 리스트"""
    exe = bytearray(exe_bytes)
    a = build()
    viol = a.check_load_delay()
    if viol:
        for v in viol:
            print("로드지연 위반:", v)
        raise RuntimeError("로드 지연 슬롯 위반")
    hook = a.assemble()
    hoff = r2f(HOOK_ADDR)
    if not all(b == 0 for b in exe[hoff:hoff + len(hook)]):
        raise RuntimeError(f"훅 자리 부족 ({len(hook)}B)")
    if len(hook) > 833:
        raise RuntimeError(f"훅 너무 큼: {len(hook)}B")
    exe[hoff:hoff + len(hook)] = hook
    exe[r2f(HOOK_POINT):r2f(HOOK_POINT) + 4] = J(HOOK_ADDR)
    for code in index_bytes:
        exe[WIDTH_TBL + code] = 13
    return bytes(exe), hook


if __name__ == "__main__":
    a = build()
    viol = a.check_load_delay()
    print("로드지연 위반:", viol if viol else "없음")
    hook = a.assemble()
    print(f"훅 {len(hook)}B ({len(hook)//4} 명령)")
    a.disasm()
