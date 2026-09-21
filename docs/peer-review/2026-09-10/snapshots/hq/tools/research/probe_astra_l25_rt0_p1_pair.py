"""Bounded L25 RT0/P1 pair probe (resistance only).

The actual path performs one gauged RT0 LU followed by one gauged P1 LU.  The
two factors are released in between.  It only uses the accepted saved sheet
operators and records a conditional local resistance bound; it does not add
GC, native-via, magnetic, return, board, or PowerSI operators.
"""

from __future__ import annotations

import argparse
import ctypes
import gc
import hashlib
import json
import math
import os
import time
from ctypes import wintypes
from pathlib import Path
from threading import Event, Thread
from time import monotonic

import numpy as np
import scipy.sparse as sp
import scipy.sparse.linalg as spla


PROGRAM = "SPD Decap PI Evaluator v0.23.1"
ROOT = Path(__file__).resolve().parents[2]
RESEARCH = ROOT / "outputs" / "research"
RT0_DIR = RESEARCH / "astra-l25-rt0-resistance-01"
TOPO_DIR = RESEARCH / "astra-l25-dual-cell-topology-01"
BIND_DIR = RESEARCH / "astra-l25-dual-cell-source-binding-01"
DRIVE_DIR = RESEARCH / "astra-l25-sheet-drive-02"
MESH_DIR = RESEARCH / "astra-l25-sheet-mesh-preflight-01"

PINS = {
    "rt0_result": (RT0_DIR / "result.json", "1bc08380e43688c232036d4ab9ed20344bd3579965637f5014fb6e91e134ddd2"),
    "rt0_npz": (RT0_DIR / "rt0-resistance.npz", "d5ef6d1e729ee429aa75c97c15f74888e1a2cfd72f8073643e8b31d9dcda83bc"),
    "topology_result": (TOPO_DIR / "result.json", "d24d13516b0a721565cdf16e2c678b2e74c03b70f8edea9557926b4599542d31"),
    "topology_npz": (TOPO_DIR / "dual-cell-topology.npz", "eb33117f14a3e5725e9efeafd2a0a083961ad83abaf022b7fe78b94bc8b5265f"),
    "binding_result": (BIND_DIR / "result.json", "874f36da7dc906171d7761510902432752a3164b4ec055835064e389c4d427e6"),
    "binding_npz": (BIND_DIR / "native-source-binding.npz", "c9d41f1e2b870dd854affa1db2c31bb86f24c472866fcd2125f313cb29981c6a"),
    "drive_result": (DRIVE_DIR / "sheet-electrode-drive.json", "ada249febeb03dd48b57bc54294d5360477534d33c4c8586fdf089eb998906b5"),
    "drive_npz": (DRIVE_DIR / "sheet-electrode-drive.npz", "dd1e927ba48e86be331e9c5e0bb0a966dcee77f8bf47052aa94660654065a266"),
    "mesh_npz": (MESH_DIR / "mesh-stiffness.npz", "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"),
}

SIGMA_S_PER_M = 59.59e6
THICKNESS_M = 32e-6
SHEET_CONDUCTANCE_S = SIGMA_S_PER_M * THICKNESS_M
PAIR = (0, 1)
GAUGE_ELECTRODE = 2
MAX_RUNTIME_S = 240.0
MAX_RSS_BYTES = 24 * 2**30
ABS_RESIDUAL_TOL_A = 1e-8
REL_TOL = 1e-8


class _MemoryCounters(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("page_fault_count", ctypes.c_uint32),
        ("peak_working_set", ctypes.c_size_t),
        ("working_set", ctypes.c_size_t),
        ("quota_peak_paged_pool", ctypes.c_size_t),
        ("quota_paged_pool", ctypes.c_size_t),
        ("quota_peak_nonpaged_pool", ctypes.c_size_t),
        ("quota_nonpaged_pool", ctypes.c_size_t),
        ("pagefile_usage", ctypes.c_size_t),
        ("peak_pagefile_usage", ctypes.c_size_t),
        ("private_usage", ctypes.c_size_t),
    ]


def _memory_bytes() -> tuple[int, int]:
    counters = _MemoryCounters()
    counters.cb = ctypes.sizeof(counters)
    process = ctypes.windll.kernel32.GetCurrentProcess
    process.argtypes = ()
    process.restype = wintypes.HANDLE
    getter = ctypes.windll.psapi.GetProcessMemoryInfo
    getter.argtypes = (wintypes.HANDLE, ctypes.POINTER(_MemoryCounters), wintypes.DWORD)
    getter.restype = wintypes.BOOL
    if not getter(process(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(counters.working_set), int(counters.private_usage)


class _Budget:
    def __init__(self, progress_path: Path) -> None:
        self.started = monotonic()
        self.progress_path = progress_path
        self.stop = Event()
        self.reason: str | None = None
        self.peak_working_set = 0
        self.peak_private = 0

    def elapsed(self) -> float:
        return monotonic() - self.started

    def sample(self) -> tuple[int, int]:
        working, private = _memory_bytes()
        self.peak_working_set = max(self.peak_working_set, working)
        self.peak_private = max(self.peak_private, private)
        return working, private

    def _refresh(self) -> str | None:
        working, private = self.sample()
        if self.reason is None:
            if self.elapsed() > MAX_RUNTIME_S:
                self.reason = "MAX_RUNTIME_EXCEEDED"
            elif max(working, private) > MAX_RSS_BYTES:
                self.reason = "MAX_RSS_EXCEEDED"
        if self.reason is not None:
            self.stop.set()
        return self.reason

    def emit(self, stage: str, **details: object) -> None:
        working, private = self.sample()
        row = {"program": PROGRAM, "elapsed_s": self.elapsed(), "stage": stage,
               "working_set_bytes": working, "private_bytes": private, **details}
        with self.progress_path.open("a", encoding="utf-8", newline="\n") as handle:
            handle.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
        print(f"[{row['elapsed_s']:.1f}s] {stage} WS={working / 2**30:.2f}GiB", flush=True)

    def check(self, stage: str) -> None:
        reason = self._refresh()
        if reason is not None:
            self.emit("budget_stop", reason=reason, failed_stage=stage)
            raise RuntimeError(reason)

    def start_watchdog(self) -> Thread:
        def watch() -> None:
            while not self.stop.wait(0.5):
                try:
                    reason = self._refresh()
                    if reason is not None:
                        self.emit("watchdog_stop", reason=reason)
                        os._exit(124)
                except BaseException:
                    os._exit(125)

        thread = Thread(target=watch, name="astra-l25-pair-budget", daemon=True)
        thread.start()
        return thread


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _read_json_pinned(name: str) -> dict:
    path, expected = PINS[name]
    if _sha256(path) != expected:
        raise ValueError(f"input hash mismatch: {name}")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"input JSON is not an object: {name}")
    return value


def _read_npz_pinned(name: str) -> dict[str, np.ndarray]:
    path, expected = PINS[name]
    if _sha256(path) != expected:
        raise ValueError(f"input hash mismatch: {name}")
    with np.load(path, allow_pickle=False) as archive:
        return {key: np.asarray(archive[key]) for key in archive.files}


def _write_json_exclusive(path: Path, value: dict) -> None:
    payload = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    with path.open("x", encoding="utf-8", newline="\n") as handle:
        handle.write(payload)


def _write_npz_exclusive(path: Path, **arrays: np.ndarray) -> None:
    with path.open("xb") as handle:
        np.savez_compressed(handle, **arrays)


def _file_receipt(path: Path) -> dict:
    return {"path": path.name, "bytes": int(path.stat().st_size), "sha256": _sha256(path)}


def _freeze_output_root(path: Path) -> None:
    if path.exists():
        raise FileExistsError(f"output root exists: {path}")
    path.mkdir(parents=True)
    with (path / "driver-at-run.py").open("xb") as handle:
        handle.write(Path(__file__).read_bytes())
    (path / "progress.jsonl").open("x", encoding="utf-8", newline="\n").close()


def _check_result_contract(rt0: dict, topo: dict, binding: dict, drive: dict) -> None:
    if rt0.get("program") != "SPD Decap PI Evaluator" or rt0.get("version") != "0.23.1":
        raise ValueError("RT0 program/version pin mismatch")
    if rt0.get("status") != "COMPLETED_L25_RT0_RESISTANCE_SHADOW":
        raise ValueError("RT0 result is not accepted")
    if topo.get("status") != "COMPLETED_SOURCE_L25_DUAL_CELL_TOPOLOGY":
        raise ValueError("topology result is not accepted")
    if binding.get("status") != "COMPLETED_L25_DUAL_CELL_NATIVE_SOURCE_BINDING":
        raise ValueError("source binding result is not accepted")
    if drive.get("status") != "COMPLETED_L25_SHEET_VIA_DRIVE_CORRECTED_SAVED_FIELD_KCL":
        raise ValueError("drive02 result is not accepted")
    coverage = binding.get("native_via_coverage", {})
    if int(topo["electrode_node_count"]) != 175 or int(coverage.get("target_incident_count", -1)) != 350:
        raise ValueError("source topology count mismatch")
    if int(drive["native_via_count"]) != 350 or int(drive["coincident_contact_count"]) != 175:
        raise ValueError("drive02 count mismatch")


def _pair_metadata(mesh: dict, drive: dict, drive_npz: dict) -> list[dict]:
    contacts = json.loads(bytes(mesh["contacts_json_utf8"].tolist()).decode("utf-8"))
    if not isinstance(contacts, list) or len(contacts) != 175:
        raise ValueError("mesh contact contract is not 175 entries")
    mapping = np.asarray(drive_npz["full_to_contracted"], dtype=np.int64)
    nodes = np.asarray(mesh["contact_node_indices"], dtype=np.int64)
    ptr = np.asarray(mesh["contact_node_indptr"], dtype=np.int64)
    if ptr.size != 176 or nodes.size == 0:
        raise ValueError("invalid mesh contact index contract")
    bindings = sorted(drive["contact_binding"], key=lambda row: int(row["mesh_electrode_ordinal"]))
    if [int(row["mesh_electrode_ordinal"]) for row in bindings] != list(range(175)):
        raise ValueError("drive02 electrode ordinals are not exact")
    result: list[dict] = []
    for ordinal in range(175):
        row = bindings[ordinal]
        start, stop = int(ptr[ordinal]), int(ptr[ordinal + 1])
        contracted = np.unique(mapping[nodes[start:stop]])
        if contracted.size != 1 or int(contracted[0]) != ordinal:
            raise ValueError(f"contact {ordinal} does not contract to its electrode node")
        source = contacts[ordinal]
        owner = str(row["owner_id"])
        if source.get("owner_id") != owner:
            raise ValueError(f"contact owner mismatch: {ordinal}")
        xy = [float(v) for v in row["xy_um"]]
        if len(xy) != 2 or not all(math.isfinite(v) for v in xy):
            raise ValueError(f"invalid source contact coordinate: {ordinal}")
        if ordinal in PAIR:
            result.append({"electrode_ordinal": ordinal, "contracted_node": int(contracted[0]),
                           "xy_um": xy, "owner_id": owner, "via_ids": list(row["via_ids"]),
                           "contact_id": source.get("contact_id"),
                           "footprint_sha256": source.get("footprint_sha256"),
                           "mesh_contact_node_count": stop - start})
    return result


def _structural_preflight(rt0: dict, topo: dict, drive: dict, topo_npz: dict) -> dict:
    branches = int(rt0["counts"]["branch_count"])
    free_cells = int(rt0["counts"]["free_cell_count"])
    potentials = int(topo["potential_node_count"])
    r_nnz = int(rt0["counts"]["sparse_nnz"])
    p1_nodes = int(drive["contracted_node_count"])
    p1_nnz = int(drive["conductance_nnz"])
    local_branch = np.asarray(topo_npz["local_facet_branch_index"], dtype=np.int64)
    natural_boundary_facets = int(np.count_nonzero(local_branch < 0))
    active_support = int(np.unique(local_branch[local_branch >= 0]).size)
    return {
        "rt0": {"free_cell_count": free_cells, "branch_count": branches,
                 "potential_node_count": potentials,
                 "mixed_unknown_count_with_one_gauge": branches + potentials + 1,
                 "mixed_structural_nnz_with_one_gauge": r_nnz + 4 * branches + 2,
                 "natural_boundary_omitted_local_facets": natural_boundary_facets,
                 "active_branch_support_count": active_support,
                 "omitted_boundary_current_is_zero_by_topology": True},
        "p1": {"contracted_node_count": p1_nodes, "conductance_nnz": p1_nnz,
               "gauged_unknown_count": p1_nodes + 1},
        "resource_policy": {"max_runtime_s": MAX_RUNTIME_S, "max_rss_gib": 24.0,
                             "sequential_lu": True, "factor_peak_rss_bytes": None,
                             "factor_peak_rss_known_before_run": False,
                             "factor_peak_is_measured_by_watchdog": True,
                             "no_fill_or_flop_estimate": True},
    }


def _incidence(first: np.ndarray, second: np.ndarray, node_count: int) -> sp.csc_matrix:
    branch_count = int(first.size)
    rows = np.concatenate((first, second))
    cols = np.concatenate((np.arange(branch_count), np.arange(branch_count)))
    data = np.concatenate((np.ones(branch_count), -np.ones(branch_count)))
    return sp.csc_matrix((data, (rows, cols)), shape=(node_count, branch_count))


def _with_gauge(matrix: sp.spmatrix, gauge_node: int) -> sp.csc_matrix:
    c = sp.csc_matrix(([1.0], ([int(gauge_node)], [0])), shape=(matrix.shape[0], 1))
    return sp.bmat([[matrix, c], [c.T, sp.csc_matrix((1, 1))]], format="csc")


def _rt0_solve(inputs: dict, output_root: Path, budget: _Budget) -> tuple[dict, np.ndarray, np.ndarray]:
    budget.emit("rt0_load_matrix")
    r_npz = inputs["rt0_npz"]
    topo_npz = inputs["topology_npz"]
    r = sp.csc_matrix((r_npz["r_data"], r_npz["r_indices"], r_npz["r_indptr"]), shape=tuple(r_npz["r_shape"]))
    first = np.asarray(topo_npz["branch_first_node"], dtype=np.int64)
    second = np.asarray(topo_npz["branch_second_node"], dtype=np.int64)
    p_count = int(inputs["topology"]["potential_node_count"])
    free_cells = int(inputs["topology"]["free_cell_count"])
    bmat = _incidence(first, second, p_count)
    mixed = sp.bmat([[r, -bmat.T], [bmat, None]], format="csc")
    gauge_p = free_cells + GAUGE_ELECTRODE
    augmented = _with_gauge(mixed, r.shape[0] + gauge_p)
    j = np.zeros(p_count, dtype=np.float64)
    j[free_cells + PAIR[0]] = 1.0
    j[free_cells + PAIR[1]] = -1.0
    rhs = np.zeros(augmented.shape[0], dtype=np.float64)
    rhs[r.shape[0] : r.shape[0] + p_count] = j
    budget.emit("rt0_factor_start", unknown_count=augmented.shape[0], structural_nnz=int(augmented.nnz))
    factor = spla.splu(augmented)
    budget.check("rt0_factor")
    solution = factor.solve(rhs)
    budget.check("rt0_solve")
    q = np.asarray(solution[: r.shape[0]], dtype=np.float64)
    v = np.asarray(solution[r.shape[0] : r.shape[0] + p_count], dtype=np.float64)
    gauge_multiplier = float(solution[-1])
    _write_npz_exclusive(output_root / "rt0-field.npz", branch_current_a=q, potential_v=v, source_j_a=j)
    budget.emit("rt0_field_saved", field_path="rt0-field.npz")
    constitutive = np.asarray(r @ q - bmat.T @ v)
    flux = np.asarray(bmat @ q)
    kcl = flux - j
    electrode_flux = flux[free_cells : free_cells + 175]
    electrode_residual = kcl[free_cells : free_cells + 175]
    electrode_rhs = j[free_cells : free_cells + 175]
    metrics = {
        "gauge_electrode": GAUGE_ELECTRODE,
        "gauge_multiplier": gauge_multiplier,
        "constitutive_residual_max_ohm_a": float(np.max(np.abs(constitutive))),
        "free_cell_kcl_residual_max_a": float(np.max(np.abs(kcl[:free_cells]))),
        "electrode_net_flux_a": [float(x) for x in electrode_flux],
        "electrode_rhs_residuals_a": [float(x) for x in electrode_residual],
        "all_175_electrode_rhs_zero_except_pair": bool(np.all(electrode_rhs[np.logical_not(np.isin(np.arange(175), PAIR))] == 0.0) and electrode_rhs[PAIR[0]] == 1.0 and electrode_rhs[PAIR[1]] == -1.0),
        "total_flux_a": float(np.sum(flux)),
        "source_total_balance_a": float(np.sum(j)),
        "total_balance_residual_a": float(np.sum(kcl)),
        "pair_voltage_ohm": float(v[free_cells + PAIR[0]] - v[free_cells + PAIR[1]]),
        "energy_qrq_ohm": float(q @ (r @ q)),
        "field_finite": bool(np.all(np.isfinite(q)) and np.all(np.isfinite(v))),
    }
    metrics["energy_pair_relative_error"] = abs(metrics["energy_qrq_ohm"] - metrics["pair_voltage_ohm"]) / max(abs(metrics["pair_voltage_ohm"]), np.finfo(float).tiny)
    metrics["physical_residual_max_a"] = float(np.max(np.abs(kcl)))
    metrics["physical_residual_gate"] = bool(metrics["constitutive_residual_max_ohm_a"] <= ABS_RESIDUAL_TOL_A and metrics["physical_residual_max_a"] <= ABS_RESIDUAL_TOL_A and abs(metrics["gauge_multiplier"]) <= ABS_RESIDUAL_TOL_A and abs(metrics["total_balance_residual_a"]) <= ABS_RESIDUAL_TOL_A and metrics["field_finite"])
    del factor, augmented, mixed, bmat, r
    gc.collect()
    budget.emit("rt0_released")
    return metrics, q, v


def _p1_solve(inputs: dict, output_root: Path, budget: _Budget) -> tuple[dict, np.ndarray]:
    budget.emit("p1_load_matrix")
    z = inputs["drive_npz"]
    g = sp.csc_matrix((z["conductance_data"], z["conductance_indices"], z["conductance_indptr"]), shape=tuple(z["conductance_shape"]))
    node_count = g.shape[0]
    j = np.zeros(node_count, dtype=np.float64)
    j[PAIR[0]] = 1.0
    j[PAIR[1]] = -1.0
    augmented = _with_gauge(g, GAUGE_ELECTRODE)
    rhs = np.zeros(augmented.shape[0], dtype=np.float64)
    rhs[:node_count] = j
    budget.emit("p1_factor_start", unknown_count=augmented.shape[0], structural_nnz=int(augmented.nnz))
    factor = spla.splu(augmented)
    budget.check("p1_factor")
    solution = factor.solve(rhs)
    budget.check("p1_solve")
    v = np.asarray(solution[:node_count], dtype=np.float64)
    gauge_multiplier = float(solution[-1])
    _write_npz_exclusive(output_root / "p1-field.npz", contracted_voltage_v=v, source_j_a=j)
    budget.emit("p1_field_saved", field_path="p1-field.npz")
    flux = np.asarray(g @ v)
    residual = flux - j
    electrode_flux = flux[:175]
    electrode_residual = residual[:175]
    metrics = {
        "gauge_electrode": GAUGE_ELECTRODE,
        "gauge_multiplier": gauge_multiplier,
        "electrode_net_flux_a": [float(x) for x in electrode_flux],
        "electrode_rhs_residuals_a": [float(x) for x in electrode_residual],
        "non_electrode_kcl_residual_max_a": float(np.max(np.abs(residual[175:]))),
        "total_flux_a": float(np.sum(flux)),
        "source_total_balance_a": float(np.sum(j)),
        "total_balance_residual_a": float(np.sum(residual)),
        "pair_voltage_ohm": float(v[PAIR[0]] - v[PAIR[1]]),
        "energy_vgv_ohm": float(v @ (g @ v)),
        "field_finite": bool(np.all(np.isfinite(v))),
    }
    metrics["energy_pair_relative_error"] = abs(metrics["energy_vgv_ohm"] - metrics["pair_voltage_ohm"]) / max(abs(metrics["pair_voltage_ohm"]), np.finfo(float).tiny)
    metrics["physical_residual_max_a"] = float(np.max(np.abs(residual)))
    metrics["physical_residual_gate"] = bool(metrics["physical_residual_max_a"] <= ABS_RESIDUAL_TOL_A and abs(metrics["gauge_multiplier"]) <= ABS_RESIDUAL_TOL_A and abs(metrics["total_balance_residual_a"]) <= ABS_RESIDUAL_TOL_A and metrics["field_finite"])
    del factor, augmented, g
    gc.collect()
    budget.emit("p1_released")
    return metrics, v


def _triangle_metrics(vertices_m: np.ndarray, values: np.ndarray, q_local: np.ndarray, conductance_s: float) -> dict:
    e = np.asarray([vertices_m[1] - vertices_m[0], vertices_m[2] - vertices_m[0]], dtype=np.float64)
    signed_det = float(np.linalg.det(e))
    area = abs(signed_det) / 2.0
    if not math.isfinite(signed_det) or area <= 0.0:
        raise ValueError("degenerate triangle")
    grad = np.linalg.solve(e, np.asarray([values[1] - values[0], values[2] - values[0]], dtype=np.float64))
    bary = np.asarray([[2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0], [1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0], [1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0]])
    samples = bary @ vertices_m
    basis = (samples[:, None, :] - vertices_m[None, :, :]) / (2.0 * area)
    current = np.einsum("f,sfc->sc", q_local, basis)
    j_energy = area / (3.0 * conductance_s) * float(np.sum(current * current))
    cross = area / 3.0 * float(np.sum(current * grad[None, :]))
    p1_energy = area * conductance_s * float(grad @ grad)
    return {"signed_det": signed_det, "area_m2": area, "gradient_v_per_m": grad,
            "rt0_energy_ohm": j_energy, "cross_integral_ohm": cross,
            "p1_energy_ohm": p1_energy, "gap_ohm": j_energy + 2.0 * cross + p1_energy}


def _gap_integral(mesh: dict, topo: dict, mapping: np.ndarray, p1_v: np.ndarray, rt0_i: np.ndarray, conductance_s: float = SHEET_CONDUCTANCE_S) -> dict:
    xy = np.asarray(mesh["node_xy_um"], dtype=np.float64) * 1e-6
    triangles = np.asarray(mesh["triangles"], dtype=np.int64)
    free = np.asarray(topo["free_triangle_indices"], dtype=np.int64)
    local_branch = np.asarray(topo["local_facet_branch_index"], dtype=np.int64)
    local_sign = np.asarray(topo["local_outward_flux_sign"], dtype=np.float64)
    gap = cross = rt_energy = p1_energy = 0.0
    signed_positive = signed_negative = 0
    chunk = 8192
    g = float(conductance_s)
    bary = np.asarray([[2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0], [1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0], [1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0]])
    for start in range(0, free.size, chunk):
        stop = min(start + chunk, free.size)
        tri = triangles[free[start:stop]]
        pos = xy[tri]
        e = np.stack((pos[:, 1] - pos[:, 0], pos[:, 2] - pos[:, 0]), axis=1)
        signed_det = e[:, 0, 0] * e[:, 1, 1] - e[:, 0, 1] * e[:, 1, 0]
        area = np.abs(signed_det) / 2.0
        if np.any(~np.isfinite(signed_det)) or np.any(area <= 0.0):
            raise ValueError("degenerate free triangle in gap integral")
        signed_positive += int(np.count_nonzero(signed_det > 0.0))
        signed_negative += int(np.count_nonzero(signed_det < 0.0))
        values = p1_v[np.asarray(mapping[tri], dtype=np.int64)]
        grad_v = np.linalg.solve(e, (values[:, 1:] - values[:, :1])[..., None])[:, :, 0]
        samples = np.einsum("sf,nfc->nsc", bary, pos)
        basis = (samples[:, :, None, :] - pos[:, None, :, :]) / (2.0 * area[:, None, None, None])
        branch_ids = local_branch[start:stop]
        qlocal = np.zeros(branch_ids.shape, dtype=np.float64)
        valid = branch_ids >= 0
        qlocal[valid] = rt0_i[branch_ids[valid]] * local_sign[start:stop][valid]
        current = np.einsum("nf,nsfc->nsc", qlocal, basis)
        rt_energy += float(np.sum(area[:, None] / (3.0 * g) * np.sum(current * current, axis=2)))
        cross += float(np.sum(area[:, None] / 3.0 * np.sum(current * grad_v[:, None, :], axis=2)))
        p1_energy += float(np.sum(area * g * np.sum(grad_v * grad_v, axis=1)))
        gap += float(np.sum(area[:, None] / (3.0 * g) * np.sum((current + g * grad_v[:, None, :]) ** 2, axis=2)))
    return {"rt0_energy_ohm": rt_energy, "cross_integral_ohm": cross,
            "p1_gradient_energy_ohm": p1_energy, "gap_ohm": gap,
            "triangle_winding_counts": {"positive": signed_positive, "negative": signed_negative},
            "three_point_degree": 2}


def _synthetic_batch(flipped: bool) -> tuple[dict, dict, np.ndarray, np.ndarray, dict]:
    g = 3.0
    xy = np.asarray([[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]])
    values = 0.7 + 0.4 * xy[:, 0] - 0.25 * xy[:, 1]
    base_cells = ((0, 1, 2), (0, 2, 3))
    triangles = np.asarray([cell if not flipped else (cell[0], cell[2], cell[1]) for cell in base_cells], dtype=np.int64)
    bary = np.asarray([[2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0], [1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0], [1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0]])
    edge_to_branch: dict[tuple[int, int], int] = {}
    branch_values: list[float] = []
    local_branch = np.empty((2, 3), dtype=np.int64)
    local_sign = np.empty((2, 3), dtype=np.float64)
    expected = {"rt0_energy_ohm": 0.0, "cross_integral_ohm": 0.0, "p1_gradient_energy_ohm": 0.0, "gap_ohm": 0.0}
    for ti, tri in enumerate(triangles):
        vertices = xy[tri]
        vals = values[tri]
        e = np.asarray([vertices[1] - vertices[0], vertices[2] - vertices[0]])
        grad = np.linalg.solve(e, np.asarray([vals[1] - vals[0], vals[2] - vals[0]]))
        area = abs(float(np.linalg.det(e))) / 2.0
        samples = bary @ vertices
        basis = (samples[:, None, :] - vertices[None, :, :]) / (2.0 * area)
        target = np.repeat((-g * grad)[None, :], 3, axis=0)
        design = basis.transpose(0, 2, 1).reshape(6, 3)
        q_local = np.linalg.lstsq(design, target.reshape(6), rcond=None)[0]
        metrics = _triangle_metrics(vertices, vals, q_local, g)
        expected["rt0_energy_ohm"] += metrics["rt0_energy_ohm"]
        expected["cross_integral_ohm"] += metrics["cross_integral_ohm"]
        expected["p1_gradient_energy_ohm"] += metrics["p1_energy_ohm"]
        expected["gap_ohm"] += metrics["gap_ohm"]
        for local in range(3):
            edge = tuple(sorted(int(x) for x in tri[np.arange(3) != local]))
            if edge not in edge_to_branch:
                edge_to_branch[edge] = len(branch_values)
                branch_values.append(float(q_local[local]))
                local_sign[ti, local] = 1.0
            else:
                branch = edge_to_branch[edge]
                reference = branch_values[branch]
                if abs(q_local[local] - reference) <= 1e-12 * max(1.0, abs(reference)):
                    local_sign[ti, local] = 1.0
                elif abs(q_local[local] + reference) <= 1e-12 * max(1.0, abs(reference)):
                    local_sign[ti, local] = -1.0
                else:
                    raise AssertionError("synthetic shared-facet orientation mismatch")
            local_branch[ti, local] = edge_to_branch[edge]
    mesh = {"node_xy_um": xy * 1e6, "triangles": triangles}
    topo = {"free_triangle_indices": np.arange(2, dtype=np.int64), "local_facet_branch_index": local_branch, "local_outward_flux_sign": local_sign}
    return mesh, topo, np.arange(4, dtype=np.int64), np.asarray(branch_values, dtype=np.float64), expected


def _synthetic_gap_self_check() -> None:
    g = 3.0
    xy = np.asarray([[0.0, 0.0], [2.0, 0.0], [2.0, 1.0], [0.0, 1.0]])
    values_all = 0.7 + 0.4 * xy[:, 0] - 0.25 * xy[:, 1]
    cells = ((0, 1, 2), (0, 2, 3))
    orientation_errors = []
    for flipped in (False, True):
        total = {"rt0_energy_ohm": 0.0, "cross_integral_ohm": 0.0, "p1_energy_ohm": 0.0, "gap_ohm": 0.0}
        for cell in cells:
            order = np.asarray(cell if not flipped else (cell[0], cell[2], cell[1]), dtype=np.int64)
            vertices = xy[order]
            values = values_all[order]
            e = np.asarray([vertices[1] - vertices[0], vertices[2] - vertices[0]])
            grad = np.linalg.solve(e, np.asarray([values[1] - values[0], values[2] - values[0]]))
            bary = np.asarray([[2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0], [1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0], [1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0]])
            sample = bary @ vertices
            area = abs(float(np.linalg.det(e))) / 2.0
            basis = (sample[:, None, :] - vertices[None, :, :]) / (2.0 * area)
            target = np.repeat((-g * grad)[None, :], 3, axis=0)
            design = basis.transpose(0, 2, 1).reshape(6, 3)
            q = np.linalg.lstsq(design, target.reshape(6), rcond=None)[0]
            metrics = _triangle_metrics(vertices, values, q, g)
            for key in total:
                total[key] += metrics[key]
        assert abs(total["cross_integral_ohm"] + total["p1_energy_ohm"]) < 1e-12
        assert abs(total["rt0_energy_ohm"] - total["p1_energy_ohm"]) < 1e-12
        assert abs(total["gap_ohm"]) < 1e-12
        orientation_errors.append(total["gap_ohm"])
    assert max(abs(x) for x in orientation_errors) < 1e-12
    for flipped in (False, True):
        mesh, topo, mapping, q_global, expected = _synthetic_batch(flipped)
        actual = _gap_integral(mesh, topo, mapping, values_all, q_global, conductance_s=g)
        for key in expected:
            assert abs(actual[key] - expected[key]) < 1e-12
        assert abs(actual["cross_integral_ohm"] + actual["p1_gradient_energy_ohm"]) < 1e-12
        assert abs(actual["rt0_energy_ohm"] - actual["p1_gradient_energy_ohm"]) < 1e-12
        assert abs(actual["gap_ohm"]) < 1e-12
    # The same synthetic bar also exercises two physical gauge choices.
    exact = 2.0 / (g * 1.5)
    b = sp.csc_matrix(np.array([[1.0], [-1.0]]))
    mixed = sp.bmat([[sp.csc_matrix([[exact]]), -b.T], [b, None]], format="csc")
    for gauge in (0, 1):
        aug = _with_gauge(mixed, 1 + gauge)
        x = spla.spsolve(aug, np.array([0.0, 1.0, -1.0, 0.0]))
        assert abs((x[1] - x[2]) - exact) < 1e-12


def _load_inputs() -> dict:
    rt0 = _read_json_pinned("rt0_result")
    topo = _read_json_pinned("topology_result")
    binding = _read_json_pinned("binding_result")
    drive = _read_json_pinned("drive_result")
    _check_result_contract(rt0, topo, binding, drive)
    rt0_npz = _read_npz_pinned("rt0_npz")
    topo_npz = _read_npz_pinned("topology_npz")
    binding_npz = _read_npz_pinned("binding_npz")
    drive_npz = _read_npz_pinned("drive_npz")
    mesh_npz = _read_npz_pinned("mesh_npz")
    if rt0_npz["r_data"].size != int(rt0["counts"]["sparse_nnz"]):
        raise ValueError("RT0 R nnz mismatch")
    if topo_npz["branch_first_node"].size != int(topo["facet_current_branch_count"]):
        raise ValueError("topology branch count mismatch")
    if drive_npz["conductance_data"].size != int(drive["conductance_nnz"]):
        raise ValueError("P1 G nnz mismatch")
    if binding_npz["l25_local_electrode_potential_index"].size != 350:
        raise ValueError("source binding 350-link mismatch")
    pair = _pair_metadata(mesh_npz, drive, drive_npz)
    return {"rt0": rt0, "topology": topo, "binding": binding, "drive": drive,
            "rt0_npz": rt0_npz, "topology_npz": topo_npz, "binding_npz": binding_npz,
            "drive_npz": drive_npz, "mesh_npz": mesh_npz, "pair": pair}


def _final_gates(rt0: dict, p1: dict, gap: dict) -> dict:
    scale = max(abs(rt0["pair_voltage_ohm"]), abs(p1["pair_voltage_ohm"]), np.finfo(float).tiny)
    cross_rel = abs(gap["cross_integral_ohm"] + p1["energy_vgv_ohm"]) / max(abs(p1["energy_vgv_ohm"]), np.finfo(float).tiny)
    identity_rel = abs(gap["gap_ohm"] - (rt0["energy_qrq_ohm"] - p1["energy_vgv_ohm"])) / scale
    rt_quadrature_rel = abs(gap["rt0_energy_ohm"] - rt0["energy_qrq_ohm"]) / max(abs(rt0["energy_qrq_ohm"]), np.finfo(float).tiny)
    p1_gradient_rel = abs(gap["p1_gradient_energy_ohm"] - p1["energy_vgv_ohm"]) / max(abs(p1["energy_vgv_ohm"]), np.finfo(float).tiny)
    energy_tol = REL_TOL * scale
    return {
        "rt0_physical_residual": rt0["physical_residual_gate"],
        "p1_physical_residual": p1["physical_residual_gate"],
        "rt0_energy_equals_pair": rt0["energy_pair_relative_error"] <= REL_TOL,
        "p1_energy_equals_pair": p1["energy_pair_relative_error"] <= REL_TOL,
        "rt0_quadrature_equals_matrix": rt_quadrature_rel <= REL_TOL,
        "p1_gradient_equals_matrix": p1_gradient_rel <= REL_TOL,
        "cross_integral_equals_negative_p1": cross_rel <= REL_TOL,
        "p1_le_rt0": p1["energy_vgv_ohm"] <= rt0["energy_qrq_ohm"] + energy_tol,
        "gap_nonnegative": gap["gap_ohm"] >= -energy_tol,
        "gap_identity": identity_rel <= REL_TOL,
        "cross_relative_error": cross_rel,
        "gap_identity_relative_error": identity_rel,
        "rt0_quadrature_relative_error": rt_quadrature_rel,
        "p1_gradient_relative_error": p1_gradient_rel,
        "energy_bound_tolerance_ohm": energy_tol,
        "finite": bool(all(math.isfinite(float(v)) for v in (rt0["energy_qrq_ohm"], p1["energy_vgv_ohm"], gap["gap_ohm"]))),
    }


def _run(args: argparse.Namespace) -> int:
    started = time.perf_counter()
    output_root = Path(args.output_root).resolve()
    _freeze_output_root(output_root)
    budget = _Budget(output_root / "progress.jsonl")
    watchdog = budget.start_watchdog()
    try:
        budget.emit("start", output_root=str(output_root))
        inputs = _load_inputs()
        preflight = _structural_preflight(inputs["rt0"], inputs["topology"], inputs["drive"], inputs["topology_npz"])
        budget.emit("inputs_loaded", pair=inputs["pair"], preflight=preflight)
        rt0_metrics, rt0_q, _ = _rt0_solve(inputs, output_root, budget)
        p1_metrics, p1_v = _p1_solve(inputs, output_root, budget)
        budget.check("post_solve")
        gap = _gap_integral(inputs["mesh_npz"], inputs["topology_npz"], inputs["drive_npz"]["full_to_contracted"], p1_v, rt0_q)
        gates = _final_gates(rt0_metrics, p1_metrics, gap)
        required = ("rt0_physical_residual", "p1_physical_residual", "rt0_energy_equals_pair", "p1_energy_equals_pair", "rt0_quadrature_equals_matrix", "p1_gradient_equals_matrix", "cross_integral_equals_negative_p1", "p1_le_rt0", "gap_nonnegative", "gap_identity", "finite")
        complete = all(bool(gates[key]) for key in required)
        result = {
            "program": PROGRAM, "version": "0.23.1",
            "status": "COMPLETED_CONDITIONAL_L25_RT0_P1_PAIR" if complete else "STOP_PAIR_PREMISE_OR_BOUNDS",
            "pair": inputs["pair"],
            "drive_contract": {"positive_electrode": 0, "negative_electrode": 1, "other_electrodes_floating": 173, "rim_flux_prescribed": False, "gauge_electrode": GAUGE_ELECTRODE},
            "material": {"sigma_s_per_m": SIGMA_S_PER_M, "thickness_m": THICKNESS_M, "sheet_conductance_s": SHEET_CONDUCTANCE_S},
            "inputs": {name: {"path": str(path), "sha256": expected} for name, (path, expected) in PINS.items()},
            "preflight": preflight, "rt0": rt0_metrics, "p1": p1_metrics, "gap": gap, "gates": gates,
            "driver": _file_receipt(output_root / "driver-at-run.py"),
            "fields": {"rt0": _file_receipt(output_root / "rt0-field.npz"), "p1": _file_receipt(output_root / "p1-field.npz")},
            "budget": {"max_runtime_s": MAX_RUNTIME_S, "max_rss_bytes": MAX_RSS_BYTES, "peak_working_set_bytes": budget.peak_working_set, "peak_private_bytes": budget.peak_private, "factor_peak_rss_measured": True},
            "limitations": ["The P1<=RT0/gap bracket is a conditional numerical comparison, not an exact full-board proof.", "The bound is conditional on saved topology KCL, electrode equipotential, and natural-boundary premises.", "No GC/native-via/magnetic/return/board/PowerSI operator or exact full-board claim is made."],
            "elapsed_s": time.perf_counter() - started,
        }
        _write_json_exclusive(output_root / "result.json", result)
        budget.emit("result_written", status=result["status"])
        return 0 if complete else 2
    except Exception as exc:
        failure = {"program": PROGRAM, "version": "0.23.1", "status": "STOP_EXCEPTION", "error": f"{type(exc).__name__}: {exc}", "elapsed_s": time.perf_counter() - started}
        try:
            _write_json_exclusive(output_root / "failure.json", failure)
        except Exception:
            pass
        return 1
    finally:
        budget.stop.set()
        watchdog.join(timeout=5.0)


def main() -> int:
    parser = argparse.ArgumentParser(prog="probe_astra_l25_rt0_p1_pair.py", description=PROGRAM)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--dry-check", action="store_true", help="load and validate pinned inputs without building matrices")
    parser.add_argument("--output-root", default=str(RESEARCH / "astra-l25-rt0-p1-pair-01"))
    args = parser.parse_args()
    if args.self_check:
        _synthetic_gap_self_check()
        print(f"{PROGRAM}: SELF_CHECK PASS")
        return 0
    if args.dry_check:
        inputs = _load_inputs()
        print(json.dumps({"program": PROGRAM, "status": "DRY_CHECK_PASS", "pair": inputs["pair"], "preflight": _structural_preflight(inputs["rt0"], inputs["topology"], inputs["drive"], inputs["topology_npz"])}, indent=2, sort_keys=True, allow_nan=False))
        return 0
    return _run(args)


if __name__ == "__main__":
    raise SystemExit(main())
