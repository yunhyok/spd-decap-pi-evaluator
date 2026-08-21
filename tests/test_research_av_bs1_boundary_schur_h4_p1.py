"""Frozen, manifest-only checks for the prospective AV-BS1 H4-P1 contract.

The production module is deliberately unable to authorize or perform an H4
solve.  These tests exercise only its read-only manifest and failure surfaces.
"""
from __future__ import annotations

import ast
import builtins
from copy import deepcopy
from hashlib import sha256
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools/research"
SOURCE = TOOLS / "av_bs1_boundary_schur_h4_p1.py"
TOKEN = ROOT / "tools/research/av_bs1_h4_p0r_p1_review_token.json"
SOURCE_BYTES = 47_295
SOURCE_LF = 852
SOURCE_SHA256 = "6f527c76cbc0928a4b504e01abbb4c049fd39edc75ef865a3335f3a619863200"
PAYLOAD_SHA256 = "ec489c784e01635955560fa0ac0681b22bd890759d6693c111cb8ff28c31b753"
WRAPPER_SHA256 = "cfa8f9a2504a8fad40ef03e9cb3842877e176a9f3d620f5805099a98c80523e7"
MATRIX_CONTRACT_SHA256 = "9d5106c3086d04fa71c19f37bbf22230c3ea5f4696c4dd47e07cc727845f2910"
EXECUTION_CONTRACT_SHA256 = "251f645fc4f6e4ef489dabffe63e5bb88ec2f4cff8493abd5110846f9facf52c"
ACCURACY_GATES_SHA256 = "7aacde866ccf22a7d81e54fda6c95346e65d4f6de8918617b84d1f51d0e69e6d"
SIDECAR_DESCRIPTOR_SHA256 = "fd664715dfc151cc1f4bd9bbebfd1cf6c6d9c2da37c9c723699c4129fed03cf6"
RESOURCE_POLICY_SHA256 = "d12da67dbc23cc2bc34755d1fa73c45aa993561cd5be4de75d95402cdc638b26"

EXPECTED_MATRIX_CONTRACT: dict[str, object] = {
    "aggregate_matrix_contract_sha256":
        "89fadbf8f7f93118f6cccda65cc635bd37eeecfd70652e40baa6014c722e2f46",
    "K_II_sha256":
        "dddee397ee96ff0bd7acc321cd5755dd611f1e3d1f6bfcf9664c388d27184361",
    "M_II_sha256":
        "4dccc26f1a12bbab4a4ee863e509d317cb518075661867da4e0b6980f7dcc6c4",
    "frequency_hz": 100_000.0,
    "angular_frequency_rad_per_s": 628318.5307179586,
    "conductor_sigma_siemens_per_m": 59_600_000.0,
    "A_background_II_formula": "complex128(K_II)",
    "A_conductor_II_formula": (
        "complex128(K_II)+1j*angular_frequency_rad_per_s*"
        "conductor_sigma_siemens_per_m*M_II"
    ),
    "A_background_II": {
        "raw_sha256":
            "8c099d2cb5947d1b72bd74f64329879c221b86c569b4100baa47dfea9aeff641",
        "row_scale_Dr_sha256":
            "13565a65f7c443cb5aadb9dc01cfd0b9e0c7d71bd5b92d4f62e8a2723120732e",
        "column_scale_Dc_sha256":
            "879a3dfdcde792239d2d2ad235c36e96c02eeb75f989c45adcaf3eee50cbc633",
        "equilibrated_Aeq_sha256":
            "9216cc8938d9efc1e531a4fb301aca45547fad55f01dec57863986622327dd74",
    },
    "A_conductor_II": {
        "raw_sha256":
            "5ead3bbdc2fc1b4f7a1a94bb7c9ebf9401c6a7d96ee63335045d64e3b082225f",
        "row_scale_Dr_sha256":
            "2e7a7b1c7d1bc2b51d33efd4185a2214422aaea0530d3483348c53422311987d",
        "column_scale_Dc_sha256":
            "0a531cd1ded535c412bb9151298e108a429bc82e29b96f665d8ba1448acba3f4",
        "equilibrated_Aeq_sha256":
            "b4efc2d388b2fe5c3280e8481d5afedd497952da3f18b34a86674a7ce97daf54",
    },
}

EXPECTED_CORE_BINDINGS = {
    "tools/research/av_bs1_boundary_schur_h4_p0r_p1.py":
        "46082123fbf98102f3e5995c32bf65d8d04bb835b9c1305de0150349e75e48c7",
    "tools/research/run_av_bs1_h4_p0r_p1_stage.ps1":
        "f0159061dbd4d1b34881911edfdfb72146a3a23e5cdc75ca8fa4069c08aadb86",
    "tests/test_research_av_bs1_boundary_schur_h4_p0r_p1.py":
        "e5496ceb24c33c835b88ce20a3f67f941c8279e0471708a01022238fc211b109",
}

EXPECTED_POSTPASS_DOC_BINDINGS = {
    "README.md": "9eededb053f33e54bd99302cee68f47bb2e7e74b88c8cbc950c19b6d9517e696",
    "docs/evaluation-research/ALGORITHM_CANDIDATES.md": "17e2b4404003f8e1b17c289014102551fb1d27c4ba3ec4bcb29dae50709600cf",
    "docs/evaluation-research/LOCAL_ORACLE_PLAN.md": "6db62ba8e27ebd6ec4a8f29c821d399c914faa3de5c012439ee05e78e560cc1b",
    "docs/evaluation-research/ORACLE_REPRODUCTION.md": "bcb6ee896123c5a65bda98c4a533512cf3e0c515d2fa902aa3a301e94551c151",
    "docs/evaluation-research/R2_ORACLE_RESULTS.md": "fcac10d7cb007ed4b8abe5514c7fbfa1d7b5aa72e4f82ca15bed2c95245b4e79",
    "docs/evaluation-research/README.md": "95b0471618ee319102b72f58811dff7dcf39970d36e6ebf9f28974fae370d774",
    "docs/evaluation-research/RESEARCH_STATE.md": "3dbe16b3cc52b1babec025654821a726b065bf8ed1fa5bd29efdfe8b048425b8",
    "docs/evaluation-research/SESSION_LOG.md": "0d44c6a6abebe1a48fba351f18bced194428ee13a47792a8fd8386da1f9ae3cb",
    "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md": "51678600dfc309250325d68cb30b5d6867256db6abc487c30e5afea62a94f57f",
    "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_RESULTS.md": "c9a3cfd622e4508ac2f64d993480303aa6e6cef1a1b3145549d7906ca68e7aec",
    "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_SPEC.md": "2080fa291263afed062b503043b7aeff11c3b74fe7b9f9df7a3a2822702a131d",
    "docs/evaluation-research/T1_CIRCLE_DTN_RESULTS.md": "392356adb52e5bf3155925acb242fb068fd0b0b16c82d3f6136148d435e49214",
    "docs/evaluation-research/T1_M1_EQ0_RESULTS.md": "ca599f4e13b48291bc0e4d222c82a0a8475f4be50df5bf89c641ad15356b742c",
    "docs/evaluation-research/T1_M1_REFERENCE_SPEC.md": "860aee6019bebdadf115f753ce359e3dceddbb7532d7b22a9fa9aeed29f0999c",
    "docs/evaluation-research/T1_TRACE_ORACLE_RESULTS.md": "ca3b206a97f934f7e38e85826af7c2a0ba8cf380b114f0ec213f139ffa3281c0",
}

EXPECTED_EVIDENCE_COMMITMENTS = {
    "claim": {"raw_sha256": "7ee75af33cdba5ee9cb4f92ff4acc601a7939717eda24ffd35536f7275c646b9", "canonical_sha256": "00611c2a7425d0100f0c49abe881b43978645355e73946af23d5424254907b68"},
    "guard": {"raw_sha256": "f8454d99b28195070a8b22ee870df6a0a95d9dc3f35b83aa7ea107ad55f7e7c6", "canonical_sha256": "f99f1f20362eb7d7cae04f38cc20c01e4c56bf5500dd437914642699f439f755"},
    "numerical": {"raw_sha256": "0af448eeea2eb302f6aeebbb04fd01992067ccc55a017eb780507aa34924728f", "payload_sha256": "b236e160b41150b856fbd69f98ab682edb3c61d6f3a53f02c5960c553f1a5c78"},
    "factor_prefix_1": {"raw_sha256": "d37940d04ede5820c020d5b352c418592f344db89a4db6430762339b115b50e5", "payload_sha256": "7e9b6c9d719c8c782f7727a49ea53945867b76721e79b67d143205699b4e49cb"},
    "factor_prefix_2": {"raw_sha256": "174f6cca5f808094a306c681a9502b9551c7d741657d1d0db62b9510ca74a3e8", "payload_sha256": "45e1a18105128778ffae2d0e53aeb2621fd080d077d4a775020f3c52cd0d5ab7"},
    "resource": {"raw_sha256": "d6358608dd97c0f245bb781d0bfc63e929a1a74733907a7fa2a1fbdb9dca97de", "canonical_sha256": "537350ed7b9b9d14206624ed8fd46014de6cb0d417c0611b7753170bae9abf84"},
    "result": {"raw_sha256": "ad007d6ac4c053981f6ab9da72505848956019cd4b5cdb1e1ecfa7b5e0d02121", "payload_sha256": "0d33860805d50e887fe8a43c9adea3da81df99df73a812ca7559c92419bdb6b0"},
    "pre_exit": {"raw_sha256": "5ff55a34ac6687cd2a86c1c890b50b7ae43b2917e862d9a6c6a001d70e10cadb", "canonical_sha256": "b5cb10f9a8b8eb5fc40f04b4ff11984d497d0ddf178e34963db1aec1e3b61207"},
    "outer_close": {"raw_sha256": "184302a02026e1ab9aa84d4a94a2f9605c8254849920379d6b30fb0ce765f3c5", "canonical_sha256": "184302a02026e1ab9aa84d4a94a2f9605c8254849920379d6b30fb0ce765f3c5"},
    "terminal_seal": {"raw_sha256": "070c86e25aeb982d5ef1c9c99c9c15d9ed3a6d08ce4e521e55262e4586a516a4", "canonical_sha256": "8c9de1f93bccac285d81ca60d7b9bc827cb1e66ffc2fe036825a008a491513b6"},
    "consumed_tombstone": {"raw_sha256": "3ce2bc00bdb4ee2d2da1a706e7c2211eba2a0579e179990aa34d2e2855cd615f", "canonical_sha256": "3ce2bc00bdb4ee2d2da1a706e7c2211eba2a0579e179990aa34d2e2855cd615f"},
}


# This validation intentionally runs before any dynamic execution of SOURCE.
_PREVALIDATED_SOURCE_RAW = SOURCE.read_bytes()
if len(_PREVALIDATED_SOURCE_RAW) != SOURCE_BYTES:
    raise AssertionError("H4-P1 source byte count changed before import")
if sha256(_PREVALIDATED_SOURCE_RAW).hexdigest() != SOURCE_SHA256:
    raise AssertionError("H4-P1 source SHA-256 changed before import")
if _PREVALIDATED_SOURCE_RAW.count(b"\n") != SOURCE_LF or b"\r" in _PREVALIDATED_SOURCE_RAW:
    raise AssertionError("H4-P1 source LF contract changed before import")
_PREVALIDATED_SOURCE_TEXT = _PREVALIDATED_SOURCE_RAW.decode("utf-8", errors="strict")
_PREVALIDATED_SOURCE_AST = ast.parse(
    _PREVALIDATED_SOURCE_TEXT, filename=str(SOURCE)
)
_MODULE_SPEC = importlib.util.spec_from_file_location(
    "av_bs1_boundary_schur_h4_p1", SOURCE
)
if _MODULE_SPEC is None or _MODULE_SPEC.loader is None:
    raise AssertionError("cannot construct H4-P1 module spec")
p1 = importlib.util.module_from_spec(_MODULE_SPEC)
sys.modules[_MODULE_SPEC.name] = p1
_MODULE_SPEC.loader.exec_module(p1)


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def _canonical_sha(value: object) -> str:
    return sha256(_canonical_bytes(value)).hexdigest()


def _assert_exact(actual: object, expected: object) -> None:
    if isinstance(expected, dict):
        assert isinstance(actual, dict)
        assert set(actual) == set(expected)
        for key, expected_value in expected.items():
            _assert_exact(actual[key], expected_value)
    elif isinstance(expected, list):
        assert isinstance(actual, list)
        assert len(actual) == len(expected)
        for actual_value, expected_value in zip(actual, expected, strict=True):
            _assert_exact(actual_value, expected_value)
    elif expected is True:
        assert actual is True
    elif expected is False:
        assert actual is False
    elif expected is None:
        assert actual is None
    else:
        assert type(actual) is type(expected)
        assert actual == expected


_REAL_SUBPROCESS_RUN = subprocess.run
_OPTIONAL_LOCK_OBSERVATIONS: list[tuple[tuple[str, ...], str]] = []


def _git_environment(source: object = None) -> dict[str, str]:
    environment = os.environ.copy() if source is None else dict(source)
    environment["GIT_OPTIONAL_LOCKS"] = "0"
    return environment


def _allowed_git_argv() -> frozenset[tuple[str, ...]]:
    commands: set[tuple[str, ...]] = {
        ("git", "status", "--porcelain=v1", "-z", "--untracked-files=all"),
        ("git", "rev-parse", "HEAD^{tree}"),
        ("git", "merge-base", "--is-ancestor", p1.POSTPASS_DOCS_COMMIT, "HEAD"),
        ("git", "rev-parse", f"{p1.ORIGINAL_TOKEN_COMMIT}^"),
        ("git", "rev-parse", f"{p1.CONSUMED_TOKEN_COMMIT}^"),
        ("git", "rev-parse", f"{p1.RETIREMENT_COMMIT}^"),
        ("git", "rev-parse", f"{p1.POSTPASS_DOCS_COMMIT}^"),
        ("git", "ls-files", "--stage", "--", p1.TOKEN_PATH),
        ("git", "ls-tree", "--name-only", "HEAD", "--", p1.TOKEN_PATH),
    }
    for commit in (
        p1.ORIGINAL_TOKEN_COMMIT, p1.CONSUMED_TOKEN_COMMIT,
        p1.RETIREMENT_COMMIT, p1.POSTPASS_DOCS_COMMIT,
    ):
        commands.add(
            ("git", "diff-tree", "--no-commit-id", "--name-status", "-r", commit)
        )
    blob_specs = {
        f"{p1.ORIGINAL_TOKEN_COMMIT}:{p1.TOKEN_PATH}",
        f"{p1.CONSUMED_TOKEN_COMMIT}:{p1.TOKEN_PATH}",
        *{
            f"{p1.POSTPASS_DOCS_COMMIT}:{path}"
            for path in [*EXPECTED_CORE_BINDINGS, *EXPECTED_POSTPASS_DOC_BINDINGS]
        },
        (
            f"{p1.BASE_CONTRACT_COMMIT}:"
            "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md"
        ),
    }
    for spec in blob_specs:
        commands.add(("git", "cat-file", "-s", spec))
        commands.add(("git", "cat-file", "blob", spec))
    return frozenset(commands)


ALLOWED_GIT_ARGV = _allowed_git_argv()


def _assert_source_still_frozen() -> None:
    raw = SOURCE.read_bytes()
    assert raw == _PREVALIDATED_SOURCE_RAW
    assert sha256(raw).hexdigest() == SOURCE_SHA256
    assert raw.count(b"\n") == SOURCE_LF
    assert b"\r" not in raw
    ast.parse(raw.decode("utf-8", errors="strict"), filename=str(SOURCE))


def _repository_sentinel() -> tuple[bytes, bytes, str, str, bool]:
    git_environment = _git_environment()
    assert git_environment["GIT_OPTIONAL_LOCKS"] == "0"
    status = _REAL_SUBPROCESS_RUN(
        ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"],
        cwd=ROOT, env=git_environment, shell=False, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout
    head_tree = _REAL_SUBPROCESS_RUN(
        ["git", "rev-parse", "HEAD^{tree}"],
        cwd=ROOT, env=git_environment, shell=False, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    ).stdout
    return (
        status,
        head_tree,
        sha256(SOURCE.read_bytes()).hexdigest(),
        sha256(Path(__file__).read_bytes()).hexdigest(),
        TOKEN.exists() or TOKEN.is_symlink(),
    )


@pytest.fixture(scope="session", autouse=True)
def no_write_no_factor_and_repository_sentinel() -> object:
    """Fail closed on mutation/factor entry points and prove pre/post state."""
    import scipy.sparse.linalg as sparse_linalg

    before = _repository_sentinel()
    patch = pytest.MonkeyPatch()
    real_builtin_open = builtins.open
    real_io_open = io.open
    real_os_open = os.open

    def mode_is_writable(mode: object) -> bool:
        return any(marker in str(mode) for marker in ("w", "a", "x", "+"))

    def guarded_builtin_open(
        file: object, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if mode_is_writable(mode):
            raise AssertionError(f"filesystem write forbidden in H4-P1 tests: {file!r}")
        return real_builtin_open(file, mode, *args, **kwargs)

    def guarded_io_open(
        file: object, mode: str = "r", *args: object, **kwargs: object
    ) -> object:
        if mode_is_writable(mode):
            raise AssertionError(f"filesystem write forbidden in H4-P1 tests: {file!r}")
        return real_io_open(file, mode, *args, **kwargs)

    write_flags = (
        os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC
    )

    def guarded_os_open(
        path: object, flags: int, mode: int = 0o777, *, dir_fd: int | None = None
    ) -> int:
        if flags & write_flags:
            raise AssertionError(f"os.open write forbidden in H4-P1 tests: {path!r}")
        if dir_fd is None:
            return real_os_open(path, flags, mode)
        return real_os_open(path, flags, mode, dir_fd=dir_fd)

    def forbidden_mutation(*args: object, **_kwargs: object) -> object:
        raise AssertionError(f"filesystem mutation forbidden in H4-P1 tests: {args!r}")

    def forbidden_factor(*args: object, **_kwargs: object) -> object:
        raise AssertionError(f"numeric factor/solve forbidden in H4-P1 tests: {args!r}")

    def guarded_subprocess_run(command: object, *args: object, **kwargs: object) -> object:
        if not isinstance(command, (list, tuple)) or not command:
            raise AssertionError(f"non-list subprocess forbidden: {command!r}")
        if args:
            raise AssertionError("positional subprocess options are forbidden")
        if kwargs.get("shell", False):
            raise AssertionError("shell=True is forbidden in H4-P1 tests")
        if kwargs.get("executable") is not None:
            raise AssertionError("subprocess executable override is forbidden")
        cwd = kwargs.get("cwd")
        if cwd is None or Path(cwd) != ROOT:
            raise AssertionError(f"subprocess cwd must be exact repository root: {cwd!r}")

        argv = tuple(os.fspath(item) for item in command)
        git_read = argv in ALLOWED_GIT_ARGV
        allowed_manifest_arguments = {
            (), ("--stage", "manifest"), ("--stage", "unknown"),
            ("--stage", "primary-h4"), ("--stage", "withheld"),
            ("--stage", "EQ0"), ("--stage", "preflight"),
            ("--stage", "consume"), ("--stage", "manifest", "extra"),
        }
        manifest_cli = (
            len(argv) >= 2
            and Path(argv[0]).resolve() == Path(sys.executable).resolve()
            and Path(argv[1]).resolve() == SOURCE.resolve()
            and argv[2:] in allowed_manifest_arguments
        )
        if not (git_read or manifest_cli):
            raise AssertionError(f"subprocess forbidden in H4-P1 tests: {argv!r}")
        if git_read:
            environment = _git_environment(kwargs.get("env"))
            assert environment["GIT_OPTIONAL_LOCKS"] == "0"
            kwargs["env"] = environment
            _OPTIONAL_LOCK_OBSERVATIONS.append(
                (argv, environment["GIT_OPTIONAL_LOCKS"])
            )
        elif dict(kwargs.get("env") or {}).get("GIT_OPTIONAL_LOCKS") != "0":
            raise AssertionError("manifest child must inherit GIT_OPTIONAL_LOCKS=0")
        kwargs["shell"] = False
        return _REAL_SUBPROCESS_RUN(command, *args, **kwargs)

    patch.setattr(builtins, "open", guarded_builtin_open)
    patch.setattr(io, "open", guarded_io_open)
    patch.setattr(os, "open", guarded_os_open)
    patch.setattr(subprocess, "run", guarded_subprocess_run)
    for name in ("remove", "unlink", "rename", "replace", "mkdir", "makedirs", "rmdir"):
        patch.setattr(os, name, forbidden_mutation)
    for name in ("write_text", "write_bytes", "touch", "mkdir", "unlink", "rename", "replace", "rmdir"):
        patch.setattr(Path, name, forbidden_mutation)
    for name in ("splu", "spsolve", "factorized"):
        patch.setattr(sparse_linalg, name, forbidden_factor)

    try:
        yield
        assert _repository_sentinel() == before
    finally:
        patch.undo()


def _run_cli(*arguments: str) -> subprocess.CompletedProcess[bytes]:
    _assert_source_still_frozen()
    environment = _git_environment()
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    assert environment["GIT_OPTIONAL_LOCKS"] == "0"
    return subprocess.run(
        [sys.executable, str(SOURCE), *arguments],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
        timeout=30,
    )


@pytest.fixture(scope="session")
def wrapper() -> dict[str, object]:
    _assert_source_still_frozen()
    completed = _run_cli("--stage", "manifest")
    assert completed.returncode == 0, completed.stderr.decode("utf-8", errors="replace")
    assert completed.stderr == b""
    value = json.loads(completed.stdout.decode("utf-8", errors="strict"))
    assert isinstance(value, dict)
    return value


def _qualified_name(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def test_source_is_exact_frozen_utf8_lf_and_ast_parseable() -> None:
    _assert_source_still_frozen()
    raw = _PREVALIDATED_SOURCE_RAW
    assert len(raw) == SOURCE_BYTES
    assert sha256(raw).hexdigest() == SOURCE_SHA256
    assert raw.count(b"\n") == SOURCE_LF
    assert b"\r" not in raw
    assert not raw.startswith(b"\xef\xbb\xbf")
    assert isinstance(_PREVALIDATED_SOURCE_AST, ast.Module)
    assert p1.PROGRAM == "SPD Decap PI Evaluator v0.22.0"
    assert p1.SCHEMA == "AV-BS1-h4-p1-manifest-v1"
    assert p1.FAILURE_SCHEMA == "AV-BS1-h4-p1-manifest-failure-v1"


def test_source_call_graph_is_manifest_only_and_has_no_mutating_sink() -> None:
    text = SOURCE.read_text(encoding="utf-8")
    tree = ast.parse(text, filename=str(SOURCE))
    imports: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imports.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.add(node.module)
    assert imports == {
        "__future__", "argparse", "hashlib", "json", "math", "pathlib",
        "subprocess", "typing",
    }
    assert "validation-output" not in text
    assert not any(
        isinstance(node, (ast.AsyncFunctionDef, ast.AsyncFor, ast.AsyncWith))
        for node in ast.walk(tree)
    )

    forbidden_call_leaves = {
        "splu", "spsolve", "factorized", "solve", "onenormest", "open",
        "write", "write_text", "write_bytes", "touch", "mkdir", "makedirs",
        "replace", "rename", "unlink", "remove", "rmdir", "finalize",
        "consume", "preflight", "primary",
    }
    calls = [
        _qualified_name(node.func)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ]
    assert not {name.rsplit(".", 1)[-1] for name in calls} & forbidden_call_leaves
    definitions = {
        node.name for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)
    }
    assert not any(
        marker in name.lower()
        for name in definitions
        for marker in ("preflight", "primary", "finalize", "consume", "write")
    )

    subprocess_owners: list[str] = []
    subprocess_calls: list[ast.Call] = []
    for function in (node for node in ast.walk(tree) if isinstance(node, ast.FunctionDef)):
        for node in ast.walk(function):
            if isinstance(node, ast.Call) and _qualified_name(node.func) == "subprocess.run":
                subprocess_owners.append(function.name)
                subprocess_calls.append(node)
    assert subprocess_owners == ["_git"]
    command = subprocess_calls[0].args[0]
    assert isinstance(command, ast.List)
    assert isinstance(command.elts[0], ast.Constant)
    assert command.elts[0].value == "git"


def test_git_helper_rejects_every_non_read_only_command_before_spawn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = False

    def forbidden_spawn(*_args: object, **_kwargs: object) -> object:
        nonlocal spawned
        spawned = True
        raise AssertionError("blocked command reached subprocess")

    monkeypatch.setattr(p1.subprocess, "run", forbidden_spawn)
    for arguments in (
        ["status"], ["show", "HEAD:x"], ["add", "x"], ["commit"],
        ["merge-base", "HEAD", "HEAD"],
    ):
        with pytest.raises(p1.AvBsError, match="non-read-only|unsupported merge-base"):
            p1._git(arguments)
    assert spawned is False


@pytest.mark.parametrize(
    "command",
    [
        [
            "git", "diff-tree", "--no-commit-id", "--name-status", "-r",
            "--output=forbidden.txt", "e35ef01214f4bf9ec75e7b428e72b21d38c9161b",
        ],
        [
            "git", "diff-tree", "--ext-diff", "--no-commit-id",
            "--name-status", "-r", "e35ef01214f4bf9ec75e7b428e72b21d38c9161b",
        ],
    ],
)
def test_subprocess_guard_rejects_diff_tree_output_and_external_diff(
    command: list[str],
) -> None:
    with pytest.raises(AssertionError, match="subprocess forbidden"):
        subprocess.run(
            command, cwd=ROOT, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )


def test_subprocess_guard_rejects_wrong_cwd_shell_and_executable_override() -> None:
    allowed = ["git", "status", "--porcelain=v1", "-z", "--untracked-files=all"]
    with pytest.raises(AssertionError, match="cwd must be exact"):
        subprocess.run(allowed, cwd=ROOT.parent, check=False)
    with pytest.raises(AssertionError, match="shell=True"):
        subprocess.run(allowed, cwd=ROOT, shell=True, check=False)
    with pytest.raises(AssertionError, match="executable override"):
        subprocess.run(allowed, cwd=ROOT, executable="git", check=False)


def test_every_permitted_git_child_forces_optional_locks_off() -> None:
    command = ["git", "ls-files", "--stage", "--", p1.TOKEN_PATH]
    environment = os.environ.copy()
    environment["GIT_OPTIONAL_LOCKS"] = "1"
    _OPTIONAL_LOCK_OBSERVATIONS.clear()
    completed = subprocess.run(
        command, cwd=ROOT, env=environment, shell=False, check=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    assert completed.stdout == b""
    assert completed.stderr == b""
    assert _OPTIONAL_LOCK_OBSERVATIONS == [(tuple(command), "0")]


def test_cli_manifest_is_canonical_deterministic_and_hash_pinned(
    wrapper: dict[str, object],
) -> None:
    repeated = _run_cli("--stage", "manifest")
    assert repeated.returncode == 0
    assert repeated.stderr == b""
    repeated_value = json.loads(repeated.stdout.decode("utf-8", errors="strict"))
    assert repeated_value == wrapper
    assert repeated.stdout.rstrip(b"\r\n") == _canonical_bytes(wrapper)

    payload = wrapper["payload"]
    assert wrapper["payload_sha256"] == PAYLOAD_SHA256
    assert wrapper["wrapper_sha256"] == WRAPPER_SHA256
    assert _canonical_sha(payload) == PAYLOAD_SHA256
    assert _canonical_sha(
        {"payload": payload, "payload_sha256": PAYLOAD_SHA256}
    ) == WRAPPER_SHA256
    assert p1.validated_manifest_wrapper() == wrapper


def test_parser_exposes_only_manifest_and_rejects_future_solve_stages() -> None:
    parser = p1._parser()
    stage_actions = [action for action in parser._actions if action.dest == "stage"]
    assert len(stage_actions) == 1
    assert tuple(stage_actions[0].choices) == ("manifest",)
    assert parser.parse_args(["--stage", "manifest"]).stage == "manifest"
    invalid_cases = [
        ((), b"required"),
        (("--stage", "unknown"), b"invalid choice"),
        (("--stage", "primary-h4"), b"invalid choice"),
        (("--stage", "withheld"), b"invalid choice"),
        (("--stage", "EQ0"), b"invalid choice"),
        (("--stage", "preflight"), b"invalid choice"),
        (("--stage", "consume"), b"invalid choice"),
        (("--stage", "manifest", "extra"), b"unrecognized arguments"),
    ]
    for arguments, expected_stderr in invalid_cases:
        completed = _run_cli(*arguments)
        assert completed.returncode == 2
        assert completed.stdout == b""
        assert expected_stderr in completed.stderr


def test_provenance_is_exact_and_reads_frozen_git_blobs_not_live_docs(
    wrapper: dict[str, object], monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = wrapper["payload"]
    provenance = payload["provenance"]
    assert provenance["required_ancestor_commit"] == p1.POSTPASS_DOCS_COMMIT == (
        "50f9e908d0fc740c05e84c6ae65262dd8774553a"
    )
    assert provenance["base_contract_commit"] == "01d198a851be7ce08db963507bb902986eb0dd15"
    assert provenance["original_token_commit"] == "e35ef01214f4bf9ec75e7b428e72b21d38c9161b"
    assert provenance["consumed_token_commit"] == "bf94e1890c489e690801b3d90bcbf50ff61ca233"
    assert provenance["retirement_commit"] == "389ec1e51b87f3eff928278c76bbbf60241af972"
    assert provenance["core_bindings"] == EXPECTED_CORE_BINDINGS
    assert provenance["postpass_document_bindings"] == EXPECTED_POSTPASS_DOC_BINDINGS
    assert len(provenance["postpass_document_bindings"]) == 15
    assert provenance["runtime_preregistration_doc_sha256"] == (
        "954a91feb1639b0eb0cb2f62c2068af7a388fc7d55fc2370e909ca32da7968b7"
    )
    assert provenance["postpass_preregistration_doc_sha256"] == (
        "51678600dfc309250325d68cb30b5d6867256db6abc487c30e5afea62a94f57f"
    )

    observed_commands: list[tuple[str, ...]] = []
    real_git = p1._git

    def recording_git(arguments: list[str], *, maximum_bytes: int = 2 * 1024 * 1024) -> bytes:
        observed_commands.append(tuple(arguments))
        return real_git(arguments, maximum_bytes=maximum_bytes)

    monkeypatch.setattr(p1, "_git", recording_git)
    p1._validate_lifecycle_and_provenance()
    expected_commands: list[tuple[str, ...]] = [
        ("merge-base", "--is-ancestor", p1.POSTPASS_DOCS_COMMIT, "HEAD"),
        ("rev-parse", f"{p1.ORIGINAL_TOKEN_COMMIT}^"),
        ("rev-parse", f"{p1.CONSUMED_TOKEN_COMMIT}^"),
        ("rev-parse", f"{p1.RETIREMENT_COMMIT}^"),
        ("rev-parse", f"{p1.POSTPASS_DOCS_COMMIT}^"),
        ("diff-tree", "--no-commit-id", "--name-status", "-r", p1.ORIGINAL_TOKEN_COMMIT),
        ("diff-tree", "--no-commit-id", "--name-status", "-r", p1.CONSUMED_TOKEN_COMMIT),
        ("diff-tree", "--no-commit-id", "--name-status", "-r", p1.RETIREMENT_COMMIT),
        ("diff-tree", "--no-commit-id", "--name-status", "-r", p1.POSTPASS_DOCS_COMMIT),
    ]
    for commit, path in (
        (p1.ORIGINAL_TOKEN_COMMIT, p1.TOKEN_PATH),
        (p1.CONSUMED_TOKEN_COMMIT, p1.TOKEN_PATH),
    ):
        spec = f"{commit}:{path}"
        expected_commands.extend([
            ("cat-file", "-s", spec), ("cat-file", "blob", spec),
        ])
    for path in [*EXPECTED_CORE_BINDINGS, *EXPECTED_POSTPASS_DOC_BINDINGS]:
        spec = f"{p1.POSTPASS_DOCS_COMMIT}:{path}"
        expected_commands.extend([
            ("cat-file", "-s", spec), ("cat-file", "blob", spec),
        ])
    runtime_spec = (
        f"{p1.BASE_CONTRACT_COMMIT}:"
        "docs/evaluation-research/T1_AV_BOUNDARY_SCHUR_H4_P0R_P1_PREREG.md"
    )
    expected_commands.extend([
        ("cat-file", "-s", runtime_spec),
        ("cat-file", "blob", runtime_spec),
        ("ls-files", "--stage", "--", p1.TOKEN_PATH),
        ("ls-tree", "--name-only", "HEAD", "--", p1.TOKEN_PATH),
    ])
    assert observed_commands == expected_commands


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("parent", "lifecycle parent mismatch"),
        ("diff", "lifecycle scope mismatch"),
        ("blob", "post-pass committed blob mismatch"),
    ],
)
def test_provenance_parent_diff_and_committed_blob_tamper_fail_closed(
    tamper: str, message: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    real_git = p1._git
    real_blob = p1._git_blob

    def tampered_git(
        arguments: list[str], *, maximum_bytes: int = 2 * 1024 * 1024
    ) -> bytes:
        if tamper == "parent" and arguments == [
            "rev-parse", f"{p1.ORIGINAL_TOKEN_COMMIT}^",
        ]:
            return ("0" * 40 + "\n").encode("ascii")
        if tamper == "diff" and arguments == [
            "diff-tree", "--no-commit-id", "--name-status", "-r",
            p1.ORIGINAL_TOKEN_COMMIT,
        ]:
            return b"M\tunexpected-path\n"
        return real_git(arguments, maximum_bytes=maximum_bytes)

    def tampered_blob(
        commit: str, path: str, *, maximum_bytes: int = 2 * 1024 * 1024
    ) -> bytes:
        raw = real_blob(commit, path, maximum_bytes=maximum_bytes)
        if tamper == "blob" and commit == p1.POSTPASS_DOCS_COMMIT and path == "README.md":
            return raw + b" "
        return raw

    monkeypatch.setattr(p1, "_git", tampered_git)
    monkeypatch.setattr(p1, "_git_blob", tampered_blob)
    with pytest.raises(p1.AvBsError, match=message):
        p1._validate_lifecycle_and_provenance()


def test_current_token_is_absent_from_worktree_index_and_head(
    wrapper: dict[str, object],
) -> None:
    payload = wrapper["payload"]
    assert not TOKEN.exists()
    assert not TOKEN.is_symlink()
    assert p1._git(["ls-files", "--stage", "--", p1.TOKEN_PATH]) == b""
    assert p1._git(["ls-tree", "--name-only", "HEAD", "--", p1.TOKEN_PATH]) == b""
    assert payload["authorization_state"] == "not_authorized"
    assert payload["token_state"] == "absent"
    assert payload["token_absence_validated"] is True
    assert payload["provenance"]["current_token_absence_validated_in_worktree_index_and_head"] is True


def test_historical_parent_evidence_is_exact_but_not_live_revalidated(
    wrapper: dict[str, object],
) -> None:
    historical = wrapper["payload"]["historical_parent_evidence"]
    assert historical["attempt_ordinal"] == 11
    assert historical["review_token_id"] == "2435fa59edc64efca2fb4665b0854d43"
    assert historical["classification"] == "consumed_v2_authoritative_sealed_pass"
    assert historical["status"] == (
        "consumed_v2_authoritative_sealed_pass_no_next_stage_authorization"
    )
    assert historical["published_result_pre_seal_authoritative"] is False
    assert historical["consumed_tombstone_pre_seal_authoritative"] is False
    assert historical["terminal_seal_historical_authoritative"] is True
    assert historical["terminal_authority_source"] == (
        "retained_terminal_seal_plus_full_chain_as_audited_at_postpass_docs_commit"
    )
    assert historical["factor_order"] == ["A_background_II", "A_conductor_II"]
    assert historical["evidence_hash_commitments"] == EXPECTED_EVIDENCE_COMMITMENTS
    assert historical["ignored_evidence_files_opened_by_manifest"] is False
    assert historical["evidence_hash_commitments_live_revalidated"] is False
    assert historical["commitment_validation_note"] == (
        "not_revalidated_in_manifest_clean_clone"
    )
    assert historical["rhs_count"] == 0
    assert historical["factor_solve_called"] is False
    assert historical["physics_solve_performed"] is False

    background, conductor = historical["factor_certificates"]
    assert background == {
        "name": "A_background_II", "input_nnz": 204545,
        "L_nnz": 1570627, "U_nnz": 1615176,
        "fill_ratio": 15.57507150015889, "exported_bytes": 64219892,
        "portable_structural_bytes": 77466936, "native_portable_bytes": 81218256,
        "factor_wall_seconds": 1.3330735,
        "matrix_bindings": EXPECTED_MATRIX_CONTRACT["A_background_II"],
    }
    assert conductor == {
        "name": "A_conductor_II", "input_nnz": 219393,
        "L_nnz": 1702688, "U_nnz": 1716333,
        "fill_ratio": 15.584002224318917, "exported_bytes": 68884252,
        "portable_structural_bytes": 83064168, "native_portable_bytes": 86058168,
        "factor_wall_seconds": 1.4459152,
        "matrix_bindings": EXPECTED_MATRIX_CONTRACT["A_conductor_II"],
    }
    process = historical["process_and_resource_summary"]
    assert process["factor_process_id"] == 63268
    assert process["finalizer_process_id"] == 39580
    assert process["consumer_process_id"] == 60012
    assert process["exit_codes"] == {
        "public_runner": 0, "inner": 0, "factor": 0, "finalizer": 0, "consumer": 0,
    }
    assert process["inner_samples_successful_total"] == [243, 243]
    assert process["outer_samples_successful_total"] == [677, 677]
    assert process["outer_retry_event_count"] == 47
    assert process["outer_retry_events_truncated"] is False
    assert process["broader_same_birth_live_count"] == 0
    assert process["minimum_host_available_physical_bytes"] == 43_885_748_224
    assert process["eight_gib_execution_evidence"] is False


def test_future_execution_matrix_equilibration_and_factor_contracts_are_frozen(
    wrapper: dict[str, object],
) -> None:
    contract = wrapper["payload"]["execution_contract"]
    assert set(contract) == {
        "prospective_stage", "prospective_stage_available",
        "one_process_factor_solve_and_sidecar_lifetime_required",
        "interior_nodes", "boundary_nodes", "rhs_count_per_factor",
        "maximum_rhs_batch", "rhs_batch_column_order", "factor_order",
        "matrix_contract", "equilibration_contract", "factorization_contract",
        "raw_system_solve_contract", "one_factor_resident_at_a_time",
        "same_factor_must_solve_all_512_rhs_in_batches_before_release",
        "factor_rebuild_for_same_matrix_forbidden",
        "retain_two_interior_extensions_until_operator_assembly",
        "persist_L_U_or_permutations_forbidden",
        "rehydrate_L_U_or_permutations_forbidden",
        "release_factor_L_U_permutations_scales_and_matrix_before_next_factor",
        "sidecar_contains_L_U_or_permutations",
        "standalone_synthetic_solve_result_forbidden", "full_outputs",
        "separate_future_authorization_required",
    }
    assert _canonical_sha(contract) == EXECUTION_CONTRACT_SHA256
    assert contract["prospective_stage"] == "primary-h4"
    assert contract["prospective_stage_available"] is False
    assert contract["one_process_factor_solve_and_sidecar_lifetime_required"] is True
    assert contract["interior_nodes"] == 31_489
    assert contract["boundary_nodes"] == 512
    assert contract["rhs_count_per_factor"] == 512
    assert contract["maximum_rhs_batch"] == 4
    assert contract["rhs_batch_column_order"] == (
        "contiguous_ascending_0_through_511_without_skip_or_repeat"
    )
    assert contract["factor_order"] == ["A_background_II", "A_conductor_II"]
    assert contract["matrix_contract"] == EXPECTED_MATRIX_CONTRACT
    assert _canonical_sha(contract["matrix_contract"]) == MATRIX_CONTRACT_SHA256

    equilibration = contract["equilibration_contract"]
    assert equilibration == {
        "row_max_formula": "row_max[i]=max_j(abs(A_raw[i,j]))",
        "row_scale_formula": "Dr=diag(1/row_max)",
        "row_scaled_matrix_formula": "A_row=Dr@A_raw",
        "column_max_formula": "column_max[j]=max_i(abs(A_row[i,j]))",
        "column_scale_formula": "Dc=diag(1/column_max)",
        "equilibrated_matrix_formula": "Aeq=Dr@A_raw@Dc",
        "raw_matrix_scales_and_Aeq_must_be_finite": True,
        "zero_or_nonfinite_row_or_column_max_disposition": "BLOCKED_AV_BS_SOLVE",
        "deterministic_order": "row_max_then_row_scale_then_column_max_then_column_scale",
    }

    factor = contract["factorization_contract"]
    assert factor == {
        "call": (
            "scipy.sparse.linalg.splu(Aeq,permc_spec='COLAMD',"
            "diag_pivot_thresh=1.0,options={'Equil':False})"
        ),
        "permc_spec": "COLAMD", "diag_pivot_thresh": 1.0,
        "superlu_equil": False, "factorizations_exactly_once_per_factor": True,
        "fallback_reordering_pivot_threshold_or_tuning_forbidden": True,
    }
    assert contract["raw_system_solve_contract"] == {
        "equilibrated_rhs_formula": "rhs_eq=Dr@rhs_raw",
        "factor_solve_formula": "z=factor.solve(rhs_eq)",
        "raw_solution_recovery_formula": "x=Dc@z",
        "residual_evaluated_on_original_A_raw_rhs_raw_and_recovered_x": True,
        "solve_fallback_or_iterative_refinement_not_preregistered": True,
    }
    assert contract["one_factor_resident_at_a_time"] is True
    assert contract["same_factor_must_solve_all_512_rhs_in_batches_before_release"] is True
    assert contract["factor_rebuild_for_same_matrix_forbidden"] is True
    assert contract["retain_two_interior_extensions_until_operator_assembly"] is True
    assert contract["persist_L_U_or_permutations_forbidden"] is True
    assert contract["rehydrate_L_U_or_permutations_forbidden"] is True
    assert contract[
        "release_factor_L_U_permutations_scales_and_matrix_before_next_factor"
    ] is True
    assert contract["sidecar_contains_L_U_or_permutations"] is False
    assert contract["standalone_synthetic_solve_result_forbidden"] is True
    assert contract["full_outputs"] == ["Y", "Y_reverse", "M9_interior"]
    assert contract["separate_future_authorization_required"] is True


def test_binary_sidecar_layout_size_path_atomicity_and_hash_binding(
    wrapper: dict[str, object],
) -> None:
    payload = wrapper["payload"]
    sidecar = payload["binary_sidecar_descriptor"]
    assert set(sidecar) == {
        "schema", "contract_only_no_file_created", "header", "dtype",
        "scalar_encoding", "byte_order", "array_order", "compression", "arrays",
        "future_array_generation_contract", "total_binary_payload_bytes",
        "base64_payload_only_bytes", "inline_json_limit_bytes",
        "base64_payload_only_exceeds_inline_limit_bytes",
        "json_key_quote_descriptor_overhead_included",
        "future_descriptor_required_fields", "future_binary_sha256",
        "future_binary_sha256_format", "path_policy", "publication_policy",
        "validation_policy", "future_evidence_binding_policy",
        "sidecar_creation_performed",
    }
    assert sidecar["schema"] == "AV-BS1-h4-p1-binary-sidecar-descriptor-v1"
    assert sidecar["contract_only_no_file_created"] is True
    assert sidecar["header"] == "none_fixed_layout_contiguous"
    assert sidecar["dtype"] == "<c16"
    assert sidecar["scalar_encoding"] == "IEEE754_binary64_real_then_imag"
    assert sidecar["byte_order"] == "little_endian"
    assert sidecar["array_order"] == "C"
    assert sidecar["compression"] == "none"
    assert sidecar["arrays"] == [
        {
            "name": "Y", "offset_bytes": 0, "nbytes": 4_194_304,
            "shape": [512, 512], "element_count": 262_144,
            "axes": ["frozen_boundary_node_order", "frozen_boundary_node_order"],
        },
        {
            "name": "Y_reverse", "offset_bytes": 4_194_304, "nbytes": 4_194_304,
            "shape": [512, 512], "element_count": 262_144,
            "axes": ["frozen_boundary_node_order", "frozen_boundary_node_order"],
        },
        {
            "name": "M9_interior", "offset_bytes": 8_388_608, "nbytes": 4_534_416,
            "shape": [31_489, 9], "element_count": 283_401,
            "axes": ["frozen_interior_node_order", "signed_mode_order_minus4_through_plus4"],
            "signed_mode_columns": list(range(-4, 5)),
        },
    ]
    assert sum(item["nbytes"] for item in sidecar["arrays"]) == 12_923_024
    assert sidecar["total_binary_payload_bytes"] == 12_923_024
    assert sidecar["base64_payload_only_bytes"] == 17_230_700
    assert sidecar["inline_json_limit_bytes"] == 16_777_216
    assert sidecar["base64_payload_only_exceeds_inline_limit_bytes"] == 453_484
    assert sidecar["json_key_quote_descriptor_overhead_included"] is False
    assert sidecar["future_descriptor_required_fields"] == [
        "schema", "relative_path", "dtype", "byte_order", "array_order",
        "arrays", "total_binary_payload_bytes", "raw_sha256",
    ]
    assert sidecar["future_binary_sha256"] is None
    assert sidecar["future_binary_sha256_format"] == "lowercase_hex_64"
    assert sidecar["sidecar_creation_performed"] is False
    assert _canonical_sha(sidecar) == SIDECAR_DESCRIPTOR_SHA256
    assert payload["binary_sidecar_descriptor_sha256"] == SIDECAR_DESCRIPTOR_SHA256

    _assert_exact(sidecar["future_array_generation_contract"], {
        "H_background_formula": "vertical_stack([X_background,I_boundary])",
        "H_conductor_formula": "vertical_stack([X_conductor,I_boundary])",
        "Y_formula": "sigma*H_background.T@M@H_conductor",
        "Y_reverse_formula": "sigma*H_conductor.T@M@H_background",
        "transpose_is_bilinear_not_conjugate_transpose": True,
        "V_M9_formula": "V_M9[n,j]=exp(1j*signed_modes[j]*theta_boundary[n])",
        "signed_modes": list(range(-4, 5)),
        "M9_interior_formula": "X_conductor@V_M9",
        "boundary_identity_is_implicit_and_not_serialized": True,
        "all_matrix_mesh_boundary_and_mode_orders_are_frozen": True,
    })

    path_policy = sidecar["path_policy"]
    _assert_exact(path_policy, {
        "fixed_basename": "av_bs1_h4_p1_arrays.bin",
        "same_quarantine_directory_as_descriptor": True,
        "absolute_path_forbidden": True,
        "parent_traversal_forbidden": True,
        "reparse_point_or_symlink_forbidden": True,
        "file_identity_recheck_required": True,
    })
    publication = sidecar["publication_policy"]
    _assert_exact(publication, {
        "same_directory_create_new_temporary": True,
        "temporary_and_final_must_be_on_same_volume": True,
        "flush_file_before_atomic_publish": True,
        "atomic_publish_must_be_no_replace": True,
        "check_then_replace_or_overwrite_forbidden": True,
        "existing_final_target_rejected_without_mutation": True,
        "post_publish_size_hash_and_file_identity_recheck": True,
        "temporary_cleanup_required_on_failure": True,
    })
    validation = sidecar["validation_policy"]
    _assert_exact(validation, {
        "exact_size_offsets_shapes_dtype_and_order_required": True,
        "stream_all_real_and_imaginary_values_finite": True,
        "normalize_each_negative_zero_component_to_positive_zero_before_serialization_and_write": True,
        "raw_sha256_covers_exact_published_normalized_12923024_bytes": True,
        "trailing_or_missing_bytes_rejected": True,
        "tamper_or_path_identity_change_rejected": True,
    })
    evidence = sidecar["future_evidence_binding_policy"]
    _assert_exact(evidence, {
        "actual_descriptor_canonical_sha256_required": True,
        "actual_binary_raw_sha256_required": True,
        "bind_both_hashes_in_quarantine_result_consumed_tombstone_and_terminal_seal": True,
        "descriptor_and_binary_binding_mismatch_rejected": True,
    })


def test_sidecar_descriptor_tamper_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    modified = deepcopy(p1._sidecar_descriptor())
    modified["arrays"][1]["offset_bytes"] += 16
    assert _canonical_sha(modified) != _canonical_sha(p1._sidecar_descriptor())
    monkeypatch.setattr(p1, "_validate_lifecycle_and_provenance", lambda: None)
    monkeypatch.setattr(p1, "_sidecar_descriptor", lambda: modified)
    with pytest.raises(p1.AvBsError, match="manifest payload checksum mismatch"):
        p1.validated_manifest_wrapper()


def test_resource_policy_pins_six_terms_margin_stops_and_monitor(
    wrapper: dict[str, object],
) -> None:
    payload = wrapper["payload"]
    resource = payload["resource_policy"]
    assert _canonical_sha(resource) == RESOURCE_POLICY_SHA256
    assert payload["resource_policy_sha256"] == RESOURCE_POLICY_SHA256
    assert resource["schema"] == "AV-BS1-h4-p1-resource-policy-v1"
    assert resource["interior_nodes"] == 31_489
    assert resource["boundary_nodes"] == 512
    assert resource["rhs_count_per_factor"] == 512
    assert resource["sparse_copy_allowance_multiplier"] == 16
    terms = [
        resource["sparse_base_arrays_bytes"],
        resource["sparse_copy_allowance_bytes"],
        resource["one_factor_hard_cap_bytes"],
        resource["two_interior_extensions_bytes"],
        resource["boundary_dense_work_bytes"],
        resource["batch4_rhs_work_bytes"],
    ]
    assert terms == [5_449_772, 87_196_352, 2_147_483_648, 515_915_776, 12_582_912, 8_061_184]
    assert sum(terms) == resource["raw_total_bytes"] == 2_776_689_644
    assert (5 * sum(terms) + 3) // 4 == resource["total_with_25pct_margin_bytes"] == 3_470_862_055
    assert resource["tree_working_set_stop_bytes"] == 4_294_967_296
    assert resource["tree_working_set_stop_predicate"] == (
        "tree_working_set_bytes>4294967296"
    )
    assert resource["tree_working_set_slack_bytes"] == 824_105_241
    assert resource["tree_private_stop_bytes"] == 5_368_709_120
    assert resource["tree_private_stop_predicate"] == (
        "tree_private_bytes>5368709120"
    )
    assert resource["tree_commit_stop_bytes"] == 5_368_709_120
    assert resource["tree_commit_stop_predicate"] == (
        "tree_commit_bytes>5368709120"
    )
    assert resource["wall_stop_seconds"] == 900
    assert resource["wall_stop_predicate"] == "wall_seconds>900"
    assert resource["available_physical_runtime_stop_bytes"] == 1_610_612_736
    assert resource["available_physical_runtime_stop_predicate"] == (
        "available_physical_bytes<1610612736"
    )
    assert resource["system_commit_headroom_runtime_stop_bytes"] == 2_147_483_648
    assert resource["system_commit_headroom_runtime_stop_predicate"] == (
        "system_commit_headroom_bytes<2147483648"
    )
    assert resource["successful_tree_sample_count_minimum"] == 1
    assert resource["resource_pass_requires_minimum_successful_tree_sample_count"] is True
    assert resource["minimum_available_physical_before_spawn_bytes"] == 5_081_474_791
    assert resource["minimum_commit_headroom_before_spawn_bytes"] == 5_618_345_703
    assert resource["maximum_rhs_batch"] == 4
    assert resource["prospective_envelope_arithmetic_pass"] is True
    assert resource["eight_gib_execution_proven"] is False

    monitor = resource["prospective_process_tree_monitor_contract"]
    _assert_exact(monitor, {
        "sample_interval_milliseconds": 100,
        "membership": "outer_observer_plus_inner_runner_plus_all_identity_bound_descendants",
        "identity_tuple": ["process_id", "process_birth_ticks"],
        "parent_only_sample_cannot_satisfy_factor_solve_visibility": True,
        "at_least_one_successful_sample_must_observe_factor_solve_child_identity": True,
        "incomplete_unreadable_query_or_identity_ambiguity_is_failure": True,
        "sample_or_retry_truncation_is_failure": True,
        "resource_stop_is_failure": True,
        "same_birth_descendants_must_all_be_absent_before_terminal_pass": True,
        "pid_reuse_does_not_count_as_original_identity_alive": True,
    })


def test_accuracy_residual_condition_assembly_reverse_reciprocity_and_passivity(
    wrapper: dict[str, object],
) -> None:
    payload = wrapper["payload"]
    gates = payload["accuracy_gates"]
    assert set(gates) == {
        "comparison_input_bindings", "solve_backward_residual",
        "condition_estimate", "assembly_transpose", "operator_scaling",
        "independent_reverse_operator", "raw_reciprocity", "raw_passivity",
        "modal_trace_normalization", "modal_field_residual", "modal_power",
        "fine_analytic_signed_modes", "h2_to_h4_signed_mode_convergence",
        "plus_minus_mode_degeneracy", "global_fail_closed_numeric_policy",
        "raw_result_policy",
    }
    assert _canonical_sha(gates) == ACCURACY_GATES_SHA256
    assert payload["accuracy_gates_sha256"] == ACCURACY_GATES_SHA256

    residual = gates["solve_backward_residual"]
    _assert_exact(residual, {
        "applies_to": "each_of_512_rhs_for_each_original_unscaled_A_background_II_and_A_conductor_II",
        "equilibrated_solve_formula": "Aeq*z=Dr*rhs; x=Dc*z",
        "raw_residual_vector_formula": "r=A_raw*x-rhs",
        "numerator_formula": "maxabs(r)",
        "denominator_formula": "norminf(A_raw)*maxabs(x)+maxabs(rhs)",
        "maximum": 1.0e-10,
        "numerator_denominator_and_result_must_be_finite": True,
        "denominator_must_be_strictly_positive": True,
        "zero_denominator_disposition": "BLOCKED_AV_BS_SOLVE",
    })

    condition = gates["condition_estimate"]
    _assert_exact(condition, {
        "applies_to": ["A_background_II_Aeq", "A_conductor_II_Aeq"],
        "inverse_linear_operator": {
            "matvec_and_matmat": "factor.solve(value,trans='N')",
            "rmatvec_and_rmatmat": "factor.solve(value,trans='H')",
            "same_resident_factor_required": True,
            "dtype": "complex128",
        },
        "per_seed_inverse_estimate_formula": "onenormest(inverse_linear_operator,t=4,itmax=10)",
        "aggregate_formula": "kappa1_u=norm1(Aeq)*max(inverse_estimate_seed_1729,inverse_estimate_seed_2718)*2**-53",
        "binary64_unit_roundoff_u": 2.0**-53,
        "maximum": 1.0e-8,
        "estimator": "onenormest",
        "onenormest_t": 4,
        "onenormest_itmax": 10,
        "frozen_seed_identities": [1729, 2718],
        "seed_application": "save_numpy_rng_state_seed_each_estimate_then_restore_rng_state",
        "minimum_average_or_single_seed_substitution_forbidden": True,
        "matrix_norm_inverse_norm_kappa_and_product_must_be_finite_and_positive": True,
        "rigorous_upper_bound": False,
    })

    assembly = gates["assembly_transpose"]
    _assert_exact(assembly, {
        "matrices": ["K_II", "M_II", "A_background_II_raw", "A_conductor_II_raw"],
        "per_matrix_formulas": {
            "K_II": "normF(K_II-K_II.T)/normF(K_II)",
            "M_II": "normF(M_II-M_II.T)/normF(M_II)",
            "A_background_II_raw": "normF(A_background_II_raw-A_background_II_raw.T)/normF(A_background_II_raw)",
            "A_conductor_II_raw": "normF(A_conductor_II_raw-A_conductor_II_raw.T)/normF(A_conductor_II_raw)",
        },
        "maximum": 1.0e-12,
        "numerator_denominator_and_result_must_be_finite": True,
        "denominator_must_be_strictly_positive": True,
    })
    scaling = gates["operator_scaling"]
    _assert_exact(scaling, {
        "all_Y_and_Y_reverse_entries_must_be_finite": True,
        "operator_floor_S_times_m": "max(1e-18,1e-10*maxabs(Y))",
        "relative_denominator": "max(normF(Y),512*operator_floor_S_times_m)",
        "floor_and_denominator_must_be_finite_and_strictly_positive": True,
    })
    _assert_exact(gates["independent_reverse_operator"], {
        "metric": "normF(Y_reverse-Y.T)/max(normF(Y),512*operator_floor_S_times_m)",
        "maximum": 1.0e-12,
        "numerator_and_result_must_be_finite": True,
    })
    _assert_exact(gates["raw_reciprocity"], {
        "metric": "normF(Y-Y.T)/max(normF(Y),512*operator_floor_S_times_m)",
        "maximum": 1.0e-8,
        "numerator_and_result_must_be_finite": True,
    })
    passivity = gates["raw_passivity"]
    _assert_exact(passivity, {
        "hermitian_gate_matrix_only": "H=(Y+Y.conj().T)/2",
        "minimum_hermitian_eigenvalue": ">=-max(operator_floor_S_times_m,1e-9*norm2(Y))",
        "eigenvalues_norm_threshold_and_comparison_must_be_finite": True,
        "H_must_not_replace_or_repair_raw_Y": True,
    })


def test_accuracy_modal_power_analytic_h2_h4_and_degeneracy_gates(
    wrapper: dict[str, object],
) -> None:
    gates = wrapper["payload"]["accuracy_gates"]
    modes = list(range(-4, 5))
    trace = gates["modal_trace_normalization"]
    _assert_exact(trace, {
        "signed_modes": modes,
        "boundary_vector_formula": "e_m[n]=exp(1j*m*theta_n)",
        "Yhat_formula": "(e_m.conj().T@Y@e_m)/(e_m.conj().T@M_Gamma@e_m)",
        "trace_denominator_must_be_finite_real_and_strictly_positive": True,
        "numerator_Yhat_and_all_intermediates_must_be_finite": True,
    })

    field = gates["modal_field_residual"]
    _assert_exact(field, {
        "signed_modes": modes,
        "raw_residual_formula": "r=A_conductor_II*u+A_conductor_I_Gamma*v",
        "denominator_formula": "norminf(A_conductor_II)*maxabs(u)+maxabs(A_conductor_I_Gamma*v)",
        "relative_formula": "maxabs(r)/denominator",
        "maximum": 1.0e-10,
        "numerator_denominator_result_and_fields_must_be_finite": True,
        "denominator_must_be_strictly_positive": True,
    })
    power = gates["modal_power"]
    _assert_exact(power, {
        "signed_modes": modes,
        "boundary_power_formula_W_per_m": "0.5*Re(v.conj().T@Y@v)",
        "volume_power_formula_W_per_m": "0.5*sigma*Re(u.conj().T@(M_II@u+M_I_Gamma@v)+v.conj().T@(M_Gamma_I@u+M_Gamma_Gamma@v))",
        "relative_mismatch_formula": "abs(P_boundary-P_volume)/max(abs(P_boundary),abs(P_volume),1e-18)",
        "relative_mismatch_maximum": 1.0e-8,
        "volume_power_minimum_W_per_m": -1.0e-18,
        "powers_denominator_mismatch_and_all_intermediates_must_be_finite": True,
        "denominator_must_be_strictly_positive": True,
    })

    analytic = gates["fine_analytic_signed_modes"]
    _assert_exact(analytic, {
        "signed_modes": modes,
        "mode_floor_formula_S": "max(1e-12,1e-10*maxabs(frozen_analytic_targets))",
        "frozen_analytic_mode_floor_S": 5.214985896880959e-08,
        "relative_formula": "abs(Yhat_h4-analytic)/max(abs(analytic),frozen_analytic_mode_floor_S)",
        "relative_maximum_each_mode": 5.0e-3,
        "phase_eligible_formula": "abs(Yhat_h4)>=10*frozen_analytic_mode_floor_S and abs(analytic)>=10*frozen_analytic_mode_floor_S",
        "phase_error_formula_deg": "abs(angle(Yhat_h4/analytic,deg=True))",
        "eligible_phase_maximum_deg": 0.25,
        "eligible_phase_mode_count_minimum": 1,
        "analytic_target_formula": "analytic=Ys_abs_m",
        "ineligible_phase_output": None,
        "ineligible_phase_disposition": "JSON_null_not_zero_not_pass_substitution",
        "floor_targets_errors_ratios_and_phases_must_be_finite": True,
    })

    h2 = gates["comparison_input_bindings"]
    _assert_exact(h2, {
        "h2_result_file_sha256": "b890e4af13d97591b3134788984b3657f6f6b046e3043ce0a6f6792fe1ad7f55",
        "h2_result_payload_sha256": "5704f3feb5bb90b6e38f8ed3ec67fc690339e9b7f0c1722d40a977295e82593e",
        "h2_numerical_payload_sha256": "bd2f6542a93e3a7adc62f4435540752651ddf1cff84226319b4aa5e8dbdc0be5",
        "h2_result_status": "passed_AV_BS_h2_stage_only_pending_h4_preregistration",
        "h2_ignored_artifact_live_revalidated_by_manifest": False,
        "h2_commitment_note": "not_revalidated_in_manifest_clean_clone",
        "future_execution_requires_exact_h2_evidence_restore_or_committed_compact_certificate": True,
        "analytic_targets": "frozen_Ys_abs_m_for_signed_modes_minus4_through_plus4",
    })
    convergence = gates["h2_to_h4_signed_mode_convergence"]
    _assert_exact(convergence, {
        "signed_modes": modes,
        "equal_weight_per_signed_mode": True,
        "frozen_h2_comparison_floor_S": 5.214985896880959e-08,
        "relative_formula": "abs(Yhat_h4-Yhat_h2)/max(abs(Yhat_h4),frozen_h2_comparison_floor_S)",
        "rms_formula": "sqrt(sum(relative_error_m**2 for m in signed_modes)/9)",
        "maximum_formula": "max(relative_error_m for m in signed_modes)",
        "rms_relative_maximum": 5.0e-3,
        "max_relative_maximum": 1.0e-2,
        "phase_eligible_formula": "abs(Yhat_h2)>=10*frozen_h2_comparison_floor_S and abs(Yhat_h4)>=10*frozen_h2_comparison_floor_S",
        "phase_error_formula_deg": "abs(angle(Yhat_h4/Yhat_h2,deg=True))",
        "eligible_phase_maximum_deg": 0.25,
        "eligible_phase_mode_count_minimum": 1,
        "ineligible_phase_output": None,
        "ineligible_phase_disposition": "JSON_null_not_zero_not_pass_substitution",
        "floor_values_errors_rms_max_ratios_and_phases_must_be_finite": True,
    })
    degeneracy = gates["plus_minus_mode_degeneracy"]
    _assert_exact(degeneracy, {
        "mode_pairs": [[1, -1], [2, -2], [3, -3], [4, -4]],
        "plus_minus_mode_floor_S": 5.214985896880959e-08,
        "relative_formula": "abs(Yhat_m-Yhat_minus_m)/max(abs(Yhat_m),abs(Yhat_minus_m),plus_minus_mode_floor_S)",
        "maximum": 1.0e-8,
        "numerator_denominator_result_and_modes_must_be_finite": True,
        "denominator_must_be_strictly_positive": True,
    })
    _assert_exact(gates["global_fail_closed_numeric_policy"], {
        "all_required_values_and_intermediates_must_be_finite": True,
        "zero_or_nonfinite_required_denominator_is_failure": True,
        "missing_signed_mode_or_missing_eligible_phase_family_is_failure": True,
    })
    _assert_exact(gates["raw_result_policy"], {
        "symmetrization_forbidden": True,
        "plus_minus_averaging_forbidden": True,
        "clipping_forbidden": True,
        "gate_on_raw_values_before_presentation": True,
        "post_hoc_threshold_floor_seed_mode_or_eligibility_tuning_forbidden": True,
    })


def test_every_execution_authorization_physics_and_next_flag_is_false(
    wrapper: dict[str, object],
) -> None:
    payload = wrapper["payload"]
    false_top_level = {
        "execution_authorized", "mandatory_stage_pass", "terminal_evidence_complete",
        "authoritative_terminal_evidence", "authoritative_stage_pass", "rhs_generated",
        "factorization_attempted", "factorization_performed", "linear_solve_called",
        "factor_solve_called", "factor_solve_performed", "physics_solve_performed",
        "accuracy_gates_evaluated", "resource_gate_evaluated",
        "sidecar_creation_performed", "sidecar_written",
        "power_si_comparison_performed", "power_si_accuracy_claim", "eight_gib_fit_claim",
        "next_stage_authorized",
    }
    assert {key: payload[key] for key in false_top_level} == {
        key: False for key in false_top_level
    }
    _assert_exact(payload["forbidden_operations"], {
        "token_created": False, "claim_created": False, "rhs_generated": False,
        "factorization_performed": False, "factor_solve_called": False,
        "linear_solve_called": False, "extensions_generated": False,
        "boundary_operator_generated": False, "Y_generated": False,
        "Y_reverse_generated": False, "modal_response_generated": False,
        "power_generated": False, "sidecar_created": False,
        "public_result_written": False, "physics_solve_performed": False,
        "finalizer_called": False, "consumer_called": False,
    })
    assert payload["available_stages"] == ["manifest"]
    assert payload["available_solve_stages"] == []
    assert payload["token_gated_stages"] == []
    assert payload["unavailable_stages"] == ["primary-h4", "withheld", "EQ0"]


@pytest.mark.parametrize(
    "raw",
    [
        b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}',
        b'{"x":1e9999}', b'[]', b'1', b'{"x":"\xff"}',
    ],
)
def test_strict_json_rejects_duplicate_nonfinite_nonobject_and_invalid_utf8(
    raw: bytes,
) -> None:
    with pytest.raises(p1.AvBsError, match="is not strict JSON|root is not an object"):
        p1._strict_json_object(raw, "tampered")
    assert p1._strict_json_object(b'{"nested":{"finite":1.25}}', "valid") == {
        "nested": {"finite": 1.25}
    }


@pytest.mark.parametrize(
    ("input_code", "expected_code"),
    [
        ("BLOCKED_AV_BS_POWER", "BLOCKED_AV_BS_POWER"),
        ("NOT_ALLOWLISTED", "BLOCKED_AV_BS_RESULT_SCHEMA"),
    ],
)
def test_failure_wrapper_is_canonical_allowlisted_and_non_authorizing(
    input_code: str, expected_code: str,
) -> None:
    wrapper = p1._failure_wrapper(p1.AvBsError(input_code, "tamper detected"), "manifest")
    payload = wrapper["payload"]
    assert payload["schema"] == "AV-BS1-h4-p1-manifest-failure-v1"
    assert payload["program"] == "SPD Decap PI Evaluator v0.22.0"
    assert payload["stage"] == "manifest"
    assert payload["status"] == expected_code
    assert payload["authorization_state"] == "not_authorized"
    assert payload["token_state"] == "unvalidated_or_unexpected"
    assert payload["failure_codes"] == [expected_code]
    assert payload["detail"] == "tamper detected"
    for key in (
        "execution_authorized", "token_absence_validated", "mandatory_stage_pass",
        "rhs_generated", "factorization_performed", "factor_solve_called",
        "physics_solve_performed", "sidecar_creation_performed",
        "next_stage_authorized",
    ):
        assert payload[key] is False
    assert wrapper["payload_sha256"] == _canonical_sha(payload)
    assert wrapper["wrapper_sha256"] == _canonical_sha(
        {"payload": payload, "payload_sha256": wrapper["payload_sha256"]}
    )


def test_in_process_main_failure_is_exit_2_and_canonical_non_authorizing_stdout(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    _assert_source_still_frozen()

    def blocked_manifest() -> object:
        raise p1.AvBsError("BLOCKED_AV_BS_FACTOR", "injected read-only failure")

    monkeypatch.setattr(p1, "validated_manifest_wrapper", blocked_manifest)
    assert p1.main(["--stage", "manifest"]) == 2
    captured = capsys.readouterr()
    assert captured.err == ""
    line = captured.out.rstrip("\r\n")
    failure = json.loads(line)
    assert line.encode("utf-8") == _canonical_bytes(failure)
    payload = failure["payload"]
    assert payload["status"] == "BLOCKED_AV_BS_FACTOR"
    assert payload["authorization_state"] == "not_authorized"
    assert payload["token_state"] == "unvalidated_or_unexpected"
    assert payload["execution_authorized"] is False
    assert payload["factorization_performed"] is False
    assert payload["factor_solve_called"] is False
    assert payload["physics_solve_performed"] is False
    assert payload["sidecar_creation_performed"] is False
    assert payload["next_stage_authorized"] is False
    assert failure["payload_sha256"] == _canonical_sha(payload)
    assert failure["wrapper_sha256"] == _canonical_sha(
        {"payload": payload, "payload_sha256": failure["payload_sha256"]}
    )
