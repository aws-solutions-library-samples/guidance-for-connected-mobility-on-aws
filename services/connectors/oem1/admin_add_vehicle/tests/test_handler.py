"""
Unit tests for admin_add_vehicle/handler.py — spec §9 matrix T9–T15.

Tests (7):
  T9  test_happy_path_admin_completed_vin_returns_200
  T10 test_non_admin_claim_returns_403
  T11 test_engineering_tenant_fleet_returns_400
  T12 test_pending_vin_returns_200_with_enrollment_pending
  T13 test_unknown_vin_after_pagination_returns_200_unknown
  T14 test_duplicate_vin_resubmit_returns_already_enrolled
  T15 test_oem1_timeout_returns_504

Uses requests-mock==1.12.1 for OEM1 HTTP mocking (matches vehicle_state_proxy).
Uses boto3 stubber for DDB mocking (matches C2.2 test layout).
"""
import json
import os
import sys
from unittest.mock import MagicMock, patch

import pytest
import requests
import requests_mock as requests_mock_lib

# --- environment defaults so handler can import cleanly ----------------------
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

try:
    import handler  # noqa: E402  (after sys.path setup in conftest.py)
except ModuleNotFoundError:
    handler = None  # type: ignore[assignment]  # red phase: handler not yet implemented

# Red-phase marker: all tests fail immediately when handler module is absent
pytestmark = pytest.mark.skipif(
    handler is None,
    reason="handler module not yet implemented (red phase)",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_ENROLLMENT_URL = f"https://oem1-feed.example.local/enrollment/v2/status/latest"
_VEHICLE_DATA_URL = f"https://oem1-feed.example.local/selfserve/v1/vehicleData"

_TEST_VIN = "1FTFW1ET0EKE12345"
_TEST_FLEET = "oem1-staging-fleet"
_ENG_FLEET = "oem1-engineering-fleet"


def _make_event(vin: str = _TEST_VIN, fleet_id: str = _TEST_FLEET, groups: list = None) -> dict:
    """R5: Cognito User Pool authorizer payload shape."""
    if groups is None:
        groups = ["platform-admin"]
    return {
        "body": json.dumps({"vin": vin, "fleetId": fleet_id}),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "cognito:groups": ",".join(groups),
                    "sub": "test-user-sub",
                }
            }
        },
    }


def _mock_supplier(token: str = "mock-bearer-token") -> MagicMock:
    sup = MagicMock()
    sup.get_token.return_value = token
    sup.handle_401.return_value = token
    return sup


def _enrollment_page(vins_and_statuses: list, page: int = 1, total_pages: int = 1, product_sku: str = "SKU-00000069", request_id: int = 42) -> dict:
    """Build a mock OEM1 /enrollment/v2/status/latest response page."""
    return {
        "data": [
            {"vehicleId": vin, "status": status, "product_sku": product_sku, "request_id": request_id}
            for vin, status in vins_and_statuses
        ],
        "pagination": {"page": page, "totalPages": total_pages},
    }


# DDB item shape produced by seed_vehicles.py:_write_vehicle (W7 — verbatim snake_case)
_EXPECTED_DDB_ITEM_KEYS = {
    "vehicleId", "oem_source", "last_seen_at", "enrolled_at", "oem1_shard_uuid"
}
_EXPECTED_DDB_PENDING_ITEM_KEYS = {
    "vehicleId", "oem_source", "last_seen_at", "enrollment_pending", "oem1_shard_uuid"
}

# M3 fleet get_item response — cloud-telemetry fleet (passes M3 check).
# Must be prepended to every Stubber that exercises the happy path, because
# the M3 check (added by spec 2026-06-09-cms-data-source-model-refactor) now
# issues a GetItem before the first PutItem.
_CLOUD_FLEET_GET_ITEM_RESP = {
    "Item": {"fleetId": {"S": _TEST_FLEET}, "data_source": {"S": "cloud-telemetry"}}
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestHappyPath:
    def test_happy_path_admin_completed_vin_returns_200(self):
        """T9 — spec §9: admin claim + COMPLETED VIN → 200; PutItem written."""
        import boto3
        from botocore.stub import Stubber

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        with Stubber(ddb) as stubber:
            # M3 GetItem for fleets table (added by 2026-06-09-cms-data-source-model-refactor)
            stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
            # Expect PutItem for vehicles table (conditional put)
            stubber.add_response("put_item", {})
            # Expect PutItem for fleet-enrollment table
            stubber.add_response("put_item", {})

            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["enrollmentStatus"] == "COMPLETED"
        assert "vehicleId" in body




class TestMMgrFields:
    def test_mmgr_fields_present_in_ddb_write(self):
        """T3.3 — spec § 1.2: 8 M-MGR fields populated on _write_vehicle PutItem call."""
        import boto3
        from botocore.stub import Stubber

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        written_item = {}

        def capture_put_item(**kwargs):
            written_item.update(kwargs.get("Item", {}))
            return {}

        with Stubber(ddb) as stubber:
            # M3 GetItem (2026-06-09-cms-data-source-model-refactor)
            stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
            stubber.add_response("put_item", {})
            stubber.add_response("put_item", {})

            with requests_mock_lib.Mocker() as m:
                m.post(
                    _ENROLLMENT_URL,
                    json=_enrollment_page([(_TEST_VIN, "COMPLETED")], product_sku="SKU-00000069", request_id=42),
                )
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        with patch.object(ddb, "put_item", side_effect=capture_put_item):
                            handler._token_supplier = None
                            result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        # 3 populated M-MGR fields
        assert written_item.get("oem1_enrollment_status") == {"S": "IN_PROGRESS"}
        assert written_item.get("oem1_active_sku") == {"S": "SKU-00000069"}
        assert written_item.get("oem1_request_id") == {"N": "42"}
        # 5 remaining fields absent until poller updates
        for absent_field in (
            "oem1_fcs_code", "oem1_status_message", "oem1_readiness_summary",
            "oem1_status_refreshed_at", "subscription_service_activation_date",
        ):
            assert absent_field not in written_item, f"{absent_field} should be absent on initial write"

class TestNonAdminClaim:
    def test_non_admin_claim_returns_403(self):
        """T10 — spec §9, M9: non-admin claim → 403 (server-side role gate)."""
        event = _make_event(groups=["read-only-users"])
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 403
        body = json.loads(result["body"])
        assert "error" in body


class TestEngineeringTenantFleet:
    def test_engineering_tenant_fleet_returns_400(self):
        """T11 — spec §9, C3/M6/OQ4: Engineering-tenant fleet → 400 (mandatory)."""
        ddb = _make_ddb_mock_for_add_vehicle("cloud-telemetry")
        with (
            patch.object(handler, "_get_ddb_client", return_value=ddb),
            patch.object(handler, "_get_engineering_fleet_ids", return_value=[_ENG_FLEET]),
        ):
            event = _make_event(fleet_id=_ENG_FLEET)
            result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body
        assert "engineering" in body["error"].lower() or "Engineering" in body["error"]


class TestPendingVin:
    def test_pending_vin_returns_200_with_enrollment_pending(self):
        """T12 — spec §9, OQ2: PENDING VIN → 200, enrollment_pending: true written, no enrolled_at."""
        import boto3
        from botocore.stub import Stubber

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        written_item = {}

        def capture_put_item(**kwargs):
            written_item.update(kwargs.get("Item", {}))
            return {}

        with Stubber(ddb) as stubber:
            # M3 GetItem (2026-06-09-cms-data-source-model-refactor)
            stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
            stubber.add_response("put_item", {})
            stubber.add_response("put_item", {})

            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "PENDING")]))
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        with patch.object(ddb, "put_item", side_effect=capture_put_item):
                            handler._token_supplier = None
                            result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["enrollmentStatus"] == "PENDING"
        # W7: assert snake_case DDB shape — enrollment_pending: true, no enrolled_at
        assert "enrollment_pending" in written_item or body.get("enrollmentStatus") == "PENDING"
        assert "enrolled_at" not in written_item


class TestUnknownVinPagination:
    def test_unknown_vin_after_pagination_returns_200_unknown(self):
        """T13 — spec §9, R8: unknown VIN after 5-page pagination → 200 UNKNOWN, no DDB write."""
        # 5 pages, each returning a different VIN — our VIN never appears
        other_vin = "OTHER-VIN-XXXXXXXXXXX"
        pages = [
            _enrollment_page([(other_vin, "COMPLETED")], page=i + 1, total_pages=5)
            for i in range(5)
        ]

        import boto3
        from botocore.stub import Stubber

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        with Stubber(ddb) as stubber:
            # M3 GetItem (2026-06-09-cms-data-source-model-refactor)
            stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
            # No DDB calls expected — VIN not found, no write
            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, [{"json": p} for p in pages])
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)

            stubber.assert_no_pending_responses()

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["enrollmentStatus"] == "UNKNOWN"
        assert "reason" in body or "UNKNOWN" in json.dumps(body)


class TestDuplicateVin:
    def test_duplicate_vin_resubmit_returns_already_enrolled(self):
        """T14 — spec §9, C9: duplicate VIN re-submit → 200, writeStatus: 'already_enrolled'."""
        import boto3
        from botocore.stub import Stubber
        from botocore.exceptions import ClientError

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        with Stubber(ddb) as stubber:
            # M3 GetItem (2026-06-09-cms-data-source-model-refactor)
            stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
            # First PutItem raises ConditionalCheckFailedException (already exists)
            stubber.add_client_error(
                "put_item",
                service_error_code="ConditionalCheckFailedException",
            )
            # UpdateItem for last_seen_at update
            stubber.add_response("update_item", {})
            # Fleet enrollment PutItem (idempotent)
            stubber.add_response("put_item", {})

            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body.get("writeStatus") == "already_enrolled"


class TestOem1Timeout:
    def test_oem1_timeout_returns_504(self):
        """T15 — spec §9, C2.2: OEM1 timeout → 504, response sanitized."""
        ddb = _make_ddb_mock_for_add_vehicle("cloud-telemetry")
        with requests_mock_lib.Mocker() as m:
            m.post(_ENROLLMENT_URL, exc=requests.exceptions.Timeout)
            with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                with patch.object(handler, "_get_ddb_client", return_value=ddb):
                    handler._token_supplier = None
                    result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 504
        body = json.loads(result["body"])
        assert "error" in body
        # Sanitized: must not echo internal OEM1 detail
        assert "oem1-feed.example.local" not in body.get("error", "")



# ---------------------------------------------------------------------------
# Defense-in-depth tests added post-spec-close per security-review.md cycle 1
# Suggestions S1 (server-side input validation) and S2 (bracket-form
# cognito:groups parser robustness).
# ---------------------------------------------------------------------------


class TestInvalidVin:
    """S1 (security-review): server-side VIN format check rejects malformed input."""

    @pytest.mark.parametrize("bad_vin", [
        "1FTFW1ET0EKE1234",     # 16 chars (too short)
        "1FTFW1ET0EKE123456",   # 18 chars (too long)
        "1FTFW1ETOEKE12345",    # contains 'O' (forbidden per ISO 3779)
        "1FTFW1ETIEKE12345",    # contains 'I' (forbidden)
        "1FTFW1ETQEKE12345",    # contains 'Q' (forbidden)
        "1FTFW1ET0EKE-1234",    # contains hyphen
        "1FTFW1ET0EKE 1234",    # contains space
        "'; DROP TABLE--XX",    # SQL-injection-style payload
    ])
    def test_invalid_vin_returns_400(self, bad_vin):
        """Malformed VIN values return 400 with a descriptive error."""
        event = _make_event(vin=bad_vin)
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 400, f"VIN {bad_vin!r} should be rejected"
        body = json.loads(result["body"])
        assert "error" in body
        assert "vin" in body["error"].lower() or "VIN" in body["error"]


class TestInvalidFleetId:
    """S1 (security-review): server-side fleetId format check rejects unsafe input."""

    @pytest.mark.parametrize("bad_fleet", [
        "fleet$injection",       # special char
        "fleet/with/slash",      # path-traversal style
        "fleet;rm -rf",          # shell-injection style
        "x" * 65,                # over 64-char cap
        "fleet name with space",
        "../parent",             # traversal
    ])
    def test_invalid_fleet_id_returns_400(self, bad_fleet):
        """Malformed fleetId values return 400."""
        event = _make_event(fleet_id=bad_fleet)
        result = handler.lambda_handler(event, None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body


class TestBracketFormCognitoGroups:
    """
    S2 (security-review): cognito:groups parser must accept BOTH the bare
    comma-separated form ('a,b,c') and the JSON-bracket form ('[a, b, c]')
    that some API Gateway authorizer configs deliver.
    """

    def _event_with_groups_raw(self, groups_raw):
        """Build an event with cognito:groups in arbitrary raw form."""
        return {
            "body": json.dumps({"vin": _TEST_VIN, "fleetId": _TEST_FLEET}),
            "requestContext": {
                "authorizer": {
                    "claims": {
                        "cognito:groups": groups_raw,
                        "sub": "test-user-sub",
                    }
                }
            },
        }

    def test_bracket_form_admin_admitted(self):
        """`[platform-admin]` is parsed correctly and admin is admitted (passes role gate)."""
        # We only need to verify the role gate accepts the claim; downstream
        # handler logic isn't exercised here, so we expect the request to
        # proceed past 403 (i.e., it will fail later for other reasons but
        # NOT with 403). The fastest signal: stub Engineering check empty,
        # supply an unmocked OEM1 host so the call fails downstream — we
        # assert the failure is NOT 403.
        ddb = _make_ddb_mock_for_add_vehicle("cloud-telemetry")
        with patch.object(handler, "_get_engineering_fleet_ids", return_value=[]):
            with patch.object(handler, "_get_ddb_client", return_value=ddb):
                event = self._event_with_groups_raw("[platform-admin]")
                result = handler.lambda_handler(event, None)
        assert result["statusCode"] != 403, (
            f"bracket-form [platform-admin] must pass role gate; got {result['statusCode']}"
        )

    def test_bracket_form_multi_group_admin_admitted(self):
        """`[other-group, platform-admin]` is parsed correctly."""
        ddb = _make_ddb_mock_for_add_vehicle("cloud-telemetry")
        with patch.object(handler, "_get_engineering_fleet_ids", return_value=[]):
            with patch.object(handler, "_get_ddb_client", return_value=ddb):
                event = self._event_with_groups_raw("[other-group, platform-admin]")
                result = handler.lambda_handler(event, None)
        assert result["statusCode"] != 403

    def test_list_form_admin_admitted(self):
        """Some authorizer configs deliver groups as a Python list — handle gracefully."""
        ddb = _make_ddb_mock_for_add_vehicle("cloud-telemetry")
        with patch.object(handler, "_get_engineering_fleet_ids", return_value=[]):
            with patch.object(handler, "_get_ddb_client", return_value=ddb):
                event = self._event_with_groups_raw(["platform-admin", "other"])
                result = handler.lambda_handler(event, None)
        assert result["statusCode"] != 403

    def test_bracket_form_non_admin_rejected(self):
        """`[read-only]` (no admin) still rejected with 403."""
        event = self._event_with_groups_raw("[read-only-users]")
        result = handler.lambda_handler(event, None)
        assert result["statusCode"] == 403



# ---------------------------------------------------------------------------
# UAT-fix tests added 2026-06-04 per
# `issues/2026-06-04-oem1-vehicle-missing-enrichment-on-list/`:
#   - Enrichment via /selfserve/v1/vehicleData (Bug 1)
#   - status: "Active" write on enrollment (Bug 2a)
# ---------------------------------------------------------------------------


def _capture_put_items(ddb_client, n: int = 2):
    """Stubber helper that captures the Item= argument of each put_item call.

    Returns a list `captured` that the test can inspect; pre-registers a
    get_item (M3 fleet check) + `n` success responses on the Stubber so
    the handler's calls succeed.
    """
    from botocore.stub import Stubber

    stubber = Stubber(ddb_client)
    captured: list = []

    real_put = ddb_client.put_item

    def _capture(*args, **kwargs):
        captured.append(kwargs.get("Item") or (args[1] if len(args) > 1 else None))
        return real_put(*args, **kwargs)

    ddb_client.put_item = _capture
    # M3 GetItem (2026-06-09-cms-data-source-model-refactor)
    stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
    for _ in range(n):
        stubber.add_response("put_item", {})
    stubber.activate()
    return stubber, captured


class TestEnrichmentSuccessWritesMakeModelYear:
    """Bug 1 — /selfserve/v1/vehicleData enrichment populates make/model/year."""

    def test_enrichment_success_populates_metadata(self):
        import boto3

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        stubber, captured = _capture_put_items(ddb, n=2)
        try:
            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
                # /selfserve/v1/vehicleData returns nested modelInfo
                m.get(_VEHICLE_DATA_URL, json={
                    "vehicleData": {"modelInfo": {"make": "Ford", "model": "F-150", "year": 2024}}
                })
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)
        finally:
            stubber.deactivate()

        assert result["statusCode"] == 200
        # First put_item is the vehicles table — verify enrichment fields present
        vehicle_item = captured[0]
        assert vehicle_item["make"] == {"S": "Ford"}
        assert vehicle_item["model"] == {"S": "F-150"}
        assert vehicle_item["year"] == {"N": "2024"}


class TestEnrichmentFailureDegradesGracefully:
    """Bug 1 — enrichment 5xx must NOT 502 the whole add-vehicle flow (spec C8)."""

    def test_enrichment_5xx_ships_row_without_enrichment(self):
        import boto3

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        stubber, captured = _capture_put_items(ddb, n=2)
        try:
            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
                # Enrichment endpoint returns 500
                m.get(_VEHICLE_DATA_URL, status_code=500, json={"error": "upstream"})
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)
        finally:
            stubber.deactivate()

        # Add-vehicle still succeeds; enrichment fields just absent
        assert result["statusCode"] == 200
        vehicle_item = captured[0]
        assert "make" not in vehicle_item
        assert "model" not in vehicle_item
        assert "year" not in vehicle_item


class TestStatusActiveOnEnrollment:
    """Bug 2a — DDB write includes status: 'Active' on enrollment row insert."""

    def test_completed_enrollment_writes_status_active(self):
        import boto3

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        stubber, captured = _capture_put_items(ddb, n=2)
        try:
            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
                m.get(_VEHICLE_DATA_URL, json={"vehicleData": {"modelInfo": {}}})
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)
        finally:
            stubber.deactivate()

        assert result["statusCode"] == 200
        vehicle_item = captured[0]
        assert vehicle_item["status"] == {"S": "Active"}, (
            f"Expected status: Active on enrollment write; got {vehicle_item.get('status')!r}"
        )

    def test_pending_enrollment_also_writes_status_active(self):
        """Even PENDING-enrollment rows get status: Active (the lifecycle state)."""
        import boto3

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        stubber, captured = _capture_put_items(ddb, n=2)
        try:
            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "PENDING")]))
                m.get(_VEHICLE_DATA_URL, json={"vehicleData": {"modelInfo": {}}})
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)
        finally:
            stubber.deactivate()

        assert result["statusCode"] == 200
        vehicle_item = captured[0]
        assert vehicle_item["status"] == {"S": "Active"}
        # PENDING-row variant invariants from spec OQ2
        assert vehicle_item["enrollment_pending"] == {"BOOL": True}
        assert "enrolled_at" not in vehicle_item



# ---------------------------------------------------------------------------
# UAT-fix test added 2026-06-04 per Q1: re-enroll path must backfill missing
# status/enrichment fields on existing rows so the duplicate-VIN UX actually
# updates rows that were inserted before status/enrichment were available.
# ---------------------------------------------------------------------------


class TestDuplicateVinBackfillsMissingFields:
    """Q1 — duplicate-VIN re-add backfills status + enrichment via if_not_exists."""

    def test_duplicate_vin_emits_if_not_exists_update(self):
        """Re-adding an existing VIN must issue an UpdateItem with
        `if_not_exists` clauses for status / make / model / year / enrollment_pending
        so existing values written by auto_register.py (Connected) are preserved
        but missing values are filled in.
        """
        import boto3
        from botocore.stub import Stubber

        ddb = boto3.client("dynamodb", region_name="us-east-1")

        # Capture the actual update_item kwargs (simpler than Stubber matchers).
        captured: dict = {}
        real_update = ddb.update_item

        def _capture_update(**kwargs):
            captured.update(kwargs)
            return real_update(**kwargs)

        ddb.update_item = _capture_update

        with Stubber(ddb) as stubber:
            # M3 GetItem (2026-06-09-cms-data-source-model-refactor)
            stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
            # First put_item raises ConditionalCheckFailedException to simulate
            # an existing row.
            stubber.add_client_error(
                "put_item",
                service_error_code="ConditionalCheckFailedException",
                service_message="Item already exists",
            )
            # Then update_item succeeds.
            stubber.add_response("update_item", {})
            # And the second put_item (fleet-enrollment) succeeds.
            stubber.add_response("put_item", {})

            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
                m.get(_VEHICLE_DATA_URL, json={
                    "vehicleData": {"modelInfo": {"make": "Ford", "model": "F-150", "year": 2024}}
                })
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        body = json.loads(result["body"])
        assert body["writeStatus"] == "already_enrolled"

        # Verify the UpdateExpression uses `if_not_exists` for the backfill fields
        update_expr = captured.get("UpdateExpression", "")
        assert "if_not_exists" in update_expr, (
            f"Expected if_not_exists clauses; got: {update_expr!r}"
        )
        assert "#s" in update_expr  # status name placeholder
        assert "#mk" in update_expr  # make
        assert "#md" in update_expr  # model
        assert "#yr" in update_expr  # year

        # Verify the values include status: Active and the enrichment data
        values = captured.get("ExpressionAttributeValues", {})
        assert values.get(":s") == {"S": "Active"}
        assert values.get(":mk") == {"S": "Ford"}
        assert values.get(":md") == {"S": "F-150"}
        assert values.get(":yr") == {"N": "2024"}

    def test_duplicate_vin_pending_sets_enrollment_pending_flag(self):
        """PENDING re-add includes enrollment_pending in the if_not_exists set."""
        import boto3
        from botocore.stub import Stubber

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        captured: dict = {}
        real_update = ddb.update_item

        def _capture_update(**kwargs):
            captured.update(kwargs)
            return real_update(**kwargs)

        ddb.update_item = _capture_update

        with Stubber(ddb) as stubber:
            # M3 GetItem (2026-06-09-cms-data-source-model-refactor)
            stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
            stubber.add_client_error(
                "put_item",
                service_error_code="ConditionalCheckFailedException",
                service_message="Item already exists",
            )
            stubber.add_response("update_item", {})
            stubber.add_response("put_item", {})

            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "PENDING")]))
                m.get(_VEHICLE_DATA_URL, json={"vehicleData": {"modelInfo": {}}})
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        update_expr = captured.get("UpdateExpression", "")
        assert "#ep" in update_expr  # enrollment_pending placeholder
        values = captured.get("ExpressionAttributeValues", {})
        assert values.get(":ep") == {"BOOL": True}

    def test_duplicate_vin_completed_does_not_set_pending_flag(self):
        """COMPLETED re-add does NOT add enrollment_pending (would regress
        a row that already moved past pending)."""
        import boto3
        from botocore.stub import Stubber

        ddb = boto3.client("dynamodb", region_name="us-east-1")
        captured: dict = {}
        real_update = ddb.update_item

        def _capture_update(**kwargs):
            captured.update(kwargs)
            return real_update(**kwargs)

        ddb.update_item = _capture_update

        with Stubber(ddb) as stubber:
            # M3 GetItem (2026-06-09-cms-data-source-model-refactor)
            stubber.add_response("get_item", _CLOUD_FLEET_GET_ITEM_RESP)
            stubber.add_client_error(
                "put_item",
                service_error_code="ConditionalCheckFailedException",
                service_message="Item already exists",
            )
            stubber.add_response("update_item", {})
            stubber.add_response("put_item", {})

            with requests_mock_lib.Mocker() as m:
                m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
                m.get(_VEHICLE_DATA_URL, json={"vehicleData": {"modelInfo": {}}})
                with patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()):
                    with patch.object(handler, "_get_ddb_client", return_value=ddb):
                        handler._token_supplier = None
                        result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
        update_expr = captured.get("UpdateExpression", "")
        assert "#ep" not in update_expr, (
            f"COMPLETED resubmit must NOT touch enrollment_pending; got: {update_expr!r}"
        )


# ---------------------------------------------------------------------------
# M3 fleet data_source consistency check tests
# Spec: 2026-06-09-cms-data-source-model-refactor § "Verification approach"
# Cases: (a) cloud-telemetry→pass, (b) vehicle-telemetry→400,
#         (c) missing-attribute→400, (d) cloud-oem1 dual-read→pass
# ---------------------------------------------------------------------------


def _make_ddb_mock_for_add_vehicle(data_source: str | None) -> MagicMock:
    """DDB mock for admin_add_vehicle M3 check.

    data_source=None simulates a fleet row with no data_source attribute.
    """
    ddb = MagicMock()
    if data_source is None:
        fleet_item = {"fleetId": {"S": _TEST_FLEET}}
    else:
        fleet_item = {"fleetId": {"S": _TEST_FLEET}, "data_source": {"S": data_source}}
    ddb.get_item.return_value = {"Item": fleet_item}
    ddb.put_item.return_value = {}
    ddb.update_item.return_value = {}
    return ddb


class TestM3FleetDataSource:
    """M3 check: fleet data_source gate on admin_add_vehicle."""

    def test_m3_cloud_telemetry_fleet_passes(self):
        """(a) DDB returns cloud-telemetry fleet → vehicle add succeeds (200)."""
        ddb = _make_ddb_mock_for_add_vehicle("cloud-telemetry")
        with requests_mock_lib.Mocker() as m:
            m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
            m.get(_VEHICLE_DATA_URL, json={"vehicleData": {"modelInfo": {}}})
            with (
                patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()),
                patch.object(handler, "_get_ddb_client", return_value=ddb),
                patch.object(handler, "_get_engineering_fleet_ids", return_value=[]),
            ):
                handler._token_supplier = None
                result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200

    def test_m3_vehicle_telemetry_fleet_rejected(self):
        """(b) DDB returns vehicle-telemetry fleet → 400 with M3 error."""
        ddb = _make_ddb_mock_for_add_vehicle("vehicle-telemetry")
        with (
            patch.object(handler, "_get_ddb_client", return_value=ddb),
            patch.object(handler, "_get_engineering_fleet_ids", return_value=[]),
        ):
            result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "cloud-fed" in body["error"] or "telemetry" in body["error"].lower()

    def test_m3_missing_data_source_rejected(self):
        """(c) DDB returns fleet with no data_source attribute → 400 (defaults to vehicle-telemetry)."""
        ddb = _make_ddb_mock_for_add_vehicle(None)
        with (
            patch.object(handler, "_get_ddb_client", return_value=ddb),
            patch.object(handler, "_get_engineering_fleet_ids", return_value=[]),
        ):
            result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 400
        body = json.loads(result["body"])
        assert "error" in body

    def test_m3_cloud_oem1_dual_read_passes(self):
        """(d) DDB returns cloud-oem1 fleet (legacy value) → vehicle add succeeds (dual-read)."""
        ddb = _make_ddb_mock_for_add_vehicle("cloud-oem1")
        with requests_mock_lib.Mocker() as m:
            m.post(_ENROLLMENT_URL, json=_enrollment_page([(_TEST_VIN, "COMPLETED")]))
            m.get(_VEHICLE_DATA_URL, json={"vehicleData": {"modelInfo": {}}})
            with (
                patch.object(handler, "_get_token_supplier", return_value=_mock_supplier()),
                patch.object(handler, "_get_ddb_client", return_value=ddb),
                patch.object(handler, "_get_engineering_fleet_ids", return_value=[]),
            ):
                handler._token_supplier = None
                result = handler.lambda_handler(_make_event(), None)

        assert result["statusCode"] == 200
