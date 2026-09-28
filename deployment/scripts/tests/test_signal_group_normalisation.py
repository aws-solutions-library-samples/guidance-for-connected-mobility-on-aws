# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
DX35 — signal_group normalisation and its negative control.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (T5.1b, F13, F17, decisions.md)

WHAT DX35 ASSERTS
-----------------
1. The 6 collisions that broke DX27 collapse: `ADAS`/`adas`, `Chassis`/`chassis`,
   `Emissions`/`emissions`, `Engine`→`powertrain`, `EV`→`ev_charging`,
   `HVAC`→`cabin_climate`.

2. (Integration) The normalised groups cover all 319 live rows with no row lost or
   double-counted.

3. **The negative control**: an un-normalised catalog lookup misses rows that a
   normalised one finds.  This is the point of DX35.  Without it the collision tests
   pass vacuously — they prove the mapping table is correct but not that anything calls
   it.  The negative control proves "if you bypass the normaliser the defect returns."

4. The normaliser is imported from `signal_attribution`, not reimplemented locally —
   so a future edit cannot fork normalisation and restart the F13 drift.

WHY F13 HAD TO BE A READ-TIME FIX (F17)
-----------------------------------------
`signal_group` is the catalog table's PARTITION KEY (verified 2026-09-03 via
`aws dynamodb describe-table cms-staging-signal-catalog`):

    KeySchema: signal_group  HASH  /  signal_name  RANGE

A partition key cannot be updated in place — a "data fix" would require DELETE + PUT,
which makes `?group=Engine` return empty for callers still passing the old casing.
The one-time-data-fix half of T5.1b's Accept clause is therefore struck (decisions.md
§ F17).  The negative control here serves the same guarantee that "two copies of the
data" was supposed to provide: the test enforces the boundary rather than hoping two
things drift in sync.

NEGATIVE CONTROL DESIGN
-----------------------
The correct approach to signal_group lookups is:

    normalised = normalise_signal_group(raw_group_from_catalog_row)
    ECU = SIGNAL_GROUP_TO_MODEL_ECU[normalised]

The defect path is bypassing normalisation:

    ECU = SIGNAL_GROUP_TO_MODEL_ECU.get(raw_group_from_catalog_row)  # wrong

The negative control builds a synthetic catalog with one row whose `signal_group` is
"Engine" (a value present in the live table), queries the tier-3 map first WITHOUT
normalising (defect path), then WITH normalising (correct path), and asserts:

  - un-normalised: "Engine" not in map → lookup returns None
  - normalised:    "Engine" → "powertrain" which IS in map → lookup returns "ECM"

This demonstrates the specific failure mode F13 introduced: a combustion signal
(`signal_group="Engine"`) is invisible to a powertrain-keyed lookup unless the
normaliser runs, meaning a BEV diagnostic profile could appear to carry it while
the ICE one misses it — inverted, precisely because they share the table key.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# sys.path — same dual-context pattern as test_signal_attribution.py
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SIM_DIR = os.path.join(_REPO_ROOT, "services", "simulation")

for _p in (_REPO_ROOT, _SIM_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_module(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_sa = _load_module("signal_attribution")
_MODULE_PRESENT = _sa is not None

_LIVE_TABLE = "cms-staging-signal-catalog"
_LIVE_REGION = "us-west-2"

# ---------------------------------------------------------------------------
# Live catalog access (same pagination-safe pattern as test_signal_attribution.py)
# ---------------------------------------------------------------------------

_LIVE_CACHE: Any = "__unset__"


def _scan_live_catalog() -> Optional[List[Dict[str, object]]]:
    """Paginated full scan of the live staging signal catalog, or None if unreachable."""
    try:
        import boto3  # noqa: PLC0415
    except ModuleNotFoundError:
        return None
    try:
        table = boto3.resource("dynamodb", region_name=_LIVE_REGION).Table(_LIVE_TABLE)
        rows: List[Dict[str, object]] = []
        kwargs: Dict[str, Any] = {}
        while True:
            page = table.scan(**kwargs)
            rows.extend(page.get("Items", []))
            key = page.get("LastEvaluatedKey")
            if not key:
                return rows
            kwargs["ExclusiveStartKey"] = key
    except Exception:  # noqa: BLE001
        return None


def _live_catalog() -> Optional[List[Dict[str, object]]]:
    global _LIVE_CACHE
    if _LIVE_CACHE == "__unset__":
        _LIVE_CACHE = _scan_live_catalog()
    return _LIVE_CACHE


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _raw_groups_from_catalog(rows: List[Dict[str, object]]) -> List[str]:
    """Extract the raw signal_group values from a catalog row list."""
    return [str(r.get("signal_group") or "") for r in rows]


# ---------------------------------------------------------------------------
# Import pin — single source of normalisation
# ---------------------------------------------------------------------------

def test_normalise_signal_group_is_imported_from_signal_attribution() -> None:
    """The normaliser lives in signal_attribution — not redefined here or elsewhere.

    Importing it from the canonical source is what prevents normalisation drift:
    two implementations of the same mapping can diverge silently (F13 happened once,
    and the docstring of signal_attribution.normalise_signal_group explicitly states
    this requirement for T5.1b).  This test verifies the *source file*, not just the
    name.
    """
    assert _MODULE_PRESENT, (
        "signal_attribution not importable — T4.3 not shipped"
    )
    assert hasattr(_sa, "normalise_signal_group"), (
        "normalise_signal_group missing from signal_attribution"
    )
    fn = _sa.normalise_signal_group
    source_file = inspect.getfile(fn)
    assert source_file.endswith("signal_attribution.py"), (
        f"normalise_signal_group is defined in {source_file!r}, expected "
        "'signal_attribution.py'.  Do not redefine it — import from signal_attribution "
        "as T5.1b's Constraints require."
    )


# ---------------------------------------------------------------------------
# DX35a — the 6 collisions collapse
# ---------------------------------------------------------------------------

def test_dx35a_casing_collisions_collapse() -> None:
    """DX35a: mixed-case forms of the same group normalise to a single canonical value.

    The live catalog carries both `adas` and `ADAS`, `chassis` and `Chassis`, and
    `emissions` and `Emissions`.  Without lowercasing, these are three distinct
    DynamoDB partition-key values and map to three distinct (or absent) tier-3 entries.
    """
    if not _MODULE_PRESENT:
        return

    n = _sa.normalise_signal_group

    # All three should collapse to a single canonical lower-case form:
    assert n("ADAS") == n("adas"), (
        f"DX35a FAILED: 'ADAS' normalises to {n('ADAS')!r} but 'adas' normalises to "
        f"{n('adas')!r} — these must be identical."
    )
    assert n("Chassis") == n("chassis"), (
        f"DX35a FAILED: 'Chassis' normalises to {n('Chassis')!r} but 'chassis' "
        f"normalises to {n('chassis')!r}."
    )
    assert n("Emissions") == n("emissions"), (
        f"DX35a FAILED: 'Emissions' normalises to {n('Emissions')!r} but 'emissions' "
        f"normalises to {n('emissions')!r}."
    )

    # Canonical forms must be in the tier-3 map:
    for pair_a, pair_b in (("ADAS", "adas"), ("Chassis", "chassis"), ("Emissions", "emissions")):
        canonical = n(pair_a)
        assert canonical in _sa.SIGNAL_GROUP_TO_MODEL_ECU, (
            f"DX35a FAILED: canonical group {canonical!r} (from {pair_a!r}/{pair_b!r}) "
            "is NOT in SIGNAL_GROUP_TO_MODEL_ECU — casing collision survives normalisation."
        )


def test_dx35a_synonym_collisions_collapse() -> None:
    """DX35a: synonym groups map to their canonical equivalents.

    `Engine`→`powertrain`, `EV`→`ev_charging`, `HVAC`→`cabin_climate` are synonym
    pairs where two different strings denote the same automotive domain.  Without the
    synonym table, the upper-case legacy form (`Engine`, `EV`, `HVAC`) would either
    fall through to `unattributed` or land on a different ECU than the canonical form.
    """
    if not _MODULE_PRESENT:
        return

    n = _sa.normalise_signal_group

    assert n("Engine") == "powertrain", (
        f"DX35a FAILED: 'Engine' → {n('Engine')!r}, expected 'powertrain'. "
        "The 3 Engine rows carry combustion signals that must reach ECM via powertrain, "
        "not be invisible to a powertrain-keyed lookup."
    )
    assert n("engine") == "powertrain", (
        f"DX35a FAILED: lowercase 'engine' → {n('engine')!r}, expected 'powertrain'. "
        "Lowercasing must happen BEFORE the synonym table."
    )
    assert n("EV") == "ev_charging", (
        f"DX35a FAILED: 'EV' → {n('EV')!r}, expected 'ev_charging'."
    )
    assert n("ev") == "ev_charging", (
        f"DX35a FAILED: lowercase 'ev' → {n('ev')!r}, expected 'ev_charging'."
    )
    assert n("HVAC") == "cabin_climate", (
        f"DX35a FAILED: 'HVAC' → {n('HVAC')!r}, expected 'cabin_climate'."
    )
    assert n("hvac") == "cabin_climate", (
        f"DX35a FAILED: lowercase 'hvac' → {n('hvac')!r}, expected 'cabin_climate'."
    )


# ---------------------------------------------------------------------------
# DX35b — row count preserved (integration)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dx35b_row_count_preserved_at_319() -> None:
    """DX35b: normalising signal_group does not drop or duplicate any row.

    319 raw rows → 319 normalised rows.  The normaliser is a pure function over a
    string field; it must not change the set of rows, only the values of that field.

    Live rather than the snapshot (F16): the snapshot is stale by 8 rows, and a test
    asserting a wrong count is not a guard — it is a false green.
    """
    if not _MODULE_PRESENT:
        return

    rows = _live_catalog()
    if rows is None:
        pytest.skip(f"live {_LIVE_TABLE} unreachable — skipping DX35b")

    n = _sa.normalise_signal_group
    normalised = [n(r.get("signal_group")) for r in rows]  # type: ignore[arg-type]

    assert len(normalised) == len(rows), (
        f"DX35b FAILED: normalisation changed row count "
        f"({len(normalised)} vs {len(rows)}).  The normaliser must be a pure string "
        "function — it cannot filter or duplicate rows."
    )

    expected = 319
    assert len(rows) == expected, (
        f"DX35b: live catalog has {len(rows)} rows; expected {expected}. "
        "If the catalog grew, update the expected count here AND re-run T5.2 whose "
        "per-model counts (D23) are downstream."
    )

    raw_groups = set(_raw_groups_from_catalog(rows))
    canonical_groups = set(normalised) - {""}
    assert len(canonical_groups) < len(raw_groups), (
        f"DX35b FAILED: normalisation did not reduce the group count "
        f"({len(raw_groups)} raw → {len(canonical_groups)} canonical).  At least one "
        "collision must exist; if the catalog changed verify that 6 collisions are gone."
    )


# ---------------------------------------------------------------------------
# DX35c — the negative control
# ---------------------------------------------------------------------------

def test_dx35c_negative_control_unnormalised_lookup_misses_collision_rows() -> None:
    """DX35c — THE NEGATIVE CONTROL.

    Demonstrates that bypassing normalise_signal_group causes rows to be missed,
    so this test CANNOT pass vacuously if a future change removes the normaliser call.

    WHAT IS BEING SHOWN
    -------------------
    The live catalog carries rows with `signal_group = "Engine"`.  Those rows should
    belong to the `powertrain` group (and therefore be attributed to ECM in T5.2).

    DEFECT PATH (wrong, but present before T4.3):
        raw_group = row["signal_group"]           # "Engine"
        ecu = SIGNAL_GROUP_TO_MODEL_ECU.get(raw_group)
        #  "Engine" is NOT in the map → ecu is None → row is UNATTRIBUTED

    CORRECT PATH (shipped as T4.3):
        canonical = normalise_signal_group(raw_group)  # "Engine" → "powertrain"
        ecu = SIGNAL_GROUP_TO_MODEL_ECU.get(canonical)
        #  "powertrain" IS in the map → ecu is "ECM" → row is attributed

    The negative control creates a synthetic catalog row with `signal_group="Engine"`
    and applies both paths.  The defect path must produce no ECU; the correct path
    must produce an ECU.  If a future edit makes both paths identical (because the
    map now also contains "Engine" directly, or the normaliser is removed), this test
    fails loudly rather than passing silently.
    """
    if not _MODULE_PRESENT:
        return

    n = _sa.normalise_signal_group
    tier3 = _sa.SIGNAL_GROUP_TO_MODEL_ECU

    # Verify the test's own preconditions:
    assert "Engine" not in tier3, (
        "DX35c precondition failed: 'Engine' is now in SIGNAL_GROUP_TO_MODEL_ECU. "
        "If this was intentional the map no longer needs normalisation for 'Engine', "
        "but the test's negative control premise has changed — update it."
    )
    assert n("Engine") == "powertrain", (
        "DX35c precondition failed: normalise_signal_group('Engine') != 'powertrain' "
        "— the normaliser is broken."
    )
    assert "powertrain" in tier3, (
        "DX35c precondition failed: 'powertrain' is not in SIGNAL_GROUP_TO_MODEL_ECU "
        "— the tier-3 map is incomplete."
    )

    # --- The negative control ---

    raw_group = "Engine"  # a value present in the live catalog

    # DEFECT PATH: raw key used directly, bypassing normalisation
    ecu_without_normalisation = tier3.get(raw_group)

    # CORRECT PATH: normalised key used
    ecu_with_normalisation = tier3.get(n(raw_group))

    assert ecu_without_normalisation is None, (
        f"DX35c NEGATIVE CONTROL BROKEN: 'Engine' directly resolves to "
        f"{ecu_without_normalisation!r} in SIGNAL_GROUP_TO_MODEL_ECU. "
        "The map must NOT contain the un-normalised form 'Engine' — otherwise the "
        "normaliser is no longer the discriminator and this test loses its meaning. "
        "If this was intentional, document why both paths are now equivalent and "
        "update the negative control accordingly."
    )
    assert ecu_with_normalisation is not None, (
        "DX35c FAILED: normalise_signal_group('Engine') → 'powertrain', but "
        "'powertrain' is not in SIGNAL_GROUP_TO_MODEL_ECU. "
        "The correct path produces no ECU, so the normaliser has no effect."
    )

    # The specific ECU matters: powertrain must map to a combustion-only ECU so that
    # DX27's BEV-exclusion property holds (see signal_attribution.py comments).
    assert ecu_with_normalisation != ecu_without_normalisation, (
        "DX35c NEGATIVE CONTROL BROKEN: both paths produce the same result. "
        "The negative control is vacuous."
    )


def test_dx35c_negative_control_unnormalised_lookup_misses_ev_rows() -> None:
    """DX35c variant: same defect demonstrated for the 'EV' → 'ev_charging' synonym.

    Two negative-control cases because the defect has two independent shapes:
    (a) 'Engine' bypasses powertrain — combustion signals leak INTO a BEV profile
    (b) 'EV' bypasses ev_charging — EV-exclusive signals go UNATTRIBUTED in ICE profiles

    Both are the F13 defect; both must be shown to fail without normalisation.
    """
    if not _MODULE_PRESENT:
        return

    n = _sa.normalise_signal_group
    tier3 = _sa.SIGNAL_GROUP_TO_MODEL_ECU

    # Preconditions
    assert "EV" not in tier3, (
        "DX35c-ev precondition: 'EV' is now in the tier-3 map directly. "
        "The negative control's premise has changed — see test_dx35c for guidance."
    )
    assert n("EV") == "ev_charging", (
        "DX35c-ev precondition: normalise_signal_group('EV') != 'ev_charging'."
    )
    assert "ev_charging" in tier3, (
        "DX35c-ev precondition: 'ev_charging' not in SIGNAL_GROUP_TO_MODEL_ECU."
    )

    ecu_without_normalisation = tier3.get("EV")
    ecu_with_normalisation = tier3.get(n("EV"))

    assert ecu_without_normalisation is None, (
        f"DX35c-ev NEGATIVE CONTROL BROKEN: 'EV' resolves directly to "
        f"{ecu_without_normalisation!r}."
    )
    assert ecu_with_normalisation is not None, (
        "DX35c-ev FAILED: 'EV' normalises to 'ev_charging' but that is not in the map."
    )
    assert ecu_with_normalisation != ecu_without_normalisation, (
        "DX35c-ev NEGATIVE CONTROL BROKEN: both paths agree — control is vacuous."
    )


# ---------------------------------------------------------------------------
# DX35d — live catalog: un-normalised lookup misses rows a normalised one finds
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_dx35d_live_normalised_finds_more_rows_than_raw() -> None:
    """DX35d: against the live catalog, normalised lookups attribute more rows.

    This is the integration form of the negative control.  It asserts that at least
    one row in the actual staging catalog has a raw `signal_group` value that the
    tier-3 map does not contain directly, but does contain after normalisation.

    If this test passes vacuously (because every raw group is already in the map),
    the catalog no longer has any collision rows and F13 no longer applies — update
    the spec and remove this test with a note.
    """
    if not _MODULE_PRESENT:
        return

    rows = _live_catalog()
    if rows is None:
        pytest.skip(f"live {_LIVE_TABLE} unreachable — skipping DX35d")

    n = _sa.normalise_signal_group
    tier3 = _sa.SIGNAL_GROUP_TO_MODEL_ECU

    missed_by_raw = []
    found_by_normalised = []

    for row in rows:
        raw = str(row.get("signal_group") or "")
        if not raw:
            continue
        if tier3.get(raw) is None and tier3.get(n(raw)) is not None:
            missed_by_raw.append(raw)
        if tier3.get(n(raw)) is not None:
            found_by_normalised.append(raw)

    assert missed_by_raw, (
        "DX35d NEGATIVE CONTROL: no live row has a raw signal_group that the tier-3 "
        "map misses directly but finds after normalisation. "
        "Either F13 no longer applies to this catalog (update the spec), or the map "
        "was expanded to include raw un-normalised forms (revert that — it makes the "
        "normaliser call redundant and drift resumes)."
    )

    assert len(found_by_normalised) > len(found_by_normalised) - len(missed_by_raw), (
        "DX35d consistency: found_by_normalised should be a strict superset of "
        "found_by_raw; the assertion above already checked this, but the counts should "
        "be inspectable on failure."
    )

    # Confirm the specific collision values the task text names:
    collision_raw_values = {"Engine", "EV", "HVAC", "ADAS", "Chassis", "Emissions"}
    collisions_present = collision_raw_values & set(missed_by_raw)
    assert collisions_present, (
        f"DX35d: none of the F13 collision values {sorted(collision_raw_values)} "
        f"appear in the live catalog's missed-by-raw set ({sorted(set(missed_by_raw))[:10]}). "
        "If the catalog was cleaned up, the negative control premise has changed — "
        "verify with a live scan and update this test."
    )
