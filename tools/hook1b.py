#!/usr/bin/env python3
"""1b: 2바이트 마커 + 음절 렌더 훅

인코딩: 0x92 <n> -> 음절 인덱스 n (0~111)
음절 폰트: ASCII 페이지(896,0)의 빈 영역 V144~255
  U = (n & 0x0F) * 16
  V = 144 + (n >> 4) * 15

훅 지점 0x80036960 -> j HOOK.
훅 진입: v1=문자, v0=0x20(딜레이슬롯), s3=텍스트, s0=프리미티브, s2=X, s6=Y
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
MARKER = 0x92

EXE_IN = "/home/claude/legaia/cn/SCUS_US.exe"
EXE_OUT = "/home/claude/legaia/build/SCUS_HOOK.exe"
cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)


def r2f(r):
    return r - LOAD + HDR


def build_hook():
    """훅 기계어. 주소 라벨은 HOOK_ADDR 기준 순차 계산."""
    ins = []
    def emit(b): ins.append(b)
    # 현재 주소 = HOOK_ADDR + len(ins)*4

    # --- 디스패치 ---
    # 진입: v1=문자, v0=0x20
    # 0xCE 검사
    emit(ADDIU("t0", "zero", 0xCE))               # +0
    # v1 != 0xCE -> check marker
    # 분기 타겟은 나중에 채우기 위해 위치 계산 필요.
    # 레이아웃을 고정하기 위해 먼저 명령 배열을 주소와 함께 구성.

    # 명령을 (함수, 인자) 지연평가 대신, 2패스로:
    # 1패스에서 라벨 위치 확정, 2패스에서 분기 인코딩.
    return None  # placeholder, 실제는 아래 assemble_hook 사용


def assemble_hook():
    A = HOOK_ADDR
    # 라벨 오프셋(명령 인덱스)
    # 순서:
    #  L_start:
    #   0 addiu t0,zero,0xCE
    #   1 bne v1,t0, L_checkmark      (v1!=CE)
    #   2 nop
    #   3 j CE_PATH
    #   4 nop
    #  L_checkmark:
    #   5 addiu t0,zero,0x92
    #   6 bne v1,t0, L_glyph          (v1!=marker)
    #   7 nop
    #   8 j L_hangul
    #   9 nop
    #  L_glyph:
    #   10 j GLYPH_PATH               (v0=0x20 이미 설정됨)
    #   11 nop
    #  L_hangul:
    #   12.. 프리미티브 생성
    def addr(idx):
        return A + idx * 4

    L_checkmark = addr(5)
    L_glyph = addr(10)
    L_hangul = addr(12)

    code = []
    # 0
    code.append(ADDIU("t0", "zero", 0xCE))
    # 1
    code.append(BNE("v1", "t0", addr(1), L_checkmark))
    # 2
    code.append(NOP())
    # 3
    code.append(J(CE_PATH))
    # 4
    code.append(NOP())
    # 5 L_checkmark
    code.append(ADDIU("t0", "zero", MARKER))
    # 6
    code.append(BNE("v1", "t0", addr(6), L_glyph))
    # 7
    code.append(NOP())
    # 8
    code.append(J(L_hangul))
    # 9
    code.append(NOP())
    # 10 L_glyph
    code.append(J(GLYPH_PATH))
    # 11
    code.append(NOP())
    # 12 L_hangul: 프리미티브 생성 시작
    # tag = 0x04000000
    code.append(LUI("v0", 0x0400))          # 12
    code.append(SW("v0", 0x00, "s0"))       # 13
    # cmd+color = 0x64808080
    code.append(LUI("v1", 0x6480))          # 14
    code.append(ORI("v1", "v1", 0x8080))    # 15
    code.append(SW("v1", 0x04, "s0"))       # 16
    # X, Y
    code.append(SH("s2", 0x08, "s0"))       # 17
    code.append(SH("s6", 0x0A, "s0"))       # 18
    # n = [s3+1]
    code.append(LBU("t1", 1, "s3"))         # 19
    # U = (n & 0xF) * 16
    code.append(ANDI("t2", "t1", 0x0F))     # 20
    code.append(SLL("t2", "t2", 4))         # 21
    code.append(SB("t2", 0x0C, "s0"))       # 22
    # V = 144 + (n>>4)*15
    code.append(SRL("t3", "t1", 4))         # 23
    code.append(SLL("t4", "t3", 4))         # 24  t4 = x*16
    code.append(SUBU("t4", "t4", "t3"))     # 25  t4 = x*15
    code.append(ADDIU("t4", "t4", 144))     # 26
    code.append(SB("t4", 0x0D, "s0"))       # 27
    # W=14, H=15
    code.append(ADDIU("v0", "zero", 0x0E))  # 28
    code.append(SH("v0", 0x10, "s0"))       # 29
    code.append(ADDIU("v0", "zero", 0x0F))  # 30
    code.append(SH("v0", 0x12, "s0"))       # 31
    # CLUT = [0x8007B454] + 0x7F86
    code.append(LUI("v0", 0x8008))          # 32
    code.append(LHU("v1", 0xB454, "v0"))    # 33  (-0x4BAC = 0xB454 as u16)
    code.append(ADDIU("v1", "v1", 0x7F86))  # 34
    code.append(SH("v1", 0x0E, "s0"))       # 35
    # 등록: a1=s0, a0=[0x1F8003F4]
    code.append(MOVE("a1", "s0"))           # 36
    code.append(LUI("a0", 0x1F80))          # 37
    code.append(LW("a0", 0x03F4, "a0"))     # 38
    code.append(ADDIU("s0", "s0", 0x14))    # 39  s0 += 20
    code.append(JAL(REGFUNC))               # 40
    code.append(ADDIU("a0", "a0", 4))       # 41  [딜레이슬롯]
    # 전진
    code.append(ADDIU("s2", "s2", 14))      # 42  X += 14
    code.append(ADDIU("s3", "s3", 2))       # 43  2바이트 소비
    code.append(ADDIU("s4", "s4", 1))       # 44  문자수++
    code.append(J(LOOP))                     # 45
    code.append(NOP())                       # 46
    return b"".join(code)


def main():
    exe = bytearray(open(EXE_IN, "rb").read())
    hook = assemble_hook()

    print(f"[1] 훅 코드 {len(hook)}B ({len(hook)//4} 명령)")
    for i in range(0, len(hook), 4):
        a = HOOK_ADDR + i
        ins = next(cs.disasm(hook[i:i+4], a), None)
        t = f"{ins.mnemonic} {ins.op_str}" if ins else "?"
        print(f"    {a:08X}: {struct.unpack('<I',hook[i:i+4])[0]:08X}  {t}")

    hoff = r2f(HOOK_ADDR)
    assert all(b == 0 for b in exe[hoff:hoff+len(hook)]), "훅자리 안비었음"
    assert len(hook) <= 833, "훅 너무 큼"
    exe[hoff:hoff+len(hook)] = hook

    poff = r2f(HOOK_POINT)
    exe[poff:poff+4] = J(HOOK_ADDR)
    print(f"[2] 0x{HOOK_POINT:08X} -> j 0x{HOOK_ADDR:08X}")

    open(EXE_OUT, "wb").write(bytes(exe))
    print(f"[3] 저장 {EXE_OUT}")


if __name__ == "__main__":
    main()
