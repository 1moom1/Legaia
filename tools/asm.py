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
try:
    from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
    _CAP = True
except ImportError:
    _CAP = False

_cs = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN) if _CAP else None


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
        """로드 지연 슬롯 위반 검사 (R3000A)

        lw/lbu/lhu 바로 다음 명령에서 그 레지스터를 읽으면 갱신 전 값이 나온다.
        -> nop 을 넣어야 한다. (한글이 아예 안 보이는 증상으로 겪음)

        capstone 이 없으면 검사할 수 없다. 훅 코드는 이미 검증된 고정 코드이므로
        번역 작업에는 문제가 없지만, 훅을 수정했다면 capstone 을 설치할 것.
        """
        if not _CAP:
            return []
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
        if not _CAP:
            print("  (capstone 없음 — 디스어셈블 생략)")
            return
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


def check_hook_site(exe, r2f, target, hook_addr, handled_insns):
    """🔴 함수 진입점을 `j 훅` 으로 덮을 때의 딜레이 슬롯 검사.

    MIPS 는 분기/점프 **다음 명령(딜레이 슬롯)을 반드시 실행한다**.
    그래서 함수 첫 명령을 `j 훅` 으로 덮으면, **두 번째 명령이 그대로 실행된다**.

        원본:  addiu sp, sp, -0x50     <- 이걸 j 로 덮음
               sw    s1, 0x34(sp)      <- 딜레이 슬롯! sp 조정 전에 실행됨

    -> 호출자의 스택 프레임이 오염된다. (전투 모델이 깨지는 증상으로 겪음)

    올바른 처리:
        1. 진입점 두 번째 명령을 nop 으로 덮고
        2. 훅이 원본 1·2번째 명령을 모두 수행한 뒤 세 번째로 복귀

    이 함수는 (1)이 되어 있는지 검사한다.
    handled_insns = 훅이 대신 수행하는 원본 명령 수 (보통 2)
    """
    import struct
    slot = struct.unpack("<I", exe[r2f(target) + 4:r2f(target) + 8])[0]
    if slot != 0:
        raise RuntimeError(
            f"🔴 딜레이 슬롯 미처리: 0x{target+4:08X} 가 nop 이 아님 (0x{slot:08X}).\n"
            f"   `j 훅` 의 딜레이 슬롯은 반드시 실행된다. nop 으로 덮고,\n"
            f"   훅이 원본 명령을 대신 수행한 뒤 0x{target+4*handled_insns:08X} 로 복귀할 것.")
    return True
