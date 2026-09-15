# 데이터 특성 분석 보고서 (Cadence Sigrity SPD / Touchstone)

대상: 사용자 Windows PC `~/mnt/examples/` 폴더의 6개 파일 (SPD 3개 + Touchstone 3개). 모든 파일은 읽기 전용으로만 조회했으며 수정하지 않음. 분석 스크립트는 `~/mnt/examples/analysis/scripts/`에 저장.

## 1. SPD 파일 공통 구조

세 SPD 모두 `Title WorkflowKey = 0x100000067 - LayoutWorkbench file for version 2000.20`, Cadence "Layout Workbench 25.1.0.09191" 형식. 섹션 키워드(줄 시작 `.`)는 PCB/패키지 형상(`.Shape/.EndShape`), 패드/비아 스택(`.PadDef`, `.PadStackDef`), 부품(`.Component`/`.Connect`/`.EndC`, 1:1:1 개수 동일), 회로 모델(`.PartialCkt`/`.EndPartialCkt`, 내부에 SPICE 소자 라인 또는 `.SUBCKT/.ENDS`), 재질(`.Material`/`.DielectricModel`/`.MetalModel`/`.ThermalModel`), 주파수 설정(`.FrequencySetup`/`.FrequencySweep`), 포트(`.Port`/`.EndPort`), 넷리스트(`.NetList`/`.EndNetList`)로 구성. **`.Via`, `.Node` 같은 전용 섹션 키워드는 존재하지 않음** — 비아/노드는 `.Package` 블록 내부의 `Circle...::net±` 도형(비아 패드)과 포트 정의부의 `$Package.NodeNNNNN!!shapeid::net` 인라인 참조로만 표현됨. 넷 이름은 도형 토큰의 `::` 뒤, `+/-` 앞 부분에서 추출.

## 2. 파일별 세부 특성

### S4LB002-2Para_260729_1_injected.spd (1.1GB)
- 스택업: 48개 도전층(`Signal$TOP` ~ `Signal$L47` ~ `Signal$BOTTOM`), 층 두께 20~35µm(Cu), 유전체 47개(ABF-GL102 빌드업 30µm 대부분 + 중간 EL190T 코어 70~105µm 일부). 재질 물성: ABF-GL102/EL190T/BU-DIELECTRIC 등에 대해 주파수별 εr·tanδ 테이블 보유, Cu 도전율 5.959e7 S/m.
- 부품: `.Component` 11,173개 = 디캡 11,162개(`CAP_0603_1UF` 6,490 / `CAP_0402_100NF` 4,410 / `CAP_1608_10UF` 254 / `CAP_0603_68NF` 8[모델 본문 비어있음]) + `DUT`×2 + `LGA`×1 + `ALIGN500`×4 + `ALIGN1998`×3 + `1998ARROW`×1.
- 디캡 모델: Murata 제공 SPICE 래더(C-L-R 직렬-병렬 다단, 최대 13단) 2024년산 데이터.
- 넷: 4,900개(고유). 포트: 92개(`Port1_SITE0`~`Port92_SITE1`, SITE0/SITE1 각 46개 레일 — 동일 DUT 2사이트 패널). 비아/패드 원형 도형(`Circle`) 57,279개(포트 노드가 묶이는 개별 비아 근사치).
- 주파수 설정: 메인 추출 `.FrequencySweep 0MHz ~ 2000MHz, Adaptive`(S-파라미터), 별도 최적화/EMI용 Log/Linear 스윕 다수. `ReferenceImpedance = 50Ω`(포트 정규화 임피던스), `ReferenceImpedance2 = 1Ω`.

### S4LB002-2Para_260804_1_injected.spd (1.1GB) — 동일 설계의 재작업본
- 스택업 층수·명칭은 260729와 동일(48층, `Signal$TOP`~`BOTTOM`)이나 **유전체 구성이 다름**: 전 층 유전체가 `EL190T`로 통일되고(260729는 ABF-GL102 위주), 상부 층 유전체 두께가 15µm로 260729(30µm)보다 얇음. 재질 물성 테이블(εr·tanδ)은 동일 라이브러리.
- 부품: `.Component` 10,880개 = 디캡 10,869개(`CAP_0603_1UF` 6,339 / `CAP_0402_100NF` 4,268 / `CAP_1608_10UF` 254 / `CAP_0603_68NF` 8) + 동일 정렬/DUT/LGA 부품. **260729 대비 디캡 293개 감소**(1UF -151개, 100NF -142개). `.NetAlias/.EndNetAlias` 섹션이 추가로 존재(260729엔 없음).
- 넷 4,900개, 포트 92개(포트명·순서 동일). 주파수/기준임피던스 설정 동일(0~2GHz Adaptive, 50Ω).

### s5m6585_32p_260414_length3_1.spd (139MB) — 별도 소형 보드
- 스택업: **2개 도전층만**(TOP, BOTTOM), 재질 `COPPER_1`, εr=3.83 — 얇은 flex/rigid 평가 보드로 추정.
- 부품: `.Component` 3,137개 = `CAP_1005_0603-100N_100N`(100nF, 16단 SPICE 서브서킷) 1,920 / `CAP_2012-NULL_NULL`(모델 본문 없음, 비실장 자리) 512 / `CAP_1005_0603-100P_100P`(100pF, 9단 SPICE) 512 / `CAP_1005_0603-1U_1U`(이상적 C 1µF, 기생 無) 96 / `CAP_1005_0603-220N_220N`(이상적 C 220nF) 32 / `NEC_UD2_5NU`(8단자, 모델 본문 없음) 32 / `FLEX_POGO200`(10×10단자 포고핀 프로브, 모델 본문 없음=이상 단락) 32 / `S5M6585_32P_LGA_10875`(PMIC 본체, 형상기반 파셜서킷) 1.
- 넷 3,555개. 포트 160개, 전부 `GenFromCktInstance="U1_0"` 단일 사이트(듀얼사이트 아님). 주파수 0~1GHz Adaptive, 기준임피던스 50Ω/1Ω.

## 3. Touchstone(.s92p, .s160p) 검증 결과

옵션 라인은 세 파일 모두 `# Hz S RI R 1` — 주파수 단위 Hz, S-파라미터, **RI(실수-허수) 포맷**, 기준임피던스 **1Ω**. `!` 주석 헤더는 포트명 목록 2벌(짧은 형식 `PortN_SITEx::netname/idx`, 별칭 형식 `Port[N] = 2nd_SITEx-netname/idx`)을 포함.

**행 포맷 검증**: N포트 Touchstone 1.x는 한 주파수당 N개 행, 각 행은 최대 4쌍(8개 실수)씩 줄바꿈. 92포트는 행당 23줄(마지막 줄까지 정확히 4쌍, 92=4×23), 블록당 92×23=2,116줄. 160포트는 행당 40줄, 블록당 160×40=6,400줄. 실측 데이터 줄 수가 이 공식과 정확히 나눠떨어짐을 확인(92p: 1,747,816줄=2,116×826, 160p: 4,006,400줄=6,400×626)하여 포맷 가정을 검증함. 2행 첫 값이 1행의 S12와 일치(상반성 S21=S12)하여 행-우선(row-major) 순서도 확인.

**Z 변환**: Z0=1Ω로 `Z = Z0·(I+S)·(I−S)⁻¹`을 각 주파수 블록마다 계산(92×92, 160×160 역행렬, scipy 없이 `numpy.linalg.inv`만 사용). 92p는 826개, 160p는 626개 주파수점. 전체 파싱+역행렬 계산 시간은 파일당 4~9초(전체 706MB 텍스트를 `str.split`/`numpy.fromstring`으로 직접 float 배열화, 최대 메모리 사용량 약 2GB, 3GB 한도 내).

### S4LB002_260729 — Port18(`ADC_VDD_075_VTRIP_SRAM/0`) Z[17,17]
| f | Z (Ω) | \|Z\| |
|---|---|---|
| 1 kHz | 7.406e-3 − j6.057e-1 | 0.6057 |
| 10 kHz | 1.345e-3 − j6.157e-2 | 0.06159 |
| 100 kHz | 7.240e-4 − j6.247e-3 | 6.289e-3 |
| **1 MHz** | **6.915e-4 − j4.258e-4** | 8.121e-4 |
| 10 MHz | 1.335e-3 + j8.761e-4 | 1.597e-3 |
| 100 MHz | 1.780e-3 + j1.051e-2 | 1.066e-2 |

1MHz 값(6.915e-4 − j4.258e-4)은 목표치(≈0.00069 − j0.00043)와 일치 → 포맷 가정(RI, R=1, 행-우선) 검증 완료. |Z| 최소값은 **7.347e-4Ω @ 1.585MHz**(공진 근처).

### S4LB002_260804 — 동일 포트
| f | Z (Ω) | \|Z\| |
|---|---|---|
| 1 kHz | 7.466e-3 − j6.057e-1 | 0.6057 |
| 10 kHz | 1.404e-3 − j6.157e-2 | 0.06159 |
| 100 kHz | 7.846e-4 − j6.235e-3 | 6.284e-3 |
| 1 MHz | 8.036e-4 − j3.960e-4 | 8.959e-4 |
| 10 MHz | 1.412e-3 + j9.448e-4 | 1.699e-3 |
| 100 MHz | 1.908e-3 + j1.055e-2 | 1.072e-2 |

|Z| 최소값 **8.488e-4Ω @ 1.445MHz**. 260729 대비 1MHz 부근 |Z|가 약간 높고(8.96e-4 vs 8.12e-4) 최소 임피던스 주파수가 낮은 방향(1.585→1.445MHz)으로 이동 — 디캡 개수 감소(293개)·스택업 유전체 변경과 정합.

### s5m6585 — Port1(`ADC_AVDD08_LO/0`), Port160(index159)
| f | Z[port1] | Z[port160] |
|---|---|---|
| 1 kHz | 1.770 − j277.4 | 1.624e4 − j6.001e4 |
| 10 kHz | 0.1709 − j27.97 | 226.2 − j6474 |
| 100 kHz | 0.02672 − j2.822 | 22.63 − j647.5 |
| 1 MHz | 9.431e-3 − j0.2783 | 2.266 − j64.74 |
| 10 MHz | 0.01212 + j0.04233 | 0.2314 − j6.420 |
| 100 MHz | 0.6305 + j1.224 | 0.04250 − j0.1103 |

두 포트의 임피던스 크기 차이(port160이 port1 대비 수백~수천 배)가 큼 — 서로 다른 레일/전류용량 특성으로 추정(추가 해석 없음, 원자료 그대로 기록).

## 4. 산출물

- `~/mnt/examples/analysis/S4LB002_260729_Zdiag.npz` (1.87MB): `freq`(826,), `Zdiag`(826×92 complex), `Zfull_sel`(6×92×92, 1k/10k/100k/1M/10M/100MHz 최근접점), `Zfull_sel_freq`, `Zfull_sel_idx`, `port_names`(92,)
- `~/mnt/examples/analysis/S4LB002_260804_Zdiag.npz` (1.87MB): 동일 구조
- `~/mnt/examples/analysis/s5m6585_Zdiag.npz` (3.81MB): `Zdiag`(626×160), `Zfull_sel`(6×160×160) 등
- `~/mnt/examples/analysis/scripts/process_s92p_260729.py`, `process_s92p_260804.py`, `process_s160p.py`: 파싱·Z변환·저장 스크립트(재실행 가능)
- 원본 SPD/Touchstone 파일은 전혀 수정하지 않음. 중간 산출물(헤더/키워드 grep 결과 등)은 `~/work/`에만 저장하고 device 재부팅/세션 종료 시 정리 대상.

## 5. 사용 명령어 요약

```bash
# 섹션 키워드 집계 (한 번의 grep로 전체 파일 스캔, ~4초/1.1GB)
grep -o '^\.[A-Za-z][A-Za-z0-9_]*' file.spd | sort | uniq -c | sort -rn

# 특정 섹션 라인 위치 찾기 → sed로 해당 구간만 추출
grep -n '^\.Package\|^\.Material\|^\.Port\b' file.spd
sed -n '<start>,<end>p' file.spd > block.txt

# 디캡 인스턴스→모델 매핑 집계 (하이픈 포함 모델명 주의: [^ ]* 사용)
grep -o '^\.Connect [^ ]* [^ ]*' file.spd | awk '{print $3}' | sort | uniq -c | sort -rn

# Touchstone Z 변환 (요지, 전체 스크립트는 analysis/scripts/ 참조)
python3 - <<'PY'
import numpy as np
with open(path) as fh:
    header = [fh.readline() for _ in range(HEADER_LINES)]
    data = fh.read()
arr = np.fromstring(data, dtype=np.float64, sep=' ')   # 92p: str.split 후 배열화도 동일 속도
per_block = 1 + N*N*2
arr = arr[:nfreq*per_block].reshape(nfreq, per_block)
freq = arr[:,0]
S = (arr[:,1::2] + 1j*arr[:,2::2]).reshape(nfreq, N, N)
I = np.eye(N)
Z = np.array([1.0*(I+S[i])@np.linalg.inv(I-S[i]) for i in range(nfreq)])
PY
```
