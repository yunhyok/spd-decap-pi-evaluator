# EXP-38 계획 — build 가속 1단계: 균질화 `homog.batched_gx`의 A2000 이식 + 동일 창 중복 제거 (물리 변경 없음)

작성 2026-09-17, 실행 전 사전 등록. EXP-37 프로파일에서 build가 end-to-end의 84–87 %이고 그중 `batched_gx`(창별 배치 Jacobi-PCG, (N,20,20) float64, 최대 600회)가 P18 127 s, P14 22 s다. 커널은 elementwise + 축 합뿐이라 CuPy로 그대로 옮길 수 있다.

## 1. 규칙(고정)
- 물리·수치 정의(창 정의, CG 공식, tol 1e-7, maxit 600, face_fix 규칙) 불변. 기본 경로(numpy)는 바이트 단위로 불변.
- 두 가지 가속을 옵트인으로 붙인다.
  - (A) **동일 창 중복 제거**: 비트패턴이 같은 창은 한 번만 풀고 결과를 복사한다. 결과는 기본 경로와 **비트 동일**해야 한다(같은 창 집합을 같은 순서로 풀면 CG 수렴 판정 `np.all(rn ≤ tol·bnorm)`이 배치 전체에 걸리므로 배치 구성이 달라지면 반복 횟수가 달라질 수 있다 → 비트 동일이 깨지면 (A)는 채택하지 않고 기록만).
  - (B) **CuPy 이식**: 같은 코드를 `xp = cupy`로 실행(A2000). 축 합의 리덕션 순서가 달라 비트 동일은 기대하지 않는다.
- 플래그: (A)는 `SPD_PI_FAST=1`에 포함(정확 변환), (B)는 `SPD_PI_SOLVER=cudss`일 때 켠다(GPU 사용 의사). CuPy가 없거나 실패하면 numpy로 폴백하고 로그를 남긴다.

## 2. 판정 기준(동결)
- (a) 정확도: (A)는 G 비트 동일. (B)는 같은 창 집합에서 CPU 대비 max |ΔG| ≤ 1e-9(절대, G∈[0,1]) 이고, 7케이스 Z(f)가 기준선 exp28/p 영수증 대비 max |ΔZ|/|Z| ≤ 1e-6(인계 문서의 영수증 재현 문턱). 1e-8 초과 시 그 값을 보고서에 명시하고 채택 여부는 소유자에게 위임(GPU 정책의 1e-8은 인수분해 경로 기준).
- (b) 속도: P18 build 475 s → ≤ 300 s, N 1.3M 포트(Port3/49) build 약 35 분 → ≤ 20 분이면 채택.
- (c) 기본 경로 불변: `smoke_port18.py` 5.83e-12 PASS, `run11.py --variant p --smoke --smoke-baseline exp28:p` 0.0, `homog.selfcheck_face_fix()` 출력 불변.

## 3. 산출물
`WORK_DIR/exp38/`: 창 통계(중복 비율), 정확도 로그, 7케이스 검증 영수증(`result_*_any_p*.json`), `EXP38_REPORT.md`; 저장소 사본 `results/exp38/`.
