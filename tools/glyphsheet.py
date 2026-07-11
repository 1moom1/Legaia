#!/usr/bin/env python3
"""Render a byte region as a grid of 1bpp or 4bpp glyphs to a PNG, for eyeballing fonts."""
from PIL import Image
import sys

def render_1bpp(data, off, gw, gh, cols, rows, msb_left=True, scale=3, invert=False):
    row_bytes = (gw + 7) // 8
    gbytes = row_bytes * gh
    W = cols * gw
    H = rows * gh
    img = Image.new("L", (W, H), 0)
    px = img.load()
    p = off
    for gy in range(rows):
        for gx in range(cols):
            base = p + (gy * cols + gx) * gbytes
            for y in range(gh):
                for xb in range(row_bytes):
                    if base + y*row_bytes + xb >= len(data): 
                        b = 0
                    else:
                        b = data[base + y*row_bytes + xb]
                    for bit in range(8):
                        x = xb*8 + bit
                        if x >= gw: break
                        v = (b >> (7-bit)) & 1 if msb_left else (b >> bit) & 1
                        if invert: v ^= 1
                        px[gx*gw + x, gy*gh + y] = 255 if v else 0
    return img.resize((W*scale, H*scale), Image.NEAREST)

def render_4bpp(data, off, gw, gh, cols, rows, scale=3):
    # 4bpp: two pixels per byte, low nibble = left pixel
    row_bytes = (gw + 1)//2
    gbytes = row_bytes*gh
    W=cols*gw; H=rows*gh
    img=Image.new("L",(W,H),0); px=img.load()
    for gy in range(rows):
        for gx in range(cols):
            base=off+(gy*cols+gx)*gbytes
            for y in range(gh):
                for xb in range(row_bytes):
                    idx=base+y*row_bytes+xb
                    b=data[idx] if idx<len(data) else 0
                    lo=b&0xf; hi=(b>>4)&0xf
                    x0=xb*2
                    if x0<gw: px[gx*gw+x0, gy*gh+y]=lo*17
                    if x0+1<gw: px[gx*gw+x0+1, gy*gh+y]=hi*17
    return img.resize((W*scale,H*scale),Image.NEAREST)

if __name__=="__main__":
    src, off, fmt, gw, gh, cols, rows, out = sys.argv[1:9]
    data=open(src,"rb").read()
    off=int(off,0); gw=int(gw); gh=int(gh); cols=int(cols); rows=int(rows)
    if fmt=="1":
        img=render_1bpp(data,off,gw,gh,cols,rows)
    else:
        img=render_4bpp(data,off,gw,gh,cols,rows)
    img.save(out)
    print("saved",out,img.size)
