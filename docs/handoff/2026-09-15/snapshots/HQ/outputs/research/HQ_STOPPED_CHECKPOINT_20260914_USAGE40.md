# SPD Decap PI Evaluator v0.23.1 — STOPPED at user usage40% threshold

Saved: 2026-09-14T15:30:58.736235+09:00

## Controlling instruction

Official force-refresh LIVE remaining40.0% was observed by Astra task01a09d3c-a47e-7213-a338-1fa220a14d4b and independently confirmed by mobile task01a07696-742c-7020-bf48-8b9ae43ce684. STOP is latched in HQ_USAGE_STOP_POLICY_20260914.json. No new research, calculation, failed-run retry, refactor, delegation or Reset. Usage recovery does not permit restart; explicit user resume is required. At observation, computation had already ended; only existing completion reviews/checkpoint saving were allowed to finish.

## Exact location and preserved changes

- Workspace: C:/Users/User/.codex/worktrees/5950/SPD Decap PI Evaluator
- Branch: codex/astra-evaluation-resume-20260906
- HEAD: e2f219e71d8c8a397009f72242cce10d78cfc7ab
- Version:0.23.1. No product version change, commit, push or release performed.
- Entire Git status snapshot: outputs/research/hq-stopped-working-tree-20260914-usage40.txt (788 status entries, preserved without staging/reset/cleanup). Existing changes include the research plan/documents and many research scripts; do not attribute all of them to the latest fragment.
- Research outputs are generally ignored by Git and remain on disk. Exact final receipt hashes: HQ_STOPPED_ARTIFACTS_20260914_USAGE40.json.
- Never read/search/run accuracy_parse.py. Preserve all failure artifacts; do not automatically rerun earlier full L25/L02/native experiments.

## What was established

The accepted actual-board baseline remains 1MHz, port18, ADC_VDD_075_VTRIP_SRAM/0:
Z=0.0005545735011944134-j0.0005883820472006132 ohm.
PowerSI reference=0.0006915479466520245-j0.0004257504501139287 ohm.
Accepted complex relative error=26.18268397573785%. No new accepted accuracy improvement or broadband1kHz–100MHz claim.

1. Finite L14/L25 current redistribution run: astra-finite-joint-p-20260914-01.22 joint actions,2214.156s, info1, scaled residual1.7996732, KCL49.9774mA, power mismatch424.080uOhm. Rejected. Fields and final magnetic actions are saved. The worker used LEFT GMRES although the accepted solver used a RIGHT residual correction; this mismatch was missed before launch. It is not proven to be the sole nonconvergence cause.
2. Saved affine diagnostic: initial finite residual222.7182 ->1.7996732; optimal two-field recombination improves only0.00345%. Do not repeat it. Remaining error is predominantly nodal KCL.
3. Right-correction probe: astra-finite-right-probe-20260914-01. Seven joint actions completed; eighth fresh final action hit the900s external limit (900.843s). Saved v/q/eta exists, final force/flux/metrics/info do not. Static readback KCL49.9774mA ->18.1286mA, still fails1e-7A. No additional magnetic validation run is justified just to validate a field already failing KCL.
4. Current static M leakage: astra-static-nodal-leakage-20260914-01. One existing factor setup,2 M applications,0 FMM,38.578s. Magnetic-only RHS with zero node rows produced4.9339749A node leakage. Pure-node repair18.1286mA ->18.1634mA did not improve. Original sign/algebra allclose failed at6.0523e-12A; measurements saved before assertion and FAIL preserved. Independent review confirms that this small discrepancy does not explain the much larger leakage; no full PASS was asserted.
5. Tighter static inverse: astra-static-tighter-inverse-20260914-01. Same2 RHS, right GMRESrestart8/maxiter3,28 M and28 static actions each,0 FMM,123.171s. Both info3. Sample1 node leakage4.934A ->0.369224A, scaled residual0.127834,43.535s. Sample2 repair18.1286mA ->4.30261mA, scaled residual0.381860,41.827s. Both still far from1e-7A, and much costlier than one1s M apply. Sign/algebra mismatch4.9621e-12A remains FAIL. Both sample JSONs saved before assertion; combined success report is absent. Solved vectors were NOT saved, so do not claim they are available for reuse.

## Key reusable artifacts and code

- Accepted baseline field: astra-l02-hybrid-right-correction-01/field.npz; SHA960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b.
- Original conditional operator: astra-l02-conditional-hybrid-operator-02/conditional-hybrid-operator.npz; SHA5ab5ba38aaf8c317aa05ae1d4f790c1d6447406ec25afe3c30e96536c714aa92.
- Failed left final magnetic checkpoint: astra-finite-joint-p-20260914-01/finite/raw-finite-magnetic-field-before-gates.npz; SHA38207bc8c0dbe9d9886dcb61d64ed83b00b7dcc46a76046f88ebc2a93b61e78c.
- Right timeout raw field: astra-finite-right-probe-20260914-01/finite/raw-finite-field-before-gates.npz; SHA2569d379d05dd99d1a86506fbc6bfb03f768dbb18025419cf5cfcac78e7db520.
- Tighter sample JSONs: static/sample-01-baseline_alpha1_q_eta_with_zero_nodal_rows.json and static/sample-02-latest_right_field_pure_nodal_residual.json under the tighter run. SHA8792795622df8e2896a6fb6fa0a61c462d96787668bd4a2fc92ea650800bcd51 and fcec46ecddaba6924081b92b4715c0bb0f347b3bd2ae0171b489365d62ad710c.
- Latest child driver: child06f1/outputs/child-port-fixture/static-tighter-inverse-01/static_tighter_inverse_driver.py; SHA7adf754ac44ff3bbb4687b8f6fc449f4da03baa62bb0ee8efbe12b5a9cdc5687.
- HQ actor: tools/research/run_astra_static_tighter_actor.py; SHA770853958598004883dc49ca91f26f701668889576644030b08907608f2cc05e.
- Other exact driver/runtime/source pins and earlier completed physical experiments remain in HQ_ACTIVE_CHECKPOINT_20260914.md and each frozen run directory. Do not rerun completed qualifications merely to rediscover them.

## Stopped process and task state

HQ owned workers36804,39536,18080,43968 all ended; sessions74412,22752,88816,70119 closed. A direct Get-Process check found zero of these workers at final saving. CIM process lookup was unavailable; no process claim relies on it. Both HQ native child agents are completed and received STOP without new turns. Sol task01a09d3c-a425-7c70-a0e5-c81df219a284 confirmed IDLE,0 running native children, no owned worker. Physics task01a09d3c-a47e-7213-a338-1fa220a14d4b is finishing only its already-running completion checkpoint; final IDLE confirmation will be appended below. Mobile owns pausing pi-6 and hq-9-7-08 and has received STOP.

## First step after explicit user resume

Read this stopped checkpoint and the usage policy first, then verify the real checkout and available saved artifacts. Do NOT simply add more magnetic cycles or repeat the old static test. Current evidence points to the static inverse/coarse correction as a numerical weakness. A future bounded direction is to localize the remaining nodal defect using existing right raw fields and study a better static preconditioner/coarse correction. This is a saved recommendation only; no new localization or solver work was started after STOP. Adaptive inner solves require an appropriate flexible outer method if eventually used inside a magnetic solve. Keep actual port accuracy, physical gates and runtime cost separate from diagnostic improvements.

## Final all-stop confirmation

Both implementation task and physics task are confirmed IDLE by official task snapshots. Physics completion checkpoint: C:/Users/User/.codex/worktrees/353f/SPD Decap PI Evaluator/outputs/child-port-physics/checkpoint-20260914-1455KST.md; SHA256 22cdfa028de6ce59ab91ba5d930f84a107289e559082e9d5324857b1492e98bf. Both HQ native agents are completed, confirmed again by live agent listing. All four owned calculation processes and their sessions have ended. Research, computation and delegation are stopped. Only this administrative final saving/notification remains. Explicit user resume is required. Mobile coordinator confirmed pi-6 PAUSED and will pause hq-9-7-08 after this final notification.
