#!/usr/bin/env python3
"""Guard: the AppAccess policy grants ``dynamodb:Query`` on the vehicles vin-index.

Spec ``2026-09-02-cms-dms-service-convergence`` T5.2, D-G5e.

WHY THIS TEST EXISTS.

``GET /api/v1/service-history`` resolves vehicleId → vin via a two-step query:
Step 1 is a ``GetItem`` on the vehicles table (covered by the existing ``table/*``
grant); step 2 is a ``Query`` on ``vin-index``.

A GSI query needs the index ARN in the IAM resource list — not just the table ARN.
The existing ``table/*`` statement is table-level and does NOT cover ``index/*``.
Without the new statement, step 2 fails ``AccessDenied`` at runtime while every
unit test in ``test_service_history_dms_backed.py`` passes (they stub the DDB
resource and never call IAM). This is exactly the defect class D-G5e cites from
``storage_stack.py:498``: "made a MISSING index look merely like a slow one for
months."

Two properties are pinned here:

1. The literal ARN for ``vin-index`` on the vehicles table IS present in the
   AppAccess policy.
2. No resource in the AppAccess policy ends in ``/index/*`` — a wildcard would
   silently grant queries on future indexes that belong to restricted-access tables.
   This assertion makes a well-intentioned "simplify the policy" edit fail
   immediately rather than widening access invisibly.

Run:
    python3 -m pytest deployment/stacks/test_main_api_vin_index_grant.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_UI_STACK = _REPO_ROOT / "deployment" / "stacks" / "ui_stack.py"


def _ui_stack_source() -> str:
    return _UI_STACK.read_text(encoding="utf-8")


def _appaccess_block(source: str) -> str:
    """Extract the AppAccess PolicyDocument block.

    Premise: there is exactly one ``"AppAccess"`` section in ui_stack.py.
    """
    idx = source.find('"AppAccess"')
    assert idx != -1, '"AppAccess" block not found in ui_stack.py'
    # Walk forward to find the matching PolicyDocument(statements=[...]) close.
    # We grab a conservatively large slice — enough to cover all statements.
    return source[idx: idx + 5000]


class TestVinIndexGrantPresent:
    """The vin-index ARN must appear in the AppAccess policy."""

    def test_vehicles_vin_index_arn_is_in_appaccess(self) -> None:
        """Literal match on the ARN fragment, NOT a wildcard.

        The fragment must contain the index name and the table name component.
        A ``/index/*`` wildcard would pass this test AND expose future indexes —
        see ``TestNoWildcardIndexGrant`` below for why that is pinned separately.
        """
        src = _ui_stack_source()
        block = _appaccess_block(src)
        assert "vehicles/index/vin-index" in block, (
            "The AppAccess policy does not grant Query on vehicles/index/vin-index. "
            "Without this, GET /api/v1/service-history step 2 (VIN→vehicleId via "
            "vin-index) fails AccessDenied at runtime while all unit tests stay green. "
            "Add the IAM statement per D-G5e in spec "
            "2026-09-02-cms-dms-service-convergence."
        )

    def test_vin_index_grant_is_query_not_scan(self) -> None:
        """The grant must be ``dynamodb:Query``, not ``dynamodb:Scan``.

        ``storage_stack.py:498`` records that a scan fallback on this table "made
        a MISSING index look merely like a slow one for months". D-G5e explicitly
        forbids a scan fallback — an unresolvable VIN returns (vehicle_id, None)
        and logs, never falls back to a full-table scan. Granting Scan on the
        index would not add runtime power (a scan on a KEYS_ONLY index is
        equivalent to a scan on the table), but auditing it as "only Query" keeps
        the access model honest.
        """
        src = _ui_stack_source()
        block = _appaccess_block(src)

        # Find the statement containing vin-index and check its actions.
        vin_idx = block.find("vehicles/index/vin-index")
        assert vin_idx != -1  # already checked above
        # Scan backwards to find the containing PolicyStatement block
        stmt_start = block.rfind("PolicyStatement(", 0, vin_idx)
        assert stmt_start != -1, "could not find PolicyStatement before vin-index ARN"
        stmt_snippet = block[stmt_start: vin_idx + 80]
        assert '"dynamodb:Query"' in stmt_snippet or "'dynamodb:Query'" in stmt_snippet, (
            "The vin-index grant must use dynamodb:Query, not a broader action set."
        )
        assert "Scan" not in stmt_snippet, (
            "Scan must not be in the vin-index grant — D-G5e explicitly forbids it. "
            "An unresolvable VIN logs a message and returns (id, None), never scans."
        )

    def test_premise_appaccess_contains_existing_dtc_index(self) -> None:
        """Premise guard: the AppAccess block we are reading is the real one.

        The existing ``dtc-history/index/active-code-index`` grant ships in
        ui_stack.py. If the block extraction above ever returns the wrong region,
        both vin-index tests would vacuously pass (nothing to assert absent). This
        asserts the presence of the pre-existing anchor so the vin-index tests
        cannot be trivially deceived by a future refactor that moves the AppAccess
        block out of range.
        """
        src = _ui_stack_source()
        block = _appaccess_block(src)
        assert "dtc-history/index/active-code-index" in block, (
            "AppAccess block extraction returned a range that does not contain the "
            "pre-existing dtc-history/index/active-code-index grant — the vin-index "
            "assertions above are vacuous. Check _appaccess_block()."
        )


class TestNoWildcardIndexGrant:
    """No resource in AppAccess may end in ``/index/*``.

    A wildcard would silently grant ``Query`` on indexes that belong to tables
    with access restrictions — for example, a future restricted-PII table whose
    index might be queryable to every main_api caller. This assertion fails
    immediately on any edit that widens the index grant "for simplicity".

    Note: this asserts on the ENTIRE AppAccess block, not on any single statement,
    because a widening edit might modify a different statement (e.g., add ``/index/*``
    to the existing ``table/*`` statement) rather than touching the new one.
    """

    def test_no_resource_ends_with_index_wildcard(self) -> None:
        src = _ui_stack_source()
        block = _appaccess_block(src)
        # Find all resource ARN fragments in the block
        arns = re.findall(r'arn:aws:dynamodb[^"\']+', block)
        wildcard_arns = [a for a in arns if a.rstrip("'\"").endswith("/index/*")]
        assert not wildcard_arns, (
            "AppAccess policy contains a wildcard index grant, which would silently "
            "extend Query access to every future GSI on every table in the account: "
            f"{wildcard_arns}. Scope index grants to individual named indexes."
        )
