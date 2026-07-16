# 일본판 참고 자료

일본판 전환(§8-U) 작업의 시작점.

## 파일
- `SCPS_JP.exe` — 일본판 실행 파일 (LBA 24, 450,560B)

## PROT.DAT 추출 방법
일본판 PROT.DAT 는 121MB라 저장소에 넣지 않는다. 디스크에서 추출:

```python
# 일본판 디스크: Legaia Densetsu (Japan).bin (Mode2/2352)
# ISO9660 디렉토리 확인: PROT.DAT = LBA 246, size 121,815,040
RAW, UOFF, USER = 2352, 24, 2048
def read_sectors(disc, lba, n):
    out = bytearray()
    with open(disc, "rb") as f:
        for i in range(n):
            f.seek((lba+i)*RAW + UOFF)
            out += f.read(USER)
    return bytes(out)

# 앞 20MB (맵 스크립트) 만:
prot_head = read_sectors("Legaia Densetsu (Japan).bin", 246, 10000)
```

## 확인된 구조
- 미국판과 거의 동일: 파일 1235개, 첫 파일 id=0 off=6144
- 같은 `prot_files` / `pack_parse` 파서로 읽힘
- 스크립트(file3) 크기: 맵당 미국판의 약 1.03배
- 텍스트: 2바이트 문자 (SJIS 계열). 제어코드 체계 재분석 필요.
