from __future__ import annotations

from pathlib import Path
import tomllib

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
    assert __version__ == "0.10.1"
    assert APP_DISPLAY_NAME == "SPD Decap PI Evaluator v0.10.1"
    assert EXECUTABLE_BASENAME == "SPDDecapPIEvaluator"
    assert INSTALLER_BASENAME == "SPDDecapPIEvaluatorSetup-0.10.1"


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


def test_installer_build_cannot_silently_reuse_a_stale_application() -> None:
    installer_build = (
        REPO_ROOT / "scripts" / "build_spd_decap_pi_installer.ps1"
    ).read_text(encoding="utf-8")
    assert "SkipBuild" not in installer_build
    assert "build_spd_decap_pi.ps1" in installer_build
    assert "Get-FileHash" in installer_build
    assert '"$installer.sha256"' in installer_build


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
    assert "filevers=(0, 10, 1, 0)" in version_info
    assert "prodvers=(0, 10, 1, 0)" in version_info
    assert "StringStruct('FileVersion', '0.10.1')" in version_info
    assert "StringStruct('ProductVersion', '0.10.1')" in version_info
