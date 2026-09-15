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
