#!/usr/bin/env python3
"""맵별 분할 저장소.

기존엔 text_dump.json(3MB) 하나에 22,360런을 통째로 저장했다.
파일이 커서 GUI 저장이 실패하고 번역이 날아갔다.

이 모듈은 맵별로 나눠 저장한다:
    build/maps/map6.json, map15.json, ... map813.json

각 파일은 그 맵의 런만 담아 작고 안전하다. 번역(ko)만 바뀌므로
저장도 빠르다.
"""
import os
import json

HERE = os.path.dirname(os.path.abspath(__file__))
MAPS_DIR = os.path.join(HERE, "..", "build", "maps")


def split_dump(dump_path, maps_dir=MAPS_DIR):
    """text_dump.json -> build/maps/map*.json 로 분할."""
    os.makedirs(maps_dir, exist_ok=True)
    d = json.load(open(dump_path, encoding="utf-8"))
    by_map = {}
    for e in d:
        by_map.setdefault(e["map"], []).append(e)
    for m, runs in by_map.items():
        runs.sort(key=lambda e: (e["script"], e["run"]))
        p = os.path.join(maps_dir, f"map{m}.json")
        json.dump(runs, open(p, "w", encoding="utf-8"),
                  ensure_ascii=False, indent=1)
    return sorted(by_map)


def list_maps(maps_dir=MAPS_DIR):
    """저장된 맵 번호 목록."""
    if not os.path.isdir(maps_dir):
        return []
    out = []
    for f in os.listdir(maps_dir):
        if f.startswith("map") and f.endswith(".json"):
            try:
                out.append(int(f[3:-5]))
            except ValueError:
                pass
    return sorted(out)


def load_map(m, maps_dir=MAPS_DIR):
    """한 맵의 런 목록을 로드."""
    p = os.path.join(maps_dir, f"map{m}.json")
    if not os.path.exists(p):
        return []
    return json.load(open(p, encoding="utf-8"))


def save_map(m, runs, maps_dir=MAPS_DIR):
    """한 맵만 저장 (작고 빠르고 안전)."""
    os.makedirs(maps_dir, exist_ok=True)
    p = os.path.join(maps_dir, f"map{m}.json")
    # 임시 파일에 쓰고 교체 (저장 중 중단돼도 원본 보존)
    tmp = p + ".tmp"
    json.dump(runs, open(tmp, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    os.replace(tmp, p)
    return p


def load_all(maps_dir=MAPS_DIR):
    """모든 맵을 합쳐 하나의 리스트로 (빌드/분석용)."""
    out = []
    for m in list_maps(maps_dir):
        out.extend(load_map(m, maps_dir))
    return out


def merge_to_dump(dump_path, maps_dir=MAPS_DIR):
    """맵별 파일을 합쳐 text_dump.json 재생성 (빌드 파이프라인 호환)."""
    allruns = load_all(maps_dir)
    json.dump(allruns, open(dump_path, "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    return len(allruns)


def stats(maps_dir=MAPS_DIR):
    """맵별 (전체, 번역) 카운트."""
    out = {}
    for m in list_maps(maps_dir):
        runs = load_map(m, maps_dir)
        done = sum(1 for e in runs if e.get("ko"))
        out[m] = (done, len(runs))
    return out


if __name__ == "__main__":
    import sys
    dump = sys.argv[1] if len(sys.argv) > 1 else \
        os.path.join(HERE, "..", "build", "text_dump.json")
    maps = split_dump(dump)
    print(f"{len(maps)}개 맵으로 분할: {MAPS_DIR}")
    s = stats()
    tot = sum(n for _, n in s.values())
    done = sum(d for d, _ in s.values())
    print(f"총 {tot:,}런 / 번역 {done}런")
