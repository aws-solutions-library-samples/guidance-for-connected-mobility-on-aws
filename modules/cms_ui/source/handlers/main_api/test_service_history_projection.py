"""Tests for the GET /api/v1/service-history response-field allowlist.

Context: the route returned raw DynamoDB items, so `tenantId` — carrying a customer
brand name on 112 prod rows — was handed to every caller even though no UI consumer
reads it. Found by the 2026-08-10 prod exposure audit
(issues/2026-08-10-cms-demo-external-exposure/).

These tests assert the projection strips what it must AND preserves everything the
sole GET consumer reads. The second half matters as much as the first: an allowlist
that quietly drops a field the dashboard needs trades an exposure for an outage.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

os.environ.setdefault("AWS_REGION", "us-east-1")

from index import (  # noqa: E402
    _SERVICE_HISTORY_RESPONSE_FIELDS,
    _project_service_records,
)

# Every field read from a service-history row by
# components/alerts/maintenance/ServiceDashboard.tsx — the only GET consumer.
# Derived by enumerating `r.<field>` reads in its record loop.
_UI_CONSUMED_FIELDS = [
    "alertId",
    "category",
    "cost",
    "createdAt",
    "dealerId",
    "description",
    "estimatedCost",
    "estimatedDuration",
    "notes",
    "provider",
    "serviceDate",
    "serviceDetails",
    "serviceId",
    "serviceType",
    "status",
    "vehicleId",
    "vin",
]


def _realistic_row(**overrides):
    """A row shaped like the prod table, including the fields that must be stripped.

    The stripped-field VALUES here are deliberately synthetic. The real prod rows
    carry a customer brand name, an internal corporate email address and the AWS
    account id, and
    this file SHIPS to the public mirror — a fixture that embeds the canary it tests
    for is how canaries get published (see ~/.kiro/steering/public-mirror-publish.md
    on `test_secret_scan.py`, which did exactly that). The projection is
    value-agnostic, so synthetic placeholders test it identically.
    """
    row = {
        "vehicleId": "VEH-1780081115",
        "serviceId": "svc-abc123",
        "vin": "1FTFW1ET5DFC10312",
        "serviceType": "INSPECTION",
        "category": "SERVICE",
        "status": "COMPLETED",
        "description": "Brake inspection",
        "notes": "Pads within tolerance",
        "serviceDate": "2026-07-01",
        "createdAt": "2026-07-01T10:00:00Z",
        "updatedAt": "2026-07-01T10:00:00Z",
        "cost": {"totalCost": 240},
        "estimatedCost": 240,
        "estimatedDuration": 90,
        "provider": "Lone Star Truck Center",
        "dealerId": "D-1001",
        "alertId": "alert-9",
        "serviceDetails": {"recallId": "24V-123", "recallSeverity": "HIGH"},
        # ── Fields that must NOT reach the response ──
        "tenantId": "example-tenant-canary",
        "internalOwnerEmail": "owner@example.com",
        "sourceAccountId": "000000000000",
    }
    row.update(overrides)
    return row


class TestProjectionStripsSensitiveFields:
    def test_tenant_id_is_stripped(self):
        """The concrete finding: a customer brand name in tenantId must not ship."""
        out = _project_service_records([_realistic_row()])
        assert "tenantId" not in out[0], (
            "tenantId reached the response — this is the exact exposure the "
            "allowlist exists to close."
        )

    def test_brand_value_absent_from_projected_output(self):
        """Value-level assertion, not just key-level."""
        out = _project_service_records([_realistic_row()])
        assert "example-tenant-canary" not in repr(out[0]).lower()

    def test_other_non_allowlisted_fields_stripped(self):
        out = _project_service_records([_realistic_row()])
        for k in ("internalOwnerEmail", "sourceAccountId"):
            assert k not in out[0]

    def test_unknown_future_field_is_stripped_by_default(self):
        """Allowlist semantics: a field nobody has thought about does not ship.

        This is the property a denylist would not have. If a future writer adds
        `custom:whatever` to this table, it stays server-side until someone
        deliberately adds it here.
        """
        out = _project_service_records([_realistic_row(someFieldAddedLater="x")])
        assert "someFieldAddedLater" not in out[0]


class TestProjectionPreservesWhatTheUiNeeds:
    @pytest.mark.parametrize("field", _UI_CONSUMED_FIELDS)
    def test_ui_consumed_field_survives(self, field):
        """Every field ServiceDashboard.tsx reads must still be present.

        Parametrized per field so a failure names the exact one that broke, rather
        than reporting a single opaque assertion over a set.
        """
        out = _project_service_records([_realistic_row()])
        assert field in out[0], (
            f"{field!r} is read by ServiceDashboard.tsx but was projected away — "
            "this would blank a column in the service dashboard."
        )

    def test_allowlist_covers_every_ui_consumed_field(self):
        """Guard the constant itself, not just one sample row."""
        missing = [f for f in _UI_CONSUMED_FIELDS if f not in _SERVICE_HISTORY_RESPONSE_FIELDS]
        assert missing == [], f"allowlist is missing UI-consumed fields: {missing}"

    def test_nested_service_details_passed_through_whole(self):
        out = _project_service_records([_realistic_row()])
        assert out[0]["serviceDetails"]["recallId"] == "24V-123"
        assert out[0]["serviceDetails"]["recallSeverity"] == "HIGH"

    def test_cost_object_survives_intact(self):
        out = _project_service_records([_realistic_row()])
        assert out[0]["cost"]["totalCost"] == 240


class TestProjectionEdgeCases:
    def test_empty_list(self):
        assert _project_service_records([]) == []

    def test_none(self):
        assert _project_service_records(None) == []

    def test_multiple_rows_all_projected(self):
        rows = [_realistic_row(serviceId=f"svc-{i}") for i in range(3)]
        out = _project_service_records(rows)
        assert len(out) == 3
        assert all("tenantId" not in r for r in out)

    def test_row_missing_optional_fields_does_not_raise(self):
        out = _project_service_records([{"vehicleId": "V1", "tenantId": "example-tenant-canary"}])
        assert out == [{"vehicleId": "V1"}]

    def test_projection_does_not_mutate_input(self):
        """The caller still uses response['Items'] for its count."""
        rows = [_realistic_row()]
        _project_service_records(rows)
        assert "tenantId" in rows[0], "input row was mutated"
