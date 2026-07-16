#!/usr/bin/env python3
"""번역 빌드 (재배치 포함).

기존 translate.py --build 는 맵 PACK 이 슬롯을 넘으면 번역을 되돌린다.
맵 슬롯은 원본에서 이미 꽉 차 있어(여유 합계 3KB) 이런 일이 자주 생긴다.

이 스크립트는 2패스로 돈다:
    1패스: 번역을 넣었을 때 각 맵 PACK 이 얼마나 필요한지 측정
    2패스: PROT 을 재배치해 그만큼 슬롯을 준 뒤, 번역을 넣는다

재배치는 파일 내용을 그대로 두고 오프셋만 다시 잡는다.
원본이 낭비하던 공간(빈 파일 4개 x 2048B, 큰 파일들의 여유)을 회수해서
맵에 넘겨준다.

사용:
    python3 tools/build_kr.py                       # 기본 경로
    python3 tools/build_kr.py PROT.DAT SCUS.exe 출력폴더
"""
import os
import sys
import json
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from legaia import prot_files                    # noqa: E402
import translate as T                            # noqa: E402
import prot_realloc as PR                        # noqa: E402
import measure_packs as MP                       # noqa: E402
import protected                                 # noqa: E402


def main():
    if len(sys.argv) >= 4:
        prot_in, exe_in, outdir = sys.argv[1], sys.argv[2], sys.argv[3]
    else:
        prot_in = os.path.join(HERE, "..", "cn", "PROT_US.DAT")
        exe_in = os.path.join(HERE, "..", "cn", "SCUS_US.exe")
        outdir = os.path.join(HERE, "..", "build")
    dump = os.path.join(HERE, "..", "build", "text_dump.json")

    prot = open(prot_in, "rb").read()
    files = {f[0]: (f[1], f[2]) for f in prot_files(prot)}
    d = json.load(open(dump, encoding="utf-8"))
    trans = [e for e in d if e.get("ko")]
    print(f"번역 {len(trans):,}런")

    # ── 1패스: 필요한 슬롯 크기 측정 ─────────────────────
    freq = T.collect_syllables(trans)
    mapping, _ = T.assign_pages(freq, trans)
    bym = {}
    for e in trans:
        bym.setdefault(e["map"], []).append(e)

    print("[1] 맵별 필요 슬롯 측정...")
    need = {}
    for m in sorted(bym):
        ms, me = files[m]
        np_ = MP.rebuild_pack(bytes(prot[ms:me]), bym[m], mapping)
        if np_:
            need[m] = len(np_)
    over = sum(max(0, need[m] - (files[m][1] - files[m][0])) for m in need)
    n_over = sum(1 for m in need if need[m] > (files[m][1] - files[m][0]))
    print(f"    슬롯을 넘는 맵 {n_over}개, 총 초과 {over:,}B")

    # ── 2패스: 재배치 ────────────────────────────────────
    if n_over:
        print("[2] PROT 재배치...")
        offs, sizes, total, spare = PR.plan(prot, need)
        if total > PR.PROT_SIZE:
            # 재배치해도 PROT 를 넘는다. 넘치는 만큼은 각 맵의 슬롯에
            # 맞게 3패스(build_patch)가 '긴 번역부터' 자동으로 되돌린다.
            # 그러니 여기서 멈추지 않고, need 를 슬롯 이하로 낮춰 재배치를
            # 최대한 활용한 뒤 나머지는 3패스에 맡긴다.
            over_b = total - PR.PROT_SIZE
            print(f"    ⚠ 재배치해도 {over_b:,}B 부족 — 넘치는 맵은 슬롯에 맞게")
            print(f"       번역을 자동으로 되돌립니다 (긴 것부터).")
            # 슬롯을 넘는 need 를 슬롯 크기로 제한 → 재배치가 성공하도록
            capped = {}
            for m, sz in need.items():
                slot = files[m][1] - files[m][0]
                capped[m] = min(sz, slot)
            offs, sizes, total, spare = PR.plan(prot, capped)
            if total > PR.PROT_SIZE:
                print("    ★ 슬롯 제한 후에도 부족 — 원본 배치로 진행")
                prot_for_build = prot_in
            else:
                prot_new, _, _, spare = PR.rebuild(prot, capped)
                PR.verify(prot, prot_new)
                print(f"    ✓ 재배치 완료 (여유 {spare:,}B, 파일 1235개 내용 보존)")
                tmp = os.path.join(HERE, "..", "build", "PROT_REALLOC.DAT")
                open(tmp, "wb").write(prot_new)
                prot_for_build = tmp
        else:
            prot_new, _, _, spare = PR.rebuild(prot, need)
            PR.verify(prot, prot_new)          # 모든 파일 내용 보존 검증
            print(f"    ✓ 재배치 완료 (여유 {spare:,}B, 파일 1235개 내용 보존)")
            tmp = os.path.join(HERE, "..", "build", "PROT_REALLOC.DAT")
            open(tmp, "wb").write(prot_new)
            prot_for_build = tmp
    else:
        print("[2] 재배치 불필요 (슬롯 여유 충분)")
        prot_for_build = prot_in

    # ── 3패스: 번역 삽입 (기존 파이프라인) ───────────────
    print("[3] 번역 삽입...")
    out_prot = os.path.join(outdir, "PROT_KR.DAT")
    out_exe = os.path.join(outdir, "SCUS_KR.exe")
    T.build_patch(prot_in=prot_for_build, exe_in=exe_in,
                  out_prot=out_prot, out_exe=out_exe, dump=dump)

    # ── 보호구역 최종 검증 (원본 기준) ────────────────────
    newp = open(out_prot, "rb").read()
    protected.assert_protected(newp, prot)
    print("[4] 🔴 보호구역 검증 통과 (원본 기준)")

    # ── 폰트 용량 부족 기록 ───────────────────────────────
    #   400칸을 넘는 음절은 폰트에 못 들어가고, 그 대사는 영어로 남는다.
    #   어떤 음절이 왜 빠졌는지 남겨두면 나중에(용량 확장 시) 바로 복구할 수 있다.
    import missing_report
    rep = missing_report.analyze(dump)
    if rep:
        jp, mp = missing_report.write_report(rep)
        if rep["over"]:
            print(f"[5] ⚠ 폰트 {rep['over']}자 초과 → "
                  f"{len(rep['blocked_runs'])}런이 영어로 남습니다")
            print(f"    기록: {os.path.relpath(mp, os.path.join(HERE, '..'))}")
        else:
            print("[5] ✅ 모든 음절이 폰트에 들어갔습니다")

    print()
    print(f"완료:\n  {out_prot}\n  {out_exe}")


if __name__ == "__main__":
    main()
