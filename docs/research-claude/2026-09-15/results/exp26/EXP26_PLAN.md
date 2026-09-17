# EXP-26 계획 — decap별 포트→decap 경로 R과 전류 배분 (모델 진단 풀이, 소형 레일 2쌍)

작성 2026-09-17, 실행 전 사전 등록. EXP-25에서 P65_SITE1 모델의 100 kHz "나머지" 항(decap ESR 가중합)이 20.3 mΩ로 P19_SITE0의 6.2 mΩ보다 3배 커, 모델 전류가 소수 decap에 몰린다고 추정했다. 이를 모델 안에서 직접 측정한다. 기준선 exp13/j 모델(c_unit_fix=True), 파라미터 변경 없음.

## 1. 정의(동결)
- 대상 포트: Port19_SITE0, Port65_SITE1, Port29_SITE0, Port75_SITE1(각 decap 4개).
- 모델을 build한 뒤 f = 100 kHz에서 (a) 정상 풀이(포트 1 A 주입, 기준면 0 V)로 각 decap 지로 전류 I_d와 decap 임피던스 Z_d를 얻어 전류 배분 비 |I_d|/Σ|I_d|, 나머지 항 Σ|I_d|²·Re Z_d를 계산하고 영수증 Re Z − 경로 R과 대조한다. (b) decap을 모두 제거한 뒤 포트에서 각 decap 레일 노드까지 1 A를 흘려(포트 주입, 해당 노드 인출) 경로 저항 R_path,d = Re V_port를 얻는다(4회 풀이).
- 사이트 편차 지표: spread = max_d R_path,d / min_d R_path,d. 배분 왜곡 지표: 나머지 항 / (평균 ESR / N_decap).

## 2. 가설(동결)
- H_split: P65의 나머지 항이 (a)에서 18 mΩ 이상으로 재현되고(영수증 20.3 mΩ와 ±20 %), 상위 1개 decap의 전류 비율 ≥ 0.6 → "전류 집중" 채택. P19는 상위 1개 ≤ 0.4.
- H_spread: (b)에서 P65의 spread ≥ 3이고 P19의 spread ≤ 2 → "SITE1 경로 R 편차 과대" 채택. P29/P75 쌍에서 같은 방향이면 일반성 확인.
- H_elem: 경로 R이 가장 큰 decap에 대해 (b) 풀이의 요소별 |I|²R 분해(평면/via/trace/패드 링크)에서 최대 요소가 via면 "via 스택", trace면 "trace", 패드 링크면 "패드 링크" 원인으로 분류.

## 3. 판정 후 해석(미리 적음)
- H_split·H_spread 채택이면 원인은 특정 decap 경로의 과대 R이며, H_elem이 가리키는 요소의 규칙(via 경로 선택, 기본폭 trace, 패드 링크 0.5/(σt))을 다음 사전 등록에서 플래그로 되돌려 시험한다.
- 기각이면 나머지 항의 차이는 배분이 아니라 decap 모델 자체(ESR 주파수 의존)에서 오는 것이므로 decap 모델 비교로 넘어간다.

## 4. 산출물
`exp26.json`, `EXP26_REPORT.md`, 코드 `tools/research-claude/exp26/paths26.py`. 비용: 포트당 build 1–2 min + 풀이 5회, 총 10 min 이내. 저장소 사본 `results/exp26/`.
