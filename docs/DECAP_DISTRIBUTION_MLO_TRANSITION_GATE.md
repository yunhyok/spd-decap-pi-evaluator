# Historical v0.22.2 Distribution MLO evidence and vertical-XY policy

> This document is historical. It is superseded by
> [`DECAP_DISTRIBUTION_TRANSITION_POLICY_2026-08-11.md`](DECAP_DISTRIBUTION_TRANSITION_POLICY_2026-08-11.md)
> and must not be used as the current Distribution contract.

## v0.22.2 Distribution behavior

De-cap Distribution uses the fixed policy
`VERTICAL_XY_ASSUME_DESCENT_V1`. Every source-classified physical PWR landing
is projected straight down at its immutable XY to every retained destination
PWR plane. Existing MLO/microvia short spans, lateral or staggered transitions,
missing per-landing paths, and translated-recipe status are retained as source
provenance but do **not** block Distribution eligibility.

This policy does not bypass destination artwork proof. The final ordered copper
must strictly contain the projected XY; voids, boundary contact, and missing or
malformed artwork remain ineligible. Optional immutable signal-Trace
protection, shared-pad topology, isolation gaps, numeric supply, and the chosen
gap-distance objective also remain active.

The result is a placement-planning assumption. It is not fabricated-path, DRC,
SI, or manufacturing sign-off. The GUI Preview log displays this boundary, and
target/result workbooks record `Via Projection Policy`.

## Retained import provenance

Raw SPD import continues to record source-bound MLO evidence:

- `MLO_TRANSITION_RECIPE_GATE_V1` summarizes observed qualified microvia,
  lateral, and staggered transition evidence.
- `MLO_LANDING_CERTIFICATE_V1` records strict, digest-bound landing
  classifications such as `CONVENTIONAL_THROUGH_VIA`, `SHORT_SPAN_VIA`, and
  `UNRESOLVED`.
- Per-landing recovered and structural path evidence remains persisted in the
  scenario when available.

v0.22.1 used this evidence to emit `MLO_TRANSITION_RECIPE_REQUIRED`,
`REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE`, or `POWER_PROJECTION_REQUIRED` for
non-TOP Distribution candidates. That gate is intentionally retired for
Distribution in v0.22.2. The evidence remains available to other analysis
paths and future manufacturing-aware recipe work; it is neither discarded nor
rewritten as a fabricated continuous path.

The normal GUI path still calls `build_distribution_power_projection(...)`.
That exact projection proves the destination NET/layer copper at immutable XY
and preserves all non-MLO physical and topology gates.
