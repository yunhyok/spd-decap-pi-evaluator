# 결정 기록 (ADR 스타일, 2026-09-15)

## D1. Astra 전장 경로(FMM3D 하이브리드) 폐기
3.78M 미지수, 1점·1포트에 2214 s·24.4 GB, info=1/3 미수렴, KCL 오차 18–50 mA(`handoff.md:130-135`). 이산화(P1/RT0/hybrid)를 바꿔도 오차가 26.2–29.4%에서 정체 — 수치 문제가 아니라 물리 형태 문제. 노트북 VM 3GB·1kHz–100MHz sweep 요구와 구조적으로 양립 불가. 근거: `review_deep.md` §3.

## D2. 경량 2-D plane-pair 하이브리드 채택
PowerSI 자신이 plane/EM+circuit+transmission line "hybrid solver"(Cadence 포럼 57376). MFDM 단위셀(R=`Zs`, L=`μ0d`, C=`εA/d`) + via 동축 L + decap SPICE + fringing/균질화를 EXP-1→8로 단계적으로 구축. 260729 Port18 1MHz 오차를 이전 Astra 기준 26.18%에서 2.7%(EXP-8)로 낮췄다. 근거: `proposal_fresh.md` §0-1, `RESEARCH_LOG.md` §5.

## D3. 검증 게이트 G1–G5 정의
G1: 1–100kHz \|ΔRe\|≤0.05mΩ(직렬 R). G2: 0.1–1MHz ΔL(f) ±5pH(공진 인접 L). G3: 1MHz 복소오차<10%(1점 종합). G4: 1–10MHz 오차<20%·f_res±10%(공진역). G5: 10–100MHz 보고만(표피효과 미착수). 실험 전체에서 동일 게이트를 고정해 튜닝 없이 단계별 비교 가능하게 함. 근거: `EXP1_REPORT.md`~`EXP8_REPORT.md` 전체.

## D4. 양측 대칭 표면임피던스 채택 (내부 L = μ0t/12)
기존 함수 `mfdm.py:782 copper_surface_impedance`는 docstring대로 one-face 식(내부 L=μ0t/3)이며 제품 코드가 이를 오용한 것이 아니다. 오용은 Claude의 EXP-1~3 코드가 이 식을 양측 귀환 셀에 그대로 적용한 데 있었다(EXP-4 A1). 양측 셀에 `½·Zc·coth(γt/2)`(내부 L=μ0t/12)를 적용해 G3를 12.7%→9.1%로 개선, 격자에도 견고(9.0%@fine25). 내부 L=0(EXP-4 (a))이 더 잘 맞지만(7.7%) 물리적 근거가 없어 채택하지 않음. 근거: `EXP4_REPORT.md`.

## D5. cavity-wall(PowerSI 관례) 기본값 + physical(GND-only) 옵션
레일 평면 셀마다 위/아래 3개 층 안에서 **넷과 무관하게** 첫 금속을 기준면으로 삼는 규칙(EXP-8)을 기본값으로 채택한다. 7케이스 중 5개에서 ΔL이 ±3.3pH로 붕괴, 예측-실측 상관 0.95–0.97. 물리적으로는 떠 있는 저Q 전원면이 자기 투명하다는 논거(비회전 전류, 폐로 임피던스 6자리 큼)가 여전히 유효하므로(EXP-1c 물리 기각 유지, `review_fresh_exp1.md` Q1), 이는 **PowerSI 도구 관례를 추정**한 것이지 물리 모델 교체가 아니다. 제품에는 "PowerSI-compatible(cavity-wall)" 기본값과 "Physical return(GND-only)" 옵션을 명시 모드로 노출하고 차이를 함께 표시한다(구현 미착수). 근거: `EXP8_REPORT.md` §3-4.

## D6. f_res(min|Z|) 대신 Im Z 영교차(f0)를 병행 지표로 사용
Q≈0.2–0.6의 과감쇠 공진에서 min\|Z\| 바닥이 평평해 f_res가 조건 불량 지표다(`EXP9_REPORT.md` §1). Im Z=0 교차(f0, 826점 기준 보간)를 2차 지표로 도입했으나 cavity-wall 모드에서 f0가 오히려 악화되는 레일(P16 +19%, P14 +13%)이 있어, **저주파 ΔL 개선(G2)과 공진역 정확도(G4/f0)는 독립적으로 관리**해야 한다는 것이 결정이다. G4 게이트 자체는 변경하지 않는다(두 지표 모두 보고). 근거: `EXP9_REPORT.md` §1, `EXP8_REPORT.md` §2.

## D7. 기준선을 exp28/p(균질화 경계조건 결함 수정 + 평면 C 단위 수정)로 변경 (2026-09-17)
EXP-27/28: `homog.batched_gx`의 창 끝 열 주입 결함으로 폴리곤 가장자리 셀 엣지 G = 0 → decap 고립 44/92포트. 수정(`homog_face_fix`, 자유 계수 없음)으로 고립 0, 파국 오차 제거(최대 382 % → 54 %), P18 0.81 %. 게이트 PASS 수는 줄었으나(G3 30 → 22) 결함이 가리던 균일 R 결손(참조/모델 1.46)이 드러난 결과다. 소유자 위임(정확도 유리 방향)에 따라 채택. 코드 기본값과 EXP-8 영수증·smoke는 그대로 두고, 이후 변형은 `c_unit_fix=True, homog_face_fix=True`를 포함하며 비교 기준선은 `table11.py --baseline exp28:p`다. via 길이 표면 간(변형 q, EXP-32)은 92포트 오차를 줄이지만 물리 정의 근거가 불확실하고(스택 중간 패드 두께 이중 계상) 문턱 근소 미달이라 채택하지 않고 플래그로 보존한다 — PowerSI microvia 정의 확인 후 재판정.

## D8. G4의 f_res 항 유지, f0는 병행 보고 (2026-09-17)
EXP-33/34: 두 설계 모두 C·L은 맞는데 f_res(min|Z|)와 f0(Im Z 영교차) 모두 +5~14 % 편향. f0는 패키지에서 더 잘 조건화되나(+0.13~0.17) PCB에서는 차이 없음(+0.02, 문턱 0.1 미달) → 사전 등록 기준에 따라 f0 미채택. G4 정의 유지, f0 병행 기록. 1–10 MHz 곡선 오차 항(20 %)이 실질 판별 항이다.


## D9. 패키지 microvia는 구리 충전(filled)으로 가정 (2026-09-19, 소유자)
소유자 지시: PowerSI microvia 모델 정의(도금 두께 0의 의미)에 대한 GUI 확인 대신 **microvia 내부는 구리로 충전된 것으로 가정**하고 진행한다. 코드 확인: 제품 `via_model.classify_via_conductor`는 COPPER·드릴 ≤ 150 µm·인접 두 도체층 사이 유전체 1층·유전체 두께 ≤ 드릴(MLO 프로파일)이면 SOLID(충전 원기둥, 면적 πd²/4)로 분류하고, 그 외에는 도금 배럴 면적 `π·d·min(20 µm, d/4)`를 쓴다 — d ≤ 80 µm에서는 이 값이 충전 원기둥 면적과 같다. 따라서 260729/260804의 40 µm microvia(레일 via의 약 95 %)는 어느 분기에서든 **이미 충전 원기둥으로 계산**되고 있으며, D9는 수치를 바꾸지 않는다. 150 µm core PTH만 도금 배럴(HOLLOW)로 남는다(물리적으로 타당). 결론: 남은 패키지 R 결손(참조/모델 1.46)은 via 배럴 충전 여부로는 설명되지 않는다는 EXP-25/32의 판단이 유지되며, microvia 관련 미확정 항목은 길이 정의(EXP-32 q, 1/3 설명)뿐이다. 이 가정은 엔진 영수증 `validity` 문구에 명시한다.

## D10. G4 f_res 항은 케이스별 참조 f_res와 비교 (2026-09-20, 소유자)
결함: `src/spd_pi_engine/receipt.ladder_gates`는 케이스의 참조 공진 `fres_r`를 인자로 받고도 쓰지 않고, G4의 f_res 항을 `|f_res_model − 1.585 MHz| / 1.585 MHz`로 계산했다. 1.585 MHz는 `exp3/run3.py`가 260729 Port18 한 레일의 참조 f_res를 상수로 박아 둔 값이라(다른 레일의 참조 f_res는 1.5–7.2 MHz) 나머지 레일에서는 의미 없는 수이고, 그래서 G4가 **틀린 이유로** 어디서나 실패했다(W14-b §10-2에서 발견: 27영수증 G4 0/27).
결정: 리뷰어 권고대로 정정한다. G4 PASS 규칙의 f_res 항은 `G4_f_res_rel_err_vs_ref = |f_res_model − f_res_ref| / f_res_ref < 0.10`이며, 진폭항(1–10 MHz 오차 < 20 %)과 D3의 게이트 정의(f_res ±10 %) 자체는 바뀌지 않는다. `fres_r`가 없거나 유한하지 않으면 NaN이고 G4는 False다.
바뀌는 것: 이 커밋 **이후** 엔진이 쓰는 영수증의 `ladder_gates`. 새 키 `G4_f_res_ref_Hz`·`G4_f_res_rel_err_vs_ref`가 추가되고 PASS의 G4가 이 항으로 판정된다. 옛 키 `G4_f_res_rel_err_vs_1.585MHz`는 연구 영수증과의 비교를 위해 계속 기록하되 판정에는 쓰지 않는다. 이 커밋 이전 영수증에는 옛 키만 있다.
바뀌지 않는 것: 수치(5개 수치 모듈 미수정, `numerics_id` 27d81996… 불변), D3·D8의 게이트 정의, 그리고 **연구 결과 전부**. `tools/research-claude/`와 `results/expN/`·`EXPn_REPORT.md`의 G4 통과 수는 상수 규칙으로 계산된 역사 기록으로 그대로 두고 **재판정하지 않는다**(읽기 전용 규칙). D8의 "f_res +5~14 % 편향" 결론은 EXP-33/34가 참조 f_res와 직접 비교해 얻은 것이지 이 게이트에서 나온 것이 아니므로 영향이 없다.
재판정한 곳은 W14-b-2 부록 하나뿐이다(같은 영수증 27개, 새 풀이 없음): G4 PASS 0/27 → 2/27, `docs/engine/W14B_REPORT.md` §10-6. 구현·검증은 `docs/engine/W14D_REPORT.md`.
