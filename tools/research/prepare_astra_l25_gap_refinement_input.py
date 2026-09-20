"""Pin saved-field gap vectors and minimal interior-edge refinement selections."""
import argparse
import json
from pathlib import Path
from time import perf_counter

import numpy as np

import localize_astra_l25_rt0_p1_gap as prior


def run(output):
    started = perf_counter()
    helper = Path(prior.__file__)
    helper_sha = "37d17daffd6abf99bd8b1aa5146a2d7bdf0ec754401067ef30e1a4b032e704c0"
    prior._pinned(helper, helper_sha)
    prior._pinned(prior.RESULT, prior.RESULT_SHA256)
    prior._pinned(prior.DRIVER, prior.FROZEN_DRIVER_SHA256)
    receipt = json.loads(prior.RESULT.read_text(encoding="utf-8"))
    if receipt["status"] != "COMPLETED_CONDITIONAL_L25_RT0_P1_PAIR":
        raise ValueError("accepted pair required")
    mesh = prior._load_npz(prior.MESH, prior.MESH_SHA256)
    topology = prior._load_npz(prior.TOPOLOGY, prior.TOPOLOGY_SHA256)
    drive = prior._load_npz(prior.DRIVE_NPZ, prior.DRIVE_NPZ_SHA256)
    rt0 = prior._load_npz(prior.RT0_FIELD, prior.RT0_FIELD_SHA256)
    p1 = prior._load_npz(prior.P1_FIELD, prior.P1_FIELD_SHA256)
    data = prior._triangle_geometry(mesh, topology, drive, rt0, p1)
    gap = data["gap"]
    observed = np.array([gap.sum(), data["rt_energy"], data["cross_energy"], data["p1_energy"]])
    expected = np.array([receipt["gap"]["gap_ohm"], receipt["rt0"]["energy_qrq_ohm"], receipt["gap"]["cross_integral_ohm"], receipt["p1"]["energy_vgv_ohm"]])
    errors = abs(observed-expected)/abs(expected)
    if not np.all(np.isfinite(gap)) or np.any(gap < 0) or not np.all(errors < 1e-10):
        raise ValueError(f"saved-field energy gate failed: {errors}")
    points = mesh["node_xy_um"][data["triangles"][data["free"]]]*1e-6
    longest = np.max(np.linalg.norm(points[:, [1, 2, 0]]-points, axis=2), axis=1)
    aspect = longest**2/(2*data["areas"])
    if not np.all(np.isfinite(aspect)) or np.any(aspect <= 0):
        raise ValueError("invalid triangle altitude aspect")
    order = np.lexsort((data["free"], -gap))
    cumulative = np.cumsum(gap[order])
    arrays = dict(free_triangle_indices=data["free"], cell_gap_ohm=gap, cell_area_m2=data["areas"], cell_longest_edge_over_altitude=aspect, cell_centroid_um=data["centroids"])
    selections = {}
    for percent in (90, 99):
        count = int(np.searchsorted(cumulative, gap.sum()*percent/100, side="left"))+1
        selected = order[:count]
        branches = np.unique(data["local_branch"][selected])
        branches = branches[branches >= 0]
        free_count = len(gap)
        interior = (topology["branch_first_node"][branches] < free_count) & (topology["branch_second_node"][branches] < free_count)
        marks = branches[interior]
        if len(marks) == 0:
            raise ValueError("selection contains no free/free interior edges")
        support = np.bincount(data["local_branch"][data["local_branch"] >= 0], minlength=len(topology["branch_first_node"]))
        if np.any(support[marks] != 2):
            raise ValueError("marked interior edge does not have two free-cell incidences")
        arrays[f"selected_free_cell_ordinals_{percent}"] = selected
        arrays[f"marked_branch_indices_{percent}"] = marks
        arrays[f"marked_mesh_edges_{percent}"] = topology["branch_mesh_edges"][marks]
        selections[str(percent)] = dict(selected_cell_count=count, cumulative_gap_fraction=float(cumulative[count-1]/gap.sum()), preceding_gap_fraction=float(cumulative[count-2]/gap.sum()) if count > 1 else 0., marked_interior_edge_count=len(marks), filtered_contact_rim_branch_count=int(np.count_nonzero(~interior)), all_marks_free_free=True, all_mark_supports_equal_two=True)
    output.mkdir(exist_ok=False)
    source = Path(__file__)
    (output/"driver-at-run.py").write_bytes(source.read_bytes())
    (output/"localizer-at-run.py").write_bytes(helper.read_bytes())
    target = output/"cell-gap-selection.npz"
    with target.open("xb") as handle:
        np.savez_compressed(handle, **arrays)
    report = dict(program="SPD Decap PI Evaluator", version="0.23.1", status="COMPLETED_GATED_L25_GAP_REFINEMENT_INPUT", script_sha256=prior._sha256(source), localizer_sha256=helper_sha, inputs=receipt["inputs"] | {"pair_result": {"path": str(prior.RESULT), "sha256": prior.RESULT_SHA256}, "rt0_field": {"path": str(prior.RT0_FIELD), "sha256": prior.RT0_FIELD_SHA256}, "p1_field": {"path": str(prior.P1_FIELD), "sha256": prior.P1_FIELD_SHA256}, "pair_driver": {"path": str(prior.DRIVER), "sha256": prior.FROZEN_DRIVER_SHA256}}, energy_names=["gap", "rt0", "cross", "p1"], observed_energy_ohm=observed.tolist(), reference_energy_ohm=expected.tolist(), relative_energy_errors=errors.tolist(), tolerance=1e-10, selections=selections, output={"path": str(target), "sha256": prior._sha256(target)}, elapsed_s=perf_counter()-started, scope="Saved-field gap localization and deterministic minimum-cell selections only; marked edges have two free-cell incidences and omit all rims and natural boundaries. No remeshing, LU, convergence or PowerSI claim.")
    (output/"result.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(dict(status=report["status"], elapsed_s=report["elapsed_s"], relative_energy_errors=errors.tolist(), selections=selections, result_sha256=prior._sha256(output/"result.json"), output=report["output"])))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    run(args.output.resolve())
