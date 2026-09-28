"""Tests for the GET /api/v1/fleet-actions response-field allowlist.

`GET /api/v1/fleet-actions` returned raw DynamoDB items — `_normalize_action()`
canonicalises a few fields but mutates in place and returns the whole row, so every
attribute egressed. The prod table carries `tenantId` (a customer brand name) on roughly a
third of rows, plus identity and session fields the UI never reads.

This is the second half of the 2026-08-10 exposure audit
(`issues/2026-08-10-cms-demo-external-exposure/`); the service-history allowlist closed 112
of the 144 affected rows and this closes the remaining 32.

Both directions are asserted. An allowlist that quietly drops a field the command centre
renders trades an exposure for a blank dashboard.
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("AWS_REGION", "us-east-1")

from index import (  # noqa: E402
    _FLEET_ACTION_RESPONSE_FIELDS,
    _project_fleet_actions,
)

# Every field FleetCommandCenter.tsx reads off an action, by enumerating `a.<field>`.
_UI_CONSUMED = [
    "actionId",
    "agentResponse",
    "createdAt",
    "domain",
    "priority",
    "severity",
    "status",
]

# Must NEVER reach a client. tenantId is the audit's finding; the rest are identity or
# internal-session fields observed on live prod rows.
_MUST_BE_STRIPPED = [
    "tenantId",
    "driverId",
    "driverName",
    "resolvedBy",
    "connectContactId",
    "voiceSessionId",
    "triageSessionId",
]


def _row(**overrides):
    """A row shaped like the live prod table, including everything that must be dropped."""
    row = {
        # UI-consumed
        "actionId": "act-123",
        "agentResponse": "Recommend brake inspection",
        "createdAt": "2026-08-01T10:00:00Z",
        "domain": "MAINTENANCE",
        "priority": "HIGH",
        "severity": "HIGH",
        "status": "PENDING",
        # allowlisted subject / resolution
        "actionType": "INSPECTION",
        "vehicleId": "VEH-1780081115",
        "vin": "1FTFW1ET5DFC10312",
        "resolvedAt": "2026-08-02T11:00:00Z",
        # ── must be stripped ──
        "tenantId": "example-tenant-canary",
        "driverId": "DRV-001",
        "driverName": "A Person",
        "resolvedBy": "operator@example.com",
        "connectContactId": "contact-abc",
        "voiceSessionId": "voice-abc",
        "triageSessionId": "triage-abc",
        # long-tail operational fields the UI does not read
        "estimatedCost": 240,
        "campaignNumber": "24V-123",
        "sourceTag": "dtc-critical",
    }
    row.update(overrides)
    return row


class TestStripsSensitiveFields:
    @pytest.mark.parametrize("field", _MUST_BE_STRIPPED)
    def test_field_is_stripped(self, field):
        out = _project_fleet_actions([_row()])
        assert field not in out[0], (
            f"{field!r} reached the response. tenantId is the audit finding; driverId is an "
            f"authorization input; the rest are identity or internal-session values."
        )

    def test_brand_value_absent_from_output(self):
        out = _project_fleet_actions([_row()])
        assert "example-tenant-canary" not in repr(out[0]).lower()

    def test_operator_email_absent_from_output(self):
        """resolvedBy carries email addresses on live prod rows."""
        out = _project_fleet_actions([_row()])
        assert "@" not in repr(out[0]), f"an email-shaped value survived: {out[0]}"

    def test_unknown_future_field_stripped_by_default(self):
        """Allowlist semantics — this table has FOUR independent producers.

        A denylist would silently pass the next field any of them adds.
        """
        out = _project_fleet_actions([_row(someNewProducerField="x")])
        assert "someNewProducerField" not in out[0]


class TestPreservesWhatTheUiRenders:
    @pytest.mark.parametrize("field", _UI_CONSUMED)
    def test_ui_field_survives(self, field):
        out = _project_fleet_actions([_row()])
        assert field in out[0], (
            f"{field!r} is rendered by FleetCommandCenter.tsx but was projected away — "
            f"this would blank the command centre."
        )

    def test_allowlist_covers_every_ui_field(self):
        missing = [f for f in _UI_CONSUMED if f not in _FLEET_ACTION_RESPONSE_FIELDS]
        assert missing == [], f"allowlist is missing UI-rendered fields: {missing}"

    def test_normalised_severity_survives_projection(self):
        """Projection runs AFTER _normalize_action, so canonical values must persist."""
        out = _project_fleet_actions([_row(severity="CRITICAL", priority="CRITICAL")])
        assert out[0]["severity"] == "CRITICAL"
        assert out[0]["priority"] == "CRITICAL"


class TestEdgeCases:
    def test_empty_and_none(self):
        assert _project_fleet_actions([]) == []
        assert _project_fleet_actions(None) == []

    def test_multiple_rows_all_projected(self):
        out = _project_fleet_actions([_row(actionId=f"a{i}") for i in range(3)])
        assert len(out) == 3
        assert all("tenantId" not in r for r in out)

    def test_sparse_row_does_not_raise(self):
        """Live rows are sparse — only actionId/createdAt/status are on every row."""
        out = _project_fleet_actions([{"actionId": "a1", "tenantId": "brand"}])
        assert out == [{"actionId": "a1"}]

    def test_input_not_mutated(self):
        """The caller uses len(items) for `total` after projecting a slice."""
        rows = [_row()]
        _project_fleet_actions(rows)
        assert "tenantId" in rows[0], "input row was mutated"
