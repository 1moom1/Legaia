# legaia-kr

레가이아 전설 / Legend of Legaia (PS1) **한글 패치** 제작 프로젝트.

미국판(Legend of Legaia USA)을 베이스로, 중국어 팬패치를 레퍼런스 삼아
텍스트를 한글로 교체하고 폰트에 한글 글리프를 삽입하는 것이 목표.

> 진행 중인 리버스 엔지니어링 결과는 **[docs/FINDINGS.md](docs/FINDINGS.md)** 참조.
> 이 문서 하나로 작업을 이어갈 수 있게 정리되어 있음.

## ⚠️ ROM/디스크 이미지는 포함하지 않음

저작권 문제로 `.bin` / `.cue` / VRAM 덤프는 저장소에 넣지 않는다
(`.gitignore` 참조). 아래 파일을 `roms/` 에 직접 두고 작업:

```
roms/
  Legend_of_Legaia_USA.bin      # 베이스
  Legend_of_Legaia_CN.bin       # 레퍼런스(중국어 패치)
  vram.bin                      # PCSX-Redux VRAM 덤프 (선택)
```

## 도구 (`tools/`)

| 파일 | 역할 |
|------|------|
| `disc.py` | Mode2/2352 BIN + ISO9660 리더 |
| `prot.py` | PROT.DAT 아카이브 인덱스 파서 |
| `legaia.py` | PROT→PACK→LZSS→Script 전체 파이프라인 |
| `lzss.py` / `lzss_fast.c` | LZSS 압축/해제 (파이썬/C) |
| `diffsec.c` | 두 디스크 이미지 섹터 단위 비교 |
| `hangul.py` | 한글 글리프 12×12 3계조 렌더러 |
| `glyphsheet.py` / `bitmap4.py` | 폰트 시트 시각화 |
| `build_poc2.py` | 대사 교체 + 재빌드 파이프라인 |
| `write_bin.py` | PROT→BIN 기록 (EDC/ECC 재계산) |

## 빌드 준비

```bash
pip install pillow numpy capstone fonttools --break-system-packages
gcc -O2 -o tools/lzss_fast tools/lzss_fast.c
gcc -O2 -o tools/diffsec  tools/diffsec.c
```

## 현재 상태

- [x] 디스크/아카이브/압축/스크립트 구조 완전 해독
- [x] 폰트 위치·포맷·듀얼레이어·팔레트 시스템 파악
- [x] US↔CN 텍스트 대응 추출, 빌드 파이프라인 검증
- [ ] 대사창(16×16) 렌더러 PC 확정 ← **다음 작업**
- [ ] 문자코드→셀 매핑 확정 / ASM 훅
- [ ] 한글 인코딩 방식 결정
