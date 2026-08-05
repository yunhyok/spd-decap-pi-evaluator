# De-cap Distribution v0.19.0 validation

## Scope and physical contract

This release changes De-cap Distribution only. Evaluation Analysis is unchanged.

The source scenario stores one PWR/GND pair per evaluation rail, but the SPD may
contain the same PWR NET on other valid internal plane pairs. Distribution now
checks every such retained pair against the final ordered PowerSI copper artwork.
A donor landing is eligible only when its exact XY is strictly inside the target
PWR artwork; voids and boundary/tolerance contacts fail closed.

This is a placement-planning contract: it assumes that the PWR via can be
re-terminated or rerouted to the selected internal PWR plane at the same landing
XY. It is not evidence that the unchanged source via barrel already spans to that
layer. A Via-only audit of this board found no retained source chain to either the
previously accepted L42-L46 moves or the newly considered L30-L40 planes. The UI
therefore discloses the assumption instead of presenting it as as-built proof.

## Named production replay

Inputs:

- `D:\S4LB002-2Para_260804_1_injected.spd`
- `D:\S4LB002-2Para_260804_1_injected_decap_distribution_01.xlsx`

The XLSX source SHA-256 matched the SPD, all 276 target cells matched, all 10,757
Present entries reconciled, and there were no workbook warnings or count drift.

| Result | Previous v0.18.3 workbook | v0.19.0 final verified replay |
| --- | ---: | ---: |
| Requested receivers | 696 | 696 |
| Fulfilled receivers | 168 | 696 |
| Shortfall | 528 | 0 |
| NET moves | 168 | 696 |
| Isolation gaps | 10 | 208 |
| Status | PARTIAL | FULL |

The previous 528-count shortfall comprised 88 receiver cells whose evaluation
pair was TOP/L02 even though exact destination PWR artwork existed on L30-L40.
The old Distribution filter never offered those lower planes to the optimizer.

The direct raw-SPD run took 231.30 s for raw import. A final-code replay used the
reconstructed original state only after its fingerprint exactly matched the raw
import (`b07f95699a142843c45b3392978cf7da6876cec67c63751861e2152ad21b1dd7`);
it took 55.81 s for retained-plane projection, 162.55 s for planning, and 241.57 s
total excluding raw import. The output fingerprint produced by the final plan and
atomic apply preview both matched
`e6f4eab45ac16f8ba8fbaea03bf9e703710471165606850734ab66b401acd8f1`.

The plan preserved full receiver fulfillment while reporting that secondary gap
and distance optima were not proven within their time limits. Fixed-move
refinement and final atomic pruning left 208 validated isolation gaps.

## Workflow changes

- Double-click the main Distribution matrix to open one retained non-modal table.
- Export a target XLSX before or after preview, edit only Target/Tolerance values,
  then import it. Schema, rail/model IDs, formulas, source SHA, fingerprint,
  duplicates, and numeric ranges are validated before any live target changes.
- The detached window can switch board rendering between immutable original
  assignments and current distributed assignments. This is display-only and does
  not mutate scenario state, revision, dirty status, targets, or plan.
