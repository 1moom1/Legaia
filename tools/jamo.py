#!/usr/bin/env python3
"""조합형 한글 자모 글리프 렌더러 (1벌식)

레가이아 US 렌더러에 맞춘 설계:
  - 글리프 셀 14x15px
  - 한 글자 = 초성 + 중성 + 종성 (3바이트, 항상 3개)
  - X 전진 = 폭테이블[code] + 1
      초성 폭 0  -> 그린 뒤 X += 1
      중성 폭 0  -> 그린 뒤 X += 1
      종성 폭 11 -> 그린 뒤 X += 12
      합계 14px  (= 한 글자 폭)
  - 따라서 중성 글리프는 1px, 종성 글리프는 2px 왼쪽으로 미리 시프트해서
    폰트에 그려두면 정확히 한 자리에 겹쳐진다.

자모는 유니코드 조합용 자모(U+1100 계열)가 아니라, 완성형 음절을 직접
합성해서 각 자모 레이어만 추출하는 방식으로 만든다 (모양이 자연스러움).
"""
import numpy as np
from PIL import Image, ImageFont, ImageDraw

CHO = list("ㄱㄲㄴㄷㄸㄹㅁㅂㅃㅅㅆㅇㅈㅉㅊㅋㅌㅍㅎ")          # 19
JUNG = list("ㅏㅐㅑㅒㅓㅔㅕㅖㅗㅘㅙㅚㅛㅜㅝㅞㅟㅠㅡㅢㅣ")     # 21
JONG = list("ㄱㄲㄳㄴㄵㄶㄷㄹㄺㄻㄼㄽㄾㄿㅀㅁㅂㅄㅅㅆㅇㅈㅊㅋㅌㅍㅎ")  # 27 (없음 제외)

CELL_W, CELL_H = 14, 15
FONT_PATH = "/usr/share/fonts/opentype/unifont/unifont.otf"


def compose(cho_i, jung_i, jong_i):
    """자모 인덱스 -> 완성형 음절"""
    return chr(0xAC00 + (cho_i * 21 + jung_i) * 28 + jong_i)


def render_syllable(ch, size=13):
    """완성형 음절을 size x size 비트맵(0/1)으로"""
    ft = ImageFont.truetype(FONT_PATH, 16)
    img = Image.new("L", (16, 16), 0)
    d = ImageDraw.Draw(img)
    bb = ft.getbbox(ch)
    x = (16 - (bb[2] - bb[0])) // 2 - bb[0]
    y = (16 - (bb[3] - bb[1])) // 2 - bb[1]
    d.text((x, y), ch, font=ft, fill=255)
    if size != 16:
        img = img.resize((size, size), Image.LANCZOS)
    return (np.asarray(img) > 100).astype(np.uint8)


def extract_jamo_layers():
    """각 자모의 글리프를 '차분'으로 추출.

    초성 X: 음절 (X, ㅏ, 없음) 에서 (ㅇ, ㅏ, 없음) 의 ㅏ 부분을 빼는 식은 부정확.
    대신 단순하고 확실한 방법:
      - 초성: (X, ㅣ, 없음) 렌더 후, ㅣ 세로획 영역(오른쪽 끝 2px)을 지움
      - 중성: (ㅇ, X, 없음) 렌더 후, ㅇ 영역을 지움 -> 부정확

    => 더 안전: 유니코드 '호환 자모'(ㄱ ㅏ 등)를 그대로 쓰되,
       초성/중성/종성 위치에 맞게 축소·배치한다.
    """
    pass


def jamo_glyph(jamo, role, size=13):
    """자모 하나를 14x15 셀 안의 적절한 위치에 렌더.

    role: 'cho' | 'jung' | 'jong'
    반환: (15,14) uint8, 값 0/2/3  (3=진한 잉크, 2=연한)
    """
    ft_big = ImageFont.truetype(FONT_PATH, 16)
    # 자모를 큰 캔버스에 그린 뒤 bbox 잘라서 목표 영역에 맞춤
    tmp = Image.new("L", (24, 24), 0)
    d = ImageDraw.Draw(tmp)
    bb = ft_big.getbbox(jamo)
    d.text((4 - bb[0], 4 - bb[1]), jamo, font=ft_big, fill=255)
    a = np.asarray(tmp)
    ys, xs = np.where(a > 80)
    if len(ys) == 0:
        return np.zeros((CELL_H, CELL_W), np.uint8)
    crop = tmp.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))

    # 역할별 목표 박스 (셀 14x15 기준)
    if role == "cho":
        box = (0, 0, 8, 9)        # 왼쪽 위
    elif role == "jung":
        # 세로모음(ㅏㅑㅓㅕㅣ...)은 오른쪽, 가로모음(ㅗㅜㅡ..)은 아래
        if jamo in "ㅗㅛㅜㅠㅡ":
            box = (0, 9, 13, 14)
        elif jamo in "ㅘㅙㅚㅝㅞㅟㅢ":
            box = (0, 0, 13, 14)   # 복합모음: 전체
        else:
            box = (8, 0, 13, 12)   # 세로모음: 오른쪽
    else:  # jong
        box = (0, 10, 13, 15)      # 아래

    bw = box[2] - box[0]
    bh = box[3] - box[1]
    g = crop.resize((max(bw, 1), max(bh, 1)), Image.LANCZOS)
    ga = np.asarray(g).astype(float) / 255.0

    out = np.zeros((CELL_H, CELL_W), np.uint8)
    for yy in range(bh):
        for xx in range(bw):
            v = ga[yy, xx]
            Y = box[1] + yy
            X = box[0] + xx
            if Y >= CELL_H or X >= CELL_W:
                continue
            if v > 0.55:
                out[Y, X] = 3
            elif v > 0.22:
                out[Y, X] = max(out[Y, X], 2)
    return out


def shift_left(g, n):
    """글리프를 n픽셀 왼쪽으로 (X 전진 보정용)"""
    if n <= 0:
        return g
    out = np.zeros_like(g)
    out[:, :CELL_W - n] = g[:, n:]
    return out


def decompose(ch):
    """완성형 음절 -> (초성idx, 중성idx, 종성idx). 종성 없으면 -1"""
    c = ord(ch) - 0xAC00
    if not (0 <= c < 11172):
        return None
    cho = c // (21 * 28)
    jung = (c % (21 * 28)) // 28
    jong = c % 28
    return cho, jung, jong - 1     # jong 0 = 없음 -> -1


if __name__ == "__main__":
    # 미리보기: 자모 시트
    rows = []
    for role, lst in (("cho", CHO), ("jung", JUNG), ("jong", JONG)):
        sheet = Image.new("L", (len(lst) * 16, 17), 255)
        for i, j in enumerate(lst):
            g = jamo_glyph(j, role)
            arr = (255 - g * 85).astype(np.uint8)
            sheet.paste(Image.fromarray(arr, "L"), (i * 16 + 1, 1))
        rows.append((role, sheet))
    W = max(s.width for _, s in rows)
    H = sum(s.height for _, s in rows) + 10
    out = Image.new("L", (W, H), 255)
    y = 0
    for role, s in rows:
        out.paste(s, (0, y))
        y += s.height + 3
    out.resize((W * 3, H * 3), Image.NEAREST).save(
        "/home/claude/legaia/build/jamo_sheet.png")
    print("자모 시트 저장: build/jamo_sheet.png")
    print(f"  초성 {len(CHO)} / 중성 {len(JUNG)} / 종성 {len(JONG)} = {len(CHO)+len(JUNG)+len(JONG)}자")
