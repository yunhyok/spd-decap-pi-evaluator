from __future__ import annotations

from hashlib import sha1, sha256
import json
from pathlib import Path
import re
import tomllib

from spd_decap_pi._core import services as core_services
from spd_decap_pi._core.version import __version__ as CORE_VERSION
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
    assert __version__ == "0.22.6"
    assert CORE_VERSION == "0.22.6"
    assert APP_DISPLAY_NAME == "SPD Decap PI Evaluator v0.22.6"
    assert EXECUTABLE_BASENAME == "SPDDecapPIEvaluator"
    assert INSTALLER_BASENAME == "SPDDecapPIEvaluatorSetup-0.22.6"


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
    assert "filevers=(0, 22, 6, 0)" in version_info
    assert "prodvers=(0, 22, 6, 0)" in version_info
    assert "StringStruct('FileVersion', '0.22.6')" in version_info
    assert "StringStruct('ProductVersion', '0.22.6')" in version_info


def test_packaged_numerical_path_imports_threadpoolctl() -> None:
    """The declared BLAS limiter is available through the production service."""

    assert core_services.threadpool_limits is not None


def test_distribution_methodology_docs_are_current_offline_and_linked() -> None:
    markdown_path = REPO_ROOT / "docs" / "DECAP_DISTRIBUTION_RULES.md"
    html_path = REPO_ROOT / "docs" / "DECAP_DISTRIBUTION_RULES.companion.html"
    markdown = markdown_path.read_bytes().decode("utf-8")
    html = html_path.read_text(encoding="utf-8")
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    canonical_markdown = markdown.replace("\r\n", "\n").replace("\r", "\n")
    source_hash = sha256(canonical_markdown.encode("utf-8")).hexdigest()

    assert "SPD Decap PI Evaluator v0.22.5" in markdown
    assert "v0.22.5 release note" in markdown
    assert "로컬 final component" in markdown
    assert "NO_ZERO_GAP_EXACT_COUNT_COMBINATION" in markdown
    assert "exact 15 + 3 + gap 1" in markdown
    assert "TARGET_RELATION_COUNTERFLOW_V1" in markdown
    assert "Receiver Net Progress = I - O - S" in markdown
    assert "A → B → C" in markdown
    assert '<html lang="ko">' in html
    assert '<meta charset="utf-8">' in html
    assert (
        '<meta name="source-sha256-normalization" content="utf-8-lf">'
        in html
    )
    assert "SPD Decap PI Evaluator v0.22.5" in html
    assert "v0.22.5 release note" in html
    assert f'content="{source_hash}"' in html
    assert "TARGET_RELATION_COUNTERFLOW_V1" in html
    assert 'id="handoff"' in html
    assert html.count('<svg viewBox=') >= 4
    assert html.count('role="img"') >= 4
    assert html.count('role="region" tabindex="0" aria-labelledby=') >= 4
    assert "overflow-x: auto;" in html
    assert "min-width: 56rem;" in html
    assert "overscroll-behavior-inline: contain;" in html
    assert "figure svg { min-width: 0; }" in html
    assert "http://" not in html and "https://" not in html
    assert "docs/DECAP_DISTRIBUTION_RULES.md" in readme
    assert "docs/DECAP_DISTRIBUTION_RULES.companion.html" in readme


def test_readme_companion_and_manifest_are_current_and_hash_bound() -> None:
    readme_path = REPO_ROOT / "README.md"
    readme_companion_path = REPO_ROOT / "README.companion.html"
    methodology_path = REPO_ROOT / "docs" / "DECAP_DISTRIBUTION_RULES.md"
    methodology_companion_path = (
        REPO_ROOT / "docs" / "DECAP_DISTRIBUTION_RULES.companion.html"
    )
    manifest = json.loads(
        (REPO_ROOT / ".html-companions.json").read_text(encoding="utf-8")
    )
    documents = {entry["source"]: entry for entry in manifest["documents"]}

    def canonical_bytes(path: Path) -> bytes:
        return path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace(
            "\r", "\n"
        ).encode("utf-8")

    def canonical_blob_sha(path: Path) -> str:
        source = canonical_bytes(path)
        return sha1(b"blob " + str(len(source)).encode("ascii") + b"\0" + source).hexdigest()

    pairs = [
        ("README.md", readme_companion_path),
        ("docs/DECAP_DISTRIBUTION_RULES.md", methodology_companion_path),
    ]
    for source_name, output_path in pairs:
        source_path = REPO_ROOT / source_name
        source = canonical_bytes(source_path)
        output = canonical_bytes(output_path)
        entry = documents[source_name]
        assert entry["sourceBlobSha"] == canonical_blob_sha(source_path)
        assert entry["sourceSha256"] == sha256(source).hexdigest()
        assert entry["outputSha256"] == sha256(output).hexdigest()
        assert entry["output"] == output_path.relative_to(REPO_ROOT).as_posix()

    readme_companion = readme_companion_path.read_text(encoding="utf-8")
    assert "SPD Decap PI Evaluator v0.22.6" in readme_companion
    assert "v0.22.6 Evaluation fallback" in readme_companion
    assert "LOW confidence" in readme_companion
    assert "source scenario immutable" in readme_companion
    assert "v0.22.5 loader-performance release note" in readme_companion
    assert "#v0225-loader-performance-release-note" in readme_companion
    source_headings = re.findall(
        r"(?m)^#{1,6} (.+)$", readme_path.read_text(encoding="utf-8")
    )
    assert len(source_headings) == 15
    assert readme_companion.count('<h2 id=') == len(source_headings) - 1

    def heading_slug(label: str) -> str:
        slug = re.sub(r"[^\w\s-]", "", label.casefold(), flags=re.UNICODE)
        return re.sub(r"[-\s]+", "-", slug).strip("-")

    for label in source_headings:
        slug = heading_slug(label)
        assert f'id="{slug}"' in readme_companion
        assert f'href="#{slug}"' in readme_companion
        assert f">{label}</" in readme_companion
    readme_hash = sha256(canonical_bytes(readme_path)).hexdigest()
    assert f'<meta name="source-sha256" content="{readme_hash}">' in readme_companion
    assert f"Source SHA-256: <code>{readme_hash}</code>" in readme_companion
    assert "SPDDecapPIEvaluatorSetup-0.22.6.exe" in readme_companion

    methodology_evidence = documents[
        "docs/DECAP_DISTRIBUTION_RULES.md"
    ]["evidence"]
    assert any(
        item["lineStart"] == 30
        and item["lineEnd"] == 38
        and "v0.22.5 release note" in item["summary"]
        for item in methodology_evidence
    )
    assert any(
        item["lineStart"] == 89
        and item["lineEnd"] == 105
        and "Terminology" in item["summary"]
        for item in methodology_evidence
    )
    assert any(
        item["lineStart"] == 127
        and item["lineEnd"] == 149
        and "DONOR" in item["summary"]
        for item in methodology_evidence
    )


def test_evaluation_accuracy_companion_hash_is_consistent_everywhere() -> None:
    source_path = REPO_ROOT / "docs" / "EVALUATION_ACCURACY.md"
    companion = (REPO_ROOT / "docs" / "EVALUATION_ACCURACY.companion.html").read_text(encoding="utf-8")
    source = source_path.read_bytes().decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    digest = sha256(source).hexdigest()
    assert "v0.22.6 strict Evaluation policy" in companion
    assert "LOW-confidence" in companion
    assert "immutable" in companion
    assert "fail closed" in companion
    assert f"source-sha256={digest}" in companion
    assert f'<meta name="source-sha256" content="{digest}">' in companion
    assert f"Source SHA-256: <code>{digest}</code>" in companion
    manifest = json.loads((REPO_ROOT / ".html-companions.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["documents"] if item["source"] == "docs/EVALUATION_ACCURACY.md")
    assert entry["sourceSha256"] == digest
