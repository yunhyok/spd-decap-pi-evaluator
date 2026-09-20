"""Select frozen 1 MHz source categories compatible with explicit geometric P."""
from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import numpy as np
from scipy import sparse


ROOT = Path(__file__).resolve().parents[2]
R = ROOT / "outputs/research"
OUT = R / "astra-geometric-p-source-selector-20260912-02"
PINS = {
    R / "astra-combined-source-categories-01/driver-at-run.py": "739759114e7f459fe09293147c66f176f1313d5d2e96b4dda96f7042eadac196",
    R / "astra-combined-source-categories-01/result.json": "0715454b06165aa3b6526039363db9613c5eeb856af99e515766484654e7486a",
    R / "astra-combined-source-categories-01/combined-source-categories.npz": "f9253786da9eb88367c6bde51e78c64b647528e2856358b1fdd204ade1db6e9f",
    R / "astra-native-frequency-stamps-01/receipt.json": "4d93c4a89a02c7bc6295c27e8aa430ce3711edb2d2102576fbc7cf7535a52856",
    R / "astra-native-frequency-stamps-01/independent-review.json": "74d4476018645392c66164990ee7a44c08a09ca9594edc7df7dc95fff1ad2f94",
    R / "astra-native-frequency-stamps-01/frequency-stamps.npz": "bf2903c044423ea7429ce907567250528903c53eedddd9fffe3265a2669cba3b",
    R / "astra-native-frequency-stamps-01/source-frequency-inputs.json": "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc",
    R / "astra-native-loaded-vtrip-field-02/derived-field-observation.npz": "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0",
}


def digest(path: Path) -> str:
    with path.open("rb") as stream:
        return sha256(stream.read()).hexdigest()


def csc(archive, name: str) -> sparse.csc_matrix:
    return sparse.csc_matrix(
        (archive[name + "_data"], archive[name + "_indices"], archive[name + "_indptr"]),
        shape=tuple(archive[name + "_shape"]),
    )


def laplacian(first: np.ndarray, second: np.ndarray, value: np.ndarray, size: int) -> sparse.csc_matrix:
    rows = np.r_[first, second, first, second]
    columns = np.r_[first, second, second, first]
    data = np.r_[value, value, -value, -value]
    result = sparse.coo_matrix((data, (rows, columns)), shape=(size, size)).tocsc()
    result.sum_duplicates()
    result.eliminate_zeros()
    return result


def main() -> None:
    assert not OUT.exists()
    for path, expected in PINS.items():
        assert digest(path) == expected, path
    paths = list(PINS)
    category_result = json.loads(paths[1].read_text(encoding="utf-8"))
    stamp_receipt = json.loads(paths[3].read_text(encoding="utf-8"))
    stamp_review = json.loads(paths[4].read_text(encoding="utf-8"))
    source = json.loads(paths[6].read_text(encoding="utf-8"))
    assert category_result["status"] == "PASS_ORIGINAL_COMBINED_SOURCE_CATEGORY_PACK_NO_SOLVE"
    assert category_result["artifact_sha256"] == PINS[paths[2]]
    assert stamp_receipt["status"] == "VERIFIED_NATIVE_FREQUENCY_STAMPS_FROM_SAVED_SOURCE"
    assert not stamp_review["findings"] and stamp_review["direct_source_ownership"]["coverage_indices_exact"]

    with np.load(paths[2], allow_pickle=False) as archive:
        names = json.loads(archive["category_names_json_utf8"].tobytes())
        termination_category = csc(archive, "termination")
        retained_gc_nnz = int(len(archive["retained_gc_data"]))
    with np.load(paths[5], allow_pickle=False) as archive:
        frequencies = archive["frequencies_hz"]
        termination_by_frequency = archive["termination_admittance_s_by_frequency"]
        bindings = json.loads(archive["termination_bindings_json_utf8"].tobytes())
    with np.load(paths[7], allow_pickle=False) as archive:
        first = archive["termination_positive_active_indices"]
        second = archive["termination_negative_active_indices"]
        old_termination = archive["termination_admittance_s"]
        owner_ids = json.loads(archive["termination_owner_ids_json"].tobytes())

    assert set(names) == {"retained_gc", "termination", "l14_sheet_dc", "l14_distributed_gc", "l25_distributed_gc", "l02_distributed_gc", "l02_sheet_dc"}
    assert np.array_equal(frequencies, [1e6, 1e7, 1e8, 1e9])
    assert first.shape == second.shape == old_termination.shape == (11_050,)
    assert termination_by_frequency.shape == (4, 11_050)
    assert np.array_equal(old_termination, termination_by_frequency[0])
    assert len(bindings) == len(owner_ids) == len(source["decaps"]) == 11_050
    assert all(item["enabled"] and item["source_mounted"] for item in source["decaps"])
    assert all(item["cluster_id"] == "scenario-cap:" + item["refdes"] for item in bindings)
    assert [item["refdes"] for item in bindings] == [item["refdes"] for item in source["decaps"]]
    assert [item["model_id"] for item in bindings] == [item["model_id"] for item in source["decaps"]]
    assert owner_ids == [json.dumps(["component:" + item["refdes"]], separators=(",", ":")) for item in bindings]

    reconstructed = laplacian(first, second, old_termination, termination_category.shape[0])
    delta = reconstructed - termination_category
    termination_relative = float(np.max(abs(delta.data), initial=0.0) / max(np.max(abs(termination_category.data)), 1e-30))
    assert termination_relative < 2e-12

    removed_partials = [0, 6, 7, 13, 14]
    retained_gc_partials = [ordinal for ordinal in range(36) if ordinal not in removed_partials]
    selector = dict(
        current_include_category_names=np.asarray([
            "termination", "l14_sheet_dc", "retained_gc", "l14_distributed_gc", "l25_distributed_gc"
        ]),
        conditional_replace_category_names=np.asarray(["retained_gc", "l14_distributed_gc", "l25_distributed_gc"]),
        retained_gc_partial_ordinals=np.asarray(retained_gc_partials, dtype=np.int16),
        termination_positive_active_index=first,
        termination_negative_active_index=second,
        termination_admittance_s_1mhz=old_termination,
        termination_cluster_id=np.asarray(owner_ids),
    )
    OUT.mkdir()
    (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    artifact = OUT / "source-selector.npz"
    np.savez_compressed(artifact, **selector)
    report = dict(
        program="SPD Decap PI Evaluator",
        version="0.23.1",
        status="PASS_CONDITIONAL_EXPLICIT_P_SOURCE_CATEGORY_SELECTOR",
        driver_sha256=digest(Path(__file__)),
        artifact_sha256=digest(artifact),
        pins={str(path.relative_to(ROOT)): expected for path, expected in PINS.items()},
        decision={
            "include": {
                "termination": "11050 source-mounted decap sampled-impedance models; preserve as device internal admittance",
                "l14_sheet_dc": "conductor DC sheet resistance; outside dielectric P ownership",
            },
            "conditional_replace_after_matching_coverage": {
                "retained_gc": "replace only after all 31 native interlayer gaps and both electrode supports are represented",
                "l14_distributed_gc": "replace only after L14 charge and its corresponding opposite-electrode/dielectric response are represented",
                "l25_distributed_gc": "replace only after L25 charge and its corresponding opposite-electrode/dielectric response are represented",
            },
            "current_geometric_support_scope": "PWR TOP, selected G 3D window, and retained L02 sheet supports",
            "current_category_action": "retain all three old GC categories; no automatic removal is authorized yet",
            "missing_coverage": "other 31 native interlayer partials and L14/L25 opposite-electrode charge responses",
            "separate_owned_components": ["finite source R/L", "retained L02 geometric R/D/contact exchange", "explicit dielectric P"],
        },
        counts=dict(
            termination_branches=len(first),
            termination_category_nnz=termination_category.nnz,
            retained_gc_category_nnz=retained_gc_nnz,
            retained_gc_partial_count=len(retained_gc_partials),
        ),
        metrics=dict(termination_laplacian_reconstruction_relative=termination_relative),
        gates=dict(
            termination_source_coverage_exact=True,
            all_terminations_source_mounted_enabled=True,
            termination_category_reconstructed=termination_relative < 2e-12,
            retained_gc_partial_ownership_exact=len(retained_gc_partials) == 31,
        ),
        scope=(
            "Saved-category ownership selector only. `termination` is an actual source-mounted decap model and is "
            "compatible with an explicit conductor/free-charge P operator. `retained_gc` and redistributed L14/L25 "
            "G/C are prior interlayer dielectric owners, but replacement is permitted only when geometric P covers "
            "the same dielectric and both physical electrode supports. Present PWR/G/L02 supports do not cover the "
            "31 retained partials or L14/L25 opposite electrodes, so all three remain in the current exterior model; "
            "no automatic GC removal is authorized. L14 sheet DC remains a conductor resistance owner. No matrix assembly, "
            "factorization, field action, solve, or accuracy claim."
        ),
    )
    assert all(report["gates"].values())
    (OUT / "result.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "counts": report["counts"], "metrics": report["metrics"]}), flush=True)


if __name__ == "__main__":
    main()
