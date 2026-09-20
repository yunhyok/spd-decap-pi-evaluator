"""Research-process comparison of two set-containment predicates; no source edit."""

import json
from pathlib import Path
from time import monotonic

from shapely.geometry import Polygon, box
from shapely.geometry.base import BaseGeometry

import probe_astra_isolated_trace_patch as patch


def main():
    output = patch.ROOT / "docs/evaluation-research/astra_mesh_overlay_predicate_2026-09-06.json"
    if output.exists():
        raise FileExistsError(output)
    original_covers = BaseGeometry.covers
    fallbacks = []

    def overlay_covers(domain, member):
        if original_covers(domain, member):
            return True
        outside = member.difference(domain)
        if outside.is_empty:
            fallbacks.append({"domain_area": float(domain.area), "member_area": float(member.area),
                              "outside_area": float(outside.area), "outside_type": outside.geom_type})
            return True
        return False

    # A 1 pm source-resolvable hole or escape must remain non-contained.
    domain = box(0, 0, 100e-6, 100e-6)
    tiny_hole = box(50e-6, 50e-6, 50e-6 + 1e-12, 50e-6 + 1e-12)
    negatives = {
        "one_pm_hole": (domain.difference(tiny_hole), domain),
        "one_pm_outer_escape": (domain, box(0, 0, 100e-6 + 1e-12, 100e-6)),
        "macroscopic_void": (domain.difference(box(40e-6, 40e-6, 60e-6, 60e-6)), domain),
        "disconnected_bridge": (box(0, 0, 30e-6, 100e-6).union(box(70e-6, 0, 100e-6, 100e-6)), domain),
    }
    for name, (container, member) in negatives.items():
        assert not original_covers(container, member), name
        assert not overlay_covers(container, member), name
    assert overlay_covers(domain, Polygon([(0, 0), (100e-6, 0), (0, 100e-6)]))
    started = monotonic()
    patch.OUTPUT = output
    # This is a labelled experiment confined to this Python process. The checked-in
    # FEM implementation, its guards, original STOP artifact and all inputs stay frozen.
    try:
        BaseGeometry.covers = overlay_covers
        patch.main()
    finally:
        BaseGeometry.covers = original_covers
    result = json.loads(output.read_text(encoding="utf-8"))
    result["base_probe_status"] = result["status"]
    result["status"] = "EXPERIMENTAL_OVERLAY_PREDICATE_ONLY"
    result["predicate_experiment"] = {
        "definition": "A covers B OR B.difference(A).is_empty; exact set-relation comparison, no geometric buffer or area tolerance",
        "negative_controls_rejected": list(negatives), "fallback_count": len(fallbacks), "fallbacks": fallbacks,
        "elapsed_s": monotonic() - started, "production_code_changed": False,
        "limitations": "This probes GEOS predicate/overlay disagreement on one source patch. It is not a general robust-predicate proof, convergence attestation, production guard change or PowerSI validation.",
    }
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": result["status"], "base_probe_status": result["base_probe_status"],
                      "sheet_rows": result["sheet_rows"], "last_refinement_relative_change": result["last_refinement_relative_change"],
                      "local_boundary_solve": result["local_boundary_solve"], "fallback_count": len(fallbacks),
                      "negative_controls_rejected": list(negatives)}))


if __name__ == "__main__":
    main()
