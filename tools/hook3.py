#!/usr/bin/env python3
"""훅 v3 — 원본 등록/전진 코드 재활용 (안전)

v2 문제: 훅에서 jal 0x8003d2c4 (프리미티브 등록)을 직접 호출 -> 크래시.
         (0x8007AC38 은 실행되나 0x8007ACB0 에 도달 못함)

v3 해결: 훅은 프리미티브 '필드'만 채우고, 등록/전진은 원본에 맡긴다.

  원본 0x80036B88 부터:
     lui a0,0x1f80 ; lw a0,0x3f4(a0)   ; OT 포인터
     addiu s0,s0,0x14                   ; s0 += 20
     jal 0x8003d2c4                     ; 등록 (a1 = 프리미티브 시작)
     addiu a0,a0,4
     addiu s4,s4,1                      ; 글자수++
     lbu v1,(s3)                        ; 폭 계산용 바이트
     addiu s3,s3,1                      ; s3 += 1
     폭 = [0x80073F1C + v1]
     j 0x80036908 ; addu s2,s2,v1       ; X += 폭+1

  훅이 할 일:
     1. 프리미티브 필드 채우기 (U/V 는 음절식)
     2. a1 = s0
     3. s3 += 1   (원본이 +1 더 -> 총 2바이트 소비)
     4. j 0x80036B88

  => 원본이 [s3] = 인덱스바이트 를 읽어 폭 조회하므로,
     폭테이블[인덱스바이트] = 13 으로 패치 -> X += 14 ✓

마커: 0xCE <n>, n >= 0x90 이면 한글 (전처리가 2바이트로 통과시킴)
"""
import struct, sys
sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

LOAD, HDR = 0x80010000, 0x800
HOOK_ADDR = 0x8007AC00
HOOK_POINT = 0x80036960
GLYPH_PATH = 0x80036B24
CE_PATH = 0x80036968
REG_PATH = 0x80036B88        # 원본 등록/전진 코드
WIDTH_TBL = 0x6471C          # EXE 오프셋

HANGUL_MIN = 0x90
V_BASE = 144
HANGUL_WIDTH = 13            # X 전진 = 13 + 1 = 14

cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)


def r2f(r):
    return r - LOAD + HDR


def assemble_hook():
    A = HOOK_ADDR
    def at(i):
        return A + i * 4

    #  0  addiu t0, zero, 0xCE
    #  1  bne   v1, t0, L_glyph
    #  2  nop
    #  3  lbu   t1, 1(s3)          ; 인덱스
    #  4  addiu t0, zero, 0x90
    #  5  sltu  t2, t1, t0
    #  6  bnez  t2, L_ce           ; < 0x90 -> 원래 CE 기능
    #  7  nop
    #  8  j     L_hangul
    #  9  nop
    # 10 L_ce:   j CE_PATH
    # 11  nop
    # 12 L_glyph: j GLYPH_PATH
    # 13  nop
    # 14 L_hangul:
    L_ce = at(10)
    L_glyph = at(12)
    L_hangul = at(14)

    c = []
    c.append(ADDIU("t0", "zero", 0xCE))              # 0
    c.append(BNE("v1", "t0", at(1), L_glyph))        # 1
    c.append(NOP())                                  # 2
    c.append(LBU("t1", 1, "s3"))                     # 3
    c.append(ADDIU("t0", "zero", HANGUL_MIN))        # 4
    c.append(SLTU("t2", "t1", "t0"))                 # 5
    c.append(BNEZ("t2", at(6), L_ce))                # 6
    c.append(NOP())                                  # 7
    c.append(J(L_hangul))                            # 8
    c.append(NOP())                                  # 9
    c.append(J(CE_PATH))                             # 10
    c.append(NOP())                                  # 11
    c.append(J(GLYPH_PATH))                          # 12
    c.append(NOP())                                  # 13
    # --- L_hangul ---
    c.append(ADDIU("t1", "t1", -HANGUL_MIN))         # 14  idx = n - 0x90
    # 프리미티브 상수
    c.append(LUI("v0", 0x0400))                      # 15
    c.append(SW("v0", 0x00, "s0"))                   # 16  tag
    c.append(LUI("v1", 0x6480))                      # 17
    c.append(ORI("v1", "v1", 0x8080))                # 18
    c.append(SW("v1", 0x04, "s0"))                   # 19  cmd+color
    c.append(SH("s2", 0x08, "s0"))                   # 20  X
    c.append(SH("s6", 0x0A, "s0"))                   # 21  Y
    # U = (idx & 0xF) * 16
    c.append(ANDI("t2", "t1", 0x0F))                 # 22
    c.append(SLL("t2", "t2", 4))                     # 23
    c.append(SB("t2", 0x0C, "s0"))                   # 24
    # V = V_BASE + (idx >> 4) * 15
    c.append(SRL("t3", "t1", 4))                     # 25
    c.append(SLL("t4", "t3", 4))                     # 26
    c.append(SUBU("t4", "t4", "t3"))                 # 27
    c.append(ADDIU("t4", "t4", V_BASE))              # 28
    c.append(SB("t4", 0x0D, "s0"))                   # 29
    # W, H
    c.append(ADDIU("v0", "zero", 0x0E))              # 30
    c.append(SH("v0", 0x10, "s0"))                   # 31
    c.append(ADDIU("v0", "zero", 0x0F))              # 32
    c.append(SH("v0", 0x12, "s0"))                   # 33
    # CLUT = [0x8007B454] + 0x7F86
    # ★ MIPS 로드 지연 슬롯! lhu 바로 다음 명령에서 v1 을 읽으면
    #   아직 갱신 전 값을 읽는다 (R3000A). 반드시 1명령 이상 띄울 것.
    #   (원본 0x80036B74~0x80036B80 도 사이에 2명령을 끼워 넣음)
    c.append(LUI("v0", 0x8008))                      # 34
    c.append(LHU("v1", 0xB454, "v0"))                # 35  로드
    c.append(NOP())                                  # 36  ← 로드 지연 슬롯
    c.append(ADDIU("v1", "v1", 0x7F86))              # 37  이제 안전
    c.append(SH("v1", 0x0E, "s0"))                   # 38
    # 등록 인자 + s3 보정 후 원본으로
    c.append(MOVE("a1", "s0"))                       # 39  a1 = 프리미티브
    c.append(ADDIU("s3", "s3", 1))                   # 40  원본이 +1 더 -> 총 2
    c.append(J(REG_PATH))                            # 41  원본 등록/전진
    c.append(NOP())                                  # 42
    return b"".join(c)


def patch_exe(exe_bytes, n_syllables):
    exe = bytearray(exe_bytes)
    hook = assemble_hook()
    hoff = r2f(HOOK_ADDR)
    assert all(b == 0 for b in exe[hoff:hoff + len(hook)]), "훅자리 안비었음"
    exe[hoff:hoff + len(hook)] = hook
    exe[r2f(HOOK_POINT):r2f(HOOK_POINT) + 4] = J(HOOK_ADDR)
    # 폭 테이블: 인덱스 바이트(0x90 ~ 0x90+n) 의 폭 = 13
    for i in range(n_syllables):
        code = HANGUL_MIN + i
        exe[WIDTH_TBL + code] = HANGUL_WIDTH
    return bytes(exe), hook


if __name__ == "__main__":
    exe = open("/home/claude/legaia/cn/SCUS_US.exe", "rb").read()
    new, hook = patch_exe(exe, 16)
    open("/home/claude/legaia/build/SCUS_HOOK3.exe", "wb").write(new)
    print(f"훅 {len(hook)}B ({len(hook)//4} 명령)")
    for i in range(0, len(hook), 4):
        a = HOOK_ADDR + i
        ins = next(cs.disasm(hook[i:i+4], a), None)
        t = f"{ins.mnemonic} {ins.op_str}" if ins else "?"
        print(f"  {a:08X}: {t}")
