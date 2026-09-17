# MLO 설계 규칙(FICT, 2024-01, Compact Size, Z0 B-type) 대조 메모 — 2026-09-17

출처: 소유자 제공 `D:\Downloads\FICT_MLO_design_rules_CompactSize_20240131_ (Z0_Btype).pdf`(22쪽, CONFIDENTIAL — 저장소에는 요약만). 대상: 260729/260804 SPD의 MLO.

## SPD 스택업과의 대조 (260729 extract 기준)
| 항목 | 설계 규칙 | SPD |
|---|---|---|
| 빌드업 Cu 두께 | 20 µm(Normal layer) | 20 µm(L02–L47), TOP/BOTTOM 25 µm |
| 빌드업 유전체 | ABF-GL102 30 µm | ABF-GL102 30 µm ×40 |
| LVH(레이저 via) | φ40 µm / 패드 φ60 µm(80 µm 피치), 스택 최대 6 | `DR-xxxx_60`: 드릴 40 µm, 패드 60 µm, COPPER, 2층 |
| 코어 IVH | 드릴 φ150 µm(가공 직경, 완성 홀 아님) / 패드 φ350 µm | `DR-2128_350`: 드릴 150 µm, 패드 350 µm, 8층 |
| 코어 재질 | EL190T(표준 코어) | EL190T 70/100/105 µm |
→ SPD의 기하(추출기의 반지름→직경 해석 포함)는 설계 규칙과 일치한다. via 길이(층 중심 간) = 30 + 20 = 50 µm, 표면 간 = 70 µm(EXP-32 ℓ′/ℓ 1.40).

## 문서가 답하지 않는 것
- LVH의 충전 여부(구리 충전 vs 컨포멀 도금)와 원추형(상/하 직경). "Stacked via(같은 XY축 위 LVH 스택 허용)" 규칙은 충전(플랫) via를 전제하므로 물리적으로는 구리 충전 원기둥(현 SOLID 모델)이 타당하다.
- IVH는 "드릴 직경 ≠ 완성 홀"이라 도금 배럴(HOLLOW, t_p ≈ 20 µm)이 맞다.
- 따라서 남는 물음은 실제 기하가 아니라 **PowerSI가 padstack `DR-xxxx_60`(Material=COPPER, 도금 두께 미기재)을 어떻게 모델링하는가**(기본 도금 두께·충전 해석·길이 정의)이며, 이는 PowerSI GUI/문서에서만 확인된다.
