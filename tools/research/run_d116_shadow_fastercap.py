#!/usr/bin/env python
"""One-shot, evidence-sealed D116-SHADOW FasterCap runner."""
from __future__ import annotations
import argparse
from hashlib import sha256
import json, math, os, re, shutil, signal, subprocess, tempfile, threading, time
import ctypes
from pathlib import Path
from typing import Any, Callable
import numpy as np

PROGRAM = "SPD Decap PI Evaluator v0.23.1 D116-SHADOW"
HEAD = "e2f219e71d8c8a397009f72242cce10d78cfc7ab"
INPUT_ROOT = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-source-l29-l30-fastercap-input-02")
OUTPUT_ROOT = Path(r"D:\SPD-Decap-PI-Evaluator-W7\e2f219e71d8c8a397009f72242cce10d78cfc7ab\260903-d116-shadow-fastercap-raw-c-matrix-01")
RECEIPT_NAME = "d116_shadow_gap_coupon_receipt.json"
RECEIPT_SHA = "5a43383a24083e8acffe3b78066e65e475bdadba264f176422ac47066a22c06c"
SOLVER = Path(r"D:\SPD-Decap-PI-Evaluator-W7\831000e8d5e874d4ec155f0a6dda5cc93b68125c\260729-d107t-fastercap-filtered-extraction-01\payload\app\FasterCap\FasterCap.exe")
SOLVER_SHA = "02806e5b185d3cf86f9ed95e7e732fdec1bae0fdc9344f064d90550f5861c9b9"
SOLVER_SIZE = 7211520
GOMP_SHA = "68d15bf2180388c396e01d3ef3a3e5987e04d544e1a2a39e04f51e75f76984c9"
FILES = ("A_GND_259.qui", "A_PWR_264.qui", "F_DDRL_262.qui", "d116_shadow_gap_coupon.lst", RECEIPT_NAME)
FILE_META = {
    "A_GND_259.qui": (32652, "3bc8230edf765a4c5a0c0f8a8b484e249e5d652e2d3c0f9cf3c3ae0f9c78d7b2"),
    "A_PWR_264.qui": (10449, "22f2119e2f1377c8554a88af89636002f889aa9e4f5f16adf23a4df202677a87"),
    "F_DDRL_262.qui": (1252, "ab4c920091fc7e5f3d83ac2833ee39c490865bbc0795898cb360643cc871c90e"),
    "d116_shadow_gap_coupon.lst": (150, "b6ea0b3d322532f124d9c3da23fca153ed945a1c19cb79f2639af2fff24c39d6"),
    RECEIPT_NAME: (5480, RECEIPT_SHA),
}
LABELS = ("g1_A_GND_259", "g2_A_PWR_264", "g3_F_DDRL_262")
ARGV_TAIL = ("-b", "d116_shadow_gap_coupon.lst", "-a0.01", "-mc3", "-d1", "-f5", "-pj", "-e", "-i")
AUTHORITATIVE_REPO = Path(r"C:\Users\User\Documents\ChatGPT\SPD Decap PI Evaluator")
MANIFEST_REL = Path("tools/research/d116_shadow_fastercap_approval_manifest.json")
EXPECTED_UNTRACKED = {
    "accuracy_parse.py", "docs/evaluation-research/D115C_REVIEW_AND_D116_DECISION_GATE.md",
    "tests/test_audit_source_l29_l30_port_window.py", "tests/test_build_source_l29_l30_fastercap_input.py",
    "tests/test_run_d116_shadow_fastercap.py", "tools/research/audit_source_l29_l30_port_window.py",
    "tools/research/build_source_l29_l30_fastercap_input.py", str(MANIFEST_REL).replace("\\", "/"),
    "tools/research/run_d116_shadow_fastercap.py",
}

def digest(path: Path) -> str:
    h = sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""): h.update(chunk)
    return h.hexdigest()

def _git(repo: Path) -> None:
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()
    branch = subprocess.check_output(["git", "branch", "--show-current"], cwd=repo, text=True).strip()
    status = subprocess.check_output(["git", "status", "--porcelain"], cwd=repo, text=True).splitlines()
    tracked = [x for x in status if x and not x.startswith("?? ")]
    untracked = {x[3:].replace("\\", "/") for x in status if x.startswith("?? ")}
    if (head, branch, tracked) != (HEAD, "main", []): raise ValueError("repository identity or tracked cleanliness failed")
    if untracked != EXPECTED_UNTRACKED: raise ValueError("exact untracked allowlist failed")

def _approval_manifest(repo: Path, output: Path, approval_sha256: str | None = None) -> dict[str, Any]:
    runner_path = Path(__file__).resolve()
    authoritative_runner = AUTHORITATIVE_REPO / "tools/research/run_d116_shadow_fastercap.py"
    if runner_path != authoritative_runner: raise ValueError("runner path is not authoritative")
    if output.resolve() != OUTPUT_ROOT.resolve(): raise ValueError("fixed output path failed")
    manifest_path = AUTHORITATIVE_REPO / MANIFEST_REL
    if manifest_path != repo / MANIFEST_REL or not manifest_path.is_file(): raise ValueError("approval manifest path failed")
    manifest_bytes = manifest_path.read_bytes()
    manifest = json.loads(manifest_bytes.decode("utf-8"))
    observed_sha256 = sha256(manifest_bytes).hexdigest()
    if approval_sha256 is None or not re.fullmatch(r"[0-9a-f]{64}", approval_sha256) or approval_sha256 != observed_sha256: raise ValueError("approval manifest SHA anchor failed")
    if manifest.get("schema_version") != "d116-shadow-approval-v1" or manifest.get("purpose") != "narrow D116-SHADOW raw C-matrix approval": raise ValueError("approval manifest purpose failed")
    if manifest.get("head") != HEAD or manifest.get("branch") != "main" or manifest.get("output_root") != str(OUTPUT_ROOT) or manifest.get("argv_tail") != list(ARGV_TAIL[2:]): raise ValueError("approval manifest constants failed")
    for key, expected_rel in (("runner", "tools/research/run_d116_shadow_fastercap.py"), ("tests", "tests/test_run_d116_shadow_fastercap.py")):
        item = manifest.get(key, {})
        path = repo / item.get("path", "")
        if item.get("path") != expected_rel or not path.is_file() or item.get("size_bytes") != path.stat().st_size or item.get("sha256") != digest(path):
            raise ValueError(f"approval manifest {key} identity failed")
    manifest["_approved_size_bytes"] = len(manifest_bytes)
    manifest["_approved_sha256"] = observed_sha256
    manifest["_requested_sha256"] = approval_sha256
    return manifest

def validate_preflight(input_root: Path, solver: Path, output: Path, repo: Path, approval_sha256: str | None = None) -> dict[str, Any]:
    if output.resolve() != OUTPUT_ROOT.resolve(): raise ValueError("fixed output path failed")
    if output.exists(): raise FileExistsError("output already exists")
    if repo.resolve() != AUTHORITATIVE_REPO.resolve(): raise ValueError("authoritative repository path failed")
    _git(repo)
    manifest = _approval_manifest(repo, output, approval_sha256)
    if shutil.disk_usage(output.parent).free < 16 * 1024**3: raise OSError("free disk below 16 GiB")
    if set(input_root.iterdir()) != {input_root / n for n in FILES}: raise ValueError("input five-file allowlist failed")
    for n, (size, expected) in FILE_META.items():
        p = input_root / n
        if p.stat().st_size != size or digest(p) != expected: raise ValueError(f"input identity failed: {n}")
    receipt = json.loads((input_root / RECEIPT_NAME).read_text(encoding="utf-8"))
    scope, material = receipt.get("scope", {}), receipt.get("material", {})
    if receipt.get("status") != "PASS_D116_SHADOW_GAP_COUPON" or receipt.get("conductor_order") != ["A_GND_259", "A_PWR_264", "F_DDRL_262"] or receipt.get("expected_solver_labels") != list(LABELS): raise ValueError("input scope/order failed")
    required_scope = {"full_domain": False, "production_oracle": False, "powersi_comparable_zii": False, "powersi_zii_readiness": False, "solver_executed": False, "powersi_executed": False, "source_local_shadow_only": True, "artificial_w0_boundary_side_closures": True}
    if any(scope.get(k) is not v for k, v in required_scope.items()) or scope.get("included_layers") != ["L29", "L30"] or scope.get("omitted_layers") != ["L28", "L31"] or scope.get("included_ordinals") != [259, 262, 264]: raise ValueError("shadow scope failed")
    if material.get("description") != "homogeneous 1 MHz material snapshot only" or material.get("frequency_hz") != 1_000_000.0 or material.get("epsilon_r") != 3.4 or material.get("loss_tangent") != .0041 or material.get("loss_tangent_modeled") is not False or material.get("dielectric_loss_or_conductance_claim") is not False: raise ValueError("material scope failed")
    conductors = receipt.get("conductors", ())
    if len(conductors) != 3 or [int(c.get("ordinal", -1)) for c in conductors] != [259, 264, 262]: raise ValueError("conductor cardinality/order failed")
    expected_rows = {259: ("A_GND_259", "A_GND_259.qui", "8eb2e2b2a48eb74f7b9e59eab6697bcd532beeabc23d7d077bf8807e40be828f", 1089), 264: ("A_PWR_264", "A_PWR_264.qui", "f3a2abe59324887bec79aaab38b670f0943cb5cf11936548343a8ef6940cad04", 317), 262: ("F_DDRL_262", "F_DDRL_262.qui", "42ba7213b0ebae48073197b095f4ac7fb9ff3c9552fecb4f1f7c9c32dcc106ea", 77)}
    for c in conductors:
        ordinal = int(c.get("ordinal", -1)); exp = expected_rows.get(ordinal)
        if not exp or (c.get("name"), c.get("file"), c.get("intersection", {}).get("wkb_sha256"), c.get("intersection", {}).get("wkb_size_bytes")) != exp: raise ValueError("conductor geometry identity failed")
    if sum(int(c.get("panel_count", 0)) for c in conductors) != 250 or sum(int(c.get("triangle_count", 0)) for c in conductors) + 2 * sum(int(c.get("quad_count", 0)) for c in conductors) != 336: raise ValueError("input panel totals failed")
    solver_dll = solver.with_name("libgomp_64-1.dll")
    if solver.stat().st_size != SOLVER_SIZE or digest(solver) != SOLVER_SHA or not solver_dll.exists() or digest(solver_dll) != GOMP_SHA: raise ValueError("solver identity failed")
    receipt["approval_manifest"] = manifest
    return receipt

def _copy_evidence(output: Path, repo: Path, manifest: dict[str, Any]) -> list[dict[str, Any]]:
    evidence_root = output / "evidence"
    records = []
    for key in ("runner", "tests"):
        item = manifest[key]; source = repo / item["path"]; relative = item["path"].replace("\\", "/")
        target = evidence_root / relative; target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
        size, sha = target.stat().st_size, digest(target)
        if size != item["size_bytes"] or sha != item["sha256"] or size != source.stat().st_size or sha != digest(source): raise ValueError(f"evidence identity failed: {relative}")
        records.append({"source": relative, "copy": (Path("evidence") / relative).as_posix(), "size_bytes": size, "sha256": sha})
    source = repo / MANIFEST_REL; target = evidence_root / MANIFEST_REL; target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(source, target)
    size, sha = target.stat().st_size, digest(target)
    if size != manifest.get("_approved_size_bytes") or sha != manifest.get("_approved_sha256"): raise ValueError("evidence identity failed: manifest")
    records.append({"source": MANIFEST_REL.as_posix(), "copy": (Path("evidence") / MANIFEST_REL).as_posix(), "size_bytes": size, "sha256": sha})
    return records

def _diag(lines: list[str]) -> bool:
    clean = [x.strip() for x in lines if x.strip()]
    pair = ["Error : Cannot find FastFieldSolvers settings in the Registry", "Please try installing the software again"]
    indices = [i for i, x in enumerate(clean) if x == pair[0]]
    if any(x == pair[1] and (i == 0 or clean[i - 1] != pair[0]) for i, x in enumerate(clean)): raise ValueError("unexpected solver diagnostic")
    if indices and (len(indices) != 1 or indices[0] + 1 >= len(clean) or clean[indices[0]:indices[0] + 2] != pair): raise ValueError("unexpected solver diagnostic")
    for x in clean:
        if re.match(r"^(Error|Warning|Fatal|Failed)\b", x, re.I) and x not in pair: raise ValueError("unexpected solver diagnostic")
        if re.search(r"regulariz|remov|overlap|degeneracy|non[- ]dominance|\b(?:NaN|Inf(?:inity)?)\b", x, re.I): raise ValueError("forbidden solver diagnostic")
    return bool(indices)

def _parse_stdout(stdout: str, stderr: str = "") -> tuple[np.ndarray, dict[str, Any]]:
    lines = stdout.splitlines()
    required = ("FasterCap version 6.0.7", "3D Solver Engine invoked", "Output capacitance matrix to file (-e)", "Solution scheme (-g): Collocation", "GMRES tolerance (-t): 0.005", "Out-of-core free memory to link memory condition (-f): 5", "Potential interaction coefficient to mesh refinement ratio (-d): 1", "Mesh curvature (-mc): 3", "Precond Type(s) (-p): Jacobi", "Auto calculation with max error: 0.01", "Remark: Auto option overrides all other Manual settings")
    if any(x not in stdout for x in required): raise ValueError("frozen solver option echo missing")
    observed_preconds = [line.strip() for text in (stdout, stderr) for line in text.splitlines() if "Precond Type(s)" in line]
    preconds = [line for line in observed_preconds if line == "Precond Type(s) (-p): Jacobi"]
    if not preconds or len(preconds) != len(observed_preconds): raise ValueError("preconditioner evidence failed")
    for pattern, expected in ((r"GMRES tolerance \(-t\):\s*([0-9.eE+-]+)", .005), (r"Out-of-core free memory to link memory condition \(-f\):\s*([0-9.eE+-]+)", 5), (r"Potential interaction coefficient to mesh refinement ratio \(-d\):\s*([0-9.eE+-]+)", 1), (r"Mesh curvature \(-mc\):\s*([0-9.eE+-]+)", 3), (r"Auto calculation with max error:\s*([0-9.eE+-]+)", .01)):
        m = re.search(pattern, stdout)
        if not m or float(m.group(1)) != expected: raise ValueError("frozen numeric option mismatch")
    registry_stdout = _diag(lines); registry_stderr = _diag(stderr.splitlines()); registry = registry_stdout or registry_stderr
    if registry_stdout and registry_stderr: raise ValueError("duplicate registry diagnostic")
    blocks = []
    for i, line in enumerate(lines):
        if not re.search(r"Dimension\s+3\s*x\s*3", line): continue
        rows = []
        for j, label in enumerate(LABELS):
            if i + j + 1 >= len(lines): break
            parts = lines[i + j + 1].split()
            if not parts or parts[0] != label or len(parts[1:]) != 3: break
            try: rows.append([float(v) for v in parts[1:]])
            except ValueError: break
        if len(rows) != 3: raise ValueError("solver labels/order failed")
        blocks.append(np.asarray(rows, dtype=float))
    if not blocks or not np.isfinite(blocks[-1]).all(): raise ValueError("final stdout matrix missing/nonfinite")
    weighted = re.findall(r"Weighted Frobenius norm of the difference between capacitance \(auto option\):\s*([-+0-9.eE]+)", stdout)
    refined = re.findall(r"Number of panels after refinement:\s*(\d+)", stdout)
    if not weighted or not math.isfinite(float(weighted[-1])) or float(weighted[-1]) > .01: raise ValueError("convergence evidence failed")
    if not re.search(r"Number of input panels:\s*250 of which 250 conductors and 0 dielectric", stdout) or not re.search(r"Number of input panels to solver engine:\s*336", stdout) or not refined or int(refined[-1]) < 336: raise ValueError("panel statistics failed")
    return blocks[-1], {"weighted_frobenius": float(weighted[-1]), "refined_panels": int(refined[-1]), "input_panels": 250, "conductor_panels": 250, "dielectric_panels": 0, "solver_engine_panels": 336, "registry_warning_whitelisted": registry}

def _parse_csv(snapshot: bytes | str) -> np.ndarray:
    text = snapshot.decode("utf-8") if isinstance(snapshot, bytes) else snapshot
    rows = [[float(v.strip()) for v in line.split(",")] for line in text.splitlines() if line.strip()]
    a = np.asarray(rows, dtype=float)
    if a.shape != (3, 3) or not np.isfinite(a).all(): raise ValueError("CSV must be exactly 3x3 finite numeric rows")
    return a

def _maxwell(matrix: np.ndarray) -> dict[str, Any]:
    scale = float(np.max(np.diag(matrix))); tau = 1e-6 * scale; off = matrix.copy(); np.fill_diagonal(off, 0)
    eigmin = float(np.min(np.linalg.eigvalsh((matrix + matrix.T) / 2))); rec = float(np.linalg.norm(matrix - matrix.T) / np.linalg.norm(matrix))
    if scale <= 0 or np.any(np.diag(matrix) <= tau) or np.any(off > tau) or np.any(np.sum(matrix, axis=1) < -tau) or eigmin <= tau or rec > .01: raise ValueError("raw Maxwell gates failed")
    return {"scale": scale, "tau": tau, "eigmin_sym": eigmin, "reciprocity_norm": rec, "row_sums": np.sum(matrix, axis=1).tolist()}

def _write(path: Path, payload: bytes) -> None:
    with path.open("wb") as f: f.write(payload); f.flush(); os.fsync(f.fileno())

def _seal_exclusive(path: Path, payload: bytes) -> None:
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temp = Path(temp_name)
    try:
        with os.fdopen(fd, "wb") as f:
            fd = -1; f.write(payload); f.flush(); os.fsync(f.fileno())
        os.link(temp, path)  # same-directory atomic publish; fails if final exists
    finally:
        if fd != -1:
            os.close(fd)
        try: temp.unlink()
        except FileNotFoundError: pass

def _revalidate_source(input_root: Path) -> None:
    if set(input_root.iterdir()) != {input_root / n for n in FILES}: raise ValueError("authoritative input allowlist changed")
    for n, (size, expected) in FILE_META.items():
        p = input_root / n
        if p.stat().st_size != size or digest(p) != expected: raise ValueError(f"authoritative input changed: {n}")

def _revalidate_run_input(run_input: Path) -> None:
    allowed = set(FILES) | {"stdout.txt", "stderr.txt", "d116_shadow_gap_coupon.csv"}
    entries = list(run_input.iterdir()); actual = {p.name for p in entries}
    if any(p.is_dir() for p in entries) or not actual.issubset(allowed) or not set(FILES).issubset(actual):
        raise ValueError("copied run-input allowlist changed")

def _artifact(path: Path, filename: str) -> dict[str, Any]:
    if not path.is_file(): raise OSError(f"artifact is not a file: {filename}")
    return {"filename": filename, "size_bytes": path.stat().st_size, "sha256": digest(path)}

def _artifact_snapshot(payload: bytes, filename: str) -> dict[str, Any]:
    return {"filename": filename, "size_bytes": len(payload), "sha256": sha256(payload).hexdigest()}

def _install_sigint_deferral() -> tuple[Any, list[bool]]:
    if threading.current_thread() is not threading.main_thread(): return None, []
    pending: list[bool] = []
    previous = signal.getsignal(signal.SIGINT)
    signal.signal(signal.SIGINT, lambda _signum, _frame: pending.append(True))
    return previous, pending

def _restore_sigint_deferral(state: tuple[Any, list[bool]], replay: bool = False) -> None:
    previous, pending = state
    if previous is None: return
    signal.signal(signal.SIGINT, previous)
    if replay and pending: signal.raise_signal(signal.SIGINT)

def _create_job() -> Any:
    if os.name != "nt": return None
    kernel32 = ctypes.windll.kernel32
    kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    kernel32.CreateJobObjectW.restype = ctypes.c_void_p
    kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong]
    kernel32.SetInformationJobObject.restype = ctypes.c_int
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int
    h = kernel32.CreateJobObjectW(None, None)
    if not h: raise RuntimeError("CreateJobObject failed")
    class Limits(ctypes.Structure):
        _fields_=[("PerProcessUserTimeLimit",ctypes.c_longlong),("PerJobUserTimeLimit",ctypes.c_longlong),("LimitFlags",ctypes.c_ulong),("MinimumWorkingSetSize",ctypes.c_size_t),("MaximumWorkingSetSize",ctypes.c_size_t),("ActiveProcessLimit",ctypes.c_ulong),("Affinity",ctypes.c_void_p),("PriorityClass",ctypes.c_ulong),("SchedulingClass",ctypes.c_ulong)]
    class Io(ctypes.Structure): _fields_=[("ReadOps",ctypes.c_ulonglong),("WriteOps",ctypes.c_ulonglong),("OtherOps",ctypes.c_ulonglong),("ReadBytes",ctypes.c_ulonglong),("WriteBytes",ctypes.c_ulonglong),("OtherBytes",ctypes.c_ulonglong)]
    class Ext(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", Limits), ("IoInfo", Io),
                    ("ProcessMemoryLimit", ctypes.c_size_t), ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]
    x = Ext()
    x.BasicLimitInformation.LimitFlags = 0x8 | 0x2000  # ACTIVE_PROCESS_LIMIT | KILL_ON_JOB_CLOSE
    x.BasicLimitInformation.ActiveProcessLimit = 1
    if x.BasicLimitInformation.LimitFlags != 0x2008 or x.BasicLimitInformation.ActiveProcessLimit != 1:
        kernel32.CloseHandle(h)
        raise RuntimeError("invalid job limits")
    if not kernel32.SetInformationJobObject(h, 9, ctypes.byref(x), ctypes.sizeof(x)):
        kernel32.CloseHandle(h)
        raise RuntimeError("SetInformationJobObject failed")
    return h

def _assign_job(job: Any, process: Any) -> None:
    if job is not None:
        handle = getattr(process, "_handle", None)
        if not handle: raise RuntimeError("retained process handle unavailable")
        # Popen owns this handle; the job receives only a borrowed reference.
        kernel32 = ctypes.windll.kernel32
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        if not kernel32.AssignProcessToJobObject(job, handle): raise RuntimeError("AssignProcessToJobObject failed")

def _job_active_count(job: Any) -> int:
    if job is None: return 0
    kernel32 = ctypes.windll.kernel32
    kernel32.QueryInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_ulong, ctypes.c_void_p]
    kernel32.QueryInformationJobObject.restype = ctypes.c_int
    class Basic(ctypes.Structure):
        _fields_ = [("TotalUserTime", ctypes.c_longlong), ("TotalKernelTime", ctypes.c_longlong),
                    ("ThisPeriodTotalUserTime", ctypes.c_longlong), ("ThisPeriodTotalKernelTime", ctypes.c_longlong),
                    ("TotalPageFaultCount", ctypes.c_ulong), ("TotalProcesses", ctypes.c_ulong),
                    ("ActiveProcesses", ctypes.c_ulong), ("TotalTerminatedProcesses", ctypes.c_ulong)]
    b=Basic()
    if not kernel32.QueryInformationJobObject(job,1,ctypes.byref(b),ctypes.sizeof(b),None): raise RuntimeError("QueryInformationJobObject failed")
    return int(b.ActiveProcesses)

def _resume_primary_thread(process: Any) -> None:
    if os.name != "nt" or not getattr(process, "pid", None): return
    kernel32 = ctypes.windll.kernel32
    TH32CS_SNAPTHREAD = 4
    THREAD_SUSPEND_RESUME = 0x2
    INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value
    kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]
    kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p
    kernel32.Thread32First.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.Thread32First.restype = ctypes.c_int
    kernel32.Thread32Next.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    kernel32.Thread32Next.restype = ctypes.c_int
    kernel32.OpenThread.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]
    kernel32.OpenThread.restype = ctypes.c_void_p
    kernel32.ResumeThread.argtypes = [ctypes.c_void_p]
    kernel32.ResumeThread.restype = ctypes.c_ulong
    kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.restype = ctypes.c_int

    class ThreadEntry32(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong),
                    ("th32ThreadID", ctypes.c_ulong), ("th32OwnerProcessID", ctypes.c_ulong),
                    ("tpBasePri", ctypes.c_long), ("tpDeltaPri", ctypes.c_long),
                    ("dwFlags", ctypes.c_ulong)]

    snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPTHREAD, 0)
    if snap in (None, 0, INVALID_HANDLE_VALUE): raise RuntimeError("CreateToolhelp32Snapshot failed")
    owned = []
    try:
        entry = ThreadEntry32(); entry.dwSize = ctypes.sizeof(entry)
        ok = kernel32.Thread32First(snap, ctypes.byref(entry))
        if not ok: raise RuntimeError("Thread32First failed")
        while ok:
            if int(entry.th32OwnerProcessID) == int(process.pid): owned.append(int(entry.th32ThreadID))
            ok = kernel32.Thread32Next(snap, ctypes.byref(entry))
        if len(owned) != 1: raise RuntimeError(f"expected exactly one primary thread, found {len(owned)}")
        thread = kernel32.OpenThread(THREAD_SUSPEND_RESUME, False, owned[0])
        if not thread: raise RuntimeError("OpenThread failed")
        try:
            previous = kernel32.ResumeThread(thread)
            if previous == 0xFFFFFFFF: raise RuntimeError("ResumeThread failed")
            if previous != 1: raise RuntimeError(f"unexpected primary suspend count: {previous}")
        finally:
            _close_handle(thread)
    finally:
        _close_handle(snap)

def _close_handle(handle: Any) -> None:
    if handle and os.name == "nt":
        kernel32 = ctypes.windll.kernel32
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        if not kernel32.CloseHandle(handle): raise RuntimeError("CloseHandle failed")

def _working_set(process: Any) -> int | None:
    if os.name != "nt": return None
    class ProcessMemoryCounters(ctypes.Structure):
        _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong), ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t), ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t), ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t), ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]
    ctypes.windll.kernel32.OpenProcess.argtypes = [ctypes.c_ulong, ctypes.c_int, ctypes.c_ulong]; ctypes.windll.kernel32.OpenProcess.restype = ctypes.c_void_p; ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]; ctypes.windll.psapi.GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_ulong]; ctypes.windll.psapi.GetProcessMemoryInfo.restype = ctypes.c_int
    h = getattr(process, "_handle", None)
    if not h: raise RuntimeError("retained process handle unavailable")
    c = ProcessMemoryCounters(); c.cb = ctypes.sizeof(c)
    if not ctypes.windll.psapi.GetProcessMemoryInfo(h, ctypes.byref(c), c.cb): raise RuntimeError("GetProcessMemoryInfo failed")
    return int(c.PeakWorkingSetSize)

def _descendants(pid: int) -> set[int]:
    if os.name != "nt": return set()
    class Entry(ctypes.Structure):
        _fields_ = [("dwSize", ctypes.c_ulong), ("cntUsage", ctypes.c_ulong), ("th32ProcessID", ctypes.c_ulong), ("th32DefaultHeapID", ctypes.c_void_p), ("th32ModuleID", ctypes.c_ulong), ("cntThreads", ctypes.c_ulong), ("th32ParentProcessID", ctypes.c_ulong), ("pcPriClassBase", ctypes.c_long), ("dwFlags", ctypes.c_ulong), ("szExeFile", ctypes.c_wchar * 260)]
    ctypes.windll.kernel32.CreateToolhelp32Snapshot.argtypes = [ctypes.c_ulong, ctypes.c_ulong]; ctypes.windll.kernel32.CreateToolhelp32Snapshot.restype = ctypes.c_void_p; ctypes.windll.kernel32.Process32FirstW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; ctypes.windll.kernel32.Process32FirstW.restype = ctypes.c_int; ctypes.windll.kernel32.Process32NextW.argtypes = [ctypes.c_void_p, ctypes.c_void_p]; ctypes.windll.kernel32.Process32NextW.restype = ctypes.c_int; ctypes.windll.kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
    snap = ctypes.windll.kernel32.CreateToolhelp32Snapshot(2, 0)
    if snap == ctypes.c_void_p(-1).value: raise RuntimeError("CreateToolhelp32Snapshot failed")
    try:
        e = Entry(); e.dwSize = ctypes.sizeof(e); rows = []
        first = ctypes.windll.kernel32.Process32FirstW(snap, ctypes.byref(e))
        if not first: raise RuntimeError("Process32FirstW failed")
        while first:
            rows.append((int(e.th32ProcessID), int(e.th32ParentProcessID))); first = ctypes.windll.kernel32.Process32NextW(snap, ctypes.byref(e))
        found, todo = set(), [pid]
        while todo:
            parent = todo.pop()
            for child, p in rows:
                if p == parent and child not in found: found.add(child); todo.append(child)
        return found
    finally: ctypes.windll.kernel32.CloseHandle(snap)

def _monitor(process: Any, root: Path, started: float, job: Any = None, observations: dict[str, Any] | None = None) -> dict[str, Any]:
    """Collect bounded resource evidence into the caller-owned observations mapping."""
    if observations is None: observations = {}
    defaults = {
        "memory_sample_count": 0, "last_working_set_bytes": None, "peak_working_set_bytes": None,
        "slow_scan_count": 0, "last_scratch_bytes": None, "peak_scratch_bytes": None,
        "final_scratch_bytes": None, "last_job_active_processes": None,
        "max_job_active_processes": None, "descendants_ok": True,
    }
    for key, value in defaults.items(): observations.setdefault(key, value)

    def sample_ws() -> None:
        ws = _working_set(process) if os.name == "nt" else None
        if os.name == "nt" and ws is None: raise RuntimeError("working-set monitor unavailable")
        if ws is not None:
            observations["memory_sample_count"] += 1
            observations["last_working_set_bytes"] = ws
            observations["peak_working_set_bytes"] = max(observations["peak_working_set_bytes"] or 0, ws)
            if ws > 24 * 1024**3: raise MemoryError("working-set cap exceeded")

    def slow_scan() -> None:
        scratch = sum(p.stat().st_size for p in root.rglob("*") if p.is_file())
        observations["slow_scan_count"] += 1
        observations["last_scratch_bytes"] = scratch
        observations["peak_scratch_bytes"] = max(observations["peak_scratch_bytes"] or 0, scratch)
        if observations["peak_scratch_bytes"] > 8 * 1024**3: raise OSError("scratch cap exceeded")
        if job is not None:
            active = _job_active_count(job)
            observations["last_job_active_processes"] = active
            observations["max_job_active_processes"] = max(observations["max_job_active_processes"] or 0, active)
            if process.poll() is None and active != 1: raise RuntimeError(f"unexpected active job process count: {active}")
        if process.poll() is None and getattr(process, "pid", 0) and _descendants(process.pid):
            observations["descendants_ok"] = False
            raise RuntimeError("unexpected solver descendant")

    # Initial retained-handle sample is mandatory on Windows, including fast exit.
    if os.name == "nt": sample_ws()
    next_slow = 0.0
    while process.poll() is None:
        if time.time() - started > 14400: raise TimeoutError("wall timeout")
        sample_ws()
        if time.monotonic() >= next_slow:
            slow_scan(); next_slow = time.monotonic() + 5.0
        time.sleep(.5)
    # Post-exit retained-handle sample and final scratch/job checks are required.
    if os.name == "nt": sample_ws()
    slow_scan()  # final scan records scratch and job evidence atomically
    final_scratch = observations["last_scratch_bytes"]
    observations["final_scratch_bytes"] = final_scratch
    if job is not None:
        active = _job_active_count(job)
        observations["last_job_active_processes"] = active
        observations["max_job_active_processes"] = max(observations["max_job_active_processes"] or 0, active)
        if active != 0: raise RuntimeError(f"job still active after exit: {active}")
    return observations

def _terminate_tree(process: Any, job: Any = None) -> tuple[dict[str, Any], str]:
    ev = {"taskkill_return_code": None, "taskkill_error": None, "terminate_job_result": None, "terminate_job_error": None,
          "final_job_active_processes": None, "final_job_active_error": None, "primary_termination_confirmed": False,
          "tree_termination_confirmed": False, "termination_confirmed": False}
    if os.name == "nt" and getattr(process, "pid", None):
        try: ev["taskkill_return_code"] = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).returncode
        except Exception as exc: ev["taskkill_error"] = str(exc)
    if job is not None and os.name == "nt":
        try:
            kernel32 = ctypes.windll.kernel32
            kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint]
            kernel32.TerminateJobObject.restype = ctypes.c_int
            result = kernel32.TerminateJobObject(job, 1)
            ev["terminate_job_result"] = result
            if not result: ev["terminate_job_error"] = "TerminateJobObject failed"
        except Exception as exc: ev["terminate_job_error"] = str(exc)
    try:
        if process.poll() is None: process.kill()
        process.wait(timeout=30)
    except Exception as exc:
        ev["taskkill_error"] = ev["taskkill_error"] or str(exc)
    ev["primary_termination_confirmed"] = process.poll() is not None
    if job is not None:
        try: ev["final_job_active_processes"] = _job_active_count(job)
        except Exception as exc: ev["final_job_active_error"] = str(exc)
    ev["tree_termination_confirmed"] = bool(ev["primary_termination_confirmed"] and ev["final_job_active_processes"] == 0)
    ev["termination_confirmed"] = ev["tree_termination_confirmed"]
    return ev, "" if ev["tree_termination_confirmed"] else "tree termination unconfirmed"

def run(output: str | os.PathLike[str], input_root: Path = INPUT_ROOT, solver: Path = SOLVER, process_factory: Callable[..., Any] = subprocess.Popen, monitor_factory: Callable[..., dict[str, Any]] = _monitor, approval_sha256: str | None = None) -> dict[str, Any]:
    output_path = Path(output).resolve(); repo = Path(__file__).resolve().parents[2]; sealed_receipt = validate_preflight(input_root, solver, output_path, repo, approval_sha256)
    output_path.parent.mkdir(parents=True, exist_ok=True); output_path.mkdir(); run_input = output_path / "input"; run_input.mkdir()
    evidence_records = _copy_evidence(output_path, repo, sealed_receipt["approval_manifest"]) if "approval_manifest" in sealed_receipt else []
    for n in FILES: shutil.copy2(input_root / n, run_input / n)
    for n in FILES:
        if digest(run_input / n) != FILE_META[n][1]: raise ValueError(f"input copy verification failed: {n}")
    argv = [str(solver.resolve()), "-b", str((run_input / "d116_shadow_gap_coupon.lst").resolve()), *ARGV_TAIL[2:]]
    expected_evidence = [("tools/research/run_d116_shadow_fastercap.py", "evidence/tools/research/run_d116_shadow_fastercap.py"), ("tests/test_run_d116_shadow_fastercap.py", "evidence/tests/test_run_d116_shadow_fastercap.py"), ("tools/research/d116_shadow_fastercap_approval_manifest.json", "evidence/tools/research/d116_shadow_fastercap_approval_manifest.json")]
    manifest_meta = sealed_receipt.get("approval_manifest", {})
    if "approval_manifest" in sealed_receipt:
        if len(evidence_records) != 3 or [(r.get("source"), r.get("copy")) for r in evidence_records] != expected_evidence:
            raise ValueError("evidence identity set incomplete")
        if any(set(r) != {"source", "copy", "size_bytes", "sha256"} for r in evidence_records):
            raise ValueError("evidence record shape failed")
        # The real approval manifest carries the source identities; mocked runs
        # may provide only the ordered evidence shape.
        for key, expected_rel in (("runner", expected_evidence[0][0]), ("tests", expected_evidence[1][0])):
            item = manifest_meta.get(key)
            if item:
                record = next(r for r in evidence_records if r["source"] == expected_rel)
                if (record["size_bytes"], record["sha256"]) != (item.get("size_bytes"), item.get("sha256")):
                    raise ValueError(f"evidence identity failed: {expected_rel}")
                if item.get("path"):
                    source_path = repo / item["path"]
                    copy_path = output_path / record["copy"]
                    if (record["size_bytes"], record["sha256"]) != (source_path.stat().st_size, digest(source_path)) or (copy_path.stat().st_size, digest(copy_path)) != (record["size_bytes"], record["sha256"]):
                        raise ValueError(f"evidence bytes failed: {expected_rel}")
        manifest_item = manifest_meta.get("_approved_size_bytes")
        manifest_sha = manifest_meta.get("_approved_sha256")
        if manifest_item is not None and (evidence_records[2]["size_bytes"], evidence_records[2]["sha256"]) != (manifest_item, manifest_sha):
            raise ValueError("evidence identity failed: manifest")
    token = output_path / "invocation_token.json"
    token_payload = {"program": PROGRAM, "output": str(output_path), "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "argv_tail": list(ARGV_TAIL), "argv": argv, "approval_manifest_size_bytes": manifest_meta.get("_approved_size_bytes"), "approval_sha256_requested": approval_sha256, "approval_sha256_observed": manifest_meta.get("_approved_sha256"), "evidence_artifacts": evidence_records, "input_receipt_sha256": RECEIPT_SHA, "solver_sha256": SOLVER_SHA, "gomp_sha256": GOMP_SHA}
    _seal_exclusive(token, (json.dumps(token_payload, sort_keys=True)+"\n").encode())
    token_artifact = {"filename": token.name, "size_bytes": token.stat().st_size, "sha256": digest(token)}
    started, return_code, status, reason = time.time(), None, "STOP_D116_SHADOW", None; matrix = np.empty((0, 0)); stats, maxwell = {}, {}
    monitor = {"termination_attempted": False, "taskkill_return_code": None, "taskkill_error": None, "terminate_job_result": None, "terminate_job_error": None, "final_job_active_processes": None, "final_job_active_error": None, "primary_termination_confirmed": False, "tree_termination_confirmed": False, "termination_confirmed": False, "job_drained": False, "job_close_attempted": False, "job_close_succeeded": False, "job_close_error": None, "job_close_backstop_used": False, "backstop_used": False, "post_close_wait_attempted": False, "post_close_wait_return_code": None, "post_close_wait_confirmed": False, "post_close_wait_error": None}
    process_started = False; process = None; job = None; drained = False; source_revalidation_passed = False; copied_revalidation_passed = False; registry_whitelisted = False; nonzero_whitelisted = False
    outlog, errlog = run_input / "stdout.txt", run_input / "stderr.txt"
    stdout_snapshot = stderr_snapshot = csv_snapshot = None
    stdout_artifact = stderr_artifact = csv_evidence = None
    input_file_records: dict[str, dict[str, Any]] = {}
    evidence_final_records: list[dict[str, Any]] = evidence_records
    snapshot_errors: list[str] = []
    try:
        with outlog.open("wb") as fo, errlog.open("wb") as fe:
            kwargs = {"cwd": str(run_input), "stdout": fo, "stderr": fe}
            job = _create_job()  # create/configure before launch; KILL_ON_JOB_CLOSE is the backstop
            if os.name == "nt":
                # Detached avoids a conhost child before Job assignment.
                kwargs["creationflags"] = getattr(subprocess, "DETACHED_PROCESS", 0x8) | getattr(subprocess, "CREATE_SUSPENDED", 0x4)
                kwargs["stdin"] = subprocess.DEVNULL
            process = process_factory(argv, **kwargs); process_started = True; _assign_job(job, process); _resume_primary_thread(process)
            monitor_factory(process, output_path, started, job, monitor); waited = process.wait(timeout=30); return_code = process.returncode if process.returncode is not None else waited
            if return_code is None: raise ValueError("solver return code unavailable")
            if job is not None:
                active = _job_active_count(job); monitor["final_job_active_processes"] = active
                if active != 0: raise RuntimeError(f"job still active after wait: {active}")
            drained = True; monitor["job_drained"] = True
            token_now = {"size_bytes": token.stat().st_size, "sha256": digest(token)}
            if token_now != {"size_bytes": token_artifact["size_bytes"], "sha256": token_artifact["sha256"]}: raise RuntimeError("invocation token changed")
        _revalidate_source(input_root); source_revalidation_passed = True
        _revalidate_run_input(run_input)
        def take_snapshot(path: Path, name: str) -> bytes | None:
            try:
                payload = path.read_bytes()
                return payload
            except BaseException as snap_exc:
                snapshot_errors.append(f"{name}: {type(snap_exc).__name__}: {snap_exc}")
                return None
        stdout_snapshot = take_snapshot(outlog, "input/stdout.txt")
        stdout_artifact = _artifact_snapshot(stdout_snapshot, "input/stdout.txt") if stdout_snapshot is not None else {"filename": "input/stdout.txt", "error": snapshot_errors[-1]}
        stderr_snapshot = take_snapshot(errlog, "input/stderr.txt")
        stderr_artifact = _artifact_snapshot(stderr_snapshot, "input/stderr.txt") if stderr_snapshot is not None else {"filename": "input/stderr.txt", "error": snapshot_errors[-1]}
        csv_snapshot = take_snapshot(run_input / "d116_shadow_gap_coupon.csv", "input/d116_shadow_gap_coupon.csv")
        csv_evidence = _artifact_snapshot(csv_snapshot, "input/d116_shadow_gap_coupon.csv") if csv_snapshot is not None else {"filename": "input/d116_shadow_gap_coupon.csv", "error": snapshot_errors[-1]}
        for n in FILES:
            payload = take_snapshot(run_input / n, f"input/{n}")
            record = _artifact_snapshot(payload, f"input/{n}") if payload is not None else {"filename": f"input/{n}", "error": snapshot_errors[-1]}
            input_file_records[n] = record
            if payload is None or (len(payload), record.get("sha256")) != FILE_META[n]:
                snapshot_errors.append(f"input/{n}: copied input changed")
        copied_revalidation_passed = not snapshot_errors and all((r.get("size_bytes"), r.get("sha256")) == FILE_META[n] for n, r in input_file_records.items())
        if snapshot_errors: raise OSError("snapshot error: " + "; ".join(snapshot_errors))
        stdout, stderr = stdout_snapshot.decode("utf-8", errors="replace"), stderr_snapshot.decode("utf-8", errors="replace")
        matrix, stats = _parse_stdout(stdout, stderr); csv = _parse_csv(csv_snapshot)
        comparison_scale = float(max(np.max(np.abs(matrix)), np.max(np.abs(csv)))); comparison_atol = comparison_scale * 1e-12; delta = float(np.max(np.abs(matrix - csv)))
        if not np.allclose(matrix, csv, rtol=1e-5, atol=comparison_atol): raise ValueError("CSV/stdout matrix mismatch")
        stats.update({"comparison_scale": comparison_scale, "comparison_rtol": 1e-5, "comparison_atol": comparison_atol, "comparison_max_abs_delta": delta})
        maxwell = _maxwell(csv); registry_whitelisted = bool(stats.get("registry_warning_whitelisted")); nonzero_whitelisted = return_code != 0 and registry_whitelisted
        if return_code != 0 and not nonzero_whitelisted: raise ValueError("nonzero solver return is not whitelisted")
        if {"size_bytes": token.stat().st_size, "sha256": digest(token)} != {"size_bytes": token_artifact["size_bytes"], "sha256": token_artifact["sha256"]}: raise RuntimeError("invocation token changed")
        evidence_final_records = []
        for prelaunch in evidence_records:
            copy_path = output_path / prelaunch["copy"]
            evidence_bytes = copy_path.read_bytes()
            observed = {"source": prelaunch["source"], "copy": prelaunch["copy"], "size_bytes": len(evidence_bytes), "sha256": sha256(evidence_bytes).hexdigest()}
            evidence_final_records.append(observed)
            if observed != prelaunch: raise ValueError(f"final evidence identity failed: {prelaunch['source']}")
        status = "PASS_D116_SHADOW_RAW_C_MATRIX"
    except (Exception, KeyboardInterrupt) as exc:
        reason = "user interrupt" if isinstance(exc, KeyboardInterrupt) else str(exc)
        try:
            if process_started and process is not None and not drained:
                monitor["termination_attempted"] = True; ev, detail = _terminate_tree(process, job); monitor.update(ev); return_code = process.returncode
                if detail: reason = f"{reason}; {detail}"
        except (Exception, KeyboardInterrupt) as term_exc:
            reason = f"{reason}; termination failed: {term_exc}; termination_confirmed=false"
        if not outlog.exists(): _write(outlog, b"")
        if not errlog.exists(): _write(errlog, b"")
    finally:
        if job is not None:
            monitor["job_close_attempted"] = True
            try: _close_handle(job); monitor["job_close_succeeded"] = True
            except BaseException as close_exc:
                monitor["job_close_error"] = f"{type(close_exc).__name__}: {close_exc}"
                status = "STOP_D116_SHADOW"
                reason = f"{reason + '; ' if reason else ''}job close failed: {close_exc}"
        if process_started and process is not None and monitor.get("termination_attempted") and not monitor.get("termination_confirmed") and monitor.get("job_close_succeeded"):
            monitor["job_close_backstop_used"] = monitor["backstop_used"] = True
            monitor["post_close_wait_attempted"] = True
            try:
                waited_after_close = process.wait(timeout=30)
                monitor["post_close_wait_return_code"] = process.returncode if process.returncode is not None else waited_after_close
                monitor["post_close_wait_confirmed"] = process.poll() is not None
            except BaseException as wait_exc:
                monitor["post_close_wait_error"] = f"{type(wait_exc).__name__}: {wait_exc}"
    sigint_state = _install_sigint_deferral()
    try:
        artifact_errors = list(snapshot_errors)
        def safe_artifact(path: Path, name: str) -> dict[str, Any]:
            try:
                record = _artifact(path, name)
                if record is None:
                    raise OSError("artifact unavailable")
                return record
            except BaseException as exc:
                error = f"{type(exc).__name__}: {exc}"
                artifact_errors.append(f"{name}: {error}")
                return {"filename": name, "error": error}

        if stdout_artifact is None: stdout_artifact = safe_artifact(outlog, "input/stdout.txt")
        if stderr_artifact is None: stderr_artifact = safe_artifact(errlog, "input/stderr.txt")
        if csv_evidence is None: csv_evidence = safe_artifact(run_input / "d116_shadow_gap_coupon.csv", "input/d116_shadow_gap_coupon.csv")
        if not input_file_records: input_file_records = {n: safe_artifact(run_input / n, f"input/{n}") for n in FILES}
        input_file_sha256 = {n: v.get("sha256") for n, v in input_file_records.items()}
        token_revalidation_passed = False
        token_revalidation_error = None
        token_final = safe_artifact(token, token.name)
        if token_final.get("size_bytes") == token_artifact.get("size_bytes") and token_final.get("sha256") == token_artifact.get("sha256"):
            token_revalidation_passed = True
        else:
            token_revalidation_error = "invocation token changed or missing"
            artifact_errors.append(token_revalidation_error)
        if artifact_errors:
            artifact_error = "; ".join(artifact_errors)
            status = "STOP_D116_SHADOW"
            reason = f"{reason + '; ' if reason else ''}artifact error: {artifact_error}"
        else:
            artifact_error = None
        ended = time.time()
        result = {"schema_version":"d116-shadow-fastercap-run-v1", "program":PROGRAM, "status":status, "stop_reason":reason, "utc_start":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)), "utc_end":time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(ended)), "argv":argv, "cwd":str(run_input), "numerical_invocations":1 if process_started else 0, "return_code":return_code, "input_receipt_sha256":RECEIPT_SHA, "governing_inputs":sealed_receipt.get("inputs", {}), "input_file_sha256":input_file_sha256, "input_file_artifacts":input_file_records, "solver_sha256":SOLVER_SHA, "gomp_sha256":GOMP_SHA, "stdout_artifact":stdout_artifact, "stderr_artifact":stderr_artifact, "csv_artifact":csv_evidence, "registry_diagnostic_whitelisted":registry_whitelisted, "nonzero_return_whitelisted":nonzero_whitelisted, "conductor_order":["A_GND_259", "A_PWR_264", "F_DDRL_262"], "expected_solver_labels":list(LABELS), "expected_solver_triangles":336, "matrix_units":"F", "solver_version":"6.0.7", "partition":{"A":[259,264],"F":[262]}, "material":{"description":"homogeneous 1 MHz material snapshot only","frequency_hz":1000000.0,"epsilon_r":3.4,"loss_tangent":0.0041,"loss_tangent_modeled":False,"dielectric_loss_or_conductance_claim":False}, "scope":{"full_domain":False,"production_oracle":False,"powersi_comparable_zii":False,"powersi_zii_readiness":False,"lossless_1mhz_material_snapshot":True,"loss_tangent_provenance_not_modeled":True,"raw_maxwell_c_only":True,"no_transform_or_injection":True,"omitted_layers":["L28","L31"],"full_domain_layers":["L28","L29","L30","L31"],"included_layers":["L29","L30"],"artificial_w0_crop_walls":True}, "matrix_raw":matrix.tolist(), "matrix_stats":stats, "maxwell_gates":maxwell}
        result["stdout_sha256"] = (result["stdout_artifact"] or {}).get("sha256")
        result["stderr_sha256"] = (result["stderr_artifact"] or {}).get("sha256")
        result["approval_sha256_requested"] = approval_sha256
        result["approval_sha256_observed"] = sealed_receipt.get("approval_manifest", {}).get("_approved_sha256")
        result["token_artifact"] = token_artifact
        result["token_final_observation"] = token_final
        result["token_revalidation_error"] = token_revalidation_error
        result["artifact_error"] = artifact_error
        result["token_revalidation_passed"] = token_revalidation_passed
        result["source_revalidation_passed"] = source_revalidation_passed
        result["copied_revalidation_passed"] = copied_revalidation_passed
        result["evidence_artifacts"] = evidence_final_records
        result["resource_observations"] = {"wall_seconds": ended-started, "wall_timeout_seconds":14400, "working_set_cap_bytes":24*1024**3, "scratch_cap_bytes":8*1024**3, **monitor}
        receipt_path = output_path / "d116_shadow_fastercap_receipt.json"
        payload = (json.dumps(result, sort_keys=True, separators=(",",":"))+"\n").encode()
        tmp = output_path / ".receipt.tmp"; _write(tmp, payload); os.replace(tmp, receipt_path)
        receipt_sha = digest(receipt_path); sidecar = receipt_path.with_name(receipt_path.name + ".sha256")
        _seal_exclusive(sidecar, f"{receipt_sha}  {receipt_path.name}\n".encode())
    except BaseException:
        _restore_sigint_deferral(sigint_state, replay=False)
        raise
    _restore_sigint_deferral(sigint_state, replay=True)
    return result

def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=PROGRAM + " one-shot runner"); p.add_argument("--approval-sha256", required=True, type=lambda value: value if re.fullmatch(r"[0-9a-f]{64}", value) else (_ for _ in ()).throw(argparse.ArgumentTypeError("must be 64 lowercase hex"))); args = p.parse_args(argv); result = run(OUTPUT_ROOT, approval_sha256=args.approval_sha256); print(json.dumps({"program":PROGRAM,"status":result["status"],"output":str(OUTPUT_ROOT)})); return 0 if result["status"].startswith("PASS") else 1

if __name__ == "__main__": raise SystemExit(main())
