# 2026-09-15 연구 로그 인덱스

- [`RESEARCH_LOG.md`](RESEARCH_LOG.md) — 하루치 연구 전체 기록: 검토 판정, 데이터 사실, 채택 모델 정의, EXP-1~9 사다리(가설/변경/게이트/판정), 결과 요약, 미해결 항목, 코드·산출물 지도, 재현 방법.
- [`DECISIONS.md`](DECISIONS.md) — ADR 스타일 결정 6건(D1 Astra 폐기 ~ D6 f_res 대체 지표).
- [`results/`](results/) — `trackA`, `exp1`~`exp9` 각 폴더에 보고서(.md), 오버레이 플롯(.png), 결과 JSON(<1MB) 사본. 원본은 `/home/claude/work/`(컨테이너 임시).
- 원본 코드는 `tools/research-claude/exp1..9/`(저장소, 영구)에 있다.
- 기존 파일은 수정하지 않았고, 이 세션에서 솔버를 프로덕션 경로로 실행하거나 커밋하지 않았다.
- 핵심 결론 한 줄: Astra(3-D 적분방정식) 폐기, 2-D plane-pair+회로 하이브리드 채택, 260729 Port18 1MHz 오차 26.18%(이전 Astra 기준) → 2.7%(EXP-8 cavity-wall).
- 미해결: port16/19 R 결손, f_res 조건불량, 10–100MHz, PowerSI `MaxEdgeLength` 재해석 확인 요청.

- `CODEX_HANDOVER_RETROSPECTIVE.md` — Codex 인계 작업 회고(오판·수정·개선 결과·후속 과제)
- `reviews/` — 세션 검토 산출물 사본(deep/fresh review, 데이터 특성, Sigrity void 조사)

- `MODEL_PHYSICS.md` — 채택 모델의 물리 수식·코드 위치·참고문헌, 구현상 단순화 목록
- `figures/` — 보고서용 설명 그림 F1–F7 (+ CAPTIONS.md)
- `SPD_PI_연구보고서_2026-09-15.docx` — 사람이 읽기 위한 Word 보고서(회고 + 물리 + 결과)
- `results/exp10/` — EXP-10(2026-09-16, 로컬 PC): 1–100 kHz 참조 신뢰성 판정. 동결 기준 SOLVED, 단 ≤1 kHz에 켤레 비대칭 비물리 항 발견. EXP-10b: 1–100 kHz 복소계수 fit INDETERMINATE, 저주파 자기모순은 세 설계 공통. 코드 `tools/research-claude/exp10/`.
- `results/exp11/`, `results/exp12/` — EXP-11/12(2026-09-16): [구현 차이] (a)(b)/(c)(d) 플래그 시험. 전부 기본값 유지. (a)(b)에서 평면 C 단위 오류(1e6배) 발견 → EXP-13.
- `results/exp13/` — EXP-13(2026-09-16): 평면 C 단위 수정(j). 게이트 불변, P19 100 MHz 개선. 새 기본값 후보(소유자 결정 대기).
- `results/exp14/` — EXP-14(2026-09-16): [구현 차이] (e)(f)(g)(h), 기준선 exp13/j. 전부 기본값 유지. (h) via L 2배가 G5를 개선하는 단서.
- `results/exp15..17/` — EXP-15/16/17(2026-09-16): 92포트 일반화(G3 33 %, 예측 기각), R 결손 상관(핀필드 국소 경로), 10–100 MHz R 부족 25 %(90/92포트).
- `results/exp18/` — EXP-18/18b(2026-09-16): 벽 표피 R 고주파 전용(k, k2). 7케이스 개선은 92포트로 일반화 안 됨, 기준선 유지.
- `results/exp19/` — EXP-19(2026-09-16): 저주파 결손·고주파 부족 같은 기원(ρ 0.75); ΔL 재정의로도 고주파 L 부족 없음.
- `results/exp20/` — EXP-20(2026-09-17): via R 표피효과(m), +벽 표피 R(mk). 92포트 고주파 R 부족 0.254 → 0.203, 문턱 미달, 기록만.
- `results/exp22/` — EXP-22(2026-09-17): 참조 유효 L 평탄, 모델 유효 L 요동 → ΔL 지표 폐기, 공진 구조(EXP-23)로.
- `results/exp23/` — EXP-23(2026-09-17): 반공진 위치·높이 중앙값 정상, 산포는 SITE1 집중.
- `results/exp21,24,25,26/` — 2026-09-17: GND-only 92포트(H_b′ 기각), 사이트 비대칭은 모델 쪽, 경로 분해, **decap 고립 결함 발견**(P65 10 µF).
- `results/exp27/` — EXP-27(2026-09-17): 연결성 감사. 고립 decap 44/92포트, 원인은 균질화 경계조건 결함(G = 0). → EXP-28 수정.
- `results/exp28,29/` — 2026-09-17: 균질화 결함 수정(p): 고립 0·파국 오차 제거·P18 0.81 %, 그러나 균일 R 결손(×1.46) 노출로 게이트 PASS 감소. 소유자 결정 대기.
- `results/exp30,31/` — 2026-09-17: PCB s5m6585 160포트 err 중앙값 0.58 %·G3 80 % → R 결손은 패키지 특유; 요소 회귀로 급전 경로(via·trace) ×2–2.5, 평면 ×1.
- `results/exp32,33/` — 2026-09-17: via 길이 표면 간(q) 92포트 err 31.7 → 24.4 %(문턱 미달, 기록만); f_res 편향은 C·L이 아니라 지표 조건 불량.
- `results/exp34/` — EXP-34(2026-09-17): f0 지표는 PCB에서 이점 없음 → G4 유지(D8). D7: 기준선 exp28/p 채택.
- `results/exp35,36/` — EXP-35(pmk: via·벽 표피 R, 기록만)·EXP-36(pv: Special Void 채움, 결손 확대 → 기록만). `reviews/powersi_options_2026-09-17.md`(Mesh 설정 포함), `reviews/mlo_design_rules_2024_note.md`.
- `results/exp37..40/` — 효율(물리 불변): cuDSS(A2000) + assemble 캐시(37), 균질화 중복 제거·CuPy(38), 레이어 래스터 캐시(39, 가설 기각), 스캔라인 점-포함 판정(40). 포트 벽시계 33배 단축. 표준 플래그 `SPD_PI_SOLVER=cudss SPD_PI_FAST=1`.
- `results/exp41/` — held-out 260804 전 92포트(p): G3 14/92, R 비 1.47, 260729와 포트별 상관 0.95.
- `PROGRESS_SUMMARY_2026-09-18.md`: 인계 시점(Astra) → EXP-8 → 현재(exp28/p + GPU) 정량 비교와 목표 도달 정도(그래프 `figures/progress_*.png`).
