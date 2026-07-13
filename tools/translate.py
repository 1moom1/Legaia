#!/usr/bin/env python3
"""번역 삽입 파이프라인

text_dump.json 의 `ko` 필드를 채우면 게임에 삽입한다.

사용:
    python3 tools/translate.py --analyze    # 음절 빈도 분석 + 커버율
    python3 tools/translate.py --build      # 실제 삽입 + 디스크 생성

인코딩:
    음절      = 0xCE <idx+0x90>   (2B)
    페이지전환 = 0xCF <page>       (2B)  ← 빈도순 배치로 최소화
    ASCII/공백 = 그대로            (1B)
    매크로     = {NAME} 등 그대로   (1B)

제약:
    - 인코딩 결과가 원문 길이를 넘으면 삽입 불가 (경고)
    - 음절이 용량(400)을 넘으면 배치 불가 (경고)
"""
import json, struct, sys, re, argparse
from collections import Counter

sys.path.insert(0, "/home/claude/legaia/tools")
import hook7
from legaia import pack_parse, script_parse

DUMP = "/home/claude/legaia/build/text_dump.json"
HANGUL_MIN = hook7.HANGUL_MIN

# ★ 매크로(0xC0~0xCF)는 **2바이트**다: 코드 + 파라미터 1바이트.
#   예: "Look, " C1 00 ". That is"   <- C1 00 이 {NAME}
#   1바이트만 쓰면 전처리 함수가 다음 글자를 파라미터로 먹어 텍스트가 깨진다.
#   덤프는 파라미터가 0 이면 {NAME}, 아니면 {NAME:63} 으로 표기한다.
MACRO_RE = re.compile(
    r"\{(NAME|ITEM|M\d)(?::([0-9A-F]{2}))?\}"    # 2바이트 매크로 (+파라미터)
    r"|\{(BR)\}"                                  # 1바이트 제어
    r"|\{(PAL|CE):(\d+)\}"                        # 2바이트 (우리 코드)
    r"|<([0-9A-F]{2})>")                           # raw 바이트
MACRO_BYTE = {"NAME": 0xC1, "ITEM": 0xC2, "M3": 0xC3, "M4": 0xC4,
              "M5": 0xC5, "M6": 0xC6, "M7": 0xC7}


def is_hangul(ch):
    return 0xAC00 <= ord(ch) <= 0xD7A3


def macro_counts(text):
    """번역문/원문의 매크로·제어코드 개수"""
    c = Counter()
    for m in MACRO_RE.finditer(text):
        c[m.group(1) or f"<{m.group(2)}>"] += 1
    return c


def check_macros(entries):
    """🔴 원문의 매크로가 번역에 그대로 있는지 검증.

    {NAME}, {ITEM} 등은 캐릭터/아이템 이름을 넣는 '제어 코드'다.
    빼먹으면 스크립트 흐름이 깨져 게임이 멈춘다. (실제로 겪음)
    """
    bad = []
    for e in entries:
        a = macro_counts(e["text"])
        b = macro_counts(e["ko"])
        if a != b:
            bad.append((e, a, b))
    if bad:
        print(f"\n🔴 매크로 불일치 {len(bad)}건 — 게임이 멈춘다!")
        for e, a, b in bad[:10]:
            miss = {k: v for k, v in a.items() if b.get(k, 0) < v}
            ext = {k: v for k, v in b.items() if a.get(k, 0) < v}
            print(f"   EN: '{e['text']}'")
            print(f"   KO: '{e['ko']}'")
            if miss:
                print(f"      ★ 빠짐: {dict(miss)}")
            if ext:
                print(f"      ★ 추가됨: {dict(ext)}")
        raise SystemExit("매크로를 원문 그대로 유지할 것")
    return True


def tokenize(text):
    """번역문 -> 토큰 리스트. ('h', 음절) / ('b', 바이트)"""
    out = []
    i = 0
    while i < len(text):
        m = MACRO_RE.match(text, i)
        if m:
            if m.group(1):                       # {NAME} / {NAME:63} — 2바이트!
                out.append(("b", MACRO_BYTE[m.group(1)]))
                out.append(("b", int(m.group(2), 16) if m.group(2) else 0x00))
            elif m.group(3):                     # {BR} — 1바이트
                out.append(("b", 0x7C))
            elif m.group(4):                     # {PAL:n} / {CE:n} — 2바이트
                out.append(("b", 0xCF if m.group(4) == "PAL" else 0xCE))
                out.append(("b", int(m.group(5))))
            else:                                # <XX>
                out.append(("b", int(m.group(6), 16)))
            i = m.end()
            continue
        ch = text[i]
        out.append(("h", ch) if is_hangul(ch) else ("b", ord(ch) & 0xFF))
        i += 1
    return out


def collect_syllables(entries):
    """번역문 전체의 음절 빈도"""
    c = Counter()
    for e in entries:
        ko = e.get("ko", "")
        if not ko:
            continue
        for ch in ko:
            if is_hangul(ch):
                c[ch] += 1
    return c


def assign_pages(freq, entries=None):
    """페이지 배치.

    ★ 페이지 전환 코드(0xCF <p>)가 2바이트를 먹으므로, 한 대사의 음절이
      여러 페이지에 흩어지면 길이 초과가 난다. (짧은 대사일수록 치명적)

    전략: 대사 단위로 '함께 나오는 음절'을 같은 페이지에 몰아넣는다.
      1) 여유가 빠듯한(=원문이 짧은) 대사부터 처리
      2) 그 대사의 미배정 음절을 현재 페이지에 연속 배정
      3) 페이지가 차면 다음 페이지로
    이러면 한 대사가 한 페이지 안에서 끝날 확률이 크게 오른다.
    """
    cap = hook7.capacity()
    order = [1, 2, 3, 4]
    mapping = {}
    used = {p: 0 for p in order}
    over = []
    pi = 0

    def put(ch):
        nonlocal pi
        if ch in mapping:
            return True
        while pi < len(order) and used[order[pi]] >= cap[order[pi]]:
            pi += 1
        if pi >= len(order):
            over.append(ch)
            return False
        p = order[pi]
        mapping[ch] = (p, used[p])
        used[p] += 1
        return True

    if entries:
        # 원문 길이 대비 번역이 빠듯한 대사부터 (여유 = len - 글자바이트)
        def slack(e):
            n = sum(2 if is_hangul(c) else 1 for c in e["ko"])
            return e["len"] - n
        # 짧고 빠듯한 대사 먼저 -> 그 음절들이 같은 페이지에 모임
        for e in sorted(entries, key=slack):
            for ch in e["ko"]:
                if is_hangul(ch):
                    put(ch)
    # 남은 음절 (빈도순)
    for ch, _ in freq.most_common():
        put(ch)
    return mapping, over


def decode(data, mapping):
    """게임 바이트열 -> 사람이 읽는 텍스트. (encode 의 역함수)

    🔴 왕복 검증용. encode -> decode 가 원본과 일치하지 않으면
       인코딩에 버그가 있다는 뜻이다.
    """
    rev = {(p, i): ch for ch, (p, i) in mapping.items()}
    names = {v: k for k, v in MACRO_BYTE.items()}
    out = []
    page = None
    i = 0
    while i < len(data):
        b = data[i]
        if b == 0xCF and i + 1 < len(data):
            page = data[i + 1]
            i += 2
        elif b == 0xCE and i + 1 < len(data):
            idx = data[i + 1] - HANGUL_MIN
            if (page, idx) not in rev:
                out.append(f"<?{page}:{idx}>")
            else:
                out.append(rev[(page, idx)])
            i += 2
        elif b in names and i + 1 < len(data):
            prm = data[i + 1]
            out.append(f"{{{names[b]}}}" if prm == 0
                       else f"{{{names[b]}:{prm:02X}}}")
            i += 2
        elif b == 0x7C:
            out.append("{BR}")
            i += 1
        else:
            out.append(chr(b) if 0x20 <= b < 0x7F else f"<{b:02X}>")
            i += 1
    return "".join(out)


def check_roundtrip(entries, mapping):
    """🔴 왕복 검증: encode -> decode 가 원본 번역과 일치하는가?

    이 한 가지 검사가 다음을 전부 잡는다:
      - 매크로를 1바이트로 잘못 인코딩 (파라미터 누락)
      - 음절 인덱스가 1바이트를 넘음
      - 페이지 전환 누락/중복
      - 매크로 삭제/추가
    """
    bad = []
    for e in entries:
        ko = e["ko"]
        try:
            enc, _ = encode(ko, mapping)
        except KeyError as ex:
            bad.append((e, f"음절 미배치: {ex}"))
            continue
        dec = decode(enc, mapping)
        # 공백 패딩은 무시하고 비교
        if dec.rstrip() != ko.rstrip():
            bad.append((e, f"불일치\n        보냄: {ko!r}\n        받음: {dec!r}"))
    if bad:
        print(f"\n🔴 왕복 검증 실패 {len(bad)}건 — 인코딩 버그!")
        for e, why in bad[:8]:
            print(f"   map{e['map']} s{e['script']} r{e['run']}: {why}")
        raise SystemExit("encode/decode 불일치 — 인코딩을 고칠 것")
    return True


def encode(text, mapping, cur_page=None):
    """번역문 -> 게임 바이트열. (bytes, 마지막 페이지)"""
    out = bytearray()
    page = cur_page
    for kind, val in tokenize(text):
        if kind == "h":
            if val not in mapping:
                raise KeyError(f"음절 '{val}' 미배치")
            p, idx = mapping[val]
            if p != page:
                out += bytes([0xCF, p])
                page = p
            out += bytes([0xCE, HANGUL_MIN + idx])
        else:
            out.append(val)
    return bytes(out), page


def analyze():
    entries = json.load(open(DUMP))
    trans = [e for e in entries if e.get("ko")]
    print(f"전체 {len(entries):,}런 / 번역됨 {len(trans):,}런")
    if not trans:
        print("\n★ 아직 번역이 없다. text_dump.json 의 'ko' 필드를 채울 것.")
        return
    check_macros(trans)          # 🔴 매크로 검증 (빠지면 게임 멈춤)
    freq = collect_syllables(trans)
    cap = sum(hook7.capacity().values())
    print(f"\n고유 음절: {len(freq)}개  (용량 {cap})")
    total = sum(freq.values())
    print(f"총 음절 사용: {total:,}회")
    print()
    # 커버율
    srt = freq.most_common()
    for n in (100, 200, 300, 400, 500, len(freq)):
        if n > len(freq):
            continue
        cov = sum(v for _, v in srt[:n]) / total * 100
        mark = "  ← 현재 용량" if n == cap else ""
        print(f"  상위 {n:4d}자: 커버율 {cov:5.1f}%{mark}")
    print()
    mapping, over = assign_pages(freq, trans)
    check_roundtrip(trans, mapping)     # 🔴 encode->decode 왕복 검증
    if over:
        print(f"★ 용량 초과 음절 {len(over)}개: {''.join(over[:40])}")
        # 이 음절이 나오는 런은 삽입 불가
        bad = sum(1 for e in trans if any(ch in over for ch in e["ko"]))
        print(f"   -> 영향받는 런: {bad}/{len(trans)} ({bad/len(trans)*100:.1f}%)")
    else:
        print("✓ 모든 음절이 용량 안에 들어감")
    print()
    # 길이 검사
    fail = []
    for e in trans:
        try:
            b, _ = encode(e["ko"], mapping)
        except KeyError:
            continue
        if len(b) > e["len"]:
            fail.append((e, len(b)))
    print(f"길이 초과: {len(fail)}/{len(trans)}")
    for e, n in fail[:5]:
        print(f"  map{e['map']} s{e['script']} r{e['run']}: {n}B > {e['len']}B  '{e['ko'][:30]}'")
    # 페이지 전환 빈도
    sw = 0
    for e in trans:
        try:
            b, _ = encode(e["ko"], mapping)
        except KeyError:
            continue
        sw += b.count(0xCF)
    print(f"\n페이지 전환: {sw}회 (런당 평균 {sw/len(trans):.2f})")


# ─────────────────────────────────────────────────────────
# 실제 삽입
# ─────────────────────────────────────────────────────────
def build_patch(prot_in=None, exe_in=None, out_prot=None, out_exe=None, dump=None):
    """text_dump.json 의 ko 를 PROT 에 in-place 삽입"""
    import numpy as np
    from PIL import Image, ImageFont, ImageDraw
    from legaia import prot_files, read_u24
    import subprocess, tempfile, os

    HERE = os.path.dirname(os.path.abspath(__file__))
    LZ = os.path.join(HERE, "lzss_fast")
    if os.name == "nt" and not os.path.exists(LZ):
        LZ = LZ + ".exe"

    from legaia import lzss_compress as _lzss_py

    # 압축 캐시: 같은 스크립트 블록이면 재압축하지 않는다.
    #   빌드를 반복할 때(번역 몇 줄만 고치고 다시 빌드) 큰 차이가 난다.
    import hashlib
    CACHE_DIR = os.path.join(HERE, "..", "build", ".lzcache")
    os.makedirs(CACHE_DIR, exist_ok=True)

    def _cached(data):
        key = hashlib.sha1(data).hexdigest()[:16]
        cf = os.path.join(CACHE_DIR, key)
        if os.path.exists(cf):
            return open(cf, "rb").read()
        out = _compress_raw(data)
        open(cf, "wb").write(out)
        return out

    def _compress_raw(data):
        """C 구현이 있으면 쓰고, 없으면 파이썬 구현 (출력은 동일).

        파이썬 구현도 최적화되어 맵당 수 초면 끝난다.
        Windows 에서 gcc 없이도 그냥 돌아가도록 하기 위함.
        """
        if not os.path.exists(LZ):
            return _lzss_py(data)
        with tempfile.NamedTemporaryFile(delete=False) as fi:
            fi.write(data); ip = fi.name
        op = ip + ".z"
        try:
            subprocess.run([LZ, "c", ip, op], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            out = open(op, "rb").read()
        finally:
            for f in (ip, op):
                if os.path.exists(f):
                    os.unlink(f)
        return out

    lzss_compress = _cached
    from protected import assert_protected

    PROT_US = prot_in or "/home/claude/legaia/cn/PROT_US.DAT"
    EXE_US = exe_in or "/home/claude/legaia/cn/SCUS_US.exe"
    OUT_PROT = out_prot or "/home/claude/legaia/build/PROT_KR8.DAT"
    OUT_EXE = out_exe or "/home/claude/legaia/build/SCUS_KR8.exe"
    CLUT_OFF, ASCII_PIX, NUM_PIX = 0x01122C, 0x007F80, 0x011438
    GW = GH = 12
    INK = 3
    FONT = os.path.join(HERE, "Galmuri11.ttf")

    entries = json.load(open(dump or DUMP, encoding="utf-8"))
    trans = [e for e in entries if e.get("ko")]
    print(f"[1] 번역 {len(trans)}런")

    check_macros(trans)          # 🔴 매크로 검증 (빠지면 게임 멈춤)
    print("[1] 🔴 매크로 검증 통과")

    freq = collect_syllables(trans)
    mapping, over = assign_pages(freq, trans)
    check_roundtrip(trans, mapping)     # 🔴 encode->decode 왕복 검증
    print("[2] 🔴 왕복 검증 통과 (encode/decode 일치)")
    print(f"[2] 음절 {len(freq)}개 배치 (초과 {len(over)}개)")

    # --- 폰트 생성 ---
    ft = ImageFont.truetype(FONT, 12)

    def glyph(ch):
        img = Image.new("L", (16, 16), 0)
        ImageDraw.Draw(img).text((0, 0), ch, font=ft, fill=255)
        a = np.asarray(img)
        ys, xs = np.nonzero(a)
        out = np.zeros((GH, GW), np.uint8)
        if len(ys) == 0:
            return out
        y0, x0 = ys.min(), xs.min()
        for y in range(GH):
            for x in range(GW):
                sy, sx = y + y0, x + x0
                if sy < 16 and sx < 16 and a[sy, sx] > 128:
                    out[y, x] = INK
        return out

    def unpack(buf, off):
        a = np.frombuffer(buf[off:off + 32768], dtype=np.uint8)
        lo = a & 0xF
        hi = (a >> 4) & 0xF
        q = np.empty(a.size * 2, np.uint8)
        q[0::2] = lo
        q[1::2] = hi
        return q.reshape(256, 256)

    def pack(P):
        f = P.reshape(-1)
        return bytes((f[0::2] & 0xF) | ((f[1::2] & 0xF) << 4))

    prot = bytearray(open(PROT_US, "rb").read())
    ascii_px = unpack(bytes(prot), ASCII_PIX)
    num_px = unpack(bytes(prot), NUM_PIX)
    before = int((num_px[144:] != 0).sum())
    num_px[0:144] = 0        # 페이지2/3 영역 클리어 (숫자 V144+ 는 보존)

    for ch, (p, i) in mapping.items():
        U, V = hook7.uv(i, p)
        tgt = ascii_px if hook7.BUF[p] == "ascii" else num_px
        tgt[V:V + GH, U:U + GW] = glyph(ch)
    assert int((num_px[144:] != 0).sum()) == before, "숫자 폰트 훼손!"
    prot[ASCII_PIX:ASCII_PIX + 32768] = pack(ascii_px)
    prot[NUM_PIX:NUM_PIX + 32768] = pack(num_px)
    print(f"[3] 폰트 {len(mapping)}자 삽입")

    # --- 팔레트 ---
    def rgb15(r, g, b):
        return ((b // 8) << 10) | ((g // 8) << 5) | (r // 8)
    #   값3     = 한글 잉크 (12x12 픽셀폰트, 단색)
    #   값14,15 = ASCII 글리프(기호 ?!., 영문)의 잉크
    #
    #   ★ ASCII 글리프는 값14=외곽선, 값15=본체 로 안티앨리어싱을 이룬다.
    #     - 둘 다 흰색으로 만들면 → 글자가 뭉개진다 (겪음)
    #     - 원본 row6 색(32,32,32 / 128,128,128)을 그대로 쓰면
    #       → 기호가 한글(흰색)보다 어두워 색이 안 맞는다 (겪음)
    #   해결: 한글과 같은 밝기로 올리되 명암 대비는 유지
    #         값15(본체) = 흰색, 값14(외곽) = 중간 회색
    for row in (7, 8, 9, 10):
        struct.pack_into("<H", prot, CLUT_OFF + row * 32 + INK * 2,
                         rgb15(248, 248, 248))   # 한글 잉크
        struct.pack_into("<H", prot, CLUT_OFF + row * 32 + 15 * 2,
                         rgb15(248, 248, 248))   # 기호 본체
        struct.pack_into("<H", prot, CLUT_OFF + row * 32 + 14 * 2,
                         rgb15(128, 128, 128))   # 기호 외곽선
    print("[4] 팔레트: 한글(3)+기호본체(15)=흰색 / 기호외곽(14)=회색")

    # --- 텍스트 삽입 (맵별로 PACK 재구성) ---
    def wu24(b, v):
        b += bytes([v & 0xFF, (v >> 8) & 0xFF, (v >> 16) & 0xFF])

    bymap = {}
    for e in trans:
        bymap.setdefault(e["map"], []).append(e)

    files = {f[0]: (f[1], f[2]) for f in prot_files(bytes(prot))}
    done = skip_len = skip_syl = 0
    tight = []
    for mid, es in sorted(bymap.items()):
        if mid not in files:
            continue
        ms, me = files[mid]
        ents = pack_parse(bytes(prot[ms:me]))
        sblob = [d for f, d in ents if f == 3]
        if not sblob:
            continue
        sblob = sblob[0]
        scripts = [bytearray(x) for x in script_parse(sblob)]
        changed = False
        for e in es:
            sid = e["script"]
            if sid >= len(scripts):
                continue
            try:
                b, _ = encode(e["ko"], mapping)
            except KeyError:
                skip_syl += 1
                continue
            if len(b) > e["len"]:
                skip_len += 1
                continue
            sc = scripts[sid]
            o = e["off"]
            e["_orig"] = bytes(sc[o:o + e["len"]])     # 되돌리기용 원문 백업
            sc[o:o + e["len"]] = b + b"\x20" * (e["len"] - len(b))
            e["_done"] = True
            done += 1
            changed = True
        if not changed:
            continue
        # 스크립트 블록 재구성
        hdr = sblob[:0x22]
        c0, c1, c2 = struct.unpack("<HHH", sblob[0x22:0x28])
        cnt = c0 + c1 + c2
        sbase = 0x2B + cnt * 3
        footer = sblob[read_u24(sblob, 0x28) + sbase:]
        cur = sum(len(x) for x in scripts)
        nb = bytearray(hdr) + struct.pack("<HHH", c0, c1, c2)
        wu24(nb, cur)
        o2 = 0
        for x in scripts:
            wu24(nb, o2)
            o2 += len(x)
        for x in scripts:
            nb += x
        nb += footer
        # ★ 스크립트(fileId 3) 만 바뀌었다. 나머지 엔트리는 원본의
        #   '압축된 바이트'를 그대로 재사용한다 (재압축은 낭비 — 맵당 수십 초).
        raw = bytes(prot[ms:me])
        n_old, _tot_old = struct.unpack("<II", raw[:8])
        # ★ 마지막 엔트리의 끝 = PACK 의 실제 끝.
        #   raw[addr:] 로 자르면 슬롯의 0 패딩까지 포함되어 PACK 이 부풀고
        #   슬롯 초과가 난다. (실제로 겪은 버그 — 번역이 전부 되돌려짐)
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

        newents = [(f, (bytes(nb) if f == 3 else d)) for f, d in ents]
        n = len(newents)
        tbl = bytearray()
        body = bytearray()
        addr = 8 + n * 8
        tot = 0
        for fid, dec in newents:
            if fid == 3:
                comp = lzss_compress(dec)          # 바뀐 것만 재압축
            else:
                comp = old_comp[fid]               # 원본 압축 데이터 재사용
            wu24(tbl, len(dec))
            tbl.append(fid)
            tbl += struct.pack("<I", addr)
            body += comp
            addr += len(comp)
            tot += len(dec)
        newpack = struct.pack("<II", n, tot) + bytes(tbl) + bytes(body)
        slot = me - ms

        # ★ 원본 PACK 이 슬롯을 꽉 채운 맵이 있다 (여유 0B).
        #   한글은 영어만큼 압축되지 않아 재압축 결과가 슬롯을 넘을 수 있다.
        #   PROT 전체 여유가 0.4% 뿐이라 파일 재배치는 불가.
        #   -> 넘치면 번역을 '긴 것부터' 하나씩 되돌려 슬롯에 맞춘다.
        if len(newpack) > slot:
            applied = [e for e in es if e.get("_done")]
            applied.sort(key=lambda e: -len(e["ko"]))
            dropped = 0
            for e in applied:
                if len(newpack) <= slot:
                    break
                sc = scripts[e["script"]]
                o = e["off"]
                sc[o:o + e["len"]] = e["_orig"]        # 원문 복구
                e["_done"] = False
                dropped += 1
                done -= 1
                # 스크립트 블록 + PACK 재구성
                nb = bytearray(hdr) + struct.pack("<HHH", c0, c1, c2)
                cur2 = sum(len(x) for x in scripts)
                wu24(nb, cur2)
                off3 = 0
                for x in scripts:
                    wu24(nb, off3)
                    off3 += len(x)
                for x in scripts:
                    nb += x
                nb += footer
                body = bytearray()
                tbl = bytearray()
                addr = 8 + n * 8
                tot = 0
                for fid, dec in [(f, (bytes(nb) if f == 3 else d)) for f, d in ents]:
                    comp = lzss_compress(dec) if fid == 3 else old_comp[fid]
                    wu24(tbl, len(dec))
                    tbl.append(fid)
                    tbl += struct.pack("<I", addr)
                    body += comp
                    addr += len(comp)
                    tot += len(dec)
                newpack = struct.pack("<II", n, tot) + bytes(tbl) + bytes(body)
            if len(newpack) > slot:
                tight.append((mid, len(es), len(es)))
                continue
            tight.append((mid, dropped, len(es)))
        prot[ms:me] = newpack + b"\x00" * (slot - len(newpack))
    print(f"[5] 삽입 {done}런 (길이초과 {skip_len}, 음절없음 {skip_syl})")
    if tight:
        lost = sum(d for _, d, _ in tight)
        print(f"[5] ⚠ 슬롯이 꽉 찬 맵 {len(tight)}개에서 {lost}런을 넣지 못함")
        print(f"       (원본 PACK 이 슬롯을 거의 다 써서 한글이 안 들어감)")
        for mid, d, n in tight[:6]:
            print(f"       map{mid}: {d}/{n}런 실패")

    assert_protected(open(PROT_US, "rb").read(), bytes(prot))
    print("[6] 🔴 보호구역 검증 통과")
    open(OUT_PROT, "wb").write(bytes(prot))

    # --- EXE ---
    exe = open(EXE_US, "rb").read()
    idx_bytes = sorted({hook7.HANGUL_MIN + i for _, i in mapping.values()})
    exe2, hook = hook7.patch_exe(exe, idx_bytes)
    open(OUT_EXE, "wb").write(exe2)
    print(f"[7] 훅 {len(hook)}B, 폭테이블 {len(idx_bytes)}개")
    json.dump({ch: list(v) for ch, v in mapping.items()},
              open(os.path.join(os.path.dirname(OUT_PROT), "font_map.json"), "w"),
              ensure_ascii=False)
    print("[8] 저장 완료")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--analyze", action="store_true")
    ap.add_argument("--build", action="store_true")
    a = ap.parse_args()
    if a.build:
        build_patch()
    else:
        analyze()

