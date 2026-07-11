#!/usr/bin/env python3
"""Render a byte region as a continuous 4bpp bitmap (width W px) to spot font sheets."""
from PIL import Image
import sys

def render(data, off, nbytes, w_px, scale=2, low_nibble_first=True):
    row_bytes = w_px // 2
    rows = nbytes // row_bytes
    img = Image.new("L", (w_px, rows))
    px = img.load()
    for r in range(rows):
        base = off + r * row_bytes
        for xb in range(row_bytes):
            b = data[base + xb] if base + xb < len(data) else 0
            lo = b & 0xF; hi = (b >> 4) & 0xF
            if low_nibble_first:
                px[xb*2, r] = lo*17; px[xb*2+1, r] = hi*17
            else:
                px[xb*2, r] = hi*17; px[xb*2+1, r] = lo*17
    if scale != 1:
        img = img.resize((w_px*scale, rows*scale), Image.NEAREST)
    return img

if __name__ == "__main__":
    src = sys.argv[1]; off = int(sys.argv[2],0); nbytes = int(sys.argv[3],0)
    w = int(sys.argv[4]); out = sys.argv[5]
    scale = int(sys.argv[6]) if len(sys.argv)>6 else 2
    data = open(src,"rb").read()
    img = render(data, off, nbytes, w, scale)
    img.save(out); print("saved", out, img.size)
