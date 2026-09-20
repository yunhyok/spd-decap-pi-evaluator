# Evaluation Solver Implementation Plan

> Historical implementation plan. The v0.22.0 production status and named-case
> evidence are recorded in the layer-surface validation record.

- Date: 2026-08-04
- Status: historical plan with the current research executions recorded below; no product runtime change
- Target: improve Evaluation Analysis accuracy and responsiveness
- Out of scope: De-cap Distribution behavior, PowerSI-derived calibration,
  installer/release work, and sign-off claims
- Research basis: [synthesized deep-research decision
  record](EVALUATION_SOLVER_DEEP_RESEARCH_2026-08-04.md), including independent
  review of Claude commit
  [`26d7698`](https://github.com/yunhyok/probe-card-mlo-pdn/commit/26d76982ec6965c744f062dd6213e04a9fd94a51)

## Astra execution plan — 2026-09-06 (SPD Decap PI Evaluator v0.23.1)

**2026-09-12 사용자 명시 재개. 아래09-10 STOP은 해제됐으며,
peer-review9078b7ed의 수정 계획과 [공통 모델 소유·대조 기록](evaluation-research/ASTRA_COMMON_MODEL_2026-09-12.md)을 따른다. 단계1 표/소형 대조 사양 완료, 대표 port/return closure 미완으로 실제 same-A 대조 결과는 아직 없다. 기존 대형 M 변형을 반복하지 않는다.**

**2026-09-10 15:01 KST: 사용자 지시로 연구 전면 중지, 독립 Red Team은
부모 HQ 담당. 새 사용자 재개 지시 전까지 아래 후속 계획을 실행하지 않는다.
정지 상태와 미완료 코드는 HQ_REBOOT_CHECKPOINT_20260910.md 최상단 참조.**

2026-09-10 14:46 KST: forward GCROT-01 완료, PID31636 exit0,
1075.469초/private23.799GB. full0.71128913/gap0.00024500838로 common
progress 두 기준 FAIL; 비용1.09747배 PASS. 원 RHS 상대잔차710.52077,
raw info1. HQ saved QR/explicit replay 검산 PASS(8.999초). 단방향 M은
채택하지 않고 저장 데이터의 블록별 전력 일관성 기여와 결합 원인을 먼저 분리한다.
현재 numeric worker 없음. 최종 근거는 Step5 supervision의14:46 항목 참조.

2026-09-10 14:13 KST: forward GCROT-01 실제 실행, owned PID31636/exec6580,
실행466cd9ef…(검토된 held84d0c5…에서 flag만 변경). trueA/warm/guard AST동일,
M만 forward 결합 추가. M2/A3/FMM12,1200/1320/32 및 common cap 유지.
첫 reserve8.687초 PASS. 아직 결과 없음.

2026-09-10 14:05 KST: forward qualifier-01 완료17.546초/private4.932GB,
result4b617a1f…/guardf926c9c9…/artifactb1d12382…; HQ saved1.604초 PASS.
cross/RHS12.9066, 국소closed .0925735→4.194e-15; 전체수렴은 미검증.
Sol/Terra가 동일 trueA/THIRD warm/public2step의 forward M 대조 source held 준비.
1200/1320/32 유지, full.5518544768/gap.0001027969422/cost1077.9494초 이하.
현재 numeric worker 없음. 새 Mb/FMM/fullA 없는 자격 검증만 완료한 상태다.

2026-09-10 13:53 KST: saved union-01 완료13.515초/private4.295GB,
resultf2d89a98…/guard5c194949…, root QR5.664초 PASS. rank4/cond41.389,
full0.564206302/gap0.000167673029 모두 사전 기준 미달. H/FMM/새A0회,
후보NPZ 없음. 다음 최소 자격 대상은 한 번의 Mb를 유지하는 접점→closed
Lself 단방향 삼각 보정이다. 현재 numeric worker 없음, 정확도 미승인.

2026-09-10 13:36 KST: Hω paired-01 완료1030.469초/private23.799GB,
result ef23ddf9…/guard1d87f15f…/finaldb4a1c4a…, HQ saved8.435초 PASS.
full0.689818096은 기준보다30.3% 감소했으나 gap0.000236543743은84.1% 악화,
time+5.15%로 종합 paired FAIL. info1/RHS상대689.073, 정확도 미승인.
현재 numeric worker 없음. 동일 A/warm의 저장4방향 union 진단 held 준비;
새 H/FMM 없이 full0.5518544768/gap0.0001027969422 이하 가능성 평가.

2026-09-10 13:18 KST: Hω paired-01 실제 실행, owned PID30444/exec54287,
실행 SHA c3db5996…; 같은 THIRD warm/RHS/trueA에서 closed M만 변경.
1200/1320초/32GiB, M2/A3/12 FMM. 결과 전이며 기존 paired 기준 유지.

2026-09-10 13:00 KST: Hω static qualifier14.032초/peak private7.61GiB,
PID28564 exit0. result8dcab2dd…/guard16492f5d…/matrix80fd946f….
구조10+inverse3 PASS; complexLU1.184585초, actual normal/T3.423e-13/3.345e-13.
HQ rawH+bincount K action saved check2.084초 PASS. 전체 수렴/가속 미검증.
동일 THIRD warm state/trueA/public2step에서 closed M만 바꾼 paired trial을 held 준비:
full<=0.7912610103778645, gap<=0.00010279694220459625,
external<=1077.9494초. max1200/1320/32는 유지하며 factor는 매 M 새로 구성.
정적 검증은 source lift/trueA를 바꾸지 않았고 FMM/fullM/fullA를 실행하지 않았다.

2026-09-10 12:37 KST: public cycle01은979.954초/23.798GB, exit0.
result17eec05e…/guard19ade392…/final85e8db88…, M2/A3/FMM12.
full0.989076263(직전0.708308배), |J-Z|0.000128496178(0.640004배)로
두 진행 조건 PASS. 직접 최종A replay3.824e-11 및 HQ saved QR 확인도 PASS.
rawinfo1, original-RHS relative988.00783, raw reference error54.59563948%로
수렴/물리/정확도 미승인. FMM은766.845초로 전체78.25%였다.
positive 진행 결과를 유지하며, 속도 목표를 위해 추가 긴 반복 전에
H+jω C.T Lself C의 static-only 자격/비용을120/150초·8GiB에서 검토한다.
trueA/P/realH 유지, noFMM/fullM/fullA. Sol/high 감독/Terra/high held 준비 중.

2026-09-10 12:19 KST: held70dd58f2…의 public GCROT/complete assembler tiny test와
receipt preflight 및 HQ/Sol source review를 통과했다. RUN_RELEASED만 변경한
`c84e6475deee8374b4af0a3ba50df32d1f40b59a2bc5193904a9d348b90573b9`로
`astra-l04-10mhz-complete-current-gcrotmk-01` PID11392/exec66756 실행 시작.
2 M + 2 full A + 독립 최종 full A, 1200/1320초/32GiB와 기존 두 진행 기준을
유지한다. exact 최종 field/action/residual을 gate 전에 저장한다. 결과 대기 중이며
수렴/물리/정확도 미승인 상태다. 상세는 Step5 감독 기록의12:19 항목을 따른다.

**11:49 KST, 2026-09-10:** three-direction result`3e5704ef…` completed396.406s/
22.871GB. Full norm1.3963924 falls42.71% from two-column; closed/L25 also
improve versus that candidate, but closed2.079xinitial still fails the original
screen. |J-Z| falls85.22% to0.000200774 ohm; raw reference error54.59438%
is unvalidated. Original-RHS relative residual remains1394.884, far from1e-9.
Next prepare public gcrotmk correction m2/k0/maxiter1/x0=None with simultaneous
nonclosed-M/closed-H, two recurrence actions and one explicit final candidate
action. Caps1200/1320s32GiB; screen requires both20% true-residual and20%
stationary-gap decrease, with all block metrics retained. No fourth handpicked
direction, modified physical model, or convergence claim; detailed guards and
acceptance are in the Step5 supervision record. No new cycle has started yet.

**11:23 KST, 2026-09-10:** two-direction screen`3239c406…` completed363.844s/
22.483GB with structural gates passed, but closed5.11229x and L25 3.86554x
fail. Full residual2.43737 improves again, with no accepted board accuracy.
Independent saved-only convex check proves no coefficient pair can meet both
full<=2.66608 and closed<=0.00385404: constrained minimum full11.28042,
normalized squared dual lower bound17.90215>1. Do not retry the same span.
Next prepare one combined nonclosed-M/closed-H residual direction at that
boundary, one joint action, and a three-column fit under unchanged caps.
Actual magnetic physical-failure provenance replaces the legacy R-only list;
the frozen two-direction result remains preserved with the correction logged.

**10:25 KST, 2026-09-10:** psi-only direction screen`63ba3212…` completed
329.812s/22.192GB with13 structural gates passed, but performance failed:
full residual0.233478x, closed4.62437x, L25 3.56080x; port Z unchanged.
No same-direction repeat or acceptance. Next prepare one complementary
nonclosed direction from saved optimized residual, with existing flexible M
(B1 cache plus fresh Q/R factors), release factors, then one joint magnetic
action and a column-normalized two-column SVD fit against original r0.
Keep numerical-direction evidence separate from missing-physics attribution.
Current detailed preregistration and caps are in the Step5 supervision record.

**09:53 KST, 2026-09-10:** L04 same-triangle self assembly passed8/8 in
12.531s/0.797GB private (result`d8a771b8…`). Joint L25/L04 four-channel
action computed in267.956s; arrays were saved, then JSON serialization failed
on a NumPy bool. Original guard remains exit1,284.390s/18.660GB. Separate
saved-array recovery`77a73aca…` passed original6 plus4 recovery gates in
7.016s/3.585GB, without another FMM. Joint reciprocity5.299e-11, lift
gradient2.538e-11, saved Pt/Ct replay exactly equal. These qualify only the
conditional saved-field action. Next held closed-current direction screen
retains all global residual rows and starts with an unchanged-row lower bound;
no new board solution, full PSD, quadrature or PowerSI accuracy acceptance.

**08:58 KST result, 2026-09-10:** first all-row L25-to-L04 cross action
`b70503aa…` completed84.593s/5.633GB private (guard`b5f5fee1…`). Four target
calls and source-to-point/direct/lift checks passed. Saved artifact`9c880c3c…`
contains2272974 branch forces and P.T/C.T projections; the raw force is also
checkpointed before H. No numerical/accuracy acceptance changes: reverse,
L04 self, quadrature and complete coupled feedback remain open. Review existing
self-L/common point operator reuse next; do not rerun the completed one-way action.

**07:57 KST next, 2026-09-10:** actual complete-current lift/transpose
`16a44e85…` passed8/8 in8.031s; retain all644870 closed-current coordinates.
Prepare one all-row L25-to-L04 cross force and P.T/C.T projections, using
the pinned10MHz field and existing centroid FMM. Four target calls, caps
330s internal/360s external and24GiB; no extension. Direct16-point checks
qualify mapping/normalization only. Self/other layers, quadrature and complete
feedback remain open; no new accuracy claim or FMM run yet.

**07:37 KST result, 2026-09-10:** bounded L25 self+mutual result `90a64009…`
finished in 560.500s/28.166GB private, with six fullL/NtD actions and no
budget overrun. The field remains materially unconverged (info1, residual3.2536).
Its adapted physical diagnostic `16820a21…` passes18/23; globalKCL3.708mA,
L25 constitutive0.3124mV and power mismatch0.1509mOhm fail. Apparent reference
error54.60% versus prior63.41% is not accepted accuracy improvement.
Stop the isolated continuation. Next review the existing L04 complete current
representation Pg+Cpsi and its implicit transpose before coupling return
magnetism; retain closed currents and all current/charge scope limitations.

**07:20 KST execution, 2026-09-10:** latest R-only field physical replay
`75985361…` passes 19/23 gates; L04 contact KCL now passes at 8.2069 nA.
Global KCL 20.9514 microamp and power mismatch 5.3504 nano-ohm still fail
the unchanged criteria, with raw info1. Recovery/replay cost 7.531/31.047s.
The next L25 self+mutual run has started with frozen driver `fb737651…`,
after HQ corrected branch-versus-potential dimensions and Sol re-reviewed it.
Caps and conditional scope below remain unchanged. Detailed ownership and
receipts are in the [supervision record](evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md).

**06:11 KST next changed-physics discriminator, 2026-09-10:** prepare a
disabled finite10MHz intra-L25 self+mutual experiment using the existing
source-bound operator2c880f42… on all604031 L25 currents. Warm from643767bd…;
true MNA adds -jw*Lfull*q, while auxiliary Q/primal uses R+jw*Lself only.
No FMM inside M, no old board reassembly. Proposed caps remain4 M/28 B1/
24 Q/4 R/6 NtD plus6 fullL, within600s internal/720s external/32GiB.
Save actual field/residual/Z/J/Lq and final timing. Existing1MHz Lq quadrature
change1.5946% is a limitation, not a10MHz bound. Complete return/cross-layer
magnetism remains open. Numerical acceptance is still info0/1e-9 and modified
physics checks are required; this conditional ablation cannot close the full goal.

**05:58 KST result, 2026-09-10:** bounded flexible field continuation
`c81140d9…` completed in 189.172s/25.343GB private, using exactly 4 M/28 B1/
24 Q/4 R/6 true A actions. True residual fell 94.4879% to 0.000220726182650;
L02 norm fell to 0.242177x warm. The numerical screen is positive, but raw
info1 and the original1e-9 failure remain. Raw PowerSI error barely changed:
63.45055% to 63.41180%; stationary error63.41076%. No physical revalidation.
Do not extend unchanged-model iterations. Reprioritize common current/charge
representation and missing physical terms. External210s/32GiB passed; total
189.172s exceeds the cooperative180s limit and no final worker-budget receipt
exists, so end-to-end180s compliance is not claimed. This is continuation cost,
not standalone solver speed. Source/guard/field: a5384533…/4e113329…/643767bd….

**05:42 KST preregistration, 2026-09-10:** one flexible full-field correction
starts from restart4 plus the optimized saved Schur direction. Use one R=L02
sweep followed by K/Q Schur correction; retain every RHS term, including
`-D*r_g` in the outer contact-current lift. No final R sweep or old coarse
balance. Inner GMRES restart4/maxiter1 targets 0.1 reduced residual; outer
gcrotmk uses m4/k0/maxiter1 on the correction with zero initial guess.
Caps: 4 M, 28 B1, 4 R and 24 Q inverse actions, 6 true A/NtD actions,
180s internal/210s external/32GiB. Reuse joint factors. The positive screen
requires at least 20% reduction from 0.004004416642579117 and L02 norm growth
at most 2x, with finite/inverse/identity/resource gates intact. No extra cycle.
Save raw Z, ordinary-transpose stationary J, full field/action/RHS and raw
solver info; numerical acceptance still requires raw info0 and original-RHS
residual <=1e-9, followed separately by 23 physical checks. Detailed contract:
[Astra supervision record](evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md).

**05:21 KST positive screen, 2026-09-10:** the preregistered L25/joint Schur
probe `b148569d…` completed in 56.078s/10.773GB private. It needed 4 Arnoldi,
5 Schur, 7 B1 and 1 true NtD actions; only L25 was factored (1.205s).
Reduced residual 0.09012 and optimized true-global residual 0.004004416643
pass every screening gate, a 52.4059% reduction from the saved baseline.
L02 residual shrank to 0.902128 of its prior norm. Saved-only port comparison
still gives 63.45055% unvalidated raw PowerSI error for the optimized candidate.
Define arbitrary-RHS preconditioning and a bounded flexible outer continuation
before a full field run. This screen does not resolve the original physical
failures or establish convergence, broadband accuracy, or full-solver speed.

**Next bounded discriminator, preregistered 2026-09-10:** set K to the cached
joint potential/trace block, Q to L25 potential/current, and hold L02 fixed.
Use the reduced operator `S_Q=P_QQ-P_QK*B1*P_KQ`, right-preconditioned by the
measured cheap L25 factor. Allow one GMRES cycle with at most 8 Arnoldi actions,
9 Schur matvecs including the final residual check, and 11 total B1 calls.
Then make one true global NtD action. Require reduced residual <=0.1 of its
RHS, actual B1/L25 inverse residuals <=2e-8, and optimized true-global relative
residual <=0.007553155703 (10% below the prior best scalar direction).
Record induced L02/outside effects. The bounds are 120s/12GiB cooperative and
150s/24GiB external. No extra cycle after failure or inadequate global gain.
This is a preconditioner screen; original info4/physical failures are preserved.

**05:03 KST screening, 2026-09-10:** one cached joint back-substitution and one
true NtD action (`b81bd537…`) finished in 33.547s/5.518GB private. Native/L14/
L04 contact residuals are corrected, but the L25 residual dominates. Unit
residual is 5.542711x initial; optimal scaling reduces the norm only 0.253082%.
A saved-only two-direction fit improves that to only 0.260746%. Do not launch
a long continuation. Next assess a bounded L25/joint Schur correction with
outside-block effects measured; no physical or accuracy acceptance change.

**04:41 KST diagnostic, 2026-09-10:** saved internal auxiliary trace recovery
`e14e7a76…` completed in 24.031s/3.333GB private using three cached B1 actions;
no factor, NtD, or Krylov. Known-coordinate replay is 2.751e-14 and actual B1
projected-RHS residual is 7.201e-13, but full primal residual ratio is 5.07975
and H-lambda-minus-E-g relative to g is 0.848392. Next distinguish coarse-mode
and off-block L02 coupling contributions to the final trace residual. No
physical-model attribution or convergence/accuracy promotion; all five source
physical failures remain. See the supervision record for frozen receipts.

**04:18 KST screening result, 2026-09-10:** cached joint one-step measurement
`a620e7e1…` took63.579s/24.740GB private without repeating native LU. Unit
residual grows31.2158x; optimal complex scaling reduces its norm only0.0204116%.
L14 is corrected but97.2916% of unit residual squared is now L04 contact error.
Do not launch a long Krylov continuation. Saved-vector contact reconstruction
identifies true-NtD versus auxiliary trace mismatch; a three-cached-action,
no-factor diagnostic is being prepared to distinguish auxiliary equation
defects from trace disagreement. No convergence/physics/accuracy promotion.

**04:05 KST update, 2026-09-10:** joint auxiliary factor cache passed in133.625s
external (LU81.720s),16.880GB sampled private. Raw inverse fails6.10e-8; fixed
B1 normal/transpose residuals1.208e-9/1.159e-9 pass the unchanged2e-8 gate.
Result `4eb346b9…`, cache `b95162dc…`, guard `33f65bae…`. Next reuse that cache
for one true10MHz residual-action measurement; do not repeat LU or infer global
convergence from factor checks.

**03:59 KST result, 2026-09-10:** a single sparse qualified L25 self-L action on
the new unvalidated10MHz current took2.188s/156.5MB private. Fixed-field
`jω q.T Lself q/I² = +0.701607+j1.240073mOhm` has magnitude1.424792mOhm,
1.40169 times the saved raw reference gap. Result `66e50c33…` preserves info4
and all five physical failures. This motivates magnetic model work but is not
a finite response change, bound or justification for a self-only model. No
FMM/factor/solve. Joint auxiliary factor-cache preparation is under review.

**03:47 KST update, 2026-09-10:** the 10MHz field recovery and original physical
diagnostic are complete:18/23 pass, with global KCL, L04 contact KCL and power
closure failing (five gates). Saved-development-reference comparison gives
63.65262225% raw complex error; stationary J gives63.41173998%. Both remain
unvalidated, with info4 and residual0.00841369 preserved. Power imbalance is
not an impedance error bound. See the [current supervision record](evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md)
for frozen receipts. Joint native/L14/L04 auxiliary assembly now passes static
source-block identity in11.094s/0.970GB private; no factor or solve. Assess
missing magnetic/return/proximity contributions alongside numerical coupling
before another expensive run. Accepted1MHz accuracy remains26.18268398%.

**02:55 KST update, 2026-09-10:** actual10/100MHz full-contact Y operators are
assembled, each16,784,042nnz, in30.344s/1.415GB private. Result `7efcd3d9…`,
guard `ff816c7f…`, driver `9218fdd6…`; 1MHz retainedGC reproduction7.4485e-18,
finite/termination difference0. Failed attempt01 is preserved: replacing whole
partials removed unowned GC; v2 instead reuses exact owner removal. A separate
1.187s saved-input audit confirms fixed topology/R/B and frequency-matched
native/L04 inputs. Next implement and audit the frequency-specific auxiliary
preconditioner and bounded10MHz solve. This is assembly-only, with sheetDC R
unchanged and no new magnetic/proximity/skin model or accepted response.

**03:25 KST result:** the actual10MHz solve finished in864.344s internal/
865.75s external,29.772GB peak private. Info4/false; true relative residual
24.0243→0.00841369, maximum eliminated-potential current defect2.594mA.
Result `5fa0b4fa…`, guard `c05235fc…`, best field `02a2444e…`.
Raw Z=0.638331+j0.136044mOhm; stationary J=0.640775+j0.139026mOhm,
both unvalidated. Stop automatic continuation. Recover the field and apply
the original23 physical criteria with actual10MHz inputs. A joint
native/L04/L14 preconditioner is a possible next algorithmic change, subject
to measured fill/memory; no further factor or100MHz solve has run.

**03:10 KST preceding execution:** the frequency-specific native+L04 auxiliary assembly
completed in8.953s/0.853GB private; result `baa6b6f7…`, artifact `d67e7ab1…`.
HQ's independent block check found zero differences in all four blocks.
The audited10MHz right-LGMRES solve is now running, frozen `e50707e8…`, under
1020s/32GiB in `astra-l04-frequency-10mhz-hybrid-aux-right-lgmres-01`.
Its1MHz field is an explicitly unvalidated initial guess; every new residual
uses10MHz inputs and original exact NtD. Sol prepares the frequency-aware
field recovery and23-gate physical diagnostic while this run continues.

**Current priority, 2026-09-10:** bounded right-LGMRES completed in 842.297s,
peak private 29.666GB, at true relative residual 2.654079347e-6. Raw info4 and
the failed original 1e-9 criterion are preserved. Its 0.00398228 micro-ohm port
change (0.000491656%) is not an error bound or verified accuracy improvement.
Do not automatically extend iterations. The saved checkpoint's complete L04
field was recovered once with original ContactNtD in 7.297s/3.327GB private.
Measure actual current and power defects with all 23 original physical gates,
while retaining the unvalidated status and numerical failure separately.
The first diagnostic completed reconstruction but failed JSON output because
complex division at zero/subnormal bounds produced NaN. The bounded v2 replay
uses the identical magnitude ratio with real division; no bound or gate changes.
The corrected diagnostic finished in31.407s/2.083GB: 19/23 gates pass; both
global KCL and both power-closure criteria fail. A two-state transpose-stationary
port probe then finished in11.954s/4.084GB with only two true actions. Corrected
port variation is8.891595e-6 times raw variation, supporting the decision to
stop generic iteration while retaining all original failures and no error-bound
claim. Next: reuse source assembly for actual10/100MHz responses, checking
frequency-dependent termination/GC/RL inputs before any new solve.
Receipts and role policy are in [HQ supervision](evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md).

**23:37 KST preceding priority, 2026-09-09:** true one-action measurement completed,
frozen `0ee80a0d…`, result `73b2ae82…`, guard `8db1f233…`, vectors `5b33db63…`.
External 128.625s/25,804,451,840B private, exit0. Initial residual219.8892996994
matches restart6; optimized ratio0.941640719 gives5.835928% reduction versus
the old0.00799054%. Preconditioner3.629s/true action4.073s. HQ saved-array checks
pass. Sol accepts only a two-cycle restart12 right-GMRES diagnostic under
420s/32GiB, using original true NtD and fixed B1, with no automatic extension.
The original1e-7 trace diagnostic boundary is not a derived PI accuracy limit;
its small excess alone does not establish unacceptable port-Z error.

**23:30 KST preceding priority, 2026-09-09:** fixed-correction probe passed:
frozen `3c01ed5c…`, result `e0b494be…`, diagnostic `cfe44e65…`, external
`406ea640…`; 98.093s/15,164,801,024B private, exit0. Corrected N/T residuals
2.626e-10/3.850e-10 and parity 1.430e-9/6.759e-10 pass the unchanged 2e-8
gates; raw failures remain explicit. HQ checked saved NPZ `10dae96e…`.
One true-MNA restart6 action measurement is now running under 300s/32GiB,
using B1 on every auxiliary solve and exact ContactNtD for true actions.

**23:27 KST preceding priority, 2026-09-09:** raw auxiliary factor probe failed
its unchanged 2e-8 residual gate (normal 3.860e-8, ordinary-T 3.843e-8).
Frozen `5b234507…`, diagnostic `c6ab690e…`, failure `2e25d4a7…`, external
`0caa1d36…`: 74.094s/15,140,208,640B private, exit1; no result.json.
Saved-only diagnostics `8ea8a812…`/`7207a525…` rule out public-factor compaction
or high-degree summation alone as the main cause. Sol/HQ therefore test exactly
one fixed residual correction B1=2B-BAB, with the original gates retained.
The later preflight must apply B1 on every native solve and retain its matrix;
raw B, physical replacement and Krylov continuation remain unqualified.

**23:04 KST preceding priority, 2026-09-09:** auxiliary-only qualification
`dee4b3af…` and Sol review permit the saved H as a preconditioner candidate.
Static01 completed: frozen `0c8e32bf…`, result `c786147c…`, artifact `32d4c9f0…`,
external `101a5859…`; 2,455,688 rows/10,930,714 nonzeros, symmetry 1.631e-18.
External elapsed 9.516s/peak private 728,649,728B, exit0. HQ checked saved
hashes, contact ordering and twenty L02 off-block links. Next is one isolated
factor/probe, then one restart6 true-MNA measurement if the factor gates pass.
Exact ContactNtD stays authoritative; physical replacement and accuracy remain open.

**22:46 KST preceding priority, 2026-09-09:** trace01 is a preserved numerical FAIL
for physical replacement (`d2ba043b…`, diagnostic `bf94a73d…`, failure `8cee8c63…`).
Internal 43.469s/1.893GB, exit1, no external guard. All structural checks and
11/12 field checks pass, but raw H-lambda relative error 1.41308e-7 exceeds
1e-7. Shared-current relative error is 3.0724e-9; energy error about 4.67e-11.
Saved decomposition `75c6f180…` identifies shared-current mismatch as the main
term, with row-sum and sparse summation secondary. Preserve the failure and
threshold. Sol/HQ permit a separate AUXILIARY_ONLY qualification, then bounded
native+hybrid-H static assembly, isolated factor/probe and one true-MNA residual
measurement using the original exact ContactNtD. No physical H replacement or
Krylov expansion without the relevant measured evidence. Board accuracy unchanged.

**21:54 KST preceding priority, 2026-09-09:** exact L04 static cells01 completed:
frozen `cd925c92…`, result `fddf240b…`, NPZ `8f51d808…` (42,743,816B).
All 1,589,827 cells passed the inherited full/restricted RT0 arithmetic bounds.
Internal elapsed 9.750s and checkpoint memory 501,661,696B are not an external
guard measurement. HQ verified the saved arrays and the two-cell trace/current/
energy/MNA check `d6ab5891…`. Sol now supervises a disabled trace assembly and
saved-field equivalence helper. Reuse saved full-R current and tree dual to
recover traces; check shared/contact values, current action, energy and gauge
connectivity without a new factor or field solve. Accuracy remains unchanged.

**21:34 KST preceding priority, 2026-09-09:** compact preflight02 completed in
108.609s/25.236GB private (`6ca25a82…`, result `eeadffce…`, guard `85ca57ba…`).
Initial residual 219.8892996994003 matches restart6; raw ratio 5.396659 and
optimized ratio 0.999920095 mean only 0.00799054% norm reduction. Do not expand
this diagonal-R preconditioner to global Krylov. A saved-current check found
q^H diag(R) q / q^H R q = 236.389935 (`3d26b192…`, 1.032s/477MB, no replay).
Inspect reuse of the existing L02 exact full/restricted RT0 hybrid coefficients
for the unchanged L04 mesh. Topology predicts 1,698,803 sheet trace/contact
potentials, 2,455,688 including native. Local equivalence and static ownership
must precede any factor or new solve. Board accuracy is unchanged.

**21:00 KST preceding checkpoint, 2026-09-09:** compact native-factor probe01 passed
(`eed90403…`, result `cc764850…`, guard `d94ce6e8…`) in 73.578s, peak private
13.009GB. Deleting only SuperLU while keeping owned public factors and inputs
reduced observed memory from 12.815GB to 1.119GB. Two-RHS N/T parity was
2.599e-15/1.490e-15 and true scaled residual 4.822e-9/4.814e-9; HQ independently
checked the saved answers and permutations. Public solves took 1.391/1.528s
versus 0.234/0.282s for SuperLU. Prepare a distinct preflight02 with only this
native inverse replacement; retain the other three factors, full coupling,
scales and exact NtD under the same 32GiB/300s guard. No new Krylov run until
the saved restart6 residual has an actual measured reduction. Accuracy unchanged.

**20:43 KST preceding checkpoint, 2026-09-09:** sheet-aware preflight01 froze `bc2f8bfe…`
and stopped at86.782s under its32GiB guard `a173a7b8…`:35.398GB sampled private.
Four factors and the three-mode coarse checks passed (`34121e38…`,condition38172,
mode error4.504e-11), but no true residual/action artifact was reached. Keep the
failed execution frozen. Next test whether independently owned public L/U and
permutation arrays can preserve the native inverse while releasing the large
SuperLU owner. Measure parity and released memory in one isolated bounded probe
before another preflight; keep the other three factors and exact NtD unchanged.

**20:08 KST preceding checkpoint, 2026-09-09:** the native+L04 auxiliary static block
`ae45f216…`/`c9ddef8e…` passed:2384989 variables,9244607 nonzeros,10.031s/775MB
private. Its isolated factor `fcf48774…`/guard `6ed6ca72…` passed in60.578s with
12.808GB sampled private; LU57.413s,one inverse action0.140s,probe6.724e-9.
Replace the old native factor and measure combined construction and one action on
the saved restart6 residual next. No Krylov continuation or accuracy acceptance
is authorized by this isolated cost result; the true exact NtD is unchanged.

**19:42 KST preceding checkpoint, 2026-09-09:** coupled01 was stopped by HQ after
six saved restarts: scaled residual219.889,99.9993983% of residual squared in L04
contacts,468.922s/28.556GB private under the explicit32GiB cap. Stable field
`e0950b0c…` is unvalidated. NtD saved-diagnostic qualification03 `a583a96e…` is
complete, with probe02 exit1 preserved and no third action replay. Do not repeat
the same global Z=0 preconditioner. A sheet-aware enlarged native block using
`B diag(R04)^-1 B.T` only as an auxiliary needs static assembly, one isolated
bounded factor-cost check, and an actual residual-action check before another
global solve. The true NtD and all physical contacts remain unchanged.

**19:16 KST preceding checkpoint, 2026-09-09:** the full L04 contact bridge
`1583612f…`/`02508f2e…` is accepted: all38278 contacts, ten once-owned path
replacements,3.015s/255MB private. Saved NtD probe02 diagnostics satisfy the
original stream/dual criteria; its exit1 from a newly invented tighter gate is
preserved and a saved-diagnostic qualification is under review, without another
numerical replay. The complete contact-coupled1MHz solver is implemented and held
for Sol review, followed by a separate original-term physical validator. No new Z
or accuracy gain. Follow the current Step5 supervision record for exact receipts.

**18:24 KST retained baseline, 2026-09-09:** right correction `91e79248…` completed;
result `7341e700…`/field `960e767c…` pass info0, original residual9.9914003e-10
and all16 physical gates. Cost70.093s/24.628GB private is a saved-field correction;
all eight hybrid global attempts total1757.515s, excluding separate diagnostics.
Actual comparison `2049cf10…` gives26.240030%→26.182684% at the existing1MHz
development point. No new magnetic, broadband, holdout or speed acceptance follows.
Current/source join `fe8e063f…`/`95537c10…` covers26790 first posts one-to-one;
20 composite port leg1s carry current,73 deeper leg0 Vias remain reference-only.
Accepted L14 P1 currents `195fe8c9…` and reconstructable descriptor02 `e9064559…`
now cover all2377890 sheet-current triangles and26790 first posts. Corrected
128/64um cost census02 `06e97821…` uses the actual current meshes. Fullh128 projection
`2c86ce01…` completed in261.234s; action `b7aa36d5…` in13.531s. Its fixed-current
dZ/dlambda=0.00008843+j0.01211172ohm is large and is not a finite deltaZ.
The full h64 projection `f3cb2bea…` completed in650.625s; action `7ed771ea…`
in54.062s. The128→64um total sensitivity changes0.00252545%; all three pair
changes are below0.008%. Stop spatial refinement of this fixed-current diagnostic.
All1692409 finite currents are saved in `1b908c62…`; qualification `52f77a11…`
corrects I²L units to joules and reproduces accepted finite power exactly.
Current ranking `55850314…` prioritizes L04 DGND. Its source-contact ledger
`96c7396b…`/`6e5886f8…`, review `3b144bee…` and hidden-contact material paths
were already completed on September8; reuse them without source recollection.
Current rebind NPZ `2cf4dcb6…` is qualified by `dd9ae151…`; its post-save
print failure remains recorded, without a numerical rerun. Whole-L04 trace
cache `55048499…`, flat domain `52d6e2ef…` and pad domain `0eeaffc3…` are complete.
Flat/pad guards measured156.141s/2.722GB and134.640s/3.195GB private respectively.
All38278 drill supports are strictly covered in one connected candidate; tiny
source-coverage residues are retained. Current aggregation `6c03c997…`/`5c2fd3bd…`
completed in1.047s:390 singleton and37888 doubleton supports, with physical
component sum and aggregation roundoff recorded separately and no renormalization.
Reuse the existing mesh/current
machinery for a conditional local minimum-Joule lift. The predicted1.448M
nodes/2.126M triangles are comparable to the1437s L02 mesh workflow, not a cheap
preflight. L04 mesh driver `bd40ca85…` stopped on MESH_TRIANGLE_DEGENERATE
after947.890s/3.968GB external private memory. Its failure and raw CDT
`ec2850aa…` (1589829 free-domain triangles) remain saved; do not repeat CDT.
Saved-only diagnosis `7b950d8e…` took5.953s/2.836GB and identified two valid but
tiny triangles from near-coincident vertices on domain ring563. The second
has Jacobian condition3.394e10. No completed mesh or new current solve exists.
Review a local, explicitly conditional coalescence onto an existing source
vertex, maximum1.474e-10um, with exact changed-row mapping and geometry/contact
preservation checks. Do not relax the native degeneracy gate or claim exact
source geometry after a change. No full continuation is released yet.
Witness01 `79c75cf7…` exceeded its60s cooperative limit at110.687s/2.988GB
because it repeated unprepared coverage over all38278 contacts. No completed
local witness was saved. Witness02 must bound checks to the changed ring and
its AABB-selected contacts, save early, and avoid full-domain Hausdorff; it is
not a larger-budget retry. Stream/project/cross provenance changes are reviewed
code only, retaining the explicit conditional geometry approximation.
Witness02 `4226a917…`/result `3e097e70…`/external `6e498b27…` then completed
in5.531s/2.874GB private. Ten coordinate occurrences in five triangles change;
only807731/976112 collapse. Remaining minimum area0.006032677um² passes both
unchanged native area gates. One nearby contact is strictly covered before/after;
the other38277 are AABB-disjoint. Sol independently reproduces the changed-domain
SHA `8dbfbb75…`, same bounds and32422 holes. Prepare a single saved-CDT native
continuation with these exact pins; no qualified L04 mesh/current exists yet.
Mesh02 `81631267…` stopped after774.750s/6.535GB private: the wrapper wrongly
rejected a legitimate second native stiffness call. Preserve failure `6884c1f8…`
and guard `c741aab5…`. Saved native geometry/contact checkpoint `6f2f396f…`
contains1448429 nodes/2125719 triangles. RT0-only qualifier `510e5673…`, result
`b0bfb679…`/guard `1749c8d8…`, passed in3.032s/250.2MB without repeating CDT,
coverage union or P1 stiffness. No completed P1 operator is claimed.
Stream `65c48fe1…`/result `3e5417fa…`/guard `016bd303…` passed in41.547s/3.684GB
private, including1.141s factorization of644870 closed-stream unknowns.
Minimum Joule0.000474273954W and bilinear q^T Rq=0.000469788210−j0.000018659347
ohm*A² identify a material omitted-sheet resistance diagnostic, not a finite
circuit deltaZ. H128 projection `dfabd258…`/result `6f623766…`/guard `8f5bb053…`
completed in197.687s/795.3MB;1589827 triangles, no renormalization. Three new
cross pairs `b792101c…`/result `1a23bb9d…`/guard `5d8e4ddf…` completed in19.062s/
698.9MB. Added bilinear -6.824930375e-9+j2.755657400e-10J reverses the old-only
cross sign; combined cross is -4.897290484e-9+j2.614920166e-10J. Negative
off-diagonal terms are legitimate. The jomega value is not finite deltaZ.
Sol accepted saved provenance, pair sums and scope without replay. Stop this
local diagnostic; no h64/h32 or current numeric worker. Review the smallest
actual coupled-circuit route using saved L04 currents/dual potentials. Explicit
geometry approximation and original1MHz/1A currents remain bound.
Adaptive-mesh audit: L02/L04 use constrained CDT/refinement0 with no common
spatial size policy; L25's actual local longest-edge process stopped after6
steps at20k new nodes and10.99298% DC gap/P1, above1%. Reuse that refinement
only after adapting the boundary-only stream contract. Do not confuse h128/h64
magnetic grids with conductor adaptation or local DC convergence with broadband.
Next outcome gate is same-model actual1/10/100MHz complex-Z error and runtime;
rebuild frequency-dependent terms and solve at each frequency. Keep the small
26.466013→26.240030→26.182684% gain separate from the23.762889% magnetic candidate.
Other current-path cancellation, magnetic self ownership and feedback remain
necessary; no new board response, accuracy improvement or full-return acceptance follows.
No further local L02 refinement or repeated failed global solve.
See the [current HQ evidence table](evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md).

### Historical execution records

Present-tense process statements below record their individual checkpoint, not the current state.

**13:12 KST priority, 2026-09-09:** the prior P1/P1/RT0 combined R/G/C field has converged and passed
independent physical replay, but its 1MHz error only improves26.4660% to26.2400%.
No new magnetic candidate or broadband acceptance follows. P17 action and saved-source QA are complete;
actual G6078-cell D/R and3 minimum-Joule interface lifts are generated.
See the [current HQ evidence table](evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md).
At1MHz the17 same-mesh all-closed residual/current8.41789e-5 is already small,
while actual board error remains23.7628886591%. Next bind these operators to
the actual global1MHz terminal system, retaining all independent pin/cut/next-via
currents and nonoverlapping ownership.100MHz current and integration gates
remain open; no further blind local FMM expansion is launched at this checkpoint.

The full real-cell RT0/P0 hybrid coefficients are saved. Boundary replay
`f5b6874b…` reconciles all42 excluded owners against the already-bound composite
legs and two inherited leaves, and checks all76,139 native and40 split-leg
terminal joins. Zero normal sheet conduction on817,918 exact source-ring
facets is now an explicit conditional finite-2D law. It is not a full3D boundary
claim. Conditional hybrid assembly `604a44f1…`/NPZ `5ab5ba38…` now has
3,782,134 unknowns after gauge removal and16,784,042 Y nonzeros. It completed
in22.547s/2,897,514,496 private B. The actual P1 auxiliary transfer is saved;
Sol supervises its bounded iterative-solver implementation and coefficient
review before a new global field; no direct-LU repeat is authorized.
The implementation has been reassigned to a fresh Terra worker after three
incomplete handoffs. Astra's final physical validator `fd28d5a3…` passed its real
transferred-P1 replay check `35c73efb…` in16.532s/1.913GB. Local RT0 equations,
cell KCL and original-source arithmetic agree, while the unsolved transferred
field correctly fails global continuity, reconstructed-global cell KCL and
both physical/matrix power closure. First hybrid solve `ae0fb71e…` stopped
with info20/final residual9.05503e-4 in427.391s, peak23,183,859,712 private B
within600s/24GiB. Field `9fd9fae3…` remains unvalidated; physical gates were
not reached. L02 dominates the remaining residual. Diagnose coupled internal
trace relaxation before any further solve; a4.531s SGS-only action probe
`1aa456b5…` supplies cost/symmetry evidence but no convergence improvement.
Restricted auxiliary diagnosis `c28f1214…` found94,983 zero transfer columns
and a0.176722 relative Frobenius mismatch with the old P1 block. Actual
Galerkin pack `fc12864c…`/`c8b0455c…` removes those zero columns only,
retaining761,791 independent coarse variables and the same physical operator.
The endpoint graph with38,856 exact identity anchors proves full column rank
of this transfer. Frozen wrapper `d56c3cfc…` completed its actual continuation
at11:25:06KST, PID31288, output `astra-l02-conditional-hybrid-galerkin-lgmres-01`,
in341.297s/21,115,711,488 private B under600s/24GiB. G factor7.266s/probe1.60e-14
and all coarse gates pass, but info20/final residual5.03053e-5 fails1e-9.
Save unvalidated field `7a259482…` and diagnostic `3ad844ac…`; no physical
validator call or accuracy comparison. Prepare symmetric forward/coarse/
transpose-post relaxation around the exact Galerkin correction, preserving
all physical equations and acceptance gates. Its checked wrapper `0324a899…`
ran11:47:10–11:54:36KST, PID42376, output `astra-l02-conditional-hybrid-galerkin-sgs-lgmres-01`,
in445.406s/21,613,944,832 private B under600s/24GiB. All factors/coarse checks
pass, but info20/residual2.98181e-6 fails1e-9. Field `1a8efde7…`, diagnostic
`08f0a9e1…` and external `d8047398…` are preserved; no physical validator or comparison.
Root probe `561175a0…`, PID7840, measured the actual conditional L02
principal-block COLAMD factor alone under180s/24GiB, using1,694,809 rows and
7,917,807 nonzeros. Result `612770df…` passes in13.531s/11,798,323,200 private B,
with78,176,765 LU nonzeros,9.779s factor/first-probe and0.250s single solve.
It differs from the failed full mixed LU. Sol reviewed its scope and replacement
capacity. Sol completed the exact-L02 wrapper after incomplete Luna handoffs.
Root reviewed full source/preflight `52e51e48…`, preserved disabled `28c21a87…`,
and released `c1ab14f8…`. Actual PID42928 stopped after65.844s at the24GiB
memory guard:25,770,201,088B,397,312B over. All factors/coarse checks pass;
callback2 residual2.26518e-7 is not a final field or physical acceptance.
Released inner8 follow-up `87366a4f…` also stopped at90.172s/25,866,141,696B
(external `5fb05c75…`); narrowing the basis and freeing27.1MB maps was insufficient.
Installed SciPy leaves the completed vs/zs basis alive during the next restart.
Root's minimal deletion after their last use passed14-callback bitwise parity
for both augmentation storage modes, final x/info and every outer vector.
Sol independently accepted disabled `276f4c91…`/preflight `fe46002b…`.
Released `e42a44d4…`, PID38244, completed250.734s within the600s/24GiB budget:
peak25,198,362,624B,571,441,152B under the cap. Memory lifetime is fixed for
this run, but info20/final1.20793999e-9 still fails1e-9;155 operator/M actions.
Final `d5bd9859…`/JSON `e77b5f70…`/external `d11245ab…` remain unvalidated.
Sol checked that the20th update improved the residual2.38x just before the
outer limit. Reviewed continuation `59192b9e…`, PID6672, is running only four
outer cycles from that completed final field, under180s/24GiB with unchanged
operator, exact factors, lifetime patch, inner8/outer3/rtol1e-9 and physical gates.
Disabled `2737c253…`/preflight `82053e7d…` preserve the reviewed contract.
That continuation has now stopped:66.578s/25,166,741,504B, info4/final1.26737e-9;
no physical/reference call. Do not repeat it. Saved-field arithmetic diagnosis
`c90e061e…` found a~5.96e-12 action difference but essentially unchanged total
residual, so row-difference summation is not adopted as the remedy. Reviewed
right correction `91e79248…`, PID19156, is now running A M z=b−Ax0 with fixed
M and original-coordinate final gates, four cycles/180s/24GiB. Its19-variable
noncommuting dense check passes both correction/original residuals at5.19e-13.
The actual4005 source-contact delta `9140efec…`/result `4b34dcac…` is complete
in0.671s/observed1.561GB (cooperative45s/2GiB; no external guard receipt).
All25,812 G edges reconcile as21,787 endpoint complements,4005 exact saved
L02 contact bindings and20 existing composite legs. Their source delta is now
`01cfbb2c…`/`b47b6031…`:40 unique legs,93 unique Vias and5 unique ideal traces,
0.672s/observed1.612GB cooperative. All20 port leg1s contain one TOP–L02 Via.
This is source/circuit identity, not electrode, current or magnetic validation.
Strong-component graph03
`58042d13…` and batched inverse `46f7ec6e…` establish small blocks (maximum17),
89.8MB stored inverse and0.012s application. The latter completed in8.516s/
1.186GB, replacing a failed4GiB whole sparse-LU probe. Its one-action actual
residual ratio2.2124 provides no improvement evidence; keep this smoother
disabled; do not repeat another unmeasured smoother variant.

Native interface map `509f52f9…` now binds all 1956 selected first vias and
records the other 24,834 G-port finite edges. In the saved native baseline,
those outside edges carry 0.962089 A of the return. This is not the latest
23.7629% candidate field or a new-model current prediction. Keep the entire
external return and single ownership. The baseline selected-series-impedance
scale sensitivity is only 0.00120522 relative to port Z; it is an infinitesimal
diagnostic, not a replacement error bound. Evaluate reuse of the existing
global L02 distributed-sheet and L25 RT0/P0 assemblies before spending more
work on local post-only refinements. The combined global R/G/C solve is now complete.

Full saved L02 RT0 R/D/B `eb7357b1…` is now constructed: 1,583,840 free cells,
3,095,567 current DOFs, all 38,856 aggregate contacts, and 817,918 retained
exterior flux supports. Guarded runtime 29.547s, sampled peak private 1.398GB.
Actual-face and constant-current check `d4eb8d1b…` passes; no new mesh was made.
The existing 2D equipotential contact approximation remains explicit.
Saved G/C cell transfer `597d3e0f…` now recovers all 1,041,341 cuts/2064 owners;
the saved P1 current action agrees to 5.64e-14. Conservative RT0 lift
`90ac2ac7…` preserves all cell/electrode totals but costs 20.79698 times the
P1 Joule energy. Actual sparse-R attribution `662f392f…` confirms the excess;
90% lies in 452 cells. Changing only the local objective worsens it to
24.03061 times. This local lift is not a global minimum and is not accepted
as an accurate magnetic source. The complete coupled closed-stream system
`8c3116db…` spans 654,954 modes including all 33,259 insulating boundary
components. Exact D/B annihilation, connected graphs and nullity prove
completeness. Saved-system direct solve `9042844e…` costs 10.031s/3.796GB
(LU 1.125s) and reaches a 2.45e-15 actual gradient residual. Minimum loss
still equals 5.125125 times P1 under the same fixed cell/electrode currents.
Thus the local recovery excess is reduced, but the discretization gap remains;
neither result is a board-impedance error bound or mesh-convergence acceptance.
Independent actual-face/affine-R review `488140ee…` confirms complete fixed-load
finite-space stationarity at 1.75e-13 and energy agreement at 7.54e-15.

Combined L02/L14/L25 sparse Y/R/B `13594d04…` is complete in 25.547s,
sampled peak private 3.316GB. It has 2,944,100 mixed unknowns and one owned
1,692,409-branch finite list; native source-action recollapse is 6.65e-13.
Original source categories `0715454b…`/NPZ `f9253786…` preserve all seven
sheet/G/C/termination matrices separately. Together with the owned finite
branch list, they reproduce Y action to 1.09e-12 without subtracting large
finite terms from Y. Cost 25.532s/1.988GB sampled peak private.
Sol's first actual field solve ended after 214.219s, sampled peak private
22,309,175,296B, under the 300s/24GiB guard. Four block factors and rank-three
balancing passed, but 100 GMRES iterations left KCL 5.55e-6A above 1e-7A.
Unvalidated field `00cf8451…` is preserved and excluded from accuracy comparison.
Saved residual attribution `edc0af47…` places 98.17% of squared KCL norm in
native rows, led by the G port and L02 anchor. Moving all three original sheet
anchors into their own blocks also failed after 100 iterations: 212.703s,
22.329GB, KCL 4.8542e-6A and driven-power error 6.5693e-9 ohm. Preserve the
second unvalidated field `2d1ac88c…` and diagnostic `42d8ecfa…`.
That field was reused as an explicitly unvalidated initial guess for installed
[LGMRES](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.lgmres.html),
retaining three corrections between restart cycles. The first attempt
(`853f193f…`, inner20/outer3/max12) ended in 339.297s/23.499GB: KCL 9.67e-10A,
constitutive 5.19e-19V and power closure 3.09e-13 ohm pass, but info=12 and
scaled residual 3.9657e-9 still fail the fixed 1e-9 convergence contract.
Independent full source-action replay `3aaa1e7d…` confirms that qualified
verdict. The prior field `a67921f7…` remained only an initial guess. Its bounded
continuation `832841f9…`/field `c7360f92…` then passed info=0, explicit final
residual8.98338e-10 and every physical gate. Independent source replay
`28965a25…` confirms KCL4.96e-10A and power closure2.70e-13ohm. PID42684 is
finished. Cost147.656s/22.204GB applies to this continuation; all four iterative
attempts total913.875s, excluding failed direct and ordering probes.
Account for two concurrent Arnoldi bases and retain per-cycle checkpoints.
The separate L02 MMD-ordering trial stopped at 120.406s before a factor was
completed (9.446GB sampled private, guard `40969056…`); retain COLAMD.
Terra's audit found no earlier measured full combined LU. HQ then actually ran
the unchanged existing direct solver on 2,944,099 unknowns/16,697,385 nonzeros.
The 600s/24GiB external guard stopped it at 197.047s for memory, before any
field was produced: sampled peak private 27,907,796,992B, working set
8,993,251,328B. Preserve `astra-combined-saved-direct-guard-01/external-budget.json`;
do not repeat this resource failure or infer capacity from a smaller matrix.
Separate replay arithmetic diagnostic `d6703eb7…` finds 4.96e-12A differences
within conservative rowwise roundoff envelopes; the stored matrix difference
acts at only 2.38e-15A. This does not excuse the actual GMRES failure.
Frozen comparison `bb0e8daa…` finds26.466013%→26.240030%, only0.225984percentage
points and0.853864% of the remaining reference gap. The23.7629% magnetic candidate
has different physical terms and is unchanged. Actual new currents `3be7738c…`
still route−0.96435117−j0.00027425A through24,834 unselected G-port branches.
Next assess the minimal source-owned coupled L02 RT0/P0 G/C replacement using
existing operators and full return boundaries. Do not repeat fixed-load recovery,
blind local FMM, or R-only tuning as a substitute for global mutual coupling.
No new numeric worker is running; broadband and3D magnetic qualification remain open.

**05:10 KST update, 2026-09-09:** incremental14 actiona88bfe63… completed
418.391s/4,447,072,256B sampled peak private; no HQ FMM remains running.
The14 energy rule difference1.433010e-4 still fails5e-5. At100MHz, however,
the four-contact rule difference1.323854e-5 is much smaller than the7-to14
admittance change4.173153%. Saved closed-response diagnostics take0.594s
and do not establish current-space convergence. Sol now supervises guarded
14 pair attribution and actual-source action QA while completing the G
neighborhood's volume cells/400 side mates/sparse D/R. Prioritize these
physical and current-space gaps over another blind quadrature/FMM repeat.
The05 messenger execution completed; original board accuracy is unproven.

**05 KST report input, 2026-09-09:** targeted correctiona3f465db… passes the
expanded7 empirical rule gate at1.999686e-5 after1040 new touching references
and92,332 additional member replacements;53.062s/649,760,768B sampled peak
private, no FMM repeat. All150,584 pair corrections and5304 self blocks remain
available as full-affine operators. Updated fourteen-current space7174858a…
keeps old7 exact and adds7 closed witnesses; R identity7.89e-13. Incremental
FMM is running for only these new7 columns per rule, with actual two-direction
cross verification and full fine actions saved. Do not predeclare14-space
numerical or physical convergence.

G partition048491fd… is qualified only as solid/surface ownership. The next
required object is actual residual-volume cells in the source-derived
130x450.4um two-post neighborhood, matched across400 r30 side triangles,
with sparse D/R and exterior continuations. It is being implemented by Sol.
Conditional1MHz board error23.7628886591% and unverified new3D10/100MHz board
response remain the original goal's open evidence gaps.

**Latest execution, 2026-09-09 04:23 KST:** full seven-current action0c7305ad…
completed442.406s/4,350,021,632B sampled peak private. All12546 test rows are
stored. Strict source-composition QA8f60e377… passes, but7-space empirical
rule difference0.190550% fails0.005%; the old5 agreement cannot certify the
new vertical modes. Saved-action closed complementb4172c8f… takes0.328s and
identifies substantial omitted current forcing: unit-modal local RL residual
R-dual norm0.510470 at100MHz. This is not a board/current error percentage.
Next retain those independent closed-current witnesses, attribute new7-space
near-rule differences without FMM, and complete the physical G single-owner
neighborhood and its interface maps. No heavy root process remains running.

**2026-09-09 04 KST execution checkpoint:** group correction6db19ad7… extends
qualified replacements to58,252 pairs and reaches0.00162432% Jacobi27/XG31
updated-energy difference in the old five-current space, below0.005% empirical
agreement. This closes this numerical discriminator only. Preserve all4762
raw full-affine references via registry29beacba…; no repeat Green integration
is needed when projecting them onto new currents on the identical mesh.

Next actual steps are the seven-current full-fine action (running,600s/24GiB
external guard) and a conforming actual G artwork/residue neighborhood.
Seven-basis source maps and physical R pass strict independent433de3e7…;
the old5 raw columns remain unchanged and root applies their saved whitening
only for magnetic comparison. Save L@Q for all12546 fine rows and both rules,
then evaluate retained complementary current tests from those actions.
G ownershipb8910339… adds only trace residue outside the existing artwork;
978 pads,1691 traces and external/lower interfaces must retain single ownership.
All fine/exterior/circulation/charge, return and material complements remain.
The original1kHz–100MHz source-derived board accuracy goal is still open;
conditional1MHz error23.7628886591% has not changed.

**Executed2026-09-09 follow-up:** touching1292 correction8f9b6611… completes
35.265s; non-touch1934 correction921693f6… completes48.172s, reusing the former
matrix. Corrected-energy q2/q3 differences fall38.026%→6.5794%→0.569466%, still
above5e-5. Keep qualified pair references and identical point-block subtraction;
do not infer physical error reduction or grow the expensive raw FMM point set.
Non-touch attribution1a89ccc3… now uses saved non-touch minus actual sampled
matrices. k128's residual is0.2448% in the original q3 metric; kNN is not complete.
Next verify rigidly congruent pair reuse to make all-near integration practical,
and cover far interactions independently instead of blind rank-count growth.

The978 vertical plus4665 lateral P-network Joule metric9cd7cfdf… is assembled
in2.703s producer time. All owned-post cross terms and independent TOP/LOWER
flux maps remain. Independent readback77995a2d… matches energy to2.581e-15 but
had an unconditional ACCEPT label. Fresh reviewd566833a… now executes fixed
gates for all933 edge/978 post owners, geometry and cell energy1.363e-16.
No G/return/field/Z closure is claimed.
Current conditional1MHz board error23.7628886591% remains unchanged.

Rigid geometry reuseac61f159…/independenta723e510… enables31002c1a… to extend
existing refs to17324 pairs in3.359s with no new integration. Remaining updated
energy difference0.475824% still fails5e-5; do not blindly add thousands of refs.
Polynomial880915c3… verifies positive Jacobi and14-point XG rules; near errors
remain diagnostic. Full-joint compact comparisond554d516… completes195.688s,
with3 FMM channels per call and sampled peak private4,112,846,848B (guardba86dc9c…).
Every original cell, self and17324 qualified pair corrections remain. Updated
energy differences XG14/Legendre27 and Jacobi27/XG14 are0.066038%/0.071188%,
still above5e-5. Strict HQ reconstruction62f8c3ae… verifies every self/pair point
correction, raw reference projection and propagated rigid map; earlier01/02
saved-only audits are diagnostic. Degree7/31-point comparison5d578017… completes
164.656s at4.244GB peak private, but Jacobi27-relative updated difference0.132242%
still fails. HQ review04 confirms actual source/self/pair reconstruction. Next
attribute the remaining difference by uncorrected pair/group, with no new FMM.
In parallel Sol advances cached source-ground contact/junction assembly, retaining
all external continuation interfaces. G assembly is one missing physical object;
it does not close near integration, P fine-space, dielectric or full-board gaps.
For local vector frequency dependence, use bound5f075e3f…/review6e839bfd…:
static L plus its exact rank<=3 `-ik` term has R-normalized `omega*remainder`
bounded by1.3314e-6 through100MHz. This avoids repeated local-frequency FMM;
it does not waive the static-integral or charge/fine-space gates.1GHz exceeds
the same budget and remains a separate diagnostic.

**Executed2026-09-09 02시 report:** vertical transporta644574c…/review6b17350b…
passes with actual484 TOP/96 r20-lower faces and0.861067254mOhm owned energy.
Combine its978 P-post coordinates with4665 lateral coordinates, preserving
vertical/lateral shared-post cross terms and independent TOP/LOWER flux maps.

Full static point-growth probe64a4c3e… is stopped: q2/q3 change1.6986% and the
q4 process reaches46.87GB private memory. Keep its frozen q2/q3 matrices; no
larger uncompressed repeat. No-FMM attributionc1845a59… locates416525 touching
pairs in21.281s. Baseline-energy-normalized total change38.026% exposes weak-mode
error;208 pairs carry90% of its empirical L1 touching estimator. Qualify those
actual pair integrals and attribute nearby non-touching changes next. This is
still a numerical-integration prerequisite; board error23.7628886591% is unchanged.

**Executed2026-09-09 01:45KST:** actual-separated Galerkin sampled refinement
d57bec54… passes in24.468s: three source placements, every1kHz/1MHz/100MHz
block, n8/q3→n12/q4 max current4.280e-9 and charge5.968e-7; reverse2.005e-14.
It is not a near/self gate. Lower-contact622f7388…/topology21621048… proves the
existing96 contact/192 complement bottom triangles already match the actual
r20 source96gon. Reject the old floating-equality remesh inference. Sol is
preparing a TOP-to-actual-lower transport lift; HQ is measuring full-joint
inter-cell static integration error with cached self blocks and all pairs kept.

**Executed2026-09-09 01:30KST:** projection04/strict reviewdceb70d3… now pass
with correctly rebuilt affine coefficients and free exterior currents. Actual
933-edge single-owned R assemblyd1257d9c…/review3c5ff104… passes in3.062s,
including888 shared-post cross blocks. This is4665 lateral lift coordinates,
not the complete terminal/vertical current space.

Source compression5e3f87ef… passes separate frequency/radius/mode-group gates:
178092 q3 points to512 proxies; n8 setup2.016s, full q4/static checked experiment
29.125s. Max real error5.724e-6 and stable imaginary/tail4.538e-11; n4/n6 fail.
Next qualify two-sided separated Galerkin interactions and exact lower-contact
face ownership before vertical lift construction. Keep near integration and
complementary spaces; do not infer a full-operator or board accuracy result.
The1MHz board error23.7628886591% and new3D10/100MHz response gap are unchanged.

**Executed2026-09-09 01시 report, cheaper uniform mixed-row candidate:**
Touching review1baa51ca… accepts the saved discriminator's scope and checks
eight worst higher-order pairs, assembled-row change<=7.876e-7. Uniform-static
8859917a… and actual full-retarded q8 run22729902… replace automatic composite
depth growth for these selected rows. The latter uses1600 targets instead of
34704, completes86.453s including22.016s direct-reference work, and retains
every source and retarded term. Near correction falls175.594s→8.313s with
cached165 pair references. Max uniform/adaptive row difference7.060e-6;
max same-outer source-row error1.383e-5. Keep the5e-5 gate and the explicit
limits: no full-operator, source-retardation-quadrature or board qualification.

Sol directly owns the div-free projection repair after Astra found stale
unprojected affine coefficients and unchecked PASS in the Luna draft. No
consumer may use the rejected arrays. Require fresh coefficient reconstruction,
expected input hashes, Hermitian energy/idempotence, smooth reproduction and
full scaled KKT residuals before using the physical stress field.

Next assemble the actual933-bridge/978-post power-chain Joule metric with5
conforming joint modes per bridge. Use one physical post/bridge ownership
map; neighboring joint modes share a post and require their cross Gram terms.
Prove equivalence to the sum of single-owned material energies, not a block
diagonal sum of local joint models. Retain45 chains, source translations and
all physical source IDs. This candidate omits vertical/lower and complementary
charge/circulation modes; it is an assembly discriminator, not a physical
zero-boundary model, field solve or PowerSI accuracy result. Root supervises
the algorithm and Sol supervises implementation/quantitative review.

**Executed2026-09-09 00:40KST, selected-row outer integration decision:**
Adaptive95-parent bf848831… reuses unselected patches and retains every source;
its235.281s result still fails5e-5. Do not keep increasing all outer depths.
Static self-only substitution exposes cancellation with touching neighbors.
All165 actual touching vector/scalar pairs pass separate forward/reverse
integration gates in6.531s. Canonical3e65b705… then reuses these pair references
and saved mixed samples: fine vector/scalar last changes3.157e-6/1.349e-5,
summed last-patch changes9.474e-6/3.199e-5 both pass the unchanged5e-5 gate.
Subtract the actual analytic-near/q3-far static source contribution before
adding the high-order pair once; retain the entire regular retarded part.
Independent Sol/Terra ownership, scaling and sampled higher-order review is
running. Pairwise gates alone are not a proof under assembled-row cancellation.

Shared5-trace interior lifts have strict review8e3780a4…: all32 common faces
are continuous, D residual8.41e-16A, zero-net columns have zero exterior trace,
and the constant basis column has explicit[-1,+1]A TOP-electrode balance.
Full R_ib KKT residuals<=5.50e-15; no Green/field/board approval follows.
Next prepare a sparse-R projection of the existing fine stress current onto
homogeneous-Cu cellwise D=0, leaving every exterior face free. This is a
physical stress space and conditioning discriminator, not deletion of
surface/material-interface charge or independent contact states. The previous
arbitrary RT0 stress remains valid numerical evidence. Avoid a dense Schur
complement or global minimal-cycle basis. Root supervises formulation; Sol
supervises Luna implementation and quantitative independent checks.

FMM precision-cost2c5369db… measured eps1e-8 versus the frozen eps1e-12 point
action at100MHz: maximum difference1.735e-10,23.125s. The old run used1 thread
and the new run4, so speed cannot be attributed only to tolerance. Defaults
remain1e-12; lower-frequency tiny imaginary components and new target sets
are not covered by this comparison. Product runtime remainsv0.23.1 unchanged.
Latest conditional board1MHz error23.7628886591%; no new3D10/100MHz response.

**Executed2026-09-09 00시 report, full mixed point correction:**
Mixed source action7f7b3eb0…/strict review4fd67e56… passes a matched-point
5e-5 gate with maximum1.084e-5. The full retarded FMM owns every point pair;
only singular static near integration is replaced. Composite430821f1… now
tests finite -ik restoration at two actual source coincidences, but outer
depth5→7 fine-current vector/charge changes2.869e-4/4.515e-4 remain STOP.
Keep source-integration and outer-integration gates separate. Next use saved
parent/child integrand contributions to target subdivisions; measure reusable
FMM precision cost separately before changing any default tolerance.

Source-adjacent trace audit991f77da… rejects zero extension of7 local gradient
seeds through an actual next-bridge interface. Build common normal-flux trace
coordinates or a conforming constraint projection before global reduction;
the existing local seeds remain valid as local candidates/preconditioners.
This does not impose zero physical boundary flux. Complete physical source
continuations, charge/contact solve, skin/current-space convergence and new
board1kHz–100MHz validation remain. Latest conditional1MHz23.7628886591%.

**Executed2026-09-08 23:45KST, full self assembly and source-action comparison:**
All9180 scalar supports and5304 vector self blocks bc5cef31… are complete;
strict resume/physical/higher-order review e273d416… replaces the scope of
schema-only review02. Actual-mesh outgoing point FAR35d203be… has independent
saved review c1af3c31…. Use the accepted `_qualified.py` apply helper; the two
unsuffixed Luna drafts remain rejected and are not in accepted imports.

Full-source static selected-row736e14dd… is a gate STOP: q8→16 fine-current
vector/charge changes8.97e-5/1.297e-3 exceed5e-5. Do not hide this with smooth
seed-only checks. First isolate accelerated source integration at the same
saved target points using FMM plus exact static near minus identical static
point near, restoring finite coincident -ik remainder. Then adapt positive
outer integration to geometry. Root owns mixed correction and outer accuracy;
Sol owns qualified outgoing helper and scoped saved verification. Latest
conditional board1MHz23.7628886591% remains; no new3D10/100MHz board response.

**Executed2026-09-08 23:20KST, complex outgoing adapter and integration ownership:**
Saved large-source complex mixtures e46bbf93… and independent direct-subset
review c14a43b2… qualify `conj(hfmm3d(+k, conj(q)))` over1k/1M/100MHz.
Retain negative-k04 as a failed path; do not loosen its fixed gates or infer
vendor internals from the measured discrepancy. The efficient current space
now has strict physical review af031ef6…. Complete pair census eecf7faa…
records all9180 charge supports and their symmetric near/far partition;
all1,237,514 ordered topological touching pairs are covered. Radius2 still
needs integration-error qualification and has too many near pairs for casual
dense production assembly. Selected actual charge blocks8bd9965a… pass.

All5304 current-cell and9180 scalar self diagonals are being assembled with
per-entity convergence checks and saved failure data. Next combine the tested
outgoing action with analytic near minus identical point-near subtraction,
then expose complete fine-current/charge residuals. No copied joint R double
ownership, artificial zero-flux cuts or loss of source lower continuations.
Conditional board1MHz23.7628886591% remains; no new3D10/100MHz board response.

**Executed2026-09-08 22:55KST, efficient source mesh and current-space migration:**
Boundary-only source mesh322b6ded… and independent surface-union comparison
c535b5b4… preserve source geometry with5304 tetrahedra/64 mates. Current-space
44fe27c9… builds the fresh12546-face sparse mass/B and14 initial energy-scaled
transport/boundary/circulation seeds in0.704s. Old mesh results remain frozen;
all fine exterior current and charge states remain available for enrichment.
Selected real selfd2b12c07… and near paire1cc6e3e… qualify static singular
integration only. Full self/near ownership and retarded/scalar terms remain.

The12-tetra far control0464879f… passes its full-complex and tiny-imaginary
tests. Its size does not prove FMM scaling. The54k-point successor reached a
gate STOP; save all per-frequency outputs before any further gate, identify
the failed metric, then choose the smallest justified numerical correction.
Do not mask low-frequency imaginary error by flattening all frequency norms
or relaxing tolerances. Next bind the efficient current space to the validated
near/far action, independent contacts/charges and retained lower/global source.
Latest conditional board1MHz23.7628886591% remains; no new3D10/100MHz board Z.

**Executed2026-09-08 22:28KST, source joint and sparse current seeds:**
Source union150cef66…/review5aadfe85…, conforming joint6cea92e1…, physical
sparse mass9a4974ec…/reviewfa10983a…, and three transport liftsf530c60a…/
strictd0ebc565… are available. Preserve978 unique posts and933 bridges;
overlapping joint bases require cross terms on shared posts, not summed joint R.
The32 bridge-top faces are retained exterior states, not pad electrodes.

Boundary seed01 was rejected: fixed boundary currents require the R_ib j_b
cross term in the interior KKT equation. Corrected95a690b2… and strict128d6c4e…
verify full R stationarity and normalized energy orthogonality. The8 boundary
extensions and3 rotational loops remain seed functions, not a complete14-mode
field reduction. Keep all fine current/charge states for residual enrichment.

Source Node recoverya69ddd57… resolves3912 selected Contact defaults to1.
L02 via census24b32dde… finds one DR-0203_60 continuation toL03 for all1956
selected pads among24919 all-net candidates, without foreign contacts. Independent
review is pending; traces, artwork and later layers still need full assembly.
Do not terminate these real lower connections. PatchSignal's pkgshape is a metal
shape reference and provides no verified finite dielectric outline.

Next implementation: apply the existing accepted FMM3D full-complex kernel to
true3D tetrahedral quadrature; validate far action against direct identical
quadrature and higher-order quadrature separately, with self/near corrections
owned once. Then use full fine-space residuals to assess reduced modes. No old
coupon or two-sheet board rerun. Latest conditional board1MHz23.7628886591%
and this3D candidate's10/100MHz validation gap remain unchanged.

**Executed2026-09-08 21:35KST, source width semantics and split boundary flux:**
Recovery f58a446f… verifies all48 conductor defaults and3755 selected Trace
records. Apply layer inheritance to217153 omitted widths through an immutable
research overlay;34 selected L02 defaults are23um and outside the Device bbox.
Casefolded source attributes contain no taper/thickness/conductivity override.
Instance geometry f6655f84… and other-TOP pad/via census3df9e475… pass saved
independent reviews1cb3dd2b…/ee4d3692…. The latter's incomplete01 stays rejected.

Cut-face artifact d0e18560…/review0a416eff… binds62232 actual contact intervals
to shared template side faces with exact clipped support and constant-current
flux measures. Both contact/complement rows remain retained until opposing
conductor geometry and current DOFs exist. Do not substitute an area fraction
for cut-support Green integration or claim a conforming volume remesh. L02
trace review4d25904d… accepts inherited widths and nonincident-node contacts.

Immediate work: union the actual933 horizontal130x45um power traces with978
selected pads, reuse one bridge geometry, and assemble opposing interface
ownership. Keep additional TOP source copper and external/lower connections
in scope. Then form sparse geometry-aware current/charge coupling; avoid
uniformly replicating millions of tetrahedra or adding coupon orders. Latest
conditional board1MHz23.7628886591% and this candidate's10/100MHz validation
gap remain unchanged. No product behavior or release change has occurred.

**Executed2026-09-08 21:00KST, source geometry to retained interfaces:**
Conforming post template0b6a1507… (0.078s) supplies2592 positive tetrahedra,
source TOP/via/L02 dimensions,480 TOP electrode faces and retained side/lower
tags. Terra accepts its volumes/orientations/manifold checks. The circular
area deficit0.0713794% is a geometry measure, not a field error. Keep analytic
disc area separate from actual polygonal face area. Translate one shared mesh
to actual source pads; do not replicate millions of field unknowns by default.

TOP traces91f1fa71… (2.5s; reviewd1da7311…) and ordered source artwork
b3c1eb99… (3.593s; review1a3ba56c…) establish side connections for all1956
selected pads. In particular,19 pads without a trace contact connect to source
DGND artwork. Next bind other node-pad/via overlaps and lower-layer interfaces,
then partition actual retained faces and form source current/charge coupling.
Whole TOP100um pad remains a declared ideal-fixture convention; no exact
PowerSI contact-area equivalence, isolation, physical convergence or new board
accuracy result is implied. The1MHz23.7628886591% result remains the latest
conditional actual-board checkpoint.

**Executed2026-09-08 20:33KST, actual source radius interpretation:**
Source recovery8862633b… and independent81c2c20f… bind all98 PadStack blocks
to their accepted cache hashes. Official2025.1 syntax identifies the first
radius as outer, so the cache's historical drill-diameter name is misleading.
All98 omit InnerRadius; DUT100um pads have no offsets. For the next source-model
candidate, declare the official blank-plating solid-via convention explicitly;
do not infer manufactured fill or subtract an empty40um drill footprint.

Bounded metadata scanaa5c8c4c… (1.485s) plus section readback962b61b1… confirm
empty Outline, CutOutline and DielectricBlock sections. Large geometry and
Node/Trace/Via sections were skipped. Source Wave3D OuterBoxSize is not a PowerSI
dielectric outline. Keep lateral-domain uncertainty explicit and proceed with
actual source conductor/contact assembly under the one-Device-port map; no
arbitrary crop or nearby-plane-only return is authorized. Sol supervises Terra
on contact semantics and Luna on the minimum reusable assembly. Actual-board
1MHz23.7628886591% and10/100MHz validation gaps are unchanged.

**Executed2026-09-08 19:55KST, selected Device source pads and group port:**
Exact cached joinsa755ffc2… (1.609s) and audited direct-via contract inversion
46a6adee… (1.000s) recover978P/978G TOP DUT100um pads. Independent164299e4…
accepts the source-node recovery under the pinned compiler contract. The earlier
conservative missing-location hypothesis is superseded; original bytes stay frozen.
Source pad geometry is distinct from the eventual electrode-area prescription.
No raw SPD or old board solver is rerun.

Group mapd1704635… (0.031s), independently read back by Sol, has1956 rows and
two voltage groups: one differential port, one common potential, one global KCL.
Keep source branch pairs as provenance only; never force978 independent pair
KCL conditions or equal per-pad current. Next attach a declared finite contact
partition to actual source conductor volumes and preserve this group map in the
current/charge formulation. Via fill/plating and dielectric lateral boundaries
must be resolved or explicitly carried as model uncertainty; absent compact
metadata must not be interpreted as a physical void or a solid conductor.
Actual-board1MHz23.7628886591% and10/100MHz evidence gaps are unchanged.

**Executed2026-09-08, one higher increment and source geometry check:** Higher
space0bce5002…/cross9c20ba03… lead to48-current field541026b0… (0.859s).
100MHz Z=0.00651027173031+j0.0318061886851 ohm;42-to48 change0.438245% in
complex Z,1.73323% in resistance,14.7706% in current.1/10MHz complex Z changes
are1.402e-7/1.833e-5 relative. Saved58 independent6c63f3fd… accepts exact-mass
work/algebra but preserves interface6.4346% STOP. Decimal60 saved-system
review02 (02421386…) passes a fixed1e-10 gate, with current/Z drift at most
2.084e-15/3.687e-16. The earlier loose-gate review01 remains rejected; no
assembly, geometry or physical-convergence approval follows from this check.
Stop automatic thickness/width p increments; no convergence claim follows.

Neighborhoodd59b1c5d… and independent cached review show DUT100um endpads,
adjacent traces and same-power-net vias at the selected TOP trace. L02(DGND)
is a layer label, whereas those vias carry the power net. Existing conditional
DGND unions cover19.2308% of the trace projection; the full rectangular return
is artificial. No raw SPD/old circuit rerun was needed. A clipped viewport is
not an admissible physical boundary or proof of an isolated complete current path.
Next bind the actual Device positive/negative terminal populations and retained
source connections before selecting a source-current/charge volume candidate.
Reuse the accepted source metadata; preserve net ownership and declared geometry
gaps. Do not assign the23.7628886591% actual-board1MHz error to this local finding.

**Executed2026-09-08, source Cu redistribution:** Projection84ad12c2… and
independentd419fe29… distinguish3.16% ABF-field sensitivity from at most
5.215e-10 terminal-Z sensitivity on the short source-property control. Six
closed Cu curls3503459b…/independent514662b1… then add both thickness and
width profiles with zero contact flux. Cross Green1eef9f66… completes31.187s
(guard32.293s,205,922,304 bytes); refinement5.183e-9.

Field0fd925a7… solves42 currents plus10 contact/common unknowns in0.609s.
100MHz Z=0.00639743342240+j0.0317195238202 ohm;36-to42 complex Z8.67108%,
resistance29.9127%, current54.8607% changes show that36 was inadequate here.
This is sensitivity, not an accuracy gain. At1/10MHz complex Z changes are
0.0102269%/0.797265%. Numerical gates pass, but interface6.4403% keeps STOP.
Exact regional mass, rather than cell-average fields, owns loss and material RMS.
Stored52 MP60 audit56927f86… bounds solve-roundoff Z drift2.629e-15 despite
equilibrated condition1.793e10; it does not check assembly error. Mode removal
shows both thickness and width influence. Sol reviews saved52 equations.

Next: one bounded higher-profile check per Cu (Ay with z order2/3 and Az with
y order3, retaining the same longitudinal bubble and contacts). Qualify support,
mass independence, moments and Fourier; compute only new columns; report new
test residual and terminal Z before deciding on any further discretization.
Do not turn a small mode-removal change into convergence or force a0–2 pass
guarantee. Actual-board1MHz error23.7628886591% is unchanged.

**Executed2026-09-08,18:24KST:** source contact2900ab09… and scalar kernels
a3e0164e…(56.938s; independent7f7460cd…) now support a finite18-tetra
TOP25/ABF30/L0220 ideal two-port control. Field46039335…(0.609s) solves36 real-J,
8 contact charges and2 common potentials at six frequencies/two kernel orders.
DC resistance approach1.389e-11 and discrete algebra/KCL/reciprocity/passivity
pass; interface-normal current jumps5.3–6.3% fail the declared local1% discriminator.
100MHz Z=0.00448379040905+j0.0337714795561 ohm; current quadrature difference
1.852e-8. Preserve the STOP. Field01 final JSON serialization failed after all
calculations;02 centrally converts booleans. Sol reviews saved46 equations.
Next use the saved36 operator for32 total-current-conforming projections and
measure interface-constraint sensitivity of terminal Z, without new Green runs.
Do not count imposed continuity as validation. Pair-isolated ideal elements,
infinity potential reference and assumed rectangular return are explicit controls.
Actual source/board1kHz–100MHz accuracy and speed remain the outstanding objective.

**Executed2026-09-08,17:57KST:** transport-space51cdc236… and independent
review9db18dc1… accept two independent closed currents. Two RT0 columns
63dcf75f… complete under127.766s/218,501,120bytes/exit0. The74-current field
5d918e89… completes1.266s, with maximum q10/q14 current difference5.729e-8.
At100MHz complex Z changes1.59499%, resistance5.07136% and current48.9077%
versus72; at10MHz Z changes0.80422%. This is an excitation-sensitive result,
not spatial convergence. Corrected72 saved review23893613… is accepted;
Sol now reviews74 saved algebra. Freeze this homogeneous enrichment and
prepare the finite source-property TOP25/ABF30/L0220 contact/return contract.
Use explicit voltage/current definitions and retain physical interface and
external-source limitations. No new board solve or PowerSI result has occurred.

**Executed2026-09-08,17:45KST:** corrected full-Green terminal9a3ab802…(0.844s)
restores the leading -ik term omitted by the first driver. Frozen tails start at
order2. Originalterminal01 and failed enriched01 are preserved, not promoted.
1kHz resistance/DC difference1.7084e-10;100MHz
Z=0.000984093279388+j0.0156653659027 ohm. Existing136 reuse32388709…(3.672s)
reproduces the corrected72 system below2e-16 and changes Z only3.44e-15 at100MHz.
This does not demonstrate convergence: old64 eddy-oriented polynomials are
inactive for whole-end transport. Sol reviews saved72 algebra and supervises
an independent parity check. Astra qualifies two transport-specific curls only:
Ay=L0 phi0(x)phi1(z), Az=L0 phi0(x)phi1(y), phi0=1-s², phi1=s(1-s²).
Check rank, actual divergence/boundary/mean and excitation before two bounded
RT0 Green columns. No blind homogeneous growth. Preserve the2.2313e-4 body-only
radiation power discrepancy without claiming closed external-source power.
Source multi-layer finite return and actual board validation remain outstanding.

**Executed2026-09-08,17:26KST (historical, superseded above):** first potential-terminal f67f3e5d… uses
72 current coordinates plus16 independent contact charges on the frozen48tet
Cu box.1kHz resistance agrees with L/(sigma A) to1.707e-10 relative; the full
complex impedance is0.000671253566149+j1.61101106639e-7 ohm. At100MHz it is
0.000982776180281+j0.0156653657400 ohm. Contact averaging and ideal external
potential-source convention are explicit; body-only radiation is not a closed
external-source power certificate. Sol reviews saved equations/DC; Astra next
reuses the fixed136-current space and existing kernels for terminal-Z stability.
Do not run another homogeneous enrichment or a new periodic-H profile instead
of resolving transport terminals. Source-linked multi-layer return and actual
board accuracy remain unmet. Main physical source: Omar/Jiao2013 equations12–13,
22–28, with contact charge independent of the induced noncontact charge map.

**Executed2026-09-08,17:03KST:** material observables a4e42189…(0.094s) show
Cu/ABF interface-normal jumps16.1–34.1% on their own trace scale; ABF field
changes4.17–4.36% and loss differences4.85–5.52% accompany only~1e-5 global
current change. Do not approve80-space interface accuracy. Independent saved
projection/work QA bd714e95… accepts algebra only. Conforming projection
8c7aea68… executes twelve small solves in0.093s: C.H retains work/continuity
with reaction asymmetry~1.46e-5; C.T retains symmetry/continuity but has up to
3.68% power defect. Preserve both approximations; neither is a reference.
Verify the direct Fourier radiation of these saved fields, then choose a
source-bound boundary/finite-terminal observable with explicitly matched return
and voltage definitions. Sol is checking existing controls for this next step.

**Executed2026-09-08,16:55KST:**136 saved-field QA d070006d… accepts its
bounded algebra/power scope. Material trial/test1d972858… completes1.266s,
with18 saved fields180524ff… . Exact integer projection reproduces the72
operator to6.69e-16; physical work of the omitted residual reproduces its power
failure to1.315e-10 scaled. The new80 real-J Galerkin field closes power
to4.57e-12 overall and2.23e-13 for Cu/ABF, with reciprocity below1.48e-15.
Its nonzero total-normal-current jump1.733e-5–2.933e-5 uses a global body norm,
so it is explicitly not an interface accuracy certificate. The previous72
material STOP is preserved. Astra now extracts per-material loss and interface
trace measures from saved fields; Sol independently reviews projection/work.
Then select a matched source-bound boundary/terminal test. No additional
homogeneous basis growth, raw SPD rescan, product change or release is implied.
The latest actual conditional1MHz board error remains23.7628886591%.

**Executed2026-09-08,16:37KST:**136-current cross cd2f2aa2… completes125.859s,
guard126.748s/222,695,424bytes/exit0. Q14 field fd70c918… completes2.656s,
q10/q14 current difference≤7.886e-9. At100MHz the124-to-136 loss/dipole changes
are0.8850528/0.5103157%, with current mass-L2 change9.652715%. Do not call this
continuum convergence or start another automatic basis batch. Sol will review
the136 saved field; mass/polynomial scope is already independently reviewed.

Material coupling95342078… is now actually executed (3.063s), with
STOP_MATERIAL_COUPLING_DIAGNOSTIC. Equal Cu reproduces the accepted72-current
case to3.14e-15, but Cu/ABF has25.3986% mixed-drive power defect at1kHz and
1.19413% at100MHz despite tiny residuals and modest scaled conditions. Preserve
the full failed result and all18 saved cases. Next reconstruct this real-U-test,
weighted-J-source projection in a real80-current split-material space to measure
the work residual the72 tests omit. The80 rank and25closed/55charge split are
already algebraically checked; no new Green or field has run for this comparison.
Test physical total-normal continuity separately before promoting any subsequent
80-current Galerkin field. Finite terminals and actual SPD/PowerSI remain later
unmet gates. The last conditional1MHz board complex error is23.7628886591%.

**Executed2026-09-08,16:29KST:**124-field review f7257b50… accepts the saved
algebra/power scope.136-current mass02 d9e0ce8a… completes0.531s after preserving
initial implementation failure592b8601… . Sol corrected the bubble derivative
and Ax coefficient; Astra completed all72 RT0 cross terms, actual divergence
and input/normalization gates. Distinct monomial/tensorq11/tetraq10 reconstruction
checks saved mass7.73e-15 and moments5.35e-16; new12 Schur eigenvalues span
0.39976–1.13332. Polynomial Green85d46cb3… completes1.735s with angular
refinement1.98e-15 and exact-rational Fourier error≤9.37e-15.
**Running:** six new Hy RT0 columns atq10/q14,300s/24GiB guard.
**Prepared only:**136-current frequency comparison; then the total-U material
coupling control in `diagnose_astra_box_material_current_field.py`.
That next control reuses frozen48-box kernels, checks equal-Cu reproduction,
then near-equal Cu and source-property Cu/ABF on a prescribed aligned plane.
It is not extracted stackup geometry or a port. Real-U testing, complex source
contrast, physical J/E power, unchanged Green ownership and contrast-charge
nullspace diagnostics must be recorded separately. No full-operator symmetry
is imposed on the nonsymmetric D-VIE translation; failure remains a STOP.

**Executed2026-09-08,16:00KST:** axial RT0 cross e5faf3e6… completes127.844s
under the300s/24GiB guard;122-current q14 field7700be77… completes1.969s.
Independent review d2311532… accepts saved cross, real tails and field algebra
within its stated scope. Magnetic xy reuse c49b620e… qualifies four added
currents while retaining the frozen charge covariance failure2.73404e-6.
The124-current q14 field e1214855… completes2.000s, backward≤3.84e-16 and
power closure≤1.04e-11. Sol is reviewing the saved124-field arrays separately.
At100MHz the120-to-124 changes are13.8948% current mass-L2,2.594905% loss,
and1.235025% main complex magnetic dipole; q10/q14 current differs≤1.249e-8.
No port-Z or board-accuracy promotion follows from these numbers.

**Next bounded work:** mass-qualify twelve additional currents: the Hy families
(axial Legendre order,x bubble order,z bubble order)=(4,0,0),(2,2,0),(2,0,2),
two independent curls per family, and six xy mirrors. With phi_b=(1-s²)P_b
and Psi_n'=-2P_n, use Ay=L0 phi_x P_n phi_z and
Ax=-(L0/2)phi_x' Psi_n phi_z. Exclude the gradient-gauge partner Az.
Use tetraq10/tensorq12 mass andq8/q10 cell moments; old polynomial mass crosses
must be exact polynomial integrals. Sol supervises Terra's implementation.
Astra prepares a single batch of six new Hy RT0 Green columns and mirror reuse;
do not execute dependent kernels/fields before qualification. Compare loss
and complex dipole stability together with field changes. Preserve unresolved
space convergence and advance the material-interface/terminal controls after
this bounded check; do not enter automatic uniform-mesh or basis growth.
The primary1kHz–100MHz board objective remains unmet, with the last conditional
1MHz board complex error23.7628886591%. No production behavior change or release.

**Executed2026-09-08,15:20KST:** two independent axial modes are qualified by
producer285dd000…(0.375s) and Astra's actual independent reconstruction37970da1….
The latter compares full122 mass, saved cell moments/cross,75-current Poisson
projection, order and unchanged charge; errors are below2.04e-15 except the
explicit physical zero scales, which also pass. New normalized Schur eigenvalues
are0.65258/0.92592. The old q6 mass rule failed and is preserved; correctedq8
tetra/tensorq12 mass andq6/q8 moments pass. Earlier review01–03 do not provide the
final acceptance; review04 wrote no receipt. Root completed the review when Sol
hit model capacity, and requested recovery.

Two-column polynomial self/old-bubble Greena0f2b2e4… completed in0.500s with
refinement1.01e-15 and rational-moment Fourier checks below5.64e-15. The initially
premature promotion based on review02 was withheld until the root mass check.
**Running:** only RT0-to-new2 cross columns, q10/q14,300s/24GiB guard, frozen120
block reused. **Prepared, not executed:** `diagnose_astra_box_axial_field.py`.
Next compare both quadrature variants at1kHz–100MHz before choosing any further
basis extension. These two modes address Hy only; preserve that trial-space
limitation and do not infer physical anisotropy,3D convergence or board accuracy.

**Executed2026-09-08,14:39KST:** RT0-to-bubble cross integratione8eb961b… and
independent real-tail review84a9f97a… qualify the120-coordinate magnetic kernel.
q14-to-q18 diagonal-scaled change is7.34e-9. The combined first run reached its
internal deadline after savingq10/q14; q18-only continuation reused both and
completed in208.032s under a clean300s/24GiB external guard. Failure and all
quadrature stages remain preserved.

The same-Green transverse-Fourier radiation field reproduces the old72-current
case within1.08e-15 (cba91f5f…, independent reviewb7b640c7…). Enriched q18 field
eb2eca82… completes1kHz–100MHz in1.609s; final backward error≤4.95e-15 and power
closure≤5.27e-12. Independent saved algebra/power review2e5c9ba0… ACCEPT.
q14-to-q18 current mass-norm change≤3.12e-9 isolates cross quadrature from the
remaining approximation-space question. This is not broadband convergence.

**Next bounded dependency:** qualify two axial-varying closed-curl modes in the
H_y symmetry sector before another field solve: A_y proportional to
phi0(x)P2(y)phi0(z), and A_x proportional to P1(x)phi1(y)phi0(z), with normalized
coordinates and phi_m(s)=(1-s²)P_m(s). Check normal-current/charge/mean-current
zeros, exact mass cross terms and Schur rank. The analogous A_z mode has a
gradient gauge relation with these two; do not add three as independent modes.
This checks a concrete3D omission of extrusion-invariant streams. Sol supervises
the small basis implementation/review; Astra keeps operator/model selection.
Only after this qualification should corresponding Green/incident terms and
finite-frequency impact be evaluated. Maintain the source-bound material/port
and actual-board validation gates after the control-space work.

**Executed2026-09-08,14:01KST:** combined RT0+48 closed bubbles51c06b1d… and
independent review59d2fe76… qualify the exact real current/charge mass space.
The384-tetra model has528 coordinates and a48-rank bubble Schur complement;
its omega-to-zero transverse loss deficit is2.97364e-5. Exact monomial
reviewb008a3ca… independently accepts the earlier polynomial Poisson result.

Self-Green qualification95cdd1c2… uses polynomial box correlations and a
singularity-removing Duffy transform. The saved cell-average kernel projection
errs by11.418% even on384tets: do not use it as an exact enriched magnetic block.
The norm-accurate degree8 imaginary moments fail26 weak-coordinate radiation
signs. The same Green operator's sphere-Fourier identity, evaluated with stable
spherical-Bessel transforms, gives positive individual coordinates and
diagonal-scaled refinement below3.80e-14 in ef8f1543… . Dense-Gram cancellation
and all-combination power still need separate care; no eigenvalue clipping is
adopted. Independent self/radiation QA is in progress.

**Next concrete dependency:** qualify RT0-bubble static/retarded cross integrals
and finite-k incident RHS; then reuse the frozen charge operator and compare
enriched fields at1kHz, the thickness/decay transition and100MHz. Keep exact
low-frequency oracle, power/Fourier radiation and basis/convergence checks
separate. The completed independent literature report v0.2.0 (767a76ef…) is a
method-selection input, not implementation evidence. It supports testing
high-order volume representation before a separate internal-boundary model;
port-oriented adaptation and source-bound junctions remain subsequent gates.

**Executed2026-09-08,13:39KST:** the384-tetra constant-current kernel and field
completed in96.015s/26.938s. Saved-physics independent review19b371f7… ACCEPT
includes recomputed power/radiation and48-to384 comparisons. Field differences
57.799–69.168% and loss differences19.150–33.407% still reject convergence.
The exact omega-to-zero box oracle1081ca3f… isolates RT0 approximation error:
384-tetra loss-coefficient deficit15.6623%, true L2 projection error39.5756%.
Boundary-conforming polynomial diagnostic85252d66… reduces the transverse
deficit to0.003021% with16stream modes (L2 error0.549635%). Three independent
axial source tests and higher quadrature pass. This is a low-frequency basis
diagnostic, not a new full-frequency solver or board result.

The next implementation decision is therefore **representation enrichment on
the existing small control**, not automatic3072-tetra refinement. Independently
check the exact polynomial integrals, select the smallest complete3D
current/charge enrichment that retains the existing material and retarded
operators, then test high-frequency response and material/port boundaries.
Use the existing independent literature task for high-order/hierarchical bases,
finite-conductivity internal DtN/DSA and low-frequency/high-contrast stability.
Do not combine separately published formulations without deriving their common
trace, source, gauge and power conventions. A source-material junction and an
actual finite-footprint port/return control remain required after basis checks.

The product is fast pre-PowerSI/pre-design PI estimation over1kHz–100MHz.
Uniform6/48/384 tetrahedra are reference research controls. Product selection
should use geometry/contact/thickness/gap/frequency and the chosen mathematical
representation for a good initial mesh, targeting0–2 necessary adaptive passes.
Unconverged cases must remain visible. Stop criteria use complex-Z absolute
and relative errors, resonance and stable design choices; tiny local fields
need not all meet an unrelated uniform tolerance. Same-physics mesh changes and
validated surface/boundary representations are allowed. Reuse geometry, mesh,
frequency-independent integrals, multi-RHS, suitable frequency parallelism and
decap-only circuit retermination when the physical network permits it. Compare
total cold-start and same-board reevaluation time against the same reference
problem and accuracy on comparable hardware before claiming product speed.

**Additional user-provided validation candidates,2026-09-08:** mobile HQ verified
four filename/header correspondences in D:\\ and D:\\Downloads: S4LB002260729 and
260804 (92ports each), PC-2576/VDD075_NE_VDD (4ports), and s5m6585/length3 (160ports).
This is a received inventory, not an HQ rescan or full source/settings match.
The two S4LB variants may share one design family; use PC-2576/s5m6585 as
candidate design holdouts after source/material/port/termination verification.
The4-port header uses original labels31/38/45/52 mapped to current1..4 and
`# Hz S RI R 1`; do not assume50ohms or use historical labels as array indices.
Filename matching only discovers candidates. Preserve independent development
and final-evaluation sets; existing large files are not rescanned at this step.

**Executed2026-09-08,13:01KST:** the48-tetra bundledec12738… passed in72.844s;
independent kernel review49a7c997… ACCEPT. The unrestricted192-coordinate and
exact homogeneous-current72-coordinate solves both completed all six frequency
checks in about4.4s each (results6dc817b9…/f0bb57c4…). The48-vs6-tetra field changes
by86.244–90.970% and absorption by74.381–92.352%, normalized to the new result.
Do not promote this to mesh convergence. Next execute a384-tetra comparison with
the same full-wave finite-conductivity physics. First verify the exact
cellwise-constant representation implied by homogeneous divergence-free RT0
currents against the saved48-tetra vector matrices; this can reuse scalar
volume and boundary kernels without discarding a physical term. Saved-field
independent QA continues in parallel; board/PowerSI accuracy remains unqualified.

**Executed2026-09-08,12:11KST:** the first finite-3D current/charge field diagnostic
is saved in `astra-3d-current-charge-field-02` (result484d6f46…), all six interest
frequencies in0.515s. The24-current real broken-RT0 space, exact integer loop and
23 omega-scaled charge coordinates pass equation, charge/dipole, reciprocal
reaction and independent radiation checks. Complex[1,i] power closure improved
from the preserved01 failure1.5424e-3 to4.0542e-11 after a declared symmetric
two-orientation quadrature rule removed measured artificial scalar loss.
Independent field review69ffe0d9… and incident/extinction review5450eeed… accept
the diagnostic scope; the six-tetra mesh has not established
skin/field convergence. Next is a uniformly refined3D control and source-bound
material/geometry connection, not a claim that these small kernels complete
the board reference. Source inventorydaed9082… and geometry review3d86e166…
separate exact dimensions from extrusion, via-fill/plating and dielectric-domain
assumptions. Keep those distinctions in each next geometry artifact. Missing
checked cache/parser fields do not prove missing original-SPD/project metadata;
bind the actual reference generator, material/coupling settings and fabrication
properties before interpreting a later PowerSI comparison as numerical error.

**Executed2026-09-08,11:35KST:** the new asymmetric25/30/20um copper/dielectric/
copper TM boundary control now has independent acceptance14dcc30f… . Its12
frequency/period cases span1kHz–100MHz; material-subdomain condensation preserves
the original P1 equations and fixes the monolithic low-frequency failure.
The separate explicit Fourier volume-current/contrast-charge control now passes
its producer gates in7.141s (`astra-fourier-current-charge-04`, resultfd9bd4e…;
independent review79ee7eb1… ACCEPT). Worst componentwise Z error is0.0252042%, per-material
E error0.610671%, and inner-charge error0.00550797%. It uses128cells/layer, with
256at100MHz in both periods. Complex[1,j] excitation also passes volume Poynting.

The current/charge solve required analytic subtraction of a particular incident
charge, exact prescribed-H boundary condensation, a total-normal-current-conforming
trial/test space, and an exact integration-by-parts magnetic kernel. The latter
retains `k0^2 Q` explicitly instead of subtracting nearly equal O(Q) terms.
No perfect-conductor, shielding, nearest-return or quasistatic reduction is used.
Normal-current and charge continuity are constructed constraints; field, tangent-E,
inner-charge, port, power and convergence comparisons supply independent checks.
Failed attempts and the50-digit n8/n16 precision diagnosis remain separate.
This is a Fourier material-interface control, not a general3D VIE, board result
or new PowerSI accuracy result. Continue with source-bound3D current/charge bases,
self/near Green integration and actual port/material geometry; reuse these controls.

**Controlling user priority,2026-09-08:** accurate common reference physics first;
physical approximation for time/memory savings only after that reference is
validated. The near/far decap example is not an adopted fidelity policy.

The likely target machine is a512GB RAM/Threadripper workstation. Present laptop
memory/time guards protect local execution; they do not constrain reference
physics or justify omissions. Minimize time to an accurate result, not memory
usage itself. Evaluate CPU and independent-frequency parallelism, larger-memory
solves and suitable GPU kernels as the reference operations become concrete.
Workstation access and GPU presence/model/VRAM are unconfirmed; no GPU benefit
is assumed, and hardware discovery or a performance framework must not delay
the model research. Preserve the small local controls and measured diagnostics.

1. Define actual PWR/GND conductor/contact scope and common plane rules for R,
   internal/external magnetic terms, intra/interlayer mutual coupling, G/C and
   return paths. Record physical grounds for inclusions/omissions; implementation
   readiness or compute cost cannot justify changing one plane's physics.
2. Reuse the accepted source/mesh/operator artifacts to verify source ownership,
   interface continuity and numerical convergence, then complex PowerSI response
   over1kHz–100MHz. Keep current cause-isolation runs conditional. L02/L04 source
   preparation continues as input qualification; a two-layer result cannot close
   this global reference-model gate.
3. Only after reference accuracy is established, define acceptable deviation and
   assess physical reduction, RL replacement or region-dependent fidelity against
   that response. No generic reduction framework or duplicate large run is added.

Sparse/source-action operators and converged block preconditioning that preserve
the same equations remain available now. Mesh/discretization differences require
interface and convergence checks but do not require identical ultrafine meshes.
The current magnetic experiment is L14 distributed DC R plus L25 intra-layer
magnetic coupling with remaining native plane simplifications; it is not a
board-wide full magnetic model. Global G/C and interlayer magnetic coverage remain
unverified. Do not choose self-only physics because its11.1606% error happens to
be smaller than the23.7629% coupled result.

Concrete reuse boundary: the existing L25 RT0 FMM helper accepts2D triangles,
places all source points at z=0 and carries only in-plane current components.
Its accepted single-layer operator does not yet cover source layer heights or
cross-layer near interactions. Qualify these and the plane/via current interface
under the common reference contract before describing any board-wide magnetic
coverage; this observation is not an instruction to build a new generic framework.

Reuse the existing two-face material matrix in `mfdm.py`, common-mode kernel in
`tri_fem_sheet.py` and face/gap assembly in `tri_fem_stack.py` as candidates.
Source20um/59.59MS/m material control0c1fa243… passes24 uniform1D diffusion
boundary/current/power checks over1kHz–100MHz (mu_r=1). Independent review
13bb0084… accepts with a wording correction: common mode is the restricted
equal-face split K0=K1=Ktotal/2. This checks material arithmetic only; source
interfaces and board coupling remain unqualified.
Do not replace a general two-face response by its common-mode scalar merely
because that scalar is already implemented. The [MFEM examples](https://epsilon.ece.gatech.edu/publications/2009/jaeyoung_ectc.pdf)
do not supply validation of our full low-frequency-to100MHz reference scope.

**Immediate reference work,2026-09-08:** the completed magnetic ownership review
requires a single external-field owner, or a verified exact-minus-core replacement.
Do not add gap jωμd or native heuristic via L to a global operator that already
owns those fields. Gap projection alone leaves common/exterior currents undefined.
The saved G/C source audit4545b8bc… matches all36 partials to retained-neighbor
pairs across48 conductor layers. Eleven physical gaps are omitted; L04/L06/L18
have no partial because intervening source signal layers lack retained artwork.
This is a source-representation limitation, not zero physical coupling. The actual
island extractor retains1,790 same-NET edges and omits nonadjacent/fringing terms.
Independent review6485f387… accepts this2.281s metadata/CSC audit; its5.204s
checks also confirm exact CSC symmetry and the64,707 source signal-trace rows.

Define one current/charge/material-interface reference problem before further
layer-specific board solves. Evaluate volume or general surface integral
representations, consistent dielectric geometry and justified propagation limits.
The historical full-board PEEC rejection based on cost no longer controls this
choice. Reuse existing contacts, RT0 algebra, material controls and FMM operations
where they preserve that problem; this does not require a new generic solver
framework. The next acceptance must specify the source boundary and
operator partition, then independently check a minimal new joint interface case.
Do not replay already consumed local controls or extend single-layer frequency
runs merely to accumulate more results.

Prioritize an explicitly coupled volume-current/charge candidate for the next
joint reference control, with conductor/dielectric source boundaries and stable
1kHz behavior. General surface formulations remain alternatives; the particular
S-PEEC-DI study's reported low-frequency accuracy loss prevents automatic adoption
for this band. A volume-current magnetic operator already owns internal fields:
do not add the two-face/cylinder internal Z used by a boundary representation.
The existing kernels serve as local limits/oracles until their representation
is selected. Source-only checks find64,707 cached traces on the five signal layers
without retained plane artwork; use this geometry evidence in coverage planning,
not an assumption that these layers are empty.

**Latest checkpoint,2026-09-08:**6S intra-L25 magnetic1MHz run is complete and
independently accepted numerically;23.7629% complex error leaves the objective
unmet. Final-current3point/tail diagnostics are complete. L02 contact/trace/pad
geometry and the20-junction ideal-sheet discriminator are independently reviewed.
L02 mesh01 stopped at2400.454s without a snapshot. Exact hole-index coverage
let mesh02 save1,439,614nodes/2,127,824triangles and8,421,202 stiffness entries
at1437.094s. A post-result cleanup wait required supervised termination at
1729.625s; the failed guard and5.203GB peak remain recorded. Independent saved
artifact review69019084… passed all array/area/contact/stiffness gates in3.531s
and explicitly records `clean_worker_exit:false`; no geometry replay was needed.
G/C mass01 then stopped at its first owner-element moment gate in7.031s.
The isolated153-candidate diagnosis found cancellation in a small cut far from
the parent triangle origin. Moving only the integration origin to the cut
centroid passed all112 positive clipped controls and independent degree-2
quadrature without changing source geometry/density or acceptance tolerances.
Applied centroid conditioning passed1635 owners in mass02, then an elongated
owner1635 element failed at96.156s/1.618GB. Parent unit-triangle coordinates
with absolute-determinant scaling now pass both saved failures,12 orientation/
translation/multipart-hole controls and570 bounded positive source cuts, with
maximum independent mass discrepancy6.769e-13 relative. No gate was relaxed.
Applied affine conditioning passed the old failure in mass03, but direct
intersection of disjoint triangles with the large source polygon was too slow.
A supervised efficiency stop at658.235s/1.649GB preserves this incomplete run.
Four bounded256-triangle windows confirm prepared-intersects filtering retains
exact hit cuts and skips only zero-area intersections. This minimal filter was
applied under the same900s internal/1000s external/24GiB limits. Run04 completes
236.281s/2.095GB; review67268152…
accepts all2064 owners and1,041,341 local masses, with total-C relative error
1.540e-15. Board assembly01 also completes9.531s/1.394GB; full equipotential
recollapse1.715e-12 passes the unchanged2e-12 gate. Independent assembly review
6d29d818… and the first finite L02-only DC response are now complete. The latter
took92.125s/14.517GB and passed independent field review816ba18a…; its frozen
comparisonea206695… changes complex error76.103232%→75.785783%, removing only
0.417130% of the corrected native baseline gap. Saved L02 drive census and
three-drive epsilon-zero sensitivity are now independently accepted, the latter
with an explicit <=1.1e-16A inactive-contact roundoff balancing limitation.
Finite-response census03 now finds L04 DGND throughput9.498453micro-A→0.959623472A
and L02 net contact throughput1.001707734→0.212269964A. Independent review
d4723906… accepts all groups and exact expanded current mappings. Next inspect
source-bound L04 and the other connected ground planes,
then decide a coherent finite-conductor scope and measured cost before a large
new solve. The bounded block-Jacobi benchmark stopped after100 inner iterations
at the unchanged KCL gate; the rejected field and diagnostics are saved.
L04 source inventoryc86cf341… passes independent final review1759b05d…. No large
solver is running. The harmonic/source-action candidate now converges and passes
independent field reviewe7830d4d…; it saves6.3% peak memory but takes11.4% longer.
Current work is the cached L04 contact ledger under the reference-physics priority.

**User priority clarified2026-09-07:** primary accuracy band is1kHz–100MHz.
Keep1GHz as a secondary diagnostic; its error alone must not displace useful
progress within the primary band. Next fill the unsampled1kHz–1MHz interval with
the smallest informative source-bound native/two-sheet comparisons. Existing small
source slice already supplies1/10/100kHz stamps with the saved1MHz control passing;
the first1kHz native field failed the1e−7A physical KCL gate. Saved-array diagnosis
identified floating stamp cancellation; native02 recovered source-current KCL
with one same-LU correction at1kHz and none at10/100kHz. All three conventional
CSC residuals also pass. Independent review accepts these original-source points
with no P1/P2. Two-sheet shadow03 completed192.766s/14.176GiB reusing these native
points and never repeating1MHz LU. At1/10/100kHz complex relative errors improve
0.09131/0.89537/8.75179% to0.02492/0.24333/2.37860%. Actual/comparison independent
reviews both accept with no P1/P2. Physical inputs and original KCL gate remain unchanged.

**2026-09-07 executed correction:** the required 1 MHz two-port complex response
has now been generated from the saved D115b scenario and existing numerical
solver. Both native-base and source-mounted states completed in1578.875 s.
Source-mounted error is+1.554153392 dB/19.5935% complex relative error. The
conditional Trace311318 update changes Device Zdd by48.3546 micro-ohm against
a48.4554 ohm gap; its diagonal/passivity bound is0.79419 ohm. This isolated
trace is not the dominant1 MHz accuracy remedy. The10/100/1000 MHz source-mounted
follow-up also completed in1198.938 s; magnitude errors are+1.64434/+2.24124/
-1.19604 dB. AON itself has0 mounted decaps, so this does not resolve the
loaded-rail accuracy objective. The source-selected non-holdout
`ADC_VDD_075_VTRIP_SRAM/0` development case(421 decaps, port18) also completed:
1/10/100/1000 MHz magnitude errors are−2.42867/−22.54743/−21.26195/−20.79063 dB.
The1306.781 s native job and checked comparison establish a substantial loaded
accuracy gap, not an improvement. All421 components use saved sampled complex
impedance models. Next prioritize a source-bound multiconductor sheet/contact
replacement on the actual Device-to-mounted-component route, retaining every
external G/C coupling and native via R/L owner. The current island-as-one-node
representation does not resolve lateral sheet voltage/R/L; this is a confirmed
model limitation, not proof of the entire error's cause. See the
[current evaluation](evaluation-research/ASTRA_STEP5_SUPERVISION_2026-09-07.md).

**Latest two-sheet result:** the source-bound L14+L25 DC diagnostic completed
62.718s/13.71GiB. At1MHz, complex gap drops618.032→238.667µΩ(61.3828%), magnitude
error−2.42867→−0.366399dB, complex relative error76.10→29.39%, phase error−17.09°.
Independent saved-mass numeric review passed with no P1/P2. This is conditional
single-point improvement, not objective completion. Follow-up6K has now completed
10/100/1000MHz in193.906s/14.12GiB, retaining native sampled components and per-gap
source dispersion. Two-sheet magnitude errors are−8.30413/−16.75537/−20.46949dB;
complex relative errors65.10/86.83/90.53%. Gap reductions are29.69/4.96/0.43%, so
the improvement strongly diminishes with frequency;10/100MHz phase errors worsen.
Independent four-point arithmetic, provenance and saved-field port-Zdd checks pass
with no P1/P2. These are four conditional development samples, not dense-band or
unseen validation. Next6L assesses source-bound magnetic/return and internal-sheet
impedance support in existing kernels before further DC mesh expansion or new LU.

Missing cached results were derived-data absence,
not proof of missing physical input. The blanket no-scenario-load/no-substrate-
compile rule in earlier HQ briefs was an operational choice; the verified
handoff only forbids consumed-root replay and an unnecessary raw-SPD rescan.
The latest user instruction explicitly authorizes this feasibility check and
possible computation. Use a new output root, retain all physical native terms,
bound time/RSS, and record unloaded versus source-mounted states precisely.
Do not repeat a local mesh study or declare an external blocker before testing
this existing-source route.

The [current Astra checkpoint](EVALUATION_SOLVER_DEEP_RESEARCH_2026-08-04.md#current-checkpoint-2026-09-06)
supersedes the earlier pause's workflow and blanket research stop. The historical
phases below are a method catalogue, not a claim that their proposed modules are
absent or that Legacy modal remains the current product default. v0.23.1 already
uses terminal-complete Layerwise/global-Y. Preserve its profile and UI/version.

**Working location:** `C:\Users\User\.codex\worktrees\5950\SPD Decap PI Evaluator`,
branch `codex/astra-evaluation-resume-20260906`, starting at
`e2f219e71d8c8a397009f72242cce10d78cfc7ab`. The source checkout
`C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator` remains read-only.
Only the two hash-verified current documents were transferred. Referenced
uncommitted source tools are evidence, not an implicit implementation transfer.

**Objective and status:** source-derived Evaluation Analysis close to supplied
PowerSI `Zii`, followed by unseen validation. The 35–40% estimate is retired.
Current product accuracy remains recorded W6-BASE FAIL; D117 contributes input
feasibility evidence, no new accuracy gain. The three frozen executions retain
their exact STOP/PARTIAL scopes and must never be rerun or reused.

| Sequence / owner | Deliverable in this checkout | Prerequisites / acceptance | Budget and fallback |
|---|---|---|---|
| 1A / Sol ultra | `docs/evaluation-research/ASTRA_C1_STAGE_DIAGNOSIS_2026-09-06.md` | Static receipt-to-code stage map; distinguish timeouts before mesher entry from measured mesher cost. Recommend one next small measurement and compare alternatives only where evidence warrants it. | 20–30 min; no mesher/production execution or new orchestration layer. Unknown stage stays unknown. |
| 1B / Luna max, concurrent | `tools/research/study_source_dielectric_candidates.py` plus `docs/evaluation-research/ASTRA_MATERIAL_STUDY_2026-09-06.md` and a compact JSON result | Hash-verified D103 receipt only; source-table interpolation control, nonnegative Debye residues, explicit fit weighting, residuals, source-band/audit-band distinction, analytic recovery/passivity check and uncertainty. | 20–30 min editing, experiment <60 s. Use installed NumPy/SciPy, one focused check. Stop at a useful negative result instead of increasing complexity. |
| 1C / Astra xhigh, concurrent | Revised two governing documents and candidate-specific input decision | Separate physical source facts from solver coordinates/closure conventions. Review W6 error evidence and all child outputs independently. | 20–30 min initial review; record missing evidence and do not fabricate a positive source binding. |
| 1D / Sol ultra, independent physics study | `tools/research/study_mfdm_loaded_sheet.py`, `docs/evaluation-research/ASTRA_LOADED_SHEET_STUDY_2026-09-06.md` and compact JSON | Reuse existing MFDM on a fully specified synthetic strip with a remote passive decap; compare loaded/open Z to an independently evaluated transmission-line or equivalent analytic reference over a small refinement sequence. No real-board geometry/material/terminal assumptions. | 20–30 min, each small run <60 s; report mismatch instead of expanding into a new solver. |
| 2A / Astra directs, Sol supervises Luna | Fixed physical-port refinement using the existing strip study | Same positions on 4/12/36 cells; matched analytic and refinement checks separately. | Complete: ACCEPT_CANONICAL_ONLY, 12 small solves in 0.031 s. |
| 2B / Sol supervises Luna and Terra | Continuous four-terminal surface/global-Y discriminator and current-route/ownership audit | Preserve local gauges; independent loaded reference; separated-terminal STOP and duplicate G/C negative control. | Observed singular Kron STOP, same-base duplication 2x, separated-launch no-admissible-return STOP; diagnostic only. |
| 2C / Astra, independent Terra review under Sol | Existing coupled SeriesBranchBlock composition witness | Paired launch incidence, two gauges, full-mesh loaded reference before condensation; no extra native G/C. | One synthetic 2 MHz case, two gauge orderings, 0.02073 s; canonical only. |
| 3A / Sol supervises Luna and Terra; Astra decides | Bounded D096/D104 source compatibility and actual plane-contact lookup | Real footprint/return mapping, material assignment and closure; reduced indices alone are insufficient. | Executed: actual TOP pads and L21 power bridge found using existing restored SQLite. GND-to-L29 closure and current-basis native exclusion remain open. |
| 3B / Astra | Source-bound single-frequency replacement if compatible; otherwise bounded conductor-current assessment | Explicit physical current path, preserved coordinates, one owner per G/C and R/L term. | Whole-network replacement STOP. Existing FEM ran on the real eight-vertex L21 sheet: coarse 22.2576 micro-ohm, refinement STOP; full four-pad input has exact tangency. |
| 3C / Astra directs, Sol supervises local evidence review | Verify GND frontier pad/plane contacts; resolve matching shadow-input identities | Use existing indexed source rows and geometry helpers; do not select a nearest GND as the physical return. | Two upper-plane GND contacts verified in 0.703 s. Full L29 return and matching topology/raw/ownership artifacts remain open; the restored historical index is not a live-product basis. |
| 3D / Astra, Terra review under Sol | Source-coordinate conductor mutual-L feasibility using existing Neumann kernel | Three actual same-span via pairs; independent quadrature; explicit source foil-face endpoint conventions | Executed in 0.015 s, coefficient diagnostics only. No filled-via, radius, plating, self-L or assigned-return assumptions; not a full PEEC block. |
| 4A / Astra; Terra review under Sol | Restore existing D115b ownership/raw/topology artifacts, then query exact replacement boundary | Hash/size checks, same source/project/certificate identities; historical D096 remains immutable. | Executed: ownership 1.187 s; raw/topology restoration 12.406 s; boundary 0.360 s. Matching artifacts found, but native numeric base and physical replacement remain open. |
| 4B / Sol implements after worker stalls; Terra reviews, Astra accepts | Complete bounded source-copper GND route from L18/L20 to L29 | Same connected copper component plus actual via endpoints/pad footprints; source association is separate from physical current/model acceptance. | Executed in 9.421 s: source-copper L18/L20/L21 witnesses, exact L21→L24→L27→L28 stack and L28 traces to L29 contacts match compiled RL nodes. Connectivity ACCEPT only; failed direct24 inventory was not a complete cut. |
| 4C / Astra; Terra review under Sol | Quantify source trace resistance scale to guide conductor-model priority | Same-basis source width/length/copper thickness; reuse already-proven paths and verify every edge hash. | Executed: 9 traces, nominal path sums 4.205–7.461 mOhm in 0.140 s; isolated-trace diagnostic only, no PDN/PowerSI claim. |
| 4D / Astra; review under Sol | Isolate one native degree-two trace boundary and independently bound its assumed DC sheet resistance | Preserve both via RL owners and all source regular pads; qualified filled-microvia electrode assumption disclosed. | Executed: R bounds 0.335627–0.553591 mOhm; 6 local 1 MHz MNA solves/two gauges pass in 0.047 s. Original refined FEM and overlay alternative remain STOP. |
| 4E / Astra; Terra review under Sol | Resolve local DC refinement using inward dyadic geometry in similarity coordinates | Original mesh guards; analytic scale control; fixed electrodes per sequence; separate circle and mesh checks, each 2%. | Executed 2026-09-07: 64/128-sided source approximations, 6 mesh solves, 12.438 s. Finest 0.444624 mOhm; mesh changes 0.6445%/0.6330%, circle change 0.0975%. Terra ACCEPT for assumed local DC only. Two new coordinate ablation cells both STOP; normalization alone was insufficient. |
| 4F / Luna under Sol; Terra review; Astra correction | Exact rank-one trace-R response update using the existing MNA | Complex bilinear formula, direct synthetic reference and actual R=0 formula check; no assumed Device transfer. | Corrected -02 receipt ACCEPT_RESEARCH_ONLY. Requires matching loaded/unloaded Device/internal-branch 2x2 numeric response; aggregate scores/hashes and topology alone are insufficient. |
| 5A / Astra; Terra review under Sol | Complete actual four-contact L21 DC sheet and discriminate mesher failures | Same source/pad/via/material, exact domain and original guards; fixed four electrodes and full Y/R retained. | Uniform STOP 14.39%; seeded Delaunay STOP missing edges; initial Triangle apparent PASS rejected for independent discrepancy/DC sign. Exact-domain boundary20/10 resolves that local defect: area 0.444608%, circle 0.097632%, outer-boundary 0.00425969%. Accepted conditional local DC only. |
| 5B / Sol implements after Luna orphan; Terra review; Astra acceptance | Compose accepted L21 DC sheet with the four unchanged native via-RL owners | Full nodal Y4 vs coupled SeriesBranchBlock, two differential power-feed ports/two gauges, full Z and solved via currents; exact ideal-sheet control and independent local algebra. | Corrected -02 ACCEPT_CONDITIONAL_LOCAL_POWER_FEED_ONLY, 0.032 s internal/0.925 s shell. Z/current comparisons about4.2e-15; HQ independent loop elimination also passes. First orphan receipt preserved as rejected provenance. Fifth G/C excluded solely in declared local model; no full AC replacement. |
| 5C / Astra; review under Sol | Quantify unqualified core injection assumption separately from mesh error | Same source outer copper/three microvia electrodes; hypothetical core equipotential radius75 vs175 from source drill/pad dimensions; no fill/plating claim. | Executed 19.406 s+8.375 s. Core75 area/circle/boundary changes 0.407700%/0.0245799%/0.00390130%; pair R changes 9.03–83.71%. Injection assumption dominates numerical refinement and must be resolved before physical promotion. |
| 6A / Sol executes; Terra reviews; Astra supervises | Actual native 1 MHz Device/Via336274 complex 2x2 | Preserve physical G/C and finite via R/L, exact endpoints, source-mounted manifest, source identities and numerical guards. | Completed both states in1578.875 s, peak private13.9 GiB. Initial900 s operational timeout was corrected with a measured2400 s budget and state/factor checkpoints. |
| 6B / Astra; Terra review under Sol | Actual-board conditional trace shadow and reference comparison | Exact rank-one update, R=0, independent2x2 inverse, preserved loaded degree-two joint and passive energy ceiling. | Completed:48.3546 micro-ohm change against48.4554 ohm error; even the0.79419 ohm passive ceiling cannot close the gap. No material1 MHz accuracy gain or physical promotion. |
| 6C / Sol executes; Astra evaluates | Source-mounted10/100/1000 MHz six-probe diagnostic | One native compile, sequential frequencies, exact PowerSI reference points, each result checkpointed; no1 MHz or unloaded repeat. | Completed1198.938 s, peak private12.5 GiB. Errors+1.64434/+2.24124/−1.19604 dB; physical accuracy remains unproved. |
| 6D / Sol executes; Astra selects/evaluates; Terra reviews | Actual loaded development baseline: ADC_VDD_075_VTRIP_SRAM/0 |421 source-enabled caps, port18; largest represented non-holdout source population with deterministic tie-break, pinned before reference extraction. All frozen holdouts excluded. | Completed four points1306.781 s/12.3 GiB peak private. Errors−2.42867/−22.54743/−21.26195/−20.79063 dB; complex errors76.10–92.58%. Actual accuracy remains inadequate.08:00 intermediate report and later completion report delivered. |
| 6E / Astra prioritizes; Sol supervises implementation/review | Source-bound loaded Device-to-component multi-terminal sheet/contact replacement | Resolve actual contacts and return geometry, exclude only replaced owners, preserve native via R/L and every external G/C coupling. Use fixed source-derived physics, then compare the frozen development points. | Source state/contact graph proven:421 unchanged components,450 P contacts and45 Device anchors reach the L14 quotient;110 islands/1732 holes and2110 source finite-R/L edges enumerated. Actual replacement not executed: old partial partition/contact geometry/replace-once ledger remain. Missing SRAM rows in the AON ownership table are not missing source inputs. |
| 6F / Sol supervises worker implementation; Astra reviews and executes | Observe the original loaded1 MHz node voltages and finite-branch/termination currents | Original solver and physical terms unchanged; record actual row scaling, source→reduced→active mapping and36 source C partials, reproduce the saved1 MHz Zdd, and close the complex-power sum. Do not infer individual ideal-link currents. | Run01 export failed on fixed-width Unicode. Run02 native solve and three NPZ exports passed independent checks: Zdd difference1.084e−19Ω, power closure2.479e−14Ω. Final JSON was truncated by a graceful-stop/watchdog race; original preserved and separate verified artifact manifest recovered without rerun. Process exit was not clean. |
| 6G / Sol implements; Luna/Terra review; Astra accepts | Conditional source-derived L14 centerline DC Trace-R global shadow | Split110 source islands and5597 external trace nodes, move2110 finite endpoints and both affected source C partials once, retain every other term, recollapse original Y, run original numerical gates. | Completed91.953s/6.33GB, ε=1 and0.01. ΔZdd=0.437478+j0.0232705µΩ. Frozen-result comparison reduces1MHz complex gap only0.068812%; not the main gap remedy in this conditional model. No broadband/physical promotion. |
| 6H / Astra executes; Sol supervises source projection and review | Full L14 sheet ideal-limit DC sensitivity and finite-resistance global comparison | Reuse saved171957-node source mesh,1660 equality electrodes and224 original GC owners; preserve exact degree2 moments, every external coupling and native via R/L. | Completed:147057DOF ε0 sensitivity1.608289−j0.009223mΩ; exact C mass;903945DOF ε1/0.01 global solve28.047s/6.34GiB. ActualΔZ=67.445+j29.409µΩ. Complex gap reduces11.815%, but magnitude error worsens−2.42867→−2.62525dB. Mixed single-point result; no convergence, broadband or unseen promotion. |
| 6I / Sol supervises Luna field census; Astra chooses next model | Locate current redistribution after finite L14 resistance | Reuse saved ε1 voltage, rewire2110 source endpoints before deriving currents, compare actual source components/layers and category powers without another LU. | Completed. L25 same-net ideal sheet's350-via halfΣ|I| rises2.3575µA→0.905959A. Total via loss rises39.576755µΩ;35.732575µΩ occurs outside L14 boundary. L14 local ε1 sensitivity is only1.414754% of ε0 magnitude. This observed redistribution selects L25 next; no unique-return or magnetic attribution. |
| 6J / Astra source/mesh/global composition; Sol supervises owner inventory and review | Conditional L14+L25 DC sheet comparison with all remaining native terms | Retain L14 operator; source-bind L25 geometry,175 shared drill-radius contacts from350 vias,32µm copper and every original G/C owner. Recollapse to the existing L14-only matrix before the new solve. | Complete. Source mesh/drive/mass and independent numerical review pass;1436468 DOF, full-operator recollapse1.17e−14relative. Actual1MHz solve62.718s/13.71GiB reduces complex gap61.38% and magnitude error to−0.36640dB; complex relative error remains29.39%. Saved field census also checks L20/L21 throughput0.284899/0.231502A. Conditional fixed-mesh result only. |
| 6K / Astra executes; Sol supervises Luna/Terra review | Four-frequency conditional two-sheet development comparison | Reuse saved meshes and source model stamps; reproduce1MHz assembly/field without LU; preserve all native per-gap dispersion and sampled terminations. | Complete. Three new solves193.906s/14.12GiB; complex gap reductions at10/100/1000MHz29.69/4.96/0.43%, remaining relative errors65.10/86.83/90.53%. Independent arithmetic/hash/port-field review P1/P2=0. No new geometry or1MHz solve. |
| 6L / Astra algorithm decision; Sol supervises bounded code review | Choose the smallest source-bound AC discriminator using existing kernels and saved fields | Distinguish internal-sheet impedance from external magnetic coupling; verify return/current ownership. No inferred missing-L fit or nearest-ground assignment. | Saved-field internal-common-mode derivative complete0.834s, independent review P1/P2=0. Direction at1/10/100MHz is0.3168+j9.3131µΩ,18.379+j94.019µΩ,−150.691+j650.197µΩ; not finite response or an error bound. No1GHz alpha1 solve after user priority clarification. |
| 6M / Astra native/two-sheet comparison; Sol supervises reference and source-stamp review | Close the low-frequency sampling gap in the user's1kHz–100MHz band | Reuse saved source slice, raw matrices and fixed sheets; exact reference samples1/10/100kHz exist. Reproduce1MHz by saved voltage/KCL without repeating its LU. | Native02 complete50.954s/4.956GiB; independent ACCEPT P1/P2=0. Shadow03 complete192.766s/14.176GiB, one same-LU correction at1kHz and none at10/100kHz; all original KCL gates pass. Comparison complete: two-sheet complex errors0.02492/0.24333/2.37860%; actual/comparison reviews ACCEPT P1/P2=0. Frozen run01 stays STOP. |
| 6N / Astra; independent review under Sol | One100MHz finite symmetric two-face internal-sheet discriminator | Source sigma/thickness and existing kernel; multiply each fixed Gdc by Rdc/Zcm. Preserve native external terms; compare frozen output only after solve. | Complete70.875s/13.939GiB, independent actual/comparison ACCEPT P1/P2=0. Zdd0.854939+j2.008138mΩ; DC delta17.25154+j705.01083µΩ, KCL9.653e−11A. Complex relative error86.8263%→80.2338%; magnitude/phase−13.77794dB/−13.45317°. Conditional internal-only response, not external magnetic/return/proximity or continuous-band validation. |
| 6O / Astra algorithm; Sol supervises Luna implementation and Terra review | Saved-mesh P1-to-coupled-current local identity and shared-edge flux audit | Same full/contracted G, source sigma/thickness, fixed100MHz DC voltage and contacts. No geometry, LU or magnetic matrix. |03 diagnostics saved4.734s; independent review confirms both sheets STOP at original F*i→J gate. All energy comparisons pass, normalized Cartesian q agrees near1e−16. Shared-edge current jump RMS39.5293/3.64475A/m; no conforming-current or external-magnetic promotion.01 numerical STOP and02 serialization failure preserved. |
| 6P / Astra formulation and actual run; Sol supervises implementation and review | RT0/P0 conservative current formulation and source electrode/GC mapping | Same-domain adaptive fields and source-clipped G/C transfer; preserve L14 P1 and native via R/L/owner totals. Single-pair1% is not a blanket prerequisite for conditional board discrimination. | Adaptive longest-edge6 steps110.0116s reaches10.99298% gap/P1; node cap stops further refinement, all saved steps independently accepted. Final G/C transfer91.1299s independently accepted. Actual board1MHz completes27.8854s/16.015GiB, one16.8996s LU, no corrections; Zdd=0.551514−j0.588801mΩ, KCL6.613e−11A. Complex relative error29.3890%→26.4660%, phase−15.2543°; comparison and saved L25 field checks independently ACCEPT. Full L14 KCL remains the frozen run's check. Combined mesh/P0 G/C change, not a magnetic or broadband result. |
| 6Q / Astra algorithm/cost sizing; Sol supervises Luna canonical implementation | Reuse FMM3D for matched RT0 quadrature spread/gather and self/near correction | Keep RT0 unknowns; multiply FMM3D's1/(4πr) by μ0, preserve exact-minus-quadrature near blocks, no PSD assumption or spectral clipping. | Eight-triangle near/far canonical completes10.819s with independent source review ACCEPT; final matrix/energy differences4.287e−14/3.576e−15 versus finite-triangle oracle. Full1,737,531-point4channel attempt previously stopped around4min/no result; external guard now verified. Guarded65536-point subsets cost11.9366s/3.414GB for4channels eps1e−8 and2.1310s/.724GB for1channel eps1e−5, both independently accepted. Saved affine omission bound.03482µΩ also independently accepted. Next: actual saved-field four-channel source cost and near integration, then unchanged mixed unknowns with fresh R-only LU preconditioner for finite R+jωL response. Full-scale near accuracy, return composition and finite response remain open; no centroid-error or arbitrary-Krylov-current bound. |
| 6R / Astra executes; Sol reviews Luna self operator | Finite same-triangle magnetic control on the same actual1MHz board | Preserve all source/B/G/C/contacts/native via RL; solve alpha0 andalpha1 with actual new currents. | COMPLETED57.578s/17.330GB; alpha0 exact field replay; alpha1 Z0.631203−j0.493376mΩ. Complex error26.4660%→11.1606%, phase improves but magnitude slightly worsens. Original physical/power gates pass. Conditional self-only control; all mutual/return magnetic terms absent, no final physics promotion. |
| 6S / Astra solver; Sol supervises Luna centroid action | Actual matrix-free L25 self+mutual finite response, then final-field quadrature check | Same mixed unknowns, complete changed-operator gates; no derivative extrapolation. | Run03 COMPLETED:983.005s worker/984.140s external,21.854GB peak private;14 GMRES steps, residual8.80806e-10/info0 and all10 gates pass. Independent numerical/comparison review ACCEPT. Z0.813457-j0.575345mohm; complex error23.7629%, versus R-only26.4660% and self-only11.1606%; magnitude+1.77617dB/phase-3.65270deg. Final-field3point action difference1.59456% is diagnostic only. Run01 failure and run02 STOP preserved; full return, other geometric near, PSD and broadband remain unqualified. |
| 6T / Astra implementation/execution; Sol and Terra review | Selected350-via mutual-only conditional board ablation | Remove the exact rewired350 scalar stamps once; use signed axial mutual with original native R/L diagonal. Keep all other native terms and DC sheets. | Completed49.563s/18.294GB, one36.285s LU. Alpha0 nodal matrix diff0, no repeated baseline LU. Alpha1 Z0.554914-j0.577256mohm, KCL4.664e-11A/power closure8.373e-15ohm. Independent actual review ACCEPT. Frozen comparison complex error26.4660%→25.1222%, remaining gap reduction5.0775%; phase improves, magnitude worsens. Small conditional effect, no complete-return or broadband claim. |
| 6U / Astra priority and G/C binding; Sol supervises implementation/review | L02 source contacts, conductor domain and controlled junction correction | Bind active349710,491 islands,76,139 native via owners,38,662 traces and42 explicit exceptions; retain every G/C owner. | Contact/trace/pad-domain, saved mesh, G/C04 and assembly01 independently accepted. Finite L02-only1MHz completes92.125s/14.517GB; review816ba18a… accepts. Frozen comparisonea206695… gives2.585579micro-ohm DeltaZ and76.103232%→75.785783% complex error. Saved-drive census0cfdfd2d… and sensitivity3592a41d… pass independent review; sensitivity retains the quantified inactive-contact numerical balancing caveat. Finite redistribution03 finds0.959623472A L04 throughput; its QA and source inventory follow. Separate from full L25 magnetic23.7629%; no accuracy promotion. |
| 6V / Astra chooses conductor scope; Sol supervises source inventory and QA | Locate alternate ideal ground paths before expanding the magnetic model | Reuse saved finite L02 voltages, source group identities, raw/compiled SQLite and native owner mappings. Preserve40 composite legs and local contact cancellation. | Census03 covers1,323 source groups in1.500s/350.5MB; reviewd4723906… independently accepts. L04 active71610 rises from9.498453micro-A to0.959623472A finite-via throughput. Inventory L04 and connected ground-plane boundaries, then choose joint source-bound finite-R coverage and a bounded solver strategy. No geometry replay, new mesh or large LU solely for this inventory. |

**2026-09-08 00:23KST update:** Full FMM run03 completed and its worker was reaped.
It began23:52:55KST/PID24792 after the via-only worker exited, reused only the
run02 initial field and preserved thee88/2c88 operator and1e-9 target. Fresh
self-LU24.13387s reproduced its control exactly. The separate via-only result
is not composed into run03. Result SHA40a0f959…, field e095cb57… and independent
review0639523b… are frozen; full comparison7b6ce80c… closes10.21357% of the R-only
reference gap, but is worse than the self-only control. Neither monotonic accuracy
improvement with added terms nor sufficient PowerSI accuracy is established.

The accepted-field3point postcheck completed247.025s/19.659GB, external248.312s,
without a LU. Result348ea223… and independent review6d425eb0… agree:
norm(L3q-Lsaved)/norm(Lsaved)=0.01594564 and max|jwDeltaLq|=4.99716e-6V.
The415 shared-edge order64-to128 check completed5.902s/0.104GB;16 pairs still exceed
the diagnostic1e-3 refinement threshold. On the accepted final field its observed
first-order port direction is(5.82446e-17,-7.04503e-17)ohm and maximum branch
voltage action difference9.62305e-12V. These are fixed-field diagnostics, not
finite updated Z or error bounds. Keep the full operator frozen; prioritize
source-bound return composition over another tail-order or full-board replay.

L02 G/C source support is now independently accepted:2064 owner overlap polygons
completed22.875s/455.475MB and preserve C5.909971854nF. Receipt3ee62248…,
WKB7ce1575f… and independent review3f135214… are frozen; six direct whole-polygon
controls agree exactly. The target C island alone has7018 holes/763687 source
coordinates, so do not launch an unsized monolithic mesh/LU. Original native
L02 external circuit boundary reaches33026 distinct active nodes (finite31333,
GC1696, overlap3); a dense complex interface matrix alone would need17.451GB.
Contact footprint mapping and sparse/block solve feasibility are the next dependency;
no current-based removal of source contacts or unreviewed geometry simplification.

Ordered L02 contact inputs are now saved:26.609s worker/28.031s external,
567.628MB peak private, exit0. Result98bce62f… and NPZc5c0dab9… retain76,139
via owners in native order,491 source islands and42 explicit exceptions.
The38,836 exact center groups do not yet establish physical pad unions.
Separate exact38,662 trace inputs and widths25/60/100um are independently
accepted (reviewc5962052…). Current actions classify contact footprints and
flat-body/square-envelope trace coverage against cached artwork. No mesh or
finite L02 solve has begun; contact input review666463b0… now independently
accepts all compiled/raw joins and source WKB. The native nominal
epsilon0*epsilon_r/gap formula reproduces the new2,064 overlap C values within
8.468e-15 relative (receipt3cc4234e…), without fitting or an EM accuracy claim.

Trace footprint review8b3d42c3… accepts coverage01. Conditional flat-domain01
is a single Polygon with33,166 holes/846,577 coordinates,28.203s/1.396GB;
boundary arithmetic residues are retained, not repaired. The42-excluded-via
diagnostic5e18de3b… finds positive regular-pad overlap for all42, while40 owners
in20 composites have centers/drill polygons outside. These composites have3–12
owners, not two. Original-artwork contact-overlaps01 completed17.234s/842.236MB
and review10ae9931… accepts it: native76,139 pad/drill rows each contact one
island;40 excluded owners contact none, confirming trace-dependent new pads.
The8,503 noncoincident pad tangencies are not automatic electrode unions.
Flat-domain/excluded-via reviews362f1329…/707ed47c… accept only conditional
geometry. Current work recovers source path orientation/R/L from native fingerprints.
Separate native-topology-preserving sheet ablation from any newly connected
L02 junctions; an actual topology correction requires its own controlled baseline.

Composite source paths03 now passes3.765s/203.153MB with independent review
3ae31ad5…:20active links,93original via owners and5exact ideal trace bridges.
Next discriminator is the conditional ideal-L02 junction correction alone:
remove20 series branches, add40 recovered positive-RL legs to active349710,
preserving original G/C,terminals,other branches and two leaves. Assembly04
(4d0c1441…) passes1.891s; free-midpoint elimination1.316e-15, saved-field KCL
4.291e-11A, changed-stamp2.581e-13 and independent full positive-branch rebuild
1.366e-13. Keep the common native sums to avoid reordering large diagonals.
The modified native solve01 now completes11.531s/4.889GB with source/CSC KCL
5.472e-11/5.152e-11A and power closure3.172e-14ohm. Frozen result24b60736…,
comparison1a4527da… measure only1.61695e-11ohm change,2.6163e-8 of the original
reference gap. This is the original ideal-sheet baseline, not the latest L25
FMM candidate. Independent actual-field QA658b1374… accepts all1,692,409
branch/G-C/termination currents,20split-pair losses and comparison arithmetic. Continue finite
L02 conductor/contact preparation; no repeated LU for this tiny discriminator.
Flat-boundary cost diagnostic52e71117… gives813410boundary occurrences; with
38836distinct interior16-sided contacts, conditionalEuler counts are1.435M
vertices/2.122Mtriangles. These are not actual mesh or memory measurements.

**2026-09-08 03:00KST pad-domain checkpoint:** result91e16302… and independent
reviewac4f8ba2… accept the saved pad-augmented domain306515d6… and support
map3774bb01…. There are817,918 boundary vertex occurrences,33,258 holes and
38,856 unique drill supports, with strict drill coverage and zero drill residue.
The pad union retains1.36019e-12um2 uncovered area; its conditional residue status
must remain. Worker162.703s/3.029GB exited after all hashed result/artifact writes
because exclusive checkpoint persistence cannot replace an existing path. Frozen
driver8d376f85… remains governing; the live helper8acd1493… writes a separate
completion checkpoint. No geometry replay. Post-pad conditional16-sided contact
arithmetic is1,439,614 vertices/2,127,824 triangles, not measured mesh cost.
The reviewed FEM preflight keeps native geometric gates and preserves source
status; no LU, dense contact reduction or circuit owner revival is included.
Actual mesh01 passed strict validation of38,856 contacts at55.516s and started
the1,511,729-coordinate large-face triangulation at557.016s; no mesh result yet.
Source circuit binding01 completed0.516s/103.428MB, result1ef00f9c… and
NPZ61ed51f6…. It maps all76,139 native branches to38,836 supports and the20
new junctions to20 distinct additional supports, retaining all93 ordered source
via records and native source endpoint directions. The two excluded leaves
share native supports and add no circuit branches. Independent actual review
be2dff54… accepts every native source endpoint/via/R-L/support row, all93 ordered
source owners,20 junctions and both leaf policies, with no P1/P2 findings.

**First finite-L02 experiment decision and outcome:** Astra and Sol selected an
L02-only DC shadow on the accepted ideal-junction native baseline. The completed
assembly confirms856,774 sheet potentials and1,613,662 total potentials before gauge.
This isolates L02 spreading while keeping native R/L, G/C and terminations.
Immediate composition with L14+L25 RT0/FMM would instead reach about2.94M mixed
unknowns, versus the prior2.087M/21.85GB run, with no measured24GiB factor margin.
The larger mixed count remains an estimate, not a measured factorization budget.
Source G/C mass and complete finite/G-C/equipotential recollapse passed before
the L02-only LU. Its small2.585579micro-ohm response change does not establish
the effect under the expanded L14/L25 current distribution. The completed
saved-field census and source-weighted sensitivity now lead to the observed
L04 current redistribution; any later composition still needs a bounded
sparse/block design. The first ablation does not replace
the eventual multiconductor accuracy target or test L02 magnetic coupling.

Before another conductor mesh, benchmark that sparse/block design on the saved,
accepted L02 model: native-complement756887 and sheet856774 unknowns after gauge,
fixed principal-block LU preconditioning, exact cross-couplings, zero initial
guess, no dense Schur matrix, at most100 inner GMRES steps and300s/24GiB.
Record block/factor costs and the field before accepting physical KCL<1e-7A
and relative Z difference<1e-8 against the saved field. This is an algorithm
cost/convergence experiment, not another accuracy point or a promised L04 budget.
Actual preflight01 passes9.516s/1,590,972,416B with identical assemblyd20423cb…:
A00/CC/cross nnz are2,837,947/4,548,928/415,995, summing to8,218,865 after gauge.
Helper95e23a75… passes the local coupon and Sol's static review. Actual
block-Jacobi01 stops after100 inner iterations,83.109s/12,918,169,600B; PID35000
is reaped. Factor times8.640/6.766s improve on the monolithic factor, but GMRES
needs54.765s and KCL4.322717e-7A exceeds1e-7A. Z agreement2.75038e-11 relative
does not override that rejection. Field21520463… and diagnostic135dbb35… remain
unvalidated; independent review2e583d00… confirms the rejection. Saved-error
analysis93056ba9… suggests testing a source-derived harmonic common sheet mode,
with a small complex control and bounded operator/coarse-mode check before
another GMRES run. Keep the same physical model and numerical acceptance gates.

Harmonic preflight01 has completed30.547s/12.308GB and stopped before GMRES:
source-action relative mismatch2.920812e-10 exceeds its1e-10 preflight gate.
Independent review8d2aa3b7… accepts the rejection; saved mode106132ab… is reused.
No-factor attribution8b888ce9… takes11.531s/1.695GB with unchanged assembly and
identifies finite-via CSC/current evaluation and symmetric-scaling differences.
The next bounded candidate uses the existing direct source-current action for
both GMRES matvec and harmonic w/alpha, with CSC block LU as a preconditioner.
Record CSC/source discrepancies separately, retain zero initial guess and the
100-inner-step/300s ceiling, and keep final physical/CSC KCL<1e-7A unchanged.
Run a small action/reciprocity control and a60s preflight before deciding to solve.

Source-action preflightfbc38983… passes33.547s/12.621GB. Actual94a8b74c…
converges in80 steps and102.625s/13.598GB with physical KCL4.9191e-11A and
CSC residual1.17449e-10A; Z agrees with the accepted field to5.08367e-11
relative. Independent no-factor reviewe7830d4d… passes source/CSC KCL, triangle
actions, power closure and full-field comparison; the earlier wrong-runtime
blocked receipt61787f81… remains preserved. This is about6.3% lower peak
memory but11.4% longer elapsed than the single monolithic reference run;
do not promote it as a speedup or a new PowerSI accuracy point. Retain the
candidate and move to the joint source-contact ledger without more solver
tuning runs for this same matrix.

L04 inventoryc86cf341… completes5.016s/1.020GB from cached SQLite/arrays:
289islands,20um/59.59MS/m copper,36,755 traces and76,166 L04-touching source vias.
Its76,146 native boundary links own76,166 via segments, but the membership
differs by20 outside L04 owners and20 non-L04 segments inside. All20 exceptional
composite paths are already in the accepted L02 recovery: ten incident triples
and ten outside paths of5/7/12 total owners, each withtwo L04-touching segments.
Do not add the original composites again. The290 target aliases occur in none
of the36 native G/C tables; this does not prove zero physical capacitance.
Independent final review1759b05d… verifies every source/owner fact and the exact
clean external guard. Its earlier guard-filename mislookup is retained and
explicitly cleared. Conductor/contact qualification remains open.

Cached split02 result17ca350b…/review79d48106… accepts the source arithmetic
for10 endpoint remaps and10 further first-leg splits. Its60 Schur controls
restore the old paths within1.13663e-16;40→50 legs remains a prospective joint
topology, not an attachment decision. Geometry diagnostic328687c7… verifies
all ten native endpoint pads inside the289-island artwork but zero pad/drill
artwork overlap at the ten hidden sites. Local trace diagnosticcd568cd7… finds
all ten hidden60um pads touching five or six exact flat trace bodies, with no
40um drill overlap. Next qualify trace-to-artwork connectivity and the source
pad/material domain; do not infer isolation from artwork-only coverage or add
an electrical connection merely from a layer label. Combined review449ae8e8…
verifies receipts and recomputes all60 local trace/pad contacts; source artwork
overlaps are retained producer measurements. No new board model or mesh exists.

Local bridge resultfb62a6d0… completes20.532s/420.5MB and finds26 connected
pad→trace→artwork witnesses covering all ten hidden sites and nine source islands.
The289 source-island WKBs are cached ase8536def…; reuse them without decoding
the ZIP again. Independent review405a0b4c… accepts the26 local bridges by
recomputing against all289 cached islands. Prepare
the source-contact ledger with76,136 ordinary native branches, ten existing
L02-first-leg L04 endpoints and ten hidden L04 midpoints separately. Preserve
all76,166 L04-touching source via owners and the existing ordered composite
paths; source contacts are not the original76,166 compiled-owner set.

Keep a source-net coverage gate beyond this L02/L04 stage. The accepted
redistribution ledger4c7b7e62…/reviewd4723906… identifies DGND on33 layers,
including12 named OTHER_POWER and TOP:1,498 distinct source components and
2,573 islands. Select by component net, never by layer name. These are source
inventory counts, not qualified finite domains. Currents measured while the
remaining grounds are ideal are not an exclusion bound for the joint model.

Selected PWR source membership is also wider than its MAIN_POWER labels: the
accepted ledger has47 components/156 islands on L14/L20/L21/L25/TOP for
`adc_vdd_075_vtrip_sram/0`. The selected PWR+DGND topology totals1,545 components,
2,729 islands and35 layers; other nets' electromagnetic influence is not excluded
by this retained-rail census.

Contact ledger96c7396b…/NPZ6e5886f8… completes8.031s/434.9MB with clean guard
009b5fe5…. It preserves76,136 ordinary rows,10 endpoint-remap rows and20 source
via rows for10 hidden splits, plus38,278 exact XY groups and four pad/drill
definitions. Original/expanded indices and both ordinal conventions remain
separate. Sol accepted static/full-dry checks on frozenca3f25ed…; saved-artifact
review3b144bee… now accepts the saved arrays and source maps. Continue conductor/contact qualification under the common
reference-physics priority, without treating this metadata as electrical contact
coalescence or a completed joint field.

The reviewed numerical candidate partitions the expanded circuit as
`[[A00,E],[E.T,CC]]`, where A00 is the accepted gauge-reduced principal block
with L02 active349710 removed, including existing L25 RT0 currents. Keeping
other-endpoint diagonals exactly once is essential; require separate finite/G-C
and complete `P.T @ Aexpanded @ P == Aoriginal` recollapse checks. CC requires
the actual491-island connectivity, including source traces and pad bridges.
Those physical bindings are not supplied by a numerical partition.

Prefer full sparse block action with fixed block preconditioning. If a Schur
action is needed, apply `CC*u-E.T*solve(A00,E*u)` without storing it densely.
This follows the block factorization in the
[PETSc field-split documentation](https://petsc.org/release/manualpages/PC/PCFIELDSPLIT/);
the existing [SciPy LinearOperator](https://docs.scipy.org/doc/scipy/reference/generated/scipy.sparse.linalg.LinearOperator.html)
can supply matrix-vector actions. These are algorithm references, not a new
PETSc dependency or a runtime guarantee. The reciprocal complex matrix uses
transpose rather than Hermitian transpose; no SPD/CG assumption. Sol accepts
this conditional algebra, but contact/domain/mesh sizing precedes implementation
and measured memory/physical-current gates still determine feasibility.

Parallel source diagnostic: the350-via native-diagonal/axial-mutual compatibility
check completed0.569s with a positive finite spectrum (minimum1.20191e-10H;
native-diagonal-normalized minimum0.647577). It includes175 disjoint same-axis
upper/lower pairs and does not stamp a board operator or establish full return/
native internal-external ownership. Receipt `b41c3986…` is independently accepted;
review SHA `e8339cc4044045a712ca265d7e99dcddf3799b65d7fa7a6e26e988532d2955a7`.
The first full solver stopped without an accepted result; its recovered candidate
retains failed power closure and unknown original GMRES info. The now-completed
continuations used saved candidates only as initial guesses and
a fresh `R+jwLself` sparse-LU preconditioner with residual `-jw(Lfull-Lself)q`.
This changes only the numerical solve; it neither adjusts physical coefficients
nor uses PowerSI to choose them. Exact-self preconditioning may help but is not
promised to remove the remaining far-field iteration cost.

Run02 was launched by HQ at09:35 KST after packed UTF-8/proportional-size,
numeric-first persistence and real native toy checks passed. PID34744/session62679,
output `astra-native-loaded-vtrip-field-02`,1800s/24GiB. Driver SHA
`5d09ee1ffc413215e641275b9dd1851ff716f7f86aea279a10228c099ee452cd` is frozen inside
that run root. Numeric/raw/derived NPZ all passed independent CRC/non-pickle,
mapping and physical-field checks. Recovered manifest SHA is
`a34fbb39eef165724bf0aaaaedd8b5285b25440a07c28f9a202463148afc6d0a`.
The110-island external current ledger maps all2110 source via edges and both
adjacent original G/C partials; quotient KCL residual4.291e−11A. About0.184049A
must pass from the small islands to the main island under the native1A drive.
Native ideal Trace currents are not observable; the later filled-core electrodes
are explicit conditional assumptions, not fabrication or convergence certificates.
NPZ-only baseline reconstruction passed in48.906s: Zdd/full-field absolute errors
1.0695e−14/1.0244e−14Ω. The first receipt's broken RSS sampler does not prove a
24GiB peak; its ctypes HANDLE declarations were corrected without rerunning LU.
This is strict saved-field recovery, not independent recertification of every
production guard. Conditional1D Trace-R ideal-limit sensitivity is0.563µΩ;
ε=1 effect cannot be inferred from this derivative. Both the centerline Trace-R
comparison and the subsequent full-sheet DC comparison are now complete, with
the distinct outcomes recorded in6G/6H above. No native compile was repeated.

### Historical Step 4–5 context

The following missing-response statements record the earlier sequence. The
completed Step6 native responses above supersede them; do not schedule their
generation again or treat them as an external-input blocker.

Step 4 has resolved the earlier scoped search for matching saved artifacts:
D115b contains an ownership/raw/topology set with matching source, project,
certificate and geometry identities. Restoration is not a new import/compiler
run and does not repair the historical D096 missing project binding.
The existing full-class shadow seam is real, but its preserved boundary includes
nine external couplings (16.994887 nF) around the selected 340.221414 pF candidate;
its P7 recipe also fixes 1 GHz. Thus neither a few-contact wholesale replacement
nor the previously suggested direct 1 MHz recipe call is ready. Keep exact
G/C-only correction and distributed R/L replacement as different physical changes.
The saved topology lacks the numeric G/C base and factorization; these are
derived quantities that the existing solver can assemble from the saved
source scenario. Attempt-01 has now completed that physical assembly.
Source return geometry and a replacement's finite current/port representation
remain the immediate research focus. Steps 4D/4E provide a concrete local DC
trace candidate without altering the original failed meshes or production
guards. Its effect on Device Zii still requires the matching loaded/unloaded
native response, including the local-branch-to-Device transfer and retained
G/C context. Do not multiply the local R change by an assumed unit board current
or report the local MNA result as a PowerSI improvement. Generate the actual
native response from the saved scenario, then use the smallest defensible update.
The existing report inventory has no numeric response beyond hashes/aggregates.
For Step 4F the minimum sufficient input is the source/project/code/profile/port
bound complex Device/Via336274-endpoint 2x2 response at 1 MHz, separately
loaded and unloaded, or a compatible existing numeric base/factorization.
The executable rank-one witness is synthetic and does not fill this gap.

Step 5A is governed by
`evaluation-research/astra_l21_dc_acceptance_2026-09-07.json`, which pins and
reviews frozen receipts without a mesh rerun. The finest local pair resistances
span 0.193729–0.452940 mOhm. Keep the three qualified filled-microvia electrodes
separate from the core whole-pad approximation. Preserve the first Triangle
receipt's original raw PASS but treat it as rejected; its eight-edge boundary
plateau and positive off-diagonal DC Y invalidate acceptance. Later boundary
subdivision uses exactly the same polygon and retains every containment/contact
guard. The accepted result has sampled local refinement evidence, not a rigorous
error bound, production mesher fix, current-distribution certification or W6 gain.

**Astra next-candidate decision after Step 5C:** keep Trace311318 as the first
candidate for an eventual board update. Its qualified filled-microvia electrodes
and isolated degree-two native boundary have narrower unresolved assumptions.
Do not promote the L21 four-terminal block first: its fifth spatial G/C
attachment and 9–84% core-electrode sensitivity are additional physical gaps.
Obtain the matching trace/Device two-port response and apply the already-checked
rank-one identity; report the actual Device change against the frozen comparison
before broadening to any L21/core or multiple-block correction. More local mesh
refinement is not the next accuracy milestone.

Step 1B is a local mathematical/material experiment, explicitly reopened under
the user's resumed research instruction. It cannot alter a production parameter,
clear WP2, prove spatial material fill, or improve a PowerSI score by itself.
The full 16-cell/FasterCap path remains dependent on its complete input contract;
that contract does not block independent literature, analytic work or sealed
material-table experiments. No product runtime, installer or release change is
planned here. When product behavior is actually promoted, version, installer and
GitHub delivery follow the user's normal product workflow.

**Candidate boundary after evidence review:** retain A1's selected no-decap
L30/L29 `RAIL_REACHABLE_DIELECTRIC_GAP_MAXWELL_GC` experiment. Its approximately
16.4% equivalent-C deficit is not a model for the full loaded 15.911 dB error.
Step 1B informs material feasibility only. A Triangle replacement requires a
measurement of C1's expensive stage; it is not the prerequisite for step 2's
independent physics experiment.
For the broader accuracy objective, step 1D starts a bounded canonical
loaded/unloaded sheet/return experiment from the existing MFDM code independently
of C1 and material fitting. Frozen loaded 10–100 MHz
errors are negative (roughly -18 to -28 dB), so a bare low-band C correction
cannot be presumed to fix them. Test the differential/absolute-node interface
explicitly; do not stamp a local-nullspace operator into global-Y by analogy.
The [Astra dependency analysis](EVALUATION_SOLVER_DEEP_RESEARCH_2026-08-04.md#astra-evidence-review-actual-error-and-input-dependencies)
separates spatial fill authority, generated orientation witnesses, boundary
conditions and relative-coordinate invariance. Real-board WP2/WP3 remain
PARTIAL; no missing record is relabeled SEALED by this planning change.

Use the existing W6 sidecar as the accuracy result. Its three A1 hashes were
reverified; a report-level execution/completeness PASS is not a numerical PASS.
Track new work as material diagnostic / mesher feasibility / source-bound
physics / retrospective accuracy / unseen accuracy, never as one aggregate
percentage or as a count of accepted controller tests.

**Execution update:** step 1A is `ACCEPT_STATIC` after Astra checked the pinned
source and receipt. C1 certification contains at least 314,154,300 Python loop
visits, but -07's actual stage timings remain unknown. Its conditional 120 s
four-marker probe is not executed; it is not a prerequisite for steps 1B/1D.
The [diagnosis](evaluation-research/ASTRA_C1_STAGE_DIAGNOSIS_2026-09-06.md)
records that distinction. Step 1B is
`ACCEPT_DIAGNOSTIC / DEFER_PRODUCT_PROMOTION`: the final sealed-input run took
0.0045392 s and the independent analytic check passed. Balanced Dk RMSE is
0.0565969 for ABF and 0.0395323 for EL190T. ABF's rising Dk conflicts with the
positive-Debye family; EL190T's 6-equation/10-coefficient system is
underdetermined, while ABF's condition estimate reaches about `1.09e14` under
the second weighting policy. Neither low-band extrapolation nor a unique
physical spectrum is established. Keep product materials unchanged; see the
[material report](evaluation-research/ASTRA_MATERIAL_STUDY_2026-09-06.md).
Step 1D is
`ACCEPT_DIAGNOSTIC / STOP_CONVERGENCE`: 12 runs in 0.047 s, 32-cell matched
loaded-reference error at most 0.895 ppm, but 16-to-32-cell change 5.7636%
exceeds the 2% gate because the center-cell ports move. The recorded status
stays STOP; small residuals and analytic agreement do not close this gate.
The first-order propagated error estimate also reaches 58.12% and is not a
rigorous bound. See the
[loaded-sheet report](evaluation-research/ASTRA_LOADED_SHEET_STUDY_2026-09-06.md).

**Step 2A completed:** physical ports stay at `L/8` and `7L/8` on 4/12/36-cell
grids, using the existing point-port API and unchanged material/load and 2%
refinement gate. The finest loaded analytic error is `6.55570e-7` relative
(0.656 ppm), and 12-to-36-cell change is `5.24461e-6` relative (0.000524461%).
The [fixed-port study](evaluation-research/ASTRA_FIXED_PORT_SHEET_STUDY_2026-09-06.md)
is `ACCEPT_CANONICAL_ONLY`; Astra's executable self-check passed. The earlier
moving-port STOP is preserved. Only 3/12 individual solve-quality flags pass;
the first-order error estimate reaches 73.59% and is not a rigorous bound.
Sampled analytic agreement does not establish general numerical reliability.

**Step 2B/2C executed the composition checks:** the
[interface audit](evaluation-research/ASTRA_GLOBAL_Y_INTERFACE_AUDIT_2026-09-06.md)
confirms that local differential terminal admittance cannot be naively added
to an absolute global-Y network. Existing product code already documents and
guards this limitation. Native plane G/C also remains stamped when supplemental
Y is added; its current ownership check covers Via/termination, not exact plane
partial replacement. The live Layerwise caller does not forward this shadow seam.

The [four-terminal probe result](evaluation-research/astra_surface_global_y_interface_2026-09-06.json)
records a singular Kron block at `P0,G0,P1,G1` for the continuous strip under
the additive nodal route. Its separate duplicate-base control produces exactly
twice the one-stamp admittance despite the same declared owner. On continuous
TOP P/BOT G artwork, spatially separated P/G footprints stop with
`no admissible return mode`. These are useful diagnostic results, not missing
conductor evidence or a negative physical-passivity result.
The recorded JSON is the original four-terminal numerical output. Its older
boolean gates use an absolute `1e-18` row-sum threshold and mark unavailable
network results false. These frozen flags are not a physical-passivity verdict.
The current script separates expected multi-gauge STOP from scale-normalized
condensation checks; Astra's final self-check of that correction passed without
rewriting the recorded numerical result.

Astra therefore reused `SeriesBranchBlock` for a narrowly scoped positive
composition witness instead of adding an adapter. The
[runnable paired-branch probe](../tools/research/probe_balanced_sheet_branch.py)
and [result](evaluation-research/astra_balanced_sheet_branch_2026-09-06.json)
retain two launch-pair gauges and mutual coupling on one continuous 3 x 1 mm
synthetic strip. They agree with loading the mesh before condensation to
`2.97182e-10` relative; changing which nodes fix the two gauges changes the
port result by zero at the recorded precision. This is
`ACCEPT_PAIRED_CANONICAL_ONLY`, not a source-bound or live Evaluation adapter.
It does not provide a physical return for spatially separated P/G pins.
Current product artwork equivalence also collapses spatial launches on the same
conductor component. A board successor therefore needs a source-bound local
terminal partition and exact removal of the replaced native G/C, not merely
forwarding a new matrix argument. Preserve the current default until that
physical replacement is defined and independently checked.

Steps 1A–1D and the scoped Step 2 experiments are complete, including the
explicit negative results above. Step 2 advances composition with a positive
paired witness and a separate rejection-oriented interface audit/probe. All
real-board accuracy gates remain unfinished. This is not completion of the
Evaluation accuracy objective.
The original two source-document hashes remained unchanged during integration;
the active worktree contains the research changes. The focused checks passed,
including Astra's final nodal-probe check (1.61036 s shell) and the independent
loaded-mesh/paired-branch comparison. Terra reviewed the paired-branch result
without another numerical run. See the
[Sol supervision record](evaluation-research/ASTRA_STEP2_SUPERVISION_2026-09-06.md).
The rejected initial two-terminal output is preserved, without changing its
bytes, at `outputs/research/rejected/astra_surface_global_y_interface_2026-09-06.initial-two-terminal.json`
(SHA-256 `bdea1f5234c7d6683f3cd8576068d0ff0fd66fe9e23e6d9784399ecf1740b7b5`).
Its provisional ACCEPT is superseded by the four-terminal diagnostic STOP;
it is not accepted evidence for distributed-sheet composition.
The scoped diff has no whitespace errors. The shared local runtime
under ignored `outputs/research-runtime` supplies missing existing project
dependencies without changing `pyproject.toml`.

Astra owns algorithm choice, integration and evidence acceptance. Under the
user's latest instruction, Sol supervises Luna implementation and Terra audit
with disjoint file ownership; this supersedes the earlier no-intermediate-manager
arrangement. Native slots are sufficient, so no additional user-owned task was
created. Lower models use their supported efforts (Sol ultra, Luna max, Terra
ultra); Spark is not advertised by the current native spawn schema. Keep read-only immutable artifacts,
>=10 min/event-driven long-job observation, advisory usage checkpoints and
user-only reset credits. Never access the user-excluded parser file.

**Step 3 executed:** Astra independently confirmed the existing source COPPER
row at 20 C using its D103-sealed 94-byte interval and hash. D096 already assigns
that `5.959e7 S/m` value and 20 um thickness to the selected L29/L30 conductors;
this is not an earlier product-default defect. The
[new exact-record evidence](evaluation-research/astra_source_copper_record_2026-09-06.json)
and [algorithm continuation](EVALUATION_SOLVER_DEEP_RESEARCH_2026-08-04.md#step-3-continuation-actual-source-and-conductor-current-alternative)
separate that material check from source contact and numerical ownership.
The [Step 3 report](evaluation-research/ASTRA_STEP3_SOURCE_CANDIDATE_2026-09-06.md)
records actual TOP-pad discovery, the source L21 power-plane bridge and the
bounded L21 FEM result. A 0.078 s geometric contact check connected the source
records omitted by the original trace/via-only graph; it is not a complete
barrel-material, GND or source-owner acceptance. The 0.109 s two-contact FEM
study retained `STOP_LOCAL_SHEET_MESH` after its coarse result. The two GND
frontier disks are now confirmed on L18/L20 copper, and an existing-kernel
mutual-L diagnostic has run on three actual via pairs. The full L29 return and
mutually matching topology/raw/ownership artifacts remain the next source
requirements; do not rerun canonical studies or refine local DC R indefinitely
as a substitute. No full-network replacement or new PowerSI score is claimed.

## Historical pause checkpoint — 2026-09-06 (superseded operational scope)

> The canonical current checkpoint is the [2026-09-06 deep-research
> checkpoint](EVALUATION_SOLVER_DEEP_RESEARCH_2026-08-04.md#current-checkpoint-2026-09-06).
> This compact status does not rewrite the 2026-08-04 plan.

- **Authority and objective.** `SPD Decap PI Evaluator v0.23.1` is on `main` at
  `e2f219e71d8c8a397009f72242cce10d78cfc7ab`; the objective remains
  source-derived algorithms for Evaluation Analysis agreement with supplied
  PowerSI Touchstone data. Deterministic/non-regression evidence is not an
  accuracy claim; Distribution remains secondary and out of scope.
- **Historical estimate (retired).** The unmeasured **35–40%** described geometry/input-
  feasibility Stage2 is materially advanced. C0 is a scoped
  `PASS_C0_FROZEN_PSLG_CLEARANCE_GT_2DELTA` for frozen split PSLG only. C1
  `-07` is a terminal one-call wall-deadline `STOP`; WP3 production-03 has
  accepted control-plane evidence but remains `PARTIAL / STOP_NOT_REPRESENTED`.
  WP2 remains `PARTIAL`: two blockers are now conclusively
  `STOP_NOT_REPRESENTED` in native SPD and three remain `STOP_UNSEALED`.
  Solver/PowerSI evaluation remains unopened.
- **Next gate.** Do not repeat the completed C1/WP3/WP2 roots. Acquire or define
  source-authoritative evidence for the five WP2 facts while independently
  assessing a non-Triangle mesher or a smaller nonnumerical C1 feasibility
  decomposition. Close aggregate 16-cell/WP2/WP3 authority before solver/
  PowerSI correlation; consumed/tombstone roots are never rerun or reused.
- **Workflow.** **Sol supervision -> Luna execution -> Sol independent review**;
  parallelism is limited to disjoint prerequisites. Poll long jobs at >=10 min
  unless a terminal, failure, or resource event occurs.

This checkpoint authorizes or claims no runtime/code change, numerical execution,
or release.

### Reboot-safe resume point

- C1 `-07` is frozen at
  `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-cell258-c1-single-call-07`;
  controller receipt SHA-256
  `8d5bcabe2b9ab4e93c002f8fc3184eaadde7654bdeb7dda7079c2b11790c5288`.
  Triangle determinism was not evaluated and this root must not be retried.
- WP3 production-03 is frozen at
  `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-wp3-selected-pin-source-path-absence-production-03`;
  accepted controller receipt SHA-256
  `11cc90e066b49a600287d288cbffcde00cd2e1182e7894e5ddeebf3d851576cd`.
- WP2 production-01 is frozen at
  `D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260906-d117-wp2-src-xy-auth-00-production-01`;
  receipt SHA-256
  `6585a349929273f8524c206af95f54dc61581f066fe02b10c7d3d4f909deaae2`.
  Its complete bounded scan found no explicit native-SPD representation for
  XY dielectric partitions or conductor-layer void fill. Do not add an
  inference path or rerun the scan; resume by obtaining authoritative evidence
  and sealing the three remaining WP2 source facts.
- No solver, FasterCap, PowerSI correlation, release, commit, or publication is
  authorized by this pause checkpoint. The primary objective remains
  source-derived Evaluation Analysis accuracy against supplied PowerSI data.

## 1. Delivery rule

The current v0.17.0 modal solver remains the default and its scenario format
remains readable throughout development. New physics is introduced as a
sequence of independently falsifiable backends. A phase may merge into the
research branch when its own tests pass, but it may not become the product
default until the end-to-end promotion gates pass.

No parameter may be fitted to PowerSI. PowerSI and measurements are untouched
comparison data. Source-derived SPD geometry, stackup, material tables, via
dimensions, model assignments, and explicitly documented MLO filled-microvia
rules are the only production inputs.

The order is deliberately:

```text
comparison contract
  -> source topology certificate
  -> conservative actual-artwork layer kernel
  -> passive global MNA
  -> shared-sheet/material/via physics
  -> conditional homogenization
  -> blind validation
  -> sampling/acceleration/MOR
  -> guarded product rollout
```

The low-band uniform-admittance and causal-material ideas may run earlier as
research experiments. They are not substitutes for the topology and layer
network.

## 2. Compatibility boundary and stable entry points

The following tracked entry points remain stable while their internals acquire
new backend interfaces:

| Existing area | Planned responsibility |
|---|---|
| `src/spd_decap_pi/_core/io/spd.py` | parse source evidence without solver assumptions |
| `src/spd_decap_pi/spd_adapter.py` | build source-domain objects and topology inputs |
| `src/spd_decap_pi/_core/domain.py` | persist source facts, solver profile identity, and audit summaries |
| `src/spd_decap_pi/evaluation.py` | preserve Original/Tuned atomicity, cache identity, and connectivity preflight |
| `src/spd_decap_pi/_core/solver/evaluator.py` | select/compile a backend and return the existing evaluation result contract |
| `src/spd_decap_pi/_core/solver/modal.py` | retain the disclosed legacy rectangular solver and analytic regression cases |
| `src/spd_decap_pi/gui/main_window.py` | expose guarded profiles, progress phases, cancellation, and result provenance |
| `src/spd_decap_pi/gui/worker.py` | keep GUI-thread isolation and coalesced progress delivery |

New persisted fields must have safe defaults. Opening an old `.spdpi` must not
silently select an experimental solver. Cache entries from one backend or
material realization must never be reused by another.

## 3. Architectural contracts to define before kernels

### 3.1 Evidence and comparison contracts

Proposed immutable records:

- `PortManifest`: name, ordered index, rail, physical launch, observation node,
  reference conductor, reference impedance, renormalization, termination/open
  state, and driven-observation convention;
- `ReferenceRunManifest`: Touchstone hash, frequency grid, available PowerSI
  material/mesh/solver/convergence settings, and S-to-Y/Z conditioning report;
- `SourceEvidenceManifest`: SPD hash, parser version, stackup/material table
  hashes, geometry-asset hashes, and source-only assumption IDs;
- `TopologyCertificate`: compiler version, deterministic node/edge/face IDs,
  connectivity components, unresolved evidence, and an ownership ledger.

The validator compares complex S/Y/Z views where numerically meaningful and
reports transform condition number and backward residual. A port-manifest
mismatch fails before any metric is calculated.

### 3.2 Layer/operator contract

Each adjacent conductor gap implements one common contract:

```text
LayerGapOperator
  evidence_hash
  interface_terminal_ids
  conductor_face_ids
  material_descriptor_hash
  assemble_sparse_stamp(frequency_or_descriptor)
  apply(vector, frequency)              # optional query interface
  reduce_to_ports(port_ids)              # sparse solves, no explicit inverse
  energy_and_conservation_diagnostics()
```

The production representation is a passive sparse descriptor/stamp. A query
interface is defined early so a future iterative or matrix-free backend does
not require architectural surgery, but no specific fast method is selected
before scale benchmarks.

Every physical term has exactly one owner:

- a dielectric gap owns its transverse `G/C` field;
- a copper sheet owns its surface `R/L` operator once, even when it bounds two
  gaps;
- a via/pad/anti-pad/local field block is either the core representation, a
  replacement, or a declared `exact - core` defect;
- no block may be added merely because it was computed by a different method.

### 3.3 Fixed substrate and mutable branches

The compiled geometry, material descriptors, layer stamps, vertical
connections, and interface reduction form an immutable
`FixedSubstrateModel`. Decap models and placement states remain mutable branch
stamps. A substrate reduction may eliminate internal nodes only if the complete
set of possible device, decap, load, and observation attachment ports is
preserved.

## 4. Phase plan and exit gates

### Phase 0 — comparison contract and benchmark harness

Planned changes:

1. extend `scripts/validate_powersi_reference.py` to emit the two manifests,
   condition numbers, backward residuals, and consistent complex S/Y/Z metrics;
2. version the benchmark schema and bind every result to source, code, solver
   profile, port manifest, and material assumption hashes;
3. separate retrospective development, manufactured/oracle, and future blind
   reports; reject any report that mixes them;
4. record the current 401-point and adaptively refined 432/433-point curves as
   dense hidden truth for later sampling experiments.

Exit gate:

- a deliberate port reorder, reference change, wrong `Z0`, or source hash
  mismatch fails closed;
- existing correct manifests reproduce the current comparison metrics within
  serialization roundoff;
- no reference value is exposed to a production parameter builder.

### Phase 1 — source-faithful topology compiler

Proposed production modules:

- `_core/io/source_topology.py`: deterministic contact/connectivity compiler;
- `_core/models/topology.py`: immutable nodes, terminals, conductor faces,
  vias, traces, ties, evidence, and certificate records;
- `_core/solver/ownership.py`: one-owner ledger and double-stamp validator.

Compiler work:

1. resolve polygon/circle/trace/via contact using source tolerance and explicit
   provenance;
2. require source trace width or fail closed for electrical trace stamping;
3. resolve DGND components and tie paths, including locations, dimensions,
   layer span, and worst terminal-to-tie distance;
4. preserve shared-pad capacitor terminals and physical shared PWR/GND vias;
5. classify MLO microvias as solid copper only under the documented qualified
   geometry rule; otherwise retain the conservative barrel model or unresolved
   evidence;
6. assign stable IDs to both faces of a physical copper sheet and prove whether
   each face participates in an adjacent gap;
7. report periodic-cell candidates without yet enabling homogenization.

Exit gate:

- the named SPD compiles without losing source terminal identity;
- each modeled terminal has one deterministic path to its owning conductor;
- component counts and unresolved items reconcile to the raw source records;
- randomized record order produces an identical certificate hash;
- ambiguous contact, width, net, face, or fill evidence blocks only the affected
  rail with an actionable message.

### Phase 2 — low-band research bridge and causal-material study

This phase is research-only and contains two independent experiments.

#### 2A. Actual-artwork uniform-admittance bridge

Compare, in order:

1. the legacy rectangular constant mode;
2. scalar actual-artwork `C00` as a deliberately limited control;
3. per-gap, multi-net uniform `Y00(f)` with floating conductors Schur reduced;
4. the complete actual-artwork quasi-static operator on selected cases.

The bridge must quantify missing constant-to-high-mode `K0n` terms, demonstrate
passivity and charge conservation, and report both no-decap and loaded complex
`Zii`. A capacitance improvement alone is not an exit result.

#### 2B. Causal material candidates

Keep the source table and current clamped interpolation as controls. Fit
Djordjevic-Sarkar/wideband-Debye and generalized-Debye candidates using only
the SPD Dk/Df rows. Record fit residual, parameter identifiability,
Kramers-Kronig/positive-real checks, and bounded extrapolation uncertainty.

Exit gate:

- neither experiment reads PowerSI during model construction;
- every candidate is passive/causal over a wider audit band than the requested
  solve band;
- promotion requires loaded end-to-end improvement without worsening phase,
  resonance, or held-out rails; otherwise the experiment stays diagnostic.

### Phase 3 — conservative actual-artwork layer-domain reference path

Proposed modules:

- `_core/solver/layer_domain.py`: common operator and descriptor contracts;
- `_core/solver/triangle_layer.py`: nonuniform Delaunay/Voronoi or mixed-FEM
  assembly with exact source boundaries and finite ports;
- `research/oracles/cim_pair.py`: optional small selected-pair CIM, never
  imported by the application;
- `research/oracles/canonical_cases.py`: analytic and mesh-refined references.

Requirements:

1. mesh actual positive/negative artwork and locally refine at ports, slots,
   necks, and material discontinuities;
2. integrate circular/actual finite ports rather than point sampling;
3. use conservative incidence/dual-cell assembly so charge and energy identities
   hold by construction;
4. expose mesh convergence, reciprocity, passivity, and scaled residuals;
5. cross-check rectangles/circles analytically and selected small irregular
   pairs against at least one independently implemented SIE/FEM/CIM oracle.

CIM begins at approximately 2k-4k boundary unknowns. Its peak memory and
factorization time are measured; the earlier `2k-8k`, `<=1 GiB`, and
"seconds" forecasts are not acceptance facts.

Exit gate:

- analytic/manufactured cases converge at the expected order;
- triangle and independent oracle complex port matrices agree within a
  conditioning-aware tolerance, not only `|Z| <= 0.5 dB`;
- reciprocity, charge conservation, passive energy, and mesh refinement pass;
- the named SPD's real terminal count, mesh count, nonzeros, and memory are
  reported before choosing a production sparse backend.

### Phase 4 — passive global MNA and adjacent-layer composition

Proposed module: `_core/solver/global_mna.py`, promoted only after review of any
local research prototype.

Requirements:

1. stamp each adjacent-gap operator, conductor-face operator, via/tie branch,
   source/load port, and floating conductor into one global sparse system;
2. keep shared conductor and via nodes explicit across layer boundaries;
3. eliminate internal interfaces with sparse solves and Schur/Kron reduction;
4. never compose the branched PDN by scalar addition or an unqualified transfer
   matrix product;
5. prepare independent gaps in bounded Windows worker processes, then assemble
   deterministically in one ownership ledger;
6. separate symbolic topology from frequency-dependent values where the chosen
   sparse backend actually supports safe reuse.

Backend selection is benchmark-driven. SciPy SuperLU is the baseline; no plan
may assume CHOLMOD, PARDISO, MUMPS, or symbolic-only reuse until the dependency,
license, packaging, and Windows behavior are proven.

Exit gate:

- a synthetic multilayer case matches a monolithic reference;
- removing any one sheet/via stamp changes the ledger count exactly once;
- port reduction preserves reciprocity, passivity, and full-system residual;
- 30k/60k/100k/150k *actual assembled matrices*, where attainable, report
  `N`, `nnz(A)`, `nnz(L+U)`, fill ratio, ordering, pivot growth, factor/solve
  time, and peak RSS.

### Phase 5 — shared-sheet and constitutive physics

Planned changes:

1. add a reciprocal two-face finite-thickness copper operator with the coupled
   `coth/csch` limit only for sheets whose two faces are explicit interfaces;
2. preserve the current one-face slab as a regression and isolated-pair limit;
3. stamp the selected passive causal dielectric descriptor per physical gap;
4. model DGND ties as explicit R/L/connectivity paths when an ideal-common
   reference cannot be source-proven;
5. verify DC, thin/thick conductor, one-face, symmetric/antisymmetric face, and
   zero-loss limits.

Exit gate:

- no physical sheet is counted twice;
- limiting cases reduce to independent analytic results;
- the Hermitian loss/energy parts are nonnegative under scale-aware tolerance;
- VQPS C/R/L regime diagnostics improve without reference fitting.

### Phase 6 — intrinsic via-plane and local 3-D replacement

Proposed module: `_core/solver/via_intrinsic.py` behind the same local-block
contract as any PEEC/SIE alternative.

Requirements:

1. define via-domain and plate-domain modal splits, reference planes, port
   voltage/current normalization, pad/anti-pad geometry, and return terminals;
2. include self/mutual effects for single via, via pair, dense cluster, shared
   antipad, and filled microvia canonical cases;
3. replace the core local block or stamp `A_local,exact - A_local,core`;
4. prohibit independent addition of via L, antipad C, and spreading terms when
   the plate or intrinsic kernel already owns them;
5. validate local decomposition against independent 3-D/SIE/measurement
   canonical structures. The 92-port board cannot uniquely identify internal
   self/mutual L.

Exit gate:

- local port matrices pass reciprocity/passivity and reference-plane
  invariance checks;
- single/pair/cluster canonical cases meet the local oracle gates;
- exact-core replacement reproduces the exact block when reassembled;
- loaded board correlation improves without worsening VQPS or phase gates.

### Phase 7 — conditional perforation homogenization

Homogenization is enabled only for a compiler-certified periodic or locally
stationary interior cell family. It is never applied across a port, edge, slot,
net boundary, mixed pitch, or unresolved cell.

Experiment:

1. extract anisotropic effective `R/L/G/C` or a passive descriptor from a
   source-only periodic unit-cell solve;
2. grow the exact halo through `1/2/3/4/6/8` local pitches;
3. compare complex port operators, stored/dissipated energy, and local current
   against a fully explicit mesh;
4. choose the halo from a-posteriori convergence, not a fixed `2-3 pitch`
   assumption;
5. report the fraction of the real SPD actually eligible and the net memory/time
   benefit.

Exit gate:

- scale separation and stationarity are proven for each enabled zone;
- the explicit-vs-homogenized error stays inside the allocated local budget;
- transitions remain passive and do not create artificial resonances;
- a geometry change that invalidates the cell certificate disables
  homogenization and falls back to exact meshing without changing source
  identity.

### Phase 8 — adaptive sampling and measured acceleration choices

Adaptive frequency sampling is a sample selector, not final truth.

Requirements:

1. force band endpoints, decade anchors, metric frequencies, source material
   breakpoints, device SRFs, known/predicted resonances, and antiresonance
   brackets;
2. use Bayesian-VF, vector fitting, AAA-derived disagreement, or another
   uncertainty policy only to request the next expensive solve;
3. use one union grid for Original/Tuned and all compared rails;
4. retain independent midpoint/pole-bracket holdouts and finish with a hidden
   dense sweep before promotion;
5. compare complex error, peak frequency/Q, solve count, fit overhead, wall
   time, and peak memory.

The historic 4,139 s and later approximately 224.95 s runs are different
profiles. Neither supports a `5-10x` promise. Every speed report identifies the
exact source/code/profile hashes.

After measured matrix/operator scale is available, choose among sparse direct,
iterative preconditioning, H2/FMM, matrix-free Ewald/NUFFT, or localized direct
tails. A query/linear-operator interface exists from Phase 3, but implementation
of a particular accelerator is a separate go/no-go decision.

Exit gate:

- dense hidden truth passes all complex and feature gates for every rail and
  state;
- speedup is measured end to end, including fitting and verification;
- missed/narrow resonances or passivity failures automatically fall back to the
  dense adaptive solver.

### Phase 9 — passive MOR, cache, UI, and guarded rollout

Only after the fixed substrate has passed physics and blind gates:

1. realize frequency-dependent material, copper, via, and nonlocal blocks as a
   passive causal descriptor;
2. apply PRIMA or another passive MOR while preserving the complete mutable
   attachment-port subspace;
3. keep all decap branches outside the fixed substrate only when that preserved
   port set is complete;
4. expose `Legacy modal`, `Experimental layer network`, and eventually
   `Validated layer network` profiles with plain-language confidence and
   provenance;
5. retain one-click rollback to the legacy backend and never rewrite a scenario
   merely because a research profile was selected.

Exit gate:

- reduced and unreduced substrates agree over a dense audit grid and preserve
  passivity;
- Original/Tuned cache reuse is identity-safe;
- a frozen candidate passes retrospective and genuinely unseen validation;
- only then are version, installer, and release changes planned.

## 5. Cache and solver-profile identity

Every numerical cache key must include at least:

- SPD and geometry-asset hashes;
- parser/compiler version and `TopologyCertificate` hash;
- port-manifest and attachment-port-set hashes;
- backend algorithm and solver version;
- material source-table and causal-realization hashes;
- mesh policy, realized mesh, exact/homogenized zone, and local-oracle version
  hashes;
- ownership-ledger version;
- sparse backend, ordering/scaling policy, and tolerances;
- requested/realized frequency grid and adaptive-sampling policy;
- decap-model and Original/Tuned branch identity where mutable results are
  cached.

The fixed-substrate cache excludes mutable population state but includes the
complete eligible attachment-port set. Cache objects are immutable, checksummed,
written atomically, and discarded when the application or source identity no
longer matches.

## 6. Validation matrix

| Level | Cases | Required evidence |
|---|---|---|
| Unit/property | constitutive fits, face slab, incidence stamps, Schur reduction, local replacements | dimensions, limiting cases, symmetry, passivity, conservation, scaled residual |
| Manufactured | rectangles, circles, slots, floating islands, two/three gaps, one/pair via | analytic value plus mesh/order convergence |
| Independent oracle | selected small pairs and local via structures | complex matrix agreement with analytic, CIM/BEM/SIE/FEM/measurement truth |
| Source compiler | named SPD plus adversarial contact/width/net cases | deterministic certificate, complete reconciliation, fail-closed diagnostics |
| Retrospective board | all ten VQPS, all 92 ports, VTRIP/VINT/VCPU `/0` and `/1` | broad and regime-specific errors, phase, reciprocity, conditioning, no fitting |
| Blind | frozen code/profile predictions against unseen reference | preregistered hashes and pass/fail report |
| Performance/UI | named SPD, cold/warm cache, load/compile/solve/cancel/render | wall time, CPU/RSS, cache hit, cancellation latency, UI heartbeat |

The existing broad promotion gates remain authoritative. Claude's proposed
`C_eff/R/L/resonance/Q = 5/10/10/5/20%` values begin as diagnostics because
their measurability and reference uncertainty have not yet been established.

## 7. UI and process plan

The present application already runs operations through `FunctionWorker`,
coalesces progress events, validates stale results, and keeps scenario updates
atomic. Preserve those behaviors and add:

1. worker-side parsing, topology compilation, meshing, factorization, frequency
   solve, MOR, serialization, and plot-data preparation;
2. bounded process workers for independent CPU-heavy gap preparation on Windows,
   with BLAS thread count fixed to one inside each process;
3. concurrency chosen from measured per-gap peak RSS, not CPU count alone;
4. named progress phases with monotonic work units and visible cache-hit status;
5. cooperative cancellation checkpoints before/after mesh batches,
   factorization, frequency groups, reductions, and cache commit;
6. operation-generation and source/profile identity checks before accepting a
   result, even when cancellation races with a queued Qt signal;
7. incremental plot/table rendering and deferred optional diagnostics;
8. a visible solver name/version/profile and a clear `research`, `retrospective`,
   or `blind-validated` badge in results and exports.

Product targets remain six selected rails within 120 s after a warm substrate
cache, peak solver memory within 4 GiB, UI heartbeat at or below 250 ms, and
bounded cancellation latency. These are gates, not predictions.

## 8. Planned file-change map

| Area | Future action |
|---|---|
| `scripts/validate_powersi_reference.py` | manifests, conditioning, S/Y/Z and blind-report contract |
| `_core/io/spd.py`, `spd_adapter.py` | expose source contact/face/tie evidence without solver fitting |
| `_core/io/source_topology.py` | new deterministic compiler |
| `_core/models/topology.py` | new immutable topology/certificate schema |
| `_core/domain.py`, `scenario.py`, `scenario_io.py` | backward-compatible solver profile and cache provenance |
| `_core/solver/layer_domain.py` | new common sparse/query operator contract |
| `_core/solver/triangle_layer.py` | new actual-artwork conservative core |
| `_core/solver/materials.py` | new source-table and passive-causal realizations |
| `_core/solver/global_mna.py` | new reviewed global assembly/Schur path |
| `_core/solver/via_intrinsic.py` | new gated exact/replacement local block |
| `_core/solver/sampling.py` | new adaptive sample policy and dense verification |
| `_core/solver/evaluator.py` | backend/profile selection while preserving result API |
| `evaluation.py` | cache keys, fixed-substrate reuse, Original/Tuned atomicity |
| `gui/worker.py`, `gui/main_window.py` | process orchestration, phases, cancellation, provenance, guarded options |
| `research/oracles/*` | non-shipping CIM/FEM/SIE and benchmark tools |
| `tests/*` | unit, property, manufactured, compiler, integration, UI, and regression gates |

Any local untracked research prototype is evidence, not production code. It
must be reviewed against these contracts, rewritten or promoted intentionally,
and staged separately; its mere presence is not implementation progress.

## 9. Rollout and rollback

Planned backend IDs:

- `legacy_modal_v017`: default and regression reference;
- `research_uniform_admittance`: hidden diagnostic only;
- `research_layer_network`: guarded experimental profile;
- `validated_layer_network_v1`: created only after promotion gates.

A new backend first appears in CLI/research reports, then in a hidden developer
profile, then as a visible experimental option, and finally as a candidate
default. Each step has an explicit rollback that deletes only derived cache
objects and restores `legacy_modal_v017`; source and scenario data are never
rewritten.

## 10. Completion checklist for the future implementation task

- [ ] No runtime code is changed before the user authorizes implementation.
- [ ] Comparison and source evidence contracts are frozen first.
- [ ] Compiler failures are resolved or reported before kernel tuning.
- [ ] Every physical contribution has exactly one owner.
- [ ] Actual-artwork C improvement is not extrapolated to loaded `Zii` without
      measurement.
- [ ] Causal-material candidates use SPD data only and expose uncertainty.
- [ ] CIM, homogenization, AFS, matrix-free, and MOR remain optional until their
      individual gates pass.
- [ ] All resource and speed claims come from the named SPD and exact profile.
- [ ] UI heartbeat, cancellation, cache, and stale-result tests run with the
      numerical suite.
- [ ] A frozen candidate is preregistered before unseen validation.
- [ ] Version, installer, and GitHub release work starts only after a product
      backend is actually promoted.

## 11. Present nonclaim

This plan does not assert that the proposed solver already meets sub-milliohm
accuracy, 120 s runtime, 4 GiB memory, or sign-off requirements. It records the
smallest auditable path by which those claims could later be earned or
falsified.
