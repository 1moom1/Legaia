#!/usr/bin/env python3
"""조합형 한글 자모 (초성 2벌식) — 레가이아 US 렌더러용

설계:
  글리프 셀 14x15px, 한 글자 = 초성 + 중성 + 종성(항상 3바이트)

  X 전진 = 폭테이블[code] + 1  (0x800740E8 = 0)
     초성 폭 0  -> X += 1
     중성 폭 0  -> X += 1
     종성 폭 11 -> X += 12
     -------------------------
     합계 14px = 한 글자 폭

  따라서 폰트에 그릴 때:
     초성 글리프: 시프트 0
     중성 글리프: 1px 왼쪽으로 미리 시프트
     종성 글리프: 2px 왼쪽으로 미리 시프트
  => 세 자모가 정확히 한 자리(14px)에 겹쳐진다.

  초성 2벌:
     V벌 = 세로모음(ㅏㅐㅑㅓㅕㅔㅣ...)과 결합 -> 초성을 왼쪽에 세로로
     H벌 = 가로모음(ㅗㅛㅜㅠㅡ)과 결합       -> 초성을 위쪽에 납작하게
  (복합모음 ㅘㅙㅚㅝㅞㅟㅢ 는 H벌 사용)

  자모 수: 초성 19*2=38, 중성 21, 종성 22 = 81자  (가용 코드 81개에 정확히 맞음)
"""
import numpy as np
from PIL import Image, ImageFont, ImageDraw

CELL_W, CELL_H = 14, 15
FONT_PATH = "/usr/share/fonts/opentype/unifont/unifont.otf"

CHO = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")           # 19
JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")      # 21
# 종성 27개 중 자주 쓰는 22개 (겹받침 일부 제외: ㄳㄵㄾㄿㅄ)
JONG_ALL = list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")  # 27
JONG_DROP = set("ㄳㄵㄾㄿㅄ")
JONG = [j for j in JONG_ALL if j not in JONG_DROP]           # 22

# 가로모음: 초성을 위에 납작하게 놓아야 하는 모음
HORIZ_VOWELS = set("ㅗㅛㅜㅠㅡ")
COMPLEX_VOWELS = set("ㅘㅙㅚㅝㅞㅟㅢ")


def _crop(jamo):
    """자모를 렌더해서 잉크 bbox 로 자른 PIL 이미지"""
    ft = ImageFont.truetype(FONT_PATH, 16)
    tmp = Image.new("L", (28, 28), 0)
    d = ImageDraw.Draw(tmp)
    bb = ft.getbbox(jamo)
    d.text((6 - bb[0], 6 - bb[1]), jamo, font=ft, fill=255)
    a = np.asarray(tmp)
    ys, xs = np.where(a > 80)
    if len(ys) == 0:
        return None
    return tmp.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))


def _place(crop, box):
    """crop 을 box=(x0,y0,x1,y1) 영역에 리사이즈해 넣은 14x15 배열 반환"""
    out = np.zeros((CELL_H, CELL_W), np.uint8)
    if crop is None:
        return out
    x0, y0, x1, y1 = box
    bw, bh = max(x1 - x0, 1), max(y1 - y0, 1)
    g = np.asarray(crop.resize((bw, bh), Image.LANCZOS)).astype(float) / 255.0
    for yy in range(bh):
        for xx in range(bw):
            Y, X = y0 + yy, x0 + xx
            if not (0 <= Y < CELL_H and 0 <= X < CELL_W):
                continue
            v = g[yy, xx]
            if v > 0.5:
                out[Y, X] = 3
            elif v > 0.2:
                out[Y, X] = max(out[Y, X], 2)
    return out


def cho_glyph(jamo, style):
    """초성. style='V'(세로모음용) | 'H'(가로모음용)"""
    c = _crop(jamo)
    if style == "V":
        return _place(c, (0, 1, 8, 12))      # 왼쪽, 세로로 김
    else:
        return _place(c, (1, 0, 13, 7))      # 위쪽, 납작


def jung_glyph(jamo):
    c = _crop(jamo)
    if jamo in HORIZ_VOWELS:
        return _place(c, (0, 8, 13, 13))     # 아래 가로
    if jamo in COMPLEX_VOWELS:
        return _place(c, (0, 6, 14, 14))     # 아래+오른쪽 (복합)
    return _place(c, (8, 0, 14, 13))         # 오른쪽 세로


def jong_glyph(jamo):
    c = _crop(jamo)
    return _place(c, (0, 10, 14, 15))        # 맨 아래


def shift_left(g, n):
    if n <= 0:
        return g
    out = np.zeros_like(g)
    out[:, :CELL_W - n] = g[:, n:]
    return out


def decompose(ch):
    """완성형 -> (초성idx, 중성idx, 종성idx or -1)"""
    c = ord(ch) - 0xAC00
    if not (0 <= c < 11172):
        return None
    return c // (21 * 28), (c % (21 * 28)) // 28, (c % 28) - 1


def cho_style(jung_jamo):
    return "H" if (jung_jamo in HORIZ_VOWELS or jung_jamo in COMPLEX_VOWELS) else "V"


def compose_preview(ch):
    """미리보기용: 3자모를 겹쳐 14x15 한 칸에 (실제 게임 렌더 시뮬레이션)"""
    d = decompose(ch)
    if d is None:
        return np.zeros((CELL_H, CELL_W), np.uint8)
    ci, ji, ki = d
    jung = JUNG[ji]
    canvas = cho_glyph(CHO[ci], cho_style(jung))
    canvas = np.maximum(canvas, shift_left(jung_glyph(jung), 1))
    if ki >= 0:
        jj = JONG_ALL[ki]
        if jj in JONG:
            canvas = np.maximum(canvas, shift_left(jong_glyph(jj), 2))
    return canvas


if __name__ == "__main__":
    n = len(CHO) * 2 + len(JUNG) + len(JONG)
    print(f"자모 수: 초성 {len(CHO)}x2={len(CHO)*2} + 중성 {len(JUNG)} + 종성 {len(JONG)} = {n}자")

    test = "기억의상해줍니다한글패치레가이아전설고구름"
    W = len(test) * 16
    img = Image.new("L", (W, 17), 255)
    for i, ch in enumerate(test):
        g = compose_preview(ch)
        img.paste(Image.fromarray((255 - g * 85).astype(np.uint8), "L"), (i * 16 + 1, 1))
    img.resize((W * 4, 17 * 4), Image.NEAREST).save(
        "/home/claude/legaia/build/compose2.png")
    print("저장: build/compose2.png ->", test)
