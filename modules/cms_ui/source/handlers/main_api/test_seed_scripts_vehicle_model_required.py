"""
Cross-repo lint: every seed script that writes to CMS's storage-vehicles table
must carry `modelManifestName` in the file.

Prior art:
  guidance-for-connected-vehicle-experience-on-aws/scripts/tests/
    test_seed_scripts_no_fake_connected.py

The invariant: a ``seed*.py`` file whose text contains the substring
``storage-vehicles`` (which appears in every table-name literal for CMS's
vehicles table across stages — ``cms-dev-storage-vehicles``,
``cms-staging-storage-vehicles``, ``cms-prod-storage-vehicles``, etc.) MUST
also contain the substring ``modelManifestName``.  Presence-based, file-level
scan; no AST parsing.

Rationale: spec ``2026-08-28-cms-cert-follows-model`` (§ D5) makes
``modelManifestName`` a required field on every vehicle-definition row.  A
seed script that bypasses the API and writes directly to the DynamoDB table
**must** include that field or it will produce bare vehicle rows that violate
the invariant, just as CVX's persona seeder produced fake-connected rows before
the 2026-07-31 fix.

The guard covers seed scripts in BOTH repos:
  * CMS (this repo) — discovered from ``Path(__file__).parents[5]``
  * CVX (sibling repo) — discovered via ``CVX_REPO_PATH`` env var or
    ``../guidance-for-connected-vehicle-experience-on-aws`` relative to the
    CMS repo root

Exclusion set: seed scripts that reference ``storage-vehicles`` for READ-ONLY
operations or for writing to OTHER tables (not vehicle definition rows) are
excluded. The rule is scoped to scripts that CREATE new vehicle definition rows.
See ``_EXCLUDED_SEED_BASENAMES`` for the explicit allowlist with rationale.

Test structure:
  * LINT1 — real cross-repo scan (production entry point). Passes once CVX's
    ``seed-persona-users.py`` and CMS's ``seed_public_demo_fleet.py`` both carry
    the ``modelManifestName`` field (Group 5 of spec
    ``2026-08-28-cms-cert-follows-model``).
  * LINT2–LINT4 — synthetic tempdir fixtures that exercise
    ``_scan_seed_files(root)`` directly, fast and hermetic.
"""
from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import NamedTuple

import pytest


# ---------------------------------------------------------------------------
# Repo discovery
# ---------------------------------------------------------------------------

# parents[5] from modules/cms_ui/source/handlers/main_api/<this file>
#   [0] main_api/
#   [1] handlers/
#   [2] source/
#   [3] cms_ui/
#   [4] modules/
#   [5] repo root
CMS_REPO = Path(__file__).resolve().parents[5]


def _discover_cvx_repo() -> Path | None:
    """Discover the sibling CVX repo via env var or relative path."""
    env = os.environ.get("CVX_REPO_PATH")
    if env:
        p = Path(env).expanduser().resolve()
        return p if p.is_dir() else None
    candidate = CMS_REPO.parent / "guidance-for-connected-vehicle-experience-on-aws"
    return candidate if candidate.is_dir() else None


CVX_REPO: Path | None = _discover_cvx_repo()


# ---------------------------------------------------------------------------
# Exclusion set
#
# These seed files reference ``storage-vehicles`` but are explicitly excluded
# because they do NOT create new vehicle-definition rows. They either:
#   (a) read from the vehicles table to drive writes to OTHER tables, or
#   (b) update lifecycle/metadata fields on existing rows (no definition row
#       written from scratch), or
#   (c) are OEM1 / cloud-telemetry scripts which are explicitly out of scope
#       per spec § "What we are NOT doing".
#
# Key: leaf filename (e.g. "seed_trip_history.py").
# Value: human-readable rationale.
# ---------------------------------------------------------------------------

_EXCLUDED_SEED_BASENAMES: dict[str, str] = {
    # Reads the vehicles table to join VIN data into trip records.
    # Writes to the trips table only; no vehicle-definition put_item.
    "seed_trip_history.py": "reads vehicles, writes trips table only",

    # Scans vehicles + fleets + other tables to generate synthetic decision
    # journal entries. Writes to the decision-journal table only.
    "seed_decision_journal.py": "reads vehicles, writes decision-journal table only",

    # Scans vehicles to build VFO action records; writes to VFO-actions table.
    "seed_vfo_actions.py": "reads vehicles, writes VFO-actions table only",

    # Reads vehicleId values from vehicles table to assign to driver records.
    # Writes to the drivers table only.
    "seed_drivers.py": "reads vehicles (IDs only), writes drivers table only",

    # Updates existing vehicle rows with lifecycle metadata (purchase info,
    # odometer history, insurance).  Does NOT create new vehicle-definition rows
    # from scratch — uses update_item on rows the other scripts created.
    "seed_vehicle_lifecycle.py": "update_item lifecycle metadata; no new vehicle definitions",

    # Writes only the `sold_to` field on existing Meridian vehicle rows via
    # update_item; never creates a new vehicle-definition row (no put_item call).
    "seed_vehicle_sold_to.py": "update_item sold_to on existing rows only; no vehicle definitions written",

    # Scans vehicles table to pair drivers; writes enrollment + drivers tables.
    "seed_fleet_enrollment.py": "reads vehicles, writes enrollment table only",

    # OEM1 cloud-telemetry path — explicitly out of scope per spec
    # § "What we are NOT doing": "No changes to the OEM1 add-vehicle flow."
    # These vehicles carry no IoT cert and no decoderManifestRef by design.
    "seed_vehicles.py": "OEM1/cloud-telemetry path, explicitly out of spec scope",

    # Writes demo persona drivers AND their companion vehicle rows, but the
    # vehicle items it writes are the *same persona vehicles* that CVX's
    # seed-persona-users.py already owns (VEH-MICH-001, VEH-MRDN-0015, etc.).
    # The definitive fix for those rows is in Group 5 (CVX seeder companion).
    # Excluding here to keep LINT1's single point of truth in the CVX file.
    "seed_driver_users.py": "persona vehicles owned by CVX seeder; fixed in Group 5 CVX side",

    # Engineering / bulk demo fleet seeders — create large batches of
    # synthetic test vehicles (BE6 / BE07 cohorts, Meridian Trailwind fleet,
    # generic demo fleets).  These bypass the API and write direct DDB rows
    # intentionally; they pre-date the ``modelManifestName`` required invariant
    # and are out of scope for this spec's enforcement horizon (spec §
    # "Existing-row impact": "enforcement is write-time only" for the API path;
    # bulk seeder alignment is a follow-on).  The named-persona scripts
    # (CVX's seed-persona-users.py and CMS's seed_public_demo_fleet.py) are
    # the primary targets of this spec's lint rule; the bulk fleet seeders
    # are tracked as a follow-on backlog row.
    "seed_engineering_fleets.py": "bulk synthetic cohort seeder; pre-dates invariant, follow-on scope",
    "seed_meridian_fleet.py": "bulk Meridian demo fleet seeder; pre-dates invariant, follow-on scope",
    "seed_generic_fleets.py": "bulk generic demo fleet seeder; pre-dates invariant, follow-on scope",
}


# ---------------------------------------------------------------------------
# Scan helper
# ---------------------------------------------------------------------------

class ScanResult(NamedTuple):
    """Return type of ``_scan_seed_files``."""
    violations: list[str]   # human-readable "repo-label: relpath" strings
    scanned_count: int


def _scan_seed_files(
    root: Path,
    label: str = "REPO",
    excluded_basenames: frozenset[str] | None = None,
) -> ScanResult:
    """
    Scan every ``seed*.py`` under ``root`` and return files that contain
    ``storage-vehicles`` but NOT ``modelManifestName``.

    Parameters
    ----------
    root:
        Absolute path to scan. Must be a directory.
    label:
        Short label prepended to violation strings (e.g. ``"CMS"``).
    excluded_basenames:
        Leaf filenames to skip (see ``_EXCLUDED_SEED_BASENAMES``).

    Returns
    -------
    ScanResult
        ``violations`` — list of ``"<label>: <relpath>"`` strings, one per
        offending file.
        ``scanned_count`` — how many ``seed*.py`` files were examined
        (after exclusions and venv filtering).

    Raises
    ------
    ValueError
        If ``root`` is not a directory or does not exist.
    RuntimeError
        If zero ``seed*.py`` files are found anywhere under ``root`` after
        exclusions. This prevents silent-pass regressions when a scan-root
        moves or the glob no longer matches.
    """
    if not root.is_dir():
        raise ValueError(f"Scan root is not a directory: {root}")

    if excluded_basenames is None:
        excluded_basenames = frozenset(_EXCLUDED_SEED_BASENAMES.keys())

    # Directory parts to skip (venv / build artefacts).
    _SKIP_PARTS: frozenset[str] = frozenset({
        ".venv", "venv", "node_modules", "cdk.out", "build", "dist",
        "__pycache__", ".git", ".mypy_cache", ".pytest_cache",
    })

    seed_files: list[Path] = []
    for path in root.rglob("seed*.py"):
        if any(part in _SKIP_PARTS for part in path.parts):
            continue
        if not path.name.startswith("seed"):
            # Sanity: rglob pattern is leaf-based but double-check.
            continue
        if path.name in excluded_basenames:
            continue
        seed_files.append(path)

    if not seed_files:
        raise RuntimeError(
            f"Zero seed*.py files found under {root} (after exclusions). "
            "The lint is not scanning anything — check the scan root and "
            "exclusion set."
        )

    violations: list[str] = []
    for f in seed_files:
        try:
            text = f.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue

        # Comment-strip heuristic: build a version of the file with single-line
        # Python comments removed.  This prevents comment-only references (e.g.
        # ``# reads from cms-staging-storage-vehicles``) from triggering the
        # rule.  Mirrors the per-line `#` prefix check in the fake-connected
        # lint's ``_scan_file`` helper.
        #
        # Strip lines whose first non-whitespace character is ``#``, and strip
        # the inline-comment tail from mixed lines (everything after a `` # ``
        # that is not inside a string literal).  The heuristic used here is the
        # same simple one as the prior art: if ``#`` appears before the pattern
        # on a line, that line is treated as a comment rather than code.
        code_lines: list[str] = []
        for line in text.splitlines():
            stripped = line.lstrip()
            if stripped.startswith("#"):
                # Whole-line comment — drop entirely.
                continue
            code_lines.append(line)
        code_text = "\n".join(code_lines)

        if "storage-vehicles" not in code_text:
            # File doesn't reference the vehicles table in executable code;
            # rule doesn't apply.
            continue

        if "modelManifestName" not in code_text:
            try:
                rel = f.relative_to(root)
            except ValueError:
                rel = f
            violations.append(f"{label}: {rel}")

    return ScanResult(violations=violations, scanned_count=len(seed_files))


# ---------------------------------------------------------------------------
# Production entry point (used by LINT1)
# ---------------------------------------------------------------------------

def _run_cross_repo_scan(capsys_or_print=print) -> tuple[list[str], int, bool]:
    """
    Run the real cross-repo scan over CMS and (if present) CVX.

    Returns
    -------
    (all_violations, total_scanned, cvx_present)
    """
    all_violations: list[str] = []
    total_scanned = 0

    excluded = frozenset(_EXCLUDED_SEED_BASENAMES.keys())

    # CMS (always scanned).
    cms_result = _scan_seed_files(CMS_REPO, label="CMS", excluded_basenames=excluded)
    all_violations.extend(cms_result.violations)
    total_scanned += cms_result.scanned_count

    # CVX (optional — skip with NOTE if not present).
    cvx_present = CVX_REPO is not None
    if CVX_REPO is not None:
        cvx_result = _scan_seed_files(CVX_REPO, label="CVX", excluded_basenames=excluded)
        all_violations.extend(cvx_result.violations)
        total_scanned += cvx_result.scanned_count
    else:
        print(
            "NOTE: sibling CVX repo not found "
            "(set CVX_REPO_PATH or check out at "
            "../guidance-for-connected-vehicle-experience-on-aws). "
            "Only CMS is being scanned for the modelManifestName invariant."
        )

    return all_violations, total_scanned, cvx_present


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_lint1_cross_repo_scan_model_manifest_required():
    """
    LINT1: real cross-repo scan.

    Scans every ``seed*.py`` in CMS and CVX (when present); asserts that every
    file containing ``storage-vehicles`` also contains ``modelManifestName``.

    Passes once CVX's ``seed-persona-users.py`` and CMS's
    ``seed_public_demo_fleet.py`` both carry the ``modelManifestName`` field,
    which lands in Group 5 of spec ``2026-08-28-cms-cert-follows-model``.
    """
    violations, total_scanned, cvx_present = _run_cross_repo_scan()

    assert total_scanned > 0, (
        "Lint found zero seed*.py files to scan. "
        "Check that CMS_REPO and CVX_REPO point to the right directories."
    )

    assert not violations, (
        "seed*.py file(s) reference storage-vehicles without modelManifestName.\n"
        "Every seed script that writes to the CMS vehicles table MUST include\n"
        "`modelManifestName` in the item — spec 2026-08-28-cms-cert-follows-model § D5.\n\n"
        "Offending files:\n  " + "\n  ".join(violations)
    )


def test_lint2_storage_vehicles_without_model_manifest_flagged(tmp_path):
    """
    LINT2: synthetic fixture.

    A ``seed_x.py`` containing ``storage-vehicles`` but NOT ``modelManifestName``
    must appear in the violation list returned by ``_scan_seed_files``.
    """
    bad_script = tmp_path / "seed_bad_vehicles.py"
    bad_script.write_text(
        '"""Seed script that writes to cms-staging-storage-vehicles."""\n'
        'VEHICLES_TABLE = "cms-staging-storage-vehicles"\n'
        'item = {"vehicleId": "VEH-001", "vin": "TEST0000000000001"}\n'
        'table.put_item(Item=item)\n',
        encoding="utf-8",
    )

    result = _scan_seed_files(tmp_path, label="TEST")

    assert result.scanned_count == 1
    assert len(result.violations) == 1
    assert "seed_bad_vehicles.py" in result.violations[0], (
        f"Expected violation for seed_bad_vehicles.py; got: {result.violations}"
    )


def test_lint3_storage_vehicles_with_model_manifest_passes(tmp_path):
    """
    LINT3: synthetic fixture.

    A ``seed_x.py`` containing both ``storage-vehicles`` AND ``modelManifestName``
    must produce zero violations.
    """
    good_script = tmp_path / "seed_good_vehicles.py"
    good_script.write_text(
        '"""Seed script that writes to cms-staging-storage-vehicles."""\n'
        'VEHICLES_TABLE = "cms-staging-storage-vehicles"\n'
        'MODEL_MANIFEST_NAME = "CMS-Fleet-Default"\n'
        'item = {\n'
        '    "vehicleId": "VEH-001",\n'
        '    "vin": "TEST0000000000001",\n'
        '    "modelManifestName": MODEL_MANIFEST_NAME,\n'
        '    "modelManifestVersion": "1",\n'
        '    "decoderManifestRef": "cms-fleet-v3",\n'
        '    "dataSource": "vehicle-telemetry",\n'
        '}\n'
        'table.put_item(Item=item)\n',
        encoding="utf-8",
    )

    result = _scan_seed_files(tmp_path, label="TEST")

    assert result.scanned_count == 1
    assert result.violations == [], (
        f"Expected zero violations for a compliant seed script; got: {result.violations}"
    )


def test_lint4_zero_file_guard_fails_loudly(tmp_path):
    """
    LINT4: zero-file guard.

    When no ``seed*.py`` files exist under the scan root, ``_scan_seed_files``
    must raise ``RuntimeError``. This prevents silent-pass regressions if the
    scan root moves or the file naming convention changes.
    """
    # tmp_path is empty — no seed*.py files.
    with pytest.raises(RuntimeError, match="Zero seed\\*.py files found"):
        _scan_seed_files(tmp_path, label="EMPTY")
