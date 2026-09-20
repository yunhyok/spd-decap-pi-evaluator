"""SPD Decap PI Evaluator v0.23.1: actual PWR/G halfspace scalar self.

Reuses qualified bare-1/R self entries and integrates reflected supports.
The fixed 5e-5 refinement gate is relative to the COMPLETE complex physical
self coefficient, including a conservative scaled cached-direct contribution.
Only direct entries exhausting that gate are refined. Interface faces use the
combined direct/image coefficient once. Deep-stack point diagonals stay in
their smooth operator. No nonself pair or board response is assembled here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from time import monotonic
import traceback

import numpy as np

from apply_astra_owned_3d_green import TET_BARY, TRI_BARY
from astra_stratified_charge_green import EPS0, source_background
from qualify_astra_source_joint_charge_green import scalar_pair


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
GATE = 5e-5
PINS = {
    R / "astra-boundary-conforming-power-joint-01/joint-template.npz": "14b927da78b3cbf9d68f49c89b84dbeda746a99cef0bc6d9bcefd597f9db49eb",
    R / "astra-source-joint-all-self-green-02/self-blocks.npz": "fc4ed5c7889615a9f818ef1bbea5b38e683b35caa40aadd5a63d235acb8abebd",
    R / "astra-g-window-sheet-volume-coupling-20260912/sheet-volume-coupling.npz": "72ee8291d74c2bd3abaf1b32b9d59e5db9f65cbdc5431957ebdd45289ef22cdf",
    R / "astra-g-complete-self-green-20260912/self-blocks.npz": "d62fb76057f25ab3a68a670c5509922c56c598437b5f3e51da4d321b332c2cca",
    R / "astra-pwr-g-owned-green-20260912-02/template-self-map.npz": "d197f79556daaaa3da5fe74dd77b9023bb224f71f46c091cbbf630c47ee0ea1e",
    R / "astra-pwr-top-component-closure-20260912-02/pwr-top-component-closure.npz": "a5bbd79e55b2ff9d25a09c21bbc368b424b4d37f4f7c6fa3c0be97f87a055d7b",
    ROOT / "tools/research/apply_astra_owned_3d_green.py": "acc254dad5187a04d0040689896db4b997e3e71d91e82845082bd834352de080",
    ROOT / "tools/research/qualify_astra_source_joint_charge_green.py": "19176fce6bbbc953cc79ebb4fe05d6293e915e5fff6db2a7cb988b73511bc23d",
    ROOT / "tools/research/qualify_astra_tetra_volume_green.py": "24312294f3de4485ba5272afd740daeaf53e60a02974ee4745752f7aa8f155eb",
    ROOT / "tools/research/qualify_astra_tetra_charge_green.py": "aebe4d00aa7f79ff73883d6856b3331ab976e80e877d455cff0de473ea754aa9",
}


def sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def load_supports():
    for path, digest in PINS.items():
        assert sha(path) == digest, path
    paths = list(PINS)
    with np.load(paths[0], allow_pickle=False) as z:
        vertices = z["vertices_local_um"]
        pwr_tetra = vertices[z["cells"]] * 1e-6
        pwr_faces = vertices[z["face_vertices"][z["boundary_face_ids"]]] * 1e-6
    with np.load(paths[2], allow_pickle=False) as z:
        g_tetra = z["volume_charge_vertices_um"] * 1e-6
        g_faces = z["surface_charge_vertices_um"] * 1e-6
    groups = [pwr_tetra, pwr_faces, g_tetra, g_faces]
    assert [len(g) for g in groups] == [5304, 3876, 6078, 3008]
    values, orders, changes = [], [], []
    for path in (paths[1], paths[3]):
        with np.load(path, allow_pickle=False) as z:
            assert z["accepted_at_fixed_gate"].all()
            values.append(z["static_scalar_self_per_m"])
            orders.append(z["final_order"])
            changes.append(z["final_relative_errors"][:, 0])
    supports = [entity for group in groups for entity in group]
    return supports, groups, np.concatenate(values), np.concatenate(orders), np.concatenate(changes)


def classify(vertices, interface):
    z = vertices[:, 2].copy()
    z[abs(z-interface) < 1e-16] = interface
    if np.all(z == interface):
        assert len(vertices) == 3, "a volume cannot lie on an interface"
        return 0
    if z.max() <= interface:
        return -1
    if z.min() >= interface:
        return 1
    raise ValueError("Support crosses dielectric interface: split it before applying a self coefficient")


def point_self_batch(vertices, interface, eps_above, eps_below):
    """The same positive tetra4/triangle3 rule and exact-point omission as FMM."""
    rule = TET_BARY if vertices.shape[1] == 4 else TRI_BARY
    points = np.einsum("qi,tid->tqd", rule, vertices)
    z = vertices[:, :, 2]
    low, high = z.min(axis=1), z.max(axis=1)
    on = (abs(low-interface) < 1e-16) & (abs(high-interface) < 1e-16)
    assert not np.any((low < interface-1e-16) & (high > interface+1e-16))
    above = high <= interface+1e-16
    host = np.where(above, eps_above, eps_below)
    other = np.where(above, eps_below, eps_above)
    distance = np.linalg.norm(points[:, :, None]-points[:, None, :], axis=-1)
    inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > 0)
    direct = inverse.mean(axis=(1, 2))
    mirrored = points.copy(); mirrored[:, :, 2] = 2*interface-mirrored[:, :, 2]
    distance = np.linalg.norm(points[:, :, None]-mirrored[:, None, :], axis=-1)
    inverse = np.divide(1., distance, out=np.zeros_like(distance), where=distance > 0)
    image = inverse.mean(axis=(1, 2))
    result = (direct+(host-other)/(host+other)*image)/host
    result[on] = 2*direct[on]/(eps_above+eps_below)
    return result/(4*np.pi*EPS0)


def integrate_self(vertices, base, base_order, base_relative_change, interface, eps_above, eps_below, deadline):
    side = classify(vertices, interface)
    if side == 0:
        coefficient = 2/(eps_above+eps_below)/(4*np.pi*EPS0)
        return complex(coefficient*base), base, base_order, base, 0, base_relative_change, base_relative_change, 0., False
    host, other = (eps_above, eps_below) if side < 0 else (eps_below, eps_above)
    reflection = (host-other)/(host+other)
    coefficient = 1/(4*np.pi*EPS0*host)
    mirrored = vertices.copy(); mirrored[:, 2] = 2*interface-mirrored[:, 2]
    if len(vertices) == 4 and np.linalg.det((mirrored[1:]-mirrored[0]).T) < 0:
        mirrored[[1, 2]] = mirrored[[2, 1]]
    base_change = abs(base)*base_relative_change
    previous = None
    direct_refined = False
    combined = direct_part = image_part = np.inf
    for order in (8, 16, 32, 64, 128):
        assert monotonic() < deadline, "halfspace self wall-clock budget"
        image = scalar_pair(vertices, mirrored, order)
        assert np.isfinite(image) and image > 0
        physical = coefficient*(base+reflection*image)
        assert physical.real > 0 and np.isfinite(physical)
        image_part = np.inf if previous is None else abs(coefficient*reflection*(image-previous))/abs(physical)
        direct_part = abs(coefficient)*base_change/abs(physical)
        # Reuse the accepted direct cache unless its amplified change prevents
        # the complete-physical-self gate, even after the image has converged.
        while (direct_part >= GATE or (image_part < .1*GATE and direct_part > .9*GATE)) and base_order < 128:
            assert monotonic() < deadline, "halfspace direct-refinement wall-clock budget"
            new_order = int(base_order)*2
            updated = scalar_pair(vertices, vertices, new_order)
            assert updated > 0 and np.isfinite(updated)
            base_change, base, base_order = abs(updated-base), updated, new_order
            direct_refined = True
            physical = coefficient*(base+reflection*image)
            direct_part = abs(coefficient)*base_change/abs(physical)
            image_part = np.inf if previous is None else abs(coefficient*reflection*(image-previous))/abs(physical)
        combined = direct_part+image_part
        if combined < GATE:
            break
        previous = image
    assert image <= base*(1+GATE), "mirror self exceeds direct self beyond the fixed numerical gate"
    return complex(physical), base, base_order, image, order, combined, direct_part, image_part, direct_refined


def subset_rows(supports, cache_change):
    groups = {}
    for i, vertices in enumerate(supports):
        key = (i >= 9180, len(vertices), round(float(vertices[:, 2].min()*1e6), 9),
               round(float(vertices[:, 2].max()*1e6), 9))
        groups.setdefault(key, []).append(i)
    selected = []
    for rows in groups.values():
        selected.extend((rows[0], max(rows, key=lambda i: cache_change[i]),
                         max(rows, key=lambda i: np.linalg.norm(np.ptp(supports[i], axis=0)))))
    return np.unique(selected)


def expand_actual(arrays, groups):
    """Map integrated template values; evaluate point self on actual global coordinates."""
    with np.load(list(PINS)[4], allow_pickle=False) as z:
        cell = z["pwr_canonical_joint_cell"]; face = z["pwr_free_surface_cache_row"]
    with np.load(list(PINS)[5], allow_pickle=False) as z:
        xyz = z["vertices_um"]
        tetra = xyz[z["cells"]]*1e-6
        triangles = xyz[z["face_vertices"][z["free_surface_face_ids"]]]*1e-6
    assert len(cell) == len(tetra) == 288804 and len(face) == len(triangles) == 145516
    full_ids = np.r_[cell, 9180+np.arange(6078), face, 9180+6078+np.arange(3008)]
    assert arrays["accepted_at_fixed_gate"][full_ids].all()
    actual_points = []
    interface = 25e-6; ea = 1.+0j; eb = 3.4*(1-.0041j)
    for group in (tetra, groups[2], triangles, groups[3]):
        for first in range(0, len(group), 16384):
            actual_points.append(point_self_batch(group[first:first+16384], interface, ea, eb))
    actual_point = np.concatenate(actual_points)
    mapped_point = arrays["point_halfspace_self_p"][full_ids]
    mapping_error = float(np.max(abs(actual_point-mapped_point)/np.maximum(abs(actual_point), 1e-300)))
    assert mapping_error < 1e-8, "actual/template point self geometry mismatch"
    exact = arrays["halfspace_self_p"][full_ids]
    return dict(physical_halfspace_self_p=exact, point_halfspace_self_p=actual_point,
                physical_halfspace_self_delta_p=exact-actual_point,
                unique_support_row=full_ids), mapping_error


def run(output, stage, seconds, resume=None, resume_sha=None):
    started = monotonic(); deadline = started+seconds
    assert 0 < seconds <= 900 and not output.exists()
    output.mkdir(parents=True)
    (output/"driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    supports, groups, bare, order, change = load_supports()
    interfaces, eps, background = source_background()
    n = len(supports)
    arrays = dict(halfspace_self_p=np.full(n, np.nan+1j*np.nan), direct_scalar_per_m=bare.copy(),
                  direct_order=order.copy(), image_scalar_per_m=np.full(n, np.nan),
                  image_order=np.zeros(n, np.int16), complete_refinement_relative=np.full(n, np.nan),
                  direct_refinement_relative=np.full(n, np.nan), image_refinement_relative=np.full(n, np.nan),
                  direct_reintegrated=np.zeros(n, bool), accepted_at_fixed_gate=np.zeros(n, bool),
                  point_halfspace_self_p=np.concatenate([point_self_batch(g, interfaces[0], eps[0], eps[1]) for g in groups]))
    reused = 0
    if resume is not None:
        assert resume_sha and sha(resume/"result.json") == resume_sha
        receipt = json.loads((resume/"result.json").read_bytes())
        assert receipt["pins"] == {str(p): h for p, h in PINS.items()}
        assert receipt["driver_sha256"] == sha(Path(__file__))
        assert receipt["artifact_sha256"] == sha(resume/"halfspace-self.npz")
        with np.load(resume/"halfspace-self.npz", allow_pickle=False) as z:
            good = z["accepted_at_fixed_gate"]
            reused = int(good.sum())
            for key in arrays:
                arrays[key][good] = z[key][good]
    selected = subset_rows(supports, change) if stage == "subset" else np.arange(n)
    failure = None; expanded = None; mapping_error = None
    try:
        for row in selected:
            if arrays["accepted_at_fixed_gate"][row]:
                continue
            assert monotonic() < deadline, "halfspace self wall-clock budget"
            values = integrate_self(supports[row], bare[row], order[row], change[row],
                                    interfaces[0], eps[0], eps[1], deadline)
            keys = ("halfspace_self_p", "direct_scalar_per_m", "direct_order", "image_scalar_per_m",
                    "image_order", "complete_refinement_relative", "direct_refinement_relative",
                    "image_refinement_relative", "direct_reintegrated")
            for key, value in zip(keys, values):
                arrays[key][row] = value
            arrays["accepted_at_fixed_gate"][row] = values[5] < GATE
            if (int(row)+1) % 256 == 0:
                print(json.dumps(dict(completed=int(np.count_nonzero(np.isfinite(arrays["halfspace_self_p"]))),
                                      accepted=int(arrays["accepted_at_fixed_gate"].sum()),
                                      direct_refined=int(arrays["direct_reintegrated"].sum()), elapsed_s=monotonic()-started)), flush=True)
        if stage == "full" and arrays["accepted_at_fixed_gate"].all():
            expanded, mapping_error = expand_actual(arrays, groups)
    except Exception:
        failure = traceback.format_exc()
    artifact = output/"halfspace-self.npz"
    np.savez_compressed(artifact, **arrays)
    expanded_artifact = None
    if expanded is not None:
        expanded_artifact = output/"full-pwr-g-halfspace-self.npz"
        np.savez_compressed(expanded_artifact, **expanded)
    passed = failure is None and bool(arrays["accepted_at_fixed_gate"][selected].all())
    complete = np.isfinite(arrays["halfspace_self_p"])
    report = dict(program="SPD Decap PI Evaluator", version="0.23.1", stage=stage,
                  status=("PASS_ACTUAL_HALFSPACE_SELF_"+stage.upper()) if passed else "STOP_ACTUAL_HALFSPACE_SELF",
                  driver_sha256=sha(Path(__file__)), artifact_sha256=sha(artifact),
                  pins={str(p): h for p, h in PINS.items()}, source_background=background,
                  elapsed_s=monotonic()-started, wall_clock_budget_s=seconds, fixed_relative_gate=GATE,
                  unique_supports=n, selected_supports=len(selected), reused_completed_supports=reused,
                  completed_supports=int(complete.sum()), accepted_supports=int(arrays["accepted_at_fixed_gate"].sum()),
                  failed_completed_rows=np.flatnonzero(complete & ~arrays["accepted_at_fixed_gate"]).tolist(),
                  selected_rows=selected.tolist() if stage == "subset" else None,
                  interface_faces=int(sum(classify(v, interfaces[0]) == 0 for v in supports)),
                  direct_entries_refined=int(arrays["direct_reintegrated"].sum()),
                  maximum_complete_refinement_relative=float(np.nanmax(arrays["complete_refinement_relative"])) if complete.any() else None,
                  full_charge_rows=len(expanded["unique_support_row"]) if expanded is not None else None,
                  full_point_template_relative=mapping_error,
                  full_artifact_sha256=sha(expanded_artifact) if expanded_artifact else None, failure=failure,
                  scope="Owned actual-template/G HALFSPACE scalar self replacement only. Fixed complete-physical-self refinement includes scaled prior direct-cache changes, not a certified integration error bound. Actual global point self uses identical tetra4/triangle3 rules and source order [PWR volume,G volume,PWR free face,G free face]. Smooth deep-stack point diagonals remain included separately. Nonself near replacement, other-layer/native coupling and physical port closure remain outside this artifact.")
    (output/"result.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n", encoding="utf-8")
    print(json.dumps(report, allow_nan=False), flush=True)
    return 0 if passed else 2


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--stage", choices=("subset", "full"), default="subset")
    parser.add_argument("--seconds", type=float, default=900.)
    parser.add_argument("--resume", type=Path)
    parser.add_argument("--resume-sha")
    args = parser.parse_args()
    assert (args.resume is None) == (args.resume_sha is None)
    raise SystemExit(run(args.output, args.stage, args.seconds, args.resume, args.resume_sha))
