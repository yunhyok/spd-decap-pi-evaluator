"""Review source-backed versus assumed 3-D conductor geometry conventions.

This is a bounded source-code and saved-metadata audit.  It does not parse the
original SPD, open the compiled scenario, build solids, or run a field solver.
"""

from __future__ import annotations

import argparse
import ast
from hashlib import sha256
import json
from pathlib import Path
import shutil


ROOT = Path(__file__).resolve().parents[2]
INVENTORY = ROOT / "outputs/research/astra-3d-source-domain-inventory-01/result.json"
INVENTORY_SHA256 = "daed9082c027fb36706fb8a2eb9a00d094272f7bc7dfaf4b7d378e3a2fe6e663"

SOURCE_PINS = {
    "spd_parser": (
        ROOT / "src/spd_decap_pi/_core/io/spd.py",
        "fc17618801367294d1d2c1eabcef1d54db2603cfe88e2d6bfc191a3b484761fe",
    ),
    "source_trace": (
        ROOT / "src/spd_decap_pi/_core/geometry/source_trace.py",
        "f11dbfb701a58063f333a4af8e4cebad64594d9613f62aa9fb40d8db7fa2e6b8",
    ),
    "services": (
        ROOT / "src/spd_decap_pi/_core/services.py",
        "692a907838983c26fc64bffc93ad8f80482951c7f342797241d31907fcaef436",
    ),
    "via_model": (
        ROOT / "src/spd_decap_pi/_core/via_model.py",
        "d9b1acb96e2ff5137f502fdf0d24a6e9ba2e16e161e67a84a0b40b526b2b4043",
    ),
    "spd_adapter": (
        ROOT / "src/spd_decap_pi/spd_adapter.py",
        "843b39b5943ce7fbd9e9b0b2f4907dc4ca061e599646d02fb54cea105ab842b2",
    ),
    "eligibility": (
        ROOT / "src/spd_decap_pi/eligibility.py",
        "cb2f686ff4adf8e3006e516b9a3b583250663ff3e1d9bfe247039072b213346a",
    ),
    "shared_pad": (
        ROOT / "src/spd_decap_pi/_core/io/shared_pad.py",
        "f6a53ca964efe79bed9da2bc88099d395f5f01f65d9b925ab9c8e18749ce8b4c",
    ),
}


def digest(path: Path) -> str:
    value = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def require(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def line_of(text: str, fragment: str) -> int:
    matches = [index for index, line in enumerate(text.splitlines(), 1) if fragment in line]
    require(len(matches) == 1, f"expected one occurrence of {fragment!r}, got {matches}")
    return matches[0]


def class_fields(text: str, class_name: str) -> list[str]:
    tree = ast.parse(text)
    matches = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name]
    require(len(matches) == 1, f"class {class_name} cardinality changed")
    return [
        node.target.id
        for node in matches[0].body
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    ]


def write_once(path: Path, payload: object) -> None:
    encoded = (json.dumps(payload, indent=2, sort_keys=False, allow_nan=False) + "\n").encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=False)
    with path.open("xb") as handle:
        handle.write(encoded)


def run(output: Path) -> None:
    require(digest(INVENTORY) == INVENTORY_SHA256, "source-domain inventory hash changed")
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    require(
        inventory["status"] == "COMPLETE_CACHED_3D_SOURCE_DOMAIN_INVENTORY_WITH_EXPLICIT_GAPS",
        "source-domain inventory status changed",
    )

    texts: dict[str, str] = {}
    source_files: dict[str, object] = {}
    for name, (path, expected) in SOURCE_PINS.items():
        actual = digest(path)
        require(actual == expected, f"{name} source hash changed")
        text = path.read_text(encoding="utf-8")
        texts[name] = text
        source_files[name] = {"path": path.relative_to(ROOT).as_posix(), "sha256": actual}

    padstack_fields = class_fields(texts["spd_parser"], "SpdPadStack")
    trace_fields = class_fields(texts["spd_parser"], "SpdTraceRecord")
    padshape_fields = class_fields(texts["shared_pad"], "SpdPadShape")
    require(
        padstack_fields == [
            "name", "drill_diameter_um", "pad_width_um", "pad_height_um", "layers",
            "pad_shapes", "material",
        ],
        "SpdPadStack source field contract changed",
    )
    require(
        {"starting_node_id", "ending_node_id", "width_pm", "width_um", "geometry_status"}
        <= set(trace_fields),
        "SpdTraceRecord source field contract changed",
    )
    require(
        padshape_fields == ["layer", "kind", "width_um", "height_um", "reason"],
        "SpdPadShape source field contract changed",
    )
    forbidden_source_dimensions = {
        "trace_thickness_um", "trace_sidewall_angle", "trace_endcap_kind",
        "barrel_wall_thickness_um", "barrel_outer_diameter_um", "via_fill_kind",
    }
    require(not (forbidden_source_dimensions & set(padstack_fields + trace_fields + padshape_fields)),
            "new explicit 3-D source dimension appeared and requires review")

    evidence = {
        "parser_source_fields": {
            "SpdPadStack": padstack_fields,
            "SpdTraceRecord": trace_fields,
            "SpdPadShape": padshape_fields,
            "interpretation": (
                "The bounded parser retains endpoints, width, layer pad shapes, drill diameter, and "
                "padstack material. It has no explicit trace sidewall/endcap or barrel wall/outer-diameter field."
            ),
        },
        "dormant_trace_policy": {
            "policy_line": line_of(texts["source_trace"], "source-trace-straight-constant-width-flat-v1"),
            "flat_rectangle_line": line_of(texts["source_trace"], "Return deterministic flat-ended rectangle corners"),
            "interpretation": (
                "Flat-ended plan-view geometry is a disclosed deterministic solver policy over source endpoints "
                "and width; it is not an explicit etched-endcap or through-thickness source record."
            ),
        },
        "active_contact_geometry_policies": {
            "flat_trace_buffer_line": line_of(texts["eligibility"], "cap_style=2"),
            "circle_pad_tessellation_line": line_of(
                texts["shared_pad"], "return Point(shape.x_um, shape.y_um).buffer(shape.radius_um, quad_segs=64)"
            ),
            "interpretation": (
                "The active contact checks use flat trace caps and a 256-edge inscribed circle convention. "
                "These are computational contact conventions, not additional source cross-section evidence."
            ),
        },
        "via_model_assumptions": {
            "legacy_area_line": line_of(
                texts["via_model"],
                "barrel_area_m2 = pi * diameter_um * min(20.0, diameter_um / 4.0) * 1.0e-12",
            ),
            "solid_fill_provenance_line": line_of(
                texts["via_model"],
                "USER_CONFIRMED_MLO_COPPER_FILL_ASSUMPTION_WITH_SOURCE_COPPER_AND_GEOMETRY",
            ),
            "template_plating_line": line_of(texts["services"], '"barrel_plating_assumption_um": ('),
            "routing_profile_line": line_of(
                texts["spd_adapter"], "SOURCE_PADSTACK_REGULAR_SHAPES_WITH_ANALYTICAL_BARREL_V1"
            ),
            "interpretation": (
                "Existing product code fills missing fabrication data with an explicit classification/model: "
                "a qualified two-conductor/one-dielectric COPPER microvia is treated as solid fill; other cases "
                "use min(20 um, drill/4) plated-wall area. Neither value is a source-exact plating record."
            ),
        },
    }

    control = inventory["source_geometry_controls"]
    require(control["trace"]["length_um"] == 130.0 and control["trace"]["width_um"] == 45.0,
            "selected trace control changed")
    require(control["via"]["padstack"]["drill_diameter_pm"] == 40_000_000,
            "selected via drill control changed")
    require({row["width_pm"] for row in control["via"]["pad_shapes"]} == {60_000_000},
            "selected via pad control changed")

    payload = {
        "program": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "ACCEPT_SOURCE_GEOMETRY_FIELDS_WITH_EXPLICIT_MODEL_ASSUMPTIONS",
        "inputs": {
            "source_domain_inventory": {
                "path": INVENTORY.relative_to(ROOT).as_posix(),
                "sha256": INVENTORY_SHA256,
            },
            "source_files": source_files,
        },
        "selected_source_control": {
            "trace": {"id": control["trace"]["row"]["trace_id"], "length_um": 130.0, "width_um": 45.0},
            "via": {
                "id": control["via"]["row"]["via_id"],
                "pad_diameter_um": 60.0,
                "drill_diameter_um": 40.0,
                "foil_center_span_um": control["via"]["foil_center_span_um"],
                "padstack_material": control["via"]["padstack"]["material"],
            },
        },
        "evidence": evidence,
        "conclusion": {
            "source_backed_inputs": [
                "stackup conductor z/thickness/material/conductivity",
                "trace endpoint centerline and width",
                "layer-specific regular pad shape and size",
                "via endpoint/layer/XY, drill diameter, padstack material, and foil-center span",
            ],
            "not_source_exact": [
                "trace endcap, etched sidewall, and through-thickness cross-section",
                "via plated-wall thickness, barrel outer diameter, and fill state",
                "3-D pad/trace/via unions and dielectric lateral boundary",
            ],
            "decision": (
                "Cache-field absence is not proof of physical absence: enough dimensions exist to construct a "
                "declared extrusion and via model. Those constructions remain model assumptions and require "
                "reference validation; they must not be labeled source-exact board solids."
            ),
        },
        "scope": {
            "read_only": True,
            "raw_spd_opened": False,
            "compiled_scenario_opened": False,
            "geometry_built": False,
            "field_solve_run": False,
            "board_accuracy_claim": False,
        },
    }

    driver = output / "driver-at-run.py"
    output.mkdir(parents=True, exist_ok=False)
    with driver.open("xb") as handle:
        with Path(__file__).open("rb") as source:
            shutil.copyfileobj(source, handle)
    result = output / "independent-review.json"
    with result.open("xb") as handle:
        handle.write((json.dumps(payload, indent=2, allow_nan=False) + "\n").encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "outputs/research/astra-3d-source-geometry-conventions-review-01",
    )
    args = parser.parse_args()
    run(args.output.resolve())


if __name__ == "__main__":
    main()
