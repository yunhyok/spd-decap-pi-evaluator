# PowerSI 옵션 화면 요약 (소유자 제공 스크린샷, 2026-09-17)

- Layout → Processing: Via-Shape Connection "Automatically connect the vias to the shape of the same net when they run through the shape" **OFF**. Pad-Trace-Shape factor 2, Shape-Trace-Shape threshold 1000 µm. Remove slender hole ON. Polygon simplification 100 µm.
- Layout → Trace: Trapezoidal trace angle 90°, **Default surface roughness model: None**, Arc trace support OFF.
- Layout → Translator: Remove non-functional pads **ON(All layers)**; Treat pad on dielectric layer as drill OFF; Translate antipads as voids OFF; **Padstack plating thickness = "Plating thickness: 0 um"**(Drill/finished hole·Top layer thickness 아님); Component model name = Custom [ASI_MODEL].
- Simulation (Basic) → Special Void: Dogleg/Thermal/Other/Via hole smaller than **1500 µm 제외**(= 채움), slender hole 0, Auto OFF.
- Simulation (Advanced) → Mesh(소유자 제공 스크린샷, 2026-09-17 추가): **Maximum Mesh Edge Length = 4970 µm**(기본값, 패키지 크기에서 자동 산출; 유한요소 삼각형 최대 변 길이). "Simplify the geometry to improve mesh quality" **ON**, "Coarse mesh" **OFF**.
- 미확인: Simulation (Advanced) → Electric Models.

해석 메모: 도금 두께 0의 Sigrity 내부 의미(충전으로 취급 여부)는 공개 문서에서 확인 못 함. FICT 규칙·Allegro pxml(DR-1011-60: MICROVIA, drill 40 µm plating Y, pad 60 µm)은 기하만 확인. Special Void 1500 µm는 모델에 `void_fill_um=1500`(변형 pv)로 반영해 시험 중 — P18 스모크에서 Re Z −0.13 mΩ(err 0.81 → 15.9 %)로 결손을 키우는 방향.

메시 해석 메모: 참조는 최대 변 4970 µm의 기본 메시로 계산됐다. 레일 평면 크기(수 mm)에 비해 거친 값이므로 §6-2(MaxEdgeLength 1 mm·0.5 mm 재해석) 가설 H1(참조 미수렴)은 여전히 열려 있다. 우리 모델의 셀(h 200 µm, fine 50 µm)이 참조 메시보다 훨씬 촘촘하다는 점을 EXP 보고서의 비교 조건에 명시한다.
