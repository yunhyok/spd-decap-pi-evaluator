# Distribution existing-plane contract and status UX design

> Superseded for non-TOP destination eligibility on 2026-08-11. The immutable
> TOP-XY vertical projection below is unsafe for laterally staggered MLO
> microvias. See `docs/DECAP_DISTRIBUTION_TRANSITION_POLICY_2026-08-11.md` for
> the source-proven exact-layer transition contract now enforced by the app.

## Scope

Improve De-cap Distribution only. Evaluation Analysis physics and solver selection
remain unchanged. Distribution finds existing decap sites that can be reassigned
to another PWR channel without editing any PWR-plane artwork.

## Approved physical contract

A destination rail is eligible at an existing decap site when all of the
following are true:

1. the source connectivity analysis classifies at least one immutable physical
   TOP-side PWR Via landing for the direct/shared active PWR component;
2. that immutable PWR-via landing XY lies strictly inside the final ordered copper
   artwork of that destination PWR layer;
3. shared-pad connectivity, separator, anchor, and isolation-gap rules remain
   valid after reassignment.

The destination search is independent of Evaluation's selected PWR/GND pair.
Distribution enumerates every retained conductor layer that lists the destination
PWR net. GND via/layer evidence is not a destination eligibility gate: the
reassignment changes only the PWR-side channel. Evaluation Analysis retains its
own PWR/GND-pair contract and is not broadened by this Distribution rule.

The query always uses the original PWR-via landing XY. A recovered bent-via or
trace endpoint on a lower layer must not shift the query point. Copper boundary
contact, void contact, missing artwork, malformed artwork, and ambiguous via
identity fail closed. If more than one destination layer covers the landing, the
topmost retained layer in stack order is selected deterministically as the
Distribution proof layer. Generic `RailEligibility.pwr_layer/gnd_layer` continues
to describe Evaluation's selected pair; the actual Distribution layer is retained
only in Distribution-specific proof metadata so Evaluation cannot mistake one for
the other.

A destination PWR bump is used when available for NEAREST/FARTHEST ranking, but
its absence does not negate exact existing-plane eligibility. A missing-bump rail
uses deterministic canonical ordering and emits a narrative diagnostic.

The physical landing is projected vertically to every retained PWR layer of the
destination net. This is a disclosed filled-Cu microvia-stack
retarget/rebuild planning assumption: the existing via-column need not already
span the target layer, and no trace/path endpoint can move the projection XY.
Preview/apply never edits PWR-plane artwork, stack-up, retained geometry assets,
or attachments; it changes only the permitted channel assignment and required
existing isolation gaps.

Direct and shared-pad candidates use at least one physically eligible PWR-via
root for the final active PWR component. Dummy caps never manufacture an
independent root; all existing dummy, shared-PWR-via, isolation-gap, and
same-component rail-identity rules remain mandatory.

## Status and log UX

The line directly below the target table is a compact, single-line balance strip.
It contains only per-model `Donor`, `Receiver`, and `Balance` values. It contains
no validity prose, warnings, filenames, fixed-capacity explanation, proof state,
exchange detail, or newline. Color indicates ready, blocked, or incomplete state;
the detailed text remains accessible through a tooltip.

The lower text box is renamed `Distribution Status / Preview Log` and owns every
narrative message:

- invalid target or tolerance explanation;
- numeric shortage and fixed/unassignable counts;
- exact-copper proof pending state;
- exchange allowance details;
- workbook import audit and drift warnings;
- candidate-order requirement;
- preview, partial-result, diagnostics, stale-result, and apply details.

The global status bar remains a short transient operation indicator.

## UI lifecycle and concurrency

The detached target window remains a read-only mirror. Its Import, Export, and
Original/Distributed controls are disabled whenever a worker is active, then
restored from the current document state. Closing the main window explicitly
closes the detached window. Import continues to validate atomically and invalidates
stale previews. Original/Distributed display remains non-mutating.

## Acceptance criteria

- Any retained destination PWR plane is usable when exact ordered copper strictly
  contains an immutable physical PWR-via landing, even when it is not part of an
  adjacent Evaluation PWR/GND pair or the original via stack did not span it.
- Trace/path evidence cannot create a landing or move the exact immutable
  PWR-via landing XY.
- Applied plans leave all plane/stack-up/attachment and via-route data
  byte-identical; only allowed assignment and isolation-gap state can change.
- The balance strip is one line and contains only Donor/Receiver/Balance data.
- All explanations appear in the lower log.
- Detached controls cannot mutate state while a worker is active and close with
  the main window.
- Focused tests, the complete suite, the named SPD/XLSX replay, packaged EXE UI
  inspection, installer checksum, and GitHub release assets are verified.
