# De-cap Distribution v0.20.0 validation

> Historical validation only. Its immutable TOP-XY vertical rebuild assumption
> was superseded on 2026-08-11 by the source-proven exact-layer transition policy
> in `docs/DECAP_DISTRIBUTION_TRANSITION_POLICY_2026-08-11.md`.

## Scope

This validation covers De-cap Distribution only. Evaluation Analysis remains
unchanged and is not used to grant Distribution eligibility.

The v0.20.0 physical contract is:

- start from a source-classified physical PWR Via landing, not the decap center
  or a lateral Trace/path endpoint;
- hold that landing XY immutable and project it vertically to every retained
  conductor layer containing the target PWR NET;
- require strict containment in the final ordered copper artwork, with voids,
  boundaries, missing artwork, and malformed artwork failing closed;
- treat the result as a disclosed filled-Cu microvia-stack retarget/rebuild
  planning permission (`VIA STACK CHANGE REQUIRED`), while leaving all PWR-plane
  artwork unchanged;
- accept any real PWR root serving the final active shared-pad component, but
  never let a dummy cap create a root of its own;
- preserve all shared-Via, source-proven isolation-gap, same-component rail, and
  dummy/anchor rules; and
- do not use GND-side Via or layer evidence as a destination gate.

## Named raw-SPD and workbook replay

Inputs:

- `D:\S4LB002-2Para_260804_1_injected.spd` (1,120,159,188 bytes)
- `D:\S4LB002-2Para_260804_1_injected_decap_distribution_01.xlsx`
  (307,850 bytes)

The replay imported the raw SPD through the production importer, loaded the
existing target workbook, built the production Distribution projection and
plan, applied it atomically, independently reconstructed every target plane,
then saved/reopened the scenario and exported/reimported the workbook.

| Check | Result |
| --- | ---: |
| Plan status | `FULL` |
| Requested receiver count | 696 |
| Fulfilled receiver count | 696 |
| Shortfall | 0 |
| NET assignment moves | 696 |
| Isolation gaps | 212 |
| Shared-pad clusters revalidated | 996 |
| Moves with independent physical proof | 696 / 696 |
| Independent proof entries | 2,602 |
| Proofs per move | 1 to 8 |
| Moves without proof | 0 |

The workbook source SHA-256 matched the raw SPD. Its stored design fingerprint
was older, so the loader emitted the expected fingerprint-drift warning and
revalidated every Target/Tolerance value against the current raw-SPD scenario.
The exported workbook produced the same expected warning after reopen; there
were no unsupported warnings or target/tolerance round-trip changes.

## Timing

Measured wall times on the validation workstation:

| Phase | Seconds |
| --- | ---: |
| Raw SPD import and source verification | 264.636 |
| Workbook load and validation | 3.268 |
| Exact PWR-plane projection | 70.073 |
| Distribution planning | 218.209 |
| Atomic apply and topology validation | 18.189 |
| Independent move/copper proof | 190.800 |
| Save, reopen, export, and reimport | 20.711 |
| Total | 785.885 |

The independent proof deliberately does not call the planner's eligibility
helpers. It decodes every retained geometry asset, rebuilds PowerSI primitive
order, rejects negative-first/malformed artwork, and checks final-component PWR
roots against target copper. Multiple disjoint assets for the same NET/layer are
preserved rather than overwritten.

## Immutability evidence

The following SHA-256 snapshots were identical before and after apply and again
after save/reopen:

| Immutable evidence | SHA-256 |
| --- | --- |
| Normalized project | `9ea9cf57f9ffa0bd30d6444c1bc40c6d37a6a56dffff03a703c32b06a1df4e23` |
| Stackup | `bad669349580f77170b54fa17ed8c66a2c1adfeea0ada40410212edacbbaf5a6` |
| Plane metadata | `8f2b24c77d103c9cddf3aa3aaca6a6e215fdf975623dd75d1bc3a4ca0f0b88a0` |
| Attachment payloads | `93433643101fb38f6043c7c5575254e25ed84e1cf8becf41dd2a522f2130e1e5` |
| Physical decap coordinates/source identity | `b1beb5895852f15ea705e27a122859174a61a942f6aa3a6281c6ce59845826ce` |

Therefore the replay changed only allowed assignment/isolation-gap state; it did
not edit plane artwork, stackup, retained attachments, Via landing coordinates,
or source component identity.

## Regression verification

- Full automated suite: `734 passed`
- Python bytecode compilation: passed
- Git whitespace validation: passed
- Replay report: `validation-output/v0.20.0-final-real-replay-20260806-r3/real-replay-report.json`
- Reopened scenario: `validation-output/v0.20.0-final-real-replay-20260806-r3/distribution-replay.spdpi`
- Round-trip workbook: `validation-output/v0.20.0-final-real-replay-20260806-r3/distribution-replay.xlsx`
