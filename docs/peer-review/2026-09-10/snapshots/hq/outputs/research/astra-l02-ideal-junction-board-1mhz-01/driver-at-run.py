"""Isolate20 source pad/trace L02 junction corrections on the original ideal-sheet circuit."""
import argparse
import json
from pathlib import Path
import time
from types import SimpleNamespace

import numpy as np
from scipy import sparse

import project_astra_l14_gc_mass as mass
import run_astra_l14_sheet_r_shadow as solver
from run_astra_two_sheet_frequency_shadow import branch_action

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
N, TARGET, F = 756889, 349710, 1e6
PINS = {key: solver.PINS[key] for key in ("raw", "derived", "baseline_reconstruction")}
PINS.update({
    "paths": (R / "astra-l02-composite-source-paths-03/result.json", "e534da4f465ecb081ae3ec0bcdf96a1ba5329bd4f5c1353b6dfa262ef4f858ff"),
    "paths_review": (R / "astra-l02-composite-source-paths-03/independent-review.json", "3ae31ad5e917cd6d207e90fdf654918fc8dfb51f31f1c20113529df4ed4c98be"),
    "contacts": (R / "astra-l02-source-contact-overlaps-01/result.json", "5431d8f042b3c4ac3d53c28441f0301fad6a7a350a87add98aa6f9466e6b272f"),
    "contact_review": (R / "astra-l02-source-contact-overlaps-01/independent-review.json", "10ae993144dcda1e01503cf70287ada4e02b5b5e541189bbeec0c792496cf811"),
    "excluded_domain": (R / "astra-l02-excluded-via-flat-domain-01/result.json", "5e18de3bba96b07870175058852aacd5ac2c937c8ffe946bd95518899e337587"),
    "excluded_review": (R / "astra-l02-excluded-via-flat-domain-01/independent-review.json", "707ed47c3380ab6af1b0e79e38327337b74afa0bc50364165218dcb4e3702a7c"),
    "flat_domain": (R / "astra-l02-flat-conductor-domain-01/result.json", "c2757aa69aca3186852b8332c2ccf6cc84ec170f1a34fdb0098a09a74e8050d4"),
    "solver_helper": (Path(solver.__file__), "ff234c89be9bb6828f4efa3e559116c903d62b463f9f9fec6893abf71a369803"),
    "trace_helper": (Path(solver.trace.__file__), "52b5e319e071827bb1854f1ea79f066f2fd9e856f410a23534832ad22edb40c4"),
    "reconstructor": (Path(solver.recon.__file__), "354d9e004b189cf8193c2922c8fa4dc1903b407bebb295a4576673200ce6b213"),
    "current_helper": (ROOT / "tools/research/run_astra_two_sheet_frequency_shadow.py", "7e42bbb02679aa974451776e7f9577a68ba832f003884958afc744091f45da5c"),
    "persistence_helper": (Path(mass.__file__), "f2c668fd090db5c4606eec27558c9bdbbdde6552d2f82c3b64b8837bd7350dc3"),
})
sha = solver.recon._sha256_file
pair = solver.pair


def series_control(z1, z2):
    assert z1.real > 0 and z2.real > 0 and z1.imag >= 0 and z2.imag >= 0
    y1, y2 = 1 / z1, 1 / z2
    star = solver.trace.laplacian(np.array([0, 2]), np.array([2, 1]), np.array([y1, y2]), 3).toarray()
    original = solver.trace.laplacian(np.array([0]), np.array([1]), np.array([1 / (z1 + z2)]), 3).toarray()
    eliminated = star[:2, :2] - np.outer(star[:2, 2], star[2, :2]) / star[2, 2]
    relative = float(np.max(abs(eliminated - original[:2, :2])) / np.max(abs(original)))
    w = np.array([y1 / (y1 + y2), y2 / (y1 + y2), -1])
    delta = (y1 + y2) * np.outer(w, w)
    assert relative < 2e-12 and np.allclose(star - original, delta, rtol=2e-12, atol=1e-12)
    assert np.max(abs(star.sum(axis=1))) < 1e-12
    return relative


def self_check():
    assert series_control(.001 + .003j, .002 + .0004j) < 2e-12
    # Positive branch network and an explicit free midpoint give the same external solution.
    star = solver.trace.laplacian(np.array([0, 2, 1]), np.array([2, 1, 3]), np.array([1/(1+2j), 1/(3+4j), 1/5]), 4).toarray()
    v = np.linalg.solve(star[:3, :3], np.array([1., 0., 0.]))
    assert abs(v[0] - (9 + 6j)) < 1e-12


def run(output, assemble_only):
    started = time.monotonic()
    for path, expected in PINS.values():
        assert sha(path) == expected, path
    paths = json.loads(PINS["paths"][0].read_bytes())
    excluded = {row["via_id"].casefold(): row for row in json.loads(PINS["excluded_domain"][0].read_bytes())["rows"]}
    assert paths["composite_count"] == paths["active_composite_count"] == 20
    assert paths["total_source_via_owner_count"] == 93
    links = paths["links"]
    index = np.array([row["active_finite_index"] for row in links], dtype=np.int64)
    assert len(np.unique(index)) == 20
    geometrical_owners = set()
    first_z, second_z, controls = [], [], []
    for link in links:
        assert TARGET not in link["active_endpoints"] and len(set(link["active_endpoints"])) == 2
        for owner in link["l02_excluded_owner_ids"]:
            assert owner not in geometrical_owners
            geometrical_owners.add(owner)
            witness = excluded[owner]
            source = next(row for row in link["source_vias_in_native_path_order"] if row["via_id_fold"] == owner)
            assert witness["source_row_sha256"] == source["source_record_sha256"]
            assert witness["pad_overlap_area_um2"] > 0 and witness["drill_overlap_area_um2"] == 0
        z1 = link["first_leg_resistance_ohm"] + 2j * np.pi * F * link["first_leg_inductance_h"]
        z2 = link["second_leg_resistance_ohm"] + 2j * np.pi * F * link["second_leg_inductance_h"]
        controls.append(series_control(z1, z2)); first_z.append(z1); second_z.append(z2)
    assert len(geometrical_owners) == 40
    with np.load(PINS["raw"][0], allow_pickle=False) as raw, np.load(PINS["derived"][0], allow_pickle=False) as derived:
        first, second = raw["finite_first_active_indices"], raw["finite_second_active_indices"]
        count, resistance, inductance = raw["finite_count"], raw["finite_resistance_ohm_per_via"], raw["finite_inductance_h_per_via"]
        original_index = raw["finite_active_original_indices"]
        assert np.all(count[index] == 1)
        for i, link in zip(index, links, strict=True):
            assert [int(first[i]), int(second[i])] == link["active_endpoints"]
            assert original_index[i] == link["ordinal"]
            assert resistance[i] == link["resistance_ohm"] and inductance[i] == link["inductance_h"]
        original_z = resistance + 2j*np.pi*F*inductance
        assert np.allclose(np.asarray(first_z) + second_z, original_z[index], rtol=1e-13, atol=0)
        names = solver.recon._decode_text_vector(raw["surface_node_ids"], "surface IDs")
        lookup = {name: i for i, name in enumerate(names)}
        global_to_active = raw["global_to_active_indices"]
        surface_map = raw["surface_to_reduced_indices"]
        assert global_to_active[surface_map[lookup["spd-finite-via-vertex:75e70fea61021703aa9311b5"]]] == TARGET
        gc, _ = solver.recon._build_partial_matrix(raw, surface_lookup=lookup, surface_to_reduced=surface_map,
                                                  global_to_active=global_to_active, active_size=N)
        termination, termination_y, _ = solver.recon._termination_matrix(derived, N)
        tp, tn = derived["termination_positive_active_indices"], derived["termination_negative_active_indices"]
        original_y = count / original_z
        original_finite = solver.trace.laplacian(first, second, original_y, N)
        saved = raw["active_voltage"]
        assert saved.shape == (N, 1)
        saved = saved[:, 0]
        gauge = int(raw["gauge_active_index"].item())
        port = int(raw["batch_port_indices"].item())
        # Saved native solve has local batch index0; PowerSI port18 is a separate reference index.
        assert port == 0 and solver.recon._decode_text_vector(raw["batch_port_ids"], "port") == (solver.RAIL,)
        positive, negative = map(int, global_to_active[raw["solve_port_reduced_nodes"][port]])
        assert (positive, negative) == (2699, 2656) and saved[gauge] == 0
        rhs = np.zeros(N, dtype=complex); rhs[positive], rhs[negative] = 1, -1
        baseline = complex(saved[positive] - saved[negative])
        prior = json.loads(PINS["baseline_reconstruction"][0].read_bytes())
        assert baseline == complex(*prior["comparison_to_saved_field"]["saved_zdd_ohm"])
        original_action = gc @ saved + branch_action(first, second, original_y, saved) + branch_action(tp, tn, termination_y, saved)
        source_residual = float(np.max(abs(original_action - rhs)))
        original_matrix = original_finite + gc + termination
        csc_residual = float(np.max(abs(original_matrix @ saved - rhs)))
        assert max(source_residual, csc_residual) < 1e-8
        keep = np.ones(len(first), dtype=bool); keep[index] = False
        new_first = np.r_[first[keep], first[index], np.full(20, TARGET)]
        new_second = np.r_[second[keep], np.full(20, TARGET), second[index]]
        new_y = np.r_[original_y[keep], 1/np.asarray(first_z), 1/np.asarray(second_z)]
        assert len(new_y) == len(original_y) + 20 and np.all(new_y.real > 0)
        positive_branch_rebuild = solver.trace.laplacian(new_first, new_second, new_y, N)
        # Compare the full change with only the60 explicit removal/addition stamps.
        expected_delta = solver.trace.laplacian(np.r_[first[index], np.full(20,TARGET), first[index]],
            np.r_[np.full(20,TARGET), second[index], second[index]],
            np.r_[1/np.asarray(first_z), 1/np.asarray(second_z), -original_y[index]], N)
        # Preserve the unchanged native sums. Reordering1.69M branches changes large diagonals by roundoff.
        corrected = (original_finite + expected_delta).tocsc()
        branch_rebuild_check = solver.trace.sparse_difference(corrected, positive_branch_rebuild)
        assert branch_rebuild_check["relative_maximum_difference"] < 2e-12
        stamp_check = solver.trace.sparse_difference(corrected - original_finite, expected_delta)
        assert stamp_check["relative_maximum_difference"] < 2e-12
        del original_matrix, original_finite, original_action, expected_delta, positive_branch_rebuild
        def actions(v):
            return {"original_gc": gc @ v, "finite_via": branch_action(new_first, new_second, new_y, v),
                    "termination": branch_action(tp, tn, termination_y, v), "sheet_dc": np.zeros(N, dtype=complex)}
        assembly = {"series_elimination_max_relative_difference": max(controls), "original_source_current_residual_a": source_residual,
                    "original_csc_residual_a": csc_residual, "original_zdd_ohm": pair(baseline), "changed_stamp_control": stamp_check,
                    "independent_positive_branch_rebuild_control": branch_rebuild_check,
                    "original_finite_count": len(original_y), "corrected_finite_count": len(new_y),
                    "changed_original_active_finite_indices": index.tolist(), "source_via_owner_count": 93,
                    "new_ideal_l02_junction_count": 20, "positive_active_index": positive, "negative_active_index": negative,
                    "gauge_active_index": gauge, "elapsed_s": time.monotonic()-started}
        mass.atomic_json(output / "assembly.json", assembly)
        print(json.dumps({"event": "assembly_pass", **assembly}), flush=True)
        point = None
        if not assemble_only:
            point = solver.solve_point({"original_gc": gc, "finite_via": corrected, "termination": termination},
                sparse.csc_matrix((N,N), dtype=complex), 1., gauge, positive, negative, np.array([], dtype=np.int64),
                output, SimpleNamespace(emit=lambda event, **kw: print(json.dumps({"event":event, **kw}), flush=True)),
                baseline=baseline, current_actions=actions,
                field_arrays={"changed_original_active_finite_indices":index, "junction_active_index":np.array([TARGET]),
                              "first_leg_z_ohm":np.asarray(first_z), "second_leg_z_ohm":np.asarray(second_z)})
            assert point["physical_residual_max_abs_a"] < 1e-7 and point["csc_matvec_residual_max_abs_a"] < 1e-7
        result = {"program": "SPD Decap PI Evaluator", "version": "0.23.1",
                  "status": "COMPLETED_L02_IDEAL_JUNCTION_ASSEMBLY" if assemble_only else "COMPLETED_CONDITIONAL_L02_IDEAL_JUNCTION_SHADOW",
                  "script_sha256": sha(Path(__file__)), "inputs": {key:{"path":str(path),"sha256":expected} for key,(path,expected) in PINS.items()},
                  "frequency_hz": F, "assembly":assembly, "point":point, "elapsed_s":time.monotonic()-started,
                  "scope": "20 recovered original series paths are split at their source L02 nodes and tied to the original ideal L02 quotient, conditional on flat trace bodies and source circular pad contact. All93 source via R/L contributions remain exactly once. Original G/C, terminations, other native branches and two excluded leaves stay unchanged. Free-midpoint elimination reproduces each original branch; the tied circuit is deliberately a topology correction, not original-Y recollapse. No finite L02 sheet, pad equipotential-radius selection, new trace R, magnetic coupling or PowerSI fitting. Reused epsilon-1 labels are storage conventions; the sheet matrix is zero."}
        mass.atomic_json(output / "result.json", result)
        print(json.dumps({"status":result["status"], "elapsed_s":result["elapsed_s"]}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--assemble-only", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("PASS_L02_IDEAL_JUNCTION_SELF_CHECK")
    else:
        if args.output is None:
            parser.error("--output is required")
        output = args.output.resolve(); output.mkdir(exist_ok=False)
        (output / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
        try:
            run(output, args.assemble_only)
        except BaseException as exc:
            mass.atomic_json(output / "failure.json", {"status":"STOP_L02_IDEAL_JUNCTION_SHADOW", "error_type":type(exc).__name__, "error":str(exc)})
            raise
