# EXP-28 계획 — 균질화 경계조건 결함 수정 (homog_face_fix, 변형 p)

작성 2026-09-17, 실행 전 사전 등록. EXP-27에서 `homog.batched_gx`가 창 양 끝 열(셀 중심선)에 금속이 없으면 G = 0을 내어 폴리곤 가장자리 셀이 고립되는 결함을 찾았다. 산술 결함 수정이므로 EXP-13(j)과 같은 채택 규칙을 쓴다. 자유 계수 없음.

## 1. 규칙(고정)
- 플래그 `homog_face_fix`(Model3 kwarg → `homog.cell_edges(..., face_fix=True)` → `batched_gx`). 각 창에서 금속이 있는 첫 열 k0와 마지막 열 k1을 찾아, 주입면을 열 0 대신 k0, 인출면을 열 nx−1 대신 k1로 둔다(같은 2·W 결합). 전류 I는 k0 열에서 계산하고 G_rel = I·nx/ny(창 길이 정규화는 그대로 — 셀 노드가 셀의 금속 위에 있다고 보는 것과 같다). k0 = 0, k1 = nx−1인 창(면에 금속이 있는 창)은 기존과 비트 동일. 금속이 있어도 k0–k1 사이에 경로가 없으면 I = 0 → G = 0(기존과 같음).
- 변형 p = `c_unit_fix=True` + `homog_face_fix=True`. 기준선 exp13/j.

## 2. 사전 예측
- 합성 검사: 폭 ny·길이 nx 창에서 금속이 열 3..nx−4에만 있는 직사각형 스트립은 기존 G = 0, 수정 후 G = 1(전폭)이어야 한다.
- P65: 고립 decap 0개, 100 kHz Re Z가 29.6 mΩ에서 P19 수준(13–15 mΩ)으로 내려감. 92포트 고립 decap 56 → ≤ 5.
- 7케이스: 가장자리 셀이 연결되면서 평면 R·L이 소폭 변한다(|ΔZ|/|Z| ≤ 1e-2 예상). P18은 대형 평면 레일이라 게이트 불변을 예측하고, P16·P19(R 결손형)는 개선 방향(고립 decap이나 끊긴 패치 복구)을 예측한다.
- 92포트: G3 PASS 30 → 40 이상, R 결손형 포트의 |dRe|/Re Z_ref 중앙값 0.29(c_iso > 0 그룹) 감소.

## 3. 방법
1. 구현 후 `--variant none --smoke` ≤ 1e-9, `--variant j --smoke --smoke-baseline exp13:j` ≤ 1e-9, `smoke_port18.py` PASS, 합성 검사 PASS.
2. `audit27.py --one Port65_SITE1 --variant p`(플래그 전달)로 고립 0 확인. 7케이스 `run11.py --variant p` → `WORK_DIR/exp28/`, `table11.py --exp exp28 --baseline exp13:j`.
3. 92포트 `runall15.py --variant p --outdir exp28`, `summary15.py --dir exp28 --variant p`, 연결성 감사 `audit27.py --jobs 7 --variant p`(44포트만이어도 됨).

## 4. 판정 기준(동결)
- 산술 결함 수정 규칙(EXP-13과 동일): 7케이스에서 PASS→FAIL이 없으면 **새 기준선 후보로 제안**(소유자 승인 후 exp28/p를 기준선으로). PASS→FAIL이 있으면 케이스와 원인을 기록하고 소유자 판단에 맡긴다(결함 수정이므로 "개선 없음"이 채택 거부 사유는 아니다).
- 92포트 결과는 보고(G1–G4 PASS 수, err_1MHz 분포, 고립 decap 수, c_iso > 0 그룹의 |dRe|/Re Z_ref).
- G1–G5 정의 불변.

## 5. 산출물
`result_*_any_p.json`(7 + 92), `compare_p.json`, `summary15_exp28.json`, `exp27_p.json`(감사), `EXP28_REPORT.md`, 저장소 사본 `results/exp28/`.
