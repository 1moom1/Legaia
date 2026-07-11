#!/usr/bin/env python3
"""PoC v2 -- REAL Korean on screen.

Everything below is derived from verified findings:
  * charset table: EXE file 0x65F80, 776 entries of big-endian SJIS codes.
    index in that table == glyph cell index.
  * font page: PROT.DAT TIM @0x7F34, pixel data at 0x7F74, 32768 bytes,
    256x256 px 4bpp, uploaded to VRAM (896,0). Byte-identical to the VRAM dump.
  * cell grid: 12x12 px cells, 20 cells per row, arranged in four 60px-wide
    column blocks separated by a 4px gap (block stride = 64 px).
  * dual layer: pixel nibble = (layer2 << 2) | layer1, each layer 2 bits {0,2,3}.
      layer1 (low 2 bits)  = charset indices   0..419
      layer2 (high 2 bits) = charset indices 420..775
  * text in scripts is raw Shift-JIS.

Plan: pick unused charset glyphs, overwrite their cells with Hangul,
then encode the Korean line using those glyphs' SJIS codes.
"""
import sys, struct, json, subprocess, tempfile, os
import numpy as np

sys.path.insert(0, "/home/claude/legaia/tools")
from legaia import prot_files, pack_parse, script_parse, read_u24
from hangul import render_glyph_pixel

EXE_PATH = "/home/claude/legaia/extract/SCPS_100.59"
PROT_PATH = "/home/claude/legaia/extract/PROT.DAT"
LZSS_BIN = "/home/claude/legaia/tools/lzss_fast"

CHARSET_OFF = 0x65F80
CHARSET_N = 776
FONT_TIM = 0x7F34
FONT_PIX = 0x7F74          # pixel data offset inside PROT.DAT
FONT_W = 256               # px (4bpp)
FONT_H = 256
CELL = 12
COLS = 20
BLOCK_STRIDE = 64          # px between 60px blocks
LAYER1_MAX = 420           # charset indices 0..419 live in layer 1

MAP_FILE = 6
SCRIPT_ID = 3
SCRIPT_IDX = 47

KO_LINES = ["「당신에게도 소중히 간직하고 싶은",
            "　추억이 생기거든、",
            "　기억의 상에게 말을 걸어 보세요。"]


def lzss_compress(data: bytes) -> bytes:
    with tempfile.NamedTemporaryFile(delete=False) as fi:
        fi.write(data); src = fi.name
    dst = src + ".lz"
    subprocess.run([LZSS_BIN, "c", src, dst], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    out = open(dst, "rb").read()
    os.unlink(src); os.unlink(dst)
    return out


def load_charset():
    exe = open(EXE_PATH, "rb").read()
    codes = [(exe[CHARSET_OFF + i*2] << 8) | exe[CHARSET_OFF + i*2 + 1]
             for i in range(CHARSET_N)]
    chars = []
    for c in codes:
        try:
            chars.append(struct.pack(">H", c).decode("shift_jis"))
        except Exception:
            chars.append("\uFFFD")
    return codes, chars


def cell_xy(idx):
    """charset index -> (layer, x, y) top-left pixel of its 12x12 cell."""
    if idx < LAYER1_MAX:
        layer = 1; i = idx
    else:
        layer = 2; i = idx - LAYER1_MAX
    r, c = divmod(i, COLS)
    block, cc = divmod(c, 5)
    x = block * BLOCK_STRIDE + cc * CELL
    y = r * CELL
    return layer, x, y


def font_to_pixels(prot: bytes):
    """Return (H, W) uint8 array of 4-bit nibble values for the font page."""
    raw = prot[FONT_PIX:FONT_PIX + FONT_W * FONT_H // 2]
    a = np.frombuffer(raw, dtype=np.uint8)
    lo = a & 0xF; hi = (a >> 4) & 0xF
    px = np.empty(a.size * 2, dtype=np.uint8)
    px[0::2] = lo; px[1::2] = hi
    return px.reshape(FONT_H, FONT_W)


def pixels_to_font(px: np.ndarray) -> bytes:
    """Inverse of font_to_pixels."""
    flat = px.reshape(-1)
    lo = flat[0::2]; hi = flat[1::2]
    packed = (lo & 0xF) | ((hi & 0xF) << 4)
    return packed.astype(np.uint8).tobytes()


def main():
    prot = bytearray(open(PROT_PATH, "rb").read())
    codes, chars = load_charset()
    charset = "".join(chars)

    # --- which glyphs does map 6 use? ---
    for i, s, e in prot_files(bytes(prot)):
        if i == MAP_FILE:
            map_s, map_e = s, e
            ents = pack_parse(bytes(prot[s:e]))
            break
    script_blob = [d for f, d in ents if f == SCRIPT_ID][0]
    scripts = script_parse(script_blob)

    used = set()
    for sc in scripts:
        k = 0
        while k < len(sc) - 1:
            b = sc[k]
            if 0x81 <= b <= 0x9F or 0xE0 <= b <= 0xEF:
                try:
                    ch = sc[k:k+2].decode("shift_jis")
                    if ch in charset:
                        used.add(ch)
                except Exception:
                    pass
                k += 2
            else:
                k += 1
    print(f"map {MAP_FILE} uses {len(used)}/{CHARSET_N} glyphs")

    # --- Hangul we need ---
    text = KO_LINES[0] + "\x0a" + KO_LINES[1] + "\x12" + KO_LINES[2]
    hangul = []
    for ch in text:
        if 0xAC00 <= ord(ch) <= 0xD7A3 and ch not in hangul:
            hangul.append(ch)
    print(f"Hangul syllables needed: {len(hangul)} -> {''.join(hangul)}")

    # --- pick unused kanji slots (index >= 200 keeps us out of punctuation/kana) ---
    free = [i for i in range(CHARSET_N)
            if charset[i] not in used and i >= 200]
    if len(free) < len(hangul):
        print("not enough free slots"); sys.exit(1)
    slots = free[:len(hangul)]
    mapping = {}   # hangul char -> (charset index, sjis code)
    for ch, idx in zip(hangul, slots):
        mapping[ch] = (idx, codes[idx])
    print("\nglyph slot assignment (hangul -> charset idx / sjis / replaced kanji):")
    for ch, (idx, code) in mapping.items():
        print(f"  {ch} -> idx {idx:3d}  sjis {code:04x}  (was {charset[idx]})")

    # --- draw Hangul into the font page ---
    px = font_to_pixels(bytes(prot)).copy()
    for ch, (idx, code) in mapping.items():
        layer, x, y = cell_xy(idx)
        g = render_glyph_pixel(ch, CELL)   # 12x12 values in {0,2,3}
        cellblk = px[y:y+CELL, x:x+CELL]
        if layer == 1:
            # clear low 2 bits, insert glyph
            cellblk = (cellblk & 0b1100) | (g & 0b11)
        else:
            cellblk = (cellblk & 0b0011) | ((g & 0b11) << 2)
        px[y:y+CELL, x:x+CELL] = cellblk

    new_font = pixels_to_font(px)
    assert len(new_font) == FONT_W * FONT_H // 2
    prot[FONT_PIX:FONT_PIX + len(new_font)] = new_font
    print(f"\npatched font page ({len(new_font)} bytes) at PROT 0x{FONT_PIX:X}")

    # --- encode the Korean message using the assigned SJIS codes ---
    out = bytearray()
    for ch in text:
        if ch in mapping:
            out += struct.pack(">H", mapping[ch][1])
        elif ch == "\x0a":
            out.append(0x0A)
        elif ch == "\x12":
            out.append(0x12)
        elif ch in ("　", " "):
            out += "　".encode("shift_jis")
        else:
            out += ch.encode("shift_jis")
    new_msg = bytes(out)
    print(f"encoded message: {len(new_msg)} bytes")

    # --- splice into script 47 ---
    orig_msg = bytes.fromhex(
        "817582a082c882bd82e0814082bd82a282b982c282c982b582bd82a2"
        "0a81408e7682a28f6f82aa82c582ab82bd82e78141128140"
        "8b4c89af82cc919c82c9986282b582a982af82c482b282e782f182c882b382a28142")
    sc = bytearray(scripts[SCRIPT_IDX])
    pos = sc.find(orig_msg)
    assert pos >= 0, "original message not found"
    sc[pos:pos+len(orig_msg)] = new_msg
    scripts[SCRIPT_IDX] = bytes(sc)

    # rebuild script container
    header = script_blob[:0x22]
    c0, c1, c2 = struct.unpack("<HHH", script_blob[0x22:0x28])
    count = c0 + c1 + c2
    base = 0x2B + count * 3
    footer = script_blob[read_u24(script_blob, 0x28) + base:]

    def wu24(b, v):
        b.append(v & 0xFF); b.append((v >> 8) & 0xFF); b.append((v >> 16) & 0xFF)

    offs = []; cur = 0
    for s_ in scripts:
        offs.append(cur); cur += len(s_)
    nb = bytearray()
    nb += header
    nb += struct.pack("<HHH", c0, c1, c2)
    wu24(nb, cur)
    for o in offs:
        wu24(nb, o)
    for s_ in scripts:
        nb += s_
    nb += footer
    new_script_blob = bytes(nb)
    assert script_parse(new_script_blob), "rebuilt script won't parse"

    # --- repack ---
    new_ents = [(f, (new_script_blob if f == SCRIPT_ID else d)) for f, d in ents]
    n = len(new_ents)
    table = bytearray(); body = bytearray()
    addr = 8 + n * 8; total = 0
    for fid, dec in new_ents:
        comp = lzss_compress(dec)
        wu24(table, len(dec)); table.append(fid)
        table += struct.pack("<I", addr)
        body += comp; addr += len(comp); total += len(dec)
    new_pack = struct.pack("<II", n, total) + bytes(table) + bytes(body)
    print(f"pack: {map_e-map_s} -> {len(new_pack)} bytes")
    if len(new_pack) > map_e - map_s:
        print("!! doesn't fit"); sys.exit(1)
    prot[map_s:map_e] = new_pack + b"\x00" * (map_e - map_s - len(new_pack))

    open("/home/claude/legaia/build/PROT_v2.DAT", "wb").write(bytes(prot))
    json.dump({ch: {"idx": v[0], "sjis": f"{v[1]:04x}"} for ch, v in mapping.items()},
              open("/home/claude/legaia/build/ko_map_v2.json", "w"),
              ensure_ascii=False, indent=1)
    print("wrote build/PROT_v2.DAT")

    # --- end-to-end verify ---
    p2 = bytes(prot)
    _, s2, e2 = next(f for f in prot_files(p2) if f[0] == MAP_FILE)
    ents2 = pack_parse(p2[s2:e2])
    sb2 = [d for f, d in ents2 if f == SCRIPT_ID][0]
    sc2 = script_parse(sb2)[SCRIPT_IDX]
    assert new_msg in sc2, "message missing after roundtrip"
    print("END-TO-END: Korean message survives PROT->PACK->LZSS->script ✓")


if __name__ == "__main__":
    main()
