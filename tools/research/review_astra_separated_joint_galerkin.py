"""Independent saved-array review of separated joint Galerkin blocks.

This reviewer never rebuilds the fine joint sources.  It reconstructs the
two-sided proxy pairings from the saved proxy points and weights, checks the
actual placement selection, and propagates the analytic sine-tail remainder
through absolute coordinate-weight sums.
"""

from __future__ import annotations

from hashlib import sha256
import json
from math import factorial
from pathlib import Path
from time import monotonic

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "outputs/research/astra-separated-joint-galerkin-review-03"
C0 = 299_792_458.0
FREQUENCIES_HZ = (1.0e3, 1.0e6, 1.0e8)
EXPECTED = {
    "producer_driver": (
        "outputs/research/astra-separated-joint-galerkin-01/driver-at-run.py",
        "18e345712a8a6c1b521687cb31f8669379c55523a3791f5bbcb1535ea54e29ac",
    ),
    "producer_result": (
        "outputs/research/astra-separated-joint-galerkin-01/result.json",
        "d57bec549a431078cb5df048cb75fd46f73cf8407c834c57bcdf97f4d97eee08",
    ),
    "producer_artifact": (
        "outputs/research/astra-separated-joint-galerkin-01/galerkin-blocks.npz",
        "a0810a470be74f85d816705586a4cb3b7d339bcd91ba6fb5e66059264284a09c",
    ),
    "compression_result": (
        "outputs/research/astra-joint-chebyshev-sources-02/result.json",
        "5e3f87ef2b8d3905652c5618c8a6f4132070e3c7d619567f6cb8ced616ce9dd1",
    ),
    "compression_artifact": (
        "outputs/research/astra-joint-chebyshev-sources-02/compression.npz",
        "507b98a5da6186c767ebc4ed986e86ab467022b16e781f87ecc857acf9cd7910",
    ),
    "bridge_ledger": (
        "outputs/research/astra-device-power-top-bridges-02/bridge-assembly.json",
        "583c9d0da72b82bf3d8304cd9ad9b98a316dafcbdb79caa1a13c8e9f69073790",
    ),
    "lift_artifact": (
        "outputs/research/astra-shared-interface-interior-lifts-04/interior-lifts.npz",
        "9bdd7de919d425e3da295787810d11aea278b696b527314a7bf46b2fdc526e92",
    ),
}


def digest(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def relative(actual: np.ndarray, reference: np.ndarray) -> float:
    return float(np.linalg.norm(actual - reference) / max(np.linalg.norm(reference), 1.0e-300))


def stable_tail(k: float, x: np.ndarray) -> np.ndarray:
    """k*(1-sin(x)/x), accumulated without subtracting nearly equal values."""
    term = k * x * x / factorial(3)
    value = term.copy()
    for n in range(2, 13):
        term *= -(x * x) / ((2 * n) * (2 * n + 1))
        value += term
    return value


def reconstruct_blocks(
    points: np.ndarray,
    weights: np.ndarray,
    total: np.ndarray,
    displacement: np.ndarray,
    whitening: np.ndarray,
    frequency_hz: float,
) -> tuple[dict[str, np.ndarray], dict[str, float]]:
    distance = np.linalg.norm(
        points[:, None, :] + displacement[None, None, :] - points[None, :, :], axis=2
    )
    if not float(distance.min()) > 0.0:
        raise AssertionError("separated placement contains a zero-distance proxy pair")
    k = 2.0 * np.pi * frequency_hz / C0
    x = k * distance
    if not float(x.max()) < 1.0:
        raise AssertionError("alternating sine-tail bound requires kR < 1")

    raw_current = weights[:, :15].reshape(len(points), 5, 3)
    current = np.einsum("pmd,mn->pnd", raw_current, whitening)
    if np.max(np.abs(current.imag)) > 2.0e-15 * max(np.max(np.abs(current.real)), 1.0):
        raise AssertionError("saved real lift coordinates acquired an imaginary component")
    current = current.real
    charge = weights[:, 15:]
    monopole_j = whitening.T @ total[:15].reshape(5, 3)

    real_kernel = np.cos(x) / distance
    tail_kernel = stable_tail(k, x)
    current_real = sum(
        current[:, :, axis].T @ real_kernel @ current[:, :, axis] for axis in range(3)
    ) * 1.0e-7
    current_tail = sum(
        current[:, :, axis].T @ tail_kernel @ current[:, :, axis] for axis in range(3)
    ) * 1.0e-7
    charge_real = charge.T @ real_kernel @ charge
    charge_tail = charge.T @ tail_kernel @ charge
    blocks = {
        "current_real": current_real,
        "charge_real": charge_real,
        "current_tail": current_tail,
        "charge_tail": charge_tail,
        "current_imaginary": current_tail - 1.0e-7 * k * (monopole_j @ monopole_j.T),
        "charge_imaginary": charge_tail - k * np.outer(total[15:], total[15:]),
    }

    # For 0 <= x < 1 the first omitted alternating term bounds the remainder:
    # |k*(1-sin(x)/x)-tail_12| <= k*x^26/27!.
    kernel_remainder = k * float(x.max()) ** 26 / factorial(27)
    current_l1 = np.sum(np.abs(current), axis=0)  # coordinate x vector-component
    current_bound_matrix = 1.0e-7 * kernel_remainder * sum(
        np.outer(current_l1[:, axis], current_l1[:, axis]) for axis in range(3)
    )
    charge_l1 = np.sum(np.abs(charge), axis=0)
    charge_bound_matrix = kernel_remainder * np.outer(charge_l1, charge_l1)
    current_bound = float(np.linalg.norm(current_bound_matrix))
    charge_bound = float(np.linalg.norm(charge_bound_matrix))
    bounds = {
        "frequency_hz": frequency_hz,
        "kR_max": float(x.max()),
        "kernel_remainder_max_per_m": kernel_remainder,
        "current_block_frobenius_bound_h": current_bound,
        "charge_block_frobenius_bound_per_m": charge_bound,
        "current_bound_over_full_block": current_bound
        / max(float(np.linalg.norm(current_real + 1j * blocks["current_imaginary"])), 1.0e-300),
        "charge_bound_over_full_block": charge_bound
        / max(float(np.linalg.norm(charge_real + 1j * blocks["charge_imaginary"])), 1.0e-300),
        "current_bound_over_tail_component": current_bound
        / max(float(np.linalg.norm(current_tail)), 1.0e-300),
        "charge_bound_over_tail_component": charge_bound
        / max(float(np.linalg.norm(charge_tail)), 1.0e-300),
    }
    return blocks, bounds


def main() -> int:
    started = monotonic()
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    pins: dict[str, str] = {}
    for name, (relative_path, expected) in EXPECTED.items():
        actual = digest(ROOT / relative_path)
        if actual != expected:
            raise AssertionError(f"pin mismatch for {relative_path}: {actual}")
        pins[name] = actual

    producer = json.loads((ROOT / EXPECTED["producer_result"][0]).read_text(encoding="utf-8"))
    if producer["status"] != "ACCEPT_SAMPLED_SEPARATED_GALERKIN_REFINEMENT":
        raise AssertionError("producer did not pass its scoped saved gate")
    if tuple(producer["frequencies_hz"]) != FREQUENCIES_HZ:
        raise AssertionError("unexpected frequency order")

    with np.load(ROOT / EXPECTED["producer_artifact"][0]) as data:
        saved = {name: data[name].copy() for name in data.files}
    with np.load(ROOT / EXPECTED["compression_artifact"][0]) as data:
        lower, upper = data["lower_m"].copy(), data["upper_m"].copy()
        compression_total = data["authoritative_monopole"][12:].copy()
    with np.load(ROOT / EXPECTED["lift_artifact"][0]) as data:
        gram = data["energy_gram_ohm"].copy()

    total = saved["authoritative_monopole"]
    if not np.array_equal(total, compression_total):
        raise AssertionError("Galerkin monopoles differ from pinned compression source")
    whitening = saved["current_R_whitening"]
    whitening_error = float(np.max(np.abs(whitening.T @ gram @ whitening - np.eye(5))))
    if whitening_error > 1.0e-12:
        raise AssertionError("saved current coordinates are not R-whitened")

    ledger = json.loads((ROOT / EXPECTED["bridge_ledger"][0]).read_text(encoding="utf-8"))
    instances = ledger["instances"]
    locations = np.asarray([item["translation_xy_um"] for item in instances], dtype=float) * 1.0e-6
    delta = locations - locations[0]
    lengths = np.linalg.norm(delta, axis=1)
    radius = float(np.linalg.norm(upper - lower) / 2.0)
    admissible = lengths >= 4.0 * radius
    masks = (
        admissible,
        admissible & (np.abs(delta[:, 1]) < 1.0e-12),
        admissible & (np.abs(delta[:, 1]) > 1.0e-12),
    )
    selected = sorted(set([int(np.argmin(np.where(mask, lengths, np.inf))) for mask in masks] + [int(np.argmax(lengths))]))
    expected_displacements = np.column_stack((delta[selected], np.zeros(len(selected))))
    placement_error_m = float(np.max(np.abs(expected_displacements - saved["pair_displacements_m"])))
    if placement_error_m > 2.0e-18:
        raise AssertionError("saved pair placements do not match the pinned actual bridge ledger")
    for record, selected_index in zip(producer["pairs"], selected, strict=True):
        if record["observer"]["trace_id"] != instances[selected_index]["trace_id"]:
            raise AssertionError("saved observer trace does not match independent placement selection")

    block_names = (
        "current_real",
        "charge_real",
        "current_tail",
        "charge_tail",
        "current_imaginary",
        "charge_imaginary",
    )
    block_reconstruction: dict[str, float] = {}
    block_reconstruction_full_scale: dict[str, float] = {}
    proxy_monopole_errors: dict[str, float] = {}
    bound_rows: list[dict[str, float | int]] = []
    rebuilt_by_order: dict[int, dict[str, np.ndarray]] = {}
    for order in (8, 10, 12):
        points = saved[f"points_n{order}"]
        weights = saved[f"weights_n{order}"]
        proxy_monopole_errors[str(order)] = float(np.max(np.abs(np.sum(weights, axis=0) - total)))
        rebuilt = {name: [] for name in block_names}
        for pair_index, displacement in enumerate(saved["pair_displacements_m"]):
            for frequency_index, frequency in enumerate(FREQUENCIES_HZ):
                blocks, bounds = reconstruct_blocks(points, weights, total, displacement, whitening, frequency)
                for name in block_names:
                    rebuilt[name].append(blocks[name])
                bound_rows.append(
                    dict(order=order, pair_index=pair_index, frequency_index=frequency_index, **bounds)
                )
        rebuilt_by_order[order] = {name: np.asarray(values) for name, values in rebuilt.items()}
        for name in block_names:
            key = f"{name}_n{order}"
            rebuilt_block = rebuilt_by_order[order][name]
            saved_block = saved[key]
            block_reconstruction[key] = relative(rebuilt_block, saved_block)
            family = "current" if name.startswith("current") else "charge"
            full_scale = float(np.linalg.norm(saved[f"{family}_real_n{order}"]))
            block_reconstruction_full_scale[key] = float(
                np.linalg.norm(rebuilt_block - saved_block) / max(full_scale, 1.0e-300)
            )

    recomputed_errors: dict[str, dict[str, list[float]]] = {}
    result_error_difference = 0.0
    for coarse, fine in ((8, 10), (10, 12), (8, 12)):
        comparison = f"n{coarse}_n{fine}"
        recomputed_errors[comparison] = {}
        for name in block_names:
            values = [
                relative(a, b)
                for a, b in zip(saved[f"{name}_n{coarse}"], saved[f"{name}_n{fine}"], strict=True)
            ]
            recomputed_errors[comparison][name] = values
            result_error_difference = max(
                result_error_difference,
                float(np.max(np.abs(np.asarray(values) - np.asarray(producer["relative_errors"][comparison][name])))),
            )

    reverse_blocks, reverse_bound = reconstruct_blocks(
        saved["points_n8"],
        saved["weights_n8"],
        total,
        -saved["pair_displacements_m"][0],
        whitening,
        1.0e8,
    )
    reverse_reconstruction = {
        name: relative(reverse_blocks[name], saved[f"reverse_{name}"]) for name in block_names
    }
    reciprocity = {
        name: relative(saved[f"reverse_{name}"].T, saved[f"{name}_n8"][2]) for name in block_names
    }
    reciprocity_difference = max(
        abs(reciprocity[name] - producer["reciprocity"][name]) for name in block_names
    )

    max_block_reconstruction = max(block_reconstruction.values())
    max_block_reconstruction_full_scale = max(block_reconstruction_full_scale.values())
    component_reconstruction_maxima = {
        name: max(
            block_reconstruction[f"{name}_n{order}"] for order in (8, 10, 12)
        )
        for name in block_names
    }
    max_proxy_monopole_error = max(proxy_monopole_errors.values())
    max_current_remainder_h = max(row["current_block_frobenius_bound_h"] for row in bound_rows)
    max_charge_remainder_per_m = max(row["charge_block_frobenius_bound_per_m"] for row in bound_rows)
    max_current_full_relative = max(row["current_bound_over_full_block"] for row in bound_rows)
    max_charge_full_relative = max(row["charge_bound_over_full_block"] for row in bound_rows)
    max_current_tail_relative = max(row["current_bound_over_tail_component"] for row in bound_rows)
    max_charge_tail_relative = max(row["charge_bound_over_tail_component"] for row in bound_rows)
    max_kR = max(row["kR_max"] for row in bound_rows)

    gates = {
        "pins": True,
        "placement_error_m_lt_2e-18": placement_error_m < 2.0e-18,
        "whitening_error_lt_1e-12": whitening_error < 1.0e-12,
        "proxy_monopole_error_lt_2e-13": max_proxy_monopole_error < 2.0e-13,
        # Tail blocks can be many orders below the full Green block.  Gate the
        # arithmetic disagreement against the corresponding saved real block;
        # retain the raw small-block relative number as an informational metric.
        "saved_block_reconstruction_full_scale_lt_5e-13": max_block_reconstruction_full_scale
        < 5.0e-13,
        **{
            f"saved_{name}_component_relative_lt_1e-10": value < 1.0e-10
            for name, value in component_reconstruction_maxima.items()
        },
        "reported_error_table_lt_1e-15": result_error_difference < 1.0e-15,
        "reverse_reconstruction_lt_2e-13": max(reverse_reconstruction.values()) < 2.0e-13,
        "reported_reciprocity_lt_1e-15": reciprocity_difference < 1.0e-15,
        "all_actual_kR_lt_1": max_kR < 1.0,
        "tail_bound_relative_full_lt_1e-12": max(max_current_full_relative, max_charge_full_relative) < 1.0e-12,
    }
    accepted = all(gates.values())

    OUTPUT.mkdir(parents=True)
    driver = OUTPUT / "driver-at-run.py"
    driver.write_bytes(Path(__file__).read_bytes())
    arrays_path = OUTPUT / "review-arrays.npz"
    np.savez_compressed(
        arrays_path,
        placement_indices=np.asarray(selected, dtype=np.int64),
        expected_displacements_m=expected_displacements,
        bound_rows=np.asarray(
            [
                [
                    row["order"],
                    row["pair_index"],
                    row["frequency_index"],
                    row["frequency_hz"],
                    row["kR_max"],
                    row["kernel_remainder_max_per_m"],
                    row["current_block_frobenius_bound_h"],
                    row["charge_block_frobenius_bound_per_m"],
                    row["current_bound_over_full_block"],
                    row["charge_bound_over_full_block"],
                    row["current_bound_over_tail_component"],
                    row["charge_bound_over_tail_component"],
                ]
                for row in bound_rows
            ],
            dtype=float,
        ),
    )
    result = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_WITH_SCOPE" if accepted else "STOP_INDEPENDENT_SAVED_GALERKIN_REVIEW",
        "reviewer_sha256": digest(Path(__file__)),
        "review_arrays_sha256": digest(arrays_path),
        "pins": pins,
        "checks": {
            "selected_bridge_indices": selected,
            "pair_placement_max_abs_error_m": placement_error_m,
            "R_whitening_max_abs_error": whitening_error,
            "proxy_monopole_max_abs_error": max_proxy_monopole_error,
            "saved_block_reconstruction_max_relative": max_block_reconstruction,
            "saved_block_reconstruction_max_relative_to_corresponding_real_block": max_block_reconstruction_full_scale,
            "saved_block_reconstruction_raw_by_block": block_reconstruction,
            "saved_block_reconstruction_full_scale_by_block": block_reconstruction_full_scale,
            "saved_component_reconstruction_max_relative": component_reconstruction_maxima,
            "reported_refinement_table_max_abs_error": result_error_difference,
            "reverse_block_reconstruction_max_relative": max(reverse_reconstruction.values()),
            "reported_reciprocity_max_abs_error": reciprocity_difference,
            "n8_q3_to_n12_q4_current_max_relative": max(
                recomputed_errors["n8_n12"]["current_real"]
            ),
            "n8_q3_to_n12_q4_charge_max_relative": max(
                recomputed_errors["n8_n12"]["charge_real"]
            ),
            "reverse_reciprocity_max_relative": max(reciprocity.values()),
            "maximum_actual_kR": max_kR,
            "sine_tail_first_omitted_term": "k*(kR)^26/27!",
            "sine_tail_current_max_absolute_frobenius_bound_h": max_current_remainder_h,
            "sine_tail_charge_max_absolute_frobenius_bound_per_m": max_charge_remainder_per_m,
            "sine_tail_current_max_bound_over_full_block": max_current_full_relative,
            "sine_tail_charge_max_bound_over_full_block": max_charge_full_relative,
            "sine_tail_current_max_bound_over_tail_component": max_current_tail_relative,
            "sine_tail_charge_max_bound_over_tail_component": max_charge_tail_relative,
            "sine_tail_bound_samples": len(bound_rows),
        },
        "gates": gates,
        "elapsed_s": monotonic() - started,
        "scope": (
            "Independent arithmetic on the frozen proxy points and weights for three selected actual "
            "separated bridge placements. It rebuilds the R-whitened 5x5 current and independent 2x2 "
            "charge bilinear blocks, refinement table, and one reversed pair. The degree-12 stable "
            "sine-tail remainder is bounded analytically and propagated through absolute proxy-weight "
            "sums. This does not independently rebuild the fine tetra/face source quadrature, bound all "
            "placements, certify weak radiation combinations, or qualify near/self, contact, field, Z, "
            "complete rail/return, board, or PowerSI accuracy."
        ),
        "supersedes": (
            "Review-01 stopped because it divided a 2.59e-18 absolute charge-tail arithmetic "
            "difference by the much smaller tail block itself. Review-02 preserved that raw "
            "6.26e-12 ratio and gated the difference against the corresponding full real Green "
            "block. Review-03 additionally gates each real, stable-tail, and leading-term-restored "
            "imaginary component reconstruction at 1e-10 relative. No producer data were changed."
        ),
    }
    result_path = OUTPUT / "independent-review.json"
    result_path.write_text(json.dumps(result, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if accepted else 2


if __name__ == "__main__":
    raise SystemExit(main())
