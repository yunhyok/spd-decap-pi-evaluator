"""SPD Decap PI Evaluator v0.23.1: exact saved-field L14 GC cell loads for RT0 reconstruction."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from time import monotonic

import numpy as np
from scipy import sparse

import reconstruct_astra_native_loaded_field as recon

ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
OUTPUT = R / "astra-l14-rt0-gc-cell-load-20260912"
PINS = {
    "mesh": (R / "astra-l14-sheet-mesh-preflight-05/mesh-stiffness.npz", "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779"),
    "drive": (R / "astra-l14-sheet-mesh-preflight-05/sheet-electrode-drive.npz", "05a1102082a76cb2f83184576e4d1c4af5985824e134e3199b2ac06fa32c050d"),
    "map": (R / "astra-l02-l14-l25-combined-assembly-map-02/combined-assembly-map.npz", "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
    "accepted_result": (R / "astra-l02-hybrid-right-correction-01/result.json", "7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3"),
    "accepted_field": (R / "astra-l02-hybrid-right-correction-01/field.npz", "960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b"),
    "mass_result": (R / "astra-l14-gc-mass-05/receipt.json", "dd41a5a2772a280d98a73943eb276b94d3eeadd502c981f328eb7548cfb5cdeb"),
    "mass": (R / "astra-l14-gc-mass-05/gc-mass-shadow.npz", "d2a87e4753bbf80ce555c560ff50e4129bdd3ae43b2d7691c7b041c2d776470a"),
    "mass_candidate": (R / "astra-l14-gc-mass-03/owner-mass-candidate-unvalidated.npz", "521a70e96b0c89602e75352ae8d0a1da9fb478b95db52b94da210997917cba2e"),
    "space_result": (R / "astra-l14-rt0-reconstruction-space-20260912/result.json", "05d3e7f646075c657960a15ec9e004b22e9f329260b3aac08234fb04b5c1d4e9"),
    "space": (R / "astra-l14-rt0-reconstruction-space-20260912/space.npz", "bcbda7b8385e9e062aaa764e86b8a1064062d611440c7681176863cc59eec5c0"),
}


def sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def scatter(triangles: np.ndarray, values: np.ndarray, count: int) -> np.ndarray:
    return (np.bincount(triangles.ravel(), weights=values.real.ravel(), minlength=count)
            + 1j * np.bincount(triangles.ravel(), weights=values.imag.ravel(), minlength=count))


def cpair(value: complex) -> list[float]:
    return [float(value.real), float(value.imag)]


def main() -> None:
    started = monotonic()
    budget = recon._Budget.create(120.0, 4.0)
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    receipts = {}
    for name, (path, expected) in PINS.items():
        actual = sha(path)
        if actual != expected:
            raise AssertionError(f"{name} hash mismatch: {actual}")
        receipts[name] = {"path": str(path.resolve()), "sha256": actual, "size_bytes": path.stat().st_size}
    accepted = json.loads(PINS["accepted_result"][0].read_text(encoding="utf-8"))
    mass_result = json.loads(PINS["mass_result"][0].read_text(encoding="utf-8"))
    space_result = json.loads(PINS["space_result"][0].read_text(encoding="utf-8"))
    assert accepted["status"] == "COMPLETED_CONDITIONAL_HYBRID_BLOCK_LGMRES_1MHZ"
    assert accepted["lgmres"]["info"] == 0 and all(accepted["physical"]["gates"].values())
    assert accepted["field"]["sha256"] == PINS["accepted_field"][1]
    assert mass_result["status"] == "COMPLETED_SOURCE_EXACT_L14_GC_P1_MASS_FINALIZE"
    assert mass_result["output"]["sha256"] == PINS["mass"][1]
    assert mass_result["owner_binding"]["owner_count"] == 224
    assert space_result["status"] == "ASSEMBLED_L14_RT0_RECONSTRUCTION_SPACE_WAITING_EXACT_GC_CELL_LOAD"
    assert space_result["artifact_sha256"] == PINS["space"][1]
    budget.check("pinned receipts")

    with np.load(PINS["mesh"][0], allow_pickle=False) as mesh:
        triangles = np.asarray(mesh["triangles"], dtype=np.int64)
    with np.load(PINS["drive"][0], allow_pickle=False) as drive:
        contraction = np.asarray(drive["full_to_contracted"], dtype=np.int64)
    with np.load(PINS["map"][0], allow_pickle=False) as mapping:
        active = np.asarray(mapping["l14_sheet_active_indices"], dtype=np.int64)
    with np.load(PINS["accepted_field"][0], allow_pickle=False) as field:
        voltage = np.asarray(field["active_voltage_v"], dtype=np.complex128)
    with np.load(PINS["space"][0], allow_pickle=False) as space:
        triangle_contact = np.asarray(space["triangle_contact_index"], dtype=np.int64)
        free = np.asarray(space["free_triangle_indices"], dtype=np.int64)
    assert triangles.shape == (214_873, 3) and contraction.shape == (171_957,)
    assert np.array_equal(active, np.r_[718402, np.arange(756889, 903945)])
    phi = voltage[active[contraction]]
    assert triangle_contact.shape == (len(triangles),) and np.array_equal(free, np.flatnonzero(triangle_contact < 0))

    with np.load(PINS["mass_candidate"][0], allow_pickle=False) as candidate:
        rows = np.asarray(candidate["owner_mass_row"], dtype=np.int64).reshape(-1, 6)
        cols = np.asarray(candidate["owner_mass_col"], dtype=np.int64).reshape(-1, 6)
        values = np.asarray(candidate["owner_mass_data_um2"], dtype=np.float64).reshape(-1, 6)
        owner6 = np.asarray(candidate["owner_mass_owner_index"], dtype=np.int64).reshape(-1, 6)
    assert rows.shape == cols.shape == values.shape == owner6.shape
    assert np.all(owner6 == owner6[:, :1]) and np.all(np.isfinite(values))
    owner = owner6[:, 0]
    nodes = rows[:, [0, 3, 5]]
    pairs = np.asarray(((0, 0), (0, 1), (0, 2), (1, 1), (1, 2), (2, 2)))
    assert np.array_equal(rows, nodes[:, pairs[:, 0]]) and np.array_equal(cols, nodes[:, pairs[:, 1]])
    del rows, cols, owner6

    node_count, cell_count = len(phi), len(triangles)
    assert node_count**3 < np.iinfo(np.int64).max and np.all(np.diff(triangles, axis=1) > 0)
    key = lambda array: (array[:, 0] * node_count + array[:, 1]) * node_count + array[:, 2]
    triangle_keys = key(triangles)
    order = np.argsort(triangle_keys)
    sorted_keys = triangle_keys[order]
    cut_keys = key(nodes)
    position = np.searchsorted(sorted_keys, cut_keys)
    assert np.all(position < cell_count) and np.array_equal(sorted_keys[position], cut_keys)
    cut_cell = order[position]
    assert np.array_equal(triangles[cut_cell], nodes)
    cut_area = values[:, [0, 3, 5]].sum(axis=1) + 2.0 * values[:, [1, 2, 4]].sum(axis=1)
    assert np.all(cut_area > 0.0)
    budget.check("exact owner-cut to triangle binding")

    with np.load(PINS["mass"][0], allow_pickle=False) as mass:
        density = np.asarray(mass["owner_density_f_per_um2"], dtype=np.float64)
        dispersion = np.asarray(mass["owner_dispersion_s_per_f"], dtype=np.complex128)
        fingerprints = np.asarray(mass["owner_fingerprints_json_utf8"], dtype=np.uint8)
        bindings = json.loads(np.asarray(mass["owner_bindings_json_utf8"], dtype=np.uint8).tobytes())
        y = sparse.csc_matrix((mass["physical_gc_y_data_s"], mass["physical_gc_y_indices"], mass["physical_gc_y_indptr"]),
                              shape=tuple(mass["physical_gc_y_shape"]))
        owner_area = np.asarray(mass["owner_overlap_area_um2"], dtype=np.float64)
    assert density.shape == dispersion.shape == owner_area.shape == (224,) and len(bindings) == 224
    assert y.shape == (171_960, 171_960)
    external_global = np.asarray(sorted({int(row["external_global_reduced_index"]) for row in bindings}), dtype=np.int64)
    external_active = np.asarray([next(int(row["external_active_index"]) for row in bindings
                                       if int(row["external_global_reduced_index"]) == value) for value in external_global], dtype=np.int64)
    assert external_global.shape == external_active.shape == (3,)
    external_slot = {int(value): index for index, value in enumerate(external_global)}
    owner_external_slot = np.asarray([external_slot[int(row["external_global_reduced_index"])] for row in bindings], dtype=np.int64)
    owner_external_active = np.asarray([int(row["external_active_index"]) for row in bindings], dtype=np.int64)

    local_injection = np.zeros((cell_count, 3), dtype=np.complex128)
    owner_outgoing = np.zeros(224, dtype=np.complex128)
    coefficient = density * dispersion
    for begin in range(0, len(cut_cell), 50_000):
        end = min(begin + 50_000, len(cut_cell))
        a = values[begin:end]
        u = phi[nodes[begin:end]] - voltage[owner_external_active[owner[begin:end]], None]
        action = np.column_stack((
            a[:, 0]*u[:, 0] + a[:, 1]*u[:, 1] + a[:, 2]*u[:, 2],
            a[:, 1]*u[:, 0] + a[:, 3]*u[:, 1] + a[:, 4]*u[:, 2],
            a[:, 2]*u[:, 0] + a[:, 4]*u[:, 1] + a[:, 5]*u[:, 2],
        )) * coefficient[owner[begin:end], None]
        np.add.at(local_injection, cut_cell[begin:end], -action)
        np.add.at(owner_outgoing, owner[begin:end], action.sum(axis=1))
        budget.check("accepted-field element GC action")

    triangle_injection = local_injection.sum(axis=1)
    nodal_outgoing = -scatter(triangles, local_injection, node_count)
    external_outgoing = np.zeros(3, dtype=np.complex128)
    np.add.at(external_outgoing, owner_external_slot, -owner_outgoing)
    sparse_action = y @ np.r_[phi, voltage[external_active]]
    action_replay = np.r_[nodal_outgoing, external_outgoing]
    action_scale = max(float(np.linalg.norm(sparse_action)), np.finfo(float).tiny)
    action_relative = float(np.linalg.norm(action_replay - sparse_action) / action_scale)
    global_conservation = abs(complex(action_replay.sum()))
    owner_cell_area = sparse.coo_matrix((cut_area, (owner, cut_cell)), shape=(224, cell_count)).tocsr()
    area_sum = np.asarray(owner_cell_area.sum(axis=1)).ravel()
    area_relative = float(np.max(np.abs(area_sum - owner_area) / owner_area))

    contact_injection = np.zeros(1660, dtype=np.complex128)
    contact_cells = np.flatnonzero(triangle_contact >= 0)
    np.add.at(contact_injection, triangle_contact[contact_cells], triangle_injection[contact_cells])
    free_injection = triangle_injection[free]
    partition_error = abs(complex(free_injection.sum() + contact_injection.sum() - triangle_injection.sum()))
    owner_external_error = float(np.max(np.abs(np.bincount(owner_external_slot, weights=owner_outgoing.real, minlength=3)
                                                + 1j*np.bincount(owner_external_slot, weights=owner_outgoing.imag, minlength=3)
                                                + external_outgoing)))
    finite = bool(all(np.isfinite(x).all() for x in (local_injection, owner_outgoing, external_outgoing)))
    gates = {
        "all_224_source_owners_preserved": bool(np.array_equal(np.unique(owner), np.arange(224))),
        "three_external_terminals_preserved": bool(len(external_active) == 3),
        "owner_triangle_cut_area_recollapse_le_2e_12": bool(area_relative <= 2e-12),
        "p1_sparse_gc_action_replay_le_2e_10": bool(action_relative <= 2e-10),
        "owner_to_external_terminal_conservation_le_2e_12": bool(owner_external_error <= 2e-12 * max(float(np.max(np.abs(owner_outgoing))), np.finfo(float).tiny)),
        "global_gc_action_conservation_le_2e_12": bool(global_conservation <= 2e-12 * action_scale),
        "free_contact_triangle_partition_exact": bool(partition_error <= 2e-12 * max(float(np.linalg.norm(triangle_injection)), np.finfo(float).tiny)),
        "finite": finite,
        "rt0_topology_has_zero_noncontact_exterior_flux": bool(space_result["noncontact_exterior_edges"] == 145397),
        "rt0_resistance_positive": bool(space_result["minimum_local_r_eigenvalue_ohm"] > 0.0),
        "rt0_closed_space_retained": bool(space_result["closed_current_dimension"] == 34739),
    }
    OUTPUT.mkdir(parents=True)
    frozen = OUTPUT / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    artifact = OUTPUT / "l14-gc-cell-load.npz"
    with artifact.open("xb") as stream:
        np.savez_compressed(stream,
            p1_local_gc_injection_a=local_injection,
            triangle_gc_injection_a=triangle_injection,
            free_triangle_gc_injection_a=free_injection,
            contact_interior_gc_injection_a=contact_injection,
            owner_gc_outgoing_from_l14_a=owner_outgoing,
            external_terminal_global_reduced_indices=external_global,
            external_terminal_active_indices=external_active,
            external_terminal_gc_outgoing_a=external_outgoing,
            owner_cell_area_um2_data=owner_cell_area.data,
            owner_cell_area_um2_indices=owner_cell_area.indices,
            owner_cell_area_um2_indptr=owner_cell_area.indptr,
            owner_cell_area_um2_shape=np.asarray(owner_cell_area.shape, dtype=np.int64),
            owner_fingerprints_json_utf8=fingerprints,
            free_triangle_indices=free,
            triangle_contact_index=triangle_contact,
            frequency_hz=np.asarray([1e6]),
        )
    budget.check("saved exact cell load")
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1",
        "status": "PASS_ACCEPTED_1MHZ_L14_EXACT_GC_CELL_LOAD_FOR_RT0_RECONSTRUCTION" if all(gates.values()) else "STOP_L14_RT0_GC_CELL_LOAD_GATE",
        "inputs": receipts, "driver_sha256": sha(frozen), "artifact_sha256": sha(artifact),
        "counts": {"triangles": cell_count, "free_triangles": len(free), "contact_interior_triangles": len(contact_cells),
                   "contacts": 1660, "owners": 224, "external_terminals": 3, "positive_owner_triangle_cuts": len(cut_cell),
                   "owner_cell_nonzeros": int(owner_cell_area.nnz)},
        "metrics": {"owner_cut_area_relative_max": area_relative, "p1_sparse_gc_action_relative": action_relative,
                    "global_gc_action_conservation_abs_a": global_conservation, "owner_external_conservation_max_abs_a": owner_external_error,
                    "free_contact_partition_abs_a": partition_error,
                    "total_triangle_gc_injection_a": cpair(complex(triangle_injection.sum())),
                    "total_contact_interior_gc_injection_a": cpair(complex(contact_injection.sum())),
                    "total_free_triangle_gc_injection_a": cpair(complex(free_injection.sum())),
                    "external_terminal_gc_outgoing_a": [cpair(complex(value)) for value in external_outgoing]},
        "gates": gates, "budget": budget.receipt(),
        "signs": {"p1_local_gc_injection_a": "positive into the L14 sheet test function",
                  "triangle_gc_injection_a": "sum of the three local P1 injections, positive into the triangle",
                  "owner_gc_outgoing_from_l14_a": "positive from L14 toward the source-owned external terminal",
                  "external_terminal_gc_outgoing_a": "positive from each external terminal toward L14"},
        "scope": "Exact accepted-1MHz P1 GC action partitioned by source-owned overlap mass into L14 triangles, contact interiors, 224 owners and three external terminals. This supplies a conservative source ledger for the separate RT0 reconstruction. It performs no current reconstruction, solve, Green action, full-board factor, P1/RT0 equality gate, finite response or accuracy claim.",
    }
    (OUTPUT / "result.json").write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    if not all(gates.values()):
        raise AssertionError(gates)
    print(json.dumps({"status": result["status"], "elapsed_s": monotonic()-started,
                      "artifact_sha256": result["artifact_sha256"], "metrics": result["metrics"]}, allow_nan=False), flush=True)


if __name__ == "__main__":
    main()
