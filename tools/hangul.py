#!/usr/bin/env python3
"""Render Hangul syllables as 12x12, 3-level (0..3, values 0/2/3) glyphs
matching Legaia's dual-layer font format.

The game's cells are 12x12 pixels. Each pixel is 2 bits within a layer:
   0 = transparent, 2 = mid, 3 = full ink   (value 1 is never used)
"""
import numpy as np
from PIL import Image, ImageFont, ImageDraw

FONT_CANDIDATES = [
    ("/usr/share/fonts/opentype/unifont/unifont.otf", 16, 0),
    ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 12, 0),
]


def render_glyph(ch, size=12, font_path=None, font_size=None, y_off=0, x_off=0):
    """Return a 12x12 numpy array with values in {0,2,3}."""
    if font_path is None:
        font_path, font_size, _ = FONT_CANDIDATES[0]
    ft = ImageFont.truetype(font_path, font_size)
    # render at 4x for antialiasing, then downsample
    S = size * 4
    img = Image.new("L", (S, S), 0)
    d = ImageDraw.Draw(img)
    # measure and center
    bbox = ft.getbbox(ch)
    fs4 = ImageFont.truetype(font_path, font_size * 4)
    bbox = fs4.getbbox(ch)
    w = bbox[2] - bbox[0]
    h = bbox[3] - bbox[1]
    x = (S - w) // 2 - bbox[0] + x_off * 4
    y = (S - h) // 2 - bbox[1] + y_off * 4
    d.text((x, y), ch, font=fs4, fill=255)
    small = img.resize((size, size), Image.LANCZOS)
    a = np.asarray(small).astype(float) / 255.0
    # quantize to 3 levels: 0, 2, 3
    out = np.zeros((size, size), dtype=np.uint8)
    out[a > 0.62] = 3
    out[(a > 0.22) & (a <= 0.62)] = 2
    return out


def render_glyph_pixel(ch, size=12):
    """Crisper variant: render with unifont at native 16px, crop/scale to 12x12,
    threshold to 2 levels (0 and 3) plus edge softening at 2."""
    path = "/usr/share/fonts/opentype/unifont/unifont.otf"
    ft = ImageFont.truetype(path, 16)
    img = Image.new("L", (16, 16), 0)
    d = ImageDraw.Draw(img)
    bbox = ft.getbbox(ch)
    x = (16 - (bbox[2] - bbox[0])) // 2 - bbox[0]
    y = (16 - (bbox[3] - bbox[1])) // 2 - bbox[1]
    d.text((x, y), ch, font=ft, fill=255)
    small = img.resize((size, size), Image.LANCZOS)
    a = np.asarray(small).astype(float) / 255.0
    out = np.zeros((size, size), dtype=np.uint8)
    out[a > 0.55] = 3
    out[(a > 0.20) & (a <= 0.55)] = 2
    return out


if __name__ == "__main__":
    text = "당신에게도소중히간직하고싶은추억이생기거든의상말을걸어보세요"
    chars = sorted(set(text))
    cols = 10
    rows = (len(chars) + cols - 1) // cols
    sheet = Image.new("L", (cols * 14, rows * 14), 255)
    for i, ch in enumerate(chars):
        g = render_glyph_pixel(ch)
        r, c = divmod(i, cols)
        sheet.paste(Image.fromarray((255 - g * 85).astype(np.uint8), "L"),
                    (c * 14 + 1, r * 14 + 1))
    sheet.resize((cols * 14 * 5, rows * 14 * 5), Image.NEAREST).save(
        "/home/claude/legaia/build/hangul_preview.png")
    print("chars:", "".join(chars))
    print("saved build/hangul_preview.png")
