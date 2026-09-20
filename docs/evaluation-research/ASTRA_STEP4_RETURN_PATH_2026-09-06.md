# SPD Decap PI Evaluator v0.23.1 — Astra Step 4 return path

기준은 2026-09-06, branch `codex/astra-evaluation-resume-20260906`, 시작 HEAD
`e2f219e71d8c8a397009f72242cce10d78cfc7ab`이다. Step 3의 실제 GND launch
frontier 두 개에서 A1의 L29 DGND island까지 가는 작은 source 경로만 조사했다. raw SPD
재scan, import/compiler 재실행, 전체 DGND graph/shape 해석, solver, PowerSI fit과 제품 `src`
변경은 하지 않았다.

## 판정

`ACCEPT_SOURCE_RETURN_CONNECTIVITY_ONLY / PARTIAL_NATIVE_RL_REPRESENTATION / STOP_SHADOW_REPLACEMENT`

L18 `Node2452693`과 L20 `Node2543231`에서 L29까지 이어지는 하나의 실제 source-record
경로를 찾았다. 두 plane bridge는 finite pad와 source copper를 함께 검사했고, 같은 D115b/D101
basis의 compiled topology에서도 동일한 via 사슬이 연속 numeric node와 finite-RL link로
나타난다. 따라서 Step 3의 “GND가 L18/L20에서 멈춘다”는 물리 단선이 아니라, plane contact를
명시적으로 따라가지 않은 이전 traversal의 경계였음이 확인됐다.

이 ACCEPT는 연결성에 한정된다. via plating/barrel 단면과 온도 의존성, plane spreading R/L,
복수 return의 current sharing과 mutual/external L, capacitive return, native G/C의 정확한 한 번
교체는 아직 완결되지 않았다. Evaluation Zii 또는 PowerSI 정확도 향상 결과가 아니다.

## 같은 basis와 실제 경로

D115b의 byte-identical saved indexes와 ownership IR은 source/project/certificate/topology/
raw-geometry/logical/plane-sheet identity가 서로 일치한다. 과거 17dt raw discovery index와
binding hash는 달랐지만, 아래에 쓴 node, via, trace, pad, surface와 artwork row는 D115b raw에서
source-record SHA까지 다시 대조했다.

확인된 경로는 다음과 같다.

1. 기존 TOP GND site의 L18 frontier `Node2452693`에서 L18 `Node2543232`까지 실제 60 um
   pad 두 개와 1 um 폭 source-copper corridor가 연결된다. 이어 `Via1468540`이 L19
   `Node2543230`으로 가고, 같은 node에서 `Via1468539`가 L20 `Node2543231`로 간다.
2. L20의 두 disk는 source copper에 직접 닿고, 두 finite-RL via는 compiled numeric node
   `497656`을 공유한다. `Node2543231`의 기존 60 um disk와 `Via1484371` 시작
   `Node1791013` `(-7697,12161.9) um`의 60 um disk가 모두 통과했으며, 뒤 disk의 최소
   negative clearance는 `214.049310993 um`다.
3. `Via1484371`의 L21 끝 `Node2556492`는 radius 70 um이고, `Via1484205` 시작
   `Node2556354` `(-7800,12403) um`는 radius 175 um다. 두 disk는 source order의 같은
   positive polygon 4에 완전히 포함된다. disk margin은 각각 `92.523705191 um`와
   `87.957000000 um`다. 마지막 negative는 order 1566, polygon 4는 order 1570에 더해지므로
   이 한 polygon 자체가 보수적인 connected-copper witness다.
4. source endpoints가 정확히 공유되는 stacked chain은
   `Via1484205: Node2556354 -> Node2556355 (L21->L24)`,
   `Via1484206: Node2556355 -> Node2556356 (L24->L27)`,
   `Via1484207: Node2556356 -> Node1726203 (L27->L28)`이다.
5. L28 `Node1726203`에서 width 130 um의 `EXACT` source records `Trace1242901`과
   `Trace1242902`가 각각 `Node1726181`과 `Node1726180`으로 간다. 이 두 node는
   `Via1306555`와 `Via1306556`의 L28 시작점이다.
6. 두 via의 L29 끝 `Node1726155`와 `Node1726182`의 radius 50 um disk는 L29 DGND
   source copper에 직접 포함된다. minimum negative clearance는 각각
   `312.008192775 um`, `500.007443255 um`다. L29 artwork는 단일 island
   `spd-surface-island:2db099ba622781734a17c3e0`이므로 A1 ground closure에 도달한다.

같은 compiled topology에서 via owner가 만드는 numeric-node 사슬도 독립적으로 연속이다.

| source link | compiled nodes | R | L |
|---|---:|---:|---:|
| Via1468540 | 430492–307401 | 0.667708 mOhm | 26.0944 pH |
| Via1468539 | 307401–497656 | 0.667708 mOhm | 26.0944 pH |
| Via1484371 | 497656–83082 | 0.767865 mOhm | 31.6158 pH |
| Via1484205 | 83082–751706 | 0.353737 mOhm | 164.239 pH |
| Via1484206 | 751706–756084 | 0.380801 mOhm | 182.147 pH |
| Via1484207 | 756084–336467 | 0.0982867 mOhm | 41.7153 pH |
| Via1306555 / Via1306556 | 336467–555851 | 각 0.341273 mOhm | 각 26.9529 pH |

`Via1360614`와 `Via1468540`도 compiled node 430492를 공유한다. 두 L28 traces에는 별도
native owner가 없지만, exact source trace endpoints와 compiled node 336467 collapse가 서로
일치한다. 표의 합을 effective return impedance로 쓰지 않는다. 두 L29 via를 포함한 실제
병렬 경로의 전류 분배와 plane impedance가 아직 없기 때문이다.

## 폐기한 후보와 cut 진단

처음 고른 `Via1468147` L21→L28 경로는 L28 pad disk가 source antipad와 겹쳐 direct
pad-to-plane 가정을 통과하지 못했고 compiled finite-RL owner도 없었다. 이를 via 자체의 물리
단선이나 board-open으로 해석하지 않았다. endpoint trace, 중간 pad, 겹치는 다른 finite pad를
따로 찾아야 하기 때문이다. 최종 ACCEPT 경로는 이 via를 사용하지 않는다.

same-basis raw의 whole-span inventory에는 DGND L21→L28 `DR-2128_350` via가 24개 있다.
24개 모두 L21에는 `TOPOLOGY_ONLY` trace 하나가 있고 L28 endpoint에는 source trace가 없으며,
compiled via owner도 없다. 24개 L28 disk의 direct source-plane contact도 모두 실패했다. 이는
그 24개 direct 후보의 범위만 거절한다. 실제 adjacent conductor cut에는 L21→L24,
L24→L27, L27→L28 via가 각각 7,310개 있어 whole-span inventory를 전체 core cut으로
부르면 안 된다. 최종 경로는 이 중 공유-node stacked chain 세 개를 사용한다.

## replacement 경계

[same-basis replacement receipt](../../outputs/research/astra-step4-basis-01/replacement-boundary.json)는
0.360 s에 세 saved index의 basis identity를 확인했다. D096 candidate native partial은
`340.221414118 pF`이고, old closure 밖으로 이어지는 retained coupling 9개의 합은
`16.9948870889 nF`다. existing full-old-class embedding은 이 cross-boundary partial 때문에
preflight에서 거절되며, P7 recipe는 1 GHz로 고정되어 Step 3에서 제안한 1 MHz shadow를
그대로 실행할 수 없다. 이는 production STOP 실행이나 새 source replacement C의 인증값이 아니다.

따라서 다음 작은 수치 실험은 새 manifest/schema가 아니라 지금 확인한 source/compiled 경로와
기존 owner ledger를 그대로 사용해야 한다. Astra HQ가 복수 GND 경로 중 retained native RL과
새 sheet/return 항의 소유권을 정하고, current sharing 및 mutual/external L을 포함할 최소 subgraph를
선택한 뒤에만 한 주파수 shadow를 만든다. candidate partial 하나만 정확히 제외하면서 9개
cross-boundary coupling을 유지할 수 없는 구성은 실행하지 않는다.

알려진 W6/D096/D115b 메타데이터를 추가로 확인했지만 재사용 가능한 native Device/local-branch
numeric response 또는 cached sparse factorization의 path/hash/schema는 없었다. 복원물은
raw-spatial v3, compiled-topology v1과 ownership IR v2이고, boundary receipt도
`native_numeric_substrate_loaded=false`, `replacement_executed=false`,
`powersi_comparison_executed=false`다. saved finite-RL links는 Maxwell G/C base나 factorization이
아니다. 실제 Device Zii update에 필요한 최소 저장값은 같은 source/project/code/profile/port
identity에 결속된 Device와 local-branch의 complex 2x2 response다. 이 값이 없으면 frequency별
Maxwell partial과 backend/order/tolerance까지 결속된 substrate cache가 필요하다.

## Local DC와 최소 response witness

HQ의 source-bound Trace311318 연구는 local DC 범위를 수치로 좁혔다. exact-circle analytic
bound는 `0.335626783–0.553590924 mOhm`이고, original guard를 유지한 inward-dyadic 128-sided
level-3 결과는 `0.444624426 mOhm`이다. 64/128 mesh의 마지막 변화는 `0.6445%/0.6330%`,
circle 변화는 `0.0975%`로 각각 사전 선언 2% 안이다. coordinate ablation의 original-float-um과
inward-dyadic-rescaled-m는 모두 `MESH_CROSSES_VOID`여서, 이 성공은 exact-representable dyadic
subdivision에 한정된다. 일반 GEOS/FEM 수정이나 source circle의 정확한 해, AC/L, full-PDN 결과가 아니다.

HQ의 후속 [L21 4단자 조건부 연구](../../tools/research/study_astra_l21_four_terminal_dc.py)는
source asset의 단일 8-vertex positive polygon, negative primitive 부재와 L21 AON node 네 개를
같은 raw basis에 다시 결속했다. 세 qualified MLO via는 drill radius 20 um electrode를 썼지만,
unqualified core `Via336239`는 source pad radius 175 um 전체를 equipotential로 두는 별도 조건부
가정을 유지했다. Full floating 4x4 Y와 balanced 3x3 Z는 symmetry, row-sum, passivity를
통과했으나 64/128-sided mesh의 마지막 변화가 `14.3789%/14.3888%`로 2% gate를 넘었다
(circle 변화 `0.1296%`). 따라서 18.203 s receipt의 판정은
`STOP_CONDITIONAL_FOUR_TERMINAL_DC`이며 저항 행렬을 채택하지 않는다. common native node에는
네 via RL 외에 fifth ideal artwork link도 남아 AC replacement 경계가 닫히지 않는다. script/JSON
SHA-256은 각각 `7f64dc8889870bb0ced57f93492bf94863a046034b7041c24f83275a7024f400` /
`76bbb152f89b0e8086128f34a489f06c77ba10b2dfae0b952fc245723881ce3b`이고, Terra의 read-only
검토는 이 STOP과 조건부 범위에 P1/P2가 없다고 판정했다.

후속 [interior-grid seed 연구](../../tools/research/probe_astra_l21_seeded_mesh.py)는 같은 input hash와
electrode radii를 유지하고 Shapely Delaunay backend를 process 안에서만 치환한 뒤 `finally`로
복원했다. 20 um grid의 DC row 하나(`1067 nodes/2124 triangles`)는 생성됐지만, 10 um grid에서
original face boundary edge 8개가 emitted triangle edge에 없어서 tolerance 없이 즉시 중단했다.
따라서 `0.844 s`, `STOP_SEEDED_L21_DC`이고 convergence나 저항 acceptance가 없다. script/JSON
SHA-256은 `61269cfdf79b1762516452eb0bf7910e44bf382a25afcc4180cd2e259ca7cd6e` /
`e1680ce359edefc215a54150811fb16a1e3c0dfd3e3d53e6e9e5e6609941b1db`이며, Terra도 이 경계와
research-only scope에 P1/P2가 없다고 판정했다.

Luna의 [rank-one probe](../../tools/research/probe_astra_rank_one_branch_update.py)는 실제 saved
`Via336274` `R=0.667708269 mOhm`, `L=26.0943791 pH`와 위 refined trace R을 1 MHz 합성 passive
network에 넣었다. baseline 2-port Z에
`delta_y=1/(Zvia+Rtrace)-1/Zvia`를 적용한
`Zdd'=Zdd-delta_y*Zdb*Zbd/(1+delta_y*Zbb)`는 direct GlobalMNA 재조립과 상대오차 0으로
일치했다. 수정된 zero-R check도 실제 `_rank_one(z0,z_via,0)`를 호출해 prediction 오차 0,
`delta_y=0`, denominator 1을 확인했다. non-real cross impedance에서 conjugate/absolute-square 식은
`0.00122158` 상대오차로 기각됐고 positive-R denominator-zero와 nonfinite 입력도 거절됐다.
[corrected receipt](astra_rank_one_branch_update_2026-09-07-02.json)는 shell `0.866 s`,
`ACCEPT_RANK_ONE_RESEARCH_ONLY`; 수정된 script/JSON SHA-256은 각각
`10f52d09e70af25254e8e3790d4a35a5b6359d80aba117476b3e8283a141dc37` /
`33a158c8c3f412db6634ed784b9d859181cac24501e007f0f6acb58527f5d41f`이다. 초기 JSON은 SHA-256
`6d410c2feaf953bdffde8fc43f13b48a6ac4062f48a0783558955fab10a58f46`로 보존했지만 zero-R 항이
동일한 `_solve` 두 번만 비교했으므로 수정된 `-02`를 검증 근거로 삼는다. 이는 고정 branch
update에 complex 2x2 response가 충분함을 보이는 합성 조립 검사이며 실제 Device 변화가 아니다.

알려진 W6 mode-12 메타데이터도 exact path에서 다시 확인했다. hash-bound manifest의 11개
artifact에는 candidate bundle, import/report, correlation/BLAS evidence, logs와 sidecar만 있고
standalone response/cache/factorization은 없다. selected rail report에는 Device port identity,
450-point solve/frequency identity와 `impedance_sha256=9ecd8fbbe921c758ec3d5c21cffd16c7fb8309324c735de2614575e5c410c999`,
`numerical_result_reused=true`가 있지만 impedance array와 internal branch port가 없다. candidate
bundle 내부는 이 범위에서 열지 않았다. 따라서 report만으로는 Device scalar curve도 복원할 수
없고 rank-one update의 `Zdb/Zbd/Zbb`도 얻을 수 없다.

## 실제 실행

- HQ: D115b ownership/raw/compiled byte-identical 복원, exact geometry member 4개 materialization,
  replacement-boundary query를 실행했다. geometry 3개 materialization은 0.047 s, L24 추가
  materialization은 별도 receipt로 보존했다. L21 조건부 4단자와 seeded-mesh 진단도 각각
  `18.203 s` STOP과 `0.844 s` STOP으로 보존했다.
- Sol: immutable/query-only SQLite의 bounded SQL로 historical/source row 재결속, cut census,
  endpoint adjacency, source geometry sufficient witnesses와 compiled node/link 사슬을 확인하고
  [재현 script](../../tools/research/query_astra_ground_return_path.py)와
  [JSON receipt](../../outputs/research/astra-step4-return-01/ground-return-path.json)를 확정했다.
  최종 실행은 shell `9.909 s`, JSON 내부 `9.421 s`, status
  `ACCEPT_SOURCE_RETURN_CONNECTIVITY_ONLY`였다. script SHA-256은
  `e3387a59e7f5a7a3840909e4a4e20ce6256c9c331d3c5005aa3a8453b56ae974`, JSON SHA-256은
  `1dac38826ec05997169c3eef7d77721b045d5c858c29d7bf1a69af0bad08ca33`이다.
  최종 실행 전 두 번의 초기 read-only 실행은 약 `49.8 s`와 `51.9 s`에 SQLite
  `interrupted`로 끝났고, 별도 pre-output 실행 한 번은 negative-circle corridor 판정을 analytic
  closed-disk 검사로 고치기 위해 약 `30 s`에 수동 중단했다. 세 실행 모두 JSON이나 solver
  결과를 쓰지 않았다. source ID lookup을 indexed fold
  key로 바꾸고, 검증된 0225/0258/0259 geometry member와 bounding-box prefilter를 사용해 전역
  재조립/불필요한 geometry distance를 제거했다. 마지막 `EXPLAIN QUERY PLAN`은 node/via/trace가
  모두 index `SEARCH`임을 확인했고 24-disk 검사는 `0.469 s`, pass `0/24`였다.
- Luna: return-path 첫 dispatch에는 산출물이 없어 Sol이 인계했다. 후속 rank-one witness와
  zero-R 공식 호출 수정은 Luna가 새 `-02` JSON으로 한 번 실행해 위 shell `0.866 s` ACCEPT를 남겼다.
- Terra: replacement-boundary 산술과 scope를 read-only로 검토했다. candidate pF 값, retained
  9개 합과 1 GHz recipe 제한에서 모순을 찾지 않았다. 또한 HQ의 local trace DC bound를 별도
  검토해 `335.626783–553.590924 uOhm` 수식·단위·quadrature·gauge 검사를 ACCEPT했고, 이를
  full-PDN 또는 PowerSI 결과로 확대하지 않았음을 확인했다. 최종 return script/receipt도 재실행
  없이 검토해 L18 analytic circle-corridor predicate, L20 common node, L21 late-positive witness,
  stacked chain과 connectivity-only scope에 P1/P2가 없다고 판정했다. Dyadic refinement와
  수정된 rank-one 수식/zero-R 공식 호출/직접 재조립도 read-only로 ACCEPT했고 P1/P2는 없었다.

C1 1800 s STOP, WP2/WP3 PARTIAL과 W6 accuracy FAIL은 변경하지 않는다. 최종 알고리즘과
shadow 실행 여부는 Astra HQ가 결정한다.
