"""Bind surviving Run02 artifacts; never repair or overwrite the truncated JSON."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import file_digest
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
RUN = ROOT / "outputs/research/astra-native-loaded-vtrip-field-02"
PINS = {
    "field.json": "1864f9920d43fb110788d7986d86c1acac97d4fe931af5a51d5384c5dcd18830",
    "observer-driver-at-run.py": "5d09ee1ffc413215e641275b9dd1851ff716f7f86aea279a10228c099ee452cd",
    "numeric-capture-checkpoint.npz": "c54d9e73a47a862396e6f70347ad9e319a6fbc1a3310e95feeeedf2b3cf1e292",
    "raw-field-snapshot.npz": "6eac098738598a78b2068ce4f80e46fd70e17010a89bfb5949ec8a68124df4a7",
    "derived-field-observation.npz": "be5a83dff88db545de4625414e46dcc360364eb0a29077c91848a6ac805cb6c0",
    "checkpoints/frequency-1000000.json": "3b9cc9845564b6d65bcaabbe4aa9a8f0f92da19ca56e90e271495d545f00f47f",
    "checkpoints/progress.jsonl": "81d0175074b2158e2759782bfe1c6bcf97275abe3334e82c24222f48beb1a58a",
}


def complete_prefix(text: str) -> tuple[dict, str, int]:
    """Decode complete top-level values, stopping before the truncated value."""
    decoder = json.JSONDecoder()
    pos = len(text) - len(text.lstrip())
    if text[pos:pos + 1] != "{":
        raise ValueError("expected object")
    pos += 1
    result = {}
    while True:
        while pos < len(text) and text[pos].isspace():
            pos += 1
        key, pos = decoder.raw_decode(text, pos)
        if not isinstance(key, str) or key in result:
            raise ValueError("invalid or repeated key")
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if text[pos:pos + 1] != ":":
            raise ValueError("missing colon")
        pos += 1
        while pos < len(text) and text[pos].isspace():
            pos += 1
        try:
            value, pos = decoder.raw_decode(text, pos)
        except json.JSONDecodeError as exc:
            return result, key, exc.pos
        result[key] = value
        while pos < len(text) and text[pos].isspace():
            pos += 1
        if text[pos:pos + 1] != ",":
            raise ValueError("expected a truncated object with a subsequent key")
        pos += 1


def self_test() -> None:
    recovered, key, _ = complete_prefix('{"a":{"text":"},\\\""},"b":[1,2],"cut":{"x":')
    assert recovered == {"a": {"text": '},"'}, "b": [1, 2]}
    assert key == "cut"
    try:
        complete_prefix('{"a":1,"a":2,"cut":')
    except ValueError:
        pass
    else:
        raise AssertionError("duplicate keys accepted")


def recover(output: Path) -> None:
    artifacts = {}
    for name, expected in PINS.items():
        path = RUN / name
        with path.open("rb") as handle:
            actual = file_digest(handle, "sha256").hexdigest()
        if actual != expected:
            raise ValueError(f"artifact hash mismatch: {name}")
        artifacts[name] = {"path": str(path), "sha256": actual, "size_bytes": path.stat().st_size}
    prefix, incomplete, offset = complete_prefix((RUN / "field.json").read_text(encoding="utf-8"))
    if incomplete != "raw_capture" or set(prefix) != {
        "adapter", "bundle", "derived_field_snapshot", "device_port", "field",
        "frequencies_hz", "identities", "limitations", "numeric_capture_checkpoint",
        "points", "program", "rail_id",
    }:
        raise ValueError("unexpected complete prefix boundary")
    point = json.loads((RUN / "checkpoints/frequency-1000000.json").read_text(encoding="utf-8"))
    if point["device_zdd_ohm"] != prefix["points"]["1000000"]["device_zdd_ohm"]:
        raise ValueError("point checkpoint mismatch")
    if point["identities"] != prefix["identities"] or not prefix["field"]["field_power"]["pass"]:
        raise ValueError("identity or field power failure")
    progress = [json.loads(line) for line in (RUN / "checkpoints/progress.jsonl").read_text().splitlines()]
    if progress[-1]["stage"] != "watchdog_stop" or progress[-1]["reason"] is not None:
        raise ValueError("shutdown signature mismatch")
    power = prefix["field"]["field_power"]
    real_total = power["device_zdd_ohm"][0]
    result = {
        "program": prefix["program"],
        "created_utc": datetime.now(timezone.utc).isoformat(),
        "status": "RECOVERED_VERIFIED_ARTIFACT_MANIFEST_AFTER_SHUTDOWN_RACE",
        "artifacts": artifacts,
        "complete_original_top_level_values": prefix,
        "original_output": {"valid_json": False, "incomplete_key": incomplete,
                            "decode_error_character_offset": offset, "modified": False},
        "process": {"observed_exec_exit_code": 1, "clean_exit": False,
                    "final_progress": progress[-1], "native_rerun_for_recovery": False,
                    "diagnosis": "Graceful stop.set races with watchdog cancelled(); reason is null, and hard exit interrupts final JSON writing."},
        "native_model_resistive_contribution_percent": {
            name: 100 * power[key][0] / real_total for name, key in (
                ("finite_via_rl", "finite_parallel_rl_complex_contribution_ohm_at_1a"),
                ("terminations", "termination_complex_contribution_ohm_at_1a"),
                ("original_gc_partials", "partial_complex_contribution_ohm_at_1a"))},
        "limits": ["Hash manifest and exact complete-prefix recovery only; separate independent NPZ review validates arrays and physics.",
                   "No missing original JSON values, successful process status or completed raw_capture object is fabricated.",
                   "Contributions describe the present native model; they do not attribute PowerSI error or certify finite-width source contacts."],
    }
    review = RUN / "independent-artifact-review.json"
    if review.is_file():
        json.loads(review.read_text(encoding="utf-8"))
        with review.open("rb") as handle:
            result["independent_review"] = {"path": str(review), "sha256": file_digest(handle, "sha256").hexdigest()}
    with output.open("x", encoding="utf-8", newline="\n") as handle:
        json.dump(result, handle, indent=2, ensure_ascii=False, allow_nan=False)
        handle.write("\n")
    print(json.dumps({"output": str(output), "status": result["status"],
                      "resistive_percent": result["native_model_resistive_contribution_percent"]}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--output", type=Path, default=RUN / "recovered-field-manifest.json")
    args = parser.parse_args()
    self_test()
    if args.self_test:
        print("complete-prefix recovery self-test PASS")
    else:
        recover(args.output)
