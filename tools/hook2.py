#!/usr/bin/env python3
"""훅 v2 — 마커를 0xCE 로 (전처리 자동 통과)

★ 핵심 발견:
  렌더러(0x80036888) 앞에 텍스트 전처리 함수(0x80036514)가 있음.
  전처리는 원본텍스트를 0x800740EC 버퍼로 복사하며:
    - (byte & 0xF0) == 0xC0  -> 2바이트 문자로 카운트/복사
    - 0xCF, 0xCE            -> 2바이트 그대로 복사
    - 0xC1~0xC7             -> 매크로(이름/아이템) 확장
    - 그 외                  -> 1바이트

  즉 게임에 '이미 2바이트 문자 체계'가 있음!
  이전 마커 0x92 는 이 범위 밖 -> 전처리가 1바이트로 쪼개서 텍스트 파괴 -> 실패.

  => 마커를 0xCE 로 쓰면 전처리를 2바이트로 자동 통과.
     렌더러가 0xCE 를 읽으면 우리 훅으로 진입.

  0xCE 원래 기능(테이블 조회, 0x80074050+n*4)은 대사에서 13회만 사용,
  다음 바이트 값이 {02,03,0B,0E,21,80} 뿐.
  => 0xCE <n> 에서 n >= 0x90 이면 한글, 아니면 원래 기능. (안전한 분리)

훅 지점: 0x80036960  (bne v1,v0,0x80036B24; v0=0xCE)
  원본은 여기서 v1==0xCE 면 CE경로(0x80036968)로 갔음.
  훅에서: v1==0xCE 이고 다음바이트>=0x90 -> 한글 렌더
          v1==0xCE 이고 다음바이트<0x90  -> 원래 CE경로
          v1!=0xCE                      -> 글리프경로
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
LOOP = 0x80036908
REGFUNC = 0x8003D2C4

HANGUL_MIN = 0x90        # 0xCE <n>, n >= 0x90 이면 한글
V_BASE = 144             # 음절 폰트 V 시작

cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)


def r2f(r):
    return r - LOAD + HDR


def assemble_hook():
    A = HOOK_ADDR
    def at(i):
        return A + i * 4

    # 레이아웃
    #  0  addiu t0, zero, 0xCE
    #  1  bne   v1, t0, L_glyph       ; v1 != 0xCE -> 글리프
    #  2  nop
    #  3  lbu   t1, 1(s3)             ; t1 = 다음 바이트 (인덱스)
    #  4  addiu t0, zero, HANGUL_MIN
    #  5  sltu  t2, t1, t0            ; t2 = (t1 < 0x90)
    #  6  bnez  t2, L_ce              ; t1 < 0x90 -> 원래 CE 기능
    #  7  nop
    #  8  j     L_hangul
    #  9  nop
    # 10 L_ce:  j CE_PATH
    # 11  nop
    # 12 L_glyph: j GLYPH_PATH        ; v0=0x20 (딜레이슬롯에서 설정됨)
    # 13  nop
    # 14 L_hangul: 프리미티브 생성 ...
    L_ce = at(10)
    L_glyph = at(12)
    L_hangul = at(14)

    c = []
    c.append(ADDIU("t0", "zero", 0xCE))              # 0
    c.append(BNE("v1", "t0", at(1), L_glyph))        # 1
    c.append(NOP())                                  # 2
    c.append(LBU("t1", 1, "s3"))                     # 3  인덱스
    c.append(ADDIU("t0", "zero", HANGUL_MIN))        # 4
    c.append(SLTU("t2", "t1", "t0"))                 # 5  t2 = t1 < 0x90
    c.append(BNEZ("t2", at(6), L_ce))                # 6
    c.append(NOP())                                  # 7
    c.append(J(L_hangul))                            # 8
    c.append(NOP())                                  # 9
    c.append(J(CE_PATH))                             # 10 L_ce
    c.append(NOP())                                  # 11
    c.append(J(GLYPH_PATH))                          # 12 L_glyph
    c.append(NOP())                                  # 13
    # --- L_hangul: 음절 프리미티브 ---
    # 인덱스 정규화: n = t1 - 0x90  (0..)
    c.append(ADDIU("t1", "t1", -HANGUL_MIN))         # 14
    # tag
    c.append(LUI("v0", 0x0400))                      # 15
    c.append(SW("v0", 0x00, "s0"))                   # 16
    # cmd + color
    c.append(LUI("v1", 0x6480))                      # 17
    c.append(ORI("v1", "v1", 0x8080))                # 18
    c.append(SW("v1", 0x04, "s0"))                   # 19
    # X, Y
    c.append(SH("s2", 0x08, "s0"))                   # 20
    c.append(SH("s6", 0x0A, "s0"))                   # 21
    # U = (n & 0x0F) * 16
    c.append(ANDI("t2", "t1", 0x0F))                 # 22
    c.append(SLL("t2", "t2", 4))                     # 23
    c.append(SB("t2", 0x0C, "s0"))                   # 24
    # V = V_BASE + (n >> 4) * 15
    c.append(SRL("t3", "t1", 4))                     # 25
    c.append(SLL("t4", "t3", 4))                     # 26  t4 = x*16
    c.append(SUBU("t4", "t4", "t3"))                 # 27  t4 = x*15
    c.append(ADDIU("t4", "t4", V_BASE))              # 28
    c.append(SB("t4", 0x0D, "s0"))                   # 29
    # W=14, H=15
    c.append(ADDIU("v0", "zero", 0x0E))              # 30
    c.append(SH("v0", 0x10, "s0"))                   # 31
    c.append(ADDIU("v0", "zero", 0x0F))              # 32
    c.append(SH("v0", 0x12, "s0"))                   # 33
    # CLUT = [0x8007B454] + 0x7F86
    c.append(LUI("v0", 0x8008))                      # 34
    c.append(LHU("v1", 0xB454, "v0"))                # 35
    c.append(ADDIU("v1", "v1", 0x7F86))              # 36
    c.append(SH("v1", 0x0E, "s0"))                   # 37
    # 등록
    c.append(MOVE("a1", "s0"))                       # 38
    c.append(LUI("a0", 0x1F80))                      # 39
    c.append(LW("a0", 0x03F4, "a0"))                 # 40
    c.append(ADDIU("s0", "s0", 0x14))                # 41
    c.append(JAL(REGFUNC))                           # 42
    c.append(ADDIU("a0", "a0", 4))                   # 43 [딜레이슬롯]
    # 전진
    c.append(ADDIU("s2", "s2", 14))                  # 44 X += 14
    c.append(ADDIU("s3", "s3", 2))                   # 45 2바이트 소비
    c.append(ADDIU("s4", "s4", 1))                   # 46 글자수++
    c.append(J(LOOP))                                # 47
    c.append(NOP())                                  # 48
    return b"".join(c)


def patch_exe(exe_bytes):
    exe = bytearray(exe_bytes)
    hook = assemble_hook()
    hoff = r2f(HOOK_ADDR)
    assert all(b == 0 for b in exe[hoff:hoff + len(hook)]), "훅자리 안비었음"
    assert len(hook) <= 833
    exe[hoff:hoff + len(hook)] = hook
    exe[r2f(HOOK_POINT):r2f(HOOK_POINT) + 4] = J(HOOK_ADDR)
    return bytes(exe), hook


if __name__ == "__main__":
    exe = open("/home/claude/legaia/cn/SCUS_US.exe", "rb").read()
    new, hook = patch_exe(exe)
    open("/home/claude/legaia/build/SCUS_HOOK2.exe", "wb").write(new)
    print(f"훅 {len(hook)}B ({len(hook)//4} 명령) @ 0x{HOOK_ADDR:08X}")
    for i in range(0, len(hook), 4):
        a = HOOK_ADDR + i
        ins = next(cs.disasm(hook[i:i+4], a), None)
        t = f"{ins.mnemonic} {ins.op_str}" if ins else "?"
        print(f"  {a:08X}: {struct.unpack('<I',hook[i:i+4])[0]:08X}  {t}")
