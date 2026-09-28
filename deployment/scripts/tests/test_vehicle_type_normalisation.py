# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
`vehicleType` normalisation — T5.1a's negative control (F12).

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (T5.1a, F12, D24)

WHY THIS FILE EXISTS SEPARATELY FROM DX32
------------------------------------------
DX32 lives in `test_vehicle_brand_conversion.py` (authored by T4.5) and asserts the
*positive* property: casing variants resolve identically and non-empty. That is
necessary and it passes.

It is **not a negative control**, and the gap is narrower and more interesting than
"DX32 is weak". Measured with throwaway probes 2026-09-03:

| Probe | DX32 | This file |
|---|---|---|
| Normaliser bypassed entirely (`return raw`) | **4 fail** — caught | 4 fail — caught |
| Normaliser canonicalises to **UPPERCASE** | **6 pass — BLIND** | **3 fail — caught** |

DX32 asserts only that two casings *agree*. An uppercasing normaliser satisfies that
completely — `n('SUV') == n('suv') == 'SUV'` — while every body-style-keyed consumer
in this codebase is keyed lowercase, so all 12 uppercase-spelled rows would silently
miss. DX32 cannot see it because agreement is not the property that matters; agreement
*on the canonical form the consumers use* is.

So the counterfactual this file establishes is: an un-normalised (or wrongly-normalised)
lookup must be **demonstrated to miss rows** that a correct one finds. T5.1a's handback
claimed the positive assertions satisfied the negative-control requirement. They do not,
and the uppercase probe is the proof.

This is the same guard shape DX35 carries for `signal_group` (see
`test_signal_group_normalisation.py`), and for the same reason — F12 and F13 are one
defect at two layers. The `signal_group` layer got its negative control because the
task text demanded it explicitly; F12's layer is where the *assignment* happens
(D24 keys on body style), so a silent miss here means T5.4's backfill reports success
having assigned nothing — the exact § R9 failure mode.

FIXTURES: no VINs, brand names or account IDs (public-mirror rule C8). Body-style
words are standard automotive vocabulary.
"""

from __future__ import annotations

import importlib
import inspect
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _load(name: str) -> Optional[Any]:
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_bvm = _load("backfill_vehicle_models")
_PRESENT = _bvm is not None

_LIVE_TABLE = "cms-staging-storage-vehicles"
_LIVE_REGION = "us-west-2"

#: The 8 raw casings measured live 2026-09-03, with their row counts. Exactly the
#: state F12 describes: 4 body styles wearing 8 spellings.
_LIVE_RAW_CASINGS = {
    "Pickup": 2, "pickup": 1,
    "SUV": 7,    "suv": 2,
    "Sedan": 2,  "sedan": 4,
    "Van": 1,    "van": 2,
}

#: A body-style-keyed table written the way a careless consumer would write it —
#: canonical lowercase keys only. This stands in for D24's assignment mapping, which
#: is keyed on body style. It is the thing that silently misses.
_BODY_STYLE_CONSUMER_TABLE = {
    "pickup": "Sirocco-or-Azimuth",
    "suv": "Windrose-or-Trailwind",
    "sedan": "Crestwind-or-Mistral",
    "van": "Zephyr",
}


def test_module_present() -> None:
    """T5.1a's shared normaliser module must import."""
    assert _PRESENT, (
        "backfill_vehicle_models not found — T5.1a not shipped"
    )
    assert hasattr(_bvm, "normalise_vehicle_type"), (
        "backfill_vehicle_models has no normalise_vehicle_type()"
    )


# ---------------------------------------------------------------------------
# THE NEGATIVE CONTROL — the half T5.1a's handback claimed but did not deliver
# ---------------------------------------------------------------------------

def test_negative_control_unnormalised_lookup_misses_rows_normalised_finds() -> None:
    """An un-normalised body-style lookup MISSES rows; a normalised one finds them.

    This is the counterfactual. For each of the 4 uppercase spellings present live,
    the raw value must miss `_BODY_STYLE_CONSUMER_TABLE` and the normalised value
    must hit it. If a future edit removes the `normalise_vehicle_type()` call from
    T5.4's assignment path, this test fails loudly rather than greening.

    If this test ever passes *because the consumer table grew uppercase keys*, that
    is the defect too — the fix is to normalise at the boundary, not to enumerate
    every casing in every consumer. `test_consumer_table_stays_canonical_only`
    below guards that.
    """
    if not _PRESENT:
        return

    n = _bvm.normalise_vehicle_type
    uppercase_live = ["Pickup", "SUV", "Sedan", "Van"]

    for raw in uppercase_live:
        raw_hit = _BODY_STYLE_CONSUMER_TABLE.get(raw)
        norm_hit = _BODY_STYLE_CONSUMER_TABLE.get(n(raw))

        assert raw_hit is None, (
            f"NEGATIVE CONTROL VOID: raw {raw!r} already resolves to {raw_hit!r} "
            "without normalisation. The control cannot demonstrate anything — the "
            "consumer table has been polluted with non-canonical keys."
        )
        assert norm_hit is not None, (
            f"NEGATIVE CONTROL FAILED: normalise_vehicle_type({raw!r}) = "
            f"{n(raw)!r} does not resolve in a body-style-keyed table. The "
            "normaliser is not producing canonical values, so D24's assignment "
            "would skip every vehicle carrying this casing."
        )


def test_negative_control_quantifies_the_rows_at_risk() -> None:
    """Name the blast radius: 12 of 21 live rows would be missed un-normalised.

    A count makes the defect concrete rather than theoretical. The uppercase
    spellings carry 12 rows (Pickup 2 + SUV 7 + Sedan 2 + Van 1); those are exactly
    the vehicles D24's assignment would silently skip, and 12 of 21 is a majority of
    the assignable fleet — not an edge case.
    """
    if not _PRESENT:
        return

    n = _bvm.normalise_vehicle_type
    missed = sum(
        count for raw, count in _LIVE_RAW_CASINGS.items()
        if _BODY_STYLE_CONSUMER_TABLE.get(raw) is None
    )
    found_after = sum(
        count for raw, count in _LIVE_RAW_CASINGS.items()
        if _BODY_STYLE_CONSUMER_TABLE.get(n(raw)) is not None
    )

    assert missed == 12, (
        f"expected 12 rows missed by a raw lookup, computed {missed}. If the live "
        "casing distribution changed, re-measure and update _LIVE_RAW_CASINGS."
    )
    assert found_after == sum(_LIVE_RAW_CASINGS.values()) == 21, (
        f"normalisation must recover ALL 21 rows, recovered {found_after}"
    )


def test_consumer_table_stays_canonical_only() -> None:
    """The stand-in consumer table must hold only canonical keys.

    Guards the guard. If someone "fixes" the negative control by adding uppercase
    keys to the consumer table, the control silently stops controlling — so assert
    the table's keys are exactly the canonical set.
    """
    if not _PRESENT:
        return
    assert set(_BODY_STYLE_CONSUMER_TABLE) == set(_bvm.CANONICAL_BODY_STYLES), (
        "the stand-in consumer table drifted from CANONICAL_BODY_STYLES; the "
        "negative control is only meaningful while it holds canonical keys only"
    )


# ---------------------------------------------------------------------------
# Single-source guard — no second normaliser (C11 / F13 lesson)
# ---------------------------------------------------------------------------

def test_normaliser_is_defined_once_in_backfill_vehicle_models() -> None:
    """`normalise_vehicle_type` is defined in the shared module, not re-declared.

    F13's lesson generalised: two normalisers is the drift pattern. T5.4 consumes
    this one by import. Pinned via `inspect.getfile` so a copy-paste into another
    module is detectable.
    """
    if not _PRESENT:
        return
    where = inspect.getfile(_bvm.normalise_vehicle_type)
    assert where.endswith("backfill_vehicle_models.py"), (
        f"normalise_vehicle_type resolved to {where} — it must be the single "
        "definition in backfill_vehicle_models.py"
    )


def test_blank_and_none_are_not_guessed() -> None:
    """Blank/None normalise to '' — callers treat empty as unassignable.

    The 48 Ford vehicles have no `vehicleType`. Inferring one would assign a model
    to a cloud-telemetry vehicle, which C14 forbids.
    """
    if not _PRESENT:
        return
    n = _bvm.normalise_vehicle_type
    assert n(None) == ""
    assert n("") == ""
    assert n("   ") == ""
    assert _BODY_STYLE_CONSUMER_TABLE.get(n(None)) is None, (
        "an empty normalised body style must not resolve to a model line"
    )


# ---------------------------------------------------------------------------
# Live confirmation
# ---------------------------------------------------------------------------

def _live_vehicle_types() -> Optional[List[Dict[str, Any]]]:
    try:
        import boto3  # noqa: PLC0415
    except ModuleNotFoundError:
        return None
    try:
        table = boto3.resource("dynamodb", region_name=_LIVE_REGION).Table(_LIVE_TABLE)
        rows: List[Dict[str, Any]] = []
        kwargs: Dict[str, Any] = {"ProjectionExpression": "vehicleId, vehicleType"}
        while True:
            page = table.scan(**kwargs)
            rows.extend(page.get("Items", []))
            cursor = page.get("LastEvaluatedKey")
            if not cursor:
                return rows
            kwargs["ExclusiveStartKey"] = cursor
    except Exception:  # noqa: BLE001
        return None


@pytest.mark.integration
def test_live_every_present_vehicletype_normalises_into_the_canonical_set() -> None:
    """Live: every non-blank `vehicleType` normalises into the 4 canonical styles.

    Runs against the raw live data whether or not the one-time data fix has been
    applied, so it holds before and after. It is the assertion that would catch a
    NEW casing arriving from a seeder — the way F12 got there in the first place.
    """
    if not _PRESENT:
        return
    rows = _live_vehicle_types()
    if rows is None:
        pytest.skip(f"live {_LIVE_TABLE} unreachable")

    n = _bvm.normalise_vehicle_type
    canonical = set(_bvm.CANONICAL_BODY_STYLES)
    offenders = {}
    for row in rows:
        raw = row.get("vehicleType")
        if raw in (None, ""):
            continue
        result = n(raw)
        if result not in canonical:
            offenders[str(row.get("vehicleId"))] = (raw, result)

    assert not offenders, (
        f"live vehicleType values that do not normalise into {sorted(canonical)}: "
        f"{offenders}. A new body style needs adding to CANONICAL_BODY_STYLES and to "
        "D24's mapping, not a special case at the call site."
    )
