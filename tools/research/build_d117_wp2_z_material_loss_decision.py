"""SPD Decap PI Evaluator v0.23.1: static D117 WP2 material-loss decision."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sys
import tempfile
from decimal import Decimal
from pathlib import Path
from typing import Any


PRODUCT = "SPD Decap PI Evaluator"
VERSION = "0.23.1"
DECISION_ID = "D117-WP2-Z-MATERIAL-LOSS-HQ-01"
ROOT = Path(__file__).resolve().parents[2]
D103_PATH = Path(r"D:\SPD-Decap-PI-Evaluator-W7\8177f7a82715979652d7dcb3cd7bfd2770746133\260729-d103-source-stackup-material-receipt-02\stackup_material_receipt.json")
D103_SIZE = 204_735
D103_SHA256 = "4ea63cf86f6b1f4e8d56eead82033606cd2a2f9b6fae1b14e857a51e4c0749f9"
PINNED_FILES = (
    (Path("tools/research/extract_source_stackup_material_receipt.py"), 10_322, "2e0d145acdd01cb91d87246aaeaea2ec693127ed7f3678b1222465a75fe6ce70"),
    (Path("src/spd_decap_pi/_core/solver/modal.py"), 100_411, "d72bd6333895520076bf956a616b87c6be3f087fe0c3281867c36dbd10ddde4b"),
    (Path("src/spd_decap_pi/_core/solver/mfdm.py"), 73_553, "b7b140686babc17e7a31097490ac009685b15cf12782d0a71498aeceb5290a80"),
    (Path("src/spd_decap_pi/_core/solver/surface_patch_plane.py"), 66_417, "b43de84a7f7756888eeddea42e4c9524a66661e174c2d951f49202dbffd3032c"),
)
Z_VALUES = (1780, 1812, 1882, 1917, 1947, 1967, 1997, 2017, 2047, 2067, 2097, 2117)
MATERIAL_POINTS = {
    "ABF-GL102": (Decimal("3.4"), Decimal("0.0041"), "material:ABF-GL102"),
    "EL190T": (Decimal("4.7"), Decimal("0.01"), "material:EL190T"),
}
COPPER_CONDUCTIVITY = 59_590_000
DEPTH_BASIS = "cumulative source-order thickness; not absolute source z"


class Refusal(RuntimeError):
    """Raised when the pinned static contract cannot be proven."""


def _file_identity(path: Path) -> tuple[int, str]:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"required file is missing or not regular: {path}")
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1 << 20), b""):
                digest.update(block)
                size += len(block)
    except OSError as exc:
        raise Refusal(f"required file read failed: {path}") from exc
    return size, digest.hexdigest()


def _read_pinned_json(path: Path, expected_size: int, expected_sha256: str) -> dict[str, Any]:
    if path.is_symlink() or not path.is_file():
        raise Refusal(f"pinned receipt is missing or not regular: {path}")
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise Refusal("pinned D103 receipt read failed") from exc
    if len(data) != expected_size or hashlib.sha256(data).hexdigest() != expected_sha256:
        raise Refusal("pinned D103 receipt size/hash mismatch")
    return _strict_json_object(data)


def _strict_json_object(data: bytes) -> dict[str, Any]:
    def unique_pairs(items: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate JSON key")
            result[key] = value
        return result

    def reject_constant(value: str) -> None:
        raise ValueError(f"non-finite JSON constant: {value}")

    def finite_float(value: str) -> float:
        number = float(value)
        if not math.isfinite(number):
            raise ValueError("non-finite JSON number")
        return number

    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=unique_pairs,
            parse_constant=reject_constant,
            parse_float=finite_float,
        )
    except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
        raise Refusal("D103 receipt is not strict JSON") from exc
    if type(value) is not dict:
        raise Refusal("D103 receipt must be a JSON object")
    return value


def _number(value: Any, label: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(float(value)):
        raise Refusal(f"{label} is not finite")
    return float(value)


def _decimal(value: Any, label: str) -> Decimal:
    try:
        result = Decimal(str(value))
    except Exception as exc:
        raise Refusal(f"{label} is not decimal") from exc
    if not result.is_finite():
        raise Refusal(f"{label} is not finite")
    return result


def _source_identity(receipt: dict[str, Any]) -> dict[str, Any]:
    if receipt.get("status") != "PASS" or receipt.get("version") != VERSION:
        raise Refusal("D103 status/version mismatch")
    if receipt.get("derived_depth_basis") != DEPTH_BASIS:
        raise Refusal("D103 depth basis mismatch")
    source = receipt.get("source")
    if type(source) is not dict:
        raise Refusal("D103 embedded source identity is missing")
    path, sha256, size = source.get("path"), source.get("sha256"), source.get("size_bytes")
    if type(path) is not str or type(sha256) is not str or len(sha256) != 64 or type(size) is not int or size <= 0:
        raise Refusal("D103 embedded source identity is malformed")
    return {"path": path, "sha256": sha256.lower(), "size_bytes": size}


def _layer_view(row: Any) -> dict[str, Any]:
    if type(row) is not dict:
        raise Refusal("D103 stackup row is malformed")
    ordinal = row.get("ordinal")
    if type(ordinal) is not int:
        raise Refusal("D103 layer ordinal is malformed")
    name, kind, material = row.get("layer_name"), row.get("layer_kind"), row.get("material_name")
    raw_hash = row.get("raw_layer_source_record_sha256")
    if any(type(value) is not str or not value for value in (name, kind, material)):
        raise Refusal("D103 layer labels are malformed")
    if type(raw_hash) is not str or len(raw_hash) != 64:
        raise Refusal("D103 raw-layer hash is malformed")
    thickness = _number(row.get("thickness_um"), "D103 layer thickness")
    if thickness <= 0:
        raise Refusal("D103 layer thickness is not positive")
    return {
        "ordinal": ordinal,
        "name": name,
        "kind": kind,
        "material": material,
        "thickness_um": thickness,
        "raw_layer_source_record_sha256": raw_hash.lower(),
    }


def _transitions(receipt: dict[str, Any]) -> list[dict[str, Any]]:
    rows = receipt.get("stackup_layers")
    if type(rows) is not list:
        raise Refusal("D103 stackup layers are missing")
    by_ordinal: dict[int, dict[str, Any]] = {}
    for row in rows:
        if type(row) is dict and type(row.get("ordinal")) is int:
            by_ordinal[row["ordinal"]] = row
    transitions: list[dict[str, Any]] = []
    for index, z in enumerate(Z_VALUES):
        preceding_ordinal = 51 + index
        following_ordinal = preceding_ordinal + 1
        preceding = by_ordinal.get(preceding_ordinal)
        following = by_ordinal.get(following_ordinal)
        if preceding is None or following is None:
            raise Refusal("D103 51..63 source-order window is incomplete")
        preceding_depth = preceding.get("depth_from_stack_top_um")
        following_depth = following.get("depth_from_stack_top_um")
        if type(preceding_depth) is not dict or type(following_depth) is not dict:
            raise Refusal("D103 layer depth is missing")
        if _decimal(preceding_depth.get("bottom"), "preceding depth") != Decimal(z) or _decimal(following_depth.get("top"), "following depth") != Decimal(z):
            raise Refusal("D103 face z does not match the pinned source-order boundary")
        transitions.append(
            {
                "receipt_relative_z_um": z,
                "preceding_source_layer": _layer_view(preceding),
                "following_source_layer": _layer_view(following),
            }
        )
    if len(transitions) != 12:
        raise Refusal("D103 transition count mismatch")
    return transitions


def _validate_material_points(receipt: dict[str, Any]) -> None:
    points = receipt.get("dielectric_points")
    if type(points) is not list:
        raise Refusal("D103 dielectric points are missing")
    for material, (epsilon_r, tan_delta, source_id) in MATERIAL_POINTS.items():
        if not any(
            type(point) is dict
            and point.get("epsilon_source_record_id") == source_id
            and _decimal(point.get("frequency_hz"), "material frequency") == Decimal("1000000")
            and _decimal(point.get("epsilon_r"), "epsilon_r") == epsilon_r
            and _decimal(point.get("loss_tangent"), "loss tangent") == tan_delta
            for point in points
        ):
            raise Refusal(f"D103 1 MHz material point missing: {material}")
    copper_rows = receipt.get("stackup_layers")
    if type(copper_rows) is not list or not any(
        type(row) is dict
        and row.get("material_name") == "COPPER"
        and row.get("conductivity_source_record_id") == "material:COPPER"
        and _decimal(row.get("conductivity_s_per_m"), "copper conductivity") == Decimal(COPPER_CONDUCTIVITY)
        for row in copper_rows
    ):
        raise Refusal("D103 copper conductivity point missing")


def _loss_contract() -> dict[str, Any]:
    materials: dict[str, Any] = {}
    for name, (epsilon_r, tan_delta, source_id) in MATERIAL_POINTS.items():
        materials[name] = {
            "source_record_id": source_id,
            "frequency_hz": 1_000_000,
            "epsilon_r": float(epsilon_r),
            "tan_delta": float(tan_delta),
            "eim": str(epsilon_r * tan_delta),
            "role": "dielectric source material point",
        }
    materials["COPPER"] = {
        "source_record_id": "material:COPPER",
        "conductivity_s_per_m": COPPER_CONDUCTIVITY,
        "dielectric_loss": "NOT_DIELECTRIC_LOSS; CONDUCTOR_CONDUCTIVITY_ONLY",
    }
    return {
        "frequency_hz": 1_000_000,
        "frequency_role": "source material point/conductance interpretation; not a FasterCap frequency input",
        "phasor": "exp(+j*omega*t)",
        "epsilon_r_complex": "er*(1-j*tan_delta)",
        "admittance": "Y=j*omega*C_lossless*(1-j*tan_delta)",
        "conductance": "G=omega*C_lossless*tan_delta>=0",
        "materials": materials,
    }


def build_decision_receipt() -> dict[str, Any]:
    receipt = _read_pinned_json(D103_PATH, D103_SIZE, D103_SHA256)
    source_identity = _source_identity(receipt)
    _validate_material_points(receipt)
    validated_files = []
    for relative, expected_size, expected_sha256 in PINNED_FILES:
        path = ROOT / relative
        size, sha256 = _file_identity(path)
        if size != expected_size or sha256 != expected_sha256:
            raise Refusal(f"pinned source identity mismatch: {relative}")
        validated_files.append({"path": str(path), "size_bytes": size, "sha256": sha256})
    return {
        "product": PRODUCT,
        "version": VERSION,
        "decision_id": DECISION_ID,
        "status": "STOP_NUMERICAL_EXECUTION",
        "static_z_material_loss_slice": "PASS",
        "wp2_material_authority": "PARTIAL",
        "numerical_execution": "STOP",
        "face_z_material_rows": "SEALED_SOURCE_ORDER_NOMINAL_ONLY",
        "loss_convention_1mhz": "SEALED",
        "xy_dielectric_partitions": "STOP_UNSEALED",
        "conductor_layer_void_fill": "STOP_UNSEALED",
        "reference_points_and_panel_sides": "STOP_UNSEALED",
        "outer_truncation_and_closure": "STOP_UNSEALED",
        "absolute_source_z_transform": "STOP_UNSEALED",
        "source_evidence": {
            "d103_receipt": {"path": str(D103_PATH), "size_bytes": D103_SIZE, "sha256": D103_SHA256},
            "embedded_source_identity": source_identity,
            "read_only_validated_files": validated_files,
        },
        "transitions": _transitions(receipt),
        "material_loss_contract_1mhz": _loss_contract(),
        "raw_complex_matrix_sign_reference_interpretation": "STOP",
        "executed_components": [],
        "does_not_authorize": [
            "numerical_execution",
            "FasterCap",
            "FasterCap -oi",
            "Triangle",
            "C1",
            "solver",
            "PowerSI",
            "raw complex-matrix sign/reference interpretation",
        ],
    }


def _json_bytes(payload: dict[str, Any]) -> bytes:
    try:
        return (json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2, allow_nan=False) + "\n").encode("ascii")
    except (TypeError, ValueError) as exc:
        raise Refusal("decision receipt is not strict JSON") from exc


def write_atomic_no_clobber(path: Path, payload: dict[str, Any]) -> None:
    if not path.parent.is_dir() or os.path.lexists(path):
        raise Refusal("output path must name an absent file in an existing directory")
    data = _json_bytes(payload)
    temporary: Path | None = None
    try:
        fd, name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
        temporary = Path(name)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if os.path.lexists(path):
            raise Refusal("output path appeared during write")
        os.link(temporary, path)
        temporary.unlink()
        temporary = None
    except FileExistsError as exc:
        raise Refusal("output path appeared during write") from exc
    finally:
        if temporary is not None:
            try:
                temporary.unlink()
            except OSError:
                pass


def _synthetic_rows() -> list[dict[str, Any]]:
    boundaries = (1680, 1780, 1812, 1882, 1917, 1947, 1967, 1997, 2017, 2047, 2067, 2097, 2117, 2147)
    rows = []
    for ordinal in range(51, 64):
        top, bottom = boundaries[ordinal - 51], boundaries[ordinal - 50]
        rows.append(
            {
                "ordinal": ordinal,
                "layer_name": f"L{ordinal}",
                "layer_kind": "dielectric" if ordinal % 2 else "conductor",
                "material_name": "ABF-GL102" if ordinal % 2 else "COPPER",
                "thickness_um": float(bottom - top),
                "raw_layer_source_record_sha256": f"{ordinal:064x}",
                "depth_from_stack_top_um": {"top": float(top), "bottom": float(bottom)},
            }
        )
    return rows


def _self_check() -> dict[str, Any]:
    assert _loss_contract()["materials"]["ABF-GL102"]["eim"] == "0.01394"
    assert _loss_contract()["materials"]["EL190T"]["eim"] == "0.047"
    assert len(_transitions({"stackup_layers": _synthetic_rows()})) == 12
    for invalid in (b'{"a":1,"a":2}', b'{"a":NaN}', b'{"a":1e999}'):
        try:
            _strict_json_object(invalid)
        except Refusal:
            pass
        else:
            raise AssertionError("strict JSON self-check failed")
    return {"status": "PASS", "transition_count": 12, "d103_read": False, "output_written": False}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog=f"{PRODUCT} v{VERSION} D117 WP2 material-loss decision")
    parser.add_argument("--version", action="version", version=f"{PRODUCT} v{VERSION}")
    parser.add_argument("--self-check", action="store_true", help="run synthetic-only checks")
    parser.add_argument("--output", "--receipt", dest="output", type=Path, help="explicit absent JSON output path")
    args = parser.parse_args(argv)
    try:
        if args.self_check:
            if args.output is not None:
                parser.error("--self-check cannot be combined with --output")
            result = _self_check()
            print(f"{PRODUCT} v{VERSION} {result['status']} self-check PASS")
            return 0
        if args.output is None:
            parser.error("normal mode requires --output PATH")
        if os.path.lexists(args.output):
            raise Refusal("output path must be absent before reading pinned evidence")
        result = build_decision_receipt()
        write_atomic_no_clobber(args.output, result)
        print(f"{PRODUCT} v{VERSION} {result['status']} wrote {args.output}")
        return 0
    except Refusal as exc:
        print(f"{PRODUCT} v{VERSION} REFUSAL: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
