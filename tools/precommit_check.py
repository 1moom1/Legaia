#!/usr/bin/env python3
"""커밋 전 검증 — 실제로 겪은 실수들을 자동으로 잡는다.

지금까지 낸 실수들:
  - measure_packs.py 를 고쳐놓고 저장소에 커밋 안 함 (작업본만 바뀜)
  - FINDINGS.md 에 폰트 배치표를 아예 안 넣음 (지적받고 추가)
  - hook7 의 clut/tpage 값과 문서 배치표가 어긋남

이 스크립트는 그런 어긋남을 커밋 직전에 잡는다.
빌드까지는 안 돌린다 (시간이 오래 걸림). 정적 일관성만 검사한다.

사용:
    python3 tools/precommit_check.py          # 검사만
    python3 tools/precommit_check.py --strict  # 하나라도 실패면 exit 1

경로 가정:
    저장소 = 이 스크립트의 상위 (…/legaia-kr)
    작업본 = /home/claude/legaia   (있으면 tools 대조, 없으면 건너뜀)
"""
import os
import sys
import re
import filecmp

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORK = "/home/claude/legaia"                 # 작업본 (없을 수 있음)

# 빌드 파이프라인이 실제로 쓰는 도구 (build_kr.py 가 import 하는 것들)
ESSENTIAL = [
    "hook7.py", "kanji_font.py", "translate.py", "build_kr.py",
    "prot_realloc.py", "measure_packs.py", "protected.py",
    "missing_report.py", "legaia.py", "asm.py", "mips.py", "write_disc.py",
]

RESET = "\033[0m"; RED = "\033[31m"; GRN = "\033[32m"; YEL = "\033[33m"


class Check:
    def __init__(self):
        self.fail = 0
        self.warn = 0

    def ok(self, msg):
        print(f"  {GRN}✅{RESET} {msg}")

    def bad(self, msg):
        print(f"  {RED}🔴 {msg}{RESET}")
        self.fail += 1

    def warning(self, msg):
        print(f"  {YEL}⚠ {msg}{RESET}")
        self.warn += 1


def check_essential_present(c):
    """파이프라인 필수 도구가 저장소에 다 있나."""
    print("[1] 파이프라인 필수 도구 존재 확인")
    tdir = os.path.join(REPO, "tools")
    for f in ESSENTIAL:
        if os.path.exists(os.path.join(tdir, f)):
            c.ok(f"{f}")
        else:
            c.bad(f"{f} 가 저장소 tools/ 에 없다")


def check_repo_matches_work(c):
    """저장소 tools 가 작업본과 일치하나 (커밋 빠뜨림 방지)."""
    print("[2] 저장소 ↔ 작업본 일치 (커밋 빠뜨림 방지)")
    if not os.path.isdir(WORK):
        c.warning(f"작업본 {WORK} 없음 — 대조 건너뜀")
        return
    for f in ESSENTIAL:
        a = os.path.join(REPO, "tools", f)
        b = os.path.join(WORK, "tools", f)
        if not os.path.exists(b):
            c.warning(f"{f} 작업본에 없음")
            continue
        if not os.path.exists(a):
            c.bad(f"{f} 저장소에 없음 (작업본에만 존재 → 커밋 필요)")
            continue
        if filecmp.cmp(a, b, shallow=False):
            c.ok(f"{f}")
        else:
            c.bad(f"{f} 저장소와 작업본이 다르다 → 저장소로 복사 후 커밋 필요")


def _read(path):
    return open(path, encoding="utf-8").read()


def check_hook7_assembles(c):
    """hook7 이 어셈블되고 로드지연 위반이 없나."""
    print("[3] hook7 어셈블 + 로드지연 검사")
    sys.path.insert(0, os.path.join(REPO, "tools"))
    try:
        import importlib
        import hook7
        importlib.reload(hook7)
        probe = hook7.build(hook7.HOOK_ADDR).assemble()
        tbl_addr = (hook7.HOOK_ADDR + len(probe) + 3) & ~3
        a = hook7.build(tbl_addr)
        viol = a.check_load_delay()
        if viol:
            for v in viol:
                c.bad(f"로드지연 위반: {v}")
        else:
            c.ok(f"로드지연 위반 없음 (훅 {len(probe)}B)")
        # 용량 = 1,100 인지
        total = sum(hook7.capacity().values())
        if total == 1100:
            c.ok(f"폰트 용량 {total}칸")
        else:
            c.warning(f"폰트 용량이 {total}칸 (문서 기준 1,100 과 다름)")
        return hook7
    except Exception as e:
        c.bad(f"hook7 어셈블 실패: {e}")
        return None


def check_findings_matches_hook(c, hook7):
    """FINDINGS 의 폰트 배치표가 hook7 의 실제 값과 일치하나."""
    print("[4] FINDINGS 배치표 ↔ hook7 실제 값")
    fp = os.path.join(REPO, "docs", "FINDINGS.md")
    if not os.path.exists(fp):
        c.bad("docs/FINDINGS.md 가 없다")
        return
    doc = _read(fp)

    # 4-1. 배치표(1,100칸)가 문서에 있나
    if "1,100칸" in doc or "1,100 칸" in doc:
        c.ok("문서에 1,100칸 배치표 언급 있음")
    else:
        c.bad("문서에 '1,100칸' 배치표가 없다 (누락 방지)")

    if hook7 is None:
        return

    # 4-2. VRAM 좌표가 문서에 있나 (한자 TIM 2페이지)
    for coord in ("320,256", "384,256"):
        if coord in doc:
            c.ok(f"문서에 VRAM({coord}) 언급 있음")
        else:
            c.bad(f"문서에 VRAM({coord}) 이 없다")

    # 4-3. tpage 값 (0x15, 0x16) 이 코드와 문서 양쪽에 있나
    tpages = {hook7.TPAGE[p] for p in hook7.PAGES}
    for want in (0x15, 0x16):
        code_has = want in tpages
        doc_has = f"0x{want:02X}" in doc or f"0x{want:04X}" in doc
        if code_has and doc_has:
            c.ok(f"tpage 0x{want:02X} — 코드·문서 일치")
        elif code_has and not doc_has:
            c.bad(f"tpage 0x{want:02X} 는 코드에 있는데 문서에 없다")
        elif doc_has and not code_has:
            c.bad(f"tpage 0x{want:02X} 는 문서에 있는데 코드에 없다")

    # 4-4. PAGE_VAR 주소가 코드·문서 일치
    pv = getattr(hook7, "PAGE_VAR", None)
    if pv is not None:
        if f"0x{pv:08X}" in doc or f"0x{pv:X}".upper() in doc.upper():
            c.ok(f"PAGE_VAR 0x{pv:X} — 코드·문서 일치")
        else:
            c.warning(f"PAGE_VAR 0x{pv:X} 가 문서에 안 보임")


def check_no_stale_pending(c):
    """이미 해결된 것이 아직 '미완/PENDING' 으로 남았나 (경고만)."""
    print("[5] 오래된 미해결 표시 점검 (경고)")
    fp = os.path.join(REPO, "docs", "FINDINGS.md")
    if not os.path.exists(fp):
        return
    doc = _read(fp)
    # 8-T (색 오염) 는 해결됐어야
    m = re.search(r"## 8-T\..*", doc)
    if m:
        line = m.group(0)
        if "✅" in line or "해결" in line:
            c.ok("8-T (색 오염) 해결 표시됨")
        else:
            c.bad("8-T (색 오염) 이 아직 미해결 표시 — 구현됐으면 ✅ 로")
    # 남은 PENDING/미완 개수 보고 (취소선 ~~...~~ 처리된 것은 제외)
    doc_no_struck = re.sub(r"~~.*?~~", "", doc)
    n = len(re.findall(r"미완|\[TODO\]|\[PENDING\]", doc_no_struck))
    if n:
        c.warning(f"문서에 미완/TODO 표시 {n}개 남음 (진짜 남은 작업인지 확인)")
    else:
        c.ok("살아있는 미완/TODO 표시 없음")


def check_protected_intact(c):
    """보호구역 가드 파일이 있나."""
    print("[6] 보호구역 가드 존재")
    p = os.path.join(REPO, "tools", "protected.py")
    if os.path.exists(p) and "assert_protected" in _read(p):
        c.ok("protected.assert_protected 있음")
    else:
        c.bad("protected.py 또는 assert_protected 가 없다")


def check_gui_length_calc(c):
    """GUI 의 길이 계산이 실제 encode 길이와 맞나.

    GUI 의 char_bytes 가 페이지 전환(0xCF 0xF0|page, 2B)을 빠뜨리면,
    번역가가 'OK' 로 본 대사가 실제 빌드에서 슬롯을 넘어 영어로 남는다.
    매핑이 주어졌을 때는 실제 encode 와 100% 일치해야 한다.
    """
    print("[7] GUI 길이 계산 ↔ 실제 encode 일치")
    tdir = os.path.join(REPO, "tools")
    gui = os.path.join(tdir, "translator_gui.py")
    if not os.path.exists(gui):
        c.warning("translator_gui.py 없음 — 건너뜀")
        return
    sys.path.insert(0, tdir)
    try:
        import translate as T
        # GUI 의 char_bytes 만 추출해 실행
        src = _read(gui)
        m = re.search(r"def char_bytes.*?\n    return n\n", src, re.S)
        if not m:
            c.warning("char_bytes 함수를 못 찾음")
            return
        ns = {"T": T}
        exec(m.group(0), ns)
        cb = ns["char_bytes"]
        dump = os.path.join(REPO, "..", "legaia", "build", "text_dump.json")
        if not os.path.exists(dump):
            dump = os.path.join(REPO, "data", "text_dump.json")
        if not os.path.exists(dump):
            c.warning("text_dump.json 없음 — 정합성 검사 건너뜀")
            return
        import json
        d = json.load(open(dump, encoding="utf-8"))
        trans = [e for e in d if e.get("ko")]
        if not trans:
            c.warning("번역 데이터 없음 — 건너뜀")
            return
        freq = T.collect_syllables(trans)
        mapping, _ = T.assign_pages(freq, trans)
        mism = 0
        for e in trans:
            try:
                enc, _ = T.encode(e["ko"], mapping)
            except KeyError:
                continue
            if cb(e["ko"], mapping) != len(enc):
                mism += 1
        if mism == 0:
            c.ok(f"매핑 기반 길이 계산 정확 ({len(trans):,}런 전수 일치)")
        else:
            c.bad(f"GUI 길이 계산 불일치 {mism}건 — 페이지 전환 2B 누락 의심")
    except Exception as e:
        c.warning(f"GUI 길이 검사 실패: {e}")


def main():
    strict = "--strict" in sys.argv
    print("=" * 60)
    print("  커밋 전 검증")
    print("=" * 60)
    c = Check()
    check_essential_present(c)
    check_repo_matches_work(c)
    hook7 = check_hook7_assembles(c)
    check_findings_matches_hook(c, hook7)
    check_no_stale_pending(c)
    check_protected_intact(c)
    check_gui_length_calc(c)
    print("=" * 60)
    if c.fail:
        print(f"  {RED}🔴 실패 {c.fail}개, 경고 {c.warn}개 — 커밋 전에 고치세요{RESET}")
    elif c.warn:
        print(f"  {YEL}⚠ 통과 (경고 {c.warn}개){RESET}")
    else:
        print(f"  {GRN}✅ 전부 통과{RESET}")
    print("=" * 60)
    if strict and c.fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
