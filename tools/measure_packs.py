#!/usr/bin/env python3
"""번역을 넣었을 때 각 맵 PACK 이 얼마나 커지는지 측정한다.

한글은 영어만큼 LZSS 압축이 안 되므로 PACK 이 커진다.
원본 맵 슬롯은 이미 꽉 차 있어(여유 합계 3KB) 넘치는 맵이 생긴다.
-> 얼마나 필요한지 재서, PROT 재배치로 감당되는지 확인한다.
"""
import os
import sys
import json
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from legaia import prot_files, pack_parse, lzss_compress   # noqa: E402
import translate as T                                       # noqa: E402
import prot_realloc as PR                                   # noqa: E402


def ru24(b, o):
    return b[o] | (b[o + 1] << 8) | (b[o + 2] << 16)


def wu24(ba, v):
    ba.append(v & 0xFF)
    ba.append((v >> 8) & 0xFF)
    ba.append((v >> 16) & 0xFF)


def rebuild_pack(raw, entries, mapping):
    """번역을 넣은 새 PACK 을 만들어 반환."""
    ents = pack_parse(raw)
    sblob = None
    for fid, dec in ents:
        if fid == 3:
            sblob = bytearray(dec)
    if sblob is None:
        return None

    c0, c1, c2 = struct.unpack("<HHH", bytes(sblob[0x22:0x28]))
    cnt = c0 + c1 + c2
    sbase = 0x2B + cnt * 3
    offs = [ru24(sblob, 0x2B + i * 3) for i in range(cnt)]
    end = ru24(sblob, 0x28)

    scripts = []
    for i in range(cnt):
        a = offs[i] + sbase
        b = (offs[i + 1] + sbase) if i + 1 < cnt else (end + sbase)
        scripts.append(bytearray(sblob[a:b]))

    for e in entries:
        si = e["script"]
        if si >= len(scripts):
            continue
        try:
            enc, _ = T.encode(e["ko"], mapping)   # (bytes, page) 를 반환한다
        except KeyError:
            continue                              # 폰트에 없는 음절 -> 영어로 남긴다
        if len(enc) > e["len"]:
            continue
        o = e["off"]
        scripts[si][o:o + e["len"]] = enc + b"\x20" * (e["len"] - len(enc))

    hdr = bytes(sblob[:0x22])
    footer = bytes(sblob[end + sbase:])
    nb = bytearray(hdr) + struct.pack("<HHH", c0, c1, c2)
    wu24(nb, sum(len(x) for x in scripts))
    o2 = 0
    for x in scripts:
        wu24(nb, o2)
        o2 += len(x)
    for x in scripts:
        nb += x
    nb += footer

    n_old, _ = struct.unpack("<II", raw[:8])
    pack_end = len(raw.rstrip(b"\x00"))
    old_comp = {}
    for k in range(n_old):
        o = 8 + k * 8
        fid_k = raw[o + 3]
        addr_k = struct.unpack("<I", raw[o + 4:o + 8])[0]
        if k + 1 < n_old:
            nxt = struct.unpack("<I", raw[8 + (k + 1) * 8 + 4:8 + (k + 1) * 8 + 8])[0]
        else:
            nxt = pack_end
        old_comp[fid_k] = raw[addr_k:nxt]

    newents = [(f, (bytes(nb) if f == 3 else dec)) for f, dec in ents]
    n = len(newents)
    tbl = bytearray()
    body = bytearray()
    addr = 8 + n * 8
    tot = 0
    for fid, dec in newents:
        comp = lzss_compress(dec) if fid == 3 else old_comp[fid]
        wu24(tbl, len(dec))
        tbl.append(fid)
        tbl += struct.pack("<I", addr)
        body += comp
        addr += len(comp)
        tot += len(dec)
    return struct.pack("<II", n, tot) + bytes(tbl) + bytes(body)


def main():
    prot = open(os.path.join(HERE, "..", "cn", "PROT_US.DAT"), "rb").read()
    files = {f[0]: (f[1], f[2]) for f in prot_files(prot)}
    dump = os.path.join(HERE, "..", "build", "text_dump.json")
    d = json.load(open(dump, encoding="utf-8"))
    trans = [e for e in d if e.get("ko")]
    print(f"번역 {len(trans):,}런")

    freq = T.collect_syllables(trans)
    mapping, _ = T.assign_pages(freq, trans)

    bym = {}
    for e in trans:
        bym.setdefault(e["map"], []).append(e)

    need = {}
    for mid in sorted(bym):
        ms, me = files[mid]
        raw = bytes(prot[ms:me])
        np_ = rebuild_pack(raw, bym[mid], mapping)
        if np_ is None:
            continue
        need[mid] = len(np_)

    over = [(need[m] - (files[m][1] - files[m][0]), m)
            for m in need if need[m] > (files[m][1] - files[m][0])]
    over.sort(reverse=True)
    print(f"슬롯을 넘는 맵: {len(over)}개, 총 초과 {sum(o for o, _ in over):,}B")
    for o, m in over[:6]:
        print(f"  map{m}: +{o:,}B")
    print()

    offs, sizes, total, spare = PR.plan(prot, need)
    print(f"재배치 후 PROT: {total:,}B / {PR.PROT_SIZE:,}B")
    if total <= PR.PROT_SIZE:
        print(f"  ✓ 가능! 여유 {spare:,}B")
    else:
        print(f"  ★ {total - PR.PROT_SIZE:,}B 초과")

    out = os.path.join(HERE, "..", "build", "pack_need.json")
    json.dump({str(k): v for k, v in need.items()}, open(out, "w"))
    print(f"저장: {out}")


if __name__ == "__main__":
    main()
