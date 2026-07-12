#!/usr/bin/env python3
"""2패스 MIPS 어셈블러 — 라벨 자동 해결

복잡한 훅(분기/점프 많음)을 안전하게 조립하기 위한 도구.
mips.py 의 인코더를 쓰되, 라벨을 심볼로 참조.

사용:
    asm = Assembler(base_addr)
    asm.label("loop")
    asm.ins(LBU("t1", 0, "s3"))
    asm.beq("t1", "zero", "done")
    asm.j("loop")
    asm.label("done")
    asm.ins(NOP())
    code = asm.assemble()

로드 지연 슬롯 검사도 내장.
"""
import struct, sys
sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN

_cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)


class Assembler:
    def __init__(self, base):
        self.base = base
        self.items = []      # ('ins', bytes) | ('label', name) |
                             # ('branch', kind, rs, rt, target) | ('jump', kind, target)

    def label(self, name):
        self.items.append(('label', name))

    def ins(self, b):
        self.items.append(('ins', b))

    # 분기(라벨 대상) — target 은 라벨명 또는 절대주소(int)
    def beq(self, rs, rt, target):
        self.items.append(('branch', 'beq', rs, rt, target))

    def bne(self, rs, rt, target):
        self.items.append(('branch', 'bne', rs, rt, target))

    def beqz(self, rs, target):
        self.items.append(('branch', 'beq', rs, 'zero', target))

    def bnez(self, rs, target):
        self.items.append(('branch', 'bne', rs, 'zero', target))

    def j(self, target):
        self.items.append(('jump', 'j', target))

    def jal(self, target):
        self.items.append(('jump', 'jal', target))

    def _resolve_positions(self):
        """각 항목의 주소를 계산하고 라벨 테이블 구성"""
        pos = {}
        addr = self.base
        for it in self.items:
            if it[0] == 'label':
                pos[it[1]] = addr
            else:
                addr += 4
        return pos

    def assemble(self):
        labels = self._resolve_positions()

        def resolve(target):
            if isinstance(target, str):
                if target not in labels:
                    raise KeyError(f"미정의 라벨: {target}")
                return labels[target]
            return target

        out = bytearray()
        addr = self.base
        for it in self.items:
            if it[0] == 'label':
                continue
            if it[0] == 'ins':
                out += it[1]
            elif it[0] == 'branch':
                _, kind, rs, rt, target = it
                t = resolve(target)
                if kind == 'beq':
                    out += BEQ(rs, rt, addr, t)
                else:
                    out += BNE(rs, rt, addr, t)
            elif it[0] == 'jump':
                _, kind, target = it
                t = resolve(target)
                out += (J(t) if kind == 'j' else JAL(t))
            addr += 4
        return bytes(out)

    def check_load_delay(self):
        """로드 지연 슬롯 위반 검사"""
        code = self.assemble()
        LOADS = {"lbu", "lhu", "lw", "lb", "lh"}
        insns = list(_cs.disasm(code, self.base))
        viol = []
        for i in range(len(insns) - 1):
            cur, nxt = insns[i], insns[i + 1]
            if cur.mnemonic in LOADS:
                dst = cur.op_str.split(",")[0].strip()
                # 다음 명령이 dst 를 소스로 읽는지 (대상은 제외)
                nxt_ops = nxt.op_str
                # 간단 검사: dst 가 나타나면 경고 (쓰기 대상이어도 로드지연 무해하나 보수적)
                if dst in nxt_ops:
                    viol.append((cur.address, f"{cur.mnemonic} {cur.op_str}",
                                 f"{nxt.mnemonic} {nxt.op_str}"))
        return viol

    def disasm(self):
        code = self.assemble()
        for ins in _cs.disasm(code, self.base):
            print(f"  {ins.address:08X}: {ins.mnemonic} {ins.op_str}")


if __name__ == "__main__":
    # 자체 검증: 간단한 루프
    a = Assembler(0x8007AC00)
    a.ins(ADDIU("t0", "zero", 0xCE))
    a.bne("v1", "t0", "glyph")
    a.ins(NOP())
    a.j(0x80036968)
    a.ins(NOP())
    a.label("glyph")
    a.j(0x80036B24)
    a.ins(NOP())
    viol = a.check_load_delay()
    print("로드지연 위반:", viol if viol else "없음")
    a.disasm()
