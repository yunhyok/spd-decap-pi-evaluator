# EXP-11 계획 — [구현 차이] 되돌리기 (a) εr/tanδ 주파수 테이블, (b) 비인접 기준면 C 복원

작성 2026-09-16, 실행 전 사전 등록(결과를 본 뒤 §4 기준을 바꾸지 않는다). 인계 §6-4 (a)(b), `MODEL_PHYSICS.md` §"유전체" [구현 차이] 2건(`1b:129`, `1b:131-134`).
선행 게이트 G0: smoke_port18 PASS(2026-09-16, rel 5.83e-12). 동결 모델 기본 동작은 바꾸지 않고 플래그로만 켠다(규칙 4).

## 1. 대상 구현 차이
- (a) 채택 경로는 εr을 stackup 행의 `dk`(ABF-GL102 3.3, EL190T 4.3)로, tanδ를 첫 유전체의 1 MHz 테이블값(0.0041, 0.010)으로 주파수 무관하게 고정한다. SPD DielectricModel 테이블은 ≤1 MHz에서 εr 3.4 / 4.7, 100 MHz에서 3.333 / 4.433, tanδ 0.0041→0.0040 / 0.010→0.0107이다. 교과서 형태 = 각 주파수에서 테이블 보간값(`model.eps_tand(row, f)`)을 직렬 합성에 쓴다.
- (b) C는 가장 가까운 기준 후보층(k == 0)이 덮는 셀에만 둔다. 더 먼 후보가 덮는 셀은 d_eff는 받지만 C는 0이다. 교과서 형태 = 그 셀에도 실제 간격의 유전체 직렬 합성 C를 둔다.

## 2. 가설(사전 예측)
- 레일 평면쌍 C는 nF 이하이고 decap 총 C는 수백 µF이므로, (a)(b) 모두 10 MHz 이하에서 |ΔZ|/|Z| < 1e-3, 100 MHz에서 < 1e-2로 예상한다. 게이트 PASS/FAIL은 7케이스 모두 바뀌지 않을 것으로 본다(H_null).
- (a)에서 εr이 3–9 % 커지므로 평면 C가 같은 비율로 커지고, 효과가 있다면 G5(10–100 MHz) 반공진 근처에서만 나타난다.

## 3. 방법
1. 플래그: `exp1b.TwoSided(eps_table=False, c_all_refs=False)` → `model3.Model3(..., eps_table, c_all_refs)` → `Model4`/`ModelB` 전달. 기본값 False에서 결과는 기존과 비트 단위 동일해야 한다(검증: `run11.py --variant none --smoke`가 exp8 영수증 Port18 1 MHz와 상대차 ≤ 1e-9).
2. 러너 `tools/research-claude/exp11/run11.py`: `exp8/run8.run()`과 같은 주파수 사다리·게이트·영수증 스키마, `--variant a|b|ab`, 출력 `WORK_DIR/exp11/result_{tag}_{port}_any_{variant}.json`(`variant`, `flags`, 사용된 εr/tanδ 요약 포함). 기존 exp8 파일은 건드리지 않는다.
3. 7케이스(260729 P1, P7, P14, P16, P18, P19; 260804 P18)를 변형 a, b 각각 실행. 비교표 `table11.py`: exp8 저장소 영수증(`docs/.../results/exp8/`) 대비 G1–G5 지표, PASS 변화, 사다리 점별 max |ΔZ|/|Z|(f ≤ 10 MHz, 100 MHz 별도).
4. ab 동시 적용은 a, b 결과를 본 뒤가 아니라 지금 정한다: a·b 각각이 H_null이면 ab는 실행하지 않는다(효과 없음 확인으로 충분). 둘 중 하나라도 H_null을 벗어나면 ab도 실행한다.

## 4. 판정 기준(동결)
- H_null 확인: 케이스별 max |ΔZ|/|Z| (f ≤ 10 MHz) ≤ 1e-3 이고 게이트 PASS 집합이 7케이스 모두 동일.
- 교과서 형태를 새 기본값으로 **채택**: 어떤 케이스에서도 PASS→FAIL이 없고, (i) 하나 이상 FAIL→PASS 또는 (ii) 7케이스 G3 오차 중앙값이 1 %p 이상 감소. 그 외는 기본값 유지, 결과만 기록.
- 채택 여부와 무관하게 G1–G5 정의는 바꾸지 않는다.

## 5. 산출물
`result_*_any_{a,b}.json` ×14, `compare_a.json`, `compare_b.json`, `EXP11_REPORT.md`. 저장소 사본 `docs/research-claude/2026-09-15/results/exp11/`. 코드 `tools/research-claude/exp11/`(run11.py, table11.py, README.md)와 exp1b/model3의 플래그 추가(기본값 불변).
예상 비용: 케이스당 40 s–15 min(로컬은 클라우드의 약 1.45배), 7케이스 병렬 실행(20 CPU, 64 GB)으로 변형당 약 15 min.
