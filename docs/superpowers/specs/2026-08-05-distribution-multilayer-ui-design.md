# Distribution multilayer reachability and workflow design

## Scope

Improve De-cap Distribution only. Evaluation electrical modeling remains unchanged.

## Physical eligibility

The saved scenario's single evaluation PWR/GND pair is not a complete description
of where Distribution may re-terminate or reroute a decap PWR via. Distribution
therefore derives destination eligibility from three source-backed facts:

1. the existing decap or shared-pad anchor owns an exact source PWR via landing;
2. the same landing XY lies strictly inside the final ordered copper artwork for
   the destination rail on a valid retained PWR layer;
3. the destination has a valid retained PWR/GND plane-pair definition and PWR bump.

All candidate layers must be conductor layers that list the destination NET and
are not configured/ground-like reference layers. Missing source bytes, identity
mismatch, malformed artwork, or boundary-only contact fails closed. This is a
placement-planning assumption that permits same-XY via re-termination/rerouting;
it does not prove that the unchanged source via barrel already reaches the lower
layer. The resulting proof is Distribution-specific and does not change the rail
pair used by Evaluation.

## Distribution workflow UI

- Double-clicking the Distribution table opens one retained non-modal table window.
- The detached window exports the current target matrix before or after preview.
- Import uses the existing strict workbook loader; schema, rail/model identity,
  formulas, source SHA, and design fingerprint are validated atomically before the
  live targets change. A successful import invalidates any stale preview.
- A display-only Original/Distributed control changes board rendering between
  immutable source assignments and current assignments. It does not mutate the
  scenario, revision, dirty flag, targets, or plan.

## Release acceptance

The named SPD and workbook must be replayed. Diagnostics and UI disclosure must
distinguish true physical shortage from the previous selected-layer omission and
state the via re-termination assumption. Automated tests,
offscreen UI checks, packaged EXE smoke, installer build, and remote GitHub asset
verification are required before completion.
