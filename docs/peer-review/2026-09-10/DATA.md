# 외부 검토 데이터

[검토 시작](README.md) · [근거 자료](EVIDENCE.md)

JSON 수치·판정·실행 소스는 Git 저장소에서 바로 열 수 있다. 보고서가 직접 식별한 아래 NPZ 7개는 별도 Release 첨부 파일로 제공한다. 모두 게시 복사본의 SHA256을 재계산해 원래 receipt와 일치함을 확인했다. 이는 파일 동일성 확인이며 물리 정확도나 수렴의 재검증이 아니다.

| HQ 기준 원래 파일 | 다운로드 | 크기 (bytes) |
|---|---|---:|
| `outputs/research/astra-l04-10mhz-three-direction-complete-current-01/three-direction-fit-before-gates.npz` | [astra-l04-10mhz-three-direction-complete-current-01__three-direction-fit-before-gates.npz](https://github.com/yunhyok/spd-decap-pi-evaluator/releases/download/peer-review-20260910/astra-l04-10mhz-three-direction-complete-current-01__three-direction-fit-before-gates.npz) | 364,637,579 |
| `outputs/research/astra-full-contact-frequency-operators-02/frequency-10000000-conditional-operator.npz` | [astra-full-contact-frequency-operators-02__frequency-10000000-conditional-operator.npz](https://github.com/yunhyok/spd-decap-pi-evaluator/releases/download/peer-review-20260910/astra-full-contact-frequency-operators-02__frequency-10000000-conditional-operator.npz) | 101,909,646 |
| `outputs/research/astra-l04-frequency-partial-inputs-01/frequency-10000000-partial-inputs.npz` | [astra-l04-frequency-partial-inputs-01__frequency-10000000-partial-inputs.npz](https://github.com/yunhyok/spd-decap-pi-evaluator/releases/download/peer-review-20260910/astra-l04-frequency-partial-inputs-01__frequency-10000000-partial-inputs.npz) | 391,933 |
| `outputs/research/astra-l04-10mhz-closed-current-direction-01/initial-complete-model-residual.npz` | [astra-l04-10mhz-closed-current-direction-01__initial-complete-model-residual.npz](https://github.com/yunhyok/spd-decap-pi-evaluator/releases/download/peer-review-20260910/astra-l04-10mhz-closed-current-direction-01__initial-complete-model-residual.npz) | 195,512,060 |
| `outputs/research/astra-l04-10mhz-complete-current-gcrotmk-01/complete-current-final.npz` | [astra-l04-10mhz-complete-current-gcrotmk-01__complete-current-final.npz](https://github.com/yunhyok/spd-decap-pi-evaluator/releases/download/peer-review-20260910/astra-l04-10mhz-complete-current-gcrotmk-01__complete-current-final.npz) | 534,185,159 |
| `outputs/research/astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01/complete-current-final.npz` | [astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01__complete-current-final.npz](https://github.com/yunhyok/spd-decap-pi-evaluator/releases/download/peer-review-20260910/astra-l04-10mhz-closed-magnetic-gcrotmk-paired-01__complete-current-final.npz) | 534,399,095 |
| `outputs/research/astra-l04-10mhz-forward-closed-gcrotmk-01/complete-current-final.npz` | [astra-l04-10mhz-forward-closed-gcrotmk-01__complete-current-final.npz](https://github.com/yunhyok/spd-decap-pi-evaluator/releases/download/peer-review-20260910/astra-l04-10mhz-forward-closed-gcrotmk-01__complete-current-final.npz) | 534,452,095 |

전체 2,265,487,567 bytes. SHA256과 원래 위치는 [manifest.json](manifest.json)의 `release_assets`에 있다.

## 내려받기와 무결성 확인

본인 GitHub 계정으로 이 private 저장소에 로그인한 뒤 링크를 열거나, GitHub CLI에서 아래처럼 받는다. 아래 스크립트는 파일 확인만 하며 solver를 실행하지 않는다.

```powershell
gh auth login
gh release download peer-review-20260910 --repo yunhyok/spd-decap-pi-evaluator --pattern "*.npz" --dir review-arrays
python docs/peer-review/2026-09-10/verify_packet.py --assets review-arrays
```

## 가능한 검토와 범위

- 보고서의 계산식·수치·판정 근거, 같은 A/시작점에서 M을 바꾼 대조, 실행 코드와 결과 receipt를 외부에서 추적할 수 있다.
- 세 최종 candidate/residual 배열과 공통 시작/주파수 입력을 내려받아 저장 결과에 대한 독립 분석을 할 수 있다.
- 이 묶음은 SPD부터 전체 solver를 재실행하는 완전한 실행환경이 아니다. 원본 보드/Touchstone 전체, 모든 중간 mesh·연산자·런타임은 포함하지 않았다. PowerSI 비교값은 게시한 frozen comparison JSON의 개발 참조값이며, 원본 Touchstone 변환을 독립 재현했다고 주장하지 않는다.
- 전체 해석 재현이 심사의 전제라고 판단되면 검토 의견에서 필요한 원본·설정·누락 의존성을 명시한다. 현재 연구 계산과 자동 후속 실행은 중지되어 있다.
