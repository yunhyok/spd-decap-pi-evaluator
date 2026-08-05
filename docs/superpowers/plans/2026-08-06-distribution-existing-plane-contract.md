# Distribution Existing-Plane Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Distribution prove that an immutable source-classified PWR-side
Via landing lies in exact destination PWR copper under a filled-Cu microvia-stack
retarget/rebuild planning assumption, and split compact balance status from
narrative logs.

**Architecture:** Keep Evaluation plane-pair selection untouched. Use the
source-classified physical PWR TOP landing as an immutable vertical projection
origin and test its exact XY against every retained destination PWR layer; do
not require the original Via span to reach the target. Then feed that
destination proof into existing direct/shared-pad topology optimization.
Receiver and allowed exchange-chain destinations are projected.
The GUI exposes a structured balance state whose compact and narrative renderers
have separate destinations.

**Tech Stack:** Python 3.12, Pydantic, Shapely 2 vectorized predicates, SciPy/HiGHS, PySide6, pytest, PyInstaller, Inno Setup.

---

### Task 1: Lock the vertical existing-plane contract

**Files:**
- Modify: `tests/test_spd_decap_distribution.py`
- Modify: `src/spd_decap_pi/distribution.py`

- [x] **Step 1: Write failing projection tests**

Add tests for a target PWR layer with no adjacent Evaluation-valid GND pair, an
otherwise covering target layer that is below the original Via span, and a
trace/path endpoint inside copper while immutable landing XY is outside. Assert
that the source-classified physical PWR landing plus exact landing XY is
accepted and trace/path coordinates never affect it.

- [x] **Step 2: Run tests and verify RED**

Run:

```powershell
python -m pytest -q tests/test_spd_decap_distribution.py -k "nonadjacent or immutable_landing"
```

Expected: failures showing the non-adjacent PWR layer is absent and the bent
endpoint is incorrectly accepted.

- [x] **Step 3: Implement Distribution-only physical-landing proof and layer enumeration**

Replace receiver/exchange reliance on `suggest_effective_plane_pairs()` with all
retained conductor layers whose `pwr_nets` contain the destination rail net. Keep
the selected source pair fallback for source-recovery-only rails. Use a
source-classified PWR-side TOP landing as the projection origin and query every
candidate shape at `landing.x_um, landing.y_um`; an exact copper hit permits a
filled-Cu microvia-stack retarget/rebuild even when the original Via did not span
that target layer. Sort candidate keys by stack order. Preserve
Evaluation-selected `RailEligibility.pwr_layer/gnd_layer`; store the actual
target layer only in Distribution-specific proof metadata/reason.

GND via/layer is not a destination gate. Do not use trace/path evidence as a
Distribution proof, and do not let it move the landing XY or authorize copper.
The sole disclosed rebuild assumption is a vertical filled-Cu microvia stack at
the already source-proven PWR landing.

Do not require a destination device bump for eligibility. Use real bump distance
when present; otherwise use deterministic canonical order and add a diagnostic.

- [x] **Step 4: Run focused tests and verify GREEN**

```powershell
python -m pytest -q tests/test_spd_decap_distribution.py -k "projection or eligibility or existing_plane"
```

Expected: all selected tests pass.

### Task 2: Prove plane data is immutable

**Files:**
- Modify: `tests/test_spd_decap_distribution.py`

- [x] **Step 1: Add a plan/apply characterization test**

Capture `base_project.stackup_layers`, retained `spd_import.plane_geometries`, and
attachment hashes before projection/plan/apply. Assert they are byte-identical
after apply while only decap assignment and permitted isolation-gap state change.

- [x] **Step 2: Run the test**

```powershell
python -m pytest -q tests/test_spd_decap_distribution.py -k "plane_data_is_immutable"
```

Expected: PASS.

### Task 2A: Preserve existing dummy and shared-pad rules

GND via/layer is not a destination eligibility or component-anchor gate. Retain
the existing PWR-side topology protections: no dummy-only PWR island, no split
physical PWR via, source-proven isolation gaps only, and a compatible physical
PWR-via root in every active same-NET PWR component. This task deliberately
replaces the obsolete every-GND-component-anchor proposal.

### Task 3: Split balance strip and narrative log with TDD

**Files:**
- Modify: `tests/test_spd_decap_distribution_gui.py`
- Modify: `src/spd_decap_pi/gui/main_window.py`

- [x] **Step 1: Write failing GUI tests**

Assert the validation label is non-wrapping, contains only model identifiers plus
`Donor`, `Receiver`, and `Balance`, and contains no newline, `Valid`, `Invalid`,
`short`, `proof`, `exchange`, or filename. Assert shortage, invalid-input,
proof-pending, exchange, import, and candidate-order details appear in
`distribution_summary`.

- [x] **Step 2: Run tests and verify RED**

```powershell
python -m pytest -q tests/test_spd_decap_distribution_gui.py -k "balance_strip or narrative_log"
```

Expected: the current prose-bearing label assertions fail.

- [x] **Step 3: Add structured balance state and renderers**

Introduce internal immutable balance records with numeric donor, receiver,
balance, fixed, pending, and exchange fields. Render only the first three fields
in the label; render all explanations in the lower text box. Keep the legacy
`_distribution_numeric_state()` tuple wrapper for narrow internal/test
compatibility while main GUI paths use the structured state.

- [x] **Step 4: Run focused tests and verify GREEN**

```powershell
python -m pytest -q tests/test_spd_decap_distribution_gui.py -k "balance_strip or narrative_log or numeric_shortage or tolerance"
```

Expected: all selected tests pass.

### Task 4: Close detached-window race and lifecycle gaps

**Files:**
- Modify: `tests/test_spd_decap_distribution_gui.py`
- Modify: `tests/test_spd_decap_gui.py`
- Modify: `src/spd_decap_pi/gui/distribution_window.py`
- Modify: `src/spd_decap_pi/gui/main_window.py`

- [x] **Step 1: Write failing lifecycle tests**

Assert detached Import/Export/toggle controls disable during an active worker,
restore afterward, and the detached window closes explicitly with the main
window.

- [x] **Step 2: Run tests and verify RED**

```powershell
python -m pytest -q tests/test_spd_decap_distribution_gui.py tests/test_spd_decap_gui.py -k "detached and (busy or closes)"
```

Expected: detached controls remain enabled or the detached window stays open.

- [x] **Step 3: Implement lifecycle synchronization**

Extend `set_document_active()` to accept current busy state or add
`set_interaction_enabled()`, call it from `_sync_distribution_window()` and
`_set_busy()`, and close `_distribution_window` in `closeEvent()`.

- [x] **Step 4: Run focused tests and verify GREEN**

```powershell
python -m pytest -q tests/test_spd_decap_distribution_gui.py tests/test_spd_decap_gui.py -k "detached"
```

Expected: all selected tests pass.

### Task 5: Validate integrations and real board

**Files:**
- Modify: `README.md`
- Modify: `docs/DECAP_DISTRIBUTION_RULES.md`
- Create: `docs/DECAP_DISTRIBUTION_VALIDATION_2026-08-06.md`

- [x] **Step 1: Run focused and full automated verification**

```powershell
python -m pytest -q tests/test_spd_decap_distribution.py tests/test_spd_decap_distribution_gui.py tests/test_spd_decap_distribution_workbook.py tests/test_spd_decap_spreadsheet_export.py
python -m pytest -q
python -m compileall -q src
git diff --check
```

Expected: zero failures and zero diff whitespace errors.

- [x] **Step 2: Replay the named board and workbook**

Use `D:\S4LB002-2Para_260804_1_injected.spd` and
`D:\S4LB002-2Para_260804_1_injected_decap_distribution_01.xlsx`. Verify every
selected move has target-net copper at the immutable PWR-via landing XY, no plane
asset changes, and target fulfillment/shortfall totals reconcile.

- [ ] **Step 3: Inspect source and packaged UI**

Open the Distribution tab, exercise target edits, invalid values, import, preview,
detached window, busy controls, and Original/Distributed toggle. Confirm the app
name/version is visible and the strip/log contract renders correctly.

### Task 6: Version, package, and publish

**Files:**
- Modify: `pyproject.toml`
- Modify: `src/spd_decap_pi/version.py`
- Modify: `src/spd_decap_pi/_core/version.py`
- Modify: `packaging/spd_decap_pi.iss`
- Modify: `packaging/spd_decap_pi_version_info.txt`
- Modify: `tests/test_spd_decap_packaging.py`

- [x] **Step 1: Bump all visible/package metadata to 0.20.0**

Update title, package, installer, Windows version resource, README, and packaging
tests together.

- [ ] **Step 2: Build and smoke-test**

```powershell
powershell.exe -ExecutionPolicy Bypass -NoProfile -File .\scripts\build_spd_decap_pi_installer.ps1 -SkipTests
```

Expected: packaged EXE smoke passes and
`installer-output\SPDDecapPIEvaluatorSetup-0.20.0.exe` plus checksum are created.

- [ ] **Step 3: Commit, push, tag, and publish stable release**

Stage only the Distribution/UI/docs/version files, commit intentionally, push the
current branch, create annotated tag `v0.20.0`, and publish a non-draft,
non-prerelease GitHub release with installer and checksum.

- [ ] **Step 4: Verify remote artifacts**

Confirm branch, tag, and release commit match; download the remote installer to a
new temporary path and compare size and SHA-256 with the local artifact.
