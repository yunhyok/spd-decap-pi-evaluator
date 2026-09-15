# SPD Decap PI Evaluator v0.23.1 — 작은 실제 포트 물리 계약

2026-09-14. 이 계약은 구현을 진행하기에 충분하다. 검증한 fixture는 **267 전류 / 172 전하 / 4 전극**, 원본 Trace464278 bridge 96 tetrahedra와 원래 L02 rows 0/75이다. 비교에서 바꾸는 항은 **-05의 XY cross delta 하나**이다. 신규 cap·PWR 결합과 모든 P/R/D/H/종단은 두 경우에 동일하게 고정한다.

소유 경로는 `C:/Users/User/.codex/worktrees/353f/SPD Decap PI Evaluator/outputs/child-port-physics/`이다. 원본 `5950`과 구현 `06f1`은 읽기 전용으로 확인했다. 이 폴더의 계산·검산은 전체 보드 조립을 요구하지 않는다.

## 1. 확정된 형상과 다른 연구 경계

- PWR는 실제 Trace464278, trace index 32, SITE0:3576→3574의 bridge만이다. 원본 closure의 96 tets / 68 vertices와 직접 대조했다. 258 face-current 중 내부 126, 경계 132이다. 원 post-interface의 왼쪽 32면과 오른쪽 32면을 각각 새로운 유한 source/load 전극으로 선언한다. 원 post와 다른 배선은 제거된 연구 경계이다. 나머지 68면은 개별 자유 표면전하이다.
- L02는 원래 배열의 두 삼각형 전체와 z=55..75 μm를 유지한다. 전류 ID는 `[0,74,75,81,749]`, local IDs는 `[[81,75,0],[749,75,74]]`, 부호는 `[[+,+,+],[+,-,-]]`이다. current 75는 내부 공유면이다.
- Return source는 current 0의 x=-49.7 mm 외곽 wall 하나, load는 current 749의 x=-48.15 mm wall 하나이다. 이 두 면만 각각 한 유한 전극으로 선언한다. cut walls 81/74는 독립 자유 전하 면으로 바꾸고, 두 prism의 상·하 cap 네 면도 자유면으로 둔다. 분리된 cut·cap 전압을 합치지 않는다.
- 실제 최소 x 간격은 **56.5098375480291 mm**, 두 도체의 z 간격은 30 μm이다. 어느 도체도 이동하지 않는다. source/load의 ideal 외부 lead 형상은 모델에 없다. 원 보드 포트와 등가가 아니며, 이 값으로 PowerSI 정확도 개선을 주장할 수 없다.
- 전극 순서는 `[PWR_source,L02_source,PWR_load,L02_load]`이다. 1 MHz 원 표의 `CAP_1608_10UF`는 **0.003805177917341136 − j0.02055969550385439 Ω**이다. R/L/C 개별 값이 null임을 확인했고 피팅하지 않았다.

재확인한 구현 파일: `C:/Users/User/.codex/worktrees/06f1/SPD Decap PI Evaluator/outputs/child-port-fixture/child_port_fixture.npz`, SHA256 `47bec5abf13437ff1fb8e5216ebb2c1344be51b60aa2656d109ac4ba27cb3bd4`. 미사용 정점을 제거한 최종 파일이며 원 좌표·D/H/R·correction·종단 등가를 다시 확인했다.

## 2. 4모드와 D/H/R

삼각형 면적 A, 높이 h=z₁−z₀, 꼭짓점 vₐ에 대해 국소 단위 외향 전류 기저는 다음과 같다. 기저 단위는 m⁻², 계수는 A이다.

\[
b_a(x)=((x_{xy}-v_a)/(2Ah),0),\quad
b_b(x)=(z-z_1)e_z/(Ah),\quad b_t(x)=(z-z_0)e_z/(Ah).
\]

각 기저는 자신의 면에서만 적분 외향 flux +1이며, 모두 `div b=1/(Ah)`이다. 따라서 **XY도 체적 발산을 갖는다**. cap 순서는 `[row0_bottom,row0_top,row75_bottom,row75_top]`, 전체 전류에서는 PWR 258개와 XY 5개 뒤이다. bottom Jz의 음수 부호가 bottom 외향 법선 −ez와 곱해져 +1이 된다. `b_t−b_b=e_z/A`는 유지해야 하는 두께 방향 통과 전류이다. 공유 수직면에서 cap Jz의 접선 불연속은 H(div) 적합성과 모순되지 않는다.

L02 전하 순서는 `[volume0,volume75,wall81,wall74,cap0_bottom,cap0_top,cap75_bottom,cap75_top]`. 체적 전하는 prism당 **하나**이며, 그 행에 XY 세 모드와 cap 두 모드의 발산을 모두 넣는다. 적분용 tetra/triangle 분할은 전하 소유자를 추가하지 않는다.

```
             XY 0 74 75 81 749 | 0b 0t 75b 75t
D volume0      1  0  1  1   0 |  1  1   0   0
D volume75     0 -1 -1  0   1 |  0  0   1   1
D wall81       0  0  0 -1   0 |  0  0   0   0
D wall74       0  1  0  0   0 |  0  0   0   0
D four caps                  |        -I4
H source       1  0  0  0   0 |  0  0   0   0
H load         0  0  0  0   1 |  0  0   0   0
```

cap 행의 XY 열은 모두 0이다. 전극은 H, 자유면은 D의 음의 외향 flux를 소유한다. 내부 current 75의 surface q/H는 없다. 정확한 항등식은 **1ᵀD − 1ᵀH = 0**이다. 합계 항등식만으로는 잘못된 체적 q 분할을 검출하지 못하므로 위 행별 구조도 검사했다.

구리 σ=59.59 MS/m. 원 보드 전류 전체의 R 대각을 가져오지 않고 두 prism의 지지영역만 적분한다. c=삼각형 중심이면

\[
(R_{xy})_{ab}={ (c-v_a)\cdot(c-v_b)+\sum_r|v_r-c|^2/12\over4Ah\sigma},\qquad
R_{cap}={h\over6A\sigma}\begin{bmatrix}2&-1\\-1&2\end{bmatrix}.
\]

XY에는 지정된 local signs를 양쪽에 곱한다. 서로 다른 prism의 Rcap 교차항과 XY–cap R은 0이다. PWR는 기존 tetra RT0 기저를 그대로 쓴다.

## 3. 방정식·소유·수동성

시간 규약은 exp(+jωt). 전하 q는 각 부피/면에 걸친 적분 전하 C, P는 V/C, L은 H, D/H는 무차원이다.

\[
\begin{bmatrix}R+j\omega L&-D^TP&H^T\\D&j\omega I&0\\-H&0&Y\end{bmatrix}
\begin{bmatrix}i\\q\\v\end{bmatrix}=\begin{bmatrix}0\\0\\s\end{bmatrix},\quad
s=(1,-1,0,0)^T,\quad Y=uu^T/Z_{load},\quad u=(0,0,1,-1)^T.
\]

임의 grounding은 추가하지 않는다. 균형 source와 Y의 행합 0으로 전체 전하 중성이 따라오며, 각 도체 전하를 별도로 0으로 제한하면 안 된다. ω≠0에서 q를 소거해도 `DᵀPD/(jω)`가 남는다. 예를 들어 한 cap의 scalar 항은 `Pvv−2Pvb+Pbb`; cap q를 전류와 연결한다고 volume–cap/cap–cap P가 불필요해지지 않는다.

배경은 기존 `source_background()`의 **air / ABF / EL190T / ABF / air**, 4개 인터페이스, 1 MHz εr=Dk(1−jDf)를 유지한다. 유한 구리는 배경 ground plane이 아니다. source layer를 수평 무한 층으로 연장하고 내부 빈 구리 band를 인접 수지로 채우는 기존 가정도 유지한다. L은 별도 선언된 정자기 μ₀ 스칼라 커널이다. 균질 εr=4 모델로 바꾸지 않는다.

완성된 두 경우 각각 R 양정치, 실수 L의 대칭/PSD, 복소 P의 ordinary reciprocity를 확인한다. `P=(P+P.T)/2`를 선택한다면 raw 양방향 오차를 먼저 기록한다. `P.H` 평균으로 유전 손실을 지우면 안 된다. P의 Hermitian real 및 imaginary parts의 PSD도 확인한다. 특히 exp(+jωt)에서 유전 손실은 **ω Im(qᴴPq) ≥ 0**이다.

같은 phasor 전력 정규화로 다음을 비교한다(peak phasor의 평균 전력은 모든 항에 1/2):

\[
v^T\overline{s}=i^HRi+j\omega i^HLi-j\omega q^HPq+v^T\overline{Yv}.
\]

전극 KCL·행별 연속식·전체/도체별 전하·위 복소 전력 수지를 기록한다. 단일 주파수 PSD/손실 screen은 광대역 수동성 증명이 아니다. 음의 eigenvalue를 clip하거나 대각항을 임의 보강하지 않는다.

## 4. 필요한 작은 field 블록과 계산 경로

| 블록 | 이 fixture에 필요한 정확한 범위 |
|---|---|
| L PWR–PWR | 258×258, 실제 96 tets의 기존 기저. 동일 support의 기존 항만 재사용 |
| L return XY–XY | 5×5, -05에 저장된 point+local-self 기반과 지정 cross delta |
| L cap–cap | 4×4. HQ 양방향 계산과 이 폴더의 독립 2×2 self 두 개 |
| L PWR–return | 258×5 XY 및 258×4 cap, 일반 affine inner moments |
| L XY–cap | 5×4와 transpose는 스칼라 자기 커널에서 점별 dot=0으로 정확히 0 |
| P | PWR 164 q, return 8 q의 모든 자기·상호 항. return 8×8은 HQ 60개+이 폴더 cap self 4개 |

`tetra_inner`가 S(x)=∫1/R 및 M(x)=∫(y−x)/R를 주므로, `b(y)=B(y−o)+c`의 정확한 내부 적분은 **b(x)S(x)+BM(x)** 이다. prism을 3 tets로 적분 분할해도 원 prism affine 기저를 평가한다. 각 tetra의 RT0 flux로 다시 사영하면 원 -05 XY 기저가 보존되지 않는다. 관측점 anchor를 기저에 두 번 빼지 않는다.

P의 분해는 `physical halfspace support integrals + deep remainder`이다. 기존 저장 P0 volume/원 exterior wall self는 **같은 support·높이·정규화·매질**일 때만 재사용한다. 새 cut wall 74/81과 cap은 원 보드 self가 없을 수 있다. 이들만 필요한 새 자기/근접 블록을 계산하면 된다. same-prism volume–cap, opposite caps, 공유 edge 인접 caps는 대각 self 교정만으로 완성되지 않는다.

작은 P 계산에는 기존 `physical_pair`, `AdaptiveOuter`/`AnisotropicOuter`, `wall_self`를 재사용한다. 층 경계에 걸친 support는 먼저 분할하고, z=25 μm 면은 interface coefficient를 사용한다. `deep_spectral` 또는 기존 bounded deep action을 이 172개 support의 정규화된 spread/gather에 적용하며 깊은 층의 항을 한 번 포함한다. 이때 동일 실수 spread와 transpose gather를 쓰고 deep 구적을 별도로 확인한다. 전체 native 희소 맵·kernel·보드 DtN은 선행 조건이 아니다.

## 5. -05만 바꾸는 비교

`rows0-75-cross-delta.npz`의 `cross_delta_row/col`은 **5개 슬롯의 0-based 위치**이다. 실제 current ID가 아니다. `global_current_columns=[0,74,75,81,749]`를 통해 fixture의 XY index 258..262로 옮긴다. 데이터에는 local outward signs와 양방향 값이 이미 반영되어 있으므로 다시 부호를 곱하거나 transpose를 중복 추가하지 않는다. 총 17 entries, 공유 75의 대각 **−2.9220590651978033e−9 H**도 유지한다.

`L1=L0+E ΔL05 Eᵀ`; L0는 저장 point component + 저장 local self delta이며 나머지 모든 L은 두 경우 동일하다. -04의 `sparse_correction_data_h`는 full physical component여서 덧붙이면 중복이다. ΔL 자체는 indefinite여도 정상이다. 완성된 L0/L1의 에너지가 검사 대상이다.

## 6. 실제 독립 검증과 종료 경계

- 최종 fixture를 원 PWR closure와 직접 대조했다. `D−H` 최대오차 0; tetra 면 flux 오차 6.04e−12; R의 독립 구적/폐형식 오차는 PWR 3.55e−14, return 5.40e−15. R 최소 eigenvalue 7.38e−10 Ω. 구현 초안의 volume D 누락·shared 면 q·cut/cap 병합 문제는 최종 저장물에서 해결됐다.
- -05의 실제 sparse entries 재구성과 두 5×5 L의 양정치를 확인했다. 최소 eigenvalue는 base 5.20109e−12 H, candidate 5.23037e−12 H. 이는 전체 L의 passivity 검사를 대신하지 않는다.
- cap self는 해석적 z 적분과 기존 삼각형 covariogram으로 독립 계산했다. h와 ρ에 대해 s=√(ρ²+h²), I₀=asinh(h/ρ)/h, I₁=1/(s+ρ), I₃=(s+2ρ)/(3(s+ρ)²); depth kernels은 `Kbb=Ktt=(2I₀−3I₁+I₃)/3`, `Kbt=(I₃−I₀)/3`이다. L에는 μ₀h²/(4π)를 곱한다. cap charge에는 `1/sqrt(ρ²+d²)`를 같은 overlap에 적분하고 반사 높이를 적용했다. direct d=0의 radial 적분은 정확히 1/(3·sector_radius)이다.
- row0 order128, row75 order64에서 최대 self-energy/charge 상대 변화가 각각 2.82e−8, 1.05e−6이다. HQ cap 자기항과의 energy-normalized 차이는 row0 **1.08676e−4**, row75 **1.09318e−6**. row0의 일반 spectral 상대차이는 7.71e−8에 불과해 약한 [1,1] 모드 차이를 가린다. 5e−5 energy gate 통과로 보고하지 않는다. 최소 수정은 독립 계산된 신규 cap self 두 블록을 두 비교 모델에 동일하게 사용하거나 해당 작은 self만 energy 기준으로 확인하는 것이다.
- `cap-self-independent.npz`의 `P_cap_halfspace_self_per_f`는 `[0bottom,0top,75bottom,75top]` 순서의 네 complex 값이다. **deep는 포함하지 않았다.** 동일 파일의 L self 두 블록은 기존 XY self와 별개인 신규 cap self이다.
- 새 self를 넣은 저장 cap 4×4는 최소 eigenvalue 2.13143e−18 H로 양정치이며, raw 양방향 차이의 energy 정규화는 1.30471e−5이다. 누락된 cap self만 채운 저장 return halfspace P 8×8의 Hermitian real/imag 최소 eigenvalue는 각각 4.83810e8 / 1.97682e6 V/C로 양수이다. raw reciprocity는 1.42949e−6이다. 이 검산에는 deep/PWR 결합이 포함되지 않는다.
- `fixture-readback.json`, `independent-checks.json`, `saved-block-screen.json`은 검사 범위와 입력 해시를 기록한다. 실행: `python -B outputs/child-port-physics/check_physics.py`, `python -B outputs/child-port-physics/check_fixture_readback.py`, `python -B outputs/child-port-physics/check_saved_blocks.py`. 실행 위치는 이 worktree이다.

HQ가 남겨야 할 결과는 같은 행렬의 직접/반복해 복소 Z 차이, 잔차와 수지, 실제 elapsed, 별도 구적 민감도이다. 아직 이 task에서 전체 P/L 또는 port solve를 수행하지 않았고 공간 정련도 하지 않았다. 적분 세분화는 공간 기저 정련이 아니다. 이 작은 연구의 완성이 원 보드 또는 1 kHz–100 MHz 정확도 목표 완성을 뜻하지 않는다. 후속 통합은 HQ에 인계하며 반복 감사나 미래의 모든 블록 완성을 요구하지 않는다.

보조 Astra/xhigh는 cap 기저·Gram·전하 소거식을 독립 검토했다. 위 최종 계약은 실제 저장 자료로 재확인했다. 보조 검토의 마지막 문장에 나온 여러 L/P 교차 블록 전환은 채택하지 않았다. 전환 범위는 -05 하나로 고정한다.

공개 이론 확인은 facet normal moments와 H(div) 적합성을 설명한 [DefElement RT 정의](https://defelement.org/elements/raviart-thomas.html), 회로/장·유전체 결합을 다룬 [Ruehli/Heeb의 IBM 연구 원문 안내](https://research.ibm.com/publications/three-dimensional-interconnect-analysis-using-partial-element-equivalent-circuits)로 한정했다. 여기의 prism 식과 수지는 직접 도출·검산한 것이며 문헌이 이 fixture나 수치를 인증한 것은 아니다.
