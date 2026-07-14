#!/usr/bin/env python3
"""🔴 보호 구역 — 절대 건드리면 안 되는 PROT 오프셋

실제로 건드렸다가 게임이 깨진 구간들. 빌더는 반드시 이 모듈의
`assert_protected()` 로 검증할 것.

사용:
    from protected import assert_protected
    assert_protected(orig_prot_bytes, new_prot_bytes)
"""

# ─────────────────────────────────────────────────────────────
# 🔴 숫자 폰트 (TIM 0x11218, VRAM 960,256)
#    메뉴의 HP / MP / LV / G / TIME 숫자를 그리는 폰트.
#    지우거나 VRAM 좌표를 옮기면 숫자가 전부 사라진다.
#
#    TIM 헤더 0x011218
#      CLUT  0x01122C (512B)  <- 대사 팔레트. 잉크값(3)만 수정 허용
#      IMG   0x01142C
#        ix @ 0x011430 = 960   ★변경 금지
#        iy @ 0x011432 = 256   ★변경 금지
#      픽셀  0x011438 ~ 0x019438 (32,768B, 256x256 4bpp, 128B/행)
#
#    V 영역 (픽셀 오프셋 = 0x11438 + V*128):
#      V   0~ 79 : 0x011438~0x013C38  개발 메모   → 사용 가능 (페이지3)
#      V  80~143 : 0x013C38~0x015C38  빈 영역     → 사용 가능 (페이지2)
#      V 144~255 : 0x015C38~0x019438  🔴 숫자 폰트 → 절대 금지
# ─────────────────────────────────────────────────────────────

NUMFONT_PIX = 0x011438
NUMFONT_BPR = 128                       # 4bpp, 256px 폭 = 128 B/행
NUMFONT_SAFE_MAX_V = 144                # V144 부터가 숫자 폰트

# (시작, 끝, 이름) — 끝은 배타적
PROTECTED = [
    # 숫자 폰트 (TIM 0x11218, VRAM 960,256). HP/MP/LV/G/TIME/AP 를 그린다.
    #
    # ★ V168~191 은 **완전히 비어 있다** — 4가지 VRAM (평소/전투/일본판/한글판)
    #   을 모두 비교해 0 임을 확인했다. 그래서 그 24px 만 열어 한글 p4 로 쓴다.
    #   (예전엔 p4 를 ASCII TIM V216~252 에 뒀는데, 그 자리는 게임이 캐릭터
    #    텍스처로 재사용해서 **글자 안에 그림 조각이 박혔다**.)
    (0x015C38, 0x016838, "숫자 폰트 앞부분 (TIM V144~167) — 숫자/라벨"),
    (0x017438, 0x019438, "숫자 폰트 뒷부분 (TIM V192~255) — 숫자/라벨"),
]

# 한글 p4 가 쓰는 자리 (숫자 TIM 의 빈 구간). 보호하지 않지만 기록해 둔다.
P4_AREA = (0x016838, 0x017438, "한글 p4 (숫자 TIM V168~191) — 4가지 VRAM 에서 빈 것 확인")

# 헤더 필드 (값이 바뀌면 안 됨)
FIXED_U16 = [
    (0x011430, 960, "숫자폰트 TIM ix (VRAM x)"),
    (0x011432, 256, "숫자폰트 TIM iy (VRAM y)"),
]

# 팔레트: 잉크값만 수정 허용
CLUT_OFF = 0x01122C
# 한글 팔레트에서 수정을 허용하는 칸:
#   3     = 한글 잉크
#   14,15 = ASCII 글리프(기호 ?!., 등)의 잉크
#           -> 흰색으로 바꿔야 기호가 한글과 같은 색으로 나온다
CLUT_WRITABLE = {3, 14, 15}


def v_to_offset(v):
    """숫자폰트 TIM 의 V 좌표 -> PROT 오프셋"""
    return NUMFONT_PIX + v * NUMFONT_BPR


def assert_protected(orig: bytes, new: bytes):
    """보호 구역이 그대로인지 검증. 어기면 AssertionError."""
    import struct
    for a, b, name in PROTECTED:
        if orig[a:b] != new[a:b]:
            # 어디가 달라졌는지 찾아서 알려줌
            for i in range(a, b):
                if orig[i] != new[i]:
                    v = (i - NUMFONT_PIX) // NUMFONT_BPR
                    raise AssertionError(
                        f"🔴 보호 구역 훼손!\n"
                        f"   {name}\n"
                        f"   오프셋 0x{i:06X} (V={v})\n"
                        f"   허용 범위: 0x{a:06X}~0x{b:06X} 는 절대 수정 금지"
                    )
    for off, val, name in FIXED_U16:
        cur = struct.unpack("<H", new[off:off + 2])[0]
        if cur != val:
            raise AssertionError(
                f"🔴 고정 필드 변경됨!\n"
                f"   {name} @ 0x{off:06X}: {val} -> {cur}\n"
                f"   이 값을 바꾸면 숫자 폰트의 VRAM 위치가 틀어진다"
            )
    # 팔레트: 잉크값(3) 외에 바뀐 곳이 있으면 경고
    changed = []
    for row in range(16):
        for i in range(16):
            o = CLUT_OFF + row * 32 + i * 2
            if orig[o:o + 2] != new[o:o + 2] and i not in CLUT_WRITABLE:
                changed.append((row, i))
    if changed:
        raise AssertionError(
            f"🔴 팔레트에서 허용 외 칸이 변경됨: {changed}\n"
            f"   허용: {sorted(CLUT_WRITABLE)} (3=한글잉크, 14/15=기호잉크)\n"
            f"   게임 UI 가 나머지 색을 쓴다"
        )
    return True


if __name__ == "__main__":
    print("🔴 보호 구역 (PROT.DAT)")
    for a, b, name in PROTECTED:
        print(f"  0x{a:06X} ~ 0x{b:06X}  ({b-a:,}B)  {name}")
    print()
    print("고정 필드:")
    for off, val, name in FIXED_U16:
        print(f"  0x{off:06X} = {val}  {name}")
    print()
    print("사용 가능:")
    print(f"  0x{v_to_offset(0):06X} ~ 0x{v_to_offset(80):06X}   V  0~ 79  개발 메모 (페이지3)")
    print(f"  0x{v_to_offset(80):06X} ~ 0x{v_to_offset(144):06X}   V 80~143  빈 영역   (페이지2)")
