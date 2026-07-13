#!/usr/bin/env python3
"""PoC6 — LoadImage 가 불릴 때마다 우리 폰트를 덮어쓴다.

PoC5 는 '첫 LoadImage' 에서 딱 한 번만 전송했는데, 게임이 그 **뒤에**
폰트 TIM 을 로드하면서 우리 데이터를 덮어버렸다. (대사가 멀쩡했던 이유)

-> 플래그를 재진입 방지용으로만 쓰고, **매 호출마다** 전송한다.
   그러면 게임이 폰트를 로드한 직후에도 우리가 다시 덮는다.

1·2차에서 배운 것:
  - LoadImage 시점에 VRAM 을 건드려도 **화면은 안 깨진다** (동적 로딩 가능!)
  - 하지만 **LoadImage 안에서 LoadImage 를 다시 부르면** 전투 모델이 깨진다.
    순서를 바꿔도(원본 먼저 → 우리 나중) 마찬가지.
    → LoadImage 는 재진입 불가(non-reentrant). 내부 전역 상태가 꼬인다.

그래서 이번엔 LoadImage 를 쓰지 않고 **GP0 명령을 직접** 보낸다.
게임이 LoadImage 를 막 끝낸 직후라 GPU 는 idle 이다.

  GP0 0xA0 = CPU -> VRAM 전송
     word0: 0xA0000000
     word1: (y << 16) | x        (halfword 단위 VRAM 좌표)
     word2: (h << 16) | w
     word3..: 픽셀 데이터 (32비트씩, halfword 2개)
"""
import struct
import sys

sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from mips import hi16, lo16
from asm import Assembler, check_hook_site

LOAD, HDR = 0x80010000, 0x800
LI = 0x80059BD4
# 🔴 딜레이 슬롯!
#   원본:  80059BD4: addiu sp, sp, -0x50
#          80059BD8: sw    s1, 0x34(sp)
#   0x80059BD4 를 `j HOOK` 으로 덮으면, 그 **딜레이 슬롯인 0x80059BD8 이
#   그대로 실행된다**. sp 를 아직 내리지 않았는데 sw 를 하니 호출자의
#   스택 프레임이 오염된다. (전투 모델이 깨진 진짜 원인)
#   -> 훅 안에서 원본 두 명령을 모두 수행하고 세 번째로 복귀한다.
LI_BODY = 0x80059BDC          # 원본 '세 번째' 명령

HOOK = 0x8007ADA0
FLAG = 0x8007AF30
RECT = 0x8007AF34         # 8B

GPU_DATA = 0x1F801810
GPU_STAT = 0x1F801814

# 덮어쓸 위치: 한글 p1 첫 줄 (음절 20개) — 전투 중에도 안전한 영역
VX, VY = 896, 144
VW, VH = 64, 12               # halfword
WORDS = VW * VH // 2          # 전송할 32비트 워드 수

SRC = 0x80011000              # EXE 코드 (0 이 아닌 값 → 노이즈로 잘 보임)


def r2f(r):
    return r - LOAD + HDR


def build():
    a = Assembler(HOOK)

    # 재진입이면 바로 원본 (무한 재귀 방지)
    a.ins(LUI("t0", hi16(FLAG)))
    a.ins(LW("t1", lo16(FLAG), "t0"))
    a.ins(NOP())
    a.bnez("t1", "L_orig")
    a.ins(NOP())
    a.ins(ADDIU("t1", "zero", 1))
    a.ins(SW("t1", lo16(FLAG), "t0"))     # 재진입 락

    # --- 1) 원본 LoadImage 를 먼저 완료 ---
    a.ins(ADDIU("sp", "sp", -16))
    a.ins(SW("ra", 0, "sp"))
    a.jal(LI)                              # 플래그가 서 있으므로 L_orig 로 감
    a.ins(NOP())

    # --- 2) 우리 RECT/데이터로 LoadImage 호출 ---
    #   딜레이 슬롯을 고친 지금은 재귀도 안전하다.
    #   (앞서 '재귀가 원인'이라 본 것은 오진이었다 — 진짜 원인은 스택 오염)
    a.ins(LUI("t2", hi16(RECT)))
    a.ins(ADDIU("t2", "t2", lo16(RECT)))
    a.ins(ADDIU("t3", "zero", VX))
    a.ins(SH("t3", 0, "t2"))
    a.ins(ADDIU("t3", "zero", VY))
    a.ins(SH("t3", 2, "t2"))
    a.ins(ADDIU("t3", "zero", VW))
    a.ins(SH("t3", 4, "t2"))
    a.ins(ADDIU("t3", "zero", VH))
    a.ins(SH("t3", 6, "t2"))

    a.ins(LUI("a0", hi16(RECT)))
    a.ins(ADDIU("a0", "a0", lo16(RECT)))
    a.ins(LUI("a1", hi16(SRC)))
    a.ins(ADDIU("a1", "a1", lo16(SRC)))
    a.jal(LI)
    a.ins(NOP())

    # --- 락 해제 (다음 LoadImage 때 또 덮어쓰기 위해) ---
    a.ins(LUI("t0", hi16(FLAG)))
    a.ins(SW("zero", lo16(FLAG), "t0"))

    # --- 호출자에게 복귀 ---
    a.ins(LW("ra", 0, "sp"))
    a.ins(NOP())
    a.ins(ADDIU("sp", "sp", 16))
    a.ins(JR("ra"))
    a.ins(NOP())

    # --- 원본 본체 ---
    #   원본 1: addiu sp, sp, -0x50
    #   원본 2: sw    s1, 0x34(sp)   ← j 의 딜레이 슬롯에 두면 sp 조정 후 실행된다
    a.label("L_orig")
    a.ins(ADDIU("sp", "sp", -0x50))
    a.j(LI_BODY)
    a.ins(SW("s1", 0x34, "sp"))       # ★ 딜레이 슬롯 (sp 조정 뒤라 안전)

    return a


def patch(exe_bytes):
    exe = bytearray(exe_bytes)
    a = build()
    viol = a.check_load_delay()
    if viol:
        for v in viol:
            print("  로드지연 위반:", v)
        raise RuntimeError("로드 지연 위반")
    code = a.assemble()
    if HOOK + len(code) > FLAG:
        raise RuntimeError(f"훅이 FLAG 침범: {HOOK+len(code):X} > {FLAG:X}")
    off = r2f(HOOK)
    if not all(b == 0 for b in exe[off:off + len(code)]):
        raise RuntimeError("훅 자리 안 비었음")
    exe[off:off + len(code)] = code
    # LoadImage 첫 명령 -> j 훅
    exe[r2f(LI):r2f(LI) + 4] = J(HOOK)
    # 🔴 딜레이 슬롯(원본 2번째 명령)을 nop 으로. 훅이 대신 수행한다.
    exe[r2f(LI) + 4:r2f(LI) + 8] = NOP()
    exe[r2f(FLAG):r2f(FLAG) + 4] = b"\x00" * 4
    # 🔴 딜레이 슬롯 가드 — 이 검사가 없어서 PoC 를 세 번 헛돌았다
    check_hook_site(exe, r2f, LI, HOOK, handled_insns=2)
    return bytes(exe), code


if __name__ == "__main__":
    a = build()
    print("로드지연:", a.check_load_delay() or "없음")
    code = a.assemble()
    print(f"훅 {len(code)}B @ 0x{HOOK:08X}  (FLAG 까지 {FLAG-HOOK}B)")
    print(f"GP0 0xA0 직접 전송: VRAM ({VX},{VY}) {VW}x{VH} halfword = {WORDS} 워드")
    print(f"소스: 0x{SRC:08X}")
    print()
    print("★ LoadImage 재귀를 쓰지 않는다 — 그게 전투 모델을 깨뜨린 원인")
