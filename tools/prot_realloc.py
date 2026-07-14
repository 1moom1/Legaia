#!/usr/bin/env python3
"""PROT.DAT 슬롯 재배치.

문제:
  맵 PACK 슬롯이 원본에서 이미 꽉 차 있다 (여유 합계 3KB).
  한글은 영어만큼 LZSS 압축이 안 되므로 PACK 이 커져 슬롯을 넘는다.
  -> 지금 빌드는 넘치면 번역을 되돌린다 (807런 손실).

해결:
  PROT 전체 여유는 513KB 다 (맵 아닌 파일들에 흩어져 있음).
  파일을 섹터 정렬로 다시 배치해서, 맵 파일에 필요한 만큼 슬롯을 준다.

PROT 구조:
    u32[0] = 0 (미사용)
    u32[1] = 파일 수 (1235)
    u32[2..] = 각 파일의 섹터 오프셋 (offset << 11)
  파일 데이터는 섹터(2048B) 정렬로 이어 붙는다.
  -> 오프셋 테이블만 다시 쓰면 재배치가 된다.
  원본은 첫 파일이 오프셋 6144(3섹터)부터 시작한다 (헤더가 3섹터).

제약:
  PROT.DAT 전체 크기는 디스크상 고정 (121,253,888B).
  재배치 후 총합이 이를 넘으면 안 된다.
"""
import os
import sys
import struct

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from legaia import prot_files          # noqa: E402

SEC = 2048
PROT_SIZE = 121253888                  # 디스크상 고정 크기


def measure(prot_bytes):
    """각 파일의 (슬롯, 실사용) 크기."""
    out = []
    for fid, s, e in prot_files(prot_bytes):
        slot = e - s
        used = len(prot_bytes[s:e].rstrip(b"\x00"))
        out.append((fid, s, e, slot, used))
    return out


def plan(prot_bytes, need):
    """재배치 계획.

    need: {file_id: 필요한 최소 슬롯 크기}
    반환: (새 오프셋 리스트, 크기 리스트, 총 크기, 여유)
    """
    info = measure(prot_bytes)
    n = len(info)
    sizes = []
    for fid, s, e, slot, used in info:
        want = max(need.get(fid, 0), used)
        # ★ 완전히 빈 파일(used==0)은 0섹터로 준다.
        #   오프셋을 다음 파일과 같게 두면 크기 0 으로 파싱된다 (파서는 정상 동작).
        #   원본은 빈 파일에도 1섹터씩 줬다 — 4개 x 2048B = 8,192B 낭비.
        #   이 8KB 가 슬롯 부족분과 정확히 맞아떨어진다.
        sizes.append(((want + SEC - 1) // SEC) * SEC)
    # 헤더: u32[0] + u32 파일수 + u32 * n  (섹터 정렬)
    hdr = 8 + 4 * n
    hdr = ((hdr + SEC - 1) // SEC) * SEC
    total = hdr + sum(sizes)
    offsets = []
    cur = hdr
    for sz in sizes:
        offsets.append(cur)
        cur += sz
    return offsets, sizes, total, PROT_SIZE - total


def rebuild(prot_bytes, need):
    """재배치된 PROT 을 만든다.

    🔴 파일0(공통 데이터: 폰트/TIM/팔레트)의 '시작 오프셋은 유지한다'.
       protected.py 의 보호구역 오프셋이 절대값이라, 파일0 이 움직이면
       검사가 엉뚱한 자리를 본다. 파일0 은 원래 자리에 그대로 둔다.
    """
    info = measure(prot_bytes)
    offsets, sizes, total, spare = plan(prot_bytes, need)
    # 파일0 은 원래 위치를 지킨다 (보호구역 오프셋이 절대값이므로)
    if info and offsets and offsets[0] != info[0][1]:
        raise RuntimeError(
            f"파일0 이 움직였다 (0x{info[0][1]:X} -> 0x{offsets[0]:X}). "
            "보호구역 오프셋이 절대값이라 파일0 은 제자리여야 한다.")
    if total > PROT_SIZE:
        raise RuntimeError(
            f"재배치 실패: {total:,}B > {PROT_SIZE:,}B ({total-PROT_SIZE:,}B 초과)")
    out = bytearray(PROT_SIZE)
    n = len(info)
    struct.pack_into("<I", out, 0, 0)          # u32[0] = 0
    struct.pack_into("<I", out, 4, n)          # u32[1] = 파일 수
    for i, off in enumerate(offsets):
        if off % SEC:
            raise RuntimeError("오프셋이 섹터 정렬이 아님")
        struct.pack_into("<I", out, 8 + 4 * i, off >> 11)
    for i, (fid, s, e, slot, used) in enumerate(info):
        data = prot_bytes[s:s + used]
        o = offsets[i]
        out[o:o + len(data)] = data
    return bytes(out), offsets, sizes, spare


def verify(orig, new):
    """재배치 후 모든 파일 내용이 그대로인지 검증."""
    a = list(prot_files(orig))
    b = list(prot_files(new))
    if len(a) != len(b):
        raise RuntimeError(f"파일 수 불일치: {len(a)} vs {len(b)}")
    for (fa, sa, ea), (fb, sb, eb) in zip(a, b):
        if fa != fb:
            raise RuntimeError(f"파일 id 불일치: {fa} vs {fb}")
        da = orig[sa:ea].rstrip(b"\x00")
        db = new[sb:eb].rstrip(b"\x00")
        if da != db:
            raise RuntimeError(f"파일 {fa} 내용이 달라짐 ({len(da)} vs {len(db)}B)")
    return True


if __name__ == "__main__":
    p = open(os.path.join(HERE, "..", "cn", "PROT_US.DAT"), "rb").read()
    info = measure(p)
    tot_slot = sum(i[3] for i in info)
    tot_used = sum(i[4] for i in info)
    print(f"파일 {len(info)}개")
    print(f"  슬롯 합계 {tot_slot:,}B")
    print(f"  실사용   {tot_used:,}B")
    print(f"  여유     {tot_slot-tot_used:,}B")
    print()
    # 재배치만 해도 얼마나 여유가 생기나 (need 없이)
    offsets, sizes, total, spare = plan(p, {})
    print("빈틈 없이 재배치하면:")
    print(f"  총 {total:,}B / {PROT_SIZE:,}B")
    print(f"  여유 {spare:,}B  ← 맵 슬롯 확장에 쓸 수 있다")
