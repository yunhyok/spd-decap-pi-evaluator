# SPD Decap PI Evaluator v0.23.1 — 첫 포트 해의 후속 검토

2026-09-14. **동결된 q8/n32 포트 해는 수치 허용치 안의 PSD와 복소 전력 검사를 통과한다.** `P_loss > 0`을 요구한 기존 gate만 물리적 PSD 조건으로 고치는 판단에 동의한다. 원본 행렬·해·실패 기록은 변경하지 않았다.

## PSD 판정과 전력

동결 입력은 HQ `outputs/research/astra-source-fixture-port-20260914-q8-n32/`의 두 `fields.npz`, `solution.npz`, `A.npz`이다. 전체 경로·SHA256은 `frozen-port-review.json`에 저장했다. field 적분이나 선형 해법을 재실행하지 않고 저장 배열과 벡터를 읽어 검사했다.

| 독립 검사 | 결과 |
|---|---:|
| 정규화 P_loss 차원 / spectral norm | 172 / 106.974179695 |
| 최소 eigenvalue, eigvalsh | −3.20952033e−17 |
| 최소 고유쌍 residual | 5.21979170e−14 |
| 정규화 Hermitian defect, 2-norm | 1.55222373e−15 |
| 독립 반올림 allowance | 4.08552279e−11 |
| HQ 수정식의 allowance | 3.89314859e−11 |
| 두 L의 차이와 지정 -05 사이 최대오차 | 4.13590306e−25 H |

`M=S⁻¹ P_loss S⁻¹`, `S=sqrt(diag(P_loss))`에서 독립 검사식은 `λmin(M) ≥ −10 γn ||M||₂`, `γn=n eps/(1−n eps)`이다. HQ의 `8 n eps ||M||∞`도 같은 산술 규모로 이 저장물에 적절하다. 이는 **수치 PSD screen**이며 인증된 연속 연산자 오차 한계가 아니다. 원시 eigenvalue와 allowance를 함께 남기고 eigenvalue clipping·대각 보강을 하지 않는다. 손실 nullspace는 허용한다. 현재 저장물의 손실 대각은 모두 양수이므로 zero-row normalization 문제는 없다.

이 allowance는 실제 구적 오차를 흡수하지 않는다. 별도 q4/n16 저장 결과의 P_loss 최소값 −1.85653e−5는 allowance 3.89354e−11보다 훨씬 크므로 FAIL을 유지해야 한다. q8/n16 및 q8/n64의 저장 PASS도 확인했다. 이 두 실행의 field/해를 이번 task에서 다시 계산한 것은 아니다.

exp(+jωt)에서 다음 부호가 맞다.

\[
S_{src}=v^T\overline{s},\quad S_R=i^H(R+j\omega L)i,\quad
S_P=-j\omega q^HPq,\quad S_{load}=v^T\overline{Yv}.
\]

따라서 `Re(S_P)=ω qᴴ[(P−Pᴴ)/(2j)]q ≥ 0`이다. 현재 s는 실수라 코드의 `source @ v`가 올바르다. P의 reciprocity와 port dual은 **ordinary transpose**, 손실·전력은 **Hermitian** 연산을 쓴다. P를 Hermitian 평균으로 바꾸어 유전 손실을 제거하면 안 된다.

저장 direct/iterative 네 벡터 모두 원 방정식과 전력식을 확인했다. 최대 복소 전력 상대 defect는 4.79e−16, 최대 Hermitian dielectric-loss identity 차이는 5.63e−23 W이다. 양 변형 모두 conductor/dielectric/load 손실이 비음수이며 전체 전하 중성과 전극 KCL을 통과했다. 원 residual을 이용한 독립 identity도 확인했다:

\[
r_1=Zi-D^TPq+H^Tv,\quad r_2=Di+j\omega q,\quad r_3=-Hi+Yv-s,
\]
\[
S_{src}-S_R-S_P-S_{load}=-i^Hr_1-r_2^HPq-r_3^Hv.
\]

`gmres_info=20`은 내부 correction solve의 상태이다. 최종 저장 iterate는 원 시스템 backward/port/dual 검사를 통과했으므로 그 내부 코드를 숨기지 않고 최종 잔차를 함께 기록하면 된다.

| 동결 1 MHz 해 | complex Z (Ω) | direct–iterative 차이 |
|---|---|---:|
| point+self | 0.9871705921630641 + j0.14944039832568945 | 5.56e−16 Ω |
| cross05 candidate | 0.9871704741479559 + j0.09782022834546024 | 1.00e−15 Ω |

`|ΔZ|/|Zcandidate|=5.2036188235%`; base를 분모로 쓰면 5.1701970058%이다. 이는 두 연구 연산자 사이 변화이며 정확도 개선율이 아니다. R/D/H/P/Y는 bit-exact 동일했고 바뀐 L 항이 -05 하나임을 확인했다.

## 한 번의 최소 공간 판별

HQ의 **row0 최장 source edge v0–v1 중점 한 번 분할**을 채택한다. 원 삼각형의 정확한 M=(v0+v1)/2로 A=[v0,M,v2], B=[M,v1,v2], row75는 그대로 둔다. 원 도체 영역·두께·네 전극·매질·종단이 같고 shared75는 분할되지 않아 conforming이다. source wall0는 두 subfaces가 되지만 같은 기존 전극 전압을 유지한다. 이때 두 전류는 fine solve에서 독립이며, 1/2 배분은 coarse 전류를 fine 공간에 옮길 때에만 적용한다.

Return은 3 prisms / XY7+cap6=13 currents / volume3+freewall2+cap6=11 charges이다. 전체는 **271 currents / 175 charges / 4 electrodes = 450 unknowns**로, coarse보다 7개만 증가한다.

fine XY order `[0a,0b,75,81,new_M_v2,74,749]`와 coarse `[0,74,75,81,749]`에 대해

```
i_f = [i0/2, i0/2, i75, i81, (i81-i75)/2, i74, i749]
```

여기서 새 internal의 +방향은 A outward이다. row75의 old local signs `[+,-,-]`를 보존한다. A/B cap flux와 volume/cap q는 coarse row0의 각각 1/2로 prolong하고 row75 및 freewall 값은 그대로 둔다. 다음 전달 검사를 실행 가능한 최소 조건으로 둔다:

`D_f T=U D_c`, `H_f T=H_c`, `TᵀR_fT=R_c`, 각 자식에서 coarse/fine affine field의 점별 일치. 새 internal face에 q/H를 넣지 않는다. 저장 fine geometry와 신규 cap 자료의 일치·전달 검사 결과는 `refined-cap-binding.json`에 기록한다.

최종 fine NPZ SHA256 `41368069187b6283dd94fa0a4e7ead9d607c1be7cb0ad213598d15f5dd553240`에서 A/B 좌표와 z가 신규 cap 자료와 bit-exact 일치했다. D/H 전달 최대오차 0, R congruence 상대오차 5.40059e−15이다. 앞선 `20211717...` 초안은 row75 wall74 부호가 old→fine map과 달라 D 전달 오차 2, R 상대차 약 0.98로 거부했다. 구현 담당자가 old signs `[+,-,-]`와 freewall74 D=+1을 복구한 뒤 위 최종 저장물에서 해결을 확인했다. field 재적분으로 수정한 문제가 아니다.

다음 field 단계는 **fine physical candidate 하나**의 1 MHz Z이다. 기존 coarse -05 계수를 fine basis에 이식하지 않는다. row75의 unchanged self와 PWR/다른 unchanged-support 블록은 재사용한다. 새 row0 자식의 XY/cap self와 세 parent-pair mutual 및 변경 charge/far/deep 블록만 실제 fine basis로 계산한다. 양측 의미를 맞추기 위해 `TᵀL_fT`와 coarse physical L, `UᵀP_fU`와 coarse P의 차이가 기존 구적 지표에 비해 작은지도 확인한다. 이 비교는 새 block의 sign/normalization 문제를 공간 효과로 오인하지 않게 해준다.

최종 판별량은 `δspace=Zfine_candidate−Zcoarse_candidate`의 복소값·상대값, 그리고 이번 |ΔZ05|≈0.05162017 Ω와의 크기 비교이다. 격자 한 단계의 변화가 작아도 전체 공간 수렴 증명은 아니다. 크면 row0 coarse basis의 영향이 보정 효과와 경쟁한다는 근거이다. 절대값·실수/허수·구적 지표·실행시간을 따로 제시하고 임의 1% gate나 광대역 sweep을 새 선행 조건으로 만들지 않는다.

## 새로 요청된 cap self 산출물

`return-three-prism-cap-self/new-cap-self.npz`에는 A/B 순서의 `new_L_cap_self_blocks_h` 두 2×2와 `[A_bottom,A_top,B_bottom,B_top]` 순서의 `new_P_cap_halfspace_self_per_f` 네 값이 있다. 원 55..75 μm, 원 source ε/인터페이스, 기존 독립 z-depth/covariogram helper를 그대로 사용했다. 두 경우 모두 order128에서 최대 energy/charge 변화 2.831e−8 / 2.821e−8, 실행 0.0573 s이다. **deep와 cross는 포함하지 않았다.** row75 self는 기존 값을 복사했으며 재적분하지 않았다.

NPZ SHA256: `40d6a5f850af16540dd1ac72e946375c8ac61ed3b17c9b97360114a882fd86b9`. 좌표·순서·부모/helper/driver 해시와 구적 history는 같은 폴더 `result.json`에 있다. 실행한 독립 검사는 `check_frozen_port.py`, `compute_refined_cap_self.py`, `check_refined_cap_binding.py`이며 기존 보조 Astra/xhigh가 PSD·전력 부호를 별도로 검토했다. 전체 field/solve 및 공간 비교 결과의 통합 책임은 HQ이다.
