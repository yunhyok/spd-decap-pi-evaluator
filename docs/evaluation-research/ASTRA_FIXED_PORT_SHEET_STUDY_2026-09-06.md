# SPD Decap PI Evaluator v0.23.1 — MFDM fixed-port sheet study

## 판정: `ACCEPT_CANONICAL_ONLY`

기존 MFDM의 합성 strip에서 물리 포트를 `x=L/8`, `x=7L/8`로 고정하고 셀 중심에 정확히 맞춘 결과, `N=4,12,36` 모두 matched analytic lossy-RLGC 기준에 수렴했다. 기존 2% gate는 변경하지 않았고, 모든 gate가 통과했다. 이 판정은 합성 canonical 진단에만 적용한다.

## 고정 포트와 범위

| Cells | near / far center index | physical positions |
|---:|---:|---:|
| 4 | 0 / 3 | 2.5 mm / 17.5 mm |
| 12 | 1 / 10 | 2.5 mm / 17.5 mm |
| 36 | 4 / 31 | 2.5 mm / 17.5 mm |

각 포트의 P/G는 같은 raster column의 signal/return conductor에 쌍으로 배치했다. `--port-layout fixed-eighth`는 위의 정확한 셀 집합만 허용하며, 기본 `moving-center`와 기존 `8,16,32` 동작은 유지된다.

strip, 재료, remote series-RLC load, 주파수(100 kHz/1 MHz/10 MHz/100 MHz)는 기존 study와 같다. MFDM의 대칭화 전 `raw_impedance_ohm`를 그대로 사용해

\[
Z_{in}=Z_{11}-\frac{Z_{12}Z_{21}}{Z_{22}+Z_L}
\]

로 Schur 종단했다. 기준은 MFDM 행렬을 재조립하지 않는 독립 lossy-RLGC direct TL 입력 변환이며, analytic Schur는 direct 식 교차 확인으로만 남겼다. 기존 copper surface impedance는 두 모델이 공유한다.

고정 run의 실제 analytic 포트 좌표와 open stub은 `x=L/8`, `x=7L/8`이다. JSON의 `analytic_center_port_z_ohm` label은 기존 moving-center output schema에서 유래한 이름을 보존한 것으로, fixed run의 물리적 좌표를 뜻하는 새 판정 근거로 해석하지 않는다. `solve_quality_estimate_passed`는 전체 12개 run 중 정확히 `3/12`만 true였다.

## 수렴과 진단

| Cells | max open complex rel. | max loaded complex rel. | 이전 고정 포트 대비 loaded 변화 | max condition | max loaded 1차 추정 / 기준 | max reciprocity rel. | passivity |
|---:|---:|---:|---:|---:|---:|---:|:---:|
| 4 | 3.9099e-5 | 5.3106e-5 | — | 6.1973e8 | 0.77697% | 9.0892e-16 | PASS |
| 12 | 4.3440e-6 | 5.9002e-6 | 4.7204e-5 | 6.4232e9 | 8.0529% | 4.0486e-15 | PASS |
| 36 | 4.8269e-7 | 6.5557e-7 | 5.2446e-6 | 5.8700e10 | 73.5919% | 5.7964e-14 | PASS |

36셀의 finest gate 측정값은 open `4.8269e-7 ≤ 2%`, loaded `6.5557e-7 ≤ 2%`, 이전 refinement 변화 `5.2446e-6 ≤ 2%`였다. coarsest 대비 finest loaded 오차 개선 gate도 `6.5557e-7 ≤ 1.05 × 5.3106e-5`로 통과했다. 12/12 raw validation·open passivity·loaded 실수부 허용 검사가 통과했고, analytic Schur/direct 최대 상대 차이는 `4.1591e-12 ≤ 1e-6`이었다. bulk control과 boundary-loaded direct 기준의 최대 상대 차이는 `1.9817`이었다.

condition estimate는 36셀에서 최대 `5.8700e10`까지 증가했고, solver가 제공한 forward error를 Schur에 1차 전파한 loaded 추정은 기준 대비 최대 `73.5919%`였다. 이 값은 분모 교란과 고차항을 생략한 1차 추정이며 엄밀한 bound가 아니다. `solve_quality_estimate_passed`는 전체 12개 run 중 정확히 `3/12`만 true였고, 모든 run의 별도 진단 gate가 아니었다. 실제 matched direct 기준 오차와 raw reciprocity/passivity 결과를 함께 기록했을 뿐, 작은 residual이나 raw/symmetric 차이를 loaded 정확도 보증으로 해석하지 않는다.

## 실제 실행 기록

집중 self-check는 한 번 실행했다.

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'outputs/research-runtime')
$env:PYTHONDONTWRITEBYTECODE='1'
$timed = Measure-Command { & 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_mfdm_loaded_sheet.py --self-check }
Write-Output ("EXIT=" + $LASTEXITCODE)
Write-Output ("ELAPSED_S=" + $timed.TotalSeconds)
```

실행 결과는 `EXIT=0`, wrapper 측정 runtime은 `1.1573898 s`였다. 이 check는 analytic recovery, fixed-eighth 좌표/center-index 불변식, 호환되지 않는 fixed-eighth cell count 거부를 확인한다.

고정 포트 study도 한 번만 실행했다.

```powershell
$target = Join-Path (Get-Location) 'docs\evaluation-research\astra_fixed_port_sheet_study_2026-09-06.json'
if (Test-Path -LiteralPath $target) { throw "refusing to overwrite existing output: $target" }
$env:PYTHONPATH=(Join-Path (Get-Location) 'outputs/research-runtime')
$env:PYTHONDONTWRITEBYTECODE='1'
$sw=[Diagnostics.Stopwatch]::StartNew()
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_mfdm_loaded_sheet.py --port-layout fixed-eighth --output $target
$code=$LASTEXITCODE
$sw.Stop()
Write-Output ("EXIT=" + $code)
Write-Output ("ELAPSED_S=" + $sw.Elapsed.TotalSeconds)
if ($code -ne 0) { exit $code }
```

관측 결과는 `SPD Decap PI Evaluator v0.23.1`, JSON status `ACCEPT_CANONICAL_ONLY`, `EXIT=0`, shell runtime `0.9830832 s`였다. JSON 내부 runtime은 `0.031000000017229468 s`, 12 solves, 최대 physical nodes `72`, relative unknowns `36`, process limit 60초 이내였다. 기존 경로의 overwrite refusal 검사는 코드에 그대로 유지했다.

## 비주장과 상태 경계

- PowerSI에 fit하지 않았고 PowerSI, raw SPD/D117, 전체 보드의 정확도를 주장하지 않는다.
- W6 loaded discrepancy의 원인을 귀속하지 않는다.
- 새 solver, product adapter, C1, WP2, WP3를 수정하거나 승격하지 않았다. C1/WP2/WP3 상태는 기존과 동일하다.
- 이 결과는 synthetic canonical strip의 fixed-port convergence evidence이며 production solver promotion 근거가 아니다.
