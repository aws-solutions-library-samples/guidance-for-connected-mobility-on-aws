"""Tests for backfill_veh_vo_001_cert.py — BF1-BF4

Spec: `.kiro/specs/2026-08-28-cms-cert-follows-model/spec.md` § Test surface

All tests use unittest.mock.patch on boto3 clients — no live AWS calls.
Mocks are scoped to individual tests (no session-level fixtures).

NOTE: The synthetic VIN TESTVIN0000000001 is used throughout. The real
target vehicle VIN is NOT present in this file.
"""

import json
import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Add deployment/scripts to path so we can import the backfill module
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import backfill_veh_vo_001_cert as backfill  # noqa: E402

_SYNTHETIC_VIN = "TESTVIN0000000001"
_SYNTHETIC_VEHICLE_ID = "VEH-TEST-001"
_STAGE = "staging"

# A minimal ACTIVE model manifest row (matches DynamoDB resource-style plain dicts)
_MODEL_ROW = {
    "pk": "MODEL#CMS-Fleet-Default#1",
    "sk": "MODEL#CMS-Fleet-Default",
    "modelManifestName": "CMS-Fleet-Default",
    "modelManifestVersion": "1",
    "decoderManifestRef": "cms-fleet-v3",
    "ecuConfigId": "ECU-CONFIG-CMS-BASELINE",
    "status": "ACTIVE",
}

# A bare vehicle row (no model, no cert)
_BARE_VEHICLE_ROW = {
    "vehicleId": _SYNTHETIC_VEHICLE_ID,
    "vin": _SYNTHETIC_VIN,
    "vehicleType": "sedan",
}

# A vehicle row already carrying modelManifestName
_MODEL_VEHICLE_ROW = {
    "vehicleId": _SYNTHETIC_VEHICLE_ID,
    "vin": _SYNTHETIC_VIN,
    "vehicleType": "sedan",
    "modelManifestName": "CMS-Fleet-Default",
    "modelManifestVersion": "1",
    "decoderManifestRef": "cms-fleet-v3",
    "dataSource": "vehicle-telemetry",
    "hasCertificate": True,
    "certificateId": "abc123certid",
}

# Safe synthetic key material for mocked IoT responses (not real cryptographic material)
_FAKE_CERT_PEM = "CERT-PEM-PLACEHOLDER-FOR-TESTS"
_FAKE_PUBLIC_KEY = "PUBLIC-KEY-PLACEHOLDER-FOR-TESTS"
_FAKE_PRIVATE_KEY = "PRIVATE-KEY-PLACEHOLDER-FOR-TESTS"


def _make_ddb_resource_mock(vehicle_row, model_row):
    """Return a mock boto3 DynamoDB resource with Table() returning stable per-name mocks."""
    ddb_resource = MagicMock()
    _tables = {}

    def _table_factory(table_name):
        if table_name in _tables:
            return _tables[table_name]
        table = MagicMock()
        if "storage-vehicles" in table_name:
            table.get_item.return_value = {"Item": vehicle_row} if vehicle_row else {}
            table.update_item.return_value = {}
            table.scan.return_value = {"Items": []}
        elif "model-manifest" in table_name:
            # _load_model_manifest uses scan with FilterExpression
            table.scan.return_value = {"Items": [model_row] if model_row else []}
            table.get_item.return_value = {}
        elif "vehicle-certificates" in table_name:
            table.put_item.return_value = {}
            table.get_item.return_value = {
                "Item": {"vin": _SYNTHETIC_VIN, "certificateId": "cert-abc"}
            }
        else:
            table.scan.return_value = {"Items": []}
            table.get_item.return_value = {}
        _tables[table_name] = table
        return table

    ddb_resource.Table.side_effect = _table_factory
    return ddb_resource


def _make_iot_mock(thing_exists=False):
    """Return a mock IoT client with typed exception classes."""
    iot = MagicMock()

    # Wire exception classes so isinstance/try-except checks work
    ResourceNotFoundException = type("ResourceNotFoundException", (Exception,), {})
    ResourceAlreadyExistsException = type("ResourceAlreadyExistsException", (Exception,), {})
    iot.exceptions.ResourceNotFoundException = ResourceNotFoundException
    iot.exceptions.ResourceAlreadyExistsException = ResourceAlreadyExistsException

    if thing_exists:
        iot.describe_thing.return_value = {"thingName": _SYNTHETIC_VIN}
    else:
        iot.describe_thing.side_effect = ResourceNotFoundException("thing not found")

    iot.create_keys_and_certificate.return_value = {
        "certificateId": "cert-test-id",
        "certificateArn": "arn:aws:iot:us-west-2:123456789012:cert/cert-test-id",
        "certificatePem": _FAKE_CERT_PEM,
        "keyPair": {
            "PublicKey": _FAKE_PUBLIC_KEY,
            "PrivateKey": _FAKE_PRIVATE_KEY,
        },
    }
    iot.create_thing.return_value = {}
    iot.create_policy.side_effect = ResourceAlreadyExistsException("already exists")
    iot.get_policy.return_value = {
        "policyDocument": json.dumps(backfill._SHARED_POLICY_DOCUMENT)
    }
    iot.attach_thing_principal.return_value = {}
    iot.attach_principal_policy.return_value = {}
    return iot


class TestBackfillVehVo001Cert(unittest.TestCase):

    # ── BF1: dry-run against a bare row → prints plan, writes nothing ──────
    def test_bf1_dry_run_bare_row_no_writes(self):
        """BF1: dry-run against a bare row → prints plan, writes nothing.

        Verifies that with --apply absent, the script reads the vehicle row and
        model manifest, prints a plan, and makes NO DDB writes or IoT mutations.
        """
        ddb_resource = _make_ddb_resource_mock(_BARE_VEHICLE_ROW, _MODEL_ROW)
        iot_mock = _make_iot_mock(thing_exists=False)

        env = {
            "AWS_REGION": "us-west-2",
            "AWS_PROFILE": "default",
            "DEPLOYMENT_STAGE": _STAGE,
        }
        with patch.dict(os.environ, env):
            with patch("backfill_veh_vo_001_cert.boto3") as mock_boto3:
                mock_session = MagicMock()
                mock_boto3.Session.return_value = mock_session
                mock_session.resource.return_value = ddb_resource
                mock_session.client.return_value = iot_mock

                with patch("sys.argv", [
                    "backfill_veh_vo_001_cert.py",
                    "--vin", _SYNTHETIC_VIN,
                    "--vehicle-id", _SYNTHETIC_VEHICLE_ID,
                    # no --apply → dry-run
                ]):
                    with self.assertRaises(SystemExit) as ctx:
                        backfill.main()
                    self.assertEqual(ctx.exception.code, 0, "dry-run should exit 0")

        # Verify NO writes occurred — update_item and put_item must not have been called
        vehicles_table = ddb_resource.Table(f"cms-{_STAGE}-storage-vehicles")
        vehicles_table.update_item.assert_not_called()

        cert_table = ddb_resource.Table(f"cms-{_STAGE}-storage-vehicle-certificates")
        cert_table.put_item.assert_not_called()

        # IoT minting calls must not have been invoked
        iot_mock.create_keys_and_certificate.assert_not_called()
        iot_mock.create_thing.assert_not_called()
        iot_mock.attach_thing_principal.assert_not_called()
        iot_mock.attach_principal_policy.assert_not_called()

    # ── BF2: --apply against a bare row → full write path exercised ────────
    def test_bf2_apply_bare_row_writes_all(self):
        """BF2: --apply against a bare row → row updated, cert row written, IoT calls invoked.

        Verifies that with --apply, the script:
        - calls create_keys_and_certificate
        - calls create_thing with the VIN
        - calls attach_thing_principal and attach_principal_policy
        - calls update_item on the vehicles table
        - calls put_item on the certificate table
        """
        # Build mocks: first get_item returns bare row (idempotency check),
        # second get_item (verification) returns updated row.
        ddb_resource = _make_ddb_resource_mock(_BARE_VEHICLE_ROW, _MODEL_ROW)
        iot_mock = _make_iot_mock(thing_exists=False)

        # After writing, describe_thing succeeds
        iot_mock.describe_thing.side_effect = None
        iot_mock.describe_thing.return_value = {"thingName": _SYNTHETIC_VIN}

        # Vehicles table: first call returns bare row, second call (verify) returns updated row
        updated_row = {
            **_BARE_VEHICLE_ROW,
            "certificateId": "cert-test-id",
            "modelManifestName": "CMS-Fleet-Default",
        }
        vehicles_table = ddb_resource.Table(f"cms-{_STAGE}-storage-vehicles")
        vehicles_table.get_item.side_effect = [
            {"Item": _BARE_VEHICLE_ROW},   # first call: initial idempotency read
            {"Item": updated_row},          # second call: post-write verification
        ]

        # Cert table
        cert_table = ddb_resource.Table(f"cms-{_STAGE}-storage-vehicle-certificates")
        cert_table.put_item.return_value = {}
        cert_table.get_item.return_value = {
            "Item": {"vin": _SYNTHETIC_VIN, "certificateId": "cert-test-id"}
        }

        env = {
            "AWS_REGION": "us-west-2",
            "AWS_PROFILE": "default",
            "DEPLOYMENT_STAGE": _STAGE,
        }
        with patch.dict(os.environ, env):
            with patch("backfill_veh_vo_001_cert.boto3") as mock_boto3:
                mock_session = MagicMock()
                mock_boto3.Session.return_value = mock_session
                mock_session.resource.return_value = ddb_resource
                mock_session.client.return_value = iot_mock

                with patch("sys.argv", [
                    "backfill_veh_vo_001_cert.py",
                    "--vin", _SYNTHETIC_VIN,
                    "--vehicle-id", _SYNTHETIC_VEHICLE_ID,
                    "--apply",
                ]):
                    with self.assertRaises(SystemExit) as ctx:
                        backfill.main()
                    self.assertEqual(ctx.exception.code, 0, "--apply should exit 0 on success")

        # Verify IoT cert was minted
        iot_mock.create_keys_and_certificate.assert_called_once_with(setAsActive=True)
        iot_mock.create_thing.assert_called_once_with(thingName=_SYNTHETIC_VIN)
        iot_mock.attach_thing_principal.assert_called()
        iot_mock.attach_principal_policy.assert_called()

        # Verify DDB writes
        vehicles_table.update_item.assert_called()
        call_kwargs = vehicles_table.update_item.call_args
        self.assertEqual(call_kwargs.kwargs["Key"], {"vehicleId": _SYNTHETIC_VEHICLE_ID})

        cert_table.put_item.assert_called()

    # ── BF5: verify path keys cert-table get_item on vehicleId, not vin ────
    def test_bf5_verify_cert_read_keys_on_vehicle_id(self):
        """BF5: _verify() must fetch the cert row by `vehicleId`, not `vin`.

        Regression guard for backlog row `Backfill verify key` P3
        (2026-08-29). The cert table's partition key is `vehicleId` (see
        deployment/stacks/storage_stack.py VehicleCertificatesTable) — an
        earlier iteration keyed the verify-path get_item on `vin`, which
        raised ValidationException *after* every write completed, exiting
        the script non-zero on the verify step while all mutations landed.

        This test asserts the Key argument, not just that get_item was
        called — the pre-fix code called get_item too, it just called it
        with the wrong Key. See spec-workflow.md 'Verify commands must
        actually validate the output' — the assertion has to derive from
        the resource shape, not from the design.
        """
        ddb_resource = _make_ddb_resource_mock(_BARE_VEHICLE_ROW, _MODEL_ROW)
        iot_mock = _make_iot_mock(thing_exists=False)
        iot_mock.describe_thing.side_effect = None
        iot_mock.describe_thing.return_value = {"thingName": _SYNTHETIC_VIN}

        updated_row = {
            **_BARE_VEHICLE_ROW,
            "certificateId": "cert-test-id",
            "modelManifestName": "CMS-Fleet-Default",
        }
        vehicles_table = ddb_resource.Table(f"cms-{_STAGE}-storage-vehicles")
        vehicles_table.get_item.side_effect = [
            {"Item": _BARE_VEHICLE_ROW},
            {"Item": updated_row},
        ]

        cert_table = ddb_resource.Table(f"cms-{_STAGE}-storage-vehicle-certificates")
        cert_table.put_item.return_value = {}
        cert_table.get_item.return_value = {
            "Item": {"vehicleId": _SYNTHETIC_VEHICLE_ID, "vin": _SYNTHETIC_VIN, "certificateId": "cert-test-id"}
        }

        env = {
            "AWS_REGION": "us-west-2",
            "AWS_PROFILE": "default",
            "DEPLOYMENT_STAGE": _STAGE,
        }
        with patch.dict(os.environ, env):
            with patch("backfill_veh_vo_001_cert.boto3") as mock_boto3:
                mock_session = MagicMock()
                mock_boto3.Session.return_value = mock_session
                mock_session.resource.return_value = ddb_resource
                mock_session.client.return_value = iot_mock

                with patch("sys.argv", [
                    "backfill_veh_vo_001_cert.py",
                    "--vin", _SYNTHETIC_VIN,
                    "--vehicle-id", _SYNTHETIC_VEHICLE_ID,
                    "--apply",
                ]):
                    with self.assertRaises(SystemExit) as ctx:
                        backfill.main()
                    self.assertEqual(ctx.exception.code, 0, "--apply should exit 0 on success")

        # Assert cert_table.get_item was called with a Key keyed on vehicleId,
        # not vin. This is THE assertion that catches the P3 defect — the
        # pre-fix code called get_item too, just with the wrong Key.
        cert_table.get_item.assert_called()
        cert_get_call = cert_table.get_item.call_args
        self.assertEqual(
            cert_get_call.kwargs["Key"],
            {"vehicleId": _SYNTHETIC_VEHICLE_ID},
            f"cert-table get_item must key on vehicleId (the partition key), "
            f"not vin. Actual: {cert_get_call.kwargs.get('Key')}",
        )

    # ── BF3: row already has modelManifestName → exit 0, no writes ─────────
    def test_bf3_already_has_model_no_writes(self):
        """BF3: --apply against a row already carrying modelManifestName → exit 0, no writes.

        Verifies idempotency: if the row already has modelManifestName set, the
        script exits 0 immediately without any IoT or DDB writes.
        """
        ddb_resource = _make_ddb_resource_mock(_MODEL_VEHICLE_ROW, _MODEL_ROW)
        iot_mock = _make_iot_mock(thing_exists=True)
        iot_mock.describe_thing.side_effect = None
        iot_mock.describe_thing.return_value = {"thingName": _SYNTHETIC_VIN}

        env = {
            "AWS_REGION": "us-west-2",
            "AWS_PROFILE": "default",
            "DEPLOYMENT_STAGE": _STAGE,
        }
        with patch.dict(os.environ, env):
            with patch("backfill_veh_vo_001_cert.boto3") as mock_boto3:
                mock_session = MagicMock()
                mock_boto3.Session.return_value = mock_session
                mock_session.resource.return_value = ddb_resource
                mock_session.client.return_value = iot_mock

                with patch("sys.argv", [
                    "backfill_veh_vo_001_cert.py",
                    "--vin", _SYNTHETIC_VIN,
                    "--vehicle-id", _SYNTHETIC_VEHICLE_ID,
                    "--apply",
                ]):
                    with self.assertRaises(SystemExit) as ctx:
                        backfill.main()
                    self.assertEqual(ctx.exception.code, 0, "should exit 0 for already-backfilled")

        # No IoT or DDB write calls
        iot_mock.create_keys_and_certificate.assert_not_called()
        iot_mock.create_thing.assert_not_called()
        vehicles_table = ddb_resource.Table(f"cms-{_STAGE}-storage-vehicles")
        vehicles_table.update_item.assert_not_called()

    # ── BF4: CMS-Fleet-Default absent → exit non-zero, actionable message ──
    def test_bf4_model_absent_exits_nonzero(self):
        """BF4: --apply with CMS-Fleet-Default absent → exit non-zero, actionable message.

        Verifies that when the model manifest table returns no ACTIVE rows, the
        script exits with a non-zero code and prints an actionable message
        referencing seed_model_manifests.py.
        """
        # Pass None for model_row so the mock returns no items
        ddb_resource = _make_ddb_resource_mock(_BARE_VEHICLE_ROW, None)
        iot_mock = _make_iot_mock(thing_exists=False)

        env = {
            "AWS_REGION": "us-west-2",
            "AWS_PROFILE": "default",
            "DEPLOYMENT_STAGE": _STAGE,
        }
        output_lines = []
        original_print = print

        def _capture_print(*args, **kwargs):
            line = " ".join(str(a) for a in args)
            output_lines.append(line)
            original_print(*args, **kwargs)

        with patch.dict(os.environ, env):
            with patch("backfill_veh_vo_001_cert.boto3") as mock_boto3:
                mock_session = MagicMock()
                mock_boto3.Session.return_value = mock_session
                mock_session.resource.return_value = ddb_resource
                mock_session.client.return_value = iot_mock

                with patch("sys.argv", [
                    "backfill_veh_vo_001_cert.py",
                    "--vin", _SYNTHETIC_VIN,
                    "--vehicle-id", _SYNTHETIC_VEHICLE_ID,
                    "--apply",
                ]):
                    with patch("builtins.print", side_effect=_capture_print):
                        with self.assertRaises(SystemExit) as ctx:
                            backfill.main()
                        self.assertNotEqual(
                            ctx.exception.code, 0,
                            "absent model manifest should cause non-zero exit",
                        )

        # Verify actionable message references seed_model_manifests.py
        all_output = "\n".join(output_lines)
        self.assertIn(
            "seed_model_manifests", all_output,
            "Error message should reference seed_model_manifests.py",
        )

        # No IoT or DDB write calls
        iot_mock.create_keys_and_certificate.assert_not_called()
        vehicles_table = ddb_resource.Table(f"cms-{_STAGE}-storage-vehicles")
        vehicles_table.update_item.assert_not_called()


if __name__ == "__main__":
    unittest.main()
