import importlib.util
from pathlib import Path
from types import SimpleNamespace


SCRIPT = (
    Path(__file__).parents[1]
    / "scripts"
    / "validate_island_finite_via_gate.py"
)
SPEC = importlib.util.spec_from_file_location(
    "validate_island_finite_via_gate", SCRIPT
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


def _complete_gate_inputs():
    return {
        "surface_certificate": {"status": "complete"},
        "terminal_contacts": (),
        "finite_quotient": {"status": "complete"},
        "scenario_terminal_topology": {"status": "complete"},
        "island_pair_aggregates": (),
        "surface_gate_error": None,
        "port_audit": {"status": "complete"},
        "substrate_audit": {"status": "complete"},
    }


def test_first_failure_uses_only_production_gates_after_complete_certificate():
    # Raw terminal_landing_contacts are intentionally absent from this helper:
    # source-node trace anchors and scenario-isolated base rows are diagnostics,
    # while the quotient/scenario manifests are the authoritative gate.
    assert MODULE._first_gate_failure(**_complete_gate_inputs()) is None


def test_first_failure_reports_real_production_compile_error():
    inputs = _complete_gate_inputs()
    inputs["surface_gate_error"] = {
        "code": "FINITE_TOPOLOGY_INVALID",
        "message": "invalid finite topology",
    }

    assert MODULE._first_gate_failure(**inputs) == {
        "kind": "production_surface_gate",
        "code": "FINITE_TOPOLOGY_INVALID",
        "message": "invalid finite topology",
    }


def test_production_surface_gate_routes_v4_to_finite_topology_compiler(
    monkeypatch,
):
    calls = []

    def compile_v4(
        project,
        records,
        artwork_node_ids,
        *,
        certificate,
        certificate_verified,
    ):
        calls.append(
            (
                project,
                records,
                artwork_node_ids,
                certificate,
                certificate_verified,
            )
        )
        return SimpleNamespace(
            quotient_vertex_ids=("q1",),
            external_port_node_ids=("p1", "p2"),
            topology_links=("t1",),
            finite_links=("f1", "f2"),
            rail_ports=("r1",),
            omitted_rail_ids=(),
            owner_edge_by_id={"V1": "f1"},
        )

    monkeypatch.setattr(
        MODULE.layerwise_network,
        "compile_finite_via_base_topology",
        compile_v4,
    )
    monkeypatch.setattr(
        MODULE.layerwise_network,
        "_validated_surface_connectivity_certificate",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("legacy v3 validator must not receive a v4 certificate")
        ),
    )
    project = SimpleNamespace()
    records = ({"island_ids": ("I1", "I2")},)
    certificate = {
        "schema_version": MODULE.layerwise_network.FINITE_VIA_SURFACE_SCHEMA
    }

    error, stats = MODULE._production_surface_gate(
        project, records, certificate
    )

    assert error is None
    assert stats == {
        "schema_version": MODULE.layerwise_network.FINITE_VIA_SURFACE_SCHEMA,
        "quotient_vertex_count": 1,
        "external_port_node_count": 2,
        "topology_link_count": 1,
        "finite_link_count": 2,
        "rail_port_count": 1,
        "omitted_rail_count": 0,
        "owner_count": 1,
        "status": "complete",
    }
    assert calls == [
        (project, records, ("I1", "I2"), certificate, True)
    ]


def test_compiled_only_anchor_bindings_use_external_port_proof_view(
    monkeypatch,
):
    project = SimpleNamespace(
        metadata={
            "spd_import": {
                "plane_geometries": [
                    {"island_ids": ["I1", "", "I2"]},
                    {"island_ids": ["I3"]},
                ]
            }
        }
    )
    attachments = {"compiled.sqlite.zlib": b"payload"}
    surface_stub = {
        "storage_schema": (
            "spd-layer-surface-connectivity-certificate-compiled-only-v1"
        )
    }
    retained_bindings = (
        {"rail_id": "R1", "pin_id": "PWR"},
        {"rail_id": "R1", "pin_id": "GND"},
    )
    calls = []

    def load_compiled(project_arg, attachments_arg, artwork_node_ids):
        calls.append((project_arg, attachments_arg, artwork_node_ids))
        return SimpleNamespace(
            external_port_proof_view={
                "rail_anchor_bindings": retained_bindings,
            }
        )

    monkeypatch.setattr(
        MODULE,
        "load_compiled_topology_asset",
        load_compiled,
    )

    bindings = MODULE._retained_rail_anchor_bindings(
        project,
        attachments,
        surface_stub,
    )

    assert bindings == list(retained_bindings)
    assert calls == [(project, attachments, ("I1", "", "I2", "I3"))]
