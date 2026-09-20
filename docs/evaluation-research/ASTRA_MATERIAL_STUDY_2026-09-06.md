# SPD Decap PI Evaluator v0.23.1 — source-only 재료 모델 실험

## 판정: `ACCEPT_DIAGNOSTIC / DEFER_PRODUCT_PROMOTION`

D103에 봉인된 재료 표만으로 양의 Debye 완화항을 갖는 후보를 계산했다. 실행은 `PASS_DIAGNOSTIC`, 내부 측정 0.0045392초였다. 이는 수학적 후보와 잔차를 얻었다는 뜻이며, 원본 표를 정확히 재현하거나 실제 보드 정확도가 개선됐다는 뜻은 아니다. Luna가 작성한 스크립트를 Astra가 검토하고 해석식 자체 검사, 봉인 입력 실행, 결과·보고서 마무리를 수행했다.

ABF-GL102의 Dk는 10→20 GHz에서 3.2→3.4로 증가한다. 양의 Debye 완화항만으로는 이 증가를 정확히 재현할 수 없다. EL190T는 단조 감소하지만 공급된 3개 주파수만으로 완화 스펙트럼을 식별하기에는 정보가 부족하다. 두 가중치에서 비슷한 곡선을 얻었다는 사실은 불확실성 상한이나 재료 정확도 보증이 아니다.

## 입력과 대조군

입력은 다음 기존 영수증 하나다. 원본 SPD와 PowerSI는 읽지 않았다.

```text
D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json
size = 204735 bytes
SHA256 = 4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9
```

스크립트는 크기·해시, 제품/버전/PASS, 내장 원본 SPD 크기·해시, material source-record 연결 및 중복값 일치를 검증한다. 280개 ABF 행은 7개 고유점, 21개 EL190T 행은 3개 고유점으로 합쳐진다. 같은 원본 점이 여러 층에서 반복된 횟수를 통계적 가중치로 쓰지 않는다.

| 재료 | 주파수 (Hz) | Dk | Df |
|---|---:|---:|---:|
| ABF-GL102 | 1e6 | 3.4 | 0.0041 |
| ABF-GL102 | 1e9 | 3.3 | 0.0040 |
| ABF-GL102 | 5.8e9 | 3.3 | 0.0044 |
| ABF-GL102 | 1e10 | 3.2 | 0.0046 |
| ABF-GL102 | 2e10 | 3.4 | 0.0051 |
| ABF-GL102 | 4e10 | 3.3 | 0.0058 |
| ABF-GL102 | 6e10 | 3.3 | 0.0060 |
| EL190T | 1e6 | 4.7 | 0.010 |
| EL190T | 1e9 | 4.3 | 0.011 |
| EL190T | 1e10 | 4.1 | 0.012 |

대조군은 기존 `DielectricDispersion.interpolate`의 log-frequency 선형 보간과 양끝 값 고정이다. 원본 knot에서 Dk/Df RMSE는 모두 0이다. 별도의 시간영역 인과적 실현을 구성하지 않았다는 뜻으로 JSON의 `is_causal_realization=false`를 사용하며, 이것만으로 표가 물리적으로 불가능하다고 판정하지 않는다.

## 계산과 물리적 범위

시간 convention은 `exp(+jωt)`이며 다음 함수를 사용했다.

\[
\epsilon^*(\omega)=\epsilon_\infty+
\sum_k\frac{\Delta\epsilon_k}{1+j\omega\tau_k},\qquad
\epsilon_\infty>0,\ \Delta\epsilon_k\ge0,\ \tau_k>0.
\]

`τ=logspace(-12,-5,9)`를 고정하고 `Dk`와 `Dk×Df = -Im(ε*)`를 함께 맞췄다. 각 채널을 원본 값의 RMS로 나누고 고유 주파수에 같은 가중치를 주었다. `balanced_channels`는 손실 채널 배율 1, `loss_emphasis_2x`는 배율 2다. 두 번째 정책은 목적함수에서 손실 제곱오차의 가중치를 네 배로 만든다. [SciPy 공식 NNLS 문서](https://docs.scipy.org/doc/scipy/reference/generated/scipy.optimize.nnls.html)의 비음수 최소제곱을 재사용했다. 이는 원본 재료 표에 대한 근사이며 PowerSI fitting이 아니다.

이 후보의 수동성과 인과성은 구조에서 나온다. 각 완화항은 `τ dP/dt + P = ε0 Δε E`이고 pole은 `s=-1/τ`다. `Δε>0`인 항의 저장 에너지는 `P²/(2ε0Δε)`, 소산율은 `τ(dP/dt)²/(ε0Δε)`로 비음수다. `Δε=0` 항은 생략할 수 있다. 따라서 영 초기조건의 안정적 인과 응답과 비음수 소산을 갖는다. 이 설명은 이번 모델에 대한 직접 유도다. [del Castillo와 García-Colín의 논문](https://journals.aps.org/prb/abstract/10.1103/PhysRevB.37.448)은 유전체 완화의 배경 자료이며 이 NNLS 구성이나 해당 두 재료를 검증하는 근거로 사용하지 않는다.

양의 완화항의 실수부는 주파수에 대해 증가하지 않는다. 따라서 ABF의 증가 구간은 이 모델 계열과 충돌한다. 이것은 모든 수동·인과 재료 모델이 불가능하다는 결론은 아니다. pole 수를 늘려 이 제약을 감추지 않았다.

## 실제 잔차와 식별 가능성

| 재료 / 정책 | Dk RMSE | Dk 최대 절대 잔차 | Dk×Df RMSE | Df RMSE | 활성 pole |
|---|---:|---:|---:|---:|---:|
| ABF / balanced | 0.0565969 | 0.104019 | 0.000247369 | 0.0000847513 | 6 |
| ABF / loss×2 | 0.0565982 | 0.103920 | 0.000247365 | 0.0000847654 | 6 |
| EL190T / balanced | 0.0395323 | 0.0516502 | 0.00000945043 | 0.000110284 | 4 |
| EL190T / loss×2 | 0.0395465 | 0.0516687 | 0.00000236345 | 0.000109393 | 4 |

ABF의 Dk 최대 상대 잔차는 약 3.22%, EL190T는 약 1.26%다. 원본 재료 표에 허용 오차가 제공되지 않았으므로 이 값을 재료 정확도 PASS로 바꾸지 않는다.

ABF 설계행렬은 14행×10계수이고 수치 rank는 10이지만 condition은 두 정책에서 약 `5.50e13`, `1.09e14`다. full rank가 안정적인 계수 식별을 뜻하지 않는다. EL190T는 6행×10계수, rank 6인 미결정 문제다. 직사각 행렬의 6개 양의 singular value 비율을 전체 계수 식별 가능성으로 오해하지 않도록 condition은 `null`로 기록했다. 비음수 제약이 일부 모호성을 줄일 수 있어도, 여기서 실제 재료의 유일한 완화 스펙트럼을 증명한 것은 아니다.

## 원본 범위 밖의 결과

관심 범위는 100 kHz–1 GHz, 넓은 샘플 감사 범위는 10 kHz–100 GHz다. 두 재료 모두 최초 원본 점은 1 MHz이므로 **100 kHz는 외삽**이다. ABF의 원본 상한은 60 GHz, EL190T는 10 GHz이며 감사 범위를 원본 근거 범위로 확대하지 않는다.

| 100 kHz 결과 | 기존 보간 Dk / Df | balanced 후보 Dk / Df | loss×2 후보 Dk / Df |
|---|---|---|---|
| ABF | 3.4 / 0.0041 | 3.407574 / 0.000629011 | 3.407095 / 0.000633391 |
| EL190T | 4.7 / 0.010 | 4.699312 / 0.001021066 | 4.699309 / 0.001020982 |

가중치 두 개 사이의 100 kHz Dk 차이는 ABF 약 0.0140%, EL190T 약 0.0000704%로 작다. 그러나 기존 보간과 후보의 Df 차이는 크다. 같은 pole grid와 모델 계열을 공유한 두 결과의 근접성만으로 낮은 주파수의 손실을 확정할 수 없다. 이 실험은 두 가중치의 민감도만 조사했으며 pole-grid 민감도, 원본 측정 오차, 대체 재료 모델에 대한 신뢰구간을 제공하지 않는다.

## 실행과 검증

제품 코드·의존성 선언은 변경하지 않았다. 현재 bundled Python에 없는 기존 필수 패키지 SciPy와 solver import에 필요한 httpx/Shapely는 HQ가 무시되는 `outputs/research-runtime` 안에 준비했다. 사용한 NumPy는 2.3.5, SciPy는 1.18.1이다.

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'outputs/research-runtime')
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_source_dielectric_candidates.py --self-check
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_source_dielectric_candidates.py --input 'D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json' --output docs\evaluation-research\astra_material_study_2026-09-06.json
```

자체 검사는 별도로 계산한 단일 pole 해석식 `2.1+1.3/(1+jωτ)`의 응답 복원, 양의 계수 조건, 넓은 주파수 샘플의 비음수 소산 및 잘못된 원본 값 거부를 확인했다. HQ 실행은 `PASS_SELF_CHECK`(0.003252초), 봉인 입력 별도 확인은 `PASS_DIAGNOSTIC`(0.006675초), 최종 JSON 저장 실행은 `PASS_DIAGNOSTIC`(0.0045392초)였다. 기존 출력은 exclusive create로 덮어쓰지 않는다. 재현 시 다른 출력 경로를 사용한다.

결과는 [JSON](astra_material_study_2026-09-06.json), 실행 코드는 [연구 스크립트](../../tools/research/study_source_dielectric_candidates.py)에 있다. 재료 후보를 제품에 적용하지 않으며 WP2 공간 유전체/void-fill, WP3 물리 접촉, C1 또는 실제 보드 PowerSI 정확도 gate를 닫지 않는다. 현재 우선순위는 loaded-sheet의 물리 포트를 고정한 수렴 검증이다.
