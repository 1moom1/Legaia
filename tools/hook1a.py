#!/usr/bin/env python3
"""1a: Pass-through 훅 — 훅 인프라 검증용

훅으로 점프했다가 원본 로직을 그대로 재현하고 복귀.
게임이 안 깨지면 = 훅 점프/복귀가 정상 = 인프라 검증 완료.

훅 지점 0x80036960: bne $v1,$v0,0x80036B24  (v1=문자, v0=0xCE)
  -> j 0x8007AC00 로 교체 (딜레이슬롯 0x80036964 죽은코드라 무해)

훅(0x8007AC00):
  진입: v1=문자, v0=0xCE
  beq $v1,$v0, +딜레이 -> 0xCE경로(0x80036968)
  j 글리프경로(0x80036B24)
"""
import struct
import sys
sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

LOAD, HDR = 0x80010000, 0x800
HOOK_ADDR = 0x8007AC00
HOOK_POINT = 0x80036960
GLYPH_PATH = 0x80036B24
CE_PATH = 0x80036968

EXE_IN = "/home/claude/legaia/cn/SCUS_US.exe"
EXE_OUT = "/home/claude/legaia/build/SCUS_HOOK.exe"
cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)


def r2f(r):
    return r - LOAD + HDR


def build_hook():
    # ★ 중요: 0x80036964 (addiu v0,zero,0x20) 는 원래 bne 의 딜레이슬롯.
    #   훅점프(j)로 바꿔도 딜레이슬롯이라 '여전히 실행'됨.
    #   => 훅 진입 시 v0 = 0x20 (0xCE 아님!)
    #   => v0 에 의존하지 말고 0xCE 를 직접 비교해야 함.
    #
    # 훅 진입: v1=문자, v0=0x20 (이미 세팅됨)
    #   AC00: addiu t0, zero, 0xCE
    #   AC04: bne v1, t0, GLYPH   ; v1 != 0xCE -> 글리프
    #   AC08:   nop
    #   AC0C: j CE_PATH            ; v1 == 0xCE
    #   AC10:   nop
    #   GLYPH (AC14): v0 는 이미 0x20 이므로 그대로 사용 가능
    #   AC14: j GLYPH_PATH
    #   AC18:   nop
    a = HOOK_ADDR
    glyph = HOOK_ADDR + 4 * 5
    code = []
    code.append(ADDIU("t0", "zero", 0xCE)); a += 4
    code.append(BNE("v1", "t0", a, glyph)); a += 4
    code.append(NOP()); a += 4
    code.append(J(CE_PATH)); a += 4
    code.append(NOP()); a += 4
    # glyph: (v0 == 0x20 이미 설정됨 — 딜레이슬롯에서)
    code.append(J(GLYPH_PATH)); a += 4
    code.append(NOP()); a += 4
    return b"".join(code)


def main():
    exe = bytearray(open(EXE_IN, "rb").read())
    hook = build_hook()

    print(f"[1] 훅 코드 {len(hook)}B:")
    for i in range(0, len(hook), 4):
        a = HOOK_ADDR + i
        ins = next(cs.disasm(hook[i:i+4], a))
        print(f"    {a:08X}: {struct.unpack('<I',hook[i:i+4])[0]:08X}  {ins.mnemonic} {ins.op_str}")

    hoff = r2f(HOOK_ADDR)
    assert all(b == 0 for b in exe[hoff:hoff+len(hook)]), "훅자리 안비었음"
    exe[hoff:hoff+len(hook)] = hook
    print(f"[2] 훅 자리 0x{HOOK_ADDR:08X} 기록")

    # 훅 지점 교체
    poff = r2f(HOOK_POINT)
    orig = struct.unpack("<I", exe[poff:poff+4])[0]
    oins = next(cs.disasm(struct.pack("<I", orig), HOOK_POINT))
    jmp = J(HOOK_ADDR)
    exe[poff:poff+4] = jmp
    print(f"[3] 0x{HOOK_POINT:08X}: {oins.mnemonic} {oins.op_str}  ->  j 0x{HOOK_ADDR:08X}")

    open(EXE_OUT, "wb").write(bytes(exe))
    print(f"[4] 저장 {EXE_OUT}")

    # 검증
    print("\n[5] 재디스어셈블 검증:")
    print("  훅 지점:")
    for a in (HOOK_POINT, HOOK_POINT+4):
        w = exe[r2f(a):r2f(a)+4]
        ins = next(cs.disasm(w, a))
        print(f"    {a:08X}: {ins.mnemonic} {ins.op_str}")
    print("  훅 코드:")
    for i in range(0, len(hook), 4):
        a = HOOK_ADDR + i
        w = exe[r2f(a):r2f(a)+4]
        ins = next(cs.disasm(w, a))
        print(f"    {a:08X}: {ins.mnemonic} {ins.op_str}")


if __name__ == "__main__":
    main()
