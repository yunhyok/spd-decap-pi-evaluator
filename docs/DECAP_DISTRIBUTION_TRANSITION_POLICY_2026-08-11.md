# De-cap Distribution source-proven transition policy

## Why the policy changed

The former Distribution contract projected a TOP-side PWR Via landing vertically
to every retained destination plane. That assumption is not valid for MLO builds
whose sequential microvias must move laterally after a bounded number of layers.
A target plane can contain the TOP XY even though no manufacturable source-proven
path reaches that layer at that coordinate.

## Physical eligibility contract

For a non-TOP destination layer, a decap PWR root is eligible only when all of
the following are true:

1. source connectivity classifies the root as a physical PWR Via;
2. raw-SPD path recovery proves a unique monotonic Via/transition path to the
   exact destination layer;
3. the recovered endpoint XY on that layer lies strictly inside the final
   ordered destination-net copper; and
4. shared-pad, anchor, separator, and isolation-gap constraints remain valid.

Unique same-layer transition hops retained by the source path are part of the
proof. Ambiguous alternate exits, missing exact-layer evidence, boundary contact,
void contact, and malformed geometry fail closed. TOP destinations continue to
use the physical TOP landing itself.

Old `.spdpi` bundles remain readable, but a missing exact-layer transition proof
does not fall back to a virtual vertical column. Reopening the verified source
SPD is required to populate the expanded retained-layer path evidence.

## Optimization order

`NEAREST` and `FARTHEST` are the final tie-breaking objective, not the first
physical objective. The exact priority is:

1. maximize fulfilled receiver demand;
2. minimize isolation-gap sacrifices;
3. minimize active PWR NET relabels; and
4. apply the requested bump-distance ordering.

Consequently, a nearby shared-pad anchor can remain unchanged when selecting it
would require a separator while a full zero-gap plan exists. The UI and exported
workbook disclose this ordering and the planner diagnostics.

## C793_1 regression evidence

In `S4LB002-2Para_power_0811_3_injected.spd`, C793_1 belongs to the shared TOP
PWR strip C793_1 through C805_1. Its two PWR roots begin at X=1648 um and laterally
transition to X=1548 um at L06 before terminating on L09. The REF_CLK/1 retained
copper that contains the original TOP landing exists on deeper L11/L34 layers,
but the source graph does not prove a continuation to either layer. The previous
vertical projection therefore overclaimed destination eligibility. The updated
policy excludes that unproven path rather than inventing a vertical continuation.
