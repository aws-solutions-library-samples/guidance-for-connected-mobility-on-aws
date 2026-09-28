"""Tests for migrate_iot_thing_names.py

Cases covered:
  IOT1: Happy path — creates new Thing, attaches cert, detaches from old, deletes old.
  IOT2: Idempotency — new Thing already exists → create is a no-op, rest continues.
  IOT3: Idempotency — principal already attached to new Thing → attach is a no-op.
  IOT4: Idempotency — old Thing already gone → detach + delete are no-ops.
  IOT5: Ordering constraint — attach-before-detach verified via call sequence.
  IOT6: Ordering constraint — detach-before-delete verified via call sequence.
  IOT7: Failure in step 1 (create) → error counter incremented, exit non-zero.
  IOT8: Failure in step 2 (attach) → error counter incremented, exit non-zero.
  IOT9: prod without --confirm-prod → dry-run regardless of --apply.
  IOT10: cert discovered via DDB fallback when IoT has no principals.
  IOT11: map-file TSV parsing — blank lines and comments skipped.

Style: pytest + unittest.mock (no live AWS calls, no moto).
Follows the conventions of test_scrub_vehicle_brand_identifiers.py.

Run from repo root::

    python3 -m pytest deployment/scripts/tests/test_migrate_iot_thing_names.py -v
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import contextlib
from unittest.mock import MagicMock, call, patch

import pytest
from botocore.exceptions import ClientError

# ── sys.path setup ────────────────────────────────────────────────────────
_SCRIPTS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import migrate_iot_thing_names as mig  # noqa: E402

# ── Synthetic test constants (no real VINs / vehicleIds / brands) ─────────
_STAGE = "staging"
_VEHICLE_ID = "VEH-TEST-001"
_OLD_VIN = "TESTVIN_OLD_00001"
_NEW_VIN = "MRDN_TEST_00001"
_CERT_ID = "test-cert-id-00000000001"
_CERT_ARN = f"arn:aws:iot:us-west-2:123456789012:cert/{_CERT_ID}"


# ── Helpers ───────────────────────────────────────────────────────────────

def _client_error(code: str, msg: str = "test error") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": msg}}, "TestOp")


def _make_tsv_file(rows: list[tuple[str, str, str]]) -> str:
    """Write rows to a temp TSV file, return its path."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tsv", delete=False, encoding="utf-8"
    ) as fh:
        for vehicle_id, old_vin, new_vin in rows:
            fh.write(f"{vehicle_id}\t{old_vin}\t{new_vin}\n")
        return fh.name


def _make_iot_mock(
    old_thing_exists: bool = True,
    new_thing_exists: bool = False,
    cert_attached_to_old: bool = True,
    cert_attached_to_new: bool = False,
) -> MagicMock:
    """Return a configured IoT client mock."""
    iot = MagicMock()

    def _describe_thing(thingName: str) -> dict:
        if thingName == _OLD_VIN and old_thing_exists:
            return {"thingName": thingName}
        if thingName == _NEW_VIN and new_thing_exists:
            return {"thingName": thingName}
        raise _client_error("ResourceNotFoundException", f"Thing {thingName} not found")

    iot.describe_thing.side_effect = _describe_thing

    def _list_thing_principals(thingName: str) -> dict:
        if thingName == _OLD_VIN:
            if not old_thing_exists:
                raise _client_error("ResourceNotFoundException")
            return {"principals": [_CERT_ARN] if cert_attached_to_old else []}
        if thingName == _NEW_VIN:
            if not new_thing_exists:
                raise _client_error("ResourceNotFoundException")
            return {"principals": [_CERT_ARN] if cert_attached_to_new else []}
        return {"principals": []}

    iot.list_thing_principals.side_effect = _list_thing_principals
    iot.create_thing.return_value = {}
    iot.attach_thing_principal.return_value = {}
    iot.detach_thing_principal.return_value = {}
    iot.delete_thing.return_value = {}
    return iot


def _make_ddb_mock(has_cert_row: bool = True) -> MagicMock:
    """Return a configured DynamoDB client mock."""
    ddb = MagicMock()
    if has_cert_row:
        ddb.get_item.return_value = {
            "Item": {
                "vehicleId": {"S": _VEHICLE_ID},
                "certificateArn": {"S": _CERT_ARN},
                "certificateId": {"S": _CERT_ID},
            }
        }
    else:
        ddb.get_item.return_value = {}
    return ddb


def _run_migration(
    iot_mock: MagicMock,
    ddb_mock: MagicMock,
    rows: list[tuple[str, str, str]],
    apply: bool = True,
    stage: str = _STAGE,
    confirm_prod: bool = False,
) -> int:
    """Helper: write TSV, build args namespace, call run(). Returns exit code."""
    tsv_path = _make_tsv_file(rows)
    try:
        session_mock = MagicMock()
        session_mock.client.side_effect = lambda svc, **kw: (
            iot_mock if svc == "iot" else ddb_mock
        )
        args = MagicMock()
        args.stage = stage
        args.apply = apply
        args.confirm_prod = confirm_prod
        args.map_file = tsv_path

        with patch("boto3.Session", return_value=session_mock):
            with patch.dict(os.environ, {
                "VEHICLE_CERTIFICATES_TABLE_NAME": f"cms-{stage}-storage-vehicle-certificates",
            }):
                return mig.run(args)
    finally:
        os.unlink(tsv_path)


# ── IOT1: Happy path ──────────────────────────────────────────────────────

def test_iot1_happy_path_full_migration():
    """IOT1: Happy path — creates new Thing, attaches cert, detaches from old,
    deletes old. Exit 0, migrated=1.

    Acceptance:
      - create_thing called with new_vin
      - attach_thing_principal called with new_vin + cert_arn
      - detach_thing_principal called with old_vin + cert_arn
      - delete_thing called with old_vin
      - exit code 0
    """
    iot = _make_iot_mock(
        old_thing_exists=True,
        new_thing_exists=False,
        cert_attached_to_old=True,
        cert_attached_to_new=False,
    )
    ddb = _make_ddb_mock(has_cert_row=True)

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc == 0, f"Expected exit 0, got {rc}"
    iot.create_thing.assert_called_once_with(thingName=_NEW_VIN)
    iot.attach_thing_principal.assert_called_once_with(
        thingName=_NEW_VIN, principal=_CERT_ARN
    )
    iot.detach_thing_principal.assert_called_once_with(
        thingName=_OLD_VIN, principal=_CERT_ARN
    )
    iot.delete_thing.assert_called_once_with(thingName=_OLD_VIN)


# ── IOT2: New Thing already exists ───────────────────────────────────────

def test_iot2_new_thing_already_exists_is_noop():
    """IOT2: New Thing already exists → create is a no-op; remaining steps proceed.

    Acceptance:
      - create_thing NOT called (describe_thing succeeds → skip)
      - attach, detach, delete still called
      - exit code 0
    """
    iot = _make_iot_mock(
        old_thing_exists=True,
        new_thing_exists=True,   # ← already there
        cert_attached_to_old=True,
        cert_attached_to_new=False,
    )
    ddb = _make_ddb_mock()

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc == 0, f"Expected exit 0, got {rc}"
    # create_thing must NOT have been called (idempotent guard)
    iot.create_thing.assert_not_called()
    # attach still runs
    iot.attach_thing_principal.assert_called_once()
    # detach + delete still run
    iot.detach_thing_principal.assert_called_once()
    iot.delete_thing.assert_called_once()


# ── IOT3: Principal already attached to new Thing ─────────────────────────

def test_iot3_principal_already_attached_to_new_thing():
    """IOT3: Principal already attached to new Thing → attach is a no-op.

    Acceptance:
      - attach_thing_principal NOT called
      - detach + delete still called
      - exit code 0
    """
    iot = _make_iot_mock(
        old_thing_exists=True,
        new_thing_exists=True,
        cert_attached_to_old=True,
        cert_attached_to_new=True,  # ← already attached to new
    )
    ddb = _make_ddb_mock()

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc == 0, f"Expected exit 0, got {rc}"
    iot.attach_thing_principal.assert_not_called()
    # Detach and delete must still run
    iot.detach_thing_principal.assert_called_once()
    iot.delete_thing.assert_called_once()


# ── IOT4: Old Thing already gone ─────────────────────────────────────────

def test_iot4_old_thing_already_gone_is_noop():
    """IOT4: Old Thing already gone → detach + delete are no-ops; exit 0.

    This covers resuming an interrupted migration where steps 1+2 completed
    but steps 3+4 did not finish (new Thing exists, cert already moved).

    Acceptance:
      - describe_thing(old) raises ResourceNotFoundException
      - list_thing_principals(old) raises ResourceNotFoundException → detach is no-op
      - delete_thing NOT called
      - exit code 0
    """
    iot = _make_iot_mock(
        old_thing_exists=False,   # ← old already gone
        new_thing_exists=True,
        cert_attached_to_old=False,
        cert_attached_to_new=True,
    )
    ddb = _make_ddb_mock()

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc == 0, f"Expected exit 0, got {rc}"
    iot.detach_thing_principal.assert_not_called()
    iot.delete_thing.assert_not_called()
    iot.create_thing.assert_not_called()  # new Thing already exists


# ── IOT5: Ordering — attach happens before detach ─────────────────────────

def test_iot5_ordering_attach_before_detach():
    """IOT5: Ordering constraint — attach-to-new precedes detach-from-old.

    The certificate must never be left attached to nothing. This test verifies
    the call order: attach_thing_principal(new) must happen before
    detach_thing_principal(old).

    Acceptance:
      The position of the attach call in the mock's call_args_list is earlier
      than the position of the detach call.
    """
    iot = _make_iot_mock(
        old_thing_exists=True,
        new_thing_exists=False,
        cert_attached_to_old=True,
        cert_attached_to_new=False,
    )
    ddb = _make_ddb_mock()

    # Capture all calls in order
    iot_calls: list[str] = []
    original_attach = iot.attach_thing_principal.side_effect
    original_detach = iot.detach_thing_principal.side_effect

    def _attach(**kw):
        iot_calls.append("attach")
        return {}
    def _detach(**kw):
        iot_calls.append("detach")
        return {}

    iot.attach_thing_principal.side_effect = _attach
    iot.detach_thing_principal.side_effect = _detach

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc == 0, f"Expected exit 0, got {rc}"
    assert "attach" in iot_calls, "attach_thing_principal was never called"
    assert "detach" in iot_calls, "detach_thing_principal was never called"
    attach_pos = iot_calls.index("attach")
    detach_pos = iot_calls.index("detach")
    assert attach_pos < detach_pos, (
        f"attach (pos {attach_pos}) must precede detach (pos {detach_pos}); "
        f"got call order: {iot_calls}"
    )


# ── IOT6: Ordering — detach happens before delete ─────────────────────────

def test_iot6_ordering_detach_before_delete():
    """IOT6: Ordering constraint — detach-from-old precedes delete-old.

    AWS rejects deletion of a Thing with attached principals. This test
    verifies the call order.

    Acceptance:
      The position of the detach call is earlier than the position of the
      delete call in the mock's side-effect call log.
    """
    iot = _make_iot_mock(
        old_thing_exists=True,
        new_thing_exists=False,
        cert_attached_to_old=True,
        cert_attached_to_new=False,
    )
    ddb = _make_ddb_mock()

    iot_calls: list[str] = []

    def _detach(**kw):
        iot_calls.append("detach")
        return {}
    def _delete(**kw):
        iot_calls.append("delete")
        return {}

    iot.detach_thing_principal.side_effect = _detach
    iot.delete_thing.side_effect = _delete

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc == 0, f"Expected exit 0, got {rc}"
    assert "detach" in iot_calls, "detach_thing_principal was never called"
    assert "delete" in iot_calls, "delete_thing was never called"
    detach_pos = iot_calls.index("detach")
    delete_pos = iot_calls.index("delete")
    assert detach_pos < delete_pos, (
        f"detach (pos {detach_pos}) must precede delete (pos {delete_pos}); "
        f"got call order: {iot_calls}"
    )


# ── IOT7: Step 1 failure (create) → error, exit non-zero ─────────────────

def test_iot7_create_thing_failure_exits_nonzero():
    """IOT7: Failure in step 1 (create_thing) → error counter incremented,
    subsequent steps NOT attempted, exit non-zero.

    Acceptance:
      - create_thing raises a non-AlreadyExists ClientError
      - attach_thing_principal NOT called
      - detach_thing_principal NOT called
      - delete_thing NOT called
      - exit code != 0
    """
    iot = _make_iot_mock(
        old_thing_exists=True,
        new_thing_exists=False,
        cert_attached_to_old=True,
        cert_attached_to_new=False,
    )
    iot.create_thing.side_effect = _client_error("ThrottlingException", "slow down")
    ddb = _make_ddb_mock()

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc != 0, f"Expected non-zero exit on create_thing failure, got {rc}"
    iot.attach_thing_principal.assert_not_called()
    iot.detach_thing_principal.assert_not_called()
    iot.delete_thing.assert_not_called()


# ── IOT8: Step 2 failure (attach) → error, exit non-zero ─────────────────

def test_iot8_attach_failure_exits_nonzero():
    """IOT8: Failure in step 2 (attach_thing_principal) → error counter incremented,
    steps 3+4 NOT attempted, exit non-zero.

    Acceptance:
      - create_thing succeeds
      - attach_thing_principal raises a non-idempotent ClientError
      - detach_thing_principal NOT called
      - delete_thing NOT called
      - exit code != 0
    """
    iot = _make_iot_mock(
        old_thing_exists=True,
        new_thing_exists=False,
        cert_attached_to_old=True,
        cert_attached_to_new=False,
    )
    iot.attach_thing_principal.side_effect = _client_error(
        "InternalFailureException", "service error"
    )
    ddb = _make_ddb_mock()

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc != 0, f"Expected non-zero exit on attach failure, got {rc}"
    iot.create_thing.assert_called()  # step 1 succeeded
    iot.detach_thing_principal.assert_not_called()
    iot.delete_thing.assert_not_called()


# ── IOT9: prod without --confirm-prod → dry-run ───────────────────────────

def test_iot9_prod_without_confirm_prod_is_dry_run():
    """IOT9: --stage prod --apply without --confirm-prod → dry-run, no writes.

    Acceptance:
      - exit code 0
      - no IoT mutation calls
      - output contains PROD SURFACE or dry-run indicator
    """
    iot = _make_iot_mock()
    ddb = _make_ddb_mock()

    output = io.StringIO()
    tsv_path = _make_tsv_file([(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])
    try:
        session_mock = MagicMock()
        session_mock.client.side_effect = lambda svc, **kw: iot if svc == "iot" else ddb

        args = MagicMock()
        args.stage = "prod"
        args.apply = True
        args.confirm_prod = False  # ← missing
        args.map_file = tsv_path

        with patch("boto3.Session", return_value=session_mock):
            with patch.dict(os.environ, {
                "VEHICLE_CERTIFICATES_TABLE_NAME": "cms-prod-storage-vehicle-certificates",
            }):
                with contextlib.redirect_stdout(output):
                    rc = mig.run(args)
    finally:
        os.unlink(tsv_path)

    assert rc == 0, f"Expected exit 0, got {rc}"
    # No mutation calls
    iot.create_thing.assert_not_called()
    iot.attach_thing_principal.assert_not_called()
    iot.detach_thing_principal.assert_not_called()
    iot.delete_thing.assert_not_called()
    out = output.getvalue()
    assert "PROD SURFACE" in out or "dry-run" in out.lower() or "DRY-RUN" in out, (
        f"Expected prod-safety message in output; got:\n{out}"
    )


# ── IOT10: Cert from DDB fallback ────────────────────────────────────────

def test_iot10_cert_discovered_via_ddb_fallback():
    """IOT10: When IoT returns no principals for the old Thing, cert is discovered
    via the DDB cert table (get_item by vehicleId) and the migration proceeds.

    Acceptance:
      - list_thing_principals(old) returns empty list (not an error)
      - ddb.get_item called with Key={"vehicleId": {"S": _VEHICLE_ID}}
      - attach_thing_principal called with cert from DDB
      - exit code 0
    """
    iot = _make_iot_mock(
        old_thing_exists=True,
        new_thing_exists=False,
        cert_attached_to_old=False,   # ← IoT reports no principals
        cert_attached_to_new=False,
    )
    ddb = _make_ddb_mock(has_cert_row=True)  # ← DDB has the cert

    # After attach, list_thing_principals(new) should show the cert attached
    # Adjust mock: after attach is called, new Thing has cert
    attach_called = []
    original_ltp = iot.list_thing_principals.side_effect

    def _ltp_after_attach(thingName: str) -> dict:
        if thingName == _NEW_VIN and attach_called:
            return {"principals": [_CERT_ARN]}
        return original_ltp(thingName=thingName)

    iot.list_thing_principals.side_effect = _ltp_after_attach
    original_attach = iot.attach_thing_principal.side_effect

    def _attach(**kw):
        attach_called.append(True)
        return {}

    iot.attach_thing_principal.side_effect = _attach

    rc = _run_migration(iot, ddb, [(_VEHICLE_ID, _OLD_VIN, _NEW_VIN)])

    assert rc == 0, f"Expected exit 0, got {rc}"

    # DDB get_item must have been called with vehicleId key
    ddb.get_item.assert_called()
    call_args = ddb.get_item.call_args
    key = call_args[1].get("Key") or (call_args[0][0] if call_args[0] else {})
    assert key.get("vehicleId") == {"S": _VEHICLE_ID}, (
        f"DDB get_item must use vehicleId HASH key only; got Key={key}"
    )

    # Cert from DDB was used for attach
    iot.attach_thing_principal.assert_called()
    attach_call_kw = iot.attach_thing_principal.call_args[1]
    assert attach_call_kw.get("principal") == _CERT_ARN, (
        f"Expected cert ARN from DDB {_CERT_ARN!r}; got {attach_call_kw}"
    )


# ── IOT11: TSV parsing ────────────────────────────────────────────────────

def test_iot11_tsv_parsing_blank_and_comment_lines_skipped():
    """IOT11: map-file TSV — blank lines and comment lines are skipped.

    Acceptance:
      A TSV with blank lines, a comment, and two data rows produces exactly
      two migration attempts.
    """
    tsv_content = (
        "# This is a comment\n"
        "\n"  # blank line
        f"{_VEHICLE_ID}\t{_OLD_VIN}\t{_NEW_VIN}\n"
        "\n"  # another blank
        "VEH-TEST-002\tTESTVIN_OLD_00002\tMRDN_TEST_00002\n"
    )
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".tsv", delete=False, encoding="utf-8"
    ) as fh:
        fh.write(tsv_content)
        tsv_path = fh.name

    try:
        rows = mig._parse_map_file(tsv_path)
    finally:
        os.unlink(tsv_path)

    assert len(rows) == 2, f"Expected 2 rows, got {len(rows)}: {rows}"
    assert rows[0] == (_VEHICLE_ID, _OLD_VIN, _NEW_VIN)
    assert rows[1] == ("VEH-TEST-002", "TESTVIN_OLD_00002", "MRDN_TEST_00002")
