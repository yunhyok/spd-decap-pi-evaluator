# EXP-24 계획 — 사이트 비대칭 진단: 쌍둥이 넷(SITE0 /0 vs SITE1 /1) 46쌍에서 모델 대칭성과 참조 대칭성 (92포트 자료만)

작성 2026-09-17, 실행 전 사전 등록. 계기: Port19_SITE0(VAA_DDRH/0)과 Port65_SITE1(VAA_DDRH/1)은 추출 통계(레일 노드 963, via 705, decap 4, 포트 노드 12)가 동일한 쌍둥이 넷인데 dRe_100k가 −6.0 vs +10.1 mΩ로 부호가 반대다(EXP-15/16). 원인이 모델(추출·규칙)의 사이트 비대칭인지 참조(PowerSI)의 사이트 비대칭인지 자료만으로 가른다. 파라미터 변경 없음.

## 1. 정의(동결)
- 쌍 k(1–46): Port k_SITE0 ↔ Port (k+46)_SITE1. 추출 통계: 레일 노드 수, via 수, trace 수, decap 수, 포트 양극 노드 수, 레일 평면 층 목록(exp5 extract pkl).
- 100 kHz에서 R_mod(site), R_ref(site) = Re Z. 사이트 비 s_mod = R_mod(SITE1)/R_mod(SITE0), s_ref = R_ref(SITE1)/R_ref(SITE0). 1 MHz |Z|에 대해서도 같은 비를 낸다.
- "추출 동형" 쌍 = 레일 노드·via·decap·포트 노드 수가 모두 같고 층 목록이 같은 쌍.

## 2. 가설(동결)
- H_sym_mod: 추출 동형 쌍에서 |s_mod − 1| ≤ 0.1인 비율 ≥ 0.8 → 채택(모델은 사이트 대칭).
- H_asym_ref: 추출 동형 쌍에서 |s_ref − 1| > 0.2인 비율 ≥ 0.3 → 채택(참조가 사이트 비대칭). 비율 < 0.1이면 기각.
- H_asym_src: H_asym_ref 채택 시, |s_ref − 1|이 큰 쌍이 decap 수가 적은(≤ 10) 소형 레일에 몰려 있으면(상위 10쌍 중 ≥ 7) "급전 경로 지배 레일의 참조 비대칭"으로 분류.
- 예측: H_sym_mod 채택, H_asym_ref 채택. 즉 SITE 쌍의 오차 부호 반전은 모델이 아니라 참조의 비대칭(PowerSI에서 /0·/1 넷의 취급 차이 — 포트 정의, 넷 연결, 또는 실제 레이아웃 비대칭이 추출에 안 잡힌 것)에서 온다.

## 3. 판정 후 해석(미리 적음)
- 두 가설 채택이면 소유자 확인 요청을 "P19/P65 PowerDC"에서 **"VAA_DDRH/0과 /1의 PowerSI 설정·포트 정의·DC 저항 비교"**로 바꾼다. 추출 동형인데 참조가 다르면 SPD 안에 추출이 놓친 차이(예: 사이트별 다른 decap 모델 할당, 넷 병합, 플레인 형상의 미세 차이)가 있는지도 확인 대상이다.
- H_sym_mod 기각이면 추출·규칙의 사이트 의존성(포트 노드 스냅, 기준면 검색 창)을 먼저 조사한다.

## 4. 산출물
`exp24.json`, `EXP24_REPORT.md`, 그림 `exp24_site_ratio.png`(s_mod vs s_ref 산점, 46쌍). 코드 `tools/research-claude/exp24/an24.py`. 저장소 사본 `results/exp24/`.
