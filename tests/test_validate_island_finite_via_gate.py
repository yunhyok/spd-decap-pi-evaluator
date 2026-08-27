import importlib.util
import json
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


def test_strict_source_coverage_audit_hash_and_cli_contract(tmp_path, monkeypatch):
    candidate_sha = "a" * 64
    complete_result = {
        "candidate_sha256": candidate_sha,
        "raw_spd_hash_verified_during_graph_pass": True,
        "surface": {
            "status": "complete",
            "production_gate_error": None,
            "production_compile": {"status": "complete"},
            "proof_count": 1,
            "noncomplete_proof_count": 0,
            "artwork_island_count": 1,
            "terminal_landing_contact_count": 1,
            "terminal_landing_contact_status_counts": {"complete": 1},
            "terminal_landing_physical_status_counts": {"complete": 1},
            "terminal_landing_component_binding_status_counts": {"complete": 1},
            "terminal_owner_kind_counts": {"device": 1},
            "via_island_pair_aggregate_count": 1,
            "via_island_pair_physical_status_counts": {"complete": 1},
            "via_island_pair_component_binding_status_counts": {"complete": 1},
            "via_island_pair_coverage": {
                "status": "complete",
                "unsupported_missing_endpoint_count": 0,
                "terminal_owned_declared_count": 2,
                "terminal_owned_observed_count": 2,
            },
            "substrate_audit": {"population_count": 1, "status": "complete"},
            "scenario_decap_terminal_topology": {"status": "complete"},
            "rail_port_audit": {"status": "complete"},
            "first_failure": None,
            "recovery_statistics": {
                "node_section_passes": 1,
                "trace_section_passes": 1,
                "via_section_passes": 1,
                "via_source_record_replay_passes": 1,
            },
        },
    }
    complete_audit = MODULE._source_coverage_audit(
        complete_result, expected_candidate_sha256=candidate_sha
    )
    assert complete_audit == {"status": "complete", "first_failure": None}
    complete_result["source_coverage_audit"] = complete_audit
    incomplete_result = {
        **complete_result,
        "surface": {**complete_result["surface"], "status": "incomplete"},
    }
    incomplete_result["source_coverage_audit"] = MODULE._source_coverage_audit(
        incomplete_result, expected_candidate_sha256=candidate_sha
    )
    assert incomplete_result["source_coverage_audit"]["status"] == "incomplete"

    candidate = tmp_path / "candidate.zip"
    candidate.write_bytes(b"candidate")
    mismatch_output = tmp_path / "mismatch.json"
    assert MODULE.main(
        [
            "--case",
            "case",
            "--raw-spd",
            str(tmp_path / "missing.spd"),
            "--candidate",
            str(candidate),
            "--output",
            str(mismatch_output),
            "--expected-candidate-sha256",
            "0" * 64,
        ]
    ) == 2
    mismatch = json.loads(mismatch_output.read_text(encoding="utf-8"))
    assert mismatch["source_coverage_audit"]["first_failure"]["kind"] == "candidate_identity"
    assert mismatch["candidate_sha256"] != mismatch["expected_candidate_sha256"]

    returned = iter((complete_result, incomplete_result))
    monkeypatch.setattr(MODULE, "validate", lambda *_args, **_kwargs: next(returned))
    cli_args = [
        "--case",
        "case",
        "--raw-spd",
        str(tmp_path / "raw.spd"),
        "--candidate",
        str(candidate),
        "--output",
        str(tmp_path / "strict.json"),
        "--expected-candidate-sha256",
        candidate_sha,
        "--require-source-coverage",
    ]
    assert MODULE.main(cli_args) == 0
    assert MODULE.main(cli_args) == 2
