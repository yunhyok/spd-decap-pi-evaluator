from __future__ import annotations

from hashlib import sha1, sha256
from html import unescape
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import textwrap
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
    assert __version__ == "0.22.9"
    assert CORE_VERSION == "0.22.9"
    assert APP_DISPLAY_NAME == "SPD Decap PI Evaluator v0.22.9"
    assert EXECUTABLE_BASENAME == "SPDDecapPIEvaluator"
    assert INSTALLER_BASENAME == "SPDDecapPIEvaluatorSetup-0.22.9"
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    companion = (REPO_ROOT / "README.companion.html").read_text(encoding="utf-8")
    assert readme.splitlines()[0] == f"# {APP_DISPLAY_NAME}"
    assert f"> 프로그램: **{APP_DISPLAY_NAME}**" in readme
    assert f"<h1>{APP_DISPLAY_NAME}</h1>" in companion
    assert f"프로그램: <strong>{APP_DISPLAY_NAME}</strong>" in companion


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
    assert "filevers=(0, 22, 9, 0)" in version_info
    assert "prodvers=(0, 22, 9, 0)" in version_info
    assert "StringStruct('FileVersion', '0.22.9')" in version_info
    assert "StringStruct('ProductVersion', '0.22.9')" in version_info


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

    assert "SPD Decap PI Evaluator v0.22.9" in markdown
    assert "v0.22.6/v0.22.7은 Distribution 방법론과 물리를 변경하지 않았다" in markdown
    assert "v0.22.5 release note" in markdown
    assert "로컬 final component" in markdown
    assert "NO_ZERO_GAP_EXACT_COUNT_COMBINATION" in markdown
    assert "exact 15 + 3 + gap 1" in markdown
    assert "TARGET_RELATION_COUNTERFLOW_V1" in markdown
    assert "Receiver Net Progress = I - O - S" in markdown
    assert "A → B → C" in markdown
    assert '<html lang="ko">' in html
    assert '<meta charset="utf-8">' in html
    assert f'<meta name="source-sha256" content="{source_hash}">' in html
    assert "SPD Decap PI Evaluator v0.22.9" in html
    assert "v0.22.6/v0.22.7은 Distribution 방법론과 물리를 변경하지 않았다" in html
    assert "v0.22.5 release note" in html
    assert f'content="{source_hash}"' in html
    assert "TARGET_RELATION_COUNTERFLOW_V1" in html
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
    for source_name, entry in documents.items():
        source = canonical_bytes(REPO_ROOT / source_name)
        output = canonical_bytes(REPO_ROOT / entry["output"])
        assert entry["sourceSha256"] == sha256(source).hexdigest()
        assert entry["outputSha256"] == sha256(output).hexdigest()

    readme_companion = readme_companion_path.read_text(encoding="utf-8")
    readme_markdown = readme_path.read_text(encoding="utf-8")
    assert "SPD Decap PI Evaluator v0.22.9" in readme_companion
    assert "v0.22.7 source-graph and convergence release note" in readme_companion
    assert "Thermal Trace parsing, exact same-layer artwork component reachability" in readme_companion
    assert "L09 (MAIN_POWER1) / L08 (DGND)" in readme_companion
    assert "FINAL_TEMPLATE_ARTWORK_CONTAINMENT_V2" in readme_companion
    assert "PowerSI/SIwave" in readme_companion
    assert "sign-off" in readme_companion
    assert "re-import of the" in readme_companion
    assert "matching raw SPD" in readme_companion
    assert "LOW confidence" in readme_companion
    assert "source scenario remains immutable" in readme_companion
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

    first_h1 = re.search(r"<article[^>]*>\s*<h1[^>]*>(.*?)</h1>", readme_companion, re.S)
    assert first_h1 is not None
    assert unescape(re.sub(r"<[^>]+>", "", first_h1.group(1))).strip() == source_headings[0]
    first_toc = re.search(r'<nav class="toc".*?<a href="#([^"]+)">', readme_companion, re.S)
    assert first_toc is not None
    assert first_toc.group(1) == heading_slug(source_headings[0])
    readme_hash = sha256(canonical_bytes(readme_path)).hexdigest()
    assert f'<meta name="source-sha256" content="{readme_hash}">' in readme_companion
    assert f"Source SHA-256: <code>{readme_hash}</code>" in readme_companion
    for document in (readme_markdown, readme_companion):
        assert "SPDDecapPIEvaluatorSetup-0.22.9.exe" in document
        assert "SPDDecapPIEvaluatorSetup-0.22.7.exe" not in document

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
    assert "v0.22.9, strict Evaluation" in companion
    assert "L09 (MAIN_POWER1) / L08 (DGND)" in companion
    assert "SOURCE_GRAPH_PLANE_PAIR_UNRESOLVED" in companion
    assert "FINAL_TEMPLATE_ARTWORK_CONTAINMENT_V2" in companion
    assert "m8 (81 modes)" in companion
    assert "bounded m14" in companion
    assert "LOW confidence" in companion
    assert "immutable" in companion
    assert "fail-closed" in companion
    assert f"source-sha256={digest}" in companion
    assert f'<meta name="source-sha256" content="{digest}">' in companion
    assert f"Source SHA-256: <code>{digest}</code>" in companion
    manifest = json.loads((REPO_ROOT / ".html-companions.json").read_text(encoding="utf-8"))
    entry = next(item for item in manifest["documents"] if item["source"] == "docs/EVALUATION_ACCURACY.md")
    assert entry["sourceSha256"] == digest


def test_build_collects_both_vendored_scipy_array_api_layouts() -> None:
    """pyproject allows scipy>=1.15, which spans the _lib -> _external rename."""

    build_script = (REPO_ROOT / "scripts" / "build_spd_decap_pi.ps1").read_text(
        encoding="utf-8"
    )
    pyproject = tomllib.loads(
        (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert any(
        item.startswith("scipy") for item in pyproject["project"]["dependencies"]
    )

    for package in (
        "scipy._lib.array_api_compat",
        "scipy._lib.array_api_extra",
        "scipy._external.array_api_compat",
        "scipy._external.array_api_extra",
    ):
        assert f'"--collect-submodules", "{package}"' in build_script
    # --collect-submodules is a silent no-op for the layout that is absent, so
    # the reason for listing both must stay recorded next to the flags.
    assert "scipy._external.* from scipy 1.18" in build_script


def test_release_workflow_refuses_a_tag_that_does_not_match_the_package() -> None:
    """A ``v*`` tag push must fail before building when the version was not bumped."""

    workflow = (
        REPO_ROOT / ".github" / "workflows" / "windows-release.yml"
    ).read_text(encoding="utf-8")

    assert "- name: Verify tag matches packaged version" in workflow
    # The guard must not break the manual workflow_dispatch entry point.
    assert "if: startsWith(github.ref, 'refs/tags/')" in workflow
    assert 'ref_name != f"v{package_version}"' in workflow
    assert 'os.environ["GITHUB_REF_NAME"]' in workflow
    # Both version sources must agree: the artifact name uses the tag while the
    # installer filename comes from src/spd_decap_pi/version.py.
    assert 'pathlib.Path("src/spd_decap_pi/version.py")' in workflow
    assert 'pathlib.Path("pyproject.toml")' in workflow
    assert workflow.index("- name: Verify tag matches packaged version") < workflow.index(
        "- name: Build and test installer"
    )

    guard = workflow.split("        run: |\n", 1)[1].split(
        "      - name: Install Inno Setup", 1
    )[0]
    script = textwrap.dedent(guard)
    for ref_name, expected_exit in (("v" + __version__, 0), ("v0.0.0", 1), (__version__, 1)):
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            cwd=REPO_ROOT,
            env={**os.environ, "GITHUB_REF_NAME": ref_name},
            capture_output=True,
            text=True,
        )
        assert completed.returncode == expected_exit, (ref_name, completed.stderr)


def test_offline_companion_relative_links_resolve_to_tracked_files() -> None:
    """The offline HTML doc set must not link to companions that were never generated."""

    companions = sorted(REPO_ROOT.rglob("*.companion.html"))
    assert companions
    dangling: list[str] = []
    for companion in companions:
        html = companion.read_text(encoding="utf-8")
        for href in re.findall(r'href="([^"]+)"', html):
            if href.startswith(("#", "http://", "https://", "mailto:", "data:")):
                continue
            target = (companion.parent / href.split("#", 1)[0]).resolve()
            if not target.is_file():
                dangling.append(
                    f"{companion.relative_to(REPO_ROOT).as_posix()} -> {href}"
                )
    assert dangling == []


def test_separator_reoptimization_is_documented_as_min_gaps_only() -> None:
    """distribution.py runs the fixed-assignment separator stage only for MIN_GAPS."""

    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    readme_companion = (REPO_ROOT / "README.companion.html").read_text(encoding="utf-8")
    methodology = (REPO_ROOT / "docs" / "DECAP_DISTRIBUTION_RULES.md").read_text(
        encoding="utf-8"
    )

    assert (
        "separator pad를 재최적화하는 단계는 `MIN_GAPS`에서만 수행하며, "
        "원자적 topology 검증을 통과한 불필요 gap 복원은 모든 policy에 적용하여"
    ) in readme
    assert (
        "separator pad를 재최적화하는 단계는 <code>MIN_GAPS</code>에서만 수행하며, "
        "원자적 topology 검증을 통과한 불필요 gap 복원은 모든 policy에 적용하여"
    ) in readme_companion
    assert (
        "수량·이동 assignment를 고정한 뒤 separator pad를 재최적화하는 단계는\n"
        "  `MIN_GAPS`에서만 수행한다."
    ) in methodology
