# SPD Decap PI Evaluator v0.23.1 — Fable 검토 응답과 수정 계획

> 게시 편집: 레드팀 원문의 판단·수치를 보존하고 마지막 근거 표에 GitHub 링크를 연결했다. [원문 스냅샷](RED_TEAM_RESPONSE_ORIGINAL_2026-09-10.md.txt), [원문 근거 JSON](20260910-fable-review-evidence.json), [통합 후속 계획](NEXT_PLAN.md). 원문 해시는 근거 JSON의 report_sha256이며 이 링크 편집본과 구분한다.

검토일: 2026-09-10 KST. 작성: 독립 Red Team. 판정 대상은 `origin/claude/peer-review-fable-20260910`의 **3929d312a69778bee8e895fcda0a7dd09bdcab20**, 원래 packet base는 **215d7f5a69e3fec285c31778492f360e08e34851**이다. 두 커밋 간 변경은 peer-review README와 Fable 의견서 두 문서뿐임을 확인했다. 아래 소스·JSON은 별도 표시가 없으면 이 exact commit에서 `git show`로 읽었다.

**종합 판정: Fable의 연구 순서 변경을 수정 채택한다.** 큰 문제의 M 변형 반복을 멈추고, 저장된 소유 근거를 정리한 다음 대표 작은 문제에서 수치·공간·물리 오차를 분리하자는 방향은 유효하다. 다만 접점 near-L, MQS/평면쌍, 압축 L을 이미 입증된 해법으로 승격할 근거는 없다. 같은 A의 수렴 Z와 완전한 전역 소유가 아직 검증되지 않았다. 따라서 지금 확정할 것은 **판별 순서**이며 구현 계열이나 성능 약속이 아니다.

이번 작업은 문서·저장 JSON·소스 읽기와 작은 스칼라 산술 재현이다. solver/FMM/원본 SPD/PowerSI 실행, `accuracy_parse.py` 접근, 구현 변경, 새 에이전트/외부 에이전트 CLI, Git 변경·게시·다른 과제 전파를 하지 않았다. 기존 stop은 유지한다. 아래 미래 계산 단계는 실행 승인이 아니다.

## 1. 기존 정확도 정책부터 바로잡는다

**이전 Red Team 문서의 “수치 계약 미확정”이라는 포괄적 표현을 정정한다.** 승인된 W5 정책은 이미 있다. 미확정인 것은 새 **1 kHz 확장 범위, 추가 제품 지표, 시간·자원 계약**이다. Fable이 이 불일치를 지적한 것은 채택한다. 다만 Fable의 “기존 정책을 복소 오차로 확장”이라는 설명도 수정해야 한다. 기존 정책에는 이미 위상과 조건부 복소 절대 오차가 있다. [P L155–197]

| 승인된 항목 | 정확한 범위·기준 |
|---|---|
| 저주파 offset | 100 kHz와 1 MHz의 signed magnitude dB 오차 평균의 절댓값 ≤ 1 dB, rail별 |
| 주 격자 | 100 kHz–100 MHz의 241개 log 점 |
| 위상·최대 크기 오차 | rail별 phase RMS ≤ 7°, max ≤ 15°; magnitude 최대 절댓값 ≤ 2 dB |
| magnitude RMS 집계 | rail별 RMS를 동일 가중 평균. 260729 bare/loaded 각각 ≤ 1 dB, 260804 ≤ 1.25 dB |
| 복소 절대 오차 | 참조 임피던스 크기 < 1 mΩ인 점에만 적용: RMS ≤ 100 µΩ, p95 ≤ 200 µΩ. 해당 점이 없으면 N/A |
| 지배 공진 | 같은 격자의 내부 국소 최대값 중 지배 peak 선택 규칙 적용; 주파수 차이 ≤ 10%, 높이 차이 ≤ 2 dB. 참조 peak가 없으면 N/A; 참조 peak가 있는데 후보에 없으면 FAIL |

이 정책은 승인된 **판정 규칙**이며 현재 모델의 통과 증거가 아니다. Fable의 complex 10/20%, phase 5/10°, 0.05 mΩ floor, ΔZ ±30%, 최초 전대역 10분/변경 1분은 **새 초안**으로 보류한다. complex 상대 오차를 0.8/1.6 dB로 환산할 수는 없다. 그 환산은 위상이 같고 크기가 특정 방향으로 변하는 경우의 산술일 뿐이다. 참조 불확실성도 아직 측정되지 않았으므로 이를 10%로 가정하거나 floor에 자동 합산해 합격 문턱을 완화하지 않는다. [F L119–131, L187–189]

## 2. 주요 주장별 판정

“개선 가능성”은 어떤 근거로 기대하는지를 뜻한다. 아래 수치 재현은 기존 결과의 재해석이며 새로운 정확도·성능 개선 실험이 아니다.

| Fable 주장 | 판정 및 수정할 내용 | 근거 | 개선 가능성의 수준 |
|---|---|---|---|
| 54.59%는 A와 무관하며 정보가 없다 | **수정.** 수렴 모델 오차로 쓰지 않는다는 요구는 채택. 같은 A에 실제 적용한 보정의 미검증 후보/참조 차이로는 기록한다. 모든 정보를 버리는 결론은 기각 | F L17, L52–63; R0–R3; §3 아래 | 측정된 수치 진단의 의미를 바로잡음. 정확도 개선 자체는 미측정 |
| warm start 대비 0.35 nΩ, 접점 잔차 감소가 유의하지 않다 | **수정.** warm 대비 75.26–107.86 nΩ; 0.352 nΩ는 diagonal–forward 차이. 접점 norm은 최대 50.61% 감소했지만 수렴·포트 개선을 입증하지 못함 | R0 L165–179; R1 L790–828; R2 L820–858; R3 L862–900 | 감소는 측정 사실. 장기 수렴률·포트 개선은 가설 |
| 참조의 15–17 pH는 실제 기하 루프이며 대부분 plane/via 결손이다 | **수정.** 유효 단자 L 진단과 Re/Im 분해는 채택. 공간 소유별 인덕턴스 분해·원인 확정은 보류 | F L37–48, L265–283; B `points[].reference_zdd_ohm`; §3 | 관측과 양립하는 물리 가설. 원인별 기여·보정량은 미측정 |
| 접점 M에 near-L가 빠진 것이 원인이다 | **수정.** 명시적인 L04 접점 자기 근사가 없는 구조는 확인. 이를 가장 먼저 시험할 수치 가설 중 하나로 둔다. 필수·충분 조건이나 유일 원인으로 단정하지 않음 | M L159–208; FD L199–242; F L65–69 | 소스에 근거한 유력 시험 후보. 반복 수·시간 개선은 미측정 |
| FASTHENRY는 near-L를 모든 전류에 넣고 R-only는 고주파에서 작동하지 않는다 | **기각(보편 주장).** Kamon/White 원문은 inverse-R와 local-inversion을 모두 제안하고 예제별 우열이 바뀜을 보인다 | [K p192, Fig.5–6 및 결론](https://www.rle.mit.edu/cpg/publications/pub116.pdf) | near-L 실험 가설은 유지. 문헌만으로 현재 M 선택 불가 |
| L25/L04 밖 자기 소유가 불완전하므로 소유 표가 먼저다 | **수정 채택.** joint L의 두 sheet 범위는 사실. 밖의 source-owned scalar R/L을 “L 없음”으로 처리하지 않는다. 전역 self/mutual/return 결합 완전성은 별도로 미검증 | J L100–123; O L79 및 summary; C L1268–1270, L3900–3908 | 우선순위 판단의 근거는 강함. 정확도 개선량은 알 수 없음 |
| 과거 census·자기 민감도가 최신 10 MHz의 지배 경로를 입증한다 | **수정.** 서로 다른 조건부 모델·주파수의 역사적 증거로만 사용. 90% 전류 통과량을 자기 오차 완전성 기준으로 쓰지 않음 | F L73–81; C L1268–1270, L2412–2418, L3900–3910 | 소유 조사 위치를 좁히는 근거. 최신 A의 소유별 민감도는 미측정 |
| DC sheet R를 단일 coth Zs로 바꾸면 된다 | **수정.** 내부 도체 확산 검토는 채택. one-face와 two-face를 구분하고 기존 확산·외부 L 소유와 중복 제거를 먼저 확인 | F L155; MF L735–825; MO L44–102 | 손실·내부 L 누락이 확인되면 후보. 포트 Re 증가만으로 skin 단독 원인 확정 불가 |
| 1 kHz–100 MHz이면 MQS+평면쌍이 제품 경로이고 VIE/FEM은 낮은 순위다 | **보류.** 기존 MQS/MFDM을 우선 재사용 후보로 조사하되 주파수만으로 전 보드 적합성을 확정하지 않음. 전파·슬롯·반환 경로 등의 판별 후 계열 선택 | F L163–168; MF L1–18; MO L1–8; V L1–12 | 구현 재사용 가능성은 확인. 보드 전대역 물리 충분성은 미측정 |
| ≤10만 미지수 coarse 직접법이 노트북에서도 안전하다 | **기각(크기 보증).** 4만² real L 한 배열의 크기는 전체 complex A·인수분해 비용이 아님. 메모리·fill·workspace 예산에서 크기를 정함 | F L170, L201–206; §4 산술 | 작은 직접 대조 자체는 유용. 제시된 크기·며칠/몇 분 추정은 미검증 |
| coarse Im Z 두 문턱으로 순수 수치/물리 누락을 판정한다 | **기각(판정 규칙), 작은 대조는 수정 채택.** 메시를 바꾸면 A_h도 바뀐다. same-A 해법, 공간 정련, 물리 변형을 구분해야 함 | F L170–176; §4–5 | 원인 분리의 개선 가능성은 높으나 대조 설계 단계 |
| adjoint 가중 포트 오차로 정지 기준을 바꾼다 | **수정 채택.** 정확하거나 독립 검증된 dual과 잔여 오차 통제가 필요. 현재 x를 dual로 쓴 J−Z를 인증값으로 사용하지 않음 | F L115–117; §4 | 포트 목표와 수치 예산 연결은 유효. 현재 estimator 신뢰성은 미검증 |
| 현 FMM 구조는 하드웨어와 무관하게 부적합하고 압축 L로 분 단위가 가능하다 | **수정.** 기존 고비용은 확인; 고정 기하 L 재사용은 후보. 모든 설계 변경에 재사용하거나 실제 시간 목표를 달성한다는 주장은 보류 | F L83–87, L189; J L100–123; MO L1–8; §4 | 비용 구조 개선 가설. 압축 조립·rank·오차·분해·전체 시간 미측정 |
| adaptive는 구현되지 않았고 L은 거칠게 해도 충분하다 | **수정.** 최근 세 실험에서 고정 메시를 사용했다는 범위만 확인. 전 코드의 해 기반 adaptive 부재나 coarse 정확도를 입증하지 않음 | F L153–157; 이전 Red Team 공간 표현 정정; J L100–123 | near/공간 정련 대조가 필요. 요소 수만으로 과잉 해상도 판정 불가 |
| 대표 rail·bare·설계 변경을 검증해야 한다 | **채택, 새 수치 목표는 보류.** 절대 응답과 설계 민감도를 함께 평가. sparse decade 앵커로 port18 전대역 무공진을 단정하거나 holdout을 튜닝용으로 고르지 않음 | F L185–189; B `points`; P L190–197 | 검증 범위 개선은 명확. ΔZ ±30%의 타당성·성능은 미측정 |
| D1/D2는 계산 재개가 아니다 | **수정.** 저장 문서 소유 정리는 현재 가능. 원본 SPD 조사와 PowerSI 변형 실행은 현재 범위 밖이며 D2는 실제 계산 재개 | F L199–206; 현재 사용자 stop 지시 | 이 리뷰로 실행 권한이 생기지 않음 |

## 3. 산술과 물리 해석의 경계

원본 JSON을 읽어 계산한 스칼라 결과를 [작은 근거 JSON](20260910-fable-review-evidence.json)에 남겼다. 원래 실행 결과와 그 출처 해시는 보존한다.

| 상태 | 공통 THIRD 대비 복소 Z 변화의 크기 | 후보/참조 복소 차이 비율의 변화 | 접점 잔차 norm 감소 |
|---|---:|---:|---:|
| real-H | 75.259821 nΩ | +0.001259233 %p | 29.168578% |
| diagonal Hω | 107.556300 nΩ | −0.001214978 %p | 50.607424% |
| forward Hω | 107.864343 nΩ | −0.001229010 %p | 49.069945% |

diagonal–forward 차이는 **0.352379 nΩ**다. Fable의 수치는 비교 대상을 혼동했다. 그래도 약 0.85 mΩ의 후보 Z에 비해 변화가 매우 작다는 관찰은 유지된다. 같은 A·시작점에서 얻은 독립 세 cycle을 연속 세 cycle로 읽지 않는다. 원래 RHS 기준 상대잔차는 988.01/689.07/710.52이며, 수렴한 A의 포트 응답은 아직 없다. x=0의 동일 norm 상대잔차가 1이라는 산술은 맞지만, zero start가 포트 정확도나 총 수렴 비용에서 우월하다는 증명은 아니다. [R0–R3]

1 kHz/10 kHz 참조 두 점의 `Im Z = ωL − 1/(ωC)` 해는 **C=262.808996 µF, L=−16.111067 nH**다. 같은 C를 사용한 참조 유효 L은 10/100/1000 MHz에서 **14.907600/16.741594/17.500334 pH**, 최신 미검증 10 MHz 후보는 **3.459757 pH**다. 이 산술은 재현된다. 그러나 저주파 fitted L의 음수는 이 단일 상수 series-LC가 해당 포트의 전대역 물리 분해가 아님을 드러낸다. 따라서 고주파의 비교적 평탄한 유효 L을 특정 plane/via 루프의 실제 L로 바로 식별하지 않는다. [B; F 부록 A]

10 MHz `Z_ref − Z_forward`는 **0.492620 + j0.719289 mΩ**, 크기는 **0.871809 mΩ**다. Re/Im 분해를 기본 진단에 추가하는 제안은 채택한다. 하지만 이 둘은 독립적인 원인별 오차 예산이 아니다. 421개 decap의 terminal 결합·전류 분배가 검증되지 않은 상태에서 병렬 ESL 산술로 나머지를 전부 plane/via 소유로 배정할 수 없다. 참조 Re의 주파수 상승도 도체 확산과 양립하지만 전류 재분배, load 응답 및 다른 손실을 배제하지 않는다.

저장 census는 `0.5*Σ abs(external I)`라는 처리량이다. 기존 문서 자체도 손실·임피던스 민감도 순위나 필수 cut이 아니라고 한정한다. `dZ/dλ ≈ j12.1 mΩ` 역시 고정된 과거 1 MHz 전류에 대한 1차 진단이며 self·다른 경로·자기 피드백을 포함하지 않는다. 이를 최신 10 MHz의 전류나 유한 보정량으로 가져오지 않는다. 같은 이유로 “빠진 eddy return은 언제나 L을 줄이므로 이 gap의 원인이 될 수 없다”는 일반화도 채택하지 않는다. 실제 포트는 결합된 손실성 loaded network다. [C L1268–1271, L3900–3910; F L77–81]

## 4. 판별과 재사용 범위를 좁힌다

**접점 M 가설.** `_one_r_direction`은 L25 보조 분기에 self-L을 넣고 L04는 저장 저항 NtD를 사용한다. forward 변형에는 contact→closed 방향의 self-L 결합도 있지만 접점 base에 명시적인 `Pᵀ L_near P` 근사를 추가하지는 않는다. 따라서 “ψ만 바꾼다”는 표현도 이 방향 결합을 보충해야 한다. 실제 A에는 L04·L25 양방향 자기 작용이 남아 있다. 작은 동일 행렬에서 기존 M과 **한 가지 접점 자기 근사**의 비용·포트 오차를 비교하는 후보로 충분하다. cold start, k>0, 새 Krylov 계열, 새 압축법을 한꺼번에 바꾸는 실험은 원인을 다시 섞는다. [M L159–208; FD L199–242; J L100–123]

문헌 확인은 Kamon/White의 [*Preconditioning for Multipole-Accelerated 3-D Inductance Extraction*](https://www.rle.mit.edu/cpg/publications/pub116.pdf) 한 원문으로 제한했다. 웹 직접 열기는 timeout이었고, coordinator가 받은 원문 PDF의 p192 렌더를 직접 확인했다. Fig.5의 packaging은 local-inversion, Fig.6의 ground-plane은 inverse-R가 더 빨리 수렴하며 결론도 문제 의존성을 명시한다. 이 사실은 Fable의 보편적 R-only 실패 주장을 지지하지 않는다. 이는 Fable 목록의 1994 FASTHENRY 본문을 전부 검증했다는 뜻은 아니다. 나머지 기억 기반 서지·방법별 적용 대역·예상 일정은 이번에 독립 원문 검증하지 않았으며 확정 근거로 사용하지 않는다.

**직접법 크기.** 40,000²×8 B=12.8 GB는 real 배열 하나다. complex128에서는 40,000²×16 B=25.6 GB(23.84 GiB), 100,000²×16 B=160 GB(149.01 GiB)가 **행렬 하나**에 필요하다. LU·pivot·복사·workspace·다른 블록은 추가이며 sparse LU의 fill도 확인 전에는 알 수 없다. 그래서 “≤10만이면 32 GB에서도 안전”을 폐기한다. 대표 구조의 크기는 실제 사용 가능 메모리, 행렬 저장 방식과 factor 예상량, 시간 cap에서 역산해야 한다. 512 GB도 정확도나 시간 보증이 아니다.

**세 대조는 분리한다.** 직접법–반복법은 동일 `A_h,b,port,scale`에서 비교한다. h/p·near 구적 대조는 같은 물리·경계·소유를 유지하면서 각각 수렴한 해를 비교한다. 물리 변형은 각 변형의 수치·공간 불확실성을 통제하고 동일 포트·참조 조건에서 비교한다. 더 거친 `A_H`의 Im Z가 0.6 mΩ 이상이거나 참조의 1/3 이하라는 사실만으로 “순수 수치/물리 누락”을 가를 수 없다. 단일 Im 값의 일치도 전체 복소 응답·공진·반환 경로의 일치를 보장하지 않는다. 작은 canonical 해석도 원래 보드 전체를 그대로 대표한다고 가정하지 않는다.

**포트 오차 추정.** 일반적인 `ℓᴴx` 출력에는 `Aᴴz=ℓ`, `δZ=zᴴr`를 쓴다. 이 문제처럼 실수 port 벡터와 복소 대칭 A를 bilinear 방식으로 쓰면 대응되는 transpose 식을 일관되게 사용할 수 있다. 같은 입출력 포트에서 현재 x를 정확한 dual 대신 쓰면 stationary 보정 뒤에 `eᵀAe`가 남는다. 그 항의 크기·상한은 현재 상대잔차 710만으로 알 수 없다. 따라서 `J−Z=xᵀr`를 상한으로 쓰지 않는 데 동의하되, “잔차가 710이므로 2차항이 지배한다”는 단정도 하지 않는다. 검증된 dual/잔여항 통제 전에는 직접 대조 가능한 작은 문제에서 estimator를 검사한다. 수치 예산 1/10은 제안이며, production 정지 기준이 미리 알려진 PowerSI Z에 의존하도록 만들지 않는다. absolute+relative budget 및 backward error를 함께 설계한다. [F L115–117]

**기존 구현의 실제 경계.** `mfdm.py`는 adjacent-gap 4단자 loop, 공유 sheet 손실, finite-thickness two-face coupling을 이미 구현했다. `modal.py`는 rectangular finite-port cavity이고 one-face coth 도체 확산 및 외부 자기 항 분리를 이미 갖는다. `via_peec.py`는 straight vertical solid-filled microvia의 고립 기준 블록이며 pad/antipad/plane spreading을 제외한다. 특히 `global_mna_composable=False`이고 검증된 exact-minus-core 자료 없이 더할 수 없다고 명시한다. “이미 있는 블록들을 결합하면 완성” 대신 중복 소유·누락된 cross coupling·전역 조립 경계를 먼저 정리해야 한다. [MF L1–18, L735–825; MO L1–8, L44–102; V L1–12, L41–99]

**L 재사용의 조건.** 고정 기하·basis·정규화·μ·kernel에서 기하 partial-L의 주파수 독립성은 유용하다. 하지만 도체 내부 확산, 주파수에 따라 달라지는 projection/축약 basis, attachment 또는 geometry 변경까지 같은 축약 연산자가 되는 것은 아니다. FMM도 기하 연산자의 적용 방법이며 매번 새로운 물리 모델을 조립한다는 뜻은 아니다. ACA/H-matrix의 조립 시간·rank·근사 오차·분해 비용을 포함해 현재 방식과 비교해야 한다. 고정 port basis의 termination 값 변경은 low-rank 갱신 후보지만 decap 이동은 연결 위치가 바뀌므로 별도 무효화 조건이 필요하다. 40 cycle/11시간은 일정 감소율을 가정한 외삽이고 10분/1분은 제품 목표 초안이다. 둘 다 실측 보장이 아니다. [F L85–87, L189; J L100–123; MO L1–8]

## 5. 수정한 짧은 단계별 계획

### 단계 1 — 문서·저장 소유 근거를 한 표로 합친다: 현재 가능한 유일한 후속 작업

**산출물:** 기존 packet의 source owner JSON, census, driver와 정책만 사용한 표 하나. 행은 layer/net/island/via group 및 port/termination, 열은 R·내부 L·외부 self-L·mutual-L·G/C·접점/반환 경계·출처/모델/주파수·포함/누락/미확인이다. 전류 처리량은 정확히 어느 저장 모델의 값인지 붙이고 같은 A의 값으로 합치지 않는다. 원본 SPD를 새로 읽거나 배열을 재계산하지 않는다.

**이미 확인된 시작점:** L25/L04 joint 자기 작용 존재; L04 local 보정은 self만 명시; native finite branch scalar R/L 존재; MFDM two-face와 modal one-face 내부 확산 존재; isolated via PEEC는 전역 합성 불가. 이 사실들을 소유 표의 출처로 사용하고 나머지 범위를 미확인으로 남긴다. [J; O; MF; MO; V]

**통과:** 주요 경로와 접점에서 기존 항의 소유·중복 위험·미확인 범위가 출처까지 추적되고, 수치 문제와 물리 문제를 가를 **한 대표 작은 구조**의 요구사항을 적을 수 있음. **반증:** 주장한 “L 없음” 위치에 이미 소유 항이 있으면 누락 주장을 좁히거나 철회. **중단:** 저장 근거가 없으면 미확인으로 표시하고 원본 조사·계산으로 넘기지 않음. **다음 gate:** 사용자의 계산 재개 지시와 단계 2의 구조·출력·비용 cap·판정 예산 확정. 소유 표 완료 자체는 이 gate 통과가 아님.

### 단계 2 — 대표 작은 문제에서 same-A 수치 오차와 공간·물리 오차를 분리한다: 재개 승인 후

**산출물:** 단계 1에서 선택한 최소 대표 구조의 직접/반복 기준해, 동일 물리의 공간·near 구적 정련 비교, 필요한 경우 한 가지 물리 소유 변형과 독립 기준의 비교. 대표 구조는 접점·두 sheet/return·via/port 등 의심 결합을 보존하고, 기하·경계·material·termination을 명시한다. bare/loaded 중 가설을 가르는 최소 조건을 선택한다. 계산 크기는 자원 예산으로 결정하고 ≤10만을 기본값으로 삼지 않는다.

**통과:** same-A 해법 차이가 사전 수치 예산 안이며 residual/backward error와 포트 복소 응답이 일관됨. 그 다음 공간/구적 변화가 공간 예산 안에 들어온 상태에서, 남는 오차와 물리 변형의 효과를 비교할 수 있음. **반증:** 직접해도 후보 문제가 유지되면 해당 동일 A의 반복법을 유일 원인으로 보는 가설은 반증되지만 fine-board 물리 누락까지 자동 확정되지는 않음. 물리 변형 차이가 공간 불확실성보다 작으면 원인 판정 보류. **중단:** 예산 초과, 참조 경계 불일치, 비대표 구조, 오차원 분리 실패. **대형 계산 gate:** 아직 없음; 결과는 단계 3의 구현 하나를 고르는 근거만 제공.

### 단계 3 — 판별 결과에 따라 한 가지 구현만 선택한다: 별도 승인 범위에서

**산출물:** 수치 문제가 분리되면 동일 A의 접점 자기 M 등 **하나**의 최소 변경; 소유 누락/중복이 분리되면 기존 MFDM/modal/via 부품의 경계를 지키는 **하나**의 물리 수정. 두 갈래를 동시에 추진하지 않는다. Fable의 cold/warm·k>0·near-L·Schur·ACA 묶음을 한 실험으로 만들지 않는다.

**통과:** 동일 기준과 예산에서 해당 오차원 또는 총 비용 개선을 측정하고 복소 응답·보존·상반성·수동성의 적용 가능한 검사를 유지. 잔차 감소율이나 반복 수 ≤50 하나만으로 통과시키지 않음. **반증/중단:** 가설의 예측 효과가 없거나 오차를 다른 블록으로 옮김, 소유 중복, 비용 증가, gate 후퇴이면 변경을 채택하지 않고 중단·재검토. **대형 계산 gate:** 작은 문제의 공간/물리/수치 검증, 전체 메모리·시간 외삽 근거, 사용자의 실제 보드 실행 승인.

### 단계 4 — 실제 보드·holdout·설계 변경에서 제품 조건을 검증한다: 마지막 단계

**산출물:** 승인 정책이 지정한 rail/격자/집계를 보존한 실제 보드 결과와, 별도 승인한 1 kHz 확장 결과. development에서 해석법을 고정한 뒤 holdout은 튜닝 없이 평가한다. loaded·bare·공진을 드러내는 조건, decap 제거·값 변경·이동의 ΔZ와 total cold/warm time·peak memory를 기록한다. ΔZ가 거의 0인 경우를 위한 absolute 기준도 필요하다.

**통과:** 기존 W5 및 승인된 추가 계약을 해당 범위에서 모두 충족; 설계 변경 효과와 자원 요구까지 증거가 있음. **반증/중단:** bare/holdout/변경 민감도에서 실패하면 단일 rail 일치를 제품 정확도로 승격하지 않음. 실패를 보고한 뒤 모델 선택 단계로 되돌릴지 판단하며 holdout에 맞춰 계수를 조절하지 않는다. **확대 gate:** 검증된 구조·대역·보드 범위 내에서만 추가 보드나 제품 적용을 판단.

Fable D2의 PowerSI 세 변형은 필요성이 단계 1–2에서 정해질 때만 미래 참조 조건 민감도 실험으로 검토한다. decap 이상 단락·전도도 변경·한 decap 잔류는 전류와 결합을 함께 바꾸므로 각각의 ΔZ를 더해 유일한 “인덕턴스 예산”으로 해석하지 않는다. 이를 모든 모델 성과 판단의 필수 선행 조건으로 두지도 않는다.

## 6. 지금 가능한 다음 한 단계와 남은 계약

**단 하나의 다음 단계는 단계 1의 저장 근거 소유 표 작성이다.** 새 SPD scan, PowerSI, M 시험, FMM, coarse 조립은 포함하지 않는다. 이번 응답은 그 표의 시작점과 판정 구조를 제시하며, 표 전체가 완성되었다고 주장하지 않는다.

기존 W5를 다시 승인받을 필요는 없다. 계산 재개 전에 정할 것은 (1) 1 kHz–100 MHz 확장의 적용 rail·참조 조건·격자와 기존 W5의 관계, (2) 새 복소/위상/ΔZ·참조 불확실성 지표를 추가할지, (3) 수치·공간·물리·참조 오차 예산, (4) 실제 하드웨어에서 cold/warm 시간·메모리 상한 및 단계 2 실행 범위다. Fable의 새 숫자는 이 계약 후보로 남긴다. 지금은 이를 답받기 위한 질문이나 승인 요청을 추가하지 않고 기존 stop을 유지한다.

## 근거 위치

아래 경로는 모두 commit `3929d312a69778bee8e895fcda0a7dd09bdcab20` 기준이다. `S`는 `docs/peer-review/2026-09-10/snapshots/hq/`, `R`은 `S/outputs/research/`를 뜻한다. 표의 JSON 줄번호는 원본 pretty-printed blob 기준이며 필드 경로도 병기했다. 함께 제공하는 근거 JSON에는 산술 입력 파일의 SHA-256을 기록했다.

| ID | 파일·위치 |
|---|---|
| F | [docs/peer-review/2026-09-10/REVIEW_CLAUDE_FABLE_2026-09-10.md](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/REVIEW_CLAUDE_FABLE_2026-09-10.md); 본문에 해당 줄 표시 |
| P | [docs/EVALUATION_ACCURACY.md](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/EVALUATION_ACCURACY.md#L155) L155–197; 승인 정책 |
| B | [S/docs/evaluation-research/astra_primary_band_comparison_2026-09-07.json](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/docs/evaluation-research/astra_primary_band_comparison_2026-09-07.json); `points[].frequency_hz/reference_zdd_ohm/native_zdd_ohm/two_sheet_zdd_ohm` |
| R0 | [R/astra-l04-10mhz-three-direction-complete-current-01/result.json](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/outputs/research/astra-l04-10mhz-three-direction-complete-current-01/result.json#L111) L111–139, L165–179; 공통 THIRD 후보 |
| R1 | [R/astra-l04-10mhz-complete-current-gcrotmk-01/result.json](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/outputs/research/astra-l04-10mhz-complete-current-gcrotmk-01/result.json#L789) L789–828; `metrics`, `raw_z_unvalidated` |
| R2 | [R/astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01/result.json](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/outputs/research/astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01/result.json#L819) L819–858; 같은 필드 |
| R3 | [R/astra-l04-10mhz-forward-closed-gcrotmk-01/result.json](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/outputs/research/astra-l04-10mhz-forward-closed-gcrotmk-01/result.json#L861) L861–900; 같은 필드 |
| M | [S/tools/research/probe_astra_l04_10mhz_two_direction_complete_current.py](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/tools/research/probe_astra_l04_10mhz_two_direction_complete_current.py#L159) L159–208 |
| FD | [R/astra-l04-10mhz-forward-closed-gcrotmk-01/driver-at-run.py](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/outputs/research/astra-l04-10mhz-forward-closed-gcrotmk-01/driver-at-run.py#L199) L199–242 |
| J | [S/tools/research/probe_astra_l25_l04_joint_magnetic_action.py](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/tools/research/probe_astra_l25_l04_joint_magnetic_action.py#L100) L100–123 |
| O | [R/astra-all-finite-current-l-ownership-01/result.json](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/outputs/research/astra-all-finite-current-l-ownership-01/result.json#L79) L79, `scope/summary`; 과거 accepted field의 scalar R/L 소유 근거 |
| C | [S/docs/evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md.txt](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/docs/peer-review/2026-09-10/snapshots/hq/docs/evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md.txt#L1263) L1263–1271, L2412–2420, L3900–3910 |
| MF | [src/spd_decap_pi/_core/solver/mfdm.py](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/src/spd_decap_pi/_core/solver/mfdm.py#L1) L1–18, L735–825 |
| MO | [src/spd_decap_pi/_core/solver/modal.py](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/src/spd_decap_pi/_core/solver/modal.py#L1) L1–8, L44–102 |
| V | [src/spd_decap_pi/_core/solver/via_peec.py](https://github.com/yunhyok/spd-decap-pi-evaluator/blob/3929d312a69778bee8e895fcda0a7dd09bdcab20/src/spd_decap_pi/_core/solver/via_peec.py#L1) L1–12, L41–99 |
| K | [Kamon/White 원문](https://www.rle.mit.edu/cpg/publications/pub116.pdf), 인쇄 p192 Fig.5–6·결론. 제공된 원문 렌더를 직접 확인; 다른 Fable 서지 전체 검증은 아님 |

현재 HEAD에 해당 frozen 파일이 없어도 `git show 3929d312a69778bee8e895fcda0a7dd09bdcab20:<경로>`로 위 근거를 확인할 수 있다. 이 응답은 외부 검토 의견을 반영한 문서 산출물이며 계산 재개·구현 채택·제품 통과 보고가 아니다.
