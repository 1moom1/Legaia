#!/usr/bin/env python3
"""전체 대사 덤프 — PROT 의 모든 텍스트런을 구조화해 추출

출력: JSON
  [
    {"map": 6, "script": 47, "run": 0, "off": 214, "len": 22,
     "text": "The Memory Statue will", "codes": [...]},
    ...
  ]

텍스트런 = <1F> ... <00>
진짜 대사만 (ASCII 인쇄가능 70%+) 필터링.
제어코드는 <XX> 로 표기해 번역자가 보존할 수 있게 함.

특수 코드:
  0xC1 = 캐릭터 이름 매크로   -> {NAME}
  0xC2 = 아이템 이름 매크로   -> {ITEM}
  0xC7 = 기타 매크로          -> {MACRO7}
  0xCF <n> = 팔레트           -> {PAL:n}
  0xCE <n> = 테이블조회       -> {CE:n}
  0x7C = 개행/제어            -> {BR}
"""
import struct, json, sys
sys.path.insert(0, "/home/claude/legaia/tools")
from legaia import pack_parse, script_parse

PROT = "/home/claude/legaia/cn/PROT_US.DAT"
OUT = "/home/claude/legaia/build/text_dump.json"

MACRO = {0xC1: "{NAME}", 0xC2: "{ITEM}", 0xC3: "{M3}", 0xC4: "{M4}",
         0xC5: "{M5}", 0xC6: "{M6}", 0xC7: "{M7}"}
SCRIPT_ID = 3


def decode_run(run):
    """텍스트런 -> 사람이 읽을 수 있는 문자열"""
    out = []
    i = 0
    while i < len(run):
        b = run[i]
        if b == 0x7C:
            out.append("{BR}"); i += 1
        elif b == 0xCF and i + 1 < len(run):
            out.append(f"{{PAL:{run[i+1]}}}"); i += 2
        elif b == 0xCE and i + 1 < len(run):
            out.append(f"{{CE:{run[i+1]}}}"); i += 2
        elif b in MACRO:
            out.append(MACRO[b]); i += 1
        elif 0x20 <= b < 0x7F:
            out.append(chr(b)); i += 1
        else:
            out.append(f"<{b:02X}>"); i += 1
    return "".join(out)


def is_dialogue(run):
    if len(run) < 4:
        return False
    printable = sum(1 for b in run if 0x20 <= b < 0x7F)
    return printable / len(run) >= 0.7


def main():
    pu = open(PROT, "rb").read()
    count = struct.unpack("<I", pu[4:8])[0]
    offs = [struct.unpack("<I", pu[8 + 4 * i:12 + 4 * i])[0] for i in range(count)]

    entries = []
    for mid in range(count - 1):
        s = offs[mid] * 2048
        e = offs[mid + 1] * 2048
        if e - s < 16:
            continue
        try:
            ents = pack_parse(pu[s:e])
        except Exception:
            continue
        if not ents:
            continue
        sb = [d for f, d in ents if f == SCRIPT_ID]
        if not sb:
            continue
        try:
            scripts = script_parse(sb[0])
        except Exception:
            continue
        if not scripts:
            continue
        for sid, sc in enumerate(scripts):
            i = 0
            run_no = 0
            while i < len(sc):
                if sc[i] != 0x1F:
                    i += 1
                    continue
                j = sc.find(b"\x00", i + 1)
                if j < 0:
                    break
                run = sc[i + 1:j]
                if is_dialogue(run):
                    entries.append({
                        "map": mid,
                        "script": sid,
                        "run": run_no,
                        "off": i + 1,
                        "len": len(run),
                        "text": decode_run(run),
                        "ko": "",          # 번역 입력란
                    })
                    run_no += 1
                i = j + 1

    json.dump(entries, open(OUT, "w"), ensure_ascii=False, indent=1)
    total_chars = sum(len(e["text"]) for e in entries)
    maps = len({e["map"] for e in entries})
    print(f"덤프 완료: {len(entries)}런 / {total_chars}자 / {maps}개 맵")
    print(f"저장: {OUT}")
    print()
    print("샘플:")
    for e in entries[:5]:
        print(f"  map{e['map']:4d} s{e['script']:3d} r{e['run']:3d}: {e['text'][:60]}")


if __name__ == "__main__":
    main()
