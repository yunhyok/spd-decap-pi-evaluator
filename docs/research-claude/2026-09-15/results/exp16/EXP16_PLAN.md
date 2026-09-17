# EXP-16 계획 — R 결손(port19형)의 상관 진단, 92포트 결과 이용 (보유 자료만)

작성 2026-09-16, EXP-15 결과를 보기 **전에** 등록. 인계 §6-3. PowerDC 요소별 전류·bare-rail DC R 없이 가능한 범위의 진단이며, 원인을 확정하지 않고 후보를 좁히는 것이 목적이다.

## 1. 입력
EXP-15의 92포트 영수증(기준선 exp13/j). 포트별 dRe_100k(= Re Z_model − Re Z_ref, 100 kHz, mΩ), 100 kHz breakdown(평면/via/trace R), unknowns, decap 수(extract), 기준면 종류(영수증 `two_sided_layers`·`reference_search`: 기준 후보가 DGND인지 타 전원넷인지), SITE0/SITE1 쌍.

## 2. 가설(동결)
- H_a(급전 경로형): R 결손의 크기 |dRe_100k|는 모델의 via+trace R 비중(via_R+trace_R)/(총 R)과 양의 상관이 있다. 즉 decap이 적고 급전 경로가 긴 레일에서 결손이 크다(P19: via 3.3 + trace 1.2 mΩ).
- H_b(비GND 기준면형): 기준 후보층에 타 전원넷(OTHER_POWER 등)이 포함된 포트의 결손이 GND만 기준인 포트보다 크다. cavity-wall 규칙이 타넷 평면을 이상 기준으로 두지만 실제 귀환은 그 넷의 decap·via를 거쳐야 하므로 R이 빠진다는 논리다.
- H_c(레일 수준형): 같은 레일의 SITE0/SITE1 쌍은 dRe_100k 부호와 크기(±30 %)가 같다. 성립하면 결손은 사이트 국소(포트 정의·핀필드)가 아니라 레일 공통(급전망·모델 규칙)이다.
- H_d(균일 계수형, EXP-9 §6-1의 "참조 R ≈ 모델 R의 1.8배"): R 결손형 포트에서 Re Z_ref/Re Z_model(100 kHz)의 분포가 좁다(사분위 범위 < 0.5). 성립하면 단일 규칙(예: via R 관례)이 원인일 가능성이 크고, 넓으면 포트별 다른 원인이다.

## 3. 방법과 판정 기준(동결)
- 대상: EXP-15에서 성공한 포트 전부. "R 결손형" = dRe_100k < −0.05 mΩ.
- H_a: Spearman ρ(|dRe_100k|, via+trace R 비중) over 전 포트. 채택 ρ ≥ 0.5(p < 0.01), 기각 ρ < 0.2, 그 외 불확정.
- H_b: 두 그룹의 dRe_100k 중앙값 차이. Mann–Whitney p < 0.01이고 타넷 그룹 중앙값이 더 음이면 채택; p ≥ 0.05면 기각.
- H_c: 46쌍 중 부호·크기(±30 %) 일치 비율 ≥ 80 % 채택, < 50 % 기각.
- H_d: R 결손형 포트의 Re Z_ref/Re Z_model 사분위 범위(IQR) < 0.5이면 채택, ≥ 1.0이면 기각.
- 결과에 따라 모델 파라미터를 바꾸지 않는다. 각 가설의 채택/기각을 보고하고, 채택된 가설이 가리키는 소유자 확인 실험(PowerDC 요소별 전류 등)의 대상 포트를 좁혀 제안한다.

## 4. 산출물
`exp16_corr.json`, `EXP16_REPORT.md`, 산점도 `exp16_scatter.png`. 코드 `tools/research-claude/exp16/corr16.py`. 저장소 사본 `results/exp16/`.
