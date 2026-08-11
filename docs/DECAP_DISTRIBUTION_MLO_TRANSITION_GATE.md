# Distribution MLO transition gate

The import path records `MLO_TRANSITION_RECIPE_GATE_V1` in normalized-project
metadata. A source-qualified COPPER microvia (drill <= 150 um, two adjacent
conductors with one dielectric, dielectric/drill <= 1) or recovered lateral /
staggered path sets `transition_required=true`. This release does not synthesize
translated manufacturing recipes, so `translated_recipe_validated=false`. The
policy is a source-bound board-level positive summary: `transition_required=false`
means that no retained MLO evidence was observed, not that every source landing
was proven to be a continuous vertical via.

Distribution raises `MLO_TRANSITION_RECIPE_REQUIRED` before non-TOP plane
eligibility whenever that evidence is present. Signal-trace protection being
OFF does not bypass this structural gate. Conventional continuous through-via
evidence retains the existing immutable-column behavior. Every fresh or legacy
bundle is inspected from retained per-landing path metadata. An explicit
source-proven continuous vertical path remains eligible, but a landing with no
path evidence is unknown rather than conventional even when a valid fresh
board-level policy says `transition_required=false`. It is blocked for non-TOP
destinations with `REIMPORT_SOURCE_FOR_TRANSITION_EVIDENCE`. Reimporting the raw
SPD is required to recover transition evidence; no target-layer pre-existing
path is required once a conventional continuous path is proven.

The importer also persists `MLO_LANDING_CERTIFICATE_V1` as
`spd_mlo_landing_certificates`. It is source-SHA-bound and complete over the
unique Via IDs. Only a row classified `CONVENTIONAL_THROUGH_VIA` (positive
drill with explicit TOP and BOTTOM padstack copper) can admit a pathless
landing. A positive-drill `SHORT_SPAN_VIA` row (for example a DR-0102-like
TOP-to-intermediate-conductor span) is retained as a source-bound diagnostic
and returns `MLO_TRANSITION_RECIPE_REQUIRED`; it never grants non-TOP
eligibility. Incomplete/digest-mismatched metadata and all unknown classes
remain blocked; `UNRESOLVED` rows continue to require source reimport evidence.
Recovery requests every retained
same-NET PWR plane layer, so a logical rail layer cannot hide source paths
that terminate on another retained plane.

Policy metadata is parsed strictly: flags must be JSON booleans, the policy
version must be recognized, and a supplied source SHA-256 must match the active
scenario. V1 defines no translated-recipe asset/compiler, so
`translated_recipe_validated=true` is rejected rather than treated as
permission. The structural rejection is recorded before optional NumPy/Shapely
geometry imports; its summary count is the exact number of unique blocked
source landings, while detailed landing/rail/layer evidence remains bounded.

The public planner does not treat a missing projection as permission. When a
receiver or count-neutral exchange is requested, direct
`compute_distribution_plan(..., power_projection=None)` raises
`POWER_PROJECTION_REQUIRED` if it finds an observed MLO transition or a real
`spd_import` landing that needs source reimport evidence. Call
`build_distribution_power_projection(...)` first and pass its result so the
unsafe landing can be omitted per candidate while conventional landings remain
available for a partial plan. Only explicit conventional source paths retain
compatible direct-planner behavior; a board-level negative policy alone never
grants permission to a pathless landing.
