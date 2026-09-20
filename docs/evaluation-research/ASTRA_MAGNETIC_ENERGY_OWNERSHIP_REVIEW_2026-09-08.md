# SPD Decap PI Evaluator v0.23.1 — magnetic-energy ownership review

## 판정

현재 코드는 유한 두께 도체 **내부** 응답, 인접 유전체 gap의 국소 자기장, L25 평면의 zero-thickness partial-L, 수직 via의 내부 임피던스와 외부 partial-L을 각각 계산할 수 있다. 그러나 이 항들을 한 전역 연산자로 합치는 검증된 에너지 분할은 없다. 따라서 판정은 `COMPLETED_CODE_SCOPE_AUDIT__STOP_UNPARTITIONED_MAGNETIC_COMPOSITION`이다. 특히 적층 gap의 `jωμd`와 같은 전류를 사용한 전역 FMM partial-L을 그대로 더하면 동일한 gap 자기장 에너지를 두 번 셀 수 있다.

## 소유권 표

| 항 | 코드가 소유하는 물리 | source geometry / current basis | 합성 판정과 중복 위험 | 코드 근거 |
|---|---|---|---|---|
| `coth/csch` copper two-face | 도체 두께 안의 저항, skin diffusion, 내부 자기 에너지. `K0`,`K1` 두 면 전류를 2×2 surface-impedance로 연결한다. | 각 도체의 `σ,t,μ`와 두 face current. `common_mode_copper_sheet_impedance=(Zself+Ztransfer)/2`는 `K0=K1=Ktotal/2`인 제한된 대칭 모드다. | 외부장 연산자와 물리 영역은 분리 가능하지만, 기존 DC sheet R 위에 더하지 말고 같은 도체의 constitutive를 한 번 교체해야 한다. 이 항 자체는 return·plane 간 외부 mutual을 소유하지 않는다. | `mfdm.py:735-825`, `tri_fem_sheet.py:1312-1343`, `tri_fem_stack.py:876-892` |
| adjacent-gap `jω μ_gap d` | 인접 두 도체 사이 유전체에서 생기는 국소 gap-loop 외부 자기 에너지. | 공통 lateral edge/mesh support, 인접 gap separation·permeability, gap-loop current. MFDM도 edge coverage factor와 같은 항을 쓴다. | 같은 plane/return 전류에 대한 free-space FMM은 이 gap의 장을 이미 포함한다. `L_gap + L_global`의 단순 합은 금지하고, 전역 외부장을 하나만 쓰거나 검증된 `L_gap + (L_exact-L_gap)` 분해가 필요하다. 비인접 aperture/fringing은 이 항이 닫지 않는다. | `tri_fem_stack.py:870-908`, `mfdm.py:1095-1148`; `mfdm.py:15-18`의 명시적 범위 |
| L25 RT0 centroid/self/shared-edge FMM | zero-thickness L25 surface current의 free-space geometric partial-L. Same-triangle self는 exact sparse block, off-triangle은 centroid FMM, shared-edge는 centroid 값을 빼고 directed quadrature와 transpose로 교체한다. | L25 한 층의 `(n,3,2)` triangle, z=0 centroid, 두 수평 RT0 current 성분. | 연산자 안에서는 FMM point-self를 쓰지 않고 exact self를 한 번 더하며 shared-edge correction도 replacement delta라 의도된 내부 중복은 없다. 다만 다른 near pair는 근사이고, interlayer·via·return cross block은 없다. 같은 전류의 stack gap-L을 더하면 외부장 중복 위험이 있다. | `apply_astra_l25_rt0_magnetic.py:69-160`, `assemble_astra_l25_rt0_self_magnetic.py:77-114,151-256`, `assemble_astra_l25_shared_edge_magnetic.py:37-115`, `run_astra_l25_fmm_magnetic_shadow.py:75-113,188-252` |
| via PEEC internal/external | `solid_cylinder_internal_impedance`가 via 금속 내부 R/skin/internal-L을, radius-regularized finite-wire self/mutual matrix가 외부 partial-L을 맡는다. 내부 저주파 `μ0l/(8π)`와 외부 self를 코드 안에서 한 번씩 합친다. | source x/y/z span, radius, conductivity와 signed vertical branch currents. | 격리 via block 내부의 internal/external 분할은 명시적이다. 그러나 plate spreading/return core가 없으므로 layer operator에 직접 더할 수 없다. Plane-plane과 plane-via cross block은 계산하거나 채택한 basis/medium에서 exact zero임을 증명해야 한다. | `via_peec.py:333-428,463-555`와 ownership guard `via_peec.py:40-100` |
| native via diagonal R/L | source span·drill에서 얻은 DC R와 `0.2·l_mm·(log(4l/d)+1)` 형태의 경험적 단일 L이다. 내부/외부/return 소유권을 분리하지 않는다. | 각 source via segment, layer span, drill, conductor classification. | PEEC internal/external 또는 전역 via magnetic term을 도입할 때 이 L을 유지한 채 더할 수 없다. 정확히 같은 branch owner를 제거·교체하고 zero-mutual recollapse를 확인해야 한다. | `via_model.py:132-161`; 실제 L25 board binding/replay `run_astra_l25_rt0_board_shadow.py:193-209` |
| `global_mna` ownership boundary | 물리 kernel이 아니라 named nodal/series blocks를 결합하는 MNA와 owner-ID 중복 검사다. | source node/branch incidence와 block-provided impedance/admittance. | 같은 owner ID의 이중 stamp는 막지만 서로 다른 ID로 표시된 동일 field-energy 영역은 판별하지 못한다. Built-in filled-via adapter는 exact/core matrix와 검증된 subtraction이 없어 항상 실패한다. 현재 두 solid-via 호출 경로도 adapter에서 멈추며, 성공한 via-PEEC global composition은 없다. | `global_mna.py:432-604,702-805,855-997`; callers `research_full_multinet_hybrid.py:175-212`, `research_endpoint_via_reducer.py:368-426` |

`tri_fem_stack`의 incidence를 `B`라 하면 `B.T @ 1 = 0`이다. 따라서 임의의 scalar `ℓ`에 대해 `B.T @ (Zlayer + jωℓ·11.T) @ B = B.T @ Zlayer @ B`이고, `B.T @ Zlayer @ B = Zgap` 검사는 common/exterior magnetic mode를 결정하지 못한다. 코드가 `common`과 correction으로 하나의 layer-space lift를 고정하더라도, 그 선택을 complete-board exterior reference로 승격할 수는 없다.

공통 reference의 자기 소유권은 채택한 전류 표현에 따라 한 가지로 닫아야 한다. Surface-current 표현을 채택한다면, 각 conductor 내부 two-face/cylinder 항을 정확히 한 번 두고 모든 plane·via의 실제 3D surface-current basis에 대한 외부 자기 에너지를 하나의 연산자가 소유하게 하는 것이 한 선택지다. 이때 국소 gap core를 유지하려면 동일 geometry/current projection의 `exact-core` 차감 연산자와 stable hash가 필요하다. 반대로 conductor volume current를 분해해 Green operator로 직접 푸는 volume-PEEC/VIE reference는 도체 내부와 외부 자기장을 함께 소유하므로, 재료 항은 Ohmic constitutive로 두고 기존 two-face/cylinder internal impedance를 다시 더하지 않아야 한다. General surface PEEC를 local two-face slab과 동일시하거나 저주파 검증 없이 1 kHz reference로 선택할 수도 없다. 어느 표현이든 native heuristic L은 대응 owner에서 교체하고, plane-plane·plane-via·via-via mutual 및 return/gauge closure를 계산하거나 adopted basis에서 exact zero임을 증명한 뒤 1 kHz–100 MHz에서 reference 정확도를 먼저 검증해야 한다. 균질·등방 permeability의 Neumann vector-potential kernel에서는 엄밀히 수평인 sheet `J`와 엄밀히 수직인 wire `J`의 dot product가 0이므로 그 **직접** plane-via partial-L block은 exact zero다. 이는 junction spreading, 기울어진 current component, 유한 접속부 또는 다른 medium/interface 항까지 0이라는 뜻이 아니다. 이후에만 plane별 ideal/R-only나 영역 축소를 ablation으로 판단할 수 있다.

## 고정한 코드

- `mfdm.py` `b7b140686babc17e7a31097490ac009685b15cf12782d0a71498aeceb5290a80`
- `tri_fem_stack.py` `fedfac5b515a1df7e913594a26b265d733415bcbf6342939b2b039970c59c8b3`
- `tri_fem_sheet.py` `16eb6fd122fb5cf8d7ab60bb4b0364b760cb354806c02651d2847ede633640e0`
- `via_peec.py` `61003c9a87911edebc1c1132aebd041f63bc54d5bf480f9c0a037fb36619b9e4`
- `via_model.py` `d9b1acb96e2ff5137f502fdf0d24a6e9ba2e16e161e67a84a0b40b526b2b4043`
- `global_mna.py` `e4f946fc127fdb76bac3ec70e391b86168b3387e5373c721d4c90a0beed549ac`
- L25 operator/self/shared-edge/runner `2c880f428b18f40ee9fabca98154be9f7c7c6f6b91a6dfc74a2bb09810a8daa2` / `ec964ed1b08dd37a86f5dd4a8c2a36efb30dd2310c8bc454bce588e9c8326c33` / `b6f1c0a6b7a2414112aa47181f62a8138d94c0c8c7f2ab8edafbe6b17b01ad87` / `e88c3edc31b023cfcb8fd7046713bf8268824d3ecce0cad8805cb88366b25554`

이 검토는 현재 source code의 물리 소유권과 호출 가능성만 판정했다. 새 source 추출, geometry, mesh, LU/FMM action, 주파수 응답 또는 PowerSI 비교는 실행하지 않았고, 현재 mixed operator를 전역 reference로 승인하지 않는다.
