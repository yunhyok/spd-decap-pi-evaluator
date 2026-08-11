from __future__ import annotations

from pathlib import Path
import tomllib

from spd_decap_pi._core import services as core_services
from spd_decap_pi.version import (
    APP_DISPLAY_NAME,
    APP_NAME,
    EXECUTABLE_BASENAME,
    INSTALLER_BASENAME,
    __version__,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_spd_decap_release_identity_is_explicit_and_versioned() -> None:
    assert APP_NAME == "SPD Decap PI Evaluator"
    assert __version__ == "0.23.0"
    assert APP_DISPLAY_NAME == "SPD Decap PI Evaluator v0.23.0"
    assert EXECUTABLE_BASENAME == "SPDDecapPIEvaluator"
    assert INSTALLER_BASENAME == "SPDDecapPIEvaluatorSetup-0.23.0"


def test_spd_decap_console_and_packaging_metadata_are_consistent() -> None:
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    installer = (REPO_ROOT / "packaging" / "spd_decap_pi.iss").read_text(
        encoding="utf-8"
    )
    build_script = (REPO_ROOT / "scripts" / "build_spd_decap_pi.ps1").read_text(
        encoding="utf-8"
    )

    assert (
        pyproject["project"]["scripts"]["spd-decap-pi-evaluator"]
        == "spd_decap_pi.gui.app:main"
    )
    assert pyproject["project"]["name"] == "spd-decap-pi-evaluator"
    assert pyproject["project"]["version"] == __version__
    assert "XlsxWriter>=3.2" in pyproject["project"]["dependencies"]
    assert "openpyxl>=3.1" in pyproject["project"]["dependencies"]
    assert set(pyproject["project"]["scripts"]) == {"spd-decap-pi-evaluator"}
    assert f'#define AppVersion "{__version__}"' in installer
    assert f'#define AppName "{APP_NAME}"' in installer
    assert "AppId={{9D82CD53-E925-4C44-961A-735D37728A36}" in installer
    assert "OutputBaseFilename=SPDDecapPIEvaluatorSetup-{#AppVersion}" in installer
    assert "dist\\SPDDecapPIEvaluator\\SPDDecapPIEvaluator.exe" in build_script
    assert "--version-file" in build_script
    assert "packaging\\spd_decap_pi_version_info.txt" in build_script


def test_spd_decap_packaging_does_not_target_the_existing_app_outputs() -> None:
    files = [
        REPO_ROOT / "scripts" / "build_spd_decap_pi.ps1",
        REPO_ROOT / "scripts" / "build_spd_decap_pi_installer.ps1",
        REPO_ROOT / "packaging" / "spd_decap_pi.iss",
    ]
    combined = "\n".join(path.read_text(encoding="utf-8") for path in files)

    assert "dist\\ProbeCardMloPdn" not in combined
    assert "ProbeCardMloPdnSetup" not in combined


def test_application_build_rejects_a_foreign_editable_import() -> None:
    build_script = (
        REPO_ROOT / "scripts" / "build_spd_decap_pi.ps1"
    ).read_text(encoding="utf-8")

    install = 'Invoke-Native $pythonExe @("-m", "pip", "install", "-e", ".[dev]")'
    guard = 'Invoke-Native $pythonExe @("-c", $originCheck, $expectedPackageRoot)'
    assert '$env:PYTHONNOUSERSITE = "1"' in build_script
    assert "spd_decap_pi.__file__" in build_script
    assert "active-checkout import guard failed" in build_script
    assert build_script.index(install) < build_script.index(guard)
    assert build_script.index(guard) < build_script.index('"-m", "pytest"')


def test_installer_build_cannot_silently_reuse_a_stale_application() -> None:
    application_build = (
        REPO_ROOT / "scripts" / "build_spd_decap_pi.ps1"
    ).read_text(encoding="utf-8")
    installer_build = (
        REPO_ROOT / "scripts" / "build_spd_decap_pi_installer.ps1"
    ).read_text(encoding="utf-8")
    assert "SkipBuild" not in installer_build
    assert "build_spd_decap_pi.ps1" in installer_build
    assert "applicationBuildStartedAt" in application_build
    assert "did not refresh the application executable" in application_build
    assert "installerBuildStartedAt" in installer_build
    assert "did not refresh the installer" in installer_build
    assert "Get-FileHash" in installer_build
    assert '"$installer.sha256"' in installer_build
    assert "AllowDirtyWorktree" in installer_build
    assert "Installer release builds require a clean worktree" in installer_build
    assert "git status --porcelain=v1 --untracked-files=all" in installer_build
    assert 'schema = "spd-decap-installer-build-v1"' in installer_build
    assert 'worktree_clean = $worktreeClean' in installer_build
    assert 'dirty_override_used = [bool]$AllowDirtyWorktree' in installer_build
    assert '"$installer.build.json"' in installer_build
    assert "postBuildStatus" in installer_build
    assert "postBuildCommit -ne $sourceCommit" in installer_build
    assert "Release source changed while the installer was building" in installer_build


def test_release_workflow_smokes_the_silently_installed_application() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "windows-release.yml"
    ).read_text(encoding="utf-8")

    assert "Silent install and test installed application" in workflow
    assert '"/VERYSILENT"' in workflow
    assert '"/SUPPRESSMSGBOXES"' in workflow
    assert '"--smoke-test"' in workflow
    assert "Installed application smoke test failed" in workflow
    assert "FilePrivatePart" in workflow
    assert "ProductPrivatePart" in workflow
    assert "Installed FileVersion $fileVersion is not $expectedQuadVersion" in workflow
    assert "Installed ProductVersion $productVersion is not $expectedQuadVersion" in workflow
    assert "Installed FileVersion string is not the application version" in workflow
    assert "Installed ProductVersion string is not the application version" in workflow


def test_release_workflow_requires_tracked_v5_nonregression_evidence() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "windows-release.yml"
    ).read_text(encoding="utf-8")

    gate = "Validate tracked v5/r4 evidence"
    build = "Build and test installer"
    upload = "Upload installer"
    assert gate in workflow
    assert workflow.index(gate) < workflow.index(build) < workflow.index(upload)
    assert "git ls-files --error-unmatch" in workflow
    assert "scripts/validate_correlation_v5.py" in workflow
    assert "scripts/validate_known_case_nonregression.py" in workflow
    assert ".codex/capture_r4_numerical_source_manifest.py" in workflow
    assert ".codex/run_release_correlations_r4.ps1" in workflow
    assert "validation-fixtures/r4-numerical-source-start-manifest.json" in workflow
    assert (
        "e7512fe41612aaf8c591d4a0fc8ebd60420d52f623f396d1edf65886dbb8702e"
        in workflow
    )
    assert "R4 numerical-source byte verification failed" in workflow
    assert "PIP_CONSTRAINT=$constraintPath" in workflow
    assert '"numpy==$($sourceIdentity.runtime.numpy_version)"' in workflow
    assert '"scipy==$($sourceIdentity.runtime.scipy_version)"' in workflow
    assert 'python-version: "3.12.10"' in workflow
    assert "--verify-sidecar" in workflow
    assert "$env:RUNNER_TEMP" in workflow
    for case_id in ("260729", "260804"):
        report = (
            "validation-fixtures/known-case-nonregression-v1/"
            f"{case_id}/r4/correlation_report.json"
        )
        assert report in workflow


def test_tagged_release_publishes_installer_and_validation_evidence() -> None:
    workflow = (
        REPO_ROOT / ".github" / "workflows" / "windows-release.yml"
    ).read_text(encoding="utf-8")

    gate = "Validate tracked v5/r4 evidence"
    upload = "Upload installer"
    publish = "Publish GitHub Release"
    assert workflow.index(gate) < workflow.index(upload) < workflow.index(publish)
    assert "contents: write" in workflow
    assert "contents: read" in workflow
    assert "\n  publish:\n" in workflow
    assert "needs: installer" in workflow
    assert "if: startsWith(github.ref, 'refs/tags/')" in workflow
    assert "GH_TOKEN: ${{ github.token }}" in workflow
    assert "gh release create" in workflow
    assert "gh release view" in workflow
    assert "gh release upload" in workflow
    assert "--clobber" in workflow
    assert "gh release download" in workflow
    assert "GitHub Release remote asset inventory is not exact" in workflow
    assert "Downloaded release asset identity mismatch" in workflow
    assert "--verify-tag" in workflow
    assert "#Windows installer" in workflow
    assert "#SHA-256 checksum" in workflow
    assert "#Known-case non-regression attestation" in workflow
    assert "#PowerSI correlation report $caseId" in workflow
    resolve = "Resolve release identity"
    verify = "Verify exact installer identity"
    assert resolve in workflow
    assert verify in workflow
    assert workflow.index(resolve) < workflow.index(gate)
    assert workflow.index(resolve) < workflow.index("Build and test installer")
    runtime_gate = "Verify exact R4 source and build-host runtime"
    assert runtime_gate in workflow
    constraint = '"PIP_CONSTRAINT=$constraintPath"'
    assert workflow.index(constraint) < workflow.index("Build and test installer")
    assert workflow.index("Build and test installer") < workflow.index(runtime_gate)
    assert workflow.index(runtime_gate) < workflow.index(upload)
    assert "--verify-runtime" in workflow
    assert '$env:PYTHONNOUSERSITE = "1"' in workflow
    assert "Build-host runtime differs from validated R4 runtime" in workflow
    assert "SPDDecapPIEvaluatorSetup-" in workflow
    assert "SPDDecapPIEvaluatorSetup-{0}.exe" in workflow
    assert "$env:SPD_DECAP_APP_VERSION" in workflow
    assert '$env:GITHUB_REF_NAME -ne "v$version"' in workflow
    assert "Release tag $env:GITHUB_REF_NAME does not match" in workflow
    assert "Select-Object -First 1" not in workflow
    assert "path: release-artifacts/*" in workflow
    artifact_name = "SPDDecapPIEvaluator-${{ github.run_id }}"
    assert workflow.count(artifact_name) == 2
    assert "overwrite: true" in workflow
    assert "SPDDecapPIEvaluator-${{ github.ref_name }}" not in workflow
    assert "Expected exactly seven downloaded release assets" in workflow
    assert "spd-decap-release-provenance-v1" in workflow
    assert "#Release provenance" in workflow
    assert "Installer build manifest is not bound to the validated source" in workflow
    assert "Publish job source/tag identity differs from the installer job" in workflow
    assert "Validated release commit is not reachable from origin/main" in workflow
    assert "Remote release tag moved before publication" in workflow
    assert "Remote release tag moved during publication" in workflow
    assert "Existing GitHub Release provenance differs from this build" in workflow


def test_standalone_source_has_no_external_mlo_package_or_optimization_ui() -> None:
    source_root = REPO_ROOT / "src"
    assert not (source_root / "mlo_pdn").exists()
    assert not (source_root / "spd_decap_pi" / "_core" / "gui" / "main_window.py").exists()
    gui_text = (source_root / "spd_decap_pi" / "gui" / "main_window.py").read_text(
        encoding="utf-8"
    )
    assert "Optimization Mode" not in gui_text
    assert "optimize_workspace" not in gui_text


def test_windows_version_resource_matches_release_identity() -> None:
    version_info = (
        REPO_ROOT / "packaging" / "spd_decap_pi_version_info.txt"
    ).read_text(encoding="utf-8")
    assert "filevers=(0, 23, 0, 0)" in version_info
    assert "prodvers=(0, 23, 0, 0)" in version_info
    assert "StringStruct('FileVersion', '0.23.0')" in version_info
    assert "StringStruct('ProductVersion', '0.23.0')" in version_info


def test_packaged_numerical_path_imports_threadpoolctl() -> None:
    """The declared BLAS limiter is available through the production service."""

    assert core_services.threadpool_limits is not None
