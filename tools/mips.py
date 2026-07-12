#!/usr/bin/env python3
"""MIPS 명령 수동 인코더 (리틀엔디안).

keystone 의 라벨/딜레이슬롯 자동처리가 불확실하므로,
분기/점프 대상을 직접 계산해 정확한 워드를 만든다.
레지스터는 숫자: zero=0, at=1, v0=2, v1=3, a0=4..a3=7,
                t0=8..t7=15, s0=16..s7=23, t8=24,t9=25, sp=29, ra=31
"""
import struct

# 레지스터 이름 -> 번호
R = {"zero":0,"at":1,"v0":2,"v1":3,"a0":4,"a1":5,"a2":6,"a3":7,
     "t0":8,"t1":9,"t2":10,"t3":11,"t4":12,"t5":13,"t6":14,"t7":15,
     "s0":16,"s1":17,"s2":18,"s3":19,"s4":20,"s5":21,"s6":22,"s7":23,
     "t8":24,"t9":25,"k0":26,"k1":27,"gp":28,"sp":29,"fp":30,"ra":31}


def _w(v):
    return struct.pack("<I", v & 0xFFFFFFFF)


# R-type  (sh = shift amount, bits 6-10)
def _r(op, rs, rt, rd, sh, fn):
    return _w((op << 26) | (R[rs] << 21) | (R[rt] << 16) | (R[rd] << 11) | (sh << 6) | fn)


def _i(op, rs, rt, imm):
    return _w((op << 26) | (R[rs] << 21) | (R[rt] << 16) | (imm & 0xFFFF))


def _j(op, target):
    return _w((op << 26) | ((target >> 2) & 0x03FFFFFF))


# 개별 명령
def NOP():                 return _w(0)
def LUI(rt, imm):          return _i(0x0F, "zero", rt, imm)
def ORI(rt, rs, imm):      return _i(0x0D, rs, rt, imm)
def ADDIU(rt, rs, imm):    return _i(0x09, rs, rt, imm)
def ANDI(rt, rs, imm):     return _i(0x0C, rs, rt, imm)
def LBU(rt, off, base):    return _i(0x24, base, rt, off)
def LHU(rt, off, base):    return _i(0x25, base, rt, off)
def LW(rt, off, base):     return _i(0x23, base, rt, off)
def SB(rt, off, base):     return _i(0x28, base, rt, off)
def SH(rt, off, base):     return _i(0x29, base, rt, off)
def SW(rt, off, base):     return _i(0x2B, base, rt, off)
def SLL(rd, rt, sh):       return _r(0, "zero", rt, rd, sh, 0)
def SRL(rd, rt, sh):       return _r(0, "zero", rt, rd, sh, 2)
def ADDU(rd, rs, rt):      return _r(0, rs, rt, rd, 0, 0x21)
def SUBU(rd, rs, rt):      return _r(0, rs, rt, rd, 0, 0x23)
def OR(rd, rs, rt):        return _r(0, rs, rt, rd, 0, 0x25)
def JR(rs):                return _r(0, rs, "zero", "zero", 0, 8)


def J(target_addr):        return _j(0x02, target_addr)
def JAL(target_addr):      return _j(0x03, target_addr)


def BEQ(rs, rt, cur_addr, target_addr):
    off = ((target_addr - (cur_addr + 4)) >> 2) & 0xFFFF
    return _i(0x04, rs, rt, off)


def BNE(rs, rt, cur_addr, target_addr):
    off = ((target_addr - (cur_addr + 4)) >> 2) & 0xFFFF
    return _i(0x05, rs, rt, off)


def BEQZ(rs, cur_addr, target_addr):
    return BEQ(rs, "zero", cur_addr, target_addr)


def BNEZ(rs, cur_addr, target_addr):
    return BNE(rs, "zero", cur_addr, target_addr)


def assemble(instrs):
    """[(mnemonic_bytes)] 리스트를 이어붙임"""
    return b"".join(instrs)


if __name__ == "__main__":
    # 자체 검증: 알려진 원본 명령과 비교
    from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
    cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    tests = [
        (ADDIU("v0", "zero", 0x20), 0x24020020, "addiu v0,zero,0x20"),
        (LBU("v1", 1, "s3"),        0x92630001, "lbu v1,1(s3)"),
        (SLL("v1", "v1", 2),        0x00031880, "sll v1,v1,2"),
        (ANDI("v0", "v0", 0xF),     0x3042000F, "andi v0,v0,0xf"),
        (LUI("v0", 0x8007),         0x3C028007, "lui v0,0x8007"),
        (SB("v0", 0xC, "s0"),       0xA202000C, "sb v0,0xc(s0)"),
        (J(0x8007AC00),             0x0801EB00, "j 0x8007ac00"),
    ]
    allok = True
    for got_b, expect, name in tests:
        got = struct.unpack("<I", got_b)[0]
        ok = got == expect
        allok &= ok
        print(f"  {name:22s} {got:08X} {'OK' if ok else f'!= {expect:08X}'}")
    print("전부 일치!" if allok else "불일치!")


def MOVE(rd, rs):          return ADDU(rd, rs, "zero")


def SLTU(rd, rs, rt):      return _r(0, rs, rt, rd, 0, 0x2B)
def SLTIU(rt, rs, imm):    return _i(0x0B, rs, rt, imm)
