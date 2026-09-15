# Sigrity의 void·small hole 처리, DC/BBS fitting 문서 조사 (2026-09-15)

## 결론
Cadence 공개 문서에서 "special void" 분류 기준과 solver 처리 방식을 **확인하지 못했다**. PowerSI Extraction Best Practices app note는 robots/접근이 차단됐고, 포럼·Scribd에도 본문이 없었다 [미검증].
확인한 근거는 두 가지다. (a) 로컬 SPD 파일 자체의 키워드와 수치 [문서: 파일]. (b) Cadence 저자들이 PowerSI로 수행한 anti-pad 민감도 결과 [문서]. 이 두 근거는 "PowerSI가 작은 void를 단순화한다"는 가설과 **모순되지 않지만, 증명하지는 못한다**.

## 1–2. Special void / small hole
- **SPD 원문 [문서: 세 파일 모두]**
  - 헤더: `* Please do NOT edit special void criteria manually.`, `.DropShapesOfUnselectedNets`, `.ViaAntipadSearchFactor = 0`, `.ViaAntipadDistanceRangeFactor = 1`
  - 층마다: `DoglegHoleThreshold / ThermalHoleThreshold / SmallHoleThreshold / ViaHoleThreshold = 0.0015`
  - `.Configuration`: `SmallHole_Deletion_Factor = 0.00333`, `Remove_Slender_Hole = 1`
  - s5m6585 파일: DGND 폴리곤에 `SmallHole_A Sub-element` 태그가 붙어 있음
  - `.PowerSI` 블록: `.MaxEdgeLength = 4.97e-3`(S4LB002), `1.5e-2`(s5m6585)
- **해석 [추론]**
  - 0.0015가 m 단위라면 한계값은 1.5 mm다. 이 경우 패키지 anti-pad(수백 µm)는 모두 "small/via hole" 부류에 들어간다.
  - "Deletion factor"와 "Remove slender hole"이라는 이름은 삭제(=구리로 채움)를 시사한다.
  - MaxEdgeLength 4.97 mm가 최대 메시 변 길이라면, 국소 세분 없이는 개별 anti-pad를 해상할 수 없다.
  - 단위·의미·기본 동작은 모두 **[미검증]**이다.
- **Farrahi, Koether, Mechaik, Novak, DesignCon 2019 (PowerSI 사용) [문서]**
  - 2 MHz 평면 루프 L에 대해, anti-pad 개수·간격의 영향은 작았다.
  - 의미 있는 영향을 내려면 구멍을 "much larger than would normally be used in pin fields"로 키워야 했다.
  - 출처: http://www.electrical-integrity.com/Paper_download_files/DC19_PAPER_Track11_EffectPowerPlaneInductancePDNs_Farrahi_.pdf
  - 이 결과는 물리적 원인(구멍 ≲ 유전체 두께)으로도, solver 단순화로도 설명된다 [추론].

## 3. Cavity 높이와 사이에 낀 타 넷 평면
- 공개 기술은 두 가지뿐이다.
  - Plane solver가 "coupling between vias, reflection from edges, resonances … metal/dielectric losses"를 다룬다 [문서: https://community.cadence.com/cadence_technology_forums/system-analysis/f/sigrity/57376/].
  - Hybrid solver는 수평장이나 PMC 벽을 가정하지 않고 anti-pad 결합을 포함한다 [문서: Farrahi 2019].
- 높이에 유전체 두께만 쓰는지, 구리 두께를 포함하는지는 **[미검증]**이다.
- `.DropShapesOfUnselectedNets`는 이름상 비선택 넷의 도형을 버린다는 뜻으로 보인다 [추론]. 타 넷 평면이 선택되지 않았다면 해석에서 **제거(투명)**됐을 수 있다. SPD의 넷 선택 목록으로 확인할 수 있다 [미검증].

## 4. `DC_BBS_Setting DCFitted=1 BBSFitted=1`
- 매뉴얼 정의는 **[미검증]**이다.
- 관련 사실 [문서]
  - 스윕 설정은 `.FrequencySweep 0–2 GHz Adaptive`다.
  - 포럼 사용자는 "extrapolates data at low frequencies (below 100 kHz down to 0 Hz)"라고 보고했다. 답변은 없다: https://community.cadence.com/cadence_technology_forums/system-analysis/f/sigrity/66324/
  - Track A 분석에서 Re Z<0 샘플은 ≤1.1 kHz에서만 나왔다.
- **추론**
  - DC 점을 fitting으로 합치고, adaptive 표본 사이를 broadband rational fit으로 채운 것일 가능성이 크다.
  - 그렇다면 1–100 kHz의 R과, 1 MHz 미만에서 추출한 L은 "해석값"이 아니라 fit의 값일 수 있다. G1 0.03 mΩ 일치도 fit과 일치한 것일 수 있다.
  - 검증 방법: 저차(2–4극) 유리함수를 0.001–100 kHz에 맞춰 잔차가 ~1e-6이면 fit 데이터로 판정한다.

## 천공 시트 해석식
- **Rayleigh, Phil. Mag. 34(211):481–502, 1892 [문서: 서지 확인]**
  - 정사각 배열의 절연 원형 구멍(면적비 φ)에 대한 최저차 2-D 결과는 **σ_eff/σ = (1−φ)/(1+φ)** (Maxwell-Garnett 2-D)이다.
  - 고차 φ⁴ 보정계수는 **[미검증]**이다.
- **평면쌍 인덕턴스 [추론]**
  - 구멍 반경 a ≫ 유전체 두께 d이면, 2-D 평면쌍 L은 R과 같은 연산자를 따른다. 따라서 **L_eff = μ0·d·(1+φ)/(1−φ)**, R_eff = R□·(1+φ)/(1−φ)이다.
  - a ≲ d이면 자기장이 구멍을 평활화해 L 증가분은 μ0·d에 가깝게 줄어든다.
  - 관측 계수 1.31을 역산하면 φ ≈ 0.134다. solver가 작은 구멍을 채운다면 R과 L이 **같은 비율로** 참조보다 낮게 나와야 하며, 관측된 두 증상과 정합한다.
- Fan/Drewniak·Novak의 천공 평면 L 폐형식은 찾지 못했다 **[미검증]**.

## 권고 판별 실험
모델에서 한계값 이하 void(예: 1.5 mm 미만, 또는 SPD의 `SmallHole_A` 태그)를 채운 뒤 두 가지를 확인한다.
- ΔL/L_plane ≈ 0.31이 사라지는가
- 저주파 R 결손이 같은 비율로 닫히는가
