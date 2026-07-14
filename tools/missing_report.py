#!/usr/bin/env python3
"""폰트 용량 부족 기록.

현재 폰트는 400칸뿐인데, map6 하나만 번역해도 476자가 필요하다.
칸에 못 들어간 음절이 생기고, 그 음절을 쓰는 대사는 **영어로 남는다**.

이 도구는 그걸 기록한다:
    build/missing_syllables.json   기계용 (나중에 자동 복구)
    build/missing_syllables.md     사람용 (읽고 다듬기)

용량이 늘면(ASCII 회수 → 580칸) 이 기록을 보고 어떤 대사가 살아나는지 알 수 있고,
지금 당장은 "이 음절만 피하면 이 대사가 나온다"는 걸 알 수 있다.
"""
import os
import sys
import json
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import translate as T          # noqa: E402
import hook7                   # noqa: E402
import project_store as PS     # noqa: E402


def analyze(dump_path=None):
    dump = dump_path or os.path.join(HERE, "..", "build", "text_dump.json")
    d = json.load(open(dump, encoding="utf-8"))
    trans = [e for e in d if e.get("ko")]
    if not trans:
        return None

    cap = sum(hook7.capacity().values())
    freq = T.collect_syllables(trans)
    mapping, _ = T.assign_pages(freq, trans)

    # 폰트에 못 들어간 음절
    missing = {c: n for c, n in freq.items() if c not in mapping}

    # 그 음절 때문에 영어로 남는 대사
    blocked = []
    for e in trans:
        bad = sorted({c for c in e["ko"] if T.is_hangul(c) and c not in mapping})
        if bad:
            blocked.append({
                "map": e["map"], "script": e["script"], "run": e["run"],
                "en": e["text"], "ko": e["ko"],
                "missing": bad,
            })

    # 맵별 집계
    by_map = defaultdict(int)
    for b in blocked:
        by_map[b["map"]] += 1

    return {
        "capacity": cap,
        "needed": len(freq),
        "over": max(0, len(freq) - cap),
        "missing_syllables": dict(sorted(missing.items(), key=lambda x: -x[1])),
        "blocked_runs": blocked,
        "blocked_by_map": dict(sorted(by_map.items())),
        "translated_runs": len(trans),
    }


def write_report(rep, outdir=None):
    outdir = outdir or os.path.join(HERE, "..", "build")
    # 기계용
    jp = os.path.join(outdir, "missing_syllables.json")
    json.dump(rep, open(jp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    # 사람용
    lines = []
    lines.append("# 폰트 용량 부족 기록\n")
    lines.append(f"- 번역 **{rep['translated_runs']:,}런**")
    lines.append(f"- 필요한 음절 **{rep['needed']}자** / 폰트 칸 **{rep['capacity']}칸**")
    if rep["over"]:
        lines.append(f"- 🔴 **{rep['over']}자 초과** → "
                     f"**{len(rep['blocked_runs'])}런이 영어로 남습니다**")
    else:
        lines.append("- ✅ 전부 들어갑니다")
    lines.append("")

    if rep["missing_syllables"]:
        lines.append("## 폰트에 못 넣은 음절\n")
        lines.append("빈도가 낮은 것부터 잘립니다. 이 음절을 피해 번역을 다듬으면 대사가 살아납니다.\n")
        items = list(rep["missing_syllables"].items())
        lines.append("| 음절 | 쓰인 횟수 |")
        lines.append("|---|---|")
        for c, n in items:
            lines.append(f"| {c} | {n} |")
        lines.append("")
        lines.append("한 줄로: `" + "".join(c for c, _ in items) + "`\n")

    if rep["blocked_by_map"]:
        lines.append("## 맵별 영향\n")
        lines.append("| 맵 | 영어로 남는 런 |")
        lines.append("|---|---|")
        for m, n in rep["blocked_by_map"].items():
            lines.append(f"| map{m} | {n} |")
        lines.append("")

    if rep["blocked_runs"]:
        lines.append("## 영어로 남는 대사\n")
        for b in rep["blocked_runs"]:
            lines.append(f"- **m{b['map']} s{b['script']}r{b['run']}** "
                         f"— 없는 음절: `{''.join(b['missing'])}`")
            lines.append(f"  - 원문: `{b['en']}`")
            lines.append(f"  - 번역: `{b['ko']}`")
        lines.append("")

    mp = os.path.join(outdir, "missing_syllables.md")
    open(mp, "w", encoding="utf-8").write("\n".join(lines))
    return jp, mp


if __name__ == "__main__":
    rep = analyze()
    if not rep:
        print("번역이 없습니다.")
        sys.exit(0)
    jp, mp = write_report(rep)
    print(f"번역 {rep['translated_runs']:,}런")
    print(f"필요 음절 {rep['needed']}자 / 폰트 {rep['capacity']}칸")
    if rep["over"]:
        print(f"🔴 {rep['over']}자 초과 → {len(rep['blocked_runs'])}런이 영어로 남습니다")
        print()
        print("못 넣은 음절:")
        print("  " + "".join(rep["missing_syllables"]))
        print()
        print("맵별 영향:")
        for m, n in rep["blocked_by_map"].items():
            print(f"  map{m}: {n}런")
    else:
        print("✅ 전부 들어갑니다")
    print()
    print(f"기록:\n  {jp}\n  {mp}")
