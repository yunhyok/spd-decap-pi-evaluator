from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools/research/run_d117_wp3_selected_pin_source_path_absence_once.ps1"
TOKEN_NAME = "d117_wp3_selected_pin_source_path_absence_attempt.json"
CERTIFICATE_NAME = "d117_wp3_selected_pin_source_path_absence_certificate.json"
CONTROLLER_RECEIPT_NAME = "d117_wp3_selected_pin_source_path_absence_controller_receipt.json"
REVERSED_TRACES = ("Trace1258306", "Trace1258307", "Trace1275750", "Trace1275751")
PRODUCTION_REVERSED_TRACES = ("Trace1258307", "Trace1258306", "Trace1275751", "Trace1275750")


def _powershell_executables() -> tuple[str, ...]:
    paths: list[str] = []
    seen: set[str] = set()
    for name in ("pwsh", "powershell"):
        path = shutil.which(name)
        if path:
            key = os.path.normcase(os.path.abspath(path))
            if key not in seen:
                seen.add(key)
                paths.append(path)
    return tuple(paths)


def _powershell() -> str:
    executables = _powershell_executables()
    if executables:
        return executables[0]
    pytest.skip("PowerShell is unavailable")


def _powershell_args(executable: str) -> list[str]:
    arguments = ["-NoProfile", "-NonInteractive"]
    if Path(executable).name.casefold() == "powershell.exe":
        arguments.extend(("-ExecutionPolicy", "Bypass"))
    return arguments


def _run_with(executable: str, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable, *_powershell_args(executable), "-File", str(SCRIPT), *arguments],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _run(*arguments: str) -> subprocess.CompletedProcess[str]:
    return _run_with(_powershell(), *arguments)


def _run_fake_with(executable: str, fixture: _FakeRun, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [executable, *_powershell_args(executable), "-File", str(fixture.script), *arguments],
        cwd=fixture.repo,
        text=True,
        encoding="utf-8",
        capture_output=True,
        timeout=30,
        check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _run_fake(fixture: _FakeRun, *arguments: str) -> subprocess.CompletedProcess[str]:
    return _run_fake_with(_powershell(), fixture, *arguments)


def _fixture(tmp_path: Path, name: str = "hq.json", data: bytes = b'{"fixture":true}\n') -> tuple[Path, int, str]:
    path = tmp_path / name
    path.write_bytes(data)
    return path, len(data), hashlib.sha256(data).hexdigest()


@dataclass(frozen=True)
class _FakeRun:
    script: Path
    repo: Path
    artifacts: Path
    hq: Path
    input_root: Path
    input_path: Path
    source_paths: dict[str, Path]
    token: Path
    certificate: Path
    receipt: Path
    hq_size: int
    hq_sha256: str


def _compact_json(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def _sha_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _file_version(path: Path) -> str:
    escaped = str(path).replace("'", "''")
    executable = _powershell()
    result = subprocess.run(
        [executable, *_powershell_args(executable), "-Command", f"(Get-Item -LiteralPath '{escaped}').VersionInfo.FileVersion"],
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _fake_certificate(reversed_traces: tuple[str, ...] = REVERSED_TRACES) -> bytes:
    proof_flags = {
        name: False
        for name in (
            "three_dimensional_geometry_proven",
            "barrel_proven",
            "antipad_proven",
            "land_proven",
            "intermediate_access_proven",
            "target_layer_traversal_proven",
            "l29_l30_physical_pad_proven",
            "physical_geometry_proven",
            "physical_traversal_proven",
        )
    }

    def path(layer: str, traces: tuple[tuple[str, bool], ...]) -> dict[str, object]:
        edges: list[dict[str, object]] = [
            {"edge_kind": "Via", "traversal_reversed": False} for _ in range(18)
        ]
        edges.extend(
            {"edge_kind": "Trace", "traversal_reversed": reversed_flag, "trace_id": trace_id}
            for trace_id, reversed_flag in traces
        )
        return {"nodes": [{} for _ in range(22)], "edges": edges, "terminal": {"layer": layer}}

    def trace_entries(items: tuple[str, ...], fillers: tuple[str, ...]) -> tuple[tuple[str, bool], ...]:
        entries = [(trace_id, True) for trace_id in items]
        entries.extend((trace_id, False) for trace_id in fillers)
        return tuple(entries[:3])

    first_traces = trace_entries(reversed_traces[:2], ("Trace1258308", "Trace1258309", "Trace1258310"))
    second_traces = trace_entries(reversed_traces[2:], ("Trace1258311", "Trace1258312", "Trace1258313"))

    value = {
        "product": "SPD Decap PI Evaluator",
        "version": "0.23.1",
        "status": "WP3=PARTIAL",
        "wp3_status": "PARTIAL",
        "gate": "STOP_NOT_REPRESENTED",
        "logical_ownership_preserved": True,
        "physical_nonconnection_claimed": False,
        "fallback_geometry_used": False,
        "pins": [
            {"path": path("Signal$L18(DGND)", first_traces)},
            {"path": path("Signal$L20(DGND)", second_traces)},
        ],
        "padstacks": [{} for _ in range(19)],
        "proof_flags": proof_flags,
    }
    return _compact_json(value)


def _fake_run_fixture(
    tmp_path: Path,
    mode: str = "valid",
    token_leaf: str | None = None,
    certificate_traces: tuple[str, ...] = REVERSED_TRACES,
) -> _FakeRun:
    repo = tmp_path / "fake-repo"
    artifacts = tmp_path / "artifacts"
    input_root = repo / "input-root"
    source_root = repo / "sources"
    repo.mkdir()
    artifacts.mkdir()
    input_root.mkdir()
    source_root.mkdir()
    input_path = input_root / "d117_wp3_selected_pin_source_path_absence_input.json"
    input_path.write_bytes(b"fake input\n")
    source_paths = {name: source_root / f"{name}.dat" for name in ("raw_spd", "d115b", "d115c", "wp3_05")}
    for name, path in source_paths.items():
        path.write_bytes((name + " source\n").encode())
    fake_test = repo / "tests" / "fake_test.py"
    fake_test.parent.mkdir()
    fake_test.write_text("# fake test\n", encoding="utf-8")
    certificate_bytes = _fake_certificate(certificate_traces)
    hq = artifacts / "hq.json"
    token_leaf = token_leaf or TOKEN_NAME
    token = artifacts / token_leaf
    certificate = artifacts / CERTIFICATE_NAME
    receipt = artifacts / CONTROLLER_RECEIPT_NAME
    script_copy = repo / "run_wrapper.ps1"
    fake_builder = repo / "tools" / "research" / "fake_builder.py"
    fake_builder.parent.mkdir(parents=True)

    builder_code = f"""from pathlib import Path
import os
import sys

out = Path(sys.argv[sys.argv.index('--output') + 1])
out.write_bytes(bytes.fromhex({certificate_bytes.hex()!r}))
mode = {mode!r}
token = Path({str(token)!r})
input_path = Path({str(input_path)!r})
source_path = Path({str(source_paths['raw_spd'])!r})
hq_path = Path({str(hq)!r})
controller_path = Path({str(script_copy)!r})
if mode == 'token_mutation':
    token.write_bytes(b'mutated-token')
elif mode == 'input_drift':
    input_path.unlink()
elif mode == 'source_drift':
    source_path.write_bytes(b'changed-source\\n')
elif mode == 'controller_drift':
    data = controller_path.read_bytes()
    controller_path.write_bytes(data[:-1] + (b'X' if data[-1:] != b'X' else b'Y'))
elif mode == 'hq_substitute':
    replacement = hq_path.with_name('hq-replacement.json')
    replacement.write_bytes(hq_path.read_bytes())
    try:
        os.replace(replacement, hq_path)
    except OSError:
        pass
    finally:
        if replacement.exists():
            replacement.unlink()
"""
    fake_builder.write_text(builder_code, encoding="utf-8")

    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True, capture_output=True, text=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "WP3 test"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "fake fixture"], check=True)
    head = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()

    script_text = SCRIPT.read_text(encoding="utf-8")
    replacements = {
        "$RequiredRepo = 'C:\\Users\\User\\Documents\\ChatGPT\\SPD Decap PI Evaluator'": f"$RequiredRepo = {_ps_quote(str(repo))}",
        "$RequiredHead = 'e2f219e71d8c8a397009f72242cce10d78cfc7ab'": f"$RequiredHead = {_ps_quote(head)}",
        "$BuilderRelative = 'tools/research/build_d117_wp3_selected_pin_source_path_absence.py'": "$BuilderRelative = 'tools/research/fake_builder.py'",
        "$TestRelative = 'tests/test_d117_wp3_selected_pin_source_path_absence.py'": "$TestRelative = 'tests/fake_test.py'",
        "$InputRoot = 'D:\\SPD-Decap-PI-Evaluator-W7\\e2f219e71d8c8a397009f72242cce10d78cfc7ab\\260906-d117-wp3-selected-pin-source-path-absence-02'": f"$InputRoot = {_ps_quote(str(input_root))}",
        "$InputSize = [int64]23049": f"$InputSize = [int64]{input_path.stat().st_size}",
        "$InputSha = '01b281f459da111ba908c1de9924b493881788a79eff3ef607036f7c1738ce56'": f"$InputSha = {_ps_quote(_sha_bytes(input_path.read_bytes()))}",
        "$BuilderSize = [int64]53470": f"$BuilderSize = [int64]{fake_builder.stat().st_size}",
        "$BuilderSha = '8961814ab1d8bd511be6d1fae56c405c26a8ea225d9d751c5491980f35342063'": f"$BuilderSha = {_ps_quote(_sha_bytes(fake_builder.read_bytes()))}",
        "$TestSize = [int64]29723": f"$TestSize = [int64]{fake_test.stat().st_size}",
        "$TestSha = 'ed41fd65e2e3791a19cccddb0089c317d60188a07be7737cda32c91f08154cb6'": f"$TestSha = {_ps_quote(_sha_bytes(fake_test.read_bytes()))}",
        "$CertificateSize = [int64]52161": f"$CertificateSize = [int64]{len(certificate_bytes)}",
        "$CertificateSha = '581ca0606b103b0ae6c3e4d25d410f31364f6fcfe659e0bcb82af7ba9ccb3310'": f"$CertificateSha = {_ps_quote(_sha_bytes(certificate_bytes))}",
    }
    for old, new in replacements.items():
        assert old in script_text
        script_text = script_text.replace(old, new, 1)
    source_start = script_text.index("$SourceExpected = [ordered]@{")
    source_end = script_text.index("\n}\n\ntry { $Host", source_start) + 2
    source_rows = ["$SourceExpected = [ordered]@{"]
    for name, path in source_paths.items():
        data = path.read_bytes()
        source_rows.append(
            f"    {name} = [ordered]@{{ path = {_ps_quote(str(path))}; size_bytes = [int64]{len(data)}; sha256 = {_ps_quote(_sha_bytes(data))} }}"
        )
    source_rows.append("}")
    script_text = script_text[:source_start] + "\n".join(source_rows) + script_text[source_end:]
    script_copy.write_text(script_text, encoding="utf-8-sig", newline="")

    python_path = Path(sys.executable).resolve()
    python_bytes = python_path.read_bytes()
    python_version = _file_version(python_path)
    builder_bytes = fake_builder.read_bytes()
    test_bytes = fake_test.read_bytes()
    controller_bytes = script_copy.read_bytes()
    argv = [
        str(python_path),
        "-B",
        str(fake_builder),
        "--input",
        str(input_path),
        "--raw-spd",
        str(source_paths["raw_spd"]),
        "--d115b",
        str(source_paths["d115b"]),
        "--d115c",
        str(source_paths["d115c"]),
        "--wp3-05",
        str(source_paths["wp3_05"]),
        "--output",
        str(certificate),
    ]
    target_layers = ["Signal$L29(DGND)", "Signal$L30(OTHER_POWER1)"]
    terminal_layers = ["Signal$L18(DGND)", "Signal$L20(DGND)"]
    proof_flags = {
        name: False
        for name in (
            "three_dimensional_geometry_proven",
            "barrel_proven",
            "antipad_proven",
            "land_proven",
            "intermediate_access_proven",
            "target_layer_traversal_proven",
            "l29_l30_physical_pad_proven",
            "physical_geometry_proven",
            "physical_traversal_proven",
        )
    }
    counts = {"pins": 2, "nodes": 44, "edges": 42, "vias": 36, "traces": 6, "padstacks": 19}
    scientific_scope = {
        "mode": "preparation_only",
        "pins": ["SITE0:20612", "SITE0:19973"],
        "target_layers": target_layers,
        "selected_chain_only": True,
        "global_claim_authorized": False,
        "physical_nonconnection_authorized": False,
        "raw_spd_graph_scan": False,
        "counts": counts,
        "proof_flags": proof_flags,
    }
    expected_result = {
        "exit_code": 0,
        "status": "WP3=PARTIAL",
        "wp3_status": "PARTIAL",
        "gate": "STOP_NOT_REPRESENTED",
        "certificate": {"path": str(certificate), "size_bytes": len(certificate_bytes), "sha256": _sha_bytes(certificate_bytes)},
        "counts": counts,
        "terminal_layers": terminal_layers,
        "reversed_traces": list(REVERSED_TRACES),
        "proof_flags": proof_flags,
        "physical_nonconnection_claimed": False,
        "fallback_geometry_used": False,
        "logical_ownership_preserved": True,
    }
    hq_value = {
        "schema": "d117-wp3-selected-pin-source-path-absence-hq-authorization-v1",
        "product": {"name": "SPD Decap PI Evaluator", "version": "0.23.1"},
        "authorization": {"authorization_id": "fake-normal-run", "status": "SOL_HQ_APPROVED_SINGLE_EXECUTION", "execution_authorized": True},
        "scientific_scope": scientific_scope,
        "git": {"repo_root": str(repo), "head": head, "branch": "main"},
        "controller_binding": {"path": str(script_copy), "size_bytes": len(controller_bytes), "sha256": _sha_bytes(controller_bytes)},
        "builder_binding": {"path": str(fake_builder), "size_bytes": len(builder_bytes), "sha256": _sha_bytes(builder_bytes)},
        "test_binding": {"path": str(fake_test), "size_bytes": len(test_bytes), "sha256": _sha_bytes(test_bytes), "passed": 22},
        "input_binding": {"path": str(input_path), "size_bytes": input_path.stat().st_size, "sha256": _sha_bytes(input_path.read_bytes())},
        "source_bindings": {
            name: {"path": str(path), "size_bytes": path.stat().st_size, "sha256": _sha_bytes(path.read_bytes())}
            for name, path in source_paths.items()
        },
        "python_binding": {
            "launcher_token": "python",
            "resolved_executable": str(python_path),
            "file_version": python_version if mode != "python_version_mismatch" else "mismatch",
            "size_bytes": len(python_bytes),
            "sha256": _sha_bytes(python_bytes),
            "working_directory": str(repo),
            "environment": {"PYTHONDONTWRITEBYTECODE": "1"},
        },
        "argv": {"tokens": argv, "sha256": _sha_bytes(_compact_json(argv)), "hash_convention": "compact UTF-8 JSON array without BOM"},
        "expected_result": expected_result,
        "execution_policy": {"python_children": 1, "attempts": 1, "retries": 0, "wall_seconds": 600, "stdout_chars": 65536, "stderr_chars": 65536, "timeout_tree_cleanup": True},
        "attempt_token_policy": {"path": str(token), "file_mode": "System.IO.FileMode.CreateNew", "creation_timing": "immediately_before_process_creation", "permanent_retention": True},
        "artifact_policy": {"root": str(artifacts), "controller_receipt_path": str(receipt), "before": ["HQ"], "pre_controller_receipt": ["HQ", "TOKEN", "CERTIFICATE"], "success": ["HQ", "TOKEN", "CERTIFICATE", "CONTROLLER_RECEIPT"], "prohibit_logs": True, "prohibit_temp": True},
        "prohibitions": ["solver prohibited", "Triangle prohibited", "FasterCap prohibited", "PowerSI prohibited"],
    }
    if mode == "fixed_leaf_rejection":
        hq_value["attempt_token_policy"]["path"] = str(artifacts / "wrong-token.json")
    if mode == "controller_leaf_rejection":
        hq_value["artifact_policy"]["controller_receipt_path"] = str(artifacts / "wrong-receipt.json")
    hq_bytes = _compact_json(hq_value)
    hq.write_bytes(hq_bytes)
    return _FakeRun(script_copy, repo, artifacts, hq, input_root, input_path, source_paths, token, certificate, receipt, len(hq_bytes), _sha_bytes(hq_bytes))


def test_version_banner_and_positive_selfcheck_fixture() -> None:
    executables = _powershell_executables()
    if not executables:
        pytest.skip("PowerShell is unavailable")
    for executable in executables:
        version = _run_with(executable, "-SelfCheck")
        assert version.returncode == 0, f"{executable}: {version.stderr}"
        assert '"result":"PASS"' in version.stdout
        assert '"positive_pin_fixture":true' in version.stdout
        assert "SPD Decap PI Evaluator v0.23.1" in version.stdout


def test_selfcheck_rejects_any_mixed_normal_pins(tmp_path: Path) -> None:
    path, size, digest = _fixture(tmp_path)
    result = _run(
        "-SelfCheck",
        "-HqAuthorizationPath",
        str(path),
        "-HqAuthorizationSizeBytes",
        str(size),
        "-HqAuthorizationSha256",
        digest,
    )
    assert result.returncode == 2
    assert "SelfCheck rejects normal-mode HQ pins" in result.stderr


@pytest.mark.parametrize(
    "arguments",
    (
        (),
        ("-HqAuthorizationPath", "C:\\missing"),
        ("-HqAuthorizationPath", "C:\\missing", "-HqAuthorizationSizeBytes", "1"),
        ("-HqAuthorizationPath", "C:\\missing", "-HqAuthorizationSha256", "0" * 64),
        ("-HqAuthorizationSizeBytes", "1", "-HqAuthorizationSha256", "0" * 64),
    ),
)
def test_normal_mode_requires_all_external_pins(arguments: tuple[str, ...]) -> None:
    result = _run(*arguments)
    assert result.returncode == 2
    assert "requires -HqAuthorizationPath" in result.stderr


@pytest.mark.parametrize(
    ("size_text", "digest_text", "needle"),
    (
        ("0", "0" * 64, "must be positive"),
        ("1", "0" * 63, "64 hexadecimal"),
        ("1", "0" * 65, "64 hexadecimal"),
        ("1", "g" * 64, "64 hexadecimal"),
    ),
)
def test_literal_pin_shape_is_checked_before_open(
    tmp_path: Path, size_text: str, digest_text: str, needle: str
) -> None:
    path, _, _ = _fixture(tmp_path)
    result = _run(
        "-HqAuthorizationPath",
        str(path),
        "-HqAuthorizationSizeBytes",
        size_text,
        "-HqAuthorizationSha256",
        digest_text,
    )
    assert result.returncode == 2
    assert needle in result.stderr


def test_wrong_size_and_digest_are_rejected_before_json_parse(tmp_path: Path) -> None:
    path, size, digest = _fixture(tmp_path)
    wrong_size = _run(
        "-HqAuthorizationPath",
        str(path),
        "-HqAuthorizationSizeBytes",
        str(size + 1),
        "-HqAuthorizationSha256",
        digest,
    )
    assert wrong_size.returncode == 2
    assert "size pin mismatch" in wrong_size.stderr

    wrong_digest = _run(
        "-HqAuthorizationPath",
        str(path),
        "-HqAuthorizationSizeBytes",
        str(size),
        "-HqAuthorizationSha256",
        "0" * 64,
    )
    assert wrong_digest.returncode == 2
    assert "SHA-256 pin mismatch" in wrong_digest.stderr


def test_changed_same_size_hq_is_not_accepted(tmp_path: Path) -> None:
    path, size, old_digest = _fixture(tmp_path, data=b'{"fixture":true}\n')
    path.write_bytes(b'{"fixture":null}\n')
    assert path.stat().st_size == size
    result = _run(
        "-HqAuthorizationPath",
        str(path),
        "-HqAuthorizationSizeBytes",
        str(size),
        "-HqAuthorizationSha256",
        old_digest,
    )
    assert result.returncode == 2
    assert "SHA-256 pin mismatch" in result.stderr


def test_substituted_valid_hq_is_parsed_but_cannot_authorize(tmp_path: Path) -> None:
    first, size, digest = _fixture(tmp_path, "first.json")
    second, _, _ = _fixture(tmp_path, "second.json", data=first.read_bytes())
    result = _run(
        "-HqAuthorizationPath",
        str(second),
        "-HqAuthorizationSizeBytes",
        str(size),
        "-HqAuthorizationSha256",
        digest,
    )
    assert result.returncode == 2
    assert "HQ authorization" in result.stderr


def test_duplicate_keys_are_rejected_after_exact_pin_validation(tmp_path: Path) -> None:
    path, size, digest = _fixture(tmp_path, data=b'{"x":1,"x":2}\n')
    result = _run(
        "-HqAuthorizationPath",
        str(path),
        "-HqAuthorizationSizeBytes",
        str(size),
        "-HqAuthorizationSha256",
        digest,
    )
    assert result.returncode == 2
    assert "duplicate JSON key" in result.stderr


def test_existing_reparse_parent_is_rejected_without_creating_or_touching_one(tmp_path: Path) -> None:
    # Windows ships this read-only junction on the test host; using it avoids
    # requiring SeCreateSymbolicLinkPrivilege and does not mutate the profile.
    junction = Path(r"C:\Users\User\Application Data")
    if not junction.exists() or not (junction.lstat().st_file_attributes & 0x400):
        pytest.fail("safe existing Windows reparse fixture is unavailable")
    link = junction / "hq-link.json"
    size = len(b'{"fixture":true}\n')
    digest = hashlib.sha256(b'{"fixture":true}\n').hexdigest()
    result = _run(
        "-HqAuthorizationPath",
        str(link),
        "-HqAuthorizationSizeBytes",
        str(size),
        "-HqAuthorizationSha256",
        digest,
    )
    assert result.returncode == 2
    assert "reparse point rejected" in result.stderr


def test_selfcheck_exercises_create_new_inventory_timeout_and_capture_guards() -> None:
    result = _run("-SelfCheck")
    assert result.returncode == 0, result.stderr
    assert '"create_new_no_clobber":true' in result.stdout
    assert '"timed_out":true' in result.stdout
    assert '"stdout_truncated":true' in result.stdout
    assert '"stderr_truncated":true' in result.stdout


def test_script_contains_pre_and_post_inventory_and_atomic_receipt_contract() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    for marker in (
        "pre-controller-receipt inventory",
        "final inventory",
        "Write-AtomicNoClobber",
        "controller_receipt_path",
        "CreateNew",
        "timeout_tree_cleanup",
        "PYTHONDONTWRITEBYTECODE",
    ):
        assert marker in text


def _run_fake_normal_with(executable: str, fixture: _FakeRun) -> subprocess.CompletedProcess[str]:
    return _run_fake_with(
        executable,
        fixture,
        "-HqAuthorizationPath",
        str(fixture.hq),
        "-HqAuthorizationSizeBytes",
        str(fixture.hq_size),
        "-HqAuthorizationSha256",
        fixture.hq_sha256,
    )


def _run_fake_normal(fixture: _FakeRun) -> subprocess.CompletedProcess[str]:
    return _run_fake_normal_with(_powershell(), fixture)


def _read_receipt(fixture: _FakeRun) -> dict[str, object]:
    return json.loads(fixture.receipt.read_text(encoding="utf-8"))


def test_full_normal_mode_fake_child_writes_exact_success_inventory_and_receipt(tmp_path: Path) -> None:
    fixture = _fake_run_fixture(tmp_path)
    result = _run_fake_normal(fixture)
    assert result.returncode == 0, result.stderr
    assert "SPD Decap PI Evaluator v0.23.1 PASS" in result.stdout
    receipt = _read_receipt(fixture)
    assert receipt["status"] == "PASS"
    assert receipt["inventories"]["pre_run"] == ["hq.json"]
    assert receipt["inventories"]["pre_controller_receipt"] == ["d117_wp3_selected_pin_source_path_absence_attempt.json", "d117_wp3_selected_pin_source_path_absence_certificate.json", "hq.json"]
    assert sorted(receipt["inventories"]["success"]) == sorted(["hq.json", TOKEN_NAME, CERTIFICATE_NAME, CONTROLLER_RECEIPT_NAME])
    assert receipt["expected_scientific_summary"]["reversed_traces"] == list(REVERSED_TRACES)
    token_bytes = fixture.token.read_bytes()
    assert receipt["token"]["sha256"] == _sha_bytes(token_bytes)
    assert _sha_bytes(fixture.receipt.read_bytes()) == result.stdout.rsplit("sha256=", 1)[1].strip()


def test_unicode_argv_hash_and_permuted_certificate_are_accepted_by_each_engine(tmp_path: Path) -> None:
    executables = _powershell_executables()
    if not executables:
        pytest.skip("PowerShell is unavailable")
    for index, executable in enumerate(executables):
        engine_root = tmp_path / f"unicode-{index}-가짜"
        engine_root.mkdir()
        fixture = _fake_run_fixture(engine_root, certificate_traces=PRODUCTION_REVERSED_TRACES)
        result = _run_fake_normal_with(executable, fixture)
        assert result.returncode == 0, f"{executable}: {result.stderr}"
        assert _read_receipt(fixture)["status"] == "PASS"
        hq = json.loads(fixture.hq.read_text(encoding="utf-8"))
        assert hq["expected_result"]["reversed_traces"] == list(REVERSED_TRACES)
        assert any(any(ord(character) > 127 for character in token) for token in hq["argv"]["tokens"])


@pytest.mark.parametrize(
    ("label", "certificate_traces"),
    (
        ("wrong", ("Trace1258307", "Trace1258306", "Trace1275751", "Trace9999999")),
        ("missing", ("Trace1258307", "Trace1258306", "Trace1275751")),
        ("duplicate", ("Trace1258307", "Trace1258306", "Trace1258307", "Trace1275750")),
        ("case", ("trace1258307", "Trace1258306", "Trace1275751", "Trace1275750")),
    ),
)
def test_certificate_reversed_trace_set_rejects_wrong_missing_duplicate_and_case_variants(
    tmp_path: Path, label: str, certificate_traces: tuple[str, ...]
) -> None:
    fixture_root = tmp_path / f"certificate-{label}"
    fixture_root.mkdir()
    fixture = _fake_run_fixture(fixture_root, certificate_traces=certificate_traces)
    hq = json.loads(fixture.hq.read_text(encoding="utf-8"))
    assert hq["expected_result"]["reversed_traces"] == list(REVERSED_TRACES)
    result = _run_fake_normal(fixture)
    assert result.returncode == 2
    assert "certificate reversed traces" in result.stderr


def test_prefixed_reversed_trace_ids_are_sealed_and_checked(tmp_path: Path) -> None:
    fixture = _fake_run_fixture(tmp_path)
    result = _run_fake_normal(fixture)
    assert result.returncode == 0, result.stderr
    receipt = _read_receipt(fixture)
    assert receipt["expected_scientific_summary"]["reversed_traces"] == [
        "Trace1258306",
        "Trace1258307",
        "Trace1275750",
        "Trace1275751",
    ]
    assert "Trace1258306" in SCRIPT.read_text(encoding="utf-8")


def test_retained_hq_handle_blocks_substitution_and_postflight_identity_matches(tmp_path: Path) -> None:
    fixture = _fake_run_fixture(tmp_path, mode="hq_substitute")
    result = _run_fake_normal(fixture)
    assert result.returncode == 0, result.stderr
    receipt = _read_receipt(fixture)
    assert receipt["identities"]["before"]["hq"] == receipt["identities"]["after"]["hq"]
    assert not (fixture.artifacts / "hq-replacement.json").exists()


@pytest.mark.parametrize("mode", ("token_mutation", "input_drift", "source_drift", "controller_drift"))
def test_post_token_drift_produces_evidence_complete_stop_receipt(tmp_path: Path, mode: str) -> None:
    fixture = _fake_run_fixture(tmp_path, mode=mode)
    result = _run_fake_normal(fixture)
    assert result.returncode == 2
    assert fixture.receipt.exists()
    receipt = _read_receipt(fixture)
    assert receipt["status"] == "STOP"
    for key in ("pins", "identities", "argv", "counts", "process", "capture", "token", "certificate", "inventories", "expected_scientific_summary"):
        assert key in receipt
    assert receipt["process"]["timeout_tree_cleanup"] is True
    assert receipt["process"]["attempts"] == 1
    assert receipt["capture"]["cap_chars"] == 65536


@pytest.mark.parametrize("mode", ("fixed_leaf_rejection", "controller_leaf_rejection"))
def test_fixed_token_and_controller_receipt_leaf_names_reject_before_mutation(tmp_path: Path, mode: str) -> None:
    fixture = _fake_run_fixture(tmp_path, mode=mode)
    result = _run_fake_normal(fixture)
    assert result.returncode == 2
    assert not fixture.token.exists()
    assert not fixture.certificate.exists()
    assert not fixture.receipt.exists()
    assert "leaf name mismatch" in result.stderr


def test_python_file_version_mismatch_rejects_before_token_creation(tmp_path: Path) -> None:
    fixture = _fake_run_fixture(tmp_path, mode="python_version_mismatch")
    result = _run_fake_normal(fixture)
    assert result.returncode == 2
    assert "Python file version mismatch" in result.stderr
    assert not fixture.token.exists()


def test_certificate_is_bounded_and_parsed_from_the_same_read_buffer() -> None:
    text = SCRIPT.read_text(encoding="utf-8")
    assert "Read-BoundedFileBytes $Path $CertificateSize 'certificate'" in text
    assert "Read-StrictJsonBytes $bound.Bytes 'certificate'" in text
    assert "Read-StrictJsonFile $Path 'certificate'" not in text
