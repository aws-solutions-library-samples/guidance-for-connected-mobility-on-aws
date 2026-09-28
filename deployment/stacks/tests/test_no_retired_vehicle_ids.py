"""Repo-wide ratchet: a retired vehicleId must not survive anywhere in source.

WHY THIS EXISTS
---------------
On 2026-09-22 `VEH-FORD-001` was renamed to `VEH-MRDN-0015` across all 13
DynamoDB tables that referenced it, plus Cognito. The DATA migration was
complete and verified. The CODE was not: 70 references survived in 18 files,
including two seed scripts that would have recreated the old ID
(`seed_driver_users.py` carries `create=True, overwrite=True`) and a demo
storytelling override in `services/simulation/routine_sims.py` that silently
stopped firing.

The full test suite stayed green throughout, because every test passed the
retired literal DIRECTLY to the function under test instead of resolving a
vehicle from the table. Those tests proved the override *existed* while the
demo path it protected was dead — the "asserts presence, not the property"
failure in `~/.kiro/steering/testing.md`.

A per-call-site assertion cannot catch this class: the defect is the set of
places that were NOT updated, and absence is invisible to a test that names
what it checks. So the guard has to be a repo-wide scan.

See `issues/2026-09-22-vehicle-id-rename-leaves-code-references-stale/`.

HOW TO USE IT WHEN YOU RENAME A VEHICLE
---------------------------------------
1. Add the old ID to `_RETIRED_VEHICLE_IDS` with its replacement.
2. Run this test. It fails, listing every file:line still naming the old ID.
3. Fix them. The test passing is the definition of "the rename is complete".

Historical audit artifacts are allowlisted in `_ALLOWED_PATHS` — rewriting a
record of what a past migration actually did would falsify an audit trail, the
same reason the 2026-09-22 migration carried a cross-wired certificate row
forward unchanged rather than guessing at it.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# Retired identifier -> what replaced it. Add a row when you retire one.
# Covers vehicleIds, driverIds and fleetIds: the failure mode is identical
# regardless of which entity the identifier names.
_RETIRED_IDENTIFIERS: dict[str, str] = {
    "VEH-FORD-001": "VEH-MRDN-0015",
    "DRV-FORD-001": "DRV-MRDN-0015",
    # Not a rename: the FLEET#FORD-OEM enrollment row was a DUPLICATE of the
    # FLEET#MERIDIAN-OEM row for the same vehicle, so it was deleted. Renaming
    # would have collided with the row that already existed.
    "FORD-OEM": "MERIDIAN-OEM (duplicate enrollment row deleted, not renamed)",
}

# Trees permitted to keep naming a retired ID anywhere inside them. These are
# append-only historical records; editing them rewrites history.
_ALLOWED_PATHS: dict[str, str] = {
    "issues/": (
        "issue reports describe a past state on purpose; the 2026-09-22 report "
        "quotes the retired IDs throughout"
    ),
    ".kiro/specs/": (
        "spec review.md / decisions.md are append-only per spec-workflow; a "
        "spec that discusses the rename legitimately names the old ID"
    ),
    "deployment/scripts/migrate_vehicle_id.py": (
        "the migration tool documents the rename it performed in its module "
        "docstring and usage examples"
    ),
    "deployment/scripts/migrate_driver_id.py": (
        "same, for the driverId migration"
    ),
    "deployment/stacks/tests/test_no_retired_vehicle_ids.py": (
        "this guard necessarily names the IDs it guards"
    ),
}

# Individual files that legitimately retain a FIXED number of historical
# references, pinned per (file, identifier) rather than allowlisted wholesale.
#
# A whole-file allowlist would blind the guard to NEW drift in the same file —
# `docs/tech.md` is 12k lines and mixes dated audit-transcript references (which
# must survive; `:7095` preserves `vin='1FTFW1ED5MFB12345'`, the evidence for the
# cross-wired certificate row) with current-state claims that must not.
#
# Pinning is PER IDENTIFIER, and an identifier absent from a file's map is
# asserted to be ZERO there. A single per-file number would let `docs/tech.md`
# acquire a `DRV-FORD-001` reference silently, since its pin only ever spoke
# about the count it was given.
#
# Notably NOT pinned: `deployment/scripts/seed_driver_users.py`. It is the file
# that resurrects these IDs (`create=True` / `overwrite=True`), so it is held to
# zero — its 2026-09-22 decision comment deliberately describes the rename
# without naming the retired values.
_KNOWN_HISTORICAL_COUNTS: dict[str, dict[str, int]] = {
    "docs/tech.md": {
        "VEH-FORD-001": 11,
        "FORD-OEM": 6,
    },
    "docs/staging-parity-audit-2026-05-29.md": {
        "VEH-FORD-001": 5,
        "DRV-FORD-001": 4,
    },
    "deployment/scripts/rebrand-migration-records/"
    "rebrand_migration_cms-prod-storage-vehicles_20260803T160631Z.json": {
        "VEH-FORD-001": 1,
    },
    "deployment/scripts/reports/demo-fleet-reshape-plan.json": {
        "VEH-FORD-001": 1,
    },
}

# Extensions worth scanning. `.md` is included because the two stalest sites
# found on 2026-09-22 were operator-facing READMEs — `README.md` claimed the
# retired ID carried live storytelling overrides, and
# `deployment/scripts/README.md` named it as a script's current staging scope.
# Excluding markdown would have left both undetected.
_SCAN_SUFFIXES = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".java", ".json", ".yaml", ".yml", ".md",
}

# Trees that are vendored, generated, or build output.
_SKIP_DIR_PARTS = {
    "node_modules", ".venv", "sim_env", "can_env", "dist", "build",
    "cdk.out", ".build", "__pycache__", ".git", "coverage", ".pytest_cache",
    "_generated", "site-packages",
}


def _repo_root() -> Path:
    """Resolve the repo root from git rather than a hardcoded parent count.

    `Path(__file__).parents[N]` silently points at the wrong directory the
    moment this file moves, and a scan rooted at the wrong directory finds
    nothing and passes — a vacuous green, which is the exact failure mode this
    module exists to prevent.
    """
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        cwd=Path(__file__).parent,
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def _is_allowed(rel_path: str) -> bool:
    return any(rel_path.startswith(p) or p in rel_path for p in _ALLOWED_PATHS)


def _iter_source_files():
    """Yield (rel_path, text) for every scannable file in the repo."""
    root = _repo_root()
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix not in _SCAN_SUFFIXES:
            continue
        if _SKIP_DIR_PARTS & set(path.parts):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        yield str(path.relative_to(root)), text


def _scan_for(retired_id: str) -> list[str]:
    """Return "<rel_path>:<lineno>" for every occurrence that must not exist.

    Skips the prefix allowlist and the count-pinned files; the latter are
    asserted separately by `test_pinned_historical_counts_are_exact`.
    """
    hits: list[str] = []
    for rel, text in _iter_source_files():
        if _is_allowed(rel) or rel in _KNOWN_HISTORICAL_COUNTS:
            continue
        if retired_id not in text:
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            if retired_id in line:
                hits.append(f"{rel}:{i}")
    return hits


def test_repo_root_resolves_and_scan_is_not_vacuous() -> None:
    """Positive control: the scanner must be able to find a known string.

    Without this, a broken root path or an over-broad skip list would make
    every other assertion here pass by finding nothing.
    """
    root = _repo_root()
    assert (root / "deployment" / "stacks").is_dir(), (
        f"repo root resolved to {root}, which has no deployment/stacks — "
        "the scan below would be rooted in the wrong place and pass vacuously"
    )
    # The replacement ID must be findable, proving the scan reaches real source.
    found = _scan_for("VEH-MRDN-0015")
    assert len(found) > 20, (
        "scanner found <=20 references to the live ID VEH-MRDN-0015; it should "
        f"see ~70 across seeds, sims, UI data and tests. Got {len(found)}: "
        f"{found[:10]}. The scan is probably not reaching source files."
    )


@pytest.mark.parametrize(
    ("retired_id", "replacement"), sorted(_RETIRED_IDENTIFIERS.items())
)
def test_retired_identifier_absent_from_source(
    retired_id: str, replacement: str
) -> None:
    hits = _scan_for(retired_id)
    assert not hits, (
        f"{len(hits)} reference(s) to retired identifier {retired_id!r} remain "
        f"in source; it was replaced by {replacement!r}.\n"
        "A retired ID left in a seed script RESURRECTS it on the next seed "
        "run; left in behavioural code it silently stops matching.\n"
        + "\n".join(f"  {h}" for h in hits)
    )


def test_allowlist_entries_are_all_still_real() -> None:
    """Closure ratchet: an allowlist entry that no longer matches must go.

    Otherwise the allowlist grows stale and quietly widens what the guard
    ignores.
    """
    root = _repo_root()
    stale = [
        p for p in _ALLOWED_PATHS
        if not (root / p).exists() and not list(root.glob(f"{p}*"))
    ]
    assert not stale, (
        "allowlist entries no longer exist and must be deleted: "
        f"{stale}"
    )


@pytest.mark.parametrize("rel_path", sorted(_KNOWN_HISTORICAL_COUNTS))
def test_pinned_historical_counts_are_exact(rel_path: str) -> None:
    """A count-pinned file must hold EXACTLY its documented number, per ID.

    Exact, not "at most": a drop means someone edited a historical record (or
    the file moved), and a rise means new drift hid inside a file the guard was
    told to tolerate. Both need a human to re-derive the number.

    An identifier with no pin for this file is asserted to be ZERO, so a pinned
    file cannot silently acquire a reference to a DIFFERENT retired ID.
    """
    path = _repo_root() / rel_path
    assert path.is_file(), (
        f"count-pinned file {rel_path} no longer exists — remove it from "
        "_KNOWN_HISTORICAL_COUNTS or fix the path"
    )
    text = path.read_text(encoding="utf-8", errors="ignore")
    pins = _KNOWN_HISTORICAL_COUNTS[rel_path]

    for retired_id in _RETIRED_IDENTIFIERS:
        expected = pins.get(retired_id, 0)
        actual = sum(1 for ln in text.splitlines() if retired_id in ln)
        assert actual == expected, (
            f"{rel_path} holds {actual} reference(s) to {retired_id!r}, "
            f"expected exactly {expected}"
            f"{' (no pin for this ID, so zero is required)' if retired_id not in pins else ''}.\n"
            "  MORE than expected: new drift hid in a tolerated file — fix the "
            "new references, do not raise the number.\n"
            "  FEWER than expected: a historical record was edited. Confirm "
            "that was intended, then lower the number."
        )
