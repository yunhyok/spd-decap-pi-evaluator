"""Recover native frequency stamps from the pinned saved scenario, without geometry."""
import argparse
import gc
import json
from pathlib import Path
from types import SimpleNamespace
from zipfile import ZipFile

import numpy as np

from audit_astra_loaded_component_models import BUNDLE
from project_astra_l14_gc_mass import atomic_json, atomic_npz, require
from reconstruct_astra_native_loaded_field import _sha256_file
from probe_astra_native_loaded_voltage_field import base, _start_shutdown_safe_watchdog
from spd_decap_pi._core.domain import CapModel, StackupLayer
from spd_decap_pi._core.solver.evaluator import _cap_model
from spd_decap_pi._core.solver.layerwise_network import _gap_dispersion

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "outputs/research/astra-native-frequency-stamps-01"
FIELD = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02"
SCENARIO_SHA = "2f5ae107221857f7f093ab8d6cbcff4c286a0c6cee5b6a9994de1c384cf39c83"
FREQUENCIES = np.array([1e6, 1e7, 1e8, 1e9])
PINS = {
    "raw": (FIELD / "raw-field-snapshot.npz", "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7"),
    "derived": (FIELD / "derived-field-observation.npz", "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0"),
}


def decode(value):
    return json.loads(value.tobytes())


def run(budget, *, source_slice=None):
    from hashlib import sha256
    inputs = {}
    for name, (path, expected) in PINS.items():
        require(_sha256_file(path) == expected, f"{name} hash differs")
        inputs[name] = {"path": str(path), "sha256": expected}
    if source_slice is not None:
        source_path = source_slice
        require(_sha256_file(source_path) == "61390c0ef9c7554b83d55f91b2e81a59f9453690f783b747f2906c475be5f6dc", "saved source slice differs")
        source = json.loads(source_path.read_text(encoding="utf-8"))
        require(source["scenario_sha256"] == SCENARIO_SHA, "slice scenario identity differs")
    else:
        budget.emit("saved_scenario_member_read_start")
        with ZipFile(BUNDLE) as archive:
            content = archive.read("scenario.json")
        require(sha256(content).hexdigest() == SCENARIO_SHA, "scenario member hash differs")
        budget.emit("saved_scenario_member_read_done", bytes=len(content))
        scenario = json.loads(content)
        del content
        require(scenario["app_version"] == "0.23.1", "saved scenario version differs")
        source = {
            "scenario_sha256": SCENARIO_SHA, "bundle_path": str(BUNDLE),
            "cap_models": scenario["normalized_project"]["cap_models"],
            "stackup_layers": scenario["normalized_project"]["stackup_layers"],
            "decaps": [{key: row.get(key) for key in ("refdes", "model_id", "source_model_id", "enabled", "source_mounted", "current_rail_id")}
                       for row in scenario["decaps"]],
        }
        del scenario
        gc.collect()
        source_path = OUT / "source-frequency-inputs.json"
        atomic_json(source_path, source)
    inputs["source_slice"] = {"path": str(source_path), "sha256": _sha256_file(source_path)}
    budget.emit("source_slice_saved", decaps=len(source["decaps"]), cap_models=len(source["cap_models"]))
    by_refdes = {row["refdes"].casefold(): row for row in source["decaps"]}
    require(len(by_refdes) == len(source["decaps"]), "duplicate source refdes")
    by_model = {row["model_id"].casefold(): _cap_model(CapModel.model_validate(row)) for row in source["cap_models"]}
    require(len(by_model) == len(source["cap_models"]), "duplicate source model")
    project = SimpleNamespace(stackup_layers=[StackupLayer.model_validate(row) for row in source["stackup_layers"]])
    model_y = {key: 1 / model.impedance(FREQUENCIES) for key, model in by_model.items()}
    with np.load(PINS["derived"][0], allow_pickle=False) as saved:
        clusters, owners = decode(saved["termination_cluster_ids"]), decode(saved["termination_owner_ids_json"])
        original_y = saved["termination_admittance_s"]
        require(len(clusters) == len(owners) == len(original_y) == 11050, "native termination inventory differs")
        termination_y = np.empty((len(FREQUENCIES), len(clusters)), dtype=complex)
        termination_bindings = []
        for index, (cluster, owner_json) in enumerate(zip(clusters, owners, strict=True)):
            require(cluster.startswith("scenario-cap:"), "non-cap termination requires its original model")
            refdes = cluster.removeprefix("scenario-cap:")
            require(json.loads(owner_json) == [f"component:{refdes}"], "termination owner differs")
            row = by_refdes[refdes.casefold()]
            require(row["enabled"] is True and row["model_id"], "saved termination is not an enabled source model")
            termination_y[:, index] = model_y[row["model_id"].casefold()]
            termination_bindings.append({"index": index, "cluster_id": cluster, "refdes": refdes, "model_id": row["model_id"], "rail_id": row["current_rail_id"]})
        control_index = int(np.flatnonzero(FREQUENCIES == 1e6).item())
        termination_error = float(np.max(abs(termination_y[control_index] - original_y) / np.maximum(abs(original_y), np.finfo(float).tiny)))
        require(termination_error < 2e-12, "native 1MHz termination model reproduction failed")
    with np.load(PINS["raw"][0], allow_pickle=False) as raw:
        original_scale = raw["partial_actual_1mhz_dispersion_admittance_scale_s"]
        require(original_scale.shape == (36,), "original dielectric inventory differs")
        scales = np.empty((len(FREQUENCIES), 36), dtype=complex)
        gaps = []
        for index in range(36):
            prefix = f"partial_{index:02d}"
            upper, lower = decode(raw[prefix+"_upper_layer"]), decode(raw[prefix+"_lower_layer"])
            nominal = float(raw[prefix+"_nominal_relative_permittivity"].item())
            dk, df = _gap_dispersion(project, upper, lower).interpolate(FREQUENCIES)
            scales[:, index] = 1j * 2 * np.pi * FREQUENCIES * dk * (1-1j*np.maximum(df, 0)) / nominal
            gaps.append({"ordinal": index, "upper_layer": upper, "lower_layer": lower, "nominal_relative_permittivity": nominal, "dk": dk.tolist(), "df": df.tolist()})
        scale_error = float(np.max(abs(scales[control_index] - original_scale) / np.maximum(abs(original_scale), np.finfo(float).tiny)))
        require(scale_error < 2e-12, "native 1MHz dielectric reproduction failed")
    require(np.all(np.isfinite(termination_y)) and np.all(termination_y.real >= 0), "nonpassive/nonfinite termination")
    require(np.all(np.isfinite(scales)) and np.all(scales.real >= 0), "nonpassive/nonfinite dielectric")
    target = [row for row in termination_bindings if row["rail_id"] == "ADC_VDD_075_VTRIP_SRAM/0"]
    require(len(target) == 421, "target cap-to-termination join differs")
    snapshot = OUT / "frequency-stamps.npz"
    atomic_npz(snapshot, frequencies_hz=FREQUENCIES, termination_admittance_s_by_frequency=termination_y,
               partial_dispersion_s_per_f_by_frequency=scales,
               termination_bindings_json_utf8=np.frombuffer(json.dumps(termination_bindings, separators=(",", ":")).encode(), dtype=np.uint8))
    result = {
        "program": "SPD Decap PI Evaluator", "version": "0.23.1", "status": "VERIFIED_NATIVE_FREQUENCY_STAMPS_FROM_SAVED_SOURCE",
        "inputs": inputs, "scenario_member_sha256": SCENARIO_SHA, "frequencies_hz": FREQUENCIES.tolist(),
        "source_model_classes": {key: type(model).__name__ for key, model in by_model.items()},
        "termination_count": len(clusters), "target_cap_count": len(target), "partial_count": 36,
        "native_1mhz_termination_relative_error": termination_error, "native_1mhz_dielectric_relative_error": scale_error,
        "gap_properties": gaps, "target_termination_indices": [row["index"] for row in target],
        "output": {"path": str(snapshot), "sha256": _sha256_file(snapshot), "size_bytes": snapshot.stat().st_size},
        "script_sha256": _sha256_file(Path(__file__)),
        "native_model_code": {name: _sha256_file(ROOT / name) for name in (
            "src/spd_decap_pi/_core/solver/evaluator.py", "src/spd_decap_pi/_core/models/impedance.py",
            "src/spd_decap_pi/_core/solver/layerwise_network.py", "src/spd_decap_pi/_core/solver/modal.py")},
        "resource": {"elapsed_s": budget.elapsed(), "peak_private_bytes": budget.peak_private, "peak_working_set_bytes": budget.peak_working_set},
        "scope": "Source model evaluation and saved1MHz reproduction only; no geometry, native compilation, LU, PowerSI fitting or broadband accuracy claim.",
    }
    atomic_json(OUT / "receipt.json", result)
    budget.emit("frequency_stamps_verified", termination_error=termination_error, dielectric_error=scale_error)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--low-band", action="store_true", help="Reuse the small saved source slice at1/10/100kHz, with1MHz control")
    args = parser.parse_args()
    source_slice = None
    if args.low_band:
        source_slice = OUT / "source-frequency-inputs.json"
        OUT = ROOT / "outputs/research/astra-native-low-band-stamps-01"
        FREQUENCIES = np.array([1e3, 1e4, 1e5, 1e6])
    OUT.mkdir(exist_ok=False)
    (OUT / "driver-at-run.py").write_bytes(Path(__file__).read_bytes())
    budget = base._Budget(180., 16*2**30, OUT / "progress.jsonl")
    watchdog = _start_shutdown_safe_watchdog(budget)
    try:
        run(budget, source_slice=source_slice)
    except BaseException as exc:
        atomic_json(OUT / "failure.json", {"type": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        budget.stop.set()
        watchdog.join(timeout=2.)
