"""Freeze accepted L02/L14/L25/first-post source descriptors; no magnetic action."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from time import monotonic

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs" / "research"
PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
OUTPUT_NAME = "astra-combined-magnetic-source-descriptor-02"
MAX_SECONDS = 120.0
MAX_RSS_BYTES = 8 * 1024**3
EXPECTED_PHYSICAL_GATES = {
    "backward", "cell_kcl", "csc_kcl", "finite_forward_bound",
    "global_current_cell_kcl", "l02_global_constitutive",
    "l02_local_constitutive", "l25_constitutive",
    "local_global_rt0_joule", "local_rt0_joule_passivity", "matrix_power",
    "passivity", "physical_power", "physical_source_kcl",
    "shared_facet_jump_before_averaging", "source_forward_bound",
}

PINS = {
    "accepted_result": (R / "astra-l02-hybrid-right-correction-01" / "result.json", "7341e700f5ac76f96f07558a42bc93181319b118ffb86bab64f80c5bdb54b9d3"),
    "accepted_field": (R / "astra-l02-hybrid-right-correction-01" / "field.npz", "960e767c383cd7e5580dfc86e9a221cfdff78086f8478465a25d8bd4dd98654b"),
    "accepted_driver": (R / "astra-l02-hybrid-right-correction-01" / "driver-at-run.py", "91e792487f371d14c21e9ce6729f17b34b93386f9f1d0c599302b129d2d755a6"),
    "physical_diagnostic": (R / "astra-l02-hybrid-right-correction-01" / "physical-diagnostic.json", "337042aae1110021cae6c678d817d6f3dd5a5941bd3e7ec077414aca44b3f71b"),
    "fd28_validator": (ROOT / "tools/research/validate_astra_l02_hybrid_field.py", "fd28d5a3eef17d7d85e0fab52de1a88e1910faa2ad46a4cde3b16d3607c8abd9"),
    "l02_reconstructed": (R / "astra-l02-hybrid-right-correction-01" / "l02-reconstructed-field.npz", "b715867457410d6e2ba5154143b479f4d0868f263a3d487d5780c2c77c6c6f19"),
    "l02_space": (R / "astra-l02-full-rt0-current-space-01" / "l02-rt0-current-space.npz", "7cd77ee8b03fd0226596748a1913ee5a913381617498c9927ef12d8b080bc98f"),
    "l02_mesh": (R / "astra-l02-sheet-mesh-preflight-02" / "mesh-stiffness.npz", "137b4952f739a75e7bf6f2e21684529a43c48558412df59faab0ad003ea10fb9"),
    "l14_result": (R / "astra-accepted-l14-p1-currents-01" / "result.json", "195fe8c97f0408c163833efb38723179740bed8fa7fd6ef6b4330b9e81c33a11"),
    "l14_driver": (R / "astra-accepted-l14-p1-currents-01" / "driver-at-run.py", "4699cc5731fc9bd0114dfc4276b9c10a9e310acb0a0ca4337199c120e0a49bc9"),
    "l14_current": (R / "astra-accepted-l14-p1-currents-01" / "l14-p1-current-density.npz", "55ba26a5c804913e79d130a562d0eae96f0b6f13542992b55ba96731910a4153"),
    "l14_mesh": (R / "astra-l14-sheet-mesh-preflight-05" / "mesh-stiffness.npz", "a7a8234df9fb173a6fd88a5c613fc074321b984d5e7f0708842d66c8f0b9e779"),
    "l25_step_result": (R / "astra-l25-adaptive-longest-pair-01" / "step-06" / "result.json", "9734d6b5222ad72488a07bea7a901874cb983c2a6710d862faaee07233865217"),
    "l25_mesh": (R / "astra-l25-adaptive-longest-pair-01" / "step-06" / "mesh.npz", "20b52d26783bf98197524c56f22d9872aee3b67b7e526ff20a02e396f39e89cb"),
    "l25_topology": (R / "astra-l25-adaptive-longest-pair-01" / "step-06" / "topology.npz", "d70ceb1b38cede682247967495c6ef83d08ef2b0ef63e3543df908ba13412a4d"),
    "rt0_affine_moments_helper": (ROOT / "tools/research/probe_astra_l25_saved_current_moments.py", "e30fefd2ecb618f58fa8ca2eb3486b0f05623a75f33e1be6a24f590be593367e"),
    "first_post_result": (R / "astra-accepted-current-first-post-join-01" / "result.json", "fe8e063f958b6753d639cbe145f38599241f96771a37a55ddc5c5a2306171298"),
    "first_post_driver": (R / "astra-accepted-current-first-post-join-01" / "driver-at-run.py", "868470527cd518b7a13b5c92eaaca1df2f291eb648103ea15de57f308a48dcf0"),
    "first_post": (R / "astra-accepted-current-first-post-join-01" / "accepted-current-first-post.npz", "95537c104e8f750e877c35f65e0961a26ee0b7fc3521b7ea049b44f1ed6c4754"),
    "stack_result": (R / "astra-3d-source-domain-inventory-01" / "result.json", "daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663"),
    "stack_driver": (ROOT / "tools/research/inspect_astra_3d_source_domain_inventory.py", "081231139a02c5dcb2e61d8c96bae0806fa9dfd7da20fbca609326dd77405b16"),
    "composite_result": (R / "astra-full-return-composite-sources-03" / "result.json", "01cfbb2cbb324a2c7421d46e112a17e8ece2d3a6137aecbad12cc74dd07ff0c3"),
    "composite_delta": (R / "astra-full-return-composite-sources-03" / "composite-source-segment-delta.npz", "b47b6031c901e50e41c7dc12519a5fcadb7699e236d461df4d159189cd625faa"),
    "combined_map_result": (R / "astra-l02-l14-l25-combined-assembly-map-02" / "result.json", "cdbb80f3412732f614c1d482a3cc8288c5c6134b5dfb1de2376711309039ae81"),
    "combined_map": (R / "astra-l02-l14-l25-combined-assembly-map-02" / "combined-assembly-map.npz", "a6ccff514fe788396eb9d7620ebc828f966efbea3f7899e7a73991c17f8a2469"),
}


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def receipt(path: Path) -> dict[str, object]:
    return {"path": str(path.resolve()), "sha256": sha256(path), "size_bytes": path.stat().st_size}


def rss_bytes() -> int:
    if os.name != "nt":
        return 0
    import ctypes
    from ctypes import wintypes

    class Counters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("page_fault_count", ctypes.c_ulong),
                    ("peak_working_set_size", ctypes.c_size_t), ("working_set_size", ctypes.c_size_t),
                    ("quota_peak_paged_pool_usage", ctypes.c_size_t), ("quota_paged_pool_usage", ctypes.c_size_t),
                    ("quota_peak_non_paged_pool_usage", ctypes.c_size_t), ("quota_non_paged_pool_usage", ctypes.c_size_t),
                    ("pagefile_usage", ctypes.c_size_t), ("peak_pagefile_usage", ctypes.c_size_t),
                    ("private_usage", ctypes.c_size_t)]
    counters = Counters()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    psapi = ctypes.WinDLL("psapi", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    getter = psapi.GetProcessMemoryInfo
    getter.argtypes = (wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD)
    getter.restype = wintypes.BOOL
    if not getter(kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
        raise ctypes.WinError(ctypes.get_last_error())
    return int(counters.working_set_size)


class Budget:
    def __init__(self) -> None:
        self.started = monotonic()
        self.peak_rss_bytes = rss_bytes()

    def check(self, phase: str) -> None:
        elapsed = monotonic() - self.started
        self.peak_rss_bytes = max(self.peak_rss_bytes, rss_bytes())
        if elapsed > MAX_SECONDS:
            raise RuntimeError(f"time budget exceeded during {phase}: {elapsed:.3f}s")
        if self.peak_rss_bytes > MAX_RSS_BYTES:
            raise RuntimeError(f"RSS budget exceeded during {phase}: {self.peak_rss_bytes}")

    def document(self) -> dict[str, object]:
        self.check("receipt")
        return {"kind": "cooperative in-process elapsed/RSS check; not an external guard",
                "max_runtime_s": MAX_SECONDS, "max_rss_bytes": MAX_RSS_BYTES,
                "elapsed_s": monotonic() - self.started, "peak_rss_bytes": self.peak_rss_bytes}


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def atomic_npz(path: Path, arrays: dict[str, np.ndarray]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("xb") as stream:
        np.savez_compressed(stream, **arrays)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def atomic_json(path: Path, value: dict[str, object]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


def verify_pins() -> None:
    for name, (path, expected) in PINS.items():
        require(path.is_file(), f"missing pinned {name}")
        require(sha256(path) == expected, f"pinned SHA differs: {name}")


def load_moments():
    """Load the pinned canonical affine RT0 reconstruction after byte verification."""
    path = PINS["rt0_affine_moments_helper"][0]
    specification = importlib.util.spec_from_file_location("pinned_rt0_affine_moments", path)
    require(specification is not None and specification.loader is not None, "affine moments module spec")
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    function = getattr(module, "moments", None)
    require(callable(function), "affine moments function missing")
    return function


def selected_layer(stack: dict[str, object], name: str) -> dict[str, object]:
    rows = [row for row in stack["stackup"]["conductors"] if row["name"] == name]
    require(len(rows) == 1, f"missing or duplicate stack layer {name}")
    row = rows[0]
    require(float(row["z_bottom_um"]) > float(row["z_top_um"]), f"invalid slab {name}")
    return row


def triangle_vertices(mesh: dict[str, np.ndarray], triangle_ids: np.ndarray) -> np.ndarray:
    xy = np.asarray(mesh["node_xy_um"], dtype=np.float64)
    triangles = np.asarray(mesh["triangles"], dtype=np.int64)
    require(triangle_ids.ndim == 1 and np.all(triangle_ids >= 0) and np.all(triangle_ids < len(triangles)), "triangle IDs invalid")
    vertices = xy[triangles[triangle_ids]]
    require(vertices.shape == (len(triangle_ids), 3, 2) and np.all(np.isfinite(vertices)), "triangle XY invalid")
    return vertices


def load_npz(path: Path) -> dict[str, np.ndarray]:
    with np.load(path, allow_pickle=False) as archive:
        return {name: np.asarray(archive[name]) for name in archive.files}


def run(output: Path) -> dict[str, object]:
    require(not output.exists(), f"refusing to overwrite {output}")
    budget = Budget()
    verify_pins()
    moments = load_moments()
    budget.check("all hard pins")
    output.mkdir(parents=True)
    frozen = output / "driver-at-run.py"
    frozen.write_bytes(Path(__file__).read_bytes())
    try:
        accepted = json.loads(PINS["accepted_result"][0].read_bytes())
        physical = json.loads(PINS["physical_diagnostic"][0].read_bytes())
        require(accepted["status"] == "COMPLETED_CONDITIONAL_HYBRID_BLOCK_LGMRES_1MHZ", "accepted numerical status")
        require(accepted["field"]["sha256"] == PINS["accepted_field"][1], "accepted field receipt")
        require(accepted["driver"]["sha256"] == PINS["accepted_driver"][1], "accepted driver receipt")
        require(accepted["physical"] == physical, "accepted/physical receipt mismatch")
        require(physical["status"] == "PASS_CONDITIONAL_HYBRID_PHYSICAL_FIELD", "fd28 physical status")
        require(physical["validator_sha256"] == PINS["fd28_validator"][1], "fd28 validator receipt")
        require(set(physical["gates"]) == EXPECTED_PHYSICAL_GATES and all(physical["gates"].values()), "fd28 gates")
        require(physical["physical"]["reconstructed_field"]["sha256"] == PINS["l02_reconstructed"][1], "L02 reconstruction receipt")
        require("No magnetic" in physical["scope"], "accepted operator magnetic scope changed")
        budget.check("accepted fd28 chain")

        stack = json.loads(PINS["stack_result"][0].read_bytes())
        require(stack["status"] == "COMPLETE_CACHED_3D_SOURCE_DOMAIN_INVENTORY_WITH_EXPLICIT_GAPS", "stack status")
        require(stack["stackup"]["coordinate_convention"] == "z=0 at Signal$TOP top face; positive z follows source stackup order", "stack coordinate convention")
        l02_slab = selected_layer(stack, "Signal$L02(DGND)")
        l14_slab = selected_layer(stack, "Signal$L14(MAIN_POWER4)")
        l25_slab = selected_layer(stack, "Signal$L25(MAIN_POWER4)")
        top_slab = selected_layer(stack, "Signal$TOP")
        require((l02_slab["z_top_um"], l02_slab["z_bottom_um"], l14_slab["z_top_um"], l14_slab["z_bottom_um"], l25_slab["z_top_um"], l25_slab["z_bottom_um"], top_slab["z_top_um"], top_slab["z_bottom_um"]) == (55.0, 75.0, 655.0, 675.0, 1511.0, 1543.0, 0.0, 25.0), "saved layer slabs")
        budget.check("stack layer slabs")

        field = load_npz(PINS["accepted_field"][0])
        require(field["active_voltage_v"].shape == (3_178_104,) and np.all(np.isfinite(field["active_voltage_v"])), "accepted voltage")
        l25_q = np.asarray(field["l25_branch_current_a"], dtype=np.complex128)
        require(l25_q.shape == (604_031,) and np.all(np.isfinite(l25_q)), "accepted L25 branch q")

        l02_space = load_npz(PINS["l02_space"][0])
        l02_reconstructed = load_npz(PINS["l02_reconstructed"][0])
        l02_mesh = load_npz(PINS["l02_mesh"][0])
        l02_ids = np.asarray(l02_space["free_triangle_indices"], dtype=np.int64)
        l02_facets = np.asarray(l02_space["local_facet_branch_index"], dtype=np.int64)
        l02_signs = np.asarray(l02_space["local_outward_flux_sign"], dtype=np.int8)
        l02_branch_q = np.asarray(l02_reconstructed["l02_branch_current_a"], dtype=np.complex128)
        l02_cell_q = np.asarray(l02_reconstructed["cell_outward_flux_a"], dtype=np.complex128)
        require(l02_ids.shape == (1_583_840,) and l02_facets.shape == l02_signs.shape == l02_cell_q.shape == (1_583_840, 3), "L02 RT0 shapes")
        require(l02_branch_q.shape == (3_095_567,) and np.all(np.isfinite(l02_branch_q)) and np.all(np.isfinite(l02_cell_q)), "L02 currents finite")
        require(np.array_equal(l02_reconstructed["zero_flux_exterior_branch_indices"], l02_space["retained_exterior_branch_indices"]), "L02 exterior indices")
        l02_local_reconstruction = l02_signs * l02_branch_q[l02_facets]
        l02_local_error = float(np.max(abs(l02_local_reconstruction - l02_cell_q)))
        require(l02_local_error <= 3e-14, "L02 branch-to-local current reconstruction")
        l02_vertices = triangle_vertices(l02_mesh, l02_ids)
        l02_area, l02_average, l02_alpha, l02_mean_l2, l02_variation_l2 = moments(
            l02_vertices * 1e-6, l02_local_reconstruction)
        require(np.all(np.isfinite(l02_area)) and np.all(np.isfinite(l02_average))
                and np.all(np.isfinite(l02_alpha)) and np.all(np.isfinite(l02_mean_l2))
                and np.all(np.isfinite(l02_variation_l2)), "L02 affine moments")
        budget.check("L02 descriptor")

        l14_result = json.loads(PINS["l14_result"][0].read_bytes())
        require(l14_result["status"] == "PASS_ACCEPTED_HYBRID_L14_P1_CURRENT_ENERGY_READBACK", "L14 result status")
        require(l14_result["accepted_field"]["sha256"] == PINS["accepted_field"][1], "L14 accepted field identity")
        require(l14_result["driver_sha256"] == PINS["l14_driver"][1], "L14 driver identity")
        l14_current = load_npz(PINS["l14_current"][0])
        l14_mesh = load_npz(PINS["l14_mesh"][0])
        l14_ids = np.arange(len(l14_mesh["triangles"]), dtype=np.int64)
        l14_density = np.asarray(l14_current["sheet_current_density_a_per_m"], dtype=np.complex128)
        l14_joule = np.asarray(l14_current["triangle_joule_w"], dtype=np.float64)
        l14_bilinear = np.asarray(l14_current["triangle_bilinear_va"], dtype=np.complex128)
        require(l14_density.shape == (214_873, 2) and l14_joule.shape == l14_bilinear.shape == (214_873,), "L14 current shapes")
        require(np.all(np.isfinite(l14_density)) and np.all(np.isfinite(l14_joule)) and np.all(np.isfinite(l14_bilinear)), "L14 currents finite")
        l14_joule_error = abs(float(l14_joule.sum()) - float(l14_result["gradient_joule_w"]))
        require(l14_joule_error <= max(1e-15, abs(float(l14_result["gradient_joule_w"])) * 1e-9), "L14 integrated Joule receipt")
        l14_vertices = triangle_vertices(l14_mesh, l14_ids)
        budget.check("L14 descriptor")

        l25_step = json.loads(PINS["l25_step_result"][0].read_bytes())
        require(l25_step["artifacts"]["mesh"]["sha256"] == PINS["l25_mesh"][1] and l25_step["artifacts"]["topology"]["sha256"] == PINS["l25_topology"][1], "L25 step06 mesh/topology receipt")
        l25_mesh = load_npz(PINS["l25_mesh"][0])
        l25_topology = load_npz(PINS["l25_topology"][0])
        l25_step06_ids = np.asarray(l25_topology["free_triangle_indices"], dtype=np.int64)
        l25_facets = np.asarray(l25_topology["local_facet_branch_index"], dtype=np.int64)
        l25_signs = np.asarray(l25_topology["local_outward_flux_sign"], dtype=np.int8)
        require(l25_step06_ids.shape == (579_177,) and l25_facets.shape == l25_signs.shape == (579_177, 3), "L25 RT0 shapes")
        l25_active_facets = l25_facets >= 0
        require(np.all(l25_facets >= -1) and np.all(l25_facets[l25_active_facets] < len(l25_q))
                and np.all(abs(l25_signs[l25_active_facets]) == 1) and np.all(l25_signs[~l25_active_facets] == 0),
                "L25 RT0 map")
        require("original_triangle_index" in l25_mesh, "L25 ancestry missing")
        l25_original_ids = np.asarray(l25_mesh["original_triangle_index"], dtype=np.int64)[l25_step06_ids]
        l25_vertices = triangle_vertices(l25_mesh, l25_step06_ids)
        l25_local_q = np.zeros(l25_facets.shape, dtype=np.complex128)
        l25_local_q[l25_active_facets] = (l25_signs[l25_active_facets]
                                            * l25_q[l25_facets[l25_active_facets]])
        l25_area, l25_average, l25_alpha, l25_mean_l2, l25_variation_l2 = moments(
            l25_vertices * 1e-6, l25_local_q)
        require(np.all(np.isfinite(l25_area)) and np.all(np.isfinite(l25_average))
                and np.all(np.isfinite(l25_alpha)) and np.all(np.isfinite(l25_mean_l2))
                and np.all(np.isfinite(l25_variation_l2)), "L25 affine moments")
        budget.check("L25 descriptor")

        first_post_result = json.loads(PINS["first_post_result"][0].read_bytes())
        require(first_post_result["status"] == "COMPLETED_ACCEPTED_CURRENT_FIRST_POST_JOIN", "first-post result status")
        require(first_post_result["driver"]["sha256"] == PINS["first_post_driver"][1] and first_post_result["artifact"]["sha256"] == PINS["first_post"][1], "first-post receipts")
        first_post = load_npz(PINS["first_post"][0])
        post_rows = np.asarray(first_post["final_active_current_row"], dtype=np.int64)
        post_native_rows = np.asarray(first_post["native_active_current_row"], dtype=np.int64)
        post_xy_pm = np.asarray(first_post["source_xy_pm"], dtype=np.int64)
        post_current = np.asarray(first_post["port_outward_signed_current_a"], dtype=np.complex128)
        post_sign = np.asarray(first_post["port_outward_to_raw_top_to_l02_sign"], dtype=np.int8)
        post_final_to_raw = np.asarray(first_post["final_first_to_second_raw_top_to_l02_sign"], dtype=np.int8)
        require(post_rows.shape == post_native_rows.shape == post_current.shape == post_sign.shape == post_final_to_raw.shape == (26_790,) and post_xy_pm.shape == (26_790, 2), "first-post shapes")
        require(len(np.unique(post_rows)) == len(np.unique(post_native_rows)) == 26_790 and np.all(np.isfinite(post_current)) and np.all(post_sign == 1), "first-post one-to-one")
        roles = np.asarray(first_post["role"])
        composite = np.asarray(first_post["composite_port_leg1"], dtype=bool)
        legs = np.asarray(first_post["final_split_leg"], dtype=np.int8)
        require(np.count_nonzero(roles == "power") == 978 and np.count_nonzero(roles == "ground") == 25_812, "first-post role counts")
        require(np.count_nonzero(composite) == 20 and np.all(legs[composite] == 1) and np.count_nonzero(legs == 0) == 0, "first-post composite leg policy")
        post_finite_current = np.asarray(first_post["finite_current_first_to_second_a"], dtype=np.complex128)
        post_raw_top_to_l02_current = post_final_to_raw * post_finite_current
        post_orientation_error = float(np.max(abs(post_raw_top_to_l02_current - post_current)))
        require(post_orientation_error <= 3e-14, "first-post raw TOP-to-L02 current orientation")
        budget.check("first-post descriptor")

        composite_doc = load_npz(PINS["composite_delta"][0])
        composite_rows = json.loads(composite_doc["records_json_utf8"].tobytes())
        segments = [segment for row in composite_rows for leg in row["final_legs"] for segment in leg["ordered_source_segments"]]
        leg0_vias = sum(segment["kind"] == "via" for row in composite_rows for segment in row["final_legs"][0]["ordered_source_segments"])
        trace_segments = sum(segment["kind"] == "trace" for segment in segments)
        require(len(composite_rows) == 20 and leg0_vias == 73 and trace_segments == 5, "composite deeper coverage")

        combined = load_npz(PINS["combined_map"][0])
        final_rows = np.asarray(combined["final_finite_native_active_row"], dtype=np.int64)
        final_legs = np.asarray(combined["final_finite_split_leg"], dtype=np.int8)
        require(final_rows.shape == final_legs.shape == (1_692_409,), "combined finite row count")
        require(np.count_nonzero(final_legs == 0) == np.count_nonzero(final_legs == 1) == 20, "combined split-leg counts")
        non_port_final_rows = len(final_rows) - len(post_rows)
        require(non_port_final_rows == 1_665_619, "non-port final finite coverage")

        metadata = {
            "status": "SOURCE_DESCRIPTOR_ONLY_NO_MAGNETIC_ACTION",
            "coordinate_unit": "um; triangle vertices and slab values are um",
            "affine_current_coordinate_unit": "moments() receives vertices converted to m; J=Jmean+alpha*(r_m-centroid_m), Jmean A/m and alpha A/m^2",
            "stack_coordinate_convention": stack["stackup"]["coordinate_convention"],
            "lateral_slabs_um": {"l02": [55.0, 75.0], "l14": [655.0, 675.0], "l25": [1511.0, 1543.0]},
            "first_post": {"kind": "oriented_line_descriptor", "endpoint_center_z_um": [12.5, 65.0], "barrel_or_self_geometry": "NOT_SAVED", "magnetic_self_authorized": False},
            "ownership": {"accepted_operator_contains_sheet_magnetic_action": False, "existing_scalar_finite_r_l_remains_owned": True, "future_matching_vertical_self": "PROHIBITED_UNTIL_EXISTING_SCALAR_J_OMEGA_L_IS_EXPLICITLY_REPLACED", "descriptor_authorizes_added_self": False, "cross_energy": "NO_CROSS_BLOCK_EXISTS_AND_NO_PSD_GATE_IS_IMPOSED", "action_authorization": "NONE"},
            "coverage": {"final_finite_rows_total": 1692409, "vertical_first_post_rows_covered": 26790, "final_finite_rows_uncovered": non_port_final_rows, "ground_outside_selected_included": 24834, "composite_port_leg1_records": 20, "composite_leg0_vias_outside_vertical_descriptor": 73, "composite_trace_segments_outside_vertical_descriptor": 5},
        }
        arrays = {
            "metadata_json_utf8": np.frombuffer(json.dumps(metadata, sort_keys=True).encode("utf-8"), dtype=np.uint8),
            "l02_original_triangle_index": l02_ids, "l02_triangle_vertices_um": l02_vertices,
            "l02_slab_z_um": np.asarray([55.0, 75.0]), "l02_local_facet_branch_index": l02_facets,
            "l02_local_outward_flux_sign": l02_signs, "l02_branch_current_a": l02_branch_q,
            "l02_area_m2": l02_area, "l02_average_current_a_per_m": l02_average,
            "l02_affine_coefficient_a_per_m2": l02_alpha,
            "l02_mean_current_l2_squared_a2": l02_mean_l2,
            "l02_affine_variation_l2_squared_a2": l02_variation_l2,
            "l14_original_triangle_index": l14_ids, "l14_triangle_vertices_um": l14_vertices,
            "l14_slab_z_um": np.asarray([655.0, 675.0]), "l14_sheet_current_density_a_per_m": l14_density,
            "l14_triangle_joule_w": l14_joule, "l14_triangle_bilinear_va": l14_bilinear,
            "l25_step06_triangle_index": l25_step06_ids, "l25_original_triangle_index": l25_original_ids,
            "l25_triangle_vertices_um": l25_vertices,
            "l25_slab_z_um": np.asarray([1511.0, 1543.0]), "l25_local_facet_branch_index": l25_facets,
            "l25_local_outward_flux_sign": l25_signs, "l25_branch_current_a": l25_q,
            "l25_area_m2": l25_area, "l25_average_current_a_per_m": l25_average,
            "l25_affine_coefficient_a_per_m2": l25_alpha,
            "l25_mean_current_l2_squared_a2": l25_mean_l2,
            "l25_affine_variation_l2_squared_a2": l25_variation_l2,
            "first_post_final_active_current_row": post_rows, "first_post_native_active_current_row": post_native_rows,
            "first_post_original_finite_index": np.asarray(first_post["original_finite_index"], dtype=np.int64),
            "first_post_role": np.asarray(first_post["role"]), "first_post_via_id": np.asarray(first_post["via_id"]),
            "first_post_source_via_owner_id": np.asarray(first_post["source_via_owner_id"]),
            "first_post_padstack_id": np.asarray(first_post["padstack_id"]),
            "first_post_top_raw_node_id": np.asarray(first_post["top_raw_node_id"]), "first_post_lower_raw_node_id": np.asarray(first_post["lower_raw_node_id"]),
            "first_post_xy_um": post_xy_pm.astype(np.float64) / 1e6,
            "first_post_endpoint_center_z_um": np.broadcast_to(np.asarray([12.5, 65.0]), (26_790, 2)).copy(),
            "first_post_port_outward_to_raw_top_to_l02_sign": post_sign,
            "first_post_final_first_to_second_raw_top_to_l02_sign": post_final_to_raw,
            "first_post_finite_current_first_to_second_a": post_finite_current,
            "first_post_current_raw_top_to_l02_a": post_raw_top_to_l02_current,
            "first_post_port_outward_signed_current_a": post_current,
            "first_post_final_split_leg": legs, "first_post_composite_port_leg1": composite,
        }
        artifact = output / "combined-magnetic-source-descriptor.npz"
        atomic_npz(artifact, arrays)
        budget.check("descriptor write")
        gates = {
            "all_hard_pins": True, "accepted_fd28_chain": True, "accepted_operator_has_no_sheet_magnetic_action": True,
            "layer_slabs_and_units": True, "l02_branch_to_local_reconstruction": l02_local_error <= 3e-14,
            "l14_integrated_joule_receipt": True, "l25_rt0_map": True,
            "first_post_current_source_one_to_one": True, "first_post_composite_leg1_only": True,
            "coverage_counts": True, "descriptor_authorizes_no_magnetic_action_or_self": True,
        }
        result = {"program": PROGRAM, "version": VERSION, "status": "SOURCE_DESCRIPTOR_ONLY_NO_MAGNETIC_ACTION",
                  "driver": receipt(frozen), "artifact": receipt(artifact),
                  "inputs": {name: receipt(path) for name, (path, _digest) in PINS.items()},
                  "counts": {"l02_triangles": int(len(l02_ids)), "l02_rt0_branches": int(len(l02_branch_q)),
                             "l14_triangles": int(len(l14_ids)), "l25_triangles": int(len(l25_step06_ids)),
                             "l25_rt0_branches": int(len(l25_q)), "first_posts": int(len(post_rows)),
                             "ground_outside_selected_included": 24834, "composite_leg1": 20,
                             "composite_leg0_vias_outside": leg0_vias, "composite_trace_segments_outside": trace_segments,
                             "other_final_finite_rows_outside": int(non_port_final_rows)},
                  "metrics": {"l02_branch_to_local_max_abs_a": l02_local_error, "l14_integrated_joule_w": float(l14_joule.sum()),
                              "l14_joule_receipt_difference_w": float(l14_joule_error),
                              "first_post_raw_top_to_l02_orientation_max_abs_a": post_orientation_error},
                  "interaction_ownership": metadata, "gates": gates, "budget": budget.document(),
                  "scope": "Accepted current/geometry source descriptor only. It computes no magnetic, Green, FMM, reference, remesh, factorization or solve action and grants no added self or mutual term."}
        atomic_json(output / "result.json", result)
        return result
    except BaseException as error:
        atomic_json(output / "failure.json", {"program": PROGRAM, "version": VERSION,
                    "status": "STOP_SOURCE_DESCRIPTOR_ONLY_NO_MAGNETIC_ACTION", "error_type": type(error).__name__,
                    "error": str(error), "driver": receipt(frozen), "budget": budget.document()})
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=R / OUTPUT_NAME)
    args = parser.parse_args()
    result = run(args.output.resolve())
    print(json.dumps({"status": result["status"], "artifact": result["artifact"], "counts": result["counts"]}, sort_keys=True))


if __name__ == "__main__":
    main()
