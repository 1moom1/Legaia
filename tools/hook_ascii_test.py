#!/usr/bin/env python3
"""ASCII 영역 회수 실험 — 뭐가 깨지는지 본다.

580칸을 얻으려면 ASCII 폰트 영역 (896, V0~143) 을 한글로 덮어야 한다.
그런데 ASCII 를 지우면 **영어가 안 나온다**. 어디에 영어가 남아 있나?

이 PoC 는 ASCII 영역 전체를 노이즈로 덮는다. 게임을 돌려서:
  - 대사        → 이미 한글이라 멀쩡해야 함 (한글은 V144~)
  - 숫자(HP/MP) → 다른 TIM(960,256)이라 멀쩡해야 함
  - ★ 메뉴/아이템/전투 → 깨지면 = 한글화가 필요하다는 증거
  - ★ 안 깨지면 = ASCII 를 그냥 회수할 수 있다 (횡재)

검증된 hook_dynload6 방식(LoadImage 훅 + 매 호출 전송)을 그대로 쓴다.
"""
import sys

sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from mips import hi16, lo16
from asm import Assembler, check_hook_site

LOAD, HDR = 0x80010000, 0x800
LI = 0x80059BD4
LI_BODY = 0x80059BDC

HOOK = 0x8007ADA0
FLAG = 0x8007AF30

# ★ ASCII 폰트 영역 전체 (896, V0~143)
VX, VY = 896, 0
VW, VH = 64, 144          # halfword. 64x144 = 9,216 halfword

SRC = 0x80011000          # EXE 코드 = 노이즈로 잘 보임

WORDS = VW * VH // 2      # 4,608 워드


def r2f(r):
    return r - LOAD + HDR


def build():
    a = Assembler(HOOK)

    # 재진입이면 원본 (무한 재귀 방지)
    a.ins(LUI("t0", hi16(FLAG)))
    a.ins(LW("t1", lo16(FLAG), "t0"))
    a.ins(NOP())
    a.bnez("t1", "L_orig")
    a.ins(NOP())
    a.ins(ADDIU("t1", "zero", 1))
    a.ins(SW("t1", lo16(FLAG), "t0"))

    a.ins(ADDIU("sp", "sp", -16))
    a.ins(SW("ra", 0, "sp"))

    # 원본 LoadImage 먼저 완료
    a.jal(LI)
    a.ins(NOP())

    # ASCII 영역을 노이즈로 덮는다 (GP0 직접 전송)
    a.ins(LUI("t0", 0x1F80))
    a.ins(ADDIU("t0", "t0", 0x1810))       # GPU_DATA
    a.ins(LUI("t1", 0xA000))
    a.ins(SW("t1", 0, "t0"))               # GP0 0xA0
    a.ins(LUI("t1", VY))
    a.ins(ORI("t1", "t1", VX))
    a.ins(SW("t1", 0, "t0"))               # (y<<16)|x
    a.ins(LUI("t1", VH))
    a.ins(ORI("t1", "t1", VW))
    a.ins(SW("t1", 0, "t0"))               # (h<<16)|w
    a.ins(LUI("t2", hi16(SRC)))
    a.ins(ADDIU("t2", "t2", lo16(SRC)))
    a.ins(LUI("t3", (WORDS >> 16) & 0xFFFF))
    a.ins(ORI("t3", "t3", WORDS & 0xFFFF))

    a.label("L_loop")
    a.ins(LW("t4", 0, "t2"))
    a.ins(NOP())
    a.ins(SW("t4", 0, "t0"))
    a.ins(ADDIU("t2", "t2", 4))
    a.ins(ADDIU("t3", "t3", -1))
    a.bnez("t3", "L_loop")
    a.ins(NOP())

    # 락 해제 (매 호출마다 덮어써야 게임 폰트를 이긴다)
    a.ins(LUI("t0", hi16(FLAG)))
    a.ins(SW("zero", lo16(FLAG), "t0"))

    a.ins(LW("ra", 0, "sp"))
    a.ins(NOP())
    a.ins(ADDIU("sp", "sp", 16))
    a.ins(JR("ra"))
    a.ins(NOP())

    # 원본 본체 (딜레이 슬롯 처리)
    a.label("L_orig")
    a.ins(ADDIU("sp", "sp", -0x50))
    a.j(LI_BODY)
    a.ins(SW("s1", 0x34, "sp"))

    return a


def patch(exe_bytes):
    exe = bytearray(exe_bytes)
    a = build()
    viol = a.check_load_delay()
    if viol:
        raise RuntimeError(f"로드 지연 위반: {viol}")
    code = a.assemble()
    if HOOK + len(code) > FLAG:
        raise RuntimeError("훅이 FLAG 침범")
    off = r2f(HOOK)
    exe[off:off + len(code)] = code
    exe[r2f(LI):r2f(LI) + 4] = J(HOOK)
    exe[r2f(LI) + 4:r2f(LI) + 8] = NOP()
    exe[r2f(FLAG):r2f(FLAG) + 4] = b"\x00" * 4
    check_hook_site(exe, r2f, LI, HOOK, handled_insns=2)
    return bytes(exe), code


if __name__ == "__main__":
    a = build()
    print("로드지연:", a.check_load_delay() or "없음")
    code = a.assemble()
    print(f"훅 {len(code)}B @ 0x{HOOK:08X}")
    print(f"덮는 영역: VRAM ({VX},{VY}) {VW}x{VH} halfword = ASCII 폰트 전체")
    print(f"  = {WORDS:,} 워드 전송")
    print()
    print("확인할 것:")
    print("  대사(한글)   → 멀쩡해야 정상")
    print("  숫자(HP/MP)  → 멀쩡해야 정상")
    print("  ★ 메뉴/아이템/전투 → 깨지면 한글화 필요")
