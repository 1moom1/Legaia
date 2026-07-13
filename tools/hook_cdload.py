#!/usr/bin/env python3
"""PoC — DMY.DAT 에서 CdRead 로 폰트를 읽어 VRAM 에 올린다.

지금까지:
  - 동적 VRAM 전송은 검증됨 (LoadImage 시점, hook_dynload6)
  - CdRead(0x8005E4D4) 시그니처 확정: (a0=섹터수, a1=LBA, a2=버퍼), v0=1 성공

이 PoC 는 CD 읽기 자체를 검증한다:
  LoadImage 훅에서
    1. DMY.DAT 의 map6 폰트를 RAM 버퍼로 CdRead
    2. 그 버퍼를 그대로 VRAM 폰트 자리에 GP0 전송
  → 화면에 map6 폰트(자주 쓰는 660자)가 나타나면 CD 로딩 성공.

주의: CdRead 는 게임 CD 큐가 idle 일 때만 안전하다.
      LoadImage 시점은 맵 로드 직후라 대체로 idle 이지만,
      실패하면(v0!=1) 그냥 건너뛴다 (화면 안 깨지게).
"""
import struct
import sys

sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from mips import SLTU
from mips import hi16, lo16
from asm import Assembler, check_hook_site

LOAD, HDR = 0x80010000, 0x800
LI = 0x80059BD4
LI_BODY = 0x80059BDC
CD_READ = 0x8005E4D4          # CdRead(sectors, lba, buf) -> v0=1 성공
CD_SYNC = 0x8005EA84          # CdReadSync(mode, buf) -> 0이면 idle

HOOK = 0x8007ADA0
FLAG = 0x8007AF30
DONE = 0x8007AF34             # CD 로딩 완료 플래그 (한 번만)
COUNT = 0x8007AF38            # LoadImage 호출 카운터 (부팅 보호)

# 부팅 시퀀스(로고/타이틀)를 건너뛰기 위한 임계값.
# 이만큼 LoadImage 가 불린 뒤에야 CD 로딩을 시도한다.
BOOT_SKIP = 200

# DMY.DAT 의 map6 폰트
DMY_LBA = 180228
FONT_LBA = DMY_LBA + 1        # 섹터 0 은 인덱스
FONT_SECS = 24               # 660자 x 72B = 47,520B = 24섹터

# 읽어들일 RAM 버퍼 (게임이 안 쓰는 곳: 스크래치 위쪽 or 큰 여유)
#   0x801F0000 = RAM 상단, 맵 데이터 위. 임시 버퍼로 사용.
BUF = 0x801F0000

# VRAM 목적지: 한글 p1 (896, 144). 첫 줄부터 채운다.
VX, VY = 896, 144
VW, VH = 64, 120             # halfword. 660자를 20열*33행 = 396x12? 여기선 데모로 크게


def r2f(r):
    return r - LOAD + HDR


def build():
    a = Assembler(HOOK)

    # 재진입이면 원본으로
    a.ins(LUI("t0", hi16(FLAG)))
    a.ins(LW("t1", lo16(FLAG), "t0"))
    a.ins(NOP())
    a.bnez("t1", "L_orig")
    a.ins(NOP())
    a.ins(ADDIU("t1", "zero", 1))
    a.ins(SW("t1", lo16(FLAG), "t0"))

    a.ins(ADDIU("sp", "sp", -24))
    a.ins(SW("ra", 0, "sp"))
    a.ins(SW("s0", 4, "sp"))
    a.ins(SW("s1", 8, "sp"))

    # 원본 LoadImage 먼저
    a.jal(LI)
    a.ins(NOP())

    # --- 부팅 보호: LoadImage 카운터가 임계값 넘을 때까지 CD 안 건드림 ---
    a.ins(LUI("t0", hi16(COUNT)))
    a.ins(LW("t1", lo16(COUNT), "t0"))
    a.ins(NOP())
    a.ins(ADDIU("t1", "t1", 1))
    a.ins(SW("t1", lo16(COUNT), "t0"))
    a.ins(ADDIU("t2", "zero", BOOT_SKIP))
    a.ins(SLTU("t3", "t1", "t2"))         # t3 = (t1 < BOOT_SKIP)
    a.bnez("t3", "L_skip")                # 아직 부팅 중 -> 아무것도 안 함
    a.ins(NOP())

    # --- CD 로딩은 한 번만 (DONE 플래그) ---
    a.ins(LUI("t0", hi16(DONE)))
    a.ins(LW("t1", lo16(DONE), "t0"))
    a.ins(NOP())
    a.bnez("t1", "L_done_xfer")           # 이미 로드됨 -> 전송만
    a.ins(NOP())

    # --- CD 가 idle 인지 확인: CdReadSync(1, 0) != 0 이면 바쁨 -> 건너뜀 ---
    a.ins(ADDIU("a0", "zero", 1))         # non-blocking
    a.ins(ADDIU("a1", "zero", 0))
    a.jal(CD_SYNC)
    a.ins(NOP())
    a.bnez("v0", "L_skip")                # CD 바쁨 -> 다음 기회에
    a.ins(NOP())

    a.ins(LUI("t0", hi16(DONE)))
    a.ins(ADDIU("t1", "zero", 1))
    a.ins(SW("t1", lo16(DONE), "t0"))

    # CdRead(FONT_SECS, FONT_LBA, BUF)
    a.ins(ADDIU("a0", "zero", FONT_SECS))
    a.ins(LUI("a1", hi16(FONT_LBA)))
    a.ins(ADDIU("a1", "a1", lo16(FONT_LBA)))
    a.ins(LUI("a2", hi16(BUF)))
    a.ins(ADDIU("a2", "a2", lo16(BUF)))
    a.jal(CD_READ)
    a.ins(NOP())
    # v0 != 1 이면 실패 -> 건너뜀
    a.ins(ADDIU("t2", "zero", 1))
    a.bne("v0", "t2", "L_skip")           # CD 읽기 실패 -> 전송 안 함
    a.ins(NOP())

    # --- 읽기 성공: 버퍼를 VRAM 으로 GP0 전송 (성공했을 때만!) ---
    a.label("L_done_xfer")
    a.ins(LUI("t0", 0x1F80))
    a.ins(ADDIU("t0", "t0", 0x1810))       # GPU_DATA
    a.ins(LUI("t1", 0xA000))
    a.ins(SW("t1", 0, "t0"))
    a.ins(LUI("t1", VY))
    a.ins(ORI("t1", "t1", VX))
    a.ins(SW("t1", 0, "t0"))
    a.ins(ADDIU("t1", "zero", 12))
    a.ins(SLL("t1", "t1", 16))
    a.ins(ORI("t1", "t1", 64))
    a.ins(SW("t1", 0, "t0"))               # 64x12
    a.ins(LUI("t2", hi16(BUF)))
    a.ins(ADDIU("t2", "t2", lo16(BUF)))
    a.ins(ADDIU("t3", "zero", 64 * 12 // 2))   # 워드 수
    a.label("L_loop")
    a.ins(LW("t4", 0, "t2"))
    a.ins(NOP())
    a.ins(SW("t4", 0, "t0"))
    a.ins(ADDIU("t2", "t2", 4))
    a.ins(ADDIU("t3", "t3", -1))
    a.bnez("t3", "L_loop")
    a.ins(NOP())

    a.label("L_skip")
    # 락 해제 + 복귀
    a.ins(LUI("t0", hi16(FLAG)))
    a.ins(SW("zero", lo16(FLAG), "t0"))
    a.ins(LW("ra", 0, "sp"))
    a.ins(LW("s0", 4, "sp"))
    a.ins(LW("s1", 8, "sp"))
    a.ins(NOP())
    a.ins(ADDIU("sp", "sp", 24))
    a.ins(JR("ra"))
    a.ins(NOP())

    # 원본 본체
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
        for v in viol:
            print("  로드지연 위반:", v)
        raise RuntimeError("로드 지연 위반")
    code = a.assemble()
    if HOOK + len(code) > FLAG:
        raise RuntimeError(f"훅이 FLAG 침범: {HOOK+len(code):X} > {FLAG:X}")
    off = r2f(HOOK)
    exe[off:off + len(code)] = code
    exe[r2f(LI):r2f(LI) + 4] = J(HOOK)
    exe[r2f(LI) + 4:r2f(LI) + 8] = NOP()
    exe[r2f(FLAG):r2f(FLAG) + 4] = b"\x00" * 4
    exe[r2f(DONE):r2f(DONE) + 4] = b"\x00" * 4
    exe[r2f(COUNT):r2f(COUNT) + 4] = b"\x00" * 4
    check_hook_site(exe, r2f, LI, HOOK, handled_insns=2)
    return bytes(exe), code


if __name__ == "__main__":
    a = build()
    print("로드지연:", a.check_load_delay() or "없음")
    code = a.assemble()
    print(f"훅 {len(code)}B @ 0x{HOOK:08X}  (FLAG 까지 {FLAG-HOOK}B)")
    print(f"CdRead({FONT_SECS}, {FONT_LBA}, 0x{BUF:08X})")
    print(f"  DMY.DAT LBA {DMY_LBA} + 1 = {FONT_LBA}")
