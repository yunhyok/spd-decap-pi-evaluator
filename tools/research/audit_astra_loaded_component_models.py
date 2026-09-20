"""Read the pinned saved component models; do not compile or alter the board."""
from collections import Counter
from hashlib import sha256
import json
from pathlib import Path
from zipfile import ZipFile

from spd_decap_pi._core.domain import CapModel
from spd_decap_pi._core.solver.evaluator import _cap_model

ROOT = Path(__file__).resolve().parents[2]
BUNDLE = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260902-d115b-source-plane-ownership-materialization-04\source_plane_ownership_candidate.spdpi")
OUTPUT = ROOT / "docs/evaluation-research/astra_loaded_component_models_2026-09-07-02.json"
RAIL = "ADC_VDD_075_VTRIP_SRAM/0"


def main():
    if OUTPUT.exists():
        raise FileExistsError(OUTPUT)
    with ZipFile(BUNDLE) as archive:
        content = archive.read("scenario.json")
    digest = sha256(content).hexdigest()
    assert digest == "2f5ae107221857f7f093ab8d6cbcff4c286a0c6cee5b6a9994de1c384cf39c83"
    scenario = json.loads(content)
    selected = [item for item in scenario["decaps"] if item["enabled"] and item["source_mounted"] and item["current_rail_id"] == RAIL]
    assert len(selected) == 421
    assert all(item["model_id"] == item["source_model_id"] for item in selected)
    counts = Counter(item["model_id"] for item in selected)
    assert counts == {"CAP_0402_100NF": 165, "CAP_0603_1UF": 248, "CAP_1608_10UF": 8}
    frequencies = [1e6, 1e7, 1e8, 1e9]
    models = []
    for raw in scenario["normalized_project"]["cap_models"]:
        if raw["model_id"] not in counts:
            continue
        model = _cap_model(CapModel.model_validate(raw))
        assert type(model).__name__ == "SampledImpedanceModel"
        assert len(raw["impedance"]) == 401
        assert (model.valid_min_hz, model.valid_max_hz) == (1000.0, 1e9)
        values = model.impedance(frequencies)
        assert all(value.real >= 0 for value in values)
        models.append({
            "model_id": raw["model_id"], "count": counts[raw["model_id"]],
            "actual_model_class": type(model).__name__,
            "sample_count": len(raw.get("impedance", [])),
            "valid_min_hz": model.valid_min_hz, "valid_max_hz": model.valid_max_hz,
            "sample_payload_sha256": sha256(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
            "individual_component_z_ohm": [[float(value.real), float(value.imag)] for value in values],
        })
    assert len(models) == len(counts)
    result = {
        "program": "SPD Decap PI Evaluator", "version": scenario["app_version"],
        "status": "COMPLETED_SAVED_LOADED_COMPONENT_MODEL_AUDIT", "rail_id": RAIL,
        "bundle_path": str(BUNDLE), "scenario_sha256": digest,
        "code_sha256": sha256(Path(__file__).read_bytes()).hexdigest(),
        "native_model_code": {
            str(path.relative_to(ROOT)): sha256(path.read_bytes()).hexdigest()
            for path in (ROOT / "src/spd_decap_pi/_core/solver/evaluator.py", ROOT / "src/spd_decap_pi/_core/models/impedance.py")
        },
        "frequencies_hz": frequencies, "models": models,
        "limitations": [
            "Values are individual saved component impedances, not Device response or a board-free accuracy substitute.",
            "Uses the native evaluator model selection and evaluation; no geometry, placement, coupling, fit or PowerSI response is used.",
            "This read only validates the scenario member hash, not every other archive attachment; the native driver validates the full bundle separately.",
        ],
    }
    with OUTPUT.open("x", encoding="utf-8") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()
