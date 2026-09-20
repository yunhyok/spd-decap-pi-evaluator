"""SPD Decap PI Evaluator v0.23.1: saved source scalar-kernel review.

Reconstruct normalization, pairing, moments, active-face ownership, and the
retarded series from frozen arrays.  No Green integration or field solve.
"""
from __future__ import annotations

from hashlib import sha256
from itertools import combinations
from math import factorial, pi
from pathlib import Path
import argparse
import json

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
EPS0 = 8.8541878128e-12
MU0 = 4e-7 * pi
PINS = {
    "producer": ("tools/research/prepare_astra_source_potential_kernels.py", "1d4d24f6c0274885951422ff38695221438e8d5439033288abe3532f2e38eff1"),
    "result": ("outputs/research/astra-source-potential-kernels-01/result.json", "a3e0164ebdb40f79cdbe3e3891c040171171e8ef0fcadb52f4d0d0255d82c0f2"),
    "low": ("outputs/research/astra-source-potential-kernels-01/kernels-v14-f28.npz", "6f1e9d601acb36cc0db23603fcf21b8a821f5c638567dd37dd66a61d4d485f61"),
    "high": ("outputs/research/astra-source-potential-kernels-01/kernels-v20-f36.npz", "bffa3704cff006606f987911cb4121efaa39a8bf6c16cf704a7b4600afbcf437"),
    "contacts": ("outputs/research/astra-source-potential-contacts-01/contacts.npz", "55294d51fdb4da642499e38e7e613a226d9fc5561d2159e73f38de1f853aa21a"),
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            value.update(block)
    return value.hexdigest()


def scaled_max(delta: np.ndarray, reference: np.ndarray) -> float:
    roots = np.sqrt(np.diag(reference))
    return float(np.max(np.abs(delta) / roots[:, None] / roots[None, :]))


def simplex_distance2(entities: np.ndarray) -> np.ndarray:
    centers = entities.mean(axis=1)
    count = entities.shape[1]
    variance = np.sum((entities - centers[:, None]) ** 2, axis=(1, 2)) / (count * (count + 1))
    return np.sum((centers[:, None] - centers[None, :]) ** 2, axis=2) + variance[:, None] + variance[None, :]


def face_owner_counts(tetrahedra: np.ndarray, triangles: np.ndarray) -> np.ndarray:
    def key(vertices: np.ndarray) -> tuple:
        return tuple(sorted(tuple(float(x) for x in row) for row in vertices))

    ids = {key(face): i for i, face in enumerate(triangles)}
    counts = np.zeros(len(triangles), dtype=int)
    for tetrahedron in tetrahedra:
        for indices in combinations(range(4), 3):
            counts[ids[key(tetrahedron[list(indices)])]] += 1
    return counts


def inspect_bundle(path: Path, volume_entities: np.ndarray, face_entities: np.ndarray) -> tuple[dict, dict]:
    with np.load(path, allow_pickle=False) as data:
        arrays = {name: data[name] for name in data.files}
    required = {
        "raw_normalized_volume_powers": (9, 18, 18),
        "raw_normalized_face_powers": (9, 32, 32),
        "normalized_volume_powers": (9, 18, 18),
        "normalized_face_powers": (9, 32, 32),
        "volume_green_per_m": (6, 18, 18),
        "face_green_per_m": (6, 32, 32),
        "active_charge_faces": (32,),
        "frequencies_hz": (6,),
        "length_scale_m": (),
    }
    assert set(arrays) == set(required)
    for name, shape in required.items():
        assert arrays[name].shape == shape, (name, arrays[name].shape)
        assert np.all(np.isfinite(arrays[name])), name
    length = float(arrays["length_scale_m"])
    metrics = {}
    for kind, entities in (("volume", volume_entities), ("face", face_entities)):
        raw = arrays[f"raw_normalized_{kind}_powers"]
        paired = arrays[f"normalized_{kind}_powers"]
        expected = (raw + raw.transpose(0, 2, 1)) / 2
        expected[1] = 1.0
        metrics[f"{kind}_paired_saved_absolute"] = float(np.max(np.abs(paired - expected)))
        metrics[f"{kind}_raw_r0_absolute"] = float(np.max(np.abs(raw[1] - 1)))
        metrics[f"{kind}_saved_r0_absolute"] = float(np.max(np.abs(paired[1] - 1)))
        distance2 = simplex_distance2(entities / length)
        metrics[f"{kind}_r2_scaled"] = float(np.max(np.abs(paired[3] - distance2)) / np.max(distance2))
        metrics[f"{kind}_raw_static_orientation_scaled"] = scaled_max(raw[0] - raw[0].T, paired[0])
        roots = np.sqrt(np.diag(paired[0]))
        metrics[f"{kind}_minimum_normalized_static_eigenvalue"] = float(
            np.linalg.eigvalsh(paired[0] / roots[:, None] / roots[None, :]).min()
        )
        frequency = arrays["frequencies_hz"]
        reconstructed = []
        omitted_lead = []
        for value in frequency:
            k = 2 * pi * float(value) * np.sqrt(MU0 * EPS0)
            terms = sum(((-1j * k * length) ** n / factorial(n)) * paired[n] for n in range(1, 9))
            reconstructed.append((paired[0] + terms) / length)
            omitted_lead.append((paired[0] + terms + 1j * k * length * paired[1]) / length)
        saved = arrays[f"{kind}_green_per_m"]
        reconstructed = np.asarray(reconstructed)
        omitted_lead = np.asarray(omitted_lead)
        metrics[f"{kind}_retarded_reconstruction_relative"] = float(
            np.max(np.abs(saved - reconstructed)) / np.max(np.abs(saved))
        )
        lead = saved - omitted_lead
        target = -1j * 2 * pi * frequency * np.sqrt(MU0 * EPS0)
        metrics[f"{kind}_leading_minus_ik_absolute_per_m"] = float(
            np.max(np.abs(lead - target[:, None, None]))
        )
    return arrays, metrics


def run(output: Path) -> None:
    for path, expected in PINS.values():
        assert digest(ROOT / path) == expected, path
    result = json.loads((ROOT / PINS["result"][0]).read_text(encoding="utf-8"))
    assert result["status"] == "PASS_SOURCE_POTENTIAL_SCALAR_KERNELS"
    assert result["program"] == "SPD Decap PI Evaluator" and result["version"] == "0.23.1"
    with np.load(ROOT / PINS["contacts"][0], allow_pickle=False) as source:
        tetrahedra = source["tetrahedra_m"]
        triangles = source["triangles_m"]
        active = source["active_charge_faces"]
        contact = source["contact_faces"]
        noncontact = source["noncontact_faces"]
        divergence = source["distributional_face_divergence"]
        transform = source["real_current_transform"]
    assert len(active) == 32 and len(contact) == 8 and len(noncontact) == 24
    assert np.array_equal(np.sort(np.r_[contact, noncontact]), np.sort(active))
    assert len(np.intersect1d(contact, noncontact)) == 0
    active_from_current = np.flatnonzero(np.any(divergence @ transform, axis=1))
    assert np.array_equal(active_from_current, active)
    owners = face_owner_counts(tetrahedra, triangles)
    assert np.all(owners[contact] == 1)
    assert (int(np.count_nonzero(owners[active] == 1)), int(np.count_nonzero(owners[active] == 2))) == (28, 4)

    low, low_metrics = inspect_bundle(ROOT / PINS["low"][0], tetrahedra, triangles[active])
    high, high_metrics = inspect_bundle(ROOT / PINS["high"][0], tetrahedra, triangles[active])
    for bundle in (low, high):
        assert np.array_equal(bundle["active_charge_faces"], active)
        assert np.array_equal(bundle["frequencies_hz"], np.array([1e3, 1e4, 1e5, 1e6, 1e7, 1e8]))
    assert float(low["length_scale_m"]) == float(high["length_scale_m"])
    length = float(high["length_scale_m"])
    assert abs(length - np.ptp(tetrahedra.reshape(-1, 3), axis=0).max()) < 1e-18
    refinement = {
        kind: scaled_max(
            high[f"normalized_{kind}_powers"][0] - low[f"normalized_{kind}_powers"][0],
            high[f"normalized_{kind}_powers"][0],
        )
        for kind in ("volume", "face")
    }
    reported = result["cases"][-1]
    assert abs(refinement["volume"] - reported["static_refinement_diagonal_scaled"]["volume"]) < 1e-15
    assert abs(refinement["face"] - reported["static_refinement_diagonal_scaled"]["face"]) < 1e-15
    dimensions = np.ptp(tetrahedra.reshape(-1, 3), axis=0)
    radius = float(np.linalg.norm(dimensions))
    kmax = 2 * pi * 1e8 * np.sqrt(MU0 * EPS0)
    tail_bound = float(np.exp(kmax * radius) * (kmax * radius) ** 9 / factorial(9) / radius)
    assert abs(tail_bound - result["maximum_frequency_taylor_absolute_bound_per_m"]) < 1e-45

    maxima = {**{f"low_{k}": v for k, v in low_metrics.items()}, **{f"high_{k}": v for k, v in high_metrics.items()}}
    assert max(v for k, v in maxima.items() if k.endswith("paired_saved_absolute")) == 0
    assert max(v for k, v in maxima.items() if k.endswith("retarded_reconstruction_relative")) < 1e-15
    assert max(v for k, v in maxima.items() if k.endswith("leading_minus_ik_absolute_per_m")) < 1e-10
    assert max(v for k, v in maxima.items() if k.endswith("r2_scaled")) < 1e-13
    assert min(v for k, v in maxima.items() if k.endswith("minimum_normalized_static_eigenvalue")) > 1e-8

    review = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SAVED_SOURCE_POTENTIAL_SCALAR_KERNELS_WITH_SCOPE",
        "reviewer_sha256": digest(Path(__file__)),
        "pins": {key: list(value) for key, value in PINS.items()},
        "gates": {
            "pins_and_schema": True,
            "normalization_and_exact_moments": True,
            "paired_and_raw_policy": True,
            "active_face_ownership": True,
            "retarded_series_and_leading_minus_ik": True,
            "refinement_and_static_positive": True,
        },
        "face_ownership": {
            "active": 32,
            "contact_boundary": 8,
            "noncontact_boundary": 20,
            "noncontact_internal_interface": 4,
        },
        "static_refinement_diagonal_scaled": refinement,
        "maximum_frequency_taylor_absolute_bound_per_m": tail_bound,
        "maxima": maxima,
        "scope": (
            "Read-only reconstruction from frozen result/contact/kernel arrays. No Green integral or field system is rerun. "
            "Normalized mean-density R0/R2 moments, raw-to-paired matrices, static refinement/positivity, active face ownership, "
            "and the full degree-8 retarded series including exact -ik are checked. The producer-reported Gaussian controls are not "
            "reintegrated here. Their boundary-polarization vectors have zero entries on the four internal interface faces, so those "
            "rows are independently covered here only by moments, pairing, refinement, and static positivity. The Taylor number is "
            "an entrywise scalar series remainder bound, not a total operator/field error. No terminal sign, material interface, "
            "external lead, continuum convergence, board Z, or PowerSI accuracy approval."
        ),
    }
    output.mkdir(parents=True)
    with (output / "independent-review.json").open("x", encoding="utf-8") as stream:
        json.dump(review, stream, indent=2, allow_nan=False)
        stream.write("\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    destination = parser.parse_args().output.resolve()
    if destination.exists():
        raise FileExistsError(destination)
    run(destination)
