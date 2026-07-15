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
import json, os, struct, sys, re, argparse
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
    """페이지 배치 — **빈도순**.

    페이지 전환 코드(0xCF <p>)가 2바이트를 먹으므로, 한 대사의 음절이
    여러 페이지에 흩어지면 길이 초과가 날 수 있다. 그래서 예전엔
    '함께 나오는 음절을 같은 페이지에 몰아넣는' 전략을 썼다.

    하지만 실측해 보니 그 전략은 **자주 쓰이는 음절을 놓친다**.
    (예: '씐'은 41회나 쓰이는데도 잘렸다 — 그 대사의 여유가 넉넉해서
     처리 순서가 밀렸기 때문)

    폰트 칸이 모자란 지금은 **빈도순이 압도적으로 낫다**:

        빠듯한 대사 우선 : 삽입 1,626런 (음절없음 143, 길이초과 693)
        빈도순           : 삽입 2,059런 (음절없음  67, 길이초과 336)  ★

    빈도가 높은 음절은 여러 대사에 걸쳐 나오므로, 먼저 넣으면
    자연스럽게 같은 페이지에 모이고 페이지 전환도 오히려 줄어든다.
    """
    cap = hook7.capacity()
    # 페이지 순서 — 빈도 높은 음절이 앞 페이지에 간다.
    #   p1/p2/p4     : 원본이 비워둔 자리 (검증됨)
    #   p3/p5~p7     : 한자 TIM 1 (320,256)
    #   p8~p11       : 한자 TIM 2 (384,256)
    order = [1, 2, 4, 3, 5, 6, 7, 8, 9, 10, 11]

    # ★ 테스트 모드: 모든 페이지를 골고루 쓰게 한다.
    #   실사용 배치로는 음절이 478자뿐이라 뒤쪽 페이지(p6~p11)가 비어 있어,
    #   그 영역이 실제로 화면에 제대로 나오는지 확인할 수 없다.
    #   LEGAIA_TEST_PAGES=1 로 빌드하면 음절을 11개 페이지에 라운드로빈으로 뿌려
    #   **모든 폰트 영역을 한 번에 검증**할 수 있다.
    #   (페이지 전환이 늘어 길이 초과가 많아지므로 배포용은 아니다)
    if os.environ.get("LEGAIA_TEST_P8") == "1":
        # ★ p8~p11 (한자 TIM2, VRAM 384,256) 만 써서 그 영역을 격리 검증한다.
        #   모든 음절을 p8~p11 에만 몰아넣는다.
        only = [8, 9, 10, 11]
        mapping = {}
        used = {p: 0 for p in only}
        over = []
        pi = 0
        for ch, _ in freq.most_common():
            placed = False
            for _ in range(len(only)):
                p = only[pi]
                pi = (pi + 1) % len(only)
                if used[p] < cap[p]:
                    mapping[ch] = (p, used[p]); used[p] += 1; placed = True; break
            if not placed:
                over.append(ch)
        print("[2] ★ p8~p11 격리 테스트: 한자 TIM2 (384,256) 만 사용")
        print(f"    페이지별: {dict(sorted(used.items()))}  초과 {len(over)}")
        return mapping, over

    if os.environ.get("LEGAIA_TEST_PAGES") == "1":
        mapping = {}
        used = {p: 0 for p in order}
        over = []
        pi = 0
        for ch, _ in freq.most_common():
            placed = False
            for _ in range(len(order)):          # 빈 자리가 나올 때까지 순환
                p = order[pi]
                pi = (pi + 1) % len(order)
                if used[p] < cap[p]:
                    mapping[ch] = (p, used[p])
                    used[p] += 1
                    placed = True
                    break
            if not placed:
                over.append(ch)
        print("[2] ★ 테스트 모드: 음절을 전 페이지에 라운드로빈 배치")
        print(f"    페이지별: {dict(sorted(used.items()))}")
        return mapping, over
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
            n = data[i + 1]
            if n >= 0xF0:            # 우리 페이지 전환 (0xF0|page)
                page = n & 0x0F
            # n < 0xF0 은 원본 색 코드 — 페이지 매핑과 무관
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
    missing = []          # 폰트 용량 부족으로 음절이 없는 것 (인코딩 버그가 아니다)
    for e in entries:
        ko = e["ko"]
        try:
            enc, _ = encode(ko, mapping)
        except KeyError as ex:
            missing.append((e, str(ex)))
            continue
        dec = decode(enc, mapping)
        # 공백 패딩은 무시하고 비교
        if dec.rstrip() != ko.rstrip():
            bad.append((e, f"불일치\n        보냄: {ko!r}\n        받음: {dec!r}"))
    if missing:
        # 폰트 칸이 모자라 못 넣는 음절 — 그 대사는 영어로 남는다.
        syl = set()
        for e, msg in missing:
            for c in e["ko"]:
                if is_hangul(c) and c not in mapping:
                    syl.add(c)
        print(f"\n⚠ 폰트 용량 부족: {len(missing)}런이 영어로 남습니다")
        print(f"   못 넣은 음절 {len(syl)}자: {''.join(sorted(syl))[:60]}")
        print(f"   (폰트 칸을 늘리거나, 그 음절을 쓰지 않도록 번역을 다듬으세요)")
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
                # 🔴 페이지 전환은 0xCF <0xF0|page> 로 인코딩한다.
                #    0xCF 는 원본에서 '글자 색' 제어코드다 (PAL_VAR = n).
                #    원본은 n = 0~10 및 큰 값(46,254 등)을 색으로 쓰지만
                #    0xF0~0xFB 대역은 안 쓴다. 우리 훅이 n>=0xF0 을 페이지로
                #    가로채고, n<0xF0 은 원본 색 처리로 넘긴다.
                #    (예전엔 0xCF <page> 로 PAL_VAR 에 페이지를 직접 넣어,
                #     뒤따르는 영어 색이 오염됐다 — FINDINGS 8-T)
                out += bytes([0xCF, 0xF0 | p])
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

    # ★ lazy matching 압축기 — greedy 보다 ~1.2% 작다.
    #   한글은 영어만큼 압축이 안 돼 PACK 이 슬롯을 넘는데, 이 차이가 결정적이다.
    #   게임 디코더는 표준 LZSS 라 어떻게 인코딩했든 똑같이 풀린다.
    from legaia import lzss_compress_opt as _lzss_py

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
        """★ lazy matching 파이썬 구현을 쓴다.

        C 구현(lzss_fast)은 원본과 같은 greedy 라서 더 크다.
        한글 PACK 이 슬롯을 넘는 상황에선 1.2% 차이가 결정적이므로
        느리더라도 lazy 를 쓴다. (캐시가 있어 재빌드는 빠르다)
        """
        return _lzss_py(data)

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
    # 🔴 원본이 '비워둔 자리'에만 쓴다. 게임 데이터를 덮으면 글자에 그림이 박힌다.
    #    숫자 TIM
    #      V  0~ 79 : 아이콘 TIM 이 덮는다      → 원본 유지 ('얘','쁘' 가 깨졌던 곳)
    #      V 84~143 : 빔 → p2
    #      V144~167 : 숫자 폰트 (HP/MP/LV/G)    → 보호
    #      V168~191 : 빔 → p4
    #      V192~255 : 숫자 폰트                 → 보호
    before_icon = int((num_px[0:84] != 0).sum())
    before_a = int((num_px[144:168] != 0).sum())
    before_b = int((num_px[192:] != 0).sum())
    num_px[84:144] = 0       # p2
    num_px[168:192] = 0      # p4

    #    ASCII TIM
    #      V  0~143 : 영어 글리프               → 보호 (덮으면 영어가 깨진다)
    #      V144~215 : 빔 → p1
    #      V216~239 : 영어 글리프               → 보호
    before_en1 = int((ascii_px[0:144] != 0).sum())
    before_en2 = int((ascii_px[216:240] != 0).sum())
    ascii_px[144:216] = 0    # p1

    # ASCII / 숫자 TIM 만 여기서 그린다. 한자 TIM 은 아래 kanji_font.patch 가 처리.
    for ch, (p, i) in mapping.items():
        buf = hook7.BUF[p]
        if buf.startswith("kanji"):
            continue
        U, V = hook7.uv(i, p)
        tgt = ascii_px if buf == "ascii" else num_px
        tgt[V:V + GH, U:U + GW] = glyph(ch)

    assert int((num_px[0:84] != 0).sum()) == before_icon, "아이콘 영역 훼손! (숫자 V0~83)"
    assert int((num_px[144:168] != 0).sum()) == before_a, "숫자 폰트 훼손! (V144~167)"
    assert int((num_px[192:] != 0).sum()) == before_b, "숫자 폰트 훼손! (V192~255)"
    assert int((ascii_px[0:144] != 0).sum()) == before_en1, "영어 폰트 훼손! (ASCII V0~143)"
    assert int((ascii_px[216:240] != 0).sum()) == before_en2, "영어 폰트 훼손! (ASCII V216~239)"
    prot[ASCII_PIX:ASCII_PIX + 32768] = pack(ascii_px)
    prot[NUM_PIX:NUM_PIX + 32768] = pack(num_px)

    # ★ 한자 폰트 TIM (VRAM 320,256) — file894(raw) + file876(PACK) 둘 다 채운다.
    #   file894 만 바꿨더니 게임에 일본어 한자가 그대로 나왔다 → file876 이 로드된다.
    #   어느 쪽이 언제 로드되는지 확실치 않으므로 양쪽 모두 같은 한글 폰트를 넣는다.
    import kanji_font
    n876, slot876, nhit = kanji_font.patch(prot, mapping, hook7, glyph, GW, GH)
    nk = sum(1 for p, _ in mapping.values() if hook7.BUF[p].startswith("kanji"))
    print(f"[3] 폰트 {len(mapping)}자 삽입 (한자TIM {nk}자, TIM {nhit}개 교체)")
    print(f"    file876 재압축 {n876:,}B / 슬롯 {slot876:,}B "
          f"(여유 {slot876 - n876:,}B)")

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
    # ─────────────────────────────────────────────────────────────
    # ★ [4.5] PROT 재배치 — 맵 슬롯을 넓힌다
    #
    # 원본 맵 PACK 은 슬롯을 꽉 채우고 있다 (맵 여유 합계 3KB).
    # 한글은 영어만큼 LZSS 압축이 안 돼 PACK 이 커지고 슬롯을 넘는다.
    # PROT 전체로는 여유가 있으므로(맵 아닌 파일들), 파일을 섹터 정렬로
    # 다시 배치해서 맵에 필요한 만큼 슬롯을 준다.
    #
    #   빈틈없이 재배치하면 52섹터(106KB)가 생긴다.
    #   슬롯을 넘는 맵이 그보다 많으면, 초과가 작은 맵부터 포기한다
    #   (그 맵은 기존처럼 번역을 조금 되돌려 맞춘다).
    # ─────────────────────────────────────────────────────────────
    import prot_realloc as _PR
    import measure_packs as _MP

    _orig_prot = bytes(prot)
    _need = {}
    for _mid, _es in sorted(bymap.items()):
        if _mid not in files:
            continue
        _ms, _me = files[_mid]
        _np = _MP.rebuild_pack(bytes(_orig_prot[_ms:_me]), _es, mapping)
        if _np:
            _need[_mid] = len(_np)

    _over = sorted(((_need[m] - (files[m][1] - files[m][0]), m)
                    for m in _need if _need[m] > (files[m][1] - files[m][0])),
                   reverse=True)
    if _over:
        _, _, _, _budget = _PR.plan(_orig_prot, {})
        _cap = _budget // _PR.SEC          # 쓸 수 있는 섹터 수
        _grant = {m: _need[m] for _, m in _over[:_cap]}   # 초과가 큰 맵부터
        try:
            _new_prot, _, _, _spare = _PR.rebuild(_orig_prot, _grant)
            _PR.verify(_orig_prot, _new_prot)
            prot = bytearray(_new_prot)
            files = {f[0]: (f[1], f[2]) for f in prot_files(bytes(prot))}
            print(f"[4.5] PROT 재배치: {len(_grant)}개 맵 슬롯 확장 "
                  f"(넘친 맵 {len(_over)}개 중), 남은 여유 {_spare:,}B")
        except Exception as _ex:
            print(f"[4.5] ⚠ 재배치 실패, 원본 배치로 진행: {_ex}")

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

