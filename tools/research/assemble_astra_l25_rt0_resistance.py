"""Assemble the saved L25 dual-cell RT0 resistance shadow without a global solve."""

from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
import sys
from pathlib import Path
from ctypes import wintypes
from time import monotonic

import numpy as np
from scipy.sparse import coo_matrix

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

PROGRAM = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
SIGMA_S_PER_M = 59.59e6
THICKNESS_M = 32e-6
SHEET_CONDUCTANCE_S = SIGMA_S_PER_M * THICKNESS_M
REL_TOL = 1e-10
RANDOM_SEED = 20260907
BARYCENTRIC = np.asarray(((2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0), (1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0), (1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0)))
R = ROOT / "outputs/research"
PINS = {
    "topology_result": (R / "astra-l25-dual-cell-topology-01/result.json", "d24d13516b0a721565cdf16e2c678b2e74c03b70f8edea9557926b4599542d31"),
    "topology_npz": (R / "astra-l25-dual-cell-topology-01/dual-cell-topology.npz", "eb33117f14a3e5725e9efeafd2a0a083961ad83abaf022b7fe78b94bc8b5265f"),
    "mesh_npz": (R / "astra-l25-sheet-mesh-preflight-01/mesh-stiffness.npz", "09e79fcbdac766603a3e2f451e2079c3c6b8b588026d0aa47f532f1e98d78134"),
    "binding_result": (R / "astra-l25-dual-cell-source-binding-01/result.json", "874f36da7dc906171d7761510902432752a3164b4ec055835064e389c4d427e6"),
    "binding_npz": (R / "astra-l25-dual-cell-source-binding-01/native-source-binding.npz", "c9d41f1e2b870dd854affa1db2c31bb86f24c472866fcd2125f313cb29981c6a"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _local_rt0(vertices_m: np.ndarray, conductance_s: float) -> tuple[np.ndarray, float, float]:
    edge = np.stack((vertices_m[1] - vertices_m[0], vertices_m[2] - vertices_m[0]))
    signed_det = float(np.linalg.det(edge))
    area = abs(signed_det) / 2.0
    if not np.isfinite(signed_det) or area <= 0.0:
        raise ValueError("degenerate triangle")
    centroid = np.mean(vertices_m, axis=0)
    centered = vertices_m - centroid
    edge_centroid = centroid - vertices_m
    local_r = (edge_centroid @ edge_centroid.T + np.full((3, 3), np.sum(centered * centered) / 12.0)) / (4.0 * conductance_s * area)
    return local_r, signed_det, area


def _batch_local_rt0(vertices_m: np.ndarray, conductance_s: float) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    edge = np.stack((vertices_m[:, 1] - vertices_m[:, 0], vertices_m[:, 2] - vertices_m[:, 0]), axis=1)
    signed_det = edge[:, 0, 0] * edge[:, 1, 1] - edge[:, 0, 1] * edge[:, 1, 0]
    area = abs(signed_det) / 2.0
    centroid = np.mean(vertices_m, axis=1)
    centered = vertices_m - centroid[:, None, :]
    edge_centroid = centroid[:, None, :] - vertices_m
    local_r = (np.einsum("nai,nbi->nab", edge_centroid, edge_centroid) + np.sum(centered * centered, axis=(1, 2))[:, None, None] / 12.0) / (4.0 * conductance_s * area[:, None, None])
    return local_r, signed_det, area


def _quadrature_energy(vertices_m: np.ndarray, q_local: np.ndarray, conductance_s: float) -> np.ndarray:
    _, _, area = _batch_local_rt0(vertices_m, conductance_s)
    samples = np.einsum("sp,npa->nsa", BARYCENTRIC, vertices_m)
    basis = (samples[:, :, None, :] - vertices_m[:, None, :, :]) / (2.0 * area[:, None, None, None])
    current_density = np.einsum("nfk,nsfc->nskc", q_local, basis)
    return np.einsum("n,nskc,nskc->k", area, np.conj(current_density), current_density) / (3.0 * conductance_s)


def _rss_bytes() -> int:
    if os.name != "nt":
        return 0

    class ProcessMemoryCountersEx(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_uint32), ("PageFaultCount", ctypes.c_uint32), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t), ("PrivateUsage", ctypes.c_size_t)]

    counters = ProcessMemoryCountersEx()
    counters.cb = ctypes.sizeof(counters)
    current_process = ctypes.windll.kernel32.GetCurrentProcess
    current_process.argtypes = ()
    current_process.restype = wintypes.HANDLE
    get_memory = ctypes.windll.psapi.GetProcessMemoryInfo
    get_memory.argtypes = (wintypes.HANDLE, ctypes.POINTER(ProcessMemoryCountersEx), wintypes.DWORD)
    get_memory.restype = wintypes.BOOL
    if not get_memory(current_process(), ctypes.byref(counters), counters.cb):
        raise OSError("GetProcessMemoryInfo failed")
    return int(max(counters.WorkingSetSize, counters.PrivateUsage))


def _check_budget(started: float, peak_rss: int, phase: str) -> int:
    elapsed = monotonic() - started
    rss = _rss_bytes()
    peak_rss = max(peak_rss, rss)
    if elapsed > 90.0:
        raise RuntimeError(f"90s cooperative runtime budget exceeded after {phase}: {elapsed:.3f}s")
    if rss and rss > 4 * 2**30:
        raise RuntimeError(f"4GiB cooperative RSS budget exceeded after {phase}: {rss} bytes")
    return peak_rss


def _toy_quadrature(vertices_m: np.ndarray, conductance_s: float) -> np.ndarray:
    barycentric = np.asarray(((2.0 / 3.0, 1.0 / 6.0, 1.0 / 6.0), (1.0 / 6.0, 2.0 / 3.0, 1.0 / 6.0), (1.0 / 6.0, 1.0 / 6.0, 2.0 / 3.0)))
    _, _, area = _local_rt0(vertices_m, conductance_s)
    samples = barycentric @ vertices_m
    basis = (samples[:, None, :] - vertices_m[None, :, :]) / (2.0 * area)
    return area * np.einsum("p,pia,pja->ij", np.full(3, 1.0 / 3.0), basis, basis) / conductance_s


def _self_check() -> dict:
    vertices = np.asarray(((0.0, 0.0), (2.0e-3, 0.0), (0.0, 1.0e-3)))
    conductance = 7.3
    local, det, area = _local_rt0(vertices, conductance)
    quadrature = _toy_quadrature(vertices, conductance)
    permutation = np.asarray((0, 2, 1))
    flipped = vertices[permutation]
    flipped_local, flipped_det, _ = _local_rt0(flipped, conductance)
    permuted_reference = local[np.ix_(permutation, permutation)]
    if not (det > 0.0 and flipped_det < 0.0 and np.allclose(local, quadrature, rtol=1e-12, atol=1e-18)):
        raise AssertionError("three-point RT0 quadrature mismatch")
    if not np.allclose(flipped_local, permuted_reference, rtol=1e-12, atol=1e-18):
        raise AssertionError("winding permutation changed local RT0 resistance")
    batch_vertices = np.asarray((vertices, np.asarray(((0.0, 0.0), (1.3e-3, 0.2e-3), (0.1e-3, 1.7e-3))), flipped))
    batch_local, batch_det, batch_area = _batch_local_rt0(batch_vertices, conductance)
    scalar_local = np.asarray([_local_rt0(item, conductance)[0] for item in batch_vertices])
    if not (batch_det.shape == (3,) and batch_area.shape == (3,) and np.allclose(batch_local, scalar_local, rtol=1e-12, atol=1e-18)):
        raise AssertionError("vectorized RT0 local formula disagrees with scalar formula")
    q = np.asarray(((1.0 + 0.2j, -0.3 + 0.4j, 0.7 - 0.1j), (0.4 - 0.5j, 0.2 + 0.1j, -0.6 + 0.3j), (0.8 + 0.2j, -0.2 - 0.1j, 0.1 + 0.9j)))[:, :, None]
    quadrature_energy = _quadrature_energy(batch_vertices, q, conductance)
    scalar_energy = np.asarray([np.vdot(q[index, :, 0], scalar_local[index] @ q[index, :, 0]) for index in range(len(batch_vertices))])
    if not np.allclose(quadrature_energy[0], np.sum(scalar_energy), rtol=1e-12, atol=1e-18):
        raise AssertionError("degree-2 quadrature energy disagrees with scalar RT0 local energy")
    eigenvalues = np.linalg.eigvalsh(local)
    return {"status": "SELF_CHECK_PASS", "three_point_quadrature_relative": float(np.linalg.norm(local - quadrature) / np.linalg.norm(local)), "winding_permutation_relative": float(np.linalg.norm(flipped_local - permuted_reference) / np.linalg.norm(local)), "batch_scalar_relative": float(np.linalg.norm(batch_local - scalar_local) / np.linalg.norm(scalar_local)), "batch_quadrature_energy_relative": float(abs(quadrature_energy[0] - np.sum(scalar_energy)) / max(abs(np.sum(scalar_energy)), np.finfo(float).tiny)), "min_local_eigenvalue": float(np.min(eigenvalues)), "dense_global_allocated": False, "single_numpy_calls_noninterruptible": True}


def _load_and_assemble() -> tuple[dict, dict, float, int]:
    started = monotonic()
    peak_rss = 0
    for path, expected in PINS.values():
        if _sha256(path) != expected:
            raise ValueError(f"input hash mismatch: {path}")
    peak_rss = _check_budget(started, peak_rss, "input hashes")
    topology_receipt = json.loads(PINS["topology_result"][0].read_text(encoding="utf-8"))
    binding_receipt = json.loads(PINS["binding_result"][0].read_text(encoding="utf-8"))
    if topology_receipt["status"] != "COMPLETED_SOURCE_L25_DUAL_CELL_TOPOLOGY" or binding_receipt["status"] != "COMPLETED_L25_DUAL_CELL_NATIVE_SOURCE_BINDING":
        raise ValueError("pinned topology or binding receipt is not completed")
    if topology_receipt["output"]["sha256"] != PINS["topology_npz"][1] or binding_receipt["output"]["sha256"] != PINS["binding_npz"][1]:
        raise ValueError("receipt output hash does not match pinned output")
    owners = binding_receipt["gc_owners"]
    if len(owners) != 4 or len({row["fingerprint"] for row in owners}) != 4:
        raise ValueError("expected four distinct GC owners")
    with np.load(PINS["mesh_npz"][0], allow_pickle=False) as mesh, np.load(PINS["topology_npz"][0], allow_pickle=False) as topology, np.load(PINS["binding_npz"][0], allow_pickle=False) as binding:
        points_um = mesh["node_xy_um"]
        triangles = mesh["triangles"][topology["free_triangle_indices"]]
        local_branches = topology["local_facet_branch_index"]
        local_signs = topology["local_outward_flux_sign"].astype(float)
        branch_first = topology["branch_first_node"]
        branch_second = topology["branch_second_node"]
        free_count = len(triangles)
        branch_count = len(branch_first)
        if points_um.shape != (535149, 2) or free_count != 539641 or triangles.shape != (free_count, 3) or local_branches.shape != (free_count, 3) or local_signs.shape != (free_count, 3) or branch_count != 544687:
            raise ValueError("pinned L25 topology dimensions changed")
        if np.any(local_branches < -1) or np.any(local_branches >= branch_count):
            raise ValueError("local branch index out of range")
        support = np.bincount(local_branches[local_branches >= 0], minlength=branch_count)
        if np.any(support == 0):
            raise ValueError("at least one global branch has no local support")
        rng = np.random.default_rng(RANDOM_SEED)
        random_q = rng.standard_normal((branch_count, 3)) + 1j * rng.standard_normal((branch_count, 3))
        rows, cols, data = [], [], []
        quadrature_energy = np.zeros(3, dtype=np.complex128)
        analytic_local_energy = np.zeros(3, dtype=np.complex128)
        local_min_eigenvalue = np.inf
        max_local_eigenvalue = 0.0
        max_local_condition = 0.0
        max_area = 0.0
        min_area = np.inf
        for start in range(0, free_count, 100_000):
            peak_rss = _check_budget(started, peak_rss, f"before chunk {start}")
            stop = min(start + 100_000, free_count)
            vertices = points_um[triangles[start:stop]].astype(float) * 1.0e-6
            local_r, signed_det, area = _batch_local_rt0(vertices, SHEET_CONDUCTANCE_S)
            if np.any(~np.isfinite(area)) or np.any(area <= 0.0):
                raise ValueError("nonfinite or degenerate free triangle")
            local_eigenvalues = np.linalg.eigvalsh(local_r)
            local_eigenvalue = local_eigenvalues[:, 0]
            local_min_eigenvalue = min(local_min_eigenvalue, float(np.min(local_eigenvalue)))
            max_local_eigenvalue = max(max_local_eigenvalue, float(np.max(local_eigenvalues[:, -1])))
            max_local_condition = max(max_local_condition, float(np.max(local_eigenvalues[:, -1] / local_eigenvalue)))
            if np.any(~np.isfinite(local_r)) or np.any(local_eigenvalue <= 0.0):
                raise ValueError("local RT0 resistance is not SPD")
            min_area = min(min_area, float(np.min(area)))
            max_area = max(max_area, float(np.max(area)))
            branch = local_branches[start:stop]
            signs = local_signs[start:stop]
            active = branch >= 0
            safe_branch = np.maximum(branch, 0)
            q_local = signs[:, :, None] * random_q[safe_branch]
            q_local[~active, :] = 0.0
            quadrature_energy += _quadrature_energy(vertices, q_local, SHEET_CONDUCTANCE_S)
            analytic_local_energy += np.einsum("nri,nrs,nsj->ij", np.conj(q_local), local_r, q_local).diagonal()
            rr = np.broadcast_to(safe_branch[:, :, None], (stop - start, 3, 3))
            cc = np.broadcast_to(safe_branch[:, None, :], (stop - start, 3, 3))
            values = signs[:, :, None] * local_r * signs[:, None, :]
            keep = active[:, :, None] & active[:, None, :]
            rows.append(rr[keep].astype(np.int64))
            cols.append(cc[keep].astype(np.int64))
            data.append(values[keep].astype(np.float64))
            peak_rss = _check_budget(started, peak_rss, f"after chunk {start}")
        sparse_r = coo_matrix((np.concatenate(data), (np.concatenate(rows), np.concatenate(cols))), shape=(branch_count, branch_count)).tocsc()
        sparse_r.sum_duplicates()
        sparse_r.sort_indices()
        symmetry_error = float(np.max(abs((sparse_r - sparse_r.T).data), initial=0.0))
        sparse_q = sparse_r @ random_q
        sparse_energy = np.sum(np.conj(random_q) * sparse_q, axis=0)
        energy_relative = float(np.max(abs(sparse_energy - quadrature_energy) / np.maximum(abs(quadrature_energy), np.finfo(float).tiny)))
        analytic_energy_relative = float(np.max(abs(analytic_local_energy - quadrature_energy) / np.maximum(abs(quadrature_energy), np.finfo(float).tiny)))
        if symmetry_error > REL_TOL or energy_relative > REL_TOL:
            raise ValueError("sparse RT0 symmetry or energy gate failed")
        metadata = {"gc_owner_count": len(owners), "gc_owner_fingerprints": [row["fingerprint"] for row in owners], "native_via_count": int(len(binding["native_active_finite_index"])), "native_target_incident_count": int(binding_receipt["native_via_coverage"]["target_incident_count"]), "native_parallel_count_sum": float(np.sum(binding["native_parallel_count"])), "native_y_or_lu_assembled": False}
        arrays = {"r_data": sparse_r.data, "r_indices": sparse_r.indices, "r_indptr": sparse_r.indptr, "r_shape": np.asarray(sparse_r.shape, dtype=np.int64), "branch_first_node": branch_first, "branch_second_node": branch_second}
    gates = {"local_spd": bool(local_min_eigenvalue > 0.0), "sparse_symmetry": bool(symmetry_error <= REL_TOL), "branch_support": bool(np.all(support > 0)), "random_q_energy": bool(energy_relative <= REL_TOL), "three_point_quadrature_and_winding": bool(_self_check()["status"] == "SELF_CHECK_PASS")}
    peak_rss = _check_budget(started, peak_rss, "sparse assembly")
    result = {"program": PROGRAM, "version": VERSION, "status": "COMPLETED_L25_RT0_RESISTANCE_SHADOW" if all(gates.values()) else "STOP_L25_RT0_RESISTANCE_GATE", "inputs": {name: {"path": str(path), "sha256": digest} for name, (path, digest) in PINS.items()}, "material": {"sigma_s_per_m": SIGMA_S_PER_M, "thickness_m": THICKNESS_M, "sheet_conductance_s": SHEET_CONDUCTANCE_S}, "counts": {"free_cell_count": free_count, "branch_count": branch_count, "sparse_nnz": int(sparse_r.nnz)}, "metrics": {"local_min_eigenvalue_ohm": local_min_eigenvalue, "max_local_eigenvalue_ohm": max_local_eigenvalue, "max_local_condition": max_local_condition, "sparse_symmetry_max_abs_ohm": symmetry_error, "random_q_quadrature_energy_relative_error": energy_relative, "analytic_local_energy_diagnostic_relative_error": analytic_energy_relative, "min_triangle_area_m2": min_area, "max_triangle_area_m2": max_area}, "gates": gates, "source_metadata": metadata, "budget": {"max_runtime_s": 90.0, "max_rss_gib": 4.0, "peak_rss_bytes": peak_rss, "cooperative_chunk_checks": True, "single_numpy_calls_noninterruptible": True}, "limitations": ["Resistance-only RT0 shadow over saved free-cell topology; electrode interior bases and insulating boundary q=0 are omitted exactly as pinned topology specifies.", "Four GC owners and 350 native-via records are metadata bindings only; no GC/native Y, LU, contact solve, magnetic L, return closure, or PowerSI claim."], "arrays": {"file": "rt0-resistance.npz", "sparse_shape": list(sparse_r.shape), "dense_global_allocated": False}}
    return result, arrays, started, peak_rss


def main(output_root: Path) -> None:
    if output_root.exists():
        raise SystemExit(f"refusing to overwrite existing evidence: {output_root}")
    output_root.mkdir(parents=False)
    source_bytes = Path(__file__).read_bytes()
    with (output_root / "driver-at-run.py").open("xb") as handle:
        handle.write(source_bytes)
    result, arrays, started, peak_rss = _load_and_assemble()
    with (output_root / "rt0-resistance.npz").open("xb") as handle:
        np.savez_compressed(handle, **arrays)
    peak_rss = _check_budget(started, peak_rss, "NPZ serialization")
    result["script_sha256"] = hashlib.sha256(source_bytes).hexdigest()
    result["output_sha256"] = _sha256(output_root / "rt0-resistance.npz")
    result["budget"]["peak_rss_bytes"] = peak_rss
    result["elapsed_s"] = monotonic() - started
    with (output_root / "result.json").open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"status": result["status"], "elapsed_s": result["elapsed_s"], "counts": result["counts"]}, allow_nan=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--output-root", type=Path)
    args = parser.parse_args()
    if args.self_check:
        print(json.dumps(_self_check(), allow_nan=False))
    elif args.output_root is None:
        parser.error("--output-root is required unless --self-check is used")
    else:
        main(args.output_root)
