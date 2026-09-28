"""Tests for the dataSource write in admin_add_vehicle/handler.py.

Covers spec § D8 "Test surface > Backend":
  O1: _write_vehicle writes dataSource: 'cloud-telemetry' on every new row.
  O2: Re-enroll path populates dataSource via if_not_exists when absent;
      preserves existing value when present.
  O3: Source-scan invariant — handler module source never reads ``make ==``
      or the string ``"Ford"`` (brand-blind write path).

Spec: ``cms/.kiro/specs/2026-08-29-cms-vehicle-classification/spec.md``

RED PHASE: test bodies raise NotImplementedError.
Bodies land alongside the OEM1 handler change in Group 3.

Run from the repo root::

    python3 -m pytest services/connectors/oem1/admin_add_vehicle/test_datasource_write.py -v

Style: pytest + unittest.mock.MagicMock (no moto), matching the pattern in
``services/connectors/oem1/admin_add_vehicle/tests/test_handler.py``.
"""
from __future__ import annotations

import os
import sys

import pytest

# ---------------------------------------------------------------------------
# sys.path bootstrap: mirror the conftest.py pattern from tests/conftest.py
# so 'handler' (and its transitive imports from the oem1 connector package)
# resolve regardless of where pytest is invoked from.
# ---------------------------------------------------------------------------
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_OEM1_DIR = os.path.abspath(os.path.join(_THIS_DIR, ".."))     # services/connectors/oem1
_HANDLER_DIR = _THIS_DIR                                         # admin_add_vehicle/

for _p in (_OEM1_DIR, _HANDLER_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# Environment defaults required for the handler module to import cleanly.
os.environ.setdefault("OEM1_FEED_HOST", "oem1-feed.example.local")
os.environ.setdefault("SECRETS_NAME", "cms-staging-connector-oem1-credentials")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "staging")
os.environ.setdefault("VEHICLES_TABLE_NAME", "cms-staging-storage-vehicles")
os.environ.setdefault("FLEET_ENROLLMENT_TABLE_NAME", "cms-staging-storage-fleet-enrollment")
os.environ.setdefault("ENGINEERING_FLEET_IDS_PARAM", "/cms/staging/engineering-fleet-ids")
os.environ.setdefault("FLEETS_TABLE_NAME", "cms-staging-storage-fleets")


# ---------------------------------------------------------------------------
# O1 — _write_vehicle writes dataSource: 'cloud-telemetry'
# ---------------------------------------------------------------------------

def test_o1_write_vehicle_writes_cloud_telemetry_datasource():
    """O1: _write_vehicle writes dataSource='cloud-telemetry' on every new row.

    Acceptance (spec § D4):
      After Group 3's change, the DynamoDB put_item (or equivalent) call inside
      _write_vehicle includes ``item["dataSource"] = {"S": "cloud-telemetry"}``.
      The value is 'cloud-telemetry' — the OEM1 flow always produces cloud-telemetry
      vehicles by construction.

    Approach: stub boto3.client('dynamodb') so put_item records the item dict;
    assert 'dataSource' key is present with value {"S": "cloud-telemetry"}.
    """
    from unittest.mock import MagicMock, patch
    import handler as h

    mock_ddb = MagicMock()
    mock_ddb.put_item.return_value = {}
    # Simulate successful insert (no exception)
    mock_ddb.exceptions.ConditionalCheckFailedException = Exception

    h._write_vehicle(
        ddb_client=mock_ddb,
        vehicle_id="VEH-O1-TEST",
        status="COMPLETED",
        now="2026-08-29T00:00:00Z",
        record={},
    )

    # Verify put_item was called
    mock_ddb.put_item.assert_called_once()
    call_kwargs = mock_ddb.put_item.call_args[1]
    item = call_kwargs["Item"]
    assert "dataSource" in item, (
        f"_write_vehicle did not write 'dataSource' key. Item keys: {list(item.keys())}"
    )
    assert item["dataSource"] == {"S": "cloud-telemetry"}, (
        f"Expected dataSource={{'S': 'cloud-telemetry'}}, got {item['dataSource']}"
    )


# ---------------------------------------------------------------------------
# O2 — Re-enroll path populates / preserves dataSource via if_not_exists
# ---------------------------------------------------------------------------

def test_o2_reenroll_path_populates_datasource_when_absent():
    """O2 (part a): Re-enroll path populates dataSource via if_not_exists when absent.

    Acceptance (spec § D4):
      When the re-enroll branch (ConditionalCheckFailedException path at ~L282
      in the current handler) runs against a row with no 'dataSource' attribute,
      the update_item call adds 'dataSource' = 'cloud-telemetry' guarded by
      ``attribute_not_exists(dataSource)`` (if_not_exists pattern), matching the
      existing 'make'/'model' backfill pattern in that block.

    Approach: stub the DDB client such that the first put_item raises
    ConditionalCheckFailedException, triggering the re-enroll branch; capture
    the update_item call and assert its UpdateExpression includes dataSource.
    """
    from unittest.mock import MagicMock, call
    import handler as h

    mock_ddb = MagicMock()

    # Simulate already-enrolled row (triggers re-enroll path)
    class _ConditionalCheckFailed(Exception):
        pass

    mock_ddb.exceptions.ConditionalCheckFailedException = _ConditionalCheckFailed
    mock_ddb.put_item.side_effect = _ConditionalCheckFailed("duplicate")
    mock_ddb.update_item.return_value = {}

    h._write_vehicle(
        ddb_client=mock_ddb,
        vehicle_id="VEH-O2A-TEST",
        status="COMPLETED",
        now="2026-08-29T00:00:00Z",
        record={},
    )

    # Verify update_item was called
    mock_ddb.update_item.assert_called_once()
    call_kwargs = mock_ddb.update_item.call_args[1]

    update_expr = call_kwargs.get("UpdateExpression", "")
    expr_values = call_kwargs.get("ExpressionAttributeValues", {})
    expr_names = call_kwargs.get("ExpressionAttributeNames", {})

    # The update expression must reference dataSource (via the alias '#ds')
    assert "#ds" in update_expr or "dataSource" in update_expr, (
        f"UpdateExpression does not reference dataSource. Got: {update_expr!r}"
    )
    # if_not_exists must be in the expression
    assert "if_not_exists" in update_expr, (
        f"UpdateExpression must use if_not_exists for dataSource. Got: {update_expr!r}"
    )
    # Value must be cloud-telemetry
    assert ":ds" in expr_values, (
        f"ExpressionAttributeValues must contain ':ds'. Got keys: {list(expr_values.keys())}"
    )
    assert expr_values[":ds"] == {"S": "cloud-telemetry"}, (
        f"Expected :ds={{'S': 'cloud-telemetry'}}, got {expr_values[':ds']}"
    )


def test_o2_reenroll_path_preserves_existing_datasource():
    """O2 (part b): Re-enroll path preserves existing dataSource value when present.

    Acceptance (spec § D4):
      If a row already carries 'dataSource', the if_not_exists guard means the
      re-enroll update expression does NOT overwrite it. Verified by stubbing
      DDB with a row that already has dataSource='cloud-telemetry' and asserting
      the update expression either omits dataSource or uses if_not_exists.
    """
    from unittest.mock import MagicMock
    import handler as h

    mock_ddb = MagicMock()

    class _ConditionalCheckFailed(Exception):
        pass

    mock_ddb.exceptions.ConditionalCheckFailedException = _ConditionalCheckFailed
    mock_ddb.put_item.side_effect = _ConditionalCheckFailed("duplicate")
    mock_ddb.update_item.return_value = {}

    h._write_vehicle(
        ddb_client=mock_ddb,
        vehicle_id="VEH-O2B-TEST",
        status="COMPLETED",
        now="2026-08-29T00:00:00Z",
        record={},
    )

    call_kwargs = mock_ddb.update_item.call_args[1]
    update_expr = call_kwargs.get("UpdateExpression", "")

    # The spec requires if_not_exists — not unconditional SET.
    # Either: dataSource absent from expression (acceptable) OR if_not_exists present.
    if "dataSource" in update_expr or "#ds" in update_expr:
        assert "if_not_exists" in update_expr, (
            f"dataSource in UpdateExpression but NOT guarded by if_not_exists. "
            f"Got: {update_expr!r}. This would overwrite existing values."
        )


# ---------------------------------------------------------------------------
# O3 — Source-scan invariant: handler never reads make == or "Ford"
# ---------------------------------------------------------------------------

def test_o3_handler_source_has_no_make_equality_or_ford_literal():
    """O3: handler.py source never contains ``make ==`` or the literal string ``"Ford"``.

    Acceptance (spec § D8 — publish-safety invariant):
      Classification on the OEM1 write path is trivially known ('cloud-telemetry'
      by construction) and must NEVER be derived from a brand string. This test
      scans the handler module's .py source with two regexes:
        1. make followed by == — would indicate a brand-equality branch.
        2. the string 'Ford' as a Python string literal — a hardcoded brand literal.
      Both must return zero matches.

    This is the write-path companion to C8 and V18: three separate test files
    collectively assert that no code path in the system reads a brand string to
    decide classification — helpers, request handler, or OEM1 handler.
    """
    import re
    import pathlib

    handler_path = pathlib.Path(_THIS_DIR) / "handler.py"
    if not handler_path.exists():
        pytest.skip("handler.py not yet written — O3 runs after Group 3")

    source = handler_path.read_text()

    make_eq_matches = re.findall(r'make\s*==', source)
    assert make_eq_matches == [], (
        f"handler.py contains 'make ==' at {len(make_eq_matches)} location(s). "
        "Classification must never branch on a brand/make equality. "
        f"Offending matches: {make_eq_matches}"
    )

    # "Ford" as a Python string literal (single or double quotes)
    ford_literal_matches = re.findall(r'''["']Ford["']''', source)
    assert ford_literal_matches == [], (
        f"handler.py contains a 'Ford' string literal at {len(ford_literal_matches)} "
        "location(s). Brand literals must not appear in shipping runtime code. "
        "See spec § Constraints 'No brand literal in shipping code'."
    )
