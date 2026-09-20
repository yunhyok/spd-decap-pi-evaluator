"""Fill the two missing coordinate/snap controls without rerunning frozen cells."""

from hashlib import sha256
import json
from time import monotonic

from shapely.affinity import scale
from shapely.geometry import Point, box

from probe_astra_dyadic_trace_sheet import ROOT, inward_dyadic, solve, TriFemSheetError


def main():
    output = ROOT / "docs/evaluation-research/astra_trace_coordinate_ablation_2026-09-07.json"
    if output.exists():
        raise FileExistsError(output)
    paths = [ROOT / "docs/evaluation-research" / name for name in (
        "astra_isolated_trace_patch_2026-09-06.json", "astra_dyadic_trace_sheet_2026-09-07.json")]
    expected = ["3bb5027424488b17cb2c6f85c75f5edeb0ff9b041929c5b6c3087417b9c4554d", "e997be26af4c75c45c2e473e9264237a748fb49516a1db1253056d340d4b84ca"]
    inputs = []
    for path, digest in zip(paths, expected):
        data = path.read_bytes()
        assert sha256(data).hexdigest() == digest
        inputs.append(json.loads(data))
    layer = inputs[0]["source_trace"]["stackup_layer"]
    sigma, thickness = layer["conductivity_s_per_m"], layer["thickness_um"] * 1e-6
    centers = [Point(0, 0), Point(60, 0)]
    artwork = box(0, -25, 60, 25).union(centers[0].buffer(30, quad_segs=16)).union(centers[1].buffer(30, quad_segs=16))
    contacts = [p.buffer(20, quad_segs=16) for p in centers]
    quantum = inputs[1]["dyadic_quantum_um"]
    snapped = inward_dyadic(artwork, quantum)
    snapped_contacts = [inward_dyadic(p, quantum) for p in contacts]
    assert sha256(snapped.wkb).hexdigest() == inputs[1]["geometries"][0]["artwork_wkb_sha256"]
    started, rows = monotonic(), []
    cases = [("original_float_um", artwork, contacts),
        ("inward_dyadic_rescaled_to_m", scale(snapped, xfact=1e-6, yfact=1e-6, origin=(0, 0)),
         [scale(p, xfact=1e-6, yfact=1e-6, origin=(0, 0)) for p in snapped_contacts])]
    for name, domain, electrodes in cases:
        try:
            row = {"status": "COMPILED_LOCAL_DC_ONLY", **solve(domain, electrodes, 1, sigma, thickness, f"research:coordinate-ablation:{name}")}
        except TriFemSheetError as exc:
            row = {"status": "STOP", "code": exc.code, "detail": str(exc)}
        rows.append({"case": name, **row})
        assert monotonic() - started < 30
    result = {"program": inputs[0]["program"], "version": inputs[0]["version"], "status": "COORDINATE_ABLATION_DIAGNOSTIC_ONLY",
        "input_sha256": expected, "new_cells": rows,
        "reused_original_m_level1": inputs[0]["sheet_rows"][1],
        "reused_dyadic_um_level1": inputs[1]["sheet_rows"][1],
        "elapsed_s": monotonic() - started,
        "scope": "Two additional one-level controls separate normalization from inward dyadic approximation for this source patch. Reuses frozen original-m STOP and dyadic-um PASS without rerunning them. No product edit or guard bypass; neither a general predicate diagnosis nor PowerSI validation."}
    output.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
