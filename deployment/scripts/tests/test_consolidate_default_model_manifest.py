# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Consolidate default model manifest — T4.4, DX25.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (T4.4, D19)

  DX25 — `CMS-Fleet-Default` is retired and no vehicle references it
         post-consolidation. Guards D19's migration.

TWO KINDS OF ASSERTION IN THIS FILE
-----------------------------------
1. Module + dry-run correctness — always run. These fail on any local
   defect in the consolidation script itself (import errors, wrong table
   names, wrong exit codes, misdirected DDB calls).

2. **DX25 live guard** — `@pytest.mark.integration`, keyed to `cms-staging-
   model-manifest` + `cms-staging-storage-vehicles`. Expected to **FAIL**
   until T4.4 `--apply` has been run against staging. Once green, it
   guards against re-introduction. Skips when the tables are unreachable
   (offline, no creds) — that is an environment fact, not a defect.

T4.5 authored the Stage 2 red phase for DX19/22/23/24/27/29/30/31/32/33/34.
DX25/26/28 were deferred; T4.3 wrote DX26 and DX28. This file completes the
set at DX25, which is intrinsically a live-behaviour assertion — a module-
absence test would go green the moment the script exists, before the
migration has been run, and DX25's point is the migration.

FIXTURES: no VINs, brand names or account IDs (public-mirror rule).
"""

from __future__ import annotations

import importlib
import os
import sys
from typing import Any, Optional
from unittest.mock import MagicMock

import pytest

# ── sys.path setup: the script lives in deployment/scripts/ ─────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)


def _load(name: str) -> Optional[Any]:
    """Try importing `name` from deployment/scripts; return None if missing."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_MOD = _load("consolidate_default_model_manifest")
_MODULE_PRESENT = _MOD is not None

_STAGING_REGION = "us-west-2"
_STAGING_VEHICLES = "cms-staging-storage-vehicles"
_STAGING_MANIFESTS = "cms-staging-model-manifest"

_OLD_NAME = "CMS-Fleet-Default"
_OLD_PK = "MODEL#CMS-Fleet-Default#1"
_OLD_SK = "MODEL#CMS-Fleet-Default"
_NEW_NAME = "CMS-FLEET-MODEL"


# ---------------------------------------------------------------------------
# Module presence — one explicit assertion (T4.4)
# ---------------------------------------------------------------------------

def test_t44_module_present() -> None:
    """consolidate_default_model_manifest must import (T4.4)."""
    assert _MODULE_PRESENT, (
        "consolidate_default_model_manifest not found — T4.4 not yet shipped"
    )


def test_t44_names_match_the_D19_migration() -> None:
    """Both manifest names are pinned as constants, not passed in.

    D19 is a one-off migration; letting the source/target manifest names be
    caller-supplied would let a follow-on script quietly widen scope.
    """
    if not _MODULE_PRESENT:
        return
    assert _MOD._OLD_MANIFEST_NAME == _OLD_NAME  # noqa: SLF001
    assert _MOD._NEW_MANIFEST_NAME == _NEW_NAME  # noqa: SLF001
    assert _MOD._OLD_MANIFEST_PK == _OLD_PK  # noqa: SLF001
    assert _MOD._OLD_MANIFEST_SK == _OLD_SK  # noqa: SLF001


def test_t44_exit_codes_are_distinct_and_stable() -> None:
    """Exit codes are load-bearing — a change is a caller-contract break."""
    if not _MODULE_PRESENT:
        return
    codes = {
        _MOD.EXIT_OK,
        _MOD.EXIT_ARGS,
        _MOD.EXIT_LIVE_STILL_HAS_REFERENTS,
        _MOD.EXIT_NEW_MANIFEST_MISSING,
        _MOD.EXIT_AWS_ERROR,
    }
    assert len(codes) == 5, "exit codes collide — a caller cannot distinguish outcomes"
    assert _MOD.EXIT_OK == 0, "OK must be 0 (POSIX)"


# ---------------------------------------------------------------------------
# Dry-run correctness — the script MUST NOT write when --apply is absent
# ---------------------------------------------------------------------------

def _fake_ddb_with(referents: list, old_manifest_present: bool, new_manifest_present: bool) -> MagicMock:
    """Build a DDB-shaped mock. Records every write attempt for assertion.

    The mock is **stateful**: DeleteItem against the OLD manifest key flips
    the `old_manifest_present` flag so subsequent get_item reflects the
    delete. Without this, the final DX25 verify inside consolidate() would
    still see the record and return EXIT_LIVE_STILL_HAS_REFERENTS on an
    otherwise-clean fixture — a mock that lies about what it deleted is
    exactly the class of bug that hides real-world verification defects.
    """
    ddb = MagicMock()

    vehicles_table = MagicMock()
    manifest_table = MagicMock()

    # Boxed so nested closures can mutate the flag.
    state = {"old_manifest_present": old_manifest_present,
             "new_manifest_present": new_manifest_present}

    # Scan paginates once. Provide a single page with LastEvaluatedKey absent.
    def _scan(*_a: Any, **_kw: Any) -> dict:
        return {"Items": list(referents)}

    vehicles_table.scan.side_effect = _scan

    def _get_item(**kw: Any) -> dict:
        key = kw["Key"]
        if key["pk"].startswith("MODEL#CMS-Fleet-Default"):
            return (
                {"Item": {"pk": key["pk"], "sk": key["sk"]}}
                if state["old_manifest_present"] else {}
            )
        if key["pk"].startswith("MODEL#CMS-FLEET-MODEL"):
            return (
                {"Item": {"pk": key["pk"], "sk": key["sk"]}}
                if state["new_manifest_present"] else {}
            )
        return {}

    vehicles_table.get_item.side_effect = _get_item
    manifest_table.get_item.side_effect = _get_item

    def _delete_item(**kw: Any) -> dict:
        key = kw["Key"]
        if key.get("pk", "").startswith("MODEL#CMS-Fleet-Default"):
            state["old_manifest_present"] = False
        return {}

    manifest_table.delete_item.side_effect = _delete_item

    def _table(name: str) -> MagicMock:
        if "storage-vehicles" in name:
            return vehicles_table
        return manifest_table

    ddb.Table.side_effect = _table
    ddb._vehicles_table = vehicles_table  # for assertion access
    ddb._manifest_table = manifest_table
    return ddb


def test_dry_run_writes_nothing_even_with_referents_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry-run run against a state that WOULD do work must issue zero writes.

    Guards the most dangerous class of regression: silent apply. The script's
    dry-run is the safety default that keeps T4.4 sitting on the ASK-FIRST
    gate; a bug that turns dry-run into a real apply defeats the gate.
    """
    if not _MODULE_PRESENT:
        return

    referents = [
        {"vehicleId": "VEH-EXAMPLE-A", "modelManifestName": _OLD_NAME},
        {"vehicleId": "VEH-EXAMPLE-B", "modelManifestName": _OLD_NAME},
    ]
    fake_ddb = _fake_ddb_with(referents, old_manifest_present=True, new_manifest_present=True)
    monkeypatch.setattr(_MOD, "_open_ddb", lambda: fake_ddb)

    exit_code = _MOD.consolidate(stage="staging", apply=False, confirm_prod=False, log=lambda *_: None)

    # Dry-run's exit code is OK — nothing failed; nothing was done.
    assert exit_code == _MOD.EXIT_OK, (
        f"dry-run should return EXIT_OK, got {exit_code}"
    )
    # Not a single write.
    fake_ddb._vehicles_table.update_item.assert_not_called()
    fake_ddb._manifest_table.delete_item.assert_not_called()


def test_apply_moves_every_referent_then_deletes_the_manifest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With --apply, exactly one UpdateItem per referent then one DeleteItem.

    Post-conditions guarded here: (a) every referent gets an UpdateItem, and
    (b) DeleteItem fires exactly once against the OLD manifest's key. The
    scan side_effect empties the referent list after phase 1 to model the
    real-world "post-move scan is zero" behaviour, so phase 2 authorises.
    """
    if not _MODULE_PRESENT:
        return

    referents = [
        {"vehicleId": f"VEH-EXAMPLE-{i}", "modelManifestName": _OLD_NAME}
        for i in range(3)
    ]
    fake_ddb = _fake_ddb_with(referents, old_manifest_present=True, new_manifest_present=True)

    # After the first scan (phase 1), the "live" state must show zero referents
    # so that the verify + phase 2 progress. Model it by rotating the scan
    # side_effect.
    fake_ddb._vehicles_table.scan.side_effect = [
        {"Items": list(referents)},   # phase 1 discovery
        {"Items": []},                # verify after phase 1
        {"Items": []},                # final DX25 verify
    ]

    monkeypatch.setattr(_MOD, "_open_ddb", lambda: fake_ddb)

    exit_code = _MOD.consolidate(stage="staging", apply=True, confirm_prod=False, log=lambda *_: None)

    assert exit_code == _MOD.EXIT_OK, (
        f"consolidation should succeed against a clean fixture, got {exit_code}"
    )
    assert fake_ddb._vehicles_table.update_item.call_count == 3, (
        f"expected one UpdateItem per referent (3), got "
        f"{fake_ddb._vehicles_table.update_item.call_count}"
    )
    assert fake_ddb._manifest_table.delete_item.call_count == 1, (
        f"expected exactly one DeleteItem, got "
        f"{fake_ddb._manifest_table.delete_item.call_count}"
    )

    # Assert the DeleteItem targets the OLD manifest key — belt-and-braces on
    # the destructive step; a typo in the key would nuke the wrong record.
    delete_kwargs = fake_ddb._manifest_table.delete_item.call_args.kwargs
    assert delete_kwargs["Key"] == {"pk": _OLD_PK, "sk": _OLD_SK}


def test_apply_refuses_when_new_manifest_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse to migrate onto an absent target — that would orphan vehicles.

    The state where CMS-FLEET-MODEL has not been seeded yet is recoverable
    with a re-run of seed_model_manifests.py; migrating onto nothing is not.
    """
    if not _MODULE_PRESENT:
        return

    fake_ddb = _fake_ddb_with(
        [{"vehicleId": "VEH-EXAMPLE-A", "modelManifestName": _OLD_NAME}],
        old_manifest_present=True,
        new_manifest_present=False,   # <── the fault
    )
    monkeypatch.setattr(_MOD, "_open_ddb", lambda: fake_ddb)

    exit_code = _MOD.consolidate(stage="staging", apply=True, confirm_prod=False, log=lambda *_: None)

    assert exit_code == _MOD.EXIT_NEW_MANIFEST_MISSING
    fake_ddb._vehicles_table.update_item.assert_not_called()
    fake_ddb._manifest_table.delete_item.assert_not_called()


def test_apply_against_prod_without_confirm_prod_is_refused() -> None:
    """`--apply --stage=prod` without `--confirm-prod` MUST refuse.

    Prod requires a second, deliberate gate — a single-flag apply is exactly
    the accident this second flag exists to prevent.
    """
    if not _MODULE_PRESENT:
        return
    exit_code = _MOD.consolidate(
        stage="prod", apply=True, confirm_prod=False, log=lambda *_: None
    )
    assert exit_code == _MOD.EXIT_ARGS


# ---------------------------------------------------------------------------
# DX25 — live guard
# ---------------------------------------------------------------------------

def _live_referents_and_manifest() -> Optional[tuple]:
    """Return (referent_count, old_manifest_exists) or None if unreachable."""
    try:
        import boto3  # noqa: PLC0415
        from boto3.dynamodb.conditions import Attr  # noqa: PLC0415
    except ModuleNotFoundError:
        return None
    try:
        ddb = boto3.resource("dynamodb", region_name=_STAGING_REGION)
        vt = ddb.Table(_STAGING_VEHICLES)
        # Paginated so a large result cannot silently truncate.
        count = 0
        kwargs: dict = {
            "FilterExpression": Attr("modelManifestName").eq(_OLD_NAME),
            "Select": "COUNT",
        }
        while True:
            page = vt.scan(**kwargs)
            count += page.get("Count", 0)
            cursor = page.get("LastEvaluatedKey")
            if not cursor:
                break
            kwargs["ExclusiveStartKey"] = cursor
        mt = ddb.Table(_STAGING_MANIFESTS)
        resp = mt.get_item(Key={"pk": _OLD_PK, "sk": _OLD_SK})
        return count, "Item" in resp
    except Exception:  # noqa: BLE001
        return None


@pytest.mark.integration
def test_dx25_no_vehicle_references_cms_fleet_default() -> None:
    """DX25a: live scan of vehicles shows zero references to CMS-Fleet-Default.

    Expected to **FAIL** until T4.4 `--apply` has been run against staging.
    Once green, it guards D19 against re-introduction.
    """
    if not _MODULE_PRESENT:
        return
    live = _live_referents_and_manifest()
    if live is None:
        pytest.skip(f"live {_STAGING_VEHICLES} unreachable")
    count, _ = live
    assert count == 0, (
        f"DX25a FAILED: {count} vehicle(s) still reference {_OLD_NAME!r} in "
        f"live {_STAGING_VEHICLES}. Run:\n"
        f"    python3 deployment/scripts/consolidate_default_model_manifest.py "
        f"--stage staging --apply"
    )


@pytest.mark.integration
def test_dx25_cms_fleet_default_manifest_record_is_retired() -> None:
    """DX25b: the CMS-Fleet-Default record itself is absent from the manifest table.

    Only meaningful once DX25a is satisfied — a record with zero referents is
    still a record. Both halves must hold for D19's consolidation to be done.
    """
    if not _MODULE_PRESENT:
        return
    live = _live_referents_and_manifest()
    if live is None:
        pytest.skip(f"live {_STAGING_MANIFESTS} unreachable")
    _, present = live
    assert not present, (
        f"DX25b FAILED: {_OLD_NAME!r} manifest record still exists in live "
        f"{_STAGING_MANIFESTS}. Run T4.4 with --apply to complete phase 2."
    )
