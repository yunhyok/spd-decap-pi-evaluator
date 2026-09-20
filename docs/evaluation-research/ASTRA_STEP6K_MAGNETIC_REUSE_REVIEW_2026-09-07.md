# ASTRA Step6K 자기장 재사용 검토 — 2026-09-07

## 범위

읽기 전용 코드 조사만 수행했다. geometry 재구성, LU, native compile, PowerSI fit, 제품 연결은 수행하지 않았다. 기준은 현재 저장된 L14+L25 네 주파수 결과/field와 다음 solver 계열이다.

## 재사용 가능한 부분

### (a) skin-only local constitutive

- `src/spd_decap_pi/_core/solver/tri_fem_sheet.py:1312-1362`의 `common_mode_copper_sheet_impedance`는 `sigma`, 두께, permeability만으로 finite-thickness copper의 내부 self/transfer를 계산하고 DC 및 skin-depth 극한을 처리한다. `:1170-1190`의 `TriFemSheetOperator.nodal_admittance_s`는 이 scalar admittance를 canonical triangular stiffness에 곱한다.
- 같은 파일 `:1740-1818`의 `compile_tri_fem_sheet`는 유효한 Shapely island를 CDT로 삼각화하고 `island.covers`와 area/symmetric-difference coverage를 검사한다. 따라서 source polygon의 holes를 포함한 **단일 sheet의 local conduction/skin**에는 가장 작은 재사용 후보가 된다. `identity`, contact coverage, convergence attestation은 별도 source evidence가 필요하다.
- `src/spd_decap_pi/_core/solver/mfdm.py:782-826`의 `copper_surface_impedance`/`copper_two_face_surface_impedance`와 `src/spd_decap_pi/_core/solver/surface_patch_plane.py:656-778`의 `assemble_differential_admittance`/`_strip_matrices`는 raster 또는 exact clipped strip마다 conductor two-face internal impedance를 사용한다. 이는 local skin/face-transfer와 dielectric gap stamp이며, external magnetic mutual-L은 아니다.
- `src/spd_decap_pi/_core/solver/via_peec.py:375-428`의 `solid_cylinder_internal_impedance`는 filled round-via의 local skin-only constitutive로 재사용할 수 있지만 L14/L25 sheet polygon의 constitutive가 아니다.

### (b) multiconductor external magnetic/mutual-L

- 실제 mutual-L 경로는 `src/spd_decap_pi/_core/solver/via_peec.py:333-372,463-526`의 `straight_wire_external_self_inductance`, `parallel_finite_wire_mutual_inductance`, `compile_via_peec`이다. `:528-665`의 `solve_via_peec`는 branch partial-L, internal impedance, group-current constraint를 결합하고 reciprocity/KCL/passivity를 검사한다.
- 이 경로는 모듈 상단 `via_peec.py:1-12`에 명시된 것처럼 straight vertical circular microvia만 다룬다. pads, antipads, plane spreading, bends, inferred return path는 입력 범위 밖이다. 또한 `ViaPeecOwnership.global_mna_composable` (`:83-88,232-235`)는 항상 false이고 global MNA stamp를 fail closed한다. 따라서 L14/L25 arbitrary-hole/nonmatched sheet geometry에 바로 쓸 수 있는 external/mutual-L 구현은 현재 없다.
- `src/spd_decap_pi/_core/solver/mfdm.py:836-982,1067-1158`는 cut-cell raster의 lateral loop와 `mu*gap*length` 성분, two-face local impedance를 조립한다. active conductor가 한 개뿐인 column(`:862`)과 non-contiguous layer column(`:1041`)을 거절하므로 source artwork를 이 경로에 넣으려면 별도 source-to-cut-cell/topology 증거가 필요하다. 이것을 sheet external mutual-L로 해석할 수 없다.
- `src/spd_decap_pi/_core/solver/surface_patch_plane.py:1-24,414-434,509-654`는 exact artwork fragment와 finite-port condensation을 제공하지만 `assemble_global_mna_admittance`가 absolute global-MNA를 명시적으로 거절한다. local differential null/gauge와 admissible return mode를 보존하는 future formulation이 필요하다.
- `src/spd_decap_pi/_core/solver/tri_fem_pair.py:278-389,635-817,859-923`는 matching common P1 mesh 한 쌍과 one-gap replacement를 요구한다. `tri_fem_stack.py:310-447,646-914`도 controlled matching mesh와 source/topology evidence를 요구하며 `production_eligible=False`다. 그러므로 두 L14/L25의 nonmatched source sheet를 직접 수용하는 경로가 아니다. 두 모듈의 two-face constitutive와 단순 gap inductance는 local 참고식일 뿐이다.

## 가장 작은 source-bound 식별 실험

새 geometry나 LU 없이 저장된 각 주파수 field에서 sheet별 local skin 민감도만 계산한다. 각 sheet `s in {L14,L25}`와 저장 field `v_f`에 대해 exact DC sheet operator `Gdc_s`를 사용해

`B_s,f = v_f.T @ Gdc_s @ v_f` (complex bilinear),
`H_s,f = v_f.conj().T @ Gdc_s @ v_f` (Hermitian/Joule)

를 계산한다. `H`의 실수부는 frozen `l14_sheet_dc`/`sheet_dc` Joule category와 대조한다. `tri_fem_sheet.common_mode_copper_sheet_impedance(f)`의 `Zcm`과 `Rdc = 1/(sigma*t)`로

`Zsheet(alpha) = Rdc + alpha*(Zcm-Rdc)` 및
`dZdd/dalpha|0 = (Zcm/Rdc - 1)*B_s,f`

를 기록한다. 네 주파수에서 이 값을 frozen `Zdd` 변화와 비교하면 local internal skin contribution의 크기만 식별할 수 있다. 이는 양면대칭 internal-only first-order diagnostic이며 finite-alpha 재해석, loop/return path, 전체 gap, external mutual-L의 계산이 아니다.

현재 field NPZ에는 `active_voltage`와 L14/L25 active-index map은 있지만 sparse `Gdc_s` 또는 `Gdc_s @ v_f`가 없다. 실험을 재현하려면 각 sheet의 exact sparse DC operator와 active-map hash, `sigma`, thickness, permeability/material hash를 추가로 저장해야 한다. 기존 saved category power는 `H` 대조용으로 사용할 수 있다.

## 판정과 비주장

P1은 없다. P2는 field artifact가 port map뿐 아니라 per-sheet `Gdc_s` action도 보존하지 않아 위 식별 실험을 field-only로 독립 재실행할 수 없다는 점이다. 이는 source geometry나 LU를 다시 읽어 해결하는 문제가 아니라 저장 계약의 누락이다.

`ΔIm(Z)/ω`를 missing-L로 확정하지 않는다. nearest-GND, inferred return, PowerSI fit, arbitrary-hole sheet의 external mutual-L, production/global-MNA adapter, C1/WP2/WP3 상태를 주장하지 않는다.
