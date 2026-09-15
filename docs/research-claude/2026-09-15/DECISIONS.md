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
