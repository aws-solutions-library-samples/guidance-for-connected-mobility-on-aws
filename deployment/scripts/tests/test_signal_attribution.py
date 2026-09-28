# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Signal→ECU attribution and derived counts — DX26, DX28.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (T4.3, D21, F9, F13, F15, F16)

  DX26 — Model manifest `signalCount` and every `ecus[].signalCount` are DERIVED from
         a catalog query, and equal it. Guards F9's 280-vs-319 drift (D21).
  DX28 — Every signal in a model's subset maps to an ECU that model actually has:
         no orphan signals, none attributed to an absent ECU (D21).

WHY THESE DID NOT EXIST BEFORE
------------------------------
T4.5 wrote the Stage 2 red phase for DX19/22/23/24/27/29/30/31/32/33/34. DX25, DX26 and
DX28 were omitted, so T4.3's Verify named two tests that had never been written (F14).
This file is DX26 and DX28. DX25 is still unwritten and belongs to T4.4.

HOW DX26 AVOIDS BEING VACUOUS
-----------------------------
"derived == catalog" against one fixed catalog is satisfiable by a hardcoded constant
that happens to match. So DX26 has two halves: RESPONSIVENESS (feed two catalogs, the
counts must differ accordingly) and AGREEMENT (against the LIVE catalog, the derived
total must equal the live row count).

The live half matters because `deployment/scripts/signal_catalog_seed.json` is stale by
8 signals — 311 vs live 319, additive only, zero seed-only (F16). A DX26 asserting
against the snapshot would pass while being wrong: the failure class D21 exists to kill.

FIXTURES: no VINs, brand names or account IDs per C8 (public mirror). ECU names, CAN
ids and signal-group names are standard automotive vocabulary, not customer data.
"""

from __future__ import annotations

import importlib
import json
import os
import sys
from typing import Any, Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# sys.path — module under test lives in services/simulation/, so add the repo root
# and services/simulation (same dual-context approach as test_ecu_vocabulary_map.py).
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SIM_DIR = os.path.join(_REPO_ROOT, "services", "simulation")
_SNAPSHOT = os.path.join(_REPO_ROOT, "deployment", "scripts", "signal_catalog_seed.json")

for _p in (_REPO_ROOT, _SIM_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)


def _load_module(name: str) -> Optional[Any]:
    """Try importing `name`; return None if it does not exist."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_sa = _load_module("signal_attribution")
_evm = _load_module("ecu_vocabulary_map")
_MODULE_PRESENT = _sa is not None and _evm is not None

#: The universal-baseline ECU set as declared by seed_model_manifests.py:51-58.
_MANIFEST_ECUS = ["TCU", "BMS", "VCU", "BCM", "ADAS", "IVI", "GW", "CCU"]

#: The hand-written aggregates DX26 exists to retire (seed_model_manifests.py:51-60).
_HANDWRITTEN_TOTAL = 280
_HANDWRITTEN_PER_ECU_SUM = 230

_LIVE_TABLE = "cms-staging-signal-catalog"
_LIVE_REGION = "us-west-2"


# ---------------------------------------------------------------------------
# Catalog access
# ---------------------------------------------------------------------------

def _scan_live_catalog() -> Optional[List[Dict[str, object]]]:
    """Read the live staging signal catalog, or None if unreachable.

    Read-only, and paginated — a single scan page would silently truncate, which is
    exactly what made the CVX Tier 2 idempotency contract untrue.
    """
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
    except Exception:  # noqa: BLE001 — no creds, no table, no network: all "skip"
        return None


def _snapshot_catalog() -> List[Dict[str, object]]:
    """The committed catalog snapshot. Stale by 8 rows vs live (F16)."""
    with open(_SNAPSHOT, "r", encoding="utf-8") as fh:
        return json.load(fh)


#: Cache the live scan for the module's lifetime. Six tests read the catalog; without
#: this each one re-scans DynamoDB, which is slow and pointlessly chatty. Sentinel is
#: distinct from None because None is the meaningful "unreachable" result.
_LIVE_CACHE: Any = "__unset__"


def _live_catalog_cached() -> Optional[List[Dict[str, object]]]:
    """`_scan_live_catalog()` memoised for the test session."""
    global _LIVE_CACHE
    if _LIVE_CACHE == "__unset__":
        _LIVE_CACHE = _scan_live_catalog()
    return _LIVE_CACHE


def _live_or_snapshot_catalog() -> List[Dict[str, object]]:
    """Live catalog when reachable, else the snapshot, so the suite runs offline.

    Every assertion using this must hold for BOTH — so none of them may depend on the
    exact row count. Only DX26d pins a count, and it is live-only and skips.
    """
    live = _live_catalog_cached()
    return live if live is not None else _snapshot_catalog()


def _synthetic_catalog() -> List[Dict[str, object]]:
    """A small catalog exercising all three attribution tiers.

    `EngineSpeed` and `VehicleSpeed` are real DBC signal names, so they resolve via a
    transmitting node or message prefix. The `zzz_` rows are in no DBC and fall to the
    signal_group tier.
    """
    return [
        {"signal_name": "EngineSpeed", "signal_group": "powertrain"},
        {"signal_name": "VehicleSpeed", "signal_group": "core_telemetry"},
        {"signal_name": "zzz_synthetic_door", "signal_group": "doors"},
        {"signal_name": "zzz_synthetic_charge", "signal_group": "ev_charging"},
    ]


# ---------------------------------------------------------------------------
# Module presence — one explicit assertion, per the red-phase pattern.
# ---------------------------------------------------------------------------

def test_attribution_module_present() -> None:
    """signal_attribution and ecu_vocabulary_map must both import (T4.3)."""
    assert _MODULE_PRESENT, (
        "signal_attribution or ecu_vocabulary_map not found — T4.3 not yet shipped"
    )


# ---------------------------------------------------------------------------
# DX26 — counts are derived, not hand-written
# ---------------------------------------------------------------------------

def test_dx26_counts_respond_to_catalog_contents() -> None:
    """DX26a: derived counts track the catalog. A constant cannot pass this."""
    if not _MODULE_PRESENT:
        return

    full = _synthetic_catalog()
    reduced = [r for r in full if r["signal_group"] != "ev_charging"]

    counts_full = _sa.derive_counts(_sa.attribute_catalog(full), _MANIFEST_ECUS)
    counts_reduced = _sa.derive_counts(_sa.attribute_catalog(reduced), _MANIFEST_ECUS)

    assert counts_full.total_catalog_signals == len(full), (
        f"DX26a FAILED: total {counts_full.total_catalog_signals} != catalog it was "
        f"derived from ({len(full)})"
    )
    assert counts_reduced.total_catalog_signals == len(reduced), (
        f"DX26a FAILED: total not derived for the reduced catalog — "
        f"{counts_reduced.total_catalog_signals} != {len(reduced)}"
    )
    assert counts_reduced.per_ecu["CCU"] < counts_full.per_ecu["CCU"], (
        "DX26a FAILED: removing the only ev_charging signal did not reduce CCU's count "
        f"({counts_full.per_ecu['CCU']} -> {counts_reduced.per_ecu['CCU']}). A per-ECU "
        "count that does not track its catalog subset is hand-written."
    )


def test_dx26_every_declared_ecu_has_a_derived_count() -> None:
    """DX26b: per_ecu covers exactly the model's ECUs, including zero-signal ones.

    A missing key and a count of zero mean different things — "owns no signals" is a
    modelled state (cf. D18's empty sidecar sets), not an absence.
    """
    if not _MODULE_PRESENT:
        return

    counts = _sa.derive_counts(
        _sa.attribute_catalog(_synthetic_catalog()), _MANIFEST_ECUS
    )
    assert set(counts.per_ecu) == set(_MANIFEST_ECUS), (
        f"DX26b FAILED: per_ecu keys {sorted(counts.per_ecu)} != declared manifest "
        f"ECUs {sorted(_MANIFEST_ECUS)}"
    )
    assert counts.model_signal_count == sum(counts.per_ecu.values()), (
        "DX26b FAILED: model_signal_count is not the sum of per_ecu — it is carried "
        "independently, which is how the 280-vs-230 drift happened"
    )


def test_dx26_derivation_does_not_reproduce_the_handwritten_aggregates() -> None:
    """DX26c: derived counts are not the hand-written ones dressed up.

    The catalog holds 319 rows live (311 in the snapshot); the manifest asserted 280
    with a per-ECU sum of 230. Reproducing either would mean reading the manifest.
    """
    if not _MODULE_PRESENT:
        return

    counts = _sa.derive_counts(
        _sa.attribute_catalog(_live_or_snapshot_catalog()), _MANIFEST_ECUS
    )
    assert counts.total_catalog_signals != _HANDWRITTEN_TOTAL, (
        f"DX26c FAILED: derived total equals the hand-written {_HANDWRITTEN_TOTAL} — "
        "derivation is almost certainly reading the manifest, not the catalog"
    )
    assert counts.model_signal_count != _HANDWRITTEN_PER_ECU_SUM, (
        f"DX26c FAILED: derived attributable total equals the hand-written "
        f"{_HANDWRITTEN_PER_ECU_SUM} — same concern"
    )


@pytest.mark.integration
def test_dx26_derived_total_equals_live_catalog_count() -> None:
    """DX26d: against LIVE DynamoDB, every row is accounted for exactly once.

    Live rather than the snapshot deliberately (F16). Skips when the table is
    unreachable — that is an environment fact, not a defect in the derivation.
    """
    if not _MODULE_PRESENT:
        return

    rows = _live_catalog_cached()
    if rows is None:
        pytest.skip(f"live {_LIVE_TABLE} unreachable")

    counts = _sa.derive_counts(
        _sa.attribute_catalog(rows), _MANIFEST_ECUS + ["ECM"]
    )
    assert counts.total_catalog_signals == len(rows), (
        f"DX26d FAILED: derived total {counts.total_catalog_signals} != live row count "
        f"{len(rows)}"
    )
    accounted = (
        counts.model_signal_count
        + counts.unattributed
        + sum(counts.attributed_to_absent_ecu.values())
    )
    assert accounted == len(rows), (
        f"DX26d FAILED: {accounted} signals accounted for but live holds {len(rows)} — "
        "signals are being dropped or double-counted"
    )


# ---------------------------------------------------------------------------
# DX28 — no orphans, nothing attributed to an absent ECU
# ---------------------------------------------------------------------------

def test_dx28_no_attribution_targets_an_unknown_ecu() -> None:
    """DX28a: every attribution names an ECU in a known vocabulary.

    Covers both tier tables — a typo in either surfaces here rather than as a silently
    zero count downstream.
    """
    if not _MODULE_PRESENT:
        return

    valid = set(_sa.VALID_ATTRIBUTION_TARGETS)
    bad_dbc = {
        n: e for n, e in _evm.DBC_NODE_TO_MODEL_ECU.items() if e not in valid
    }
    bad_group = {
        g: e for g, e in _sa.SIGNAL_GROUP_TO_MODEL_ECU.items() if e not in valid
    }
    assert not bad_dbc, (
        f"DX28a FAILED: DBC_NODE_TO_MODEL_ECU targets unknown ECUs: {bad_dbc}. "
        f"Valid: {sorted(valid)}"
    )
    assert not bad_group, (
        f"DX28a FAILED: SIGNAL_GROUP_TO_MODEL_ECU targets unknown ECUs: {bad_group}. "
        f"Valid: {sorted(valid)}"
    )


def test_dx28_model_subset_contains_only_that_models_ecus() -> None:
    """DX28b: a model's derived subset never includes another ECU's signals.

    `ECM` is the live case: it owns signals but is absent from the current 8-ECU
    manifest until T5.2. Those must land in attributed_to_absent_ecu, NOT in the
    model's own counts — counting them for a model that cannot answer them is the F7
    superset defect one layer down.
    """
    if not _MODULE_PRESENT:
        return

    attributions = _sa.attribute_catalog(_live_or_snapshot_catalog())
    counts = _sa.derive_counts(attributions, _MANIFEST_ECUS)

    leaked = set(counts.per_ecu) - set(_MANIFEST_ECUS)
    assert not leaked, f"DX28b FAILED: subset includes non-manifest ECUs: {leaked}"

    assert "ECM" in counts.attributed_to_absent_ecu, (
        "DX28b FAILED: ECM owns signals but is absent from the 8-ECU manifest, so it "
        "must appear in attributed_to_absent_ecu. If this fails, either T5.2 landed "
        "ECM in the manifest (update _MANIFEST_ECUS) or ECM attribution broke."
    )

    with_ecm = _sa.derive_counts(attributions, _MANIFEST_ECUS + ["ECM"])
    assert not with_ecm.attributed_to_absent_ecu, (
        "DX28b FAILED: with ECM declared nothing should be attributed to an absent "
        f"ECU, but got {with_ecm.attributed_to_absent_ecu}"
    )


def test_dx28_no_orphan_signals() -> None:
    """DX28c: every catalog signal reaches an ECU — zero orphans.

    An orphan is not a crash; it is a signal that quietly belongs to no model and so
    appears in no count. That is the F9 drift mechanism, so it is asserted at zero.
    """
    if not _MODULE_PRESENT:
        return

    attributions = _sa.attribute_catalog(_live_or_snapshot_catalog())
    orphans = [a.signal_name for a in attributions if a.model_ecu is None]
    assert not orphans, (
        f"DX28c FAILED: {len(orphans)} signal(s) attributed to no ECU: "
        f"{sorted(orphans)[:10]}. Add the normalised signal_group to "
        "SIGNAL_GROUP_TO_MODEL_ECU rather than letting it fall through."
    )


def test_dx28_group_table_covers_every_group_in_the_catalog() -> None:
    """DX28d: the tier-3 table covers every normalised group in the catalog.

    Without this, a group added later is attributed only while the DBC happens to cover
    its signals, and silently becomes an orphan if the DBC changes. Two live groups
    (`ev_specific`, `gps`) were in exactly that state before T4.3.
    """
    if not _MODULE_PRESENT:
        return

    present = {
        _sa.normalise_signal_group(r.get("signal_group"))
        for r in _live_or_snapshot_catalog()
    }
    present.discard("")
    missing = sorted(present - set(_sa.SIGNAL_GROUP_TO_MODEL_ECU))
    assert not missing, (
        f"DX28d FAILED: catalog groups with no tier-3 mapping: {missing}"
    )


# ---------------------------------------------------------------------------
# Provenance and normalisation — supporting guards, not DX ids
# ---------------------------------------------------------------------------

def test_provenance_is_recorded_for_every_attribution() -> None:
    """Every attribution carries a known provenance tier (F15).

    73% of the catalog is attributed by `signal_group` judgement, not read off the bus.
    A count whose provenance is unknowable cannot be honestly quoted.
    """
    if not _MODULE_PRESENT:
        return

    attributions = _sa.attribute_catalog(_live_or_snapshot_catalog())
    known = set(_sa.PROVENANCE_RANK)
    unknown = {a.provenance for a in attributions} - known
    assert not unknown, f"unknown provenance values: {unknown}"

    breakdown = _sa.provenance_breakdown(attributions)
    assert sum(breakdown.values()) == len(attributions), (
        "provenance_breakdown does not account for every attribution"
    )
    assert breakdown[_sa.PROV_DBC_NODE] > 0, (
        "no signal resolved via a DBC transmitting node — the authoritative tier is "
        "not firing, so the DBC join is broken"
    )


def test_f13_normalisation_collapses_casing_and_synonyms() -> None:
    """F13 guard: the collisions that defeated the original DX27 must collapse.

    Pins the normaliser other tests depend on. T5.1b must import
    normalise_signal_group rather than write a second one.
    """
    if not _MODULE_PRESENT:
        return

    n = _sa.normalise_signal_group
    assert n("Chassis") == n("chassis") == "chassis"
    assert n("Emissions") == n("emissions") == "emissions"
    assert n("Engine") == "powertrain", (
        "F13: 'Engine' must normalise to 'powertrain' or combustion signals leak into "
        "a BEV profile — the exact defect DX27 could not detect"
    )
    assert n("EV") == "ev_charging"
    assert n("HVAC") == "cabin_climate"
    assert n(None) == "", "blank groups must not be guessed"
    assert n("  ") == "", "blank groups must not be guessed"


def test_can_id_join_is_preferred_and_exact() -> None:
    """`can_id` resolves against DBC message ids (the second exact join key, F15).

    Signal names get renamed; CAN ids are the wire contract. A catalog row whose name
    is unknown but whose can_id is present must still attribute.
    """
    if not _MODULE_PRESENT:
        return

    dbc = _sa.parse_dbc()
    attributed_msg_ids = [
        mid for mid, sig in dbc.by_message_id.items() if sig.has_named_transmitter
    ]
    assert attributed_msg_ids, "no DBC message has a named transmitter"

    probe = {
        "signal_name": "zzz_name_not_in_any_dbc",
        "signal_group": "",  # force the can_id path: no group to fall back on
        "can_id": hex(attributed_msg_ids[0]),
    }
    result = _sa.attribute_signal(probe, dbc)
    assert result.model_ecu is not None, (
        "can_id join failed: a row with a known can_id and no usable name or group "
        "should still attribute via the DBC message"
    )
    assert result.provenance == _sa.PROV_DBC_NODE, (
        f"can_id join should be authoritative, got provenance {result.provenance!r}"
    )
