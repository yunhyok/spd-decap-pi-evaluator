# SPD Decap PI Evaluator v0.23.1 — Astra Step 2 Sol supervision

기준은 2026-09-06, branch `codex/astra-evaluation-resume-20260906`, 시작 HEAD
`e2f219e71d8c8a397009f72242cce10d78cfc7ab`이다. Sol은 구현자가 아니라 감독자로서
Luna의 bounded probe와 Terra의 read-only 감사를 검토했다. 제품 `src`와 C1/WP2/WP3
상태는 바꾸지 않았고, PowerSI 값으로 합성 입력을 맞추지 않았다.

## 감독 판정

| 산출물 | Sol 판정 | 적용 범위 |
|---|---|---|
| fixed physical-port MFDM | `ACCEPT_CANONICAL_ONLY` | 같은 raster column의 P/G paired point port와 합성 strip |
| current global-Y audit | `ACCEPT_AUDIT`; integration `NO/STOP` | 현재 코드의 seam, ownership, nullspace 경계 |
| four-terminal Nodal-Y probe | `STOP_EXPECTED_INTERFACE_INCOMPATIBLE` | 두 local gauge를 가진 dense terminal Y의 기존 global-MNA 투입 |
| balanced `SeriesBranchBlock` witness | `ACCEPT_PAIRED_CANONICAL_ONLY` | 미리 호환되는 두 paired loop에 한정 |
| source-bound Evaluation 후보 | `STOP_NOT_READY` | 실제 terminal/return mapping과 exact prior-term exclusion이 아직 없음 |

이는 W6의 기존 loaded magnitude underprediction을 이 메커니즘에 귀속하는 판정이 아니다.
고정 포트 수렴과 paired composition은 다음 작은 source-bound 실험의 수학 경로를 좁히지만,
현재 제품 정확도나 실제 보드 연결성을 증명하지 않는다.

## 위임과 검토 과정

| 역할 | 실제 위임 | 결과 |
|---|---|---|
| Luna implementation | `gpt-5.6-luna`, effort `max`; fixed-port study와 four-terminal interface probe | fixed study ACCEPT, Nodal interface expected STOP |
| Terra audit/review | `gpt-5.6-terra`, effort `ultra`; current integration audit와 root-owned paired-branch read-only review | audit ACCEPT, paired canonical review ACCEPT |

첫 Terra spawn은 정확히 `agent thread limit reached`로 실패했다. 다른 작업 slot이 종료된
이벤트 뒤 한 번만 재시도해 성공했다. 두 agent 아래에 추가 manager나 worker를 두지 않았다.

초기 interface 구현은 세 차례 거절하거나 교정했다. 단일 2-terminal capacitor는 distributed
sheet를 구별하지 못해 거절했고, 서로 끊긴 두 capacitor를 4-terminal로 부른 구현도 거절했다.
연속 sheet로 바꾼 뒤에도 diagonal terminal-Y를 loaded driving-point response로 사용한 기준은
remote/internal voltage elimination을 생략했으므로 거절했다. 최종 probe는 연속 TOP P/BOT G
strip의 두 launch pair와 balanced 2 x 2 직접 기준을 사용한다.

## fixed physical-port MFDM 검토

[study script](../../tools/research/study_mfdm_loaded_sheet.py),
[report](ASTRA_FIXED_PORT_SHEET_STUDY_2026-09-06.md),
[JSON](astra_fixed_port_sheet_study_2026-09-06.json)을 검토했다. 기존 moving-center 기본값과
기존 JSON은 보존하고, `fixed-eighth`만 추가했다.

| cells | near/far cell index | 실제 x |
|---:|---:|---:|
| 4 | 0 / 3 | 2.5 mm / 17.5 mm |
| 12 | 1 / 10 | 2.5 mm / 17.5 mm |
| 36 | 4 / 31 | 2.5 mm / 17.5 mm |

12개 solve의 finest open complex error는 `4.8269134e-7`, finest loaded error는
`6.5556955e-7`, 12-to-36 loaded change는 `5.2446140e-6`로 사전 선언한 2% gate를
통과했다. 대칭화 전 `raw_impedance_ohm`으로 Schur termination을 계산했다. 최대
cancellation factor는 `34619.4509`, condition estimate는 `5.8700e10`이며,
`solve_quality_estimate_passed`는 정확히 3/12만 참이다. solver forward estimate를
Schur 식에 1차 전파한 값은 기준 대비 최대 `73.5919%`다. 분모 교란과 고차항을 생략했으므로
엄밀한 bound가 아니며, analytic discrepancy와 함께 보아야 한다.

고정 결과 JSON의 `reference.model`에 남은 `open half-cell end stubs`는 이전
moving-center 설명에서 이어진 낡은 label이다. frozen JSON은 다시 쓰지 않았다. 실제 고정
계산의 end stub은 각 grid에서 선언한 물리 포트 `L/8`, `7L/8`까지의 거리이고, script의
향후 출력 문구는 `open end stubs at declared physical port positions`로 교정했다.

실제 Luna 실행은 다음 두 개다.

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'outputs/research-runtime')
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_mfdm_loaded_sheet.py --self-check
```

`EXIT=0`, wrapper `1.1573898 s`였다.

```powershell
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\study_mfdm_loaded_sheet.py --port-layout fixed-eighth --output docs\evaluation-research\astra_fixed_port_sheet_study_2026-09-06.json
```

`EXIT=0`, wrapper `0.9830832 s`, JSON 내부 `0.0310000000172 s`, 최대 physical nodes
72, relative unknowns 36이었다. 출력 overwrite refusal과 invalid fixed-cell 입력도
self-check 범위에서 확인했다. 이전 moving-center 5.7636% refinement STOP은 그대로다.

## current global-Y 경계 감사

[Terra audit](ASTRA_GLOBAL_Y_INTERFACE_AUDIT_2026-09-06.md)은 다음을 정확히 추적했다.

- `SurfacePatchPlaneOperator.condense_finite_ports`는 footprint `W`,
  `A=null(N.T@W)`, `Za=R.T solve(K,R)`, `Yport=A solve(Za,A.T)`와 gauge/residual을 이미
  계산한다. 이는 paired compatible-current 모델이며 arbitrary absolute nodal block이 아니다.
- `LayerwiseNetworkSubstrate._solve_all_ports`는 현재
  `supplemental_nodal_admittance`를 live solve로 전달하지 않는다. 확인된 호출은 1 GHz
  shadow audit이고 `replacement_ready=false`, `production_ready=false`다.
- native `DispersiveAdjacentGap` partial은 supplemental Y 전에 항상 stamp되고 exact owner
  exclusion이 없다. 현재 owner 충돌 검사는 Via와 termination에는 동작하지만 동일 plane
  G/C의 additive duplication을 막지 않는다.
- 서로 떨어진 P/G footprint는 연속 plane 위에서도 `N.T@W`가 full rank가 되어
  `finite-port input has no admissible return mode`로 멈출 수 있다. co-location, virtual GND,
  ideal G0/G1 short 또는 fitted common-mode R/L로 이 제한을 숨기면 안 된다.

외부 D104 sealed geometry가 없다는 주장은 거절하고 HQ의 read-only 증거로 바로잡았다.
receipt는 19,728 bytes, SHA-256
`bf3d965281f3fd09cf6be26cbd49cca22b4b3085f265ba859349e8091754263b`다.
L28 `cell_0258.wkb`는 2,426,237 bytes/2,048 holes/SHA-256
`1d894ff46db6fdf1e1662d1ae9d45cbb6676b5d002c0357c27c74f9f1e4835e1`, L29
`cell_0259.wkb`는 2,040,201 bytes/4,679 holes/SHA-256
`a438379bf22b47e412b763eebb49c3aae7252c5872d5ac7c31513499108b9053`다. 둘 다 약
`+-49.7 mm` whole-island 범위다. geometry 존재와 source-terminal/material/ownership-complete
composition 준비는 다른 조건이다. Terra는 이 external root를 직접 열지 않았다.

Terra가 실제 실행한 검사는 관련 source/test 11개 UTF-8-SIG read+compile이며
`syntax=ok files=11`이었다. bundled runtime에 pytest module이 없어 계획했던 focused
pytest는 실행하지 않았고, 설치나 재시도도 하지 않았다.

## four-terminal Nodal-Y discriminator

[probe](../../tools/research/probe_surface_global_y_interface.py)와
[JSON](astra_surface_global_y_interface_2026-09-06.json)은 3 x 1 mm 연속 TOP P/BOT G
strip에서 왼쪽 `P0/G0`, 오른쪽 `P1/G1` 네 terminal을 사용한다. 2 MHz에서 각 local
base C는 0.75 nF이고 remote load는 1 uF, 80 mOhm, 10 nH series RLC다.

condensed terminal Y는 constraint rank 2/nullity 2, reciprocity error
`4.84185e-16`, compatible-current residual `4.64071e-17`, passivity minimum
`-1.73212e-14 S` 대 tolerance `4.21251e-8 S`다. balanced 좌표에서 remote load
port 전압을 Schur 소거해 종단한 driving impedance는
`0.0821717889659812 + j0.0498950973895913 ohm`이다. pair-Z Schur 및 full nodal
pseudoinverse 직접 기준과 수치 정밀도로 일치한다.

기존 Nodal supplemental seam은 정확히
`layer-network Kron block is singular near ('P0', 'G0', 'P1', 'G1')`로 멈춘다.
이는 네 terminal의 dense edge graph가 한 component처럼 보이는 동안 두 local gauge가
남는 경계를 확인한 expected STOP이다. load/body의 실제 solve stamp 완료나 response PASS로
세지 않았다.

음성 대조군도 판별력이 있다.

- 같은 연속 TOP P/BOT G strip에서 왼쪽 TOP P와 오른쪽 BOT G만 선택하면
  `finite-port input has no admissible return mode`다.
- Via 또는 termination과 supplemental owner가 충돌하면 각각 기존 guard가 fail-closed한다.
- native base G/C와 같은 owner의 supplemental G/C를 더해도 거절되지 않으며, 두 drive의
  admittance가 one-stamp 기준 정확히 `2.0 x`가 된다.

최종 status는 `STOP`이다. reciprocal/passive condensation 자체는 통과했고,
Nodal multi-gauge composition이 실패한 것이다. random exception을 expected evidence로
세지 않도록 self-check는 exact singular message, nullity 2, normalized matrix gate와 세
negative control을 고정한다.

측정 JSON은 이 classification-only 강화 전 생성된 frozen receipt라서 기존
`terminal_y_is_reciprocal_passive_and_floating=false`와 응답/stamp 관련 false 항목을
그대로 보존한다. 전자는 `6.35529e-14 S`의 수치 row sum에 지나치게 작은 절대
`1e-18 S` gate를 적용한 낡은 분류이며 물리적 nonpassivity 판정이 아니다. 후자는
expected singular STOP 뒤의 response와 numeric stamp를 미실행으로 나타낸다. 보존된
측정값을 바꾸거나 evidence JSON을 재생성하지 않고, 강화된 script와 이 감독 판정에서
각 의미를 분리했다.

HQ가 강화된 현재 script를 한 번 독립 실행했다.

```powershell
$env:PYTHONPATH=(Join-Path (Get-Location) 'outputs/research-runtime')
$env:PYTHONDONTWRITEBYTECODE='1'
& 'C:\Users\User\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe' tools\research\probe_surface_global_y_interface.py --self-check
```

결과는 `SELF_CHECK PASS`, `EXIT=0`, shell `1.61036 s`였고 새 evidence output은 쓰지
않았다. 별도 중복 narrative는 만들지 않았으며 이 감독 보고서가 probe 해석을 기록한다.

## paired branch 독립 검토

HQ가 작성·실행한 [paired-branch probe](../../tools/research/probe_balanced_sheet_branch.py)와
[JSON](astra_balanced_sheet_branch_2026-09-06.json)을 Terra가 재실행 없이 독립 검토했다.
`B.T @ Yterminal @ B / 4`를 coupled loop Y로 만들고 inverse를 기존
`SeriesBranchBlock`에 넘긴다. 독립 기준은 remote RLC를 full reduced mesh K에 먼저
stamp한 뒤 port impedance를 구한다.

기존 branch saddle system과의 loaded 상대오차는 `2.971820748e-10`, reconstruction
error는 `1.176143340e-16`이다. 실제 branch incidence graph는 두 galvanic component를
유지하고 gauges `(P0,P1)`를 선택한다. node order를 뒤집어 gauges `(G1,G0)`로 바꾸어도
impedance 변화는 `0.0`이었다. branch residual은 `9.09e-12`, backward residual은
`3.78e-19`이며, loaded port-impedance 출력의 최소 Hermitian eigenvalue는
`1.006e-3 ohm`이다.

이 probe는 별도 nodal base G/C를 주지 않아 synthetic 시스템 내부 중복은 없다. 하지만
LayerSurfaceNetwork partial stamp를 우회하므로 production exact exclusion 증거가 아니고,
shifted arbitrary P/G에 물리 return을 제공하지도 않는다. Terra 판정은
`ACCEPT_PAIRED_CANONICAL_ONLY`다.

## 다음 최소 작업

다음 source-bound 실험은 새 backend, manifest/schema 또는 full-board solve부터 만들지 않는다.
기존 source provenance/owner ledger와 D104 기록의 query seam을 재사용해 먼저 실제 후보
P/G launch의 footprint, layer/net/island closure, 재료와 retained/replaced partial이 이미
충분히 결속돼 있는지, 그리고 terminal mapping이 paired contract와 호환되는지 묻는다.
호환되는 작은 후보가 확인될 때만 `SeriesBranchBlock` canonical 경로와 기존 G/C partial의
정확한 단일 제외를 결합한 one-frequency shadow 비교를 설계한다. 호환되지 않으면 STOP을
유지하고 실제 sheet-current/return 또는 별도 bounded PEEC 후보를 조사한다.

PowerSI fit, co-located virtual pin, ideal common ground, raw SPD 재scan, 38,920-contact
condensation, full-board/FasterCap/Triangle 실행은 이 단계에 포함하지 않는다. C1의 1800초
STOP과 WP2/WP3 PARTIAL도 변경하지 않는다. 최종 알고리즘 및 다음 실행 승인은 Astra HQ가
판정한다.
