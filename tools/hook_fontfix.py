#!/usr/bin/env python3
"""폰트 복구 훅 — 게임이 폰트 영역을 덮어써도 되살린다.

## 왜 필요한가

VRAM 을 4가지 상태(평소/전투/일본판)로 비교해 보니, 우리가 한글을 넣은 자리는
**게임이 자기 데이터로 쓰는 곳**이었다:

    p1 (ASCII V144~215) : 원본 4041px  🔴 게임이 덮는다
    p2 (숫자  V80~143)  : 원본    0px  ⚠ 전투 중에만 덮인다
    p3 (숫자  V0~79)    : 원본 1015px  🔴 아이콘 TIM 이 덮는다
    p4 (숫자  V168~191) : 원본    0px  ✅ 안전

증상: 글자 한 칸이 캐릭터 그림 조각으로 바뀐다. ('얘', '쁘' 가 깨졌다)
안전한 칸은 p2+p4 = 140칸뿐인데, map6 하나만 476자가 필요하다.

## 어떻게 고치나

**게임이 덮어써도, 대사를 그리기 직전에 폰트를 다시 VRAM 에 올리면 된다.**

폰트 데이터를 EXE 에 넣을 자리는 없다 (수십 KB). 그럴 필요도 없다 —
게임이 이미 폰트 TIM 을 RAM 에 올려두고 LoadImage 로 VRAM 에 보낸다.
그 **데이터 주소를 가로채 저장**해 두었다가, 대사 직전에 **LoadImage 를 다시 호출**한다.
게임의 DMA 전송을 그대로 쓰므로 빠르다. (GP0 폴링으로 직접 보내면 부팅이 멈춘다)

## 두 개의 훅

1) LoadImage (0x80059BD4) 훅
   RECT 좌표를 보고 폰트 TIM 이면 데이터 주소를 저장한다.
     (896,0)   64x256 → ASCII TIM  → FONT_A
     (960,256) 64x256 → 숫자 TIM   → FONT_N

2) 대사 렌더러 (0x80036888) 진입 훅
   저장해 둔 주소로 LoadImage 를 다시 호출해 **한글 페이지만** 복구한다.
     p1: ASCII TIM 의 V144~215  (RECT 896,144 64x72)
     p3: 숫자  TIM 의 V0~79     (RECT 960,256 64x80)
   p2/p4 는 게임이 필드에서 안 건드리므로 복구하지 않는다 (전송량 절약).

   TIM 픽셀은 1행 = 64 halfword = 128B. V행 오프셋 = V * 128.

🔴 딜레이 슬롯: 두 훅 모두 함수 진입점을 덮으므로, 원본의 첫 두 명령을
   훅이 대신 수행하고 세 번째 명령으로 복귀해야 한다. (asm.check_hook_site 가 강제)
"""
import sys

sys.path.insert(0, "/home/claude/legaia/tools")
from mips import *
from mips import hi16, lo16
from asm import Assembler, check_hook_site

LOAD, HDR = 0x80010000, 0x800

# ── 훅 대상 ──────────────────────────────────────────────
LI = 0x80059BD4            # LoadImage(a0=RECT*, a1=data)
LI_BODY = 0x80059BDC       # 원본 3번째 명령 (진입 2명령은 훅이 수행)

DLG = 0x80036888           # 대사 렌더러 진입
DLG_BODY = 0x80036890

# ── 우리 코드/데이터 자리 ────────────────────────────────
# 🔴 EXE 안의 '0 으로 채워진 큰 블록'(0x800797D0 등)은 **안전하지 않다**.
#    비어 보이지만 게임이 런타임 작업 영역으로 쓴다.
#    거기 훅을 두었더니 실행 중 코드가 게임 데이터로 덮여
#    "Unknown instruction for dynarec - address 800797d8" 로 부팅이 죽었다.
#
# ✅ hook7 이 쓰는 검증된 영역(0x8007AC00~0x8007AF41, 833B)의 뒤쪽을 쓴다.
#    hook7 대사훅이 416B 를 쓰므로 0x8007ADA0 부터 417B 가 남는다.
BASE = 0x8007ADA0
HOOK_LI = BASE             # LoadImage 훅   (92B)
HOOK_DLG = BASE + 0x60     # 대사 훅       (188B)
DATA = BASE + 0x120        # 변수/RECT     (32B)

FONT_A = DATA + 0          # ASCII TIM 데이터 주소
FONT_N = DATA + 4          # 숫자 TIM 데이터 주소
LOCK = DATA + 8            # LoadImage 재진입 락
RECT_P1 = DATA + 16        # (896,144,64,72)
RECT_P3 = DATA + 24        # (960,256,64,80)

LIMIT = 0x8007AF41 - BASE  # 417B


def r2f(r):
    return r - LOAD + HDR


# ── TIM 픽셀 행 오프셋 (1행 = 128B) ──────────────────────
ROW = 128
P1_V = 144                 # ASCII TIM 안에서의 V
P3_V = 0                   # 숫자 TIM 안에서의 V


def build_loadimage_hook():
    """LoadImage 진입 훅 — 폰트 TIM 이면 데이터 주소를 기억한다."""
    a = Assembler(HOOK_LI)

    # RECT 를 읽는다: a0 -> {u16 x, u16 y, u16 w, u16 h}
    a.ins(LHU("t0", 0, "a0"))       # x
    a.ins(LHU("t1", 2, "a0"))       # y
    a.ins(NOP())                    # 🔴 로드 지연 슬롯

    # ASCII TIM ? (x==896 && y==0)
    a.ins(ADDIU("t2", "zero", 896))
    a.bne("t0", "t2", "L_chk_num")
    a.ins(NOP())
    a.bnez("t1", "L_chk_num")       # y != 0 이면 아님
    a.ins(NOP())
    a.ins(LUI("t3", hi16(FONT_A)))
    a.ins(SW("a1", lo16(FONT_A), "t3"))
    a.j("L_orig")
    a.ins(NOP())

    # 숫자 TIM ? (x==960 && y==256)
    a.label("L_chk_num")
    a.ins(ADDIU("t2", "zero", 960))
    a.bne("t0", "t2", "L_orig")
    a.ins(NOP())
    a.ins(ADDIU("t2", "zero", 256))
    a.bne("t1", "t2", "L_orig")
    a.ins(NOP())
    a.ins(LUI("t3", hi16(FONT_N)))
    a.ins(SW("a1", lo16(FONT_N), "t3"))

    # 원본 LoadImage 본체로 (진입 2명령을 대신 수행)
    a.label("L_orig")
    a.ins(ADDIU("sp", "sp", -0x50))
    a.j(LI_BODY)
    a.ins(SW("s1", 0x34, "sp"))
    return a


def build_dialog_hook():
    """대사 렌더러 진입 훅 — 한글 페이지를 VRAM 에 다시 올린다."""
    a = Assembler(HOOK_DLG)

    a.ins(ADDIU("sp", "sp", -32))
    a.ins(SW("ra", 0, "sp"))
    a.ins(SW("a0", 4, "sp"))
    a.ins(SW("a1", 8, "sp"))
    a.ins(SW("a2", 12, "sp"))
    a.ins(SW("a3", 16, "sp"))

    # 재진입 방지 (우리가 부르는 LoadImage 가 다시 이 훅을 타면 안 된다)
    a.ins(LUI("t0", hi16(LOCK)))
    a.ins(LW("t1", lo16(LOCK), "t0"))
    a.ins(NOP())
    a.bnez("t1", "L_done")
    a.ins(NOP())
    a.ins(ADDIU("t1", "zero", 1))
    a.ins(SW("t1", lo16(LOCK), "t0"))

    # ── p1 복구: ASCII TIM 의 V144~215 ──
    a.ins(LUI("t0", hi16(FONT_A)))
    a.ins(LW("t2", lo16(FONT_A), "t0"))
    a.ins(NOP())
    a.beqz("t2", "L_p3")             # 아직 주소를 못 잡았으면 건너뛴다
    a.ins(NOP())
    a.ins(LUI("a0", hi16(RECT_P1)))
    a.ins(ADDIU("a0", "a0", lo16(RECT_P1)))
    a.ins(LUI("t3", (P1_V * ROW) >> 16))
    a.ins(ORI("t3", "t3", (P1_V * ROW) & 0xFFFF))
    a.ins(ADDU("a1", "t2", "t3"))
    a.jal(LI)
    a.ins(NOP())

    # ── p3 복구: 숫자 TIM 의 V0~79 ──
    a.label("L_p3")
    a.ins(LUI("t0", hi16(FONT_N)))
    a.ins(LW("t2", lo16(FONT_N), "t0"))
    a.ins(NOP())
    a.beqz("t2", "L_unlock")
    a.ins(NOP())
    a.ins(LUI("a0", hi16(RECT_P3)))
    a.ins(ADDIU("a0", "a0", lo16(RECT_P3)))
    a.ins(MOVE("a1", "t2"))          # V0 이므로 오프셋 0
    a.jal(LI)
    a.ins(NOP())

    a.label("L_unlock")
    a.ins(LUI("t0", hi16(LOCK)))
    a.ins(SW("zero", lo16(LOCK), "t0"))

    a.label("L_done")
    a.ins(LW("ra", 0, "sp"))
    a.ins(LW("a0", 4, "sp"))
    a.ins(LW("a1", 8, "sp"))
    a.ins(LW("a2", 12, "sp"))
    a.ins(LW("a3", 16, "sp"))
    a.ins(NOP())
    a.ins(ADDIU("sp", "sp", 32))

    # 원본 대사 렌더러 본체로 (진입 2명령을 대신 수행)
    #   80036888: addiu $sp, $sp, -0x40
    #   8003688C: sw    $s3, 0x24($sp)
    a.ins(ADDIU("sp", "sp", -0x40))
    a.j(DLG_BODY)
    a.ins(SW("s3", 0x24, "sp"))
    return a


def patch(exe_bytes):
    exe = bytearray(exe_bytes)

    # 원본 대사 렌더러 진입 2명령 확인 (딜레이 슬롯 처리를 위해)
    import struct
    w0 = struct.unpack("<I", exe[r2f(DLG):r2f(DLG) + 4])[0]
    w1 = struct.unpack("<I", exe[r2f(DLG) + 4:r2f(DLG) + 8])[0]

    a1 = build_loadimage_hook()
    a2 = build_dialog_hook()
    for a, nm in ((a1, "LoadImage"), (a2, "대사")):
        v = a.check_load_delay()
        if v:
            raise RuntimeError(f"{nm} 훅 로드 지연 위반: {v}")

    c1 = a1.assemble()
    c2 = a2.assemble()
    # 각 훅이 자기 칸을 넘지 않는지 (겹치면 서로를 덮어쓴다)
    if len(c1) > 0x60:
        raise RuntimeError(f"LoadImage 훅이 {len(c1)}B — 0x60 초과")
    if len(c2) > 0xC0:
        raise RuntimeError(f"대사 훅이 {len(c2)}B — 0xC0 초과")
    if (DATA + 32) > 0x8007AF41:
        raise RuntimeError("데이터가 훅 영역을 넘음")

    exe[r2f(HOOK_LI):r2f(HOOK_LI) + len(c1)] = c1
    exe[r2f(HOOK_DLG):r2f(HOOK_DLG) + len(c2)] = c2

    # 데이터 초기화
    d = bytearray(32)
    struct.pack_into("<HHHH", d, 16, 896, 144, 64, 72)    # RECT_P1
    struct.pack_into("<HHHH", d, 24, 960, 256, 64, 80)    # RECT_P3
    exe[r2f(DATA):r2f(DATA) + 32] = d

    # 진입점을 훅으로
    exe[r2f(LI):r2f(LI) + 4] = J(HOOK_LI)
    exe[r2f(LI) + 4:r2f(LI) + 8] = NOP()
    exe[r2f(DLG):r2f(DLG) + 4] = J(HOOK_DLG)
    exe[r2f(DLG) + 4:r2f(DLG) + 8] = NOP()

    check_hook_site(exe, r2f, LI, HOOK_LI, handled_insns=2)
    check_hook_site(exe, r2f, DLG, HOOK_DLG, handled_insns=2)
    return bytes(exe), len(c1), len(c2), (w0, w1)


if __name__ == "__main__":
    import struct
    eu = open("/home/claude/legaia/cn/SCUS_US.exe", "rb").read()
    w0 = struct.unpack("<I", eu[r2f(DLG):r2f(DLG) + 4])[0]
    w1 = struct.unpack("<I", eu[r2f(DLG) + 4:r2f(DLG) + 8])[0]
    print(f"대사 렌더러 원본 진입 2명령: 0x{w0:08X} 0x{w1:08X}")
    from capstone import Cs, CS_ARCH_MIPS, CS_MODE_MIPS32, CS_MODE_LITTLE_ENDIAN
    md = Cs(CS_ARCH_MIPS, CS_MODE_MIPS32 | CS_MODE_LITTLE_ENDIAN)
    for i in md.disasm(eu[r2f(DLG):r2f(DLG) + 12], DLG):
        print(f"  {i.address:08X}: {i.mnemonic:8s} {i.op_str}")
