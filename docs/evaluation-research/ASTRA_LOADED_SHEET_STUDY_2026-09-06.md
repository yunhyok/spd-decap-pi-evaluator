# SPD Decap PI Evaluator v0.23.1 — MFDM loaded-sheet canonical study

## 판정: `STOP`

기존 MFDM은 이 합성 strip의 **같은 물리 포트 위치**에 맞춘 연속 lossy-RLGC 기준과 잘 일치했다. 32셀의 최대 open 2-port 복소 상대 오차는 `7.0327e-7`, loaded 입력 오차는 `8.9480e-7`이다. 그러나 16→32셀에서 셀 중심 포트가 경계 쪽으로 이동하면서 loaded 값이 10 MHz에서 `5.7636%` 변해 사전 기준 `2%`를 넘었다. 기준식 자체도 같은 포트 이동으로 `5.7636%` 변했다. 현재 refinement 순서는 동일한 물리 포트를 비교하지 않으므로 승격 근거로 사용할 수 없다.

이 `STOP`은 유용한 음성 결과다. 새 solver, 전체 보드, PowerSI, raw SPD, C1, FasterCap 또는 Triangle 실행으로 범위를 늘리지 않았다. 기록된 W6 loaded 오차의 원인 귀속도 하지 않는다. C1 `STOP`, WP2/WP3 `PARTIAL` 상태는 그대로다.

## 합성 입력과 범위

| 항목 | 고정값 |
|---|---:|
| strip 길이 × 폭 | 20 mm × 2 mm |
| plane 간격 | 100 µm |
| 구리 두께 / 전도도 | 35 µm / 5.8e7 S/m |
| 유전체 | εr = 4.0, tanδ = 0.02 |
| remote series-RLC | R = 20 mΩ, L = 500 pH, C = 100 nF |
| 주파수 | 100 kHz, 1 MHz, 10 MHz, 100 MHz |
| 본 결과 refinement | 8, 16, 32 cells |

모든 값은 독립적으로 정한 해석용 입력이다. PowerSI 값에 맞춘 계수는 없다. 각 MFDM 포트의 P/G는 같은 raster column의 signal/return conductor에 배치해 로컬 differential nullspace 조건을 지켰다.

## 방법

기존 `compile_mfdm_operator`와 `solve_mfdm`만 재사용했다. MFDM이 반환한 대칭화 전 `raw_impedance_ohm` 2-port를 remote load로 다음과 같이 종단했다.

\[
Z_{in}=Z_{11}-\frac{Z_{12}Z_{21}}{Z_{22}+Z_L}
\]

독립 기준은 MFDM 행렬을 호출하거나 다시 조립하지 않는 연속 lossy-RLGC 선로다.

\[
z'=\frac{2Z_s+j\omega\mu_0d}{W},\qquad
y'=\omega C'\tan\delta+j\omega C',\qquad
C'=\frac{\epsilon_0\epsilon_rW}{d}
\]

\[
\gamma=\sqrt{z'y'},\qquad Z_0=\frac{z'}{\gamma},\qquad
Z(x,x')=Z_0\frac{\cosh(\gamma x_<)\cosh(\gamma(L-x_>))}{\sinh(\gamma L)}
\]

N셀 포트는 `x=Δx/2`, `x=L-Δx/2`에 두고 양끝 open half-cell을 보존했다. analytic loaded 값은 open-Z Schur 상쇄를 oracle에서 반복하지 않고, 두 open stub과 중앙 선로의 직접 입력 임피던스 변환으로 계산했다. analytic Schur는 교차 확인에만 사용했으며 직접식과의 최대 상대 차이는 `1.2494e-11`이었다.

구리 `Zs`는 기존 `copper_surface_impedance`를 양쪽 모델이 공유한다. 따라서 이번 비교는 공간 MFDM 조립/이산화를 검증하지만, 그 구리 재료식 자체를 독립 검증하지는 않는다. MFDM의 원 방법은 [Engin, Bharath, Swaminathan, IEEE TEMC 2007](https://doi.org/10.1109/TEMC.2007.893331), RLGC 전송선·종단·Z-parameter 식은 [Michigan State University EM Research Group의 공식 강의 자료, pp. 30–42](https://www.egr.msu.edu/emrg/sites/default/files/content/module2_fundamental_behavior.pdf)를 기준으로 확인했다.

균일 등전위 bulk-C control은 공간 series R/L을 제거한 선언적 근사다.

\[
Z_{bulk,open}=\frac{1}{y'L},\qquad
Z_{bulk,loaded}=\frac{1}{y'L+1/Z_L}
\]

## 측정 결과

사전 기준은 32셀의 open/loaded 상대 오차와 이전 refinement 변화가 각각 `≤2%`, analytic Schur/direct 차이가 `≤1 ppm`, raw validation·reciprocity·passivity가 모두 통과하는 것이었다.

| Cells | matched open max rel. | matched loaded max rel. | 이전 N 대비 loaded 변화 | center↔boundary max 차이 | max condition | loaded 1차 오차 추정 / 기준 | max Schur 상쇄계수 |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 8 | 9.7728e-6 | 1.3916e-5 | — | 24.7718% | 2.7937e9 | 3.5025% | 4.0152e4 |
| 16 | 2.4429e-6 | 3.5474e-6 | 10.7009% | 12.3859% | 1.1505e10 | 14.4246% | 4.3253e4 |
| 32 | 7.0327e-7 | 8.9480e-7 | **5.7636%** | 6.1929% | 4.6356e10 | **58.1180%** | **4.4857e4** |

32셀의 복소 결과는 다음과 같다. open은 `Z11`; 전체 raw 2×2 행렬은 JSON에 있다.

| f | MFDM open Z11 (Ω) | analytic open Z11 (Ω) | MFDM loaded (Ω) | analytic loaded (Ω) | loaded rel. | 16→32 MFDM / analytic 변화 |
|---:|---:|---:|---:|---:|---:|---:|
| 100 kHz | 2246.068 − j112299.455 | 2245.993 − j112299.477 | 0.0295831 − j15.9119825 | 0.0295831 − j15.9119825 | 1.4602e-10 | 0.001944% / 0.001944% |
| 1 MHz | 224.602 − j11229.945 | 224.602 − j11229.945 | 0.0296084 − j1.5787544 | 0.0296084 − j1.5787544 | 1.9765e-11 | 0.027498% / 0.027498% |
| 10 MHz | 22.4647 − j1122.9647 | 22.4647 − j1122.9647 | 0.0347016 − j0.0361051 | 0.0347016 − j0.0361051 | 2.5429e-8 | **5.763637% / 5.763644%** |
| 100 MHz | 2.26259 − j112.03177 | 2.26260 − j112.03170 | 0.0712840 + j1.1186419 | 0.0712839 + j1.1186409 | 8.9480e-7 | 2.423445% / 2.423716% |

raw solve는 12/12에서 validation, reciprocity, open passivity를 통과했다. 최대 backward residual은 `5.0777e-17`, raw pair reciprocity 상대 오차는 `4.1424e-14`, raw-Schur와 대칭화 후 Schur의 최대 차이는 `1.8199e-12 Ω`였다. loaded 입력의 실수부도 12/12에서 음수가 아니었다.

그러나 `solve_quality_estimate_passed`는 12개 중 3개만 통과했다. 32셀·100 kHz에서 open `Z11`은 약 `112.3 kΩ`, loaded 값은 약 `15.9 Ω`이고 condition estimate는 `4.6356e10`이다. solver의 open forward-error estimate를 Schur 식에 1차 전파한 값은 loaded 기준의 최대 `58.118%`다. 이 값은 분모 교란과 고차항을 생략한 보수적 **1차 추정**이며 엄밀한 상한이 아니다. 반대로 작은 residual이나 작은 raw/symmetric 차이만으로 loaded 정확도를 보증할 수도 없다. 이 합성점에서는 상쇄를 피한 analytic direct 기준과 실제 차이가 작다는 사실까지만 수용한다.

등전위 bulk control의 boundary-loaded 기준 대비 차이는 100 kHz `0.0622%`, 1 MHz `0.8803%`, 10 MHz `198.1714%`, 100 MHz `73.8804%`였다. 저주파 극한은 회복하지만, 원격 load까지의 sheet/return R/L과 위치 의존성을 잃는다는 구분이 확인됐다.

## 경계 discrepancy와 중지 이유

16→32 변화의 최댓값은 MFDM `5.7636367%`, 같은 이동 포트를 사용한 analytic 기준 `5.7636435%`다. 즉 matched-model 오차가 아니라 `Δx/2`에 놓인 포트가 N마다 이동하는 효과가 지배한다. 허용 상한이 유용한지 확인한 단 한 번의 임시 16/32/64 probe에서도 32→64 변화는 `2.9887%`, analytic 변화는 `2.9887%`로 `2%`를 넘었다. 64셀 matched loaded 오차는 `2.2464e-7`이었지만 condition은 `1.8576e11`, loaded 1차 오차 추정은 기준의 `232.894%`로 악화됐다. 더 세분화하지 않고 `STOP`을 유지한다.

최소 후속 실험은 새 backend가 아니라 동일한 물리 포트를 고정하는 것이다. 예를 들어 `x=L/8`, `7L/8`을 4/12/36셀의 center index `0/1/4`와 대칭 index에 배치하면 포트 위치를 바꾸지 않고 이산화 수렴만 분리할 수 있다. 이번 범위에서는 실행하지 않았다.

## 실제 실행 기록

HQ가 기존 프로젝트 필수 패키지를 `outputs/research-runtime`에 준비한 환경을 사용했다. 제품 소스와 의존성 선언은 변경하지 않았다.

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'outputs/research-runtime')
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_mfdm_loaded_sheet.py --self-check
```

용어 정정 전후 두 번 실행했고 모두 `SELF_CHECK PASS`였다. 이 check는 electrically-short open line의 bulk-shunt 회복, analytic Schur/direct loaded 동일성, 1-cell invalid input 거부를 확인한다.

```powershell
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_mfdm_loaded_sheet.py --output docs\evaluation-research\astra_loaded_sheet_study_2026-09-06.json
```

최종 실행은 `STOP`, 내부 측정 `0.0470 s`, shell wall `1.356 s`, 12 solves, 최대 physical nodes `64`, relative unknowns `32`였다. 최초 실행도 같은 판정(`0.0320 s`)이었고, “bound”로 오해될 진단 이름을 “first-order estimate”로 정정한 뒤 결과를 재생성했다.

```powershell
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_mfdm_loaded_sheet.py --cells 16 32 64 --output "$env:TEMP\astra_mfdm_loaded_sheet_64_probe_20260906.json"
```

이 임시 probe는 `STOP`, 내부 측정 `0.0620 s`였다. 본 JSON의 8/16/32 계약은 바꾸지 않았다. 기존 본 JSON 경로로 다시 실행한 overwrite check는 비영(도구 관측 `EXIT=1`)으로 종료하며 `refusing to overwrite existing output`을 출력했다.

계획했지만 실행하지 않은 항목은 전체 보드/PowerSI/raw SPD/D117/C1/FasterCap/Triangle, production adapter 및 제품 정확도 비교다. 이 결과의 허용 범위는 합성 canonical 진단까지다.
