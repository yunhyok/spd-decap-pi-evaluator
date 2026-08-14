# SPD Decap PI Evaluator v0.22.0 — AV-BS1 H2-P1 preregistration

Status: `candidate_H2_P1_token_gated_no_solve_pending_static_review_and_clean_commit`.

This document freezes the next single-mesh boundary-Schur research stage. It does not authorize or report an H2 factorization, physics response, H4 refinement, withheld-radius run, EQ0 run, product-source change, or PowerSI-equivalence claim. A separate tracked one-use review token may authorize exactly one `primary-h2` attempt only after this contract, fixture, runner, and tests are independently reviewed and committed in a clean checkout.

The current candidate bytes have passed a no-solve static screen only. The P1 suite reports `31 passed` and the combined P0+P1 bounded suites report `37 passed`; Python compilation, PowerShell AST parsing, the manifest wrapper, and `git diff --check` pass. The manifest status is `preregistered_H2_P1_token_missing_no_solve`, `authorization_state=not_authorized`, `available_solve_stages=[]`, `factorization_performed=false`, and `physics_solve_performed=false`. Exact candidate evidence is:

| candidate object | SHA-256 |
|---|---|
| P1 fixture | `0600604cf7ab7b2a1d2b67c240ed1c659001a989482ce4f94c4b97c305ca3ca4` |
| P1 process-tree runner | `f54d2645bb01dcd287a7836af8a99990055803c793f45ffd9f91e06426624dc6` |
| P1 static test file | `01f0203009087a97f00b58461fb6fda339a2866700399be8d5453bdff47c3199` |
| P1 manifest payload | `05a21364deeb123432a0f52af9a6dbb818f9c9aa2814c34665dfc8da6b8463c4` |

These hashes are candidate evidence in the working tree, not execution authority. They must remain exact through independent review and a clean preregistration commit. No P1 review-token file exists in the checkout.

## Immutable predecessors

- H2-P0 preregistration commit: `4c1e3fce8aac659dd0aedb06c2b6d274fff73a12`.
- H2-P0 manifest payload: `68d2e20a471e0e9475dd8e575ffd1e246f4098bb6c0e2b688d0f6f702fb511ba`.
- H1 result artifact: `af17bbcc49cebc7e9ddb88e821ec0338a435fb2bf8ce51b117cfb3019b78b44d`.
- H1 result payload: `3cdef96c8de1585acfe4cc256d63e6815b93be9906df51d9b97ff5d4f51f330b`.
- H1 numerical payload: `a955393d69e22e87d759656b598e71fccee2492eff4c8d305db48623662f9fc4`.
- H1 resource report binding: `8387d19253279120a116cf0e8b48c67007394a86d140bfb0a1770ad4f8b87d72`.
- H1 signed-M9 numeric view: `034154b41d75e3ecc707fa2c82ee5012ecd4b72155a20fabbb9a3abe7b427f69`.
- H1 consumed-token tombstone: `80ffd8b486dbdd8087eb205f71137663cef0497d8ba8df74253743b302fe6f35`.

The fixture must validate these files and their semantic fields, including the H1 status, null fine/convergence/final fields, resource pass, operator blobs, signed-mode order, and consumed-token state. A matching hash string in a token is not sufficient by itself.

## Frozen H2 mesh and assembly

The solve must reconstruct the H2-P0 one-to-four refinement exactly: 8,065 nodes, 23,936 edges, 15,872 CCW P1 triangles, 256 boundary nodes, and 7,809 interior nodes. The H2 manifest SHA-256 is `34eb4f9cadcefd0b20cff3ae6c483dbee4e412ca11d0ff1a8c2c6f49ec7896a9`.

The canonical cyclic-diagonal set contains 3,712 topology-derived tags. The 128 projected outer children remain physical, nonzero stiffness couplings and must not be removed by magnitude search. Before factorization, the reconstructed matrices must match H2-P0 exactly:

| object | nnz | SHA-256 |
|---|---:|---|
| raw K | 55,937 | `ba0589aff655f2f4c39eac20b361446556d5c8ee65f0d0154e984574e961eb2c` |
| canonical K | 48,513 | `8fcbbc2bd18c9ba098382cb61c45bf6880cb47886ed2578ee48def0f7a34eef9` |
| volume M | 55,937 | `69aefda6d301e9c7174aae8d1d557439749d288ddd57301291c21c7da5f581fc` |
| trace MΓ | 768 | `2de9980d426f88a1422588bae3bbd57cba2e2f7eccc216e2509e7a83e62ca45b` |

The frozen sparse payload is 1,328,172 bytes. The conservative raw preflight is 2,043,070,476 bytes and its 25% value is 2,553,838,095 bytes. These are preregistration bounds, not measured peak memory.

## Numerical operator

At 100 kHz and radius 17.5 µm, assemble the full sparse operators

`Ab = K`, `Ap = K + jωσM`.

The mandatory transpose gate is applied to the full K, M, MΓ, Ab, and Ap operators before their II/IΓ blocks are used. Boundary extensions use

`Xb = -Ab,II^-1 Ab,IΓ`, `Xp = -Ap,II^-1 Ap,IΓ`.

Only one equilibrated SuperLU factor may be resident at a time. Boundary RHS batch size is at most four. Raw residuals are recomputed against each unscaled source matrix. The volume cross operators use nonconjugate transpose:

`Y = σ Hb^T M Hp`, `Yreverse = σ Hp^T M Hb`.

Hermitian conjugation is used only for passivity and power. Post-hoc symmetrization is forbidden.

Mandatory H2-local gates retain the H1 thresholds: full assembly transpose ≤1e-12, backward residual ≤1e-10, κ1u≤1e-8, reverse-order defect ≤1e-12, raw reciprocity ≤1e-8, raw passivity within its preregistered tolerance, and signed-M9 power mismatch ≤1e-8.

### Independently recomputable M9 power evidence

The numerical child must emit `AV-BS1-h2-M9-volume-power-v1`. It contains the ordered signed-M9 interior conductor fields as one `<c16` array of shape `7809×9` (1,124,496 raw bytes), the signed-mode order, and the source conductor-extension SHA-256. It does not store or duplicate the full `7809×256` extension.

The finalizer must decode and hash-check this field array, reconstruct the frozen H2 volume mass matrix, verify its nnz and SHA-256, rebuild `MII`, `MIΓ`, `MΓI`, and `MΓΓ`, and independently recompute for every mode

`Pvolume(m)=0.5 σ Re[u_m^H(MII u_m+MIΓ v_m)+v_m^H(MΓI u_m+MΓΓ v_m)]`.

The SHA link to the full conductor extension is provenance, not sufficient mathematical evidence. The finalizer must also reconstruct canonical K and `Ap`, then recompute each raw modal-field backward residual

`r_m=max|Ap,II u_m+Ap,IΓ v_m| / (||Ap,II||∞ max|u_m| + max|Ap,IΓ v_m|)`

with a finite, strictly positive denominator and `r_m≤1e-10`. It separately recomputes `Pboundary(m)=0.5 Re[v_m^H Y v_m]`, the per-mode normalized mismatch, and the maximum mismatch. A child-reported volume-power scalar or a jointly rehashed field/power pair alone is not gate evidence. A field, operator, mode row, or top-level numerical envelope that fails this reconstruction is `BLOCKED_AV_BS_SOLVE`, `BLOCKED_AV_BS_POWER`, or `BLOCKED_AV_BS_RESULT_SCHEMA` and cannot be promoted.

## H-to-H2 trend scope

The authoritative H1 view is the ordered JSON array `[[m,numeric_S], ...]` for signed modes -4 through +4. H2 emits the same view and SHA-256. With the frozen mode floor `5.214985896880959e-08 S`, each mode reports

`e(m)=|Yh2(m)-Yh(m)|/max(|Yh2(m)|,Yfloor)`.

RMS and maximum relative trend are reported across all nine modes. Phase trend is reported only when both magnitudes are at least `10*Yfloor`. This entire comparison is diagnostic only: `gate_applied=false`, `gate_pass=null`, and it has no authorization effect. H2 analytic error and m↔-m degeneracy also remain trend-only. The h2→h4 mesh gates, fine analytic gate, and final circle gate remain null and unexecuted.

## Resource and one-use boundary

The PowerShell runner must preserve the audited native Toolhelp32/PSAPI/GetPerformanceInfo process-tree monitor, include the runner process, retain the child process handle immediately, verify repeated descendant termination, and check for observed orphans after root exit. It stops at 900 seconds, 4 GiB tree working set, 5 GiB tree private/commit, 2 GiB minimum system commit headroom, or 1.5 GiB minimum available physical memory.

The 1,124,496-byte raw M9 field certificate is small relative to the frozen 2,553,838,095-byte conservative H2 preflight. It is serialized sequentially with the result and does not change the one-factor-resident or batch-of-four policies.

Before creating a claim, the runner must complete the token, clean-checkout, H1 artifact/tombstone, and H2-P0 preflight. The claim is an atomic `CreateNew` file at `validation-output/av-bs1/claims/<token-id>.json` and binds token ID/hash, P1 fixture/runner/commit, H1/P0 review binding, preflight payload, and Git HEAD. The same claim path and hash must be present in the guard, numerical child output, resource report, and final result. An existing claim blocks reuse. After any attempted child launch, the active token must be replaced by a reviewed consumed tombstone before further stage work.

The finalizer and token consumer both validate the full numerical envelope: program/case/stage, clean commit, fixture and runner hashes, review token and binding, guard, claim, preflight, H1/P0 bindings, execution booleans, status, and null future gates. A tentative pass is authoritative only when the consumed tombstone records `consumption_validated_pass=true`, `mandatory_stage_pass=true`, and an empty failure-code list. Invalid pass evidence is still atomically consumed and forces runner exit 2. Finalizer-stage validation failures preserve their original `BLOCKED_AV_BS_*` code with claimed-attempt provenance; factorization and physics completion are `null` unless bound by valid child evidence.

## Result scope and sequence

A successful artifact may use only `passed_AV_BS_h2_stage_only_pending_h4_preregistration`. It must set `mandatory_stage_pass=true`, `factorization_performed=true`, and `physics_solve_performed=true`, while `fine_analytic_pass`, `mesh_convergence_pass`, and `final_circle_pass` remain null and `next_stage_authorized=false`.

Execution order is fixed:

1. Complete static tests and independent Sol/Terra/Luna review with no factorization.
2. Commit this P1 contract, fixture, runner, and tests in a clean research-only commit.
3. Add a separate tracked one-use token in its own commit, bound to the P1 parent commit and exact bytes.
4. Run one guarded `primary-h2` attempt.
5. The runner atomically replaces the active token after every launched attempt, on pass or failure. Independently audit and commit that consumed tombstone together with the immutable result evidence.
6. Preregister H4 separately. No H4, withheld, EQ0, product, or PowerSI promotion follows automatically.
