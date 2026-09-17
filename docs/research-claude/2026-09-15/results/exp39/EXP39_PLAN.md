# EXP-39 계획 — build 가속 2단계: 참조 탐색 래스터화의 레이어 단위 캐시 (물리 변경 없음)

작성 2026-09-17, 실행 전 사전 등록. EXP-38 후 build의 96 %가 `exp1/model.rasterize`(블록 창마다 (layer, net) 아트워크를 셀 중심에서 평가, `matplotlib.Path.contains_points`)다. P18: 427회 rasterize / 16,093회 `points_in_path` / 424 s. 같은 (layer, net, h)가 블록·후보 레이어마다 다시 래스터화되는 것이 원인이다(`exp1b.TwoSided.mask`·`run8.TwoSidedAny.mask`의 캐시 키가 블록 단위).

## 1. 규칙(고정)
- 물리·수치 정의 불변. 기본 경로(블록마다 `rasterize(window=...)`) 바이트 단위 불변.
- 가속은 `SPD_PI_FAST=1`(정확 변환 플래그)에 붙인다: (layer, net, h)마다 **레이어 bbox 전체를 1회 래스터화**하고(원점 `floor(bbox/h)*h`, 기존 `rasterize(window=None)`과 같은 규약), 블록 마스크는 그 배열의 슬라이스로 뽑는다. 블록 원점이 h의 정수배 격자에 있지 않으면(assert) 그 블록은 기본 경로로 푼다.
- 셀 중심 좌표는 두 경로가 다른 부동소수 식(`x0_block + (i+0.5)h` 대 `x0_layer + (k+0.5)h`)으로 계산되므로 다각형 변 위 ±1 ulp의 셀이 뒤집힐 수 있다. 이를 **실측**한다(마스크 차이 셀 수).

## 2. 판정 기준(동결)
- (a) 정확도: 7케이스 + Port49의 모든 블록에서 FAST 마스크와 기본 마스크의 **차이 셀 수 = 0**이면 정확 변환으로 채택. 0이 아니면 케이스별 차이 셀 수와 Z 영향(기준선 exp28/p 대비 max |ΔZ|/|Z|)을 보고하고, ΔZ ≤ 1e-8이면 채택하되 "ulp 수준 마스크 차이 있음"을 명시, 초과면 기록만.
- (b) 속도: 같은 세션 단독 실행 대조로 P18 build ≤ 150 s(EXP-38 441 s), Port49 build ≤ 10 분(22.8 분)이면 성립. 4-병렬 7케이스 벽시계도 보고.
- (c) 기본 경로 불변: `smoke_port18.py` 5.83e-12 PASS, `run11.py --variant p --smoke --smoke-baseline exp28:p` 0.0.

## 3. 산출물
`WORK_DIR/exp39/`: 마스크 비교 로그(블록 수, 차이 셀 수), 프로파일, 7케이스+Port49 영수증, `EXP39_REPORT.md`; 저장소 사본 `results/exp39/`.
