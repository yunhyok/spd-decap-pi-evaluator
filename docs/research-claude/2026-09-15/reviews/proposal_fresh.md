# .spd 기반 PDN Z(f) 계산기 재설계 제안 (독립 리뷰, fresh perspective)

작성 기준일: 2026-09-15. 표기 규칙: **[문서]** = 인용 출처에 명시된 사실, **[추론]** = 필자의 공학적 추론, **[미검증]** = 출처 확인에 실패한 주장. 기존 코드는 읽지 않았음.

---

## 0. 핵심 결론 (요약)

1. 1 kHz–100 MHz 대역의 PDN self-impedance는 **2-D 평면쌍(plane-pair) 모델 + 회로(via/pad/decap) 모델**로 푸는 것이 업계 표준 접근이며, PowerSI 자신도 "hybrid"(평면 EM solver + 회로 solver + 전송선 solver) 구조다 [문서, §1]. 380만 미지수 3-D 체적 적분방정식은 이 대역에서 물리적으로 과잉이며 폐기를 권고한다.
2. 보고된 증상(1 MHz에서 |Z| 오차 0.04 dB, 위상 15°, 복소오차 26 %)은 **순수 위상 오차**다: |Z|가 같고 위상만 Δφ 다르면 복소 상대오차 = 2·sin(Δφ/2) = 2·sin 7.5° = **0.261**. 즉 26 %는 전부 위상에서 온다 [추론, 산술]. 목표(≲10–20 %)는 위상 오차 **≲5.7°–11.5°**와 동치이므로, 앞으로의 검증 지표는 |Z|보다 **Re Z / Im Z 분리 오차**로 잡아야 한다.
3. 권고 아키텍처: (1) DC 저항망 → (2) 준정적 R+L 행렬 + 평면 C (포트/디캡 사이트를 포트로 하는 축약 Z 행렬) → (3) M-FDM/M-FEM 주파수 해석 또는 MOR로 공진 대역 → (4) 디캡을 대각 부하로 붙이는 what-if 루프(수 초).

---

## 1. PowerSI는 실제로 무엇을 하는가

### 1.1 공개 문서로 확인된 사실
- **Hybrid solver 3요소** [문서]: Cadence Community의 Sigrity 포럼 답변에 따르면 PowerSI hybrid solver는 (a) *Plane/EM solver* — "coupling between vias, reflection from edges, resonances, power and ground voltage fluctuations, and metal/dielectric losses", (b) *Circuit solver* — 선형/비선형 회로(IBIS, HSPICE), (c) *Transmission line solver* — trace 결합, skin-effect, 유전손실. 또한 "closed-form SPICE models to represent vias, pads", 그리고 평면·와이어본드 같은 핵심 구조에는 "finite element and boundary element field solvers"를 쓴다고 서술. 출처: https://community.cadence.com/cadence_technology_forums/system-analysis/f/sigrity/57376/what-are-the-components-of-a-hybrid-solver-in-powersi
- **Cadence 엔지니어 논문의 기술** [문서]: S. Farrahi, E. Koether, M. Mechaik, I. Novak, "Effect of Power Plane Inductance on Power Delivery Networks," DesignCon 2019. PowerSI를 참고문헌 [2]로 들며 "hybrid field solver … uses combinations of numerical methods and approximations to solve for electromagnetic fields and extract S-parameters for power and ground nets"라 기술하고, 이 solver는 PMC 벽이나 수평장만을 가정하지 않는다고 명시(Fan 그룹의 modal cavity법과 대비). 2'×2' 패키지에서 측정·3-D FEM·타사 툴과 50 MHz–20 GHz 일치, 대형 마더보드 loop inductance는 1 MHz 이상에서 "within 20 %" 측정 상관. http://www.electrical-integrity.com/Paper_download_files/DC19_PAPER_Track11_EffectPowerPlaneInductancePDNs_Farrahi_.pdf
- **"accuracy down to DC (patent pending)"**, PowerDC는 별도 DC signoff(FEM, via·ball 개별 모델) [문서]: Cadence Sigrity overview (CERN Indico, 2024) https://indico.cern.ch/event/1381495/sessions/555571/attachments/2954929/5195445/Sigrity_short_overview_Oct2024.pdf
- **Sigrity X 백서** [문서]: PowerSI/XtractIM은 "hybrid solver", SPEEDEM은 FDTD, Clarity는 full-wave 3-D FEM. PowerSI 포트는 net 기반 자동 생성, PI 포트 기준임피던스를 낮게(예 1 Ω) 설정하는 것이 일반적. https://cdn.goengineer.com/cadence-sigrity-x-redefining-signal-whitepaper-goengineer.pdf
- **포트/디캡 설정 실무** [문서]: SDSU PowerSI 튜토리얼 — Setup>Port에서 Ref Z = 1 Ω, positive hook은 전원 via, negative hook은 GND via; 디캡은 C + self-resonance 모델로 정의. https://electrical.sdsu.edu/_resources/files/cadence_powersi.pdf . 포트 기준임피던스 설정: https://community.cadence.com/cadence_technology_forums/system-analysis/f/sigrity/57928/sigrity-tip-of-the-week-how-to-define-port-impedance-in-powersi-and-clarity-3d-layout
- **저주파 외삽 이슈** [문서, 단 사용자 질문일 뿐 공식 답변 없음]: 포럼 사용자가 "solver extrapolates data at low frequencies (below 100 kHz down to 0 Hz)"라고 보고. https://community.cadence.com/cadence_technology_forums/system-analysis/f/sigrity/66324/how-to-include-exact-0-hz-dc-point-in-powersi-to-avoid-low-frequency-extrapolation
- **기원** [문서]: Sigrity 창업자 J. Fang 그룹의 초기 작업 — Y. Chen, Z. Chen, J. Fang, "Optimum placement of decoupling capacitors on packages and printed circuit boards under the guidance of electromagnetic field simulation," Proc. IEEE ECTC 1996 (IEEE Xplore doc. 550492). https://ieeexplore.ieee.org/document/550492/ — 이 논문의 수치 기법(FDTD 여부)과 페이지는 **[미검증]**.

### 1.2 공개되지 않은 부분 (추론)
- 평면 solver의 정확한 이산화(FEM/BEM/FD 중 무엇, 메시 기준), 다중 pin 포트의 pin 결합 방식(이상 short vs 분배), via/pad closed-form 수식, skin-effect 적용 방식, 주파수 스윕 알고리즘(adaptive 여부)은 **공개 문서에서 확인 불가** [미검증]. Cadence "PowerSI Extraction Best Practices" app note(https://www.cadence.com/content/dam/cadence-www/global/en_US/documents/tools/ic-package-design-analysis/sigrity-resources/sigrity-powersi-extraction-best-practices-an.pdf)는 접근 차단되어 내용 확인 실패 — 팀이 Cadence 지원 계정으로 반드시 확보할 것.
- [추론] "평면쌍 2-D EM + via 회로모델" 구조라는 점에서 PowerSI는 §2의 M-FDM/M-FEM 계열과 **같은 물리 등급**이다. 따라서 2-D 모델이 PowerSI와 어긋날 때 원인은 대개 *물리의 누락*이 아니라 *모델링 관례 차이*(포트 pin 결합, via/pad 인덕턴스 수식, 디캡 모델 해석, 손실)다.

---

## 2. 경량 PDN Z(f) 기법 비교

| 기법 | 포착 물리 | 보드 규모 미지수 | 1 kHz–100 MHz 정확도(3-D 대비) | 실패 모드 |
|---|---|---|---|---|
| Cavity resonator (모드 합) | 직사각 평면쌍 TM 모드, PMC 가장자리 | 포트 수 N (모드 합) | 직사각·무보이드면 우수 | 불규칙 형상·보이드, 모드 합 수렴 느림 |
| Segmentation (Okoshi) | cavity 블록 연결 | 경계 포트 수백 | 형상 분할 가능 시 양호 | 복잡 보이드/anti-pad 필드 |
| TMM | RLCG 단위셀 격자, via L | 셀 수 (예 37×30) | 양호 | 셀 크기 한계, 불규칙 형상 비효율 |
| M-FDM / M-FEM | 2-D Helmholtz, 층간 aperture wrap-around 결합, via L | 10³–10⁶ | full-wave와 "comparable" | 미세형상 시 메시 폭증(FDM), 포트 보정 필요 |
| Via-plane physics-based (MS&T) | 준정적 L 행렬 + 평면 C + DC R | 포트/via 수 | <8 %(CST 대비) | 첫 공진 이상, PMC 가정 |
| PEEC | 부분 RLC | 셀 수, 밀집 | 우수(고비용) | 밀집행렬 |

### 2.1 Cavity model
G. T. Lei, R. W. Techentin, B. K. Gilbert, "High-frequency characterization of power/ground-plane structures," *IEEE Trans. MTT*, 47(5):562–569, 1999, doi:10.1109/22.763156 (https://mayoclinic.elsevierpure.com/en/publications/high-frequency-characterization-of-powerground-plane-structures/) — "full cavity-mode frequency-domain resonator model" [문서].
J. Kim, L. Ren, J. Fan, "Physics-based inductance extraction for via arrays in parallel planes for power distribution network design," *IEEE Trans. MTT*, 58(9):2434–2447, 2010, doi:10.1109/TMTT.2010.2058278 (https://scholarsmine.mst.edu/cgi/viewcontent.cgi?article=7273&context=ele_comeng_facwork): (0,0) 모드는 평면 C, 나머지 모드는 "evanescent modes that contribute only to the inductance"; 인덕턴스 항은 "nearly constant … below approximately 60% of the first cavity resonance"; 상호인덕턴스가 **음수**가 될 수 있음; 가정: via 간 다중산란 무시(간격/높이 비 >1.6), PMC 가장자리 [문서].

### 2.2 Segmentation
T. Okoshi, *Planar Circuits for Microwaves and Lightwaves*, Springer, 1985, Ch.5 "Segmentation Method" (https://link.springer.com/chapter/10.1007/978-3-642-70083-5_5). PDN 응용 예: J. Kim et al., "Hybrid Analytical Modeling Method for Split Power Bus in Multilayered Package," *IEEE Trans. Adv. Packag.*, 2006 (https://ieeexplore.ieee.org/document/1614042/) [문서, 세부 권·쪽 미검증].

### 2.3 TMM
J.-H. Kim, M. Swaminathan, "Modeling of multilayered power distribution planes using transmission matrix method," *IEEE Trans. Adv. Packag.*, 25(2):189–199, 2002 (http://emlab.uiuc.edu/ece546/appnotes/pdn_paper.pdf). 평면을 RLCG 단위셀로, via는 자기·상호 인덕터로 모델; "a unit cell size that is ten times less than the wavelength at the highest frequency of interest is required"; 예제 7.62 mm 셀, 37×30 셀; 셀 R에 DC와 skin-effect AC 성분 포함 [문서].

### 2.4 M-FDM / M-FEM
A. E. Engin, K. Bharath, M. Swaminathan, "Multilayered finite-difference method (MFDM) for modeling of package and printed circuit board planes," *IEEE Trans. EMC*, 49(2):441–447, 2007, doi:10.1109/TEMC.2007.893331 (https://pure.psu.edu/en/publications/multilayered-finite-difference-method-mfdm-for-modeling-of-packag/). 특허 US7895540 (https://patents.google.com/patent/US7895540): (∇T²+k²)u = −jωμd·Jz; 단위셀 **L = μd**(셀 크기 무관, 평면쌍 "per-square" 인덕턴스), **C = εh²/d**, **R = 2/(σt) + 2√(jωμ/σ)**, G = ωC·tanδ; indefinite admittance로 층 적층; aperture 가장자리 wrap-around 전류로 수직 결합 [문서]. 특허에 **정량적 셀 크기 기준은 없음** [문서: 요약상 부재].
K. Bharath, J. Y. Choi, M. Swaminathan, "Use of the Finite Element Method for the Modeling of Multi-Layered Power/Ground Planes with Small Features," Proc. 59th ECTC, 2009 (p.1634 포함; 전체 쪽수 미검증) (https://epsilon.ece.gatech.edu/publications/2009/jaeyoung_ectc.pdf): 적응 삼각 메시 MFEM **1,439 미지수 0.25 s/주파수** vs 균일 MFDM **250,000 미지수 10 s/주파수**(Core2 Duo, 2 GB); 상용 2.5-D FEM 툴과 "good correlation" [문서]. → 2코어 프로토타입에 직접적 근거.
Swaminathan & Engin, *Power Integrity Modeling and Design for Semiconductors and Systems*, Prentice Hall, 2007 [문서, 위 논문 참고문헌].
포트 보정: K.-B. Wu et al., "Delaunay-Voronoi modeling of power-ground planes with source port correction," *IEEE Trans. Adv. Packag.*, 31(2):303–310, 2008 [문서].
Hybrid FEM-SPICE: C. Guo, T. H. Hubing, *IEEE Trans. Adv. Packag.*, 29(3):441–447, 2006 (https://ieeexplore.ieee.org/document/1667862).

### 2.5 MS&T physics-based via-plane / 경계적분
- L. Zhang, PhD diss., "PDN Modeling for High-Speed Multilayer PCB Boards and Decap Optimization Using Machine Learning Techniques," Missouri S&T, 2021 (https://scholarsmine.mst.edu/cgi/viewcontent.cgi?article=3994&context=doctoral_dissertations): **BEM(경계만 1-D 이산화)으로 준정적 via 간 L**, **Contour Integral Method로 DC R**(1.59 mΩ vs CST 1.55 mΩ), 노드전압법 L-C 회로; HFSS 대비 10 kHz–20 MHz "perfect agreement", **<1 s vs >10 min**; 디캡(C/ESL/ESR) Z 파라미터를 보드 Z에 결합; 한계: 공진 대역 정확도 저하, PMC 가정 [문서].
- B. Zhao, S. Liang, …, J. Fan, J. Drewniak, "Decoupling Capacitor Power Ground Via Layout Analysis for Multi-layered PCB PDNs," *IEEE EMC Magazine*, 9(3):84–94, 2020, doi:10.1109/MEMC.2020.9241560 (https://scholarsmine.mst.edu/cgi/viewcontent.cgi?article=8004&context=ele_comeng_facwork): L를 L_above / L_PCB_Decap / L_PCB_Plane / L_PCB_IC 블록으로 분해, CST 대비 "<8 %", 유효 대역 "a few hundred kilohertz to one- or two-hundred megahertz" [문서].

### 2.6 PEEC
C. Li et al., "PEEC-Based On-chip PDN Impedance Modeling Using Layered Green's Function," IEEE EMC+SIPI 2022, doi:10.1109/EMCSI39492.2022.9889515 (https://par.nsf.gov/servlets/purl/10393364) — 온칩 대상, 오차 <12 %, 1.4 GB/0.05 CPU-h vs HFSS 74 GB/1.3 h [문서]. 보드 규모 평면에는 밀집행렬 부담으로 비권장 [추론].

### 2.7 상용·오픈소스
- **Ansys SIwave**: "fast 2.5D solver", HFSS 3-D FEM 영역 삽입 hybrid (https://www.mwrf.com/technologies/embedded/software/article/21849451/ansys-advanced-simulation-feature-combines-best-of-both-worlds); PI 시뮬레이션에서 coupling 옵션(cavity field 등) 단계 선택 (https://ansyshelp.ansys.com/public/Views/Secured/Electronics/v252/en/Subsystems/HFSS3DLayout/Content/3DLayout/SelectingSIwaveSolutionSetupOptions.htm) [문서].
- **Keysight PIPro**: "EM-based", AC PDN impedance·DC IR·plane resonance 3 엔진; 이산화 방식은 공개 자료에서 **[미검증]** (https://www.microwavejournal.com/articles/26069-redefining-signal-and-power-integrity-analysis-with-ads-sipro-and-pipro-solutions).
- **Altium PDN Analyzer (by CST)**: **DC(IR drop) 전용** — AC Z(f) 아님 (https://www.altium.com/documentation/altium-designer/analyzing-pcb/pi-analysis/pdn-analyzer-cst) [문서].
- **오픈소스**: GT-CHIPS/PDN-Impedance-Analysis(MATLAB, T-matrix 캐스케이드, 파라미터화 구조 전용, 레이아웃 입력 없음, 라이선스 명시 없음) https://github.com/GT-CHIPS/PDN-Impedance-Analysis [문서]. 요청에 언급된 "OpenPDN", "pdn-solver", Ozen 오픈소스 PDN solver는 **검색으로 존재 확인 실패** — 인용하지 않음. .spd를 읽는 공개 AC PDN solver는 찾지 못함.

---

## 3. 1 kHz–100 MHz 대역의 물리와 필요 충실도

**기본 수치** [추론, 표준식]:
- Skin depth δ = 1/√(πfμσ): Cu에서 1 MHz ≈ 66 µm, 100 MHz ≈ 7 µm(Bharath 2009 논문도 100 MHz 7 µm 명시 [문서]). 35 µm 평면은 평면쌍에서 전류가 마주보는 면에 몰리므로 δ ≲ t, 즉 약 **3–5 MHz 이상**부터 R_ac가 √f로 증가. 박막 평면쌍 손실은 I. Novak, "Lossy power distribution networks with thin dielectric layers and/or thin conductive layers," *IEEE Trans. Adv. Packag.*, 23(3), 2000 (https://ieeexplore.ieee.org/document/861547/) [권·호 미검증]. M-FDM 셀 R식의 2√(jωμ/σ) 항이 이것 [문서].
- 평면쌍 확산(spreading) 인덕턴스: 정사각형당 μ0·h (h=100 µm → 약 126 pH/□) — M-FDM의 L=μd [문서]. 평면 C: FR-4 0.25 mm에서 약 16 pF/cm² (Hubing, LearnEMC https://learnemc.com/decoupling-for-boards-with-closely-spaces-power-planes) [문서].
- 첫 cavity 공진 f₁₀ = c/(2a√εr): a=300 mm, εr≈4 → ≈250 MHz. FR-4 파장 100 MHz ≈ 1.5 m [추론]. 따라서 **무부하 보드는 100 MHz까지 대부분 첫 공진 아래**이며, 이 대역의 공진/반공진은 주로 **디캡 ESL·마운트 L·확산 L과 평면/디캡 C의 LC 공진**이다. 패키지는 더 작으므로 1 GHz까지 준정적 영역이 넓다.

**데케이드별 요구 충실도** [추론, 근거 병기]:
1. **1–100 kHz**: Z ≈ R_DC(평면+via+pad) ∥ Σ(ESR_i + 1/jωC_i). Σ C가 수백 µF면 |X_C|는 1 kHz에서 수백 mΩ, 10 kHz에서 수십 mΩ → **R 망과 디캡 ESR(주파수 의존 포함), 벌크캡/VRM 모델**이 지배. 2-D 저항망(CIM/FEM) 필요(Zhang 2021).
2. **100 kHz–10 MHz**: 디캡 뱅크 직렬공진 부근. Z ≈ R + jωL_eff + 1/(jωC). L_eff = 디캡 ESL + L_above(pad/trace/via) + via 배럴 L(cavity 관통) + 평면 확산 L. **위상이 가장 민감한 대역** — 공진 근처에서 X≈0이므로 수 % L 오차가 큰 위상 오차로 증폭. 준정적 L 행렬(Kim-Ren-Fan 2010; Zhao 2020 "<8 %")로 충분.
3. **10–100 MHz**: 평면 C와 L_eff의 반공진, 고주파 디캡의 영향, 평면쌍 분포 효과 시작, skin effect. 대형 보드·두꺼운 코어는 첫 모드 근접. M-FDM/M-FEM 주파수 해석 필요(Engin 2007; Bharath 2009).

**2-D 모델이 PowerSI와 >10 % 어긋날 곳** [추론, 근거 병기]:
- (a) **L_above**(디캡 pad–trace–via 루프, 최상층 평면 위): 평면쌍 모델 밖의 3-D 부분인덕턴스. PowerSI는 "closed-form SPICE models … vias, pads"를 씀 [문서]. 수식이 달라 수십 pH 차이 → 디캡 1개 ESL(수백 pH)의 10 % 이상.
- (b) **포트 영역**: 이산화 셀이 via 반경보다 크면 확산 L을 과소평가 — 누락 L ≈ (μ0h/2π)·ln(r_eq/r_via), r_eq ≈ 0.2·Δx(5점 FD 격자에 대한 Peaceman 등가반경; D. W. Peaceman, *SPE J.*, 18(3):183–194, 1978, https://onepetro.org/spejournal/article-pdf/18/03/183/2158027/spe-6893-pa.pdf). 전자기 PDN에의 적용은 [추론]; Wu et al. 2008 port correction이 같은 문제를 다룸 [문서]. 이것은 DC 확산저항에도 동일 로그항으로 나타난다.
- (c) **BGA/pin field anti-pad 천공**: 평면 유효 L/R 증가(Farrahi 2019가 anti-pad pitch 영향을 분석 [문서]).
- (d) **Kim-Fan 모델 가정 위반**: via 밀집(간격/높이 <1.6)에서 다중산란, PMC 가장자리 [문서].
- (e) 분할면 간 gap/fringing 결합, 좁은 neck [문서: Bharath 2009는 slot 0.2 mm ≫ 1 mil 유전체일 때 gap 결합 무시 가능하다고 명시].
- (f) 50–100 MHz 이상 평면 모드 근접, skin effect/유전손실 누락.

---

## 4. 권고 아키텍처 (단계별, 검증 게이트 포함)

**공통 원칙**
- 포트 정의: Sigrity 포트 = positive pin group(전원 via/ball/pad) + negative pin group(GND). [추론 권고] 각 group 내 pin을 **이상 도체로 short한 슈퍼노드**로 두고 포트 전류는 슈퍼노드 간 1 A 주입; 대안으로 pin별 포트 후 Z 행렬에서 "등전위 조건"으로 축약. 두 방식 모두 구현해 Touchstone과 비교해 PowerSI 관례를 역추정(§5 E5). PowerSI 실제 관례는 **[미검증]**.
- Touchstone → Z: 반드시 **전체 행렬** Z = Z0^{1/2}(I+S)(I−S)^{-1}Z0^{1/2} (Z0 = 헤더/포트별 기준임피던스, 예 1 Ω). Z_ii는 "다른 포트 open" 조건 [추론, 표준 네트워크 이론]. S_ii 단독 변환은 다른 91개 포트가 Z0로 종단된 값이다.
- 디캡: .spd의 SPICE/RLC 모델을 그대로 주파수별 Z_dec(f)로 평가(주파수 의존 ESR 보존), 장착 L은 별도 항으로 분리.

**Step 1 — DC/저주파 (게이트: 1–100 kHz)**
- 모델: 층별 2-D 저항망(CIM 또는 삼각 FEM, via/pin 주변 국소 세분화) + via 배럴 R + pad R + 디캡 ESR/C.
- 규모 [추론]: 레일 관련 층 수 L≈4–8, 층당 적응 메시 2만–10만 노드 → 총 10⁵–5×10⁵ 실수 SPD 행렬; CHOLMOD 한 번 인수분해 ≈ 수 초, 포트+디캡 사이트 ~513 RHS 후진대입 수 초–수십 초(2코어, <2 GB).
- 게이트: 1–10 kHz에서 Re Z, Im Z 각각 ±10 %; DC 극한 R 망을 PowerDC 결과가 있으면 대조(없으면 생략). 참조 데이터 100 kHz 이하가 외삽일 가능성(§1.1 포럼) 유의.

**Step 2 — 준정적 R+L+C (게이트: ~10 MHz)**
- 핵심 관찰 [추론]: 단일 평면쌍에서 셀 직렬임피던스 z(ω) = R_□ + jωμ0h (+skin 항)이고 R 망과 L 망이 **같은 그래프 라플라시안**을 공유(M-FDM 단위셀 식). 따라서 Step 1의 인수분해를 재사용해 포트/사이트 간 **R 행렬과 L 행렬을 동시에** 얻을 수 있음(두 평면 형상이 다르면 근사 — 이 경우 M-FDM의 2-레이어 indefinite admittance로 분리).
- via 배럴의 cavity별 L·상호 L: Kim-Ren-Fan 2010 식 또는 BEM(Zhang 2021). 평면 C는 cavity당 (0,0) 모드 = εA/d.
- 결과: ~513포트 축약 Z_board(ω) = R + jωL + 1/(jωC) 형태 [Kim 2010의 모드 분리를 근거로 한 추론]. 주파수당 비용은 513×513 소행렬 연산뿐.
- 게이트: 100 kHz–10 MHz Re/Im 각각 ±10 %, 직렬공진 주파수 ±5 %, 포트쌍 루프 L (§5 E5) ±10 %.

**Step 3 — 분포/공진 (게이트: 100 MHz, 1 GHz 보조)**
- M-FDM(균일) 또는 M-FEM(삼각) Helmholtz, 층간 via 결합; 복소 대칭 희소 LU(MUMPS/PARDISO/UMFPACK).
- 메시 방어 논리: TMM의 λ/10 규칙(Kim & Swaminathan 2002)은 100 MHz에서 15 cm라 **구속조건이 아님**; 실제 구속은 형상(anti-pad, neck, via 간격). M-FDM 논문군에 정량적 수렴 기준이 없으므로(특허 요약 기준) **h-수렴 시험을 게이트로 포함**: 최소 형상 크기의 1/2–1/3 국소 세분화, 전역 2–5 mm, 메시 절반 시 ΔZ<2 %. 포트 via에는 Peaceman/Wu 보정 적용.
- 규모 [추론]: Bharath 2009 수치(250k 미지수 10 s/주파수, 2009년 2코어)로 보아 10⁵–3×10⁵ 미지수에서 주파수당 수–수십 초. 주파수 100점 전부 직접 풀면 수십 분 → Step 4 필수.
- 게이트: 10–100 MHz 반공진 주파수 ±5 %, 피크 크기 ±20 %, 복소오차 중앙값 ≤15 %.

**Step 4 — MOR / what-if**
- 보드(디캡 없음)를 포트+후보 사이트 포트로 축약: PRIMA (A. Odabasioglu, M. Celik, L. T. Pileggi, *IEEE Trans. CAD*, 17(8):645–654, 1998, https://ieeexplore.ieee.org/document/712097/) 로 수동성 보존 Krylov 축약 — 단일/소수 확장점 인수분해 재사용 [문서: 알고리즘; 적용은 추론]. skin-effect의 √f 항은 저주파(Step 2)와 고주파 확장점을 분할하거나 vector fitting(B. Gustavsen, A. Semlyen, *IEEE Trans. Power Delivery*, 14(3), 1999 **[이번 검색에서 미검증]**)으로 처리.
- what-if: Z_tot = Z_pp − Z_ps(Z_ss + Z_dec,diag)^{-1}Z_sp (디캡 = 사이트 포트의 대각 부하) — 사이트 513개면 주파수당 ~10⁸ flop ≈ 수십 ms, 100점 **수 초** [추론]. 이동은 후보 사이트를 사전 포트화해야 가능. Zhang 2021의 "디캡 Z를 보드 Z에 결합" 방식과 동일 [문서].
- 게이트: 참조 보드에서 디캡 제거 시나리오를 PowerSI로 1회 추가 생성해(가능하면) 델타 검증.

---

## 5. "|Z| 일치, 위상 15° 오차 @1 MHz" 진단

**증상 해석** [추론]: |Z| 동일·위상 15° ⇒ Re Z와 Im Z가 서로 반대 방향으로 틀림(예: 참조 45°→모델 60°면 R −29 %, X +22 %). "크기만 맞는" 우연 상쇄이므로 L 또는 R 단독 오류보다 **공진 근처 위상 민감도** 또는 **R·L 동시 오차**를 의심.

**원인 후보**
1. **공진 주파수 이동**: 1 MHz가 뱅크 직렬공진 근처면 L_eff/C_eff 수 % 오차로도 위상 수십°, |Z|≈R은 유지.
2. **직렬 R 누락/과다**: 디캡 ESR 해석(상수 vs 주파수 의존; MLCC 저주파 ESR ≈ DF/(ωC)), 평면 확산저항(§3 b 로그항), via/pad DC R, 참조면(GND) 저항 누락.
3. **L_above/마운트 L 수식 차이**(§3 a).
4. **S→Z 변환 오류**: S_ii 단독 변환(다른 포트 Z0 종단 → 저 Z0일수록 병렬 저항 부하가 붙어 위상 변화), Z0 오지정, Touchstone 형식(MA/DB/RI, 각도 단위), 포트 순서.
5. **참조 데이터에 디캡 포함 여부**: PowerSI extraction 시 회로모델 포함/제외.
6. **포트 pin group 결합 관례**, **de-embedding**, VRM/벌크캡 모델 포함 여부.
7. 참조의 저주파 외삽/보간 아티팩트 [미검증, §1.1].

**Touchstone만으로 하는 최소 실험** [추론, 표준 회로 해석]
- **E0 (5분)**: 전체 행렬 Z 변환; Re Z_ii ≥ 0, Z_ij = Z_ji 확인. S_ii 단독 변환과 비교 — 차이가 1 MHz에서 수 °면 원인 4.
- **E1 (1–10 kHz 피팅)**: Z_ii ≈ R₀ + 1/(jωC₀) 최소제승. C₀ ≈ Σ C(레일 디캡) → 디캡 포함(원인 5 판별); C₀ ≈ 평면 C(nF)면 미포함. R₀를 모델의 ESR∥+R_DC와 비교.
- **E2 (직렬공진)**: Im Z_ii = 0인 f₀와 |Z(f₀)| = R(f₀). L_eff = 1/((2πf₀)²C₀). 모델 f₀가 어긋나면 원인 1/3, R(f₀)가 어긋나면 원인 2.
- **E3 (Re/Im 분리 곡선)**: ΔR(f)=Re(Z_m−Z_r), ΔX(f)=Im(Z_m−Z_r)를 100 kHz–10 MHz에 그림. ΔX/ω ≈ 상수 → L 오차(값 = ΔL); ΔR ≈ 상수 → 누락 R; ΔR ∝ √f → skin; ΔX·ω ≈ 상수 → C 오차.
- **E4 (Re Z 기울기)**: 참조 Re Z_ii(f)가 1–10 MHz에서 증가하면 skin/근접효과 포함 증거.
- **E5 (디캡 소거 루프량)**: 같은 레일 두 포트 i,j에 대해 Z_loop = Z_ii + Z_jj − 2Z_ij. 공통 디캡 뱅크·평면 C 기여가 대부분 상쇄되어 **평면 확산 R/L과 포트 국소 L만** 남음. 모델과 일치하면 평면 모델은 옳고 오류는 디캡/마운트 쪽; 불일치면 평면·포트 정의 쪽(원인 6, §3 b). 92포트로 수백 쌍 통계 가능.
- **E6 (보드 간 교차)**: 세 보드에서 동일 증상 비율이 디캡 수·유전 두께와 상관되는지 확인 → 원인 2(ESR, 디캡 수 비례) vs 3(마운트 L).

**미검증 항목 요약**: PowerSI 평면 solver 이산화·포트 pin 결합·via closed-form·주파수 스윕 방식; Chen/Fang 1996 기법·페이지; Novak 2000 권호; Gustavsen 1999 서지; PIPro 이산화; "OpenPDN" 등 오픈소스 존재; Peaceman 등가반경의 PDN 적용(추론); 모든 런타임 추정치(추론).
