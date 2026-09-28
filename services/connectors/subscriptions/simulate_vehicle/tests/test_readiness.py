# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for T5.0 — per-vehicle simulation readiness on the picker's list endpoint.

Covers:
  - _fetch_enrolled_vehicle_ids: paginated scan, fail-soft on exception
  - _fetch_certified_vehicle_ids: paginated scan, fail-soft on exception
  - _fetch_campaign_covered_target_arns: paginated scan, filters on RUNNING + non-empty signals
  - _annotate_vehicle_readiness: all-reasons reporting, cloud-telemetry skips campaign check
  - list_vehicles_handler: response shape (simulation_ready, not_ready_reasons, ready_count)
  - Mutations M12, M13, M14

Anti-vacuity floor: _mock_readiness_tables() returns a state where exactly 1 of 3 test
vehicles is ready, so assertions about individual reason tokens are distinguishable.
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock, call, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path: make simulate_vehicle importable (mirrors test_handler.py)
# ---------------------------------------------------------------------------
_SUBSCRIPTIONS_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _SUBSCRIPTIONS_DIR not in sys.path:
    sys.path.insert(0, _SUBSCRIPTIONS_DIR)

os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")
os.environ.setdefault("DEPLOYMENT_STAGE", "test")
os.environ.setdefault("VEHICLES_TABLE_NAME", "test-vehicles")
os.environ.setdefault("SIMULATION_FUNCTION_NAME", "cms-test-simulation-api")

from simulate_vehicle import handler as h  # noqa: E402

# ---------------------------------------------------------------------------
# Re-usable fixtures
# ---------------------------------------------------------------------------

_STAGE = "test"
_FLEET_ID = "flt-meridian-range-001"

# Three vehicles representing the three readiness cases.
#
# VID_READY    — enrolled + cert present + campaign covers it (vehicle-telemetry)
# VID_NOFLEET  — NOT enrolled; cert present; campaign present
# VID_NOCERT   — enrolled; NO cert; campaign present
# VID_NOCAMPAIGN — enrolled; cert present; NO campaign (vehicle-telemetry)
# VID_CLOUD    — enrolled; cert absent; cloud-telemetry (no campaign check)
# VID_ALL_FAIL — enrolled=NO, cert=NO, campaign=NO (vehicle-telemetry)

_VID_READY     = "VEH-MRDN-READY-001"
_VIN_READY     = "MRDN0000READY0001"
_VID_NOFLEET   = "VEH-MRDN-NOFLEET-001"
_VIN_NOFLEET   = "MRDN0000NOFLEET01"
_VID_NOCERT    = "VEH-MRDN-NOCERT-001"
_VIN_NOCERT    = "MRDN0000NOCERT001"
_VID_NOCAMPAIGN = "VEH-MRDN-NOCMP-001"
_VIN_NOCAMPAIGN = "MRDN0000NOCMP0001"
_VID_CLOUD     = "VEH-MRDN-CLOUD-001"
_VIN_CLOUD     = "MRDN0000CLOUD0001"
_VID_ALL_FAIL  = "VEH-MRDN-ALLFAIL-01"
_VIN_ALL_FAIL  = "MRDN000ALLFAIL001"

_VEHICLE_READY = {
    "vehicleId": _VID_READY, "vin": _VIN_READY,
    "dataSource": "vehicle-telemetry", "producer": "meridian",
}
_VEHICLE_NOFLEET = {
    "vehicleId": _VID_NOFLEET, "vin": _VIN_NOFLEET,
    "dataSource": "vehicle-telemetry", "producer": "meridian",
}
_VEHICLE_NOCERT = {
    "vehicleId": _VID_NOCERT, "vin": _VIN_NOCERT,
    "dataSource": "vehicle-telemetry", "producer": "meridian",
}
_VEHICLE_NOCAMPAIGN = {
    "vehicleId": _VID_NOCAMPAIGN, "vin": _VIN_NOCAMPAIGN,
    "dataSource": "vehicle-telemetry", "producer": "meridian",
}
_VEHICLE_CLOUD = {
    "vehicleId": _VID_CLOUD, "vin": _VIN_CLOUD,
    "dataSource": "cloud-telemetry", "producer": "meridian",
}
_VEHICLE_ALL_FAIL = {
    "vehicleId": _VID_ALL_FAIL, "vin": _VIN_ALL_FAIL,
    "dataSource": "vehicle-telemetry", "producer": "meridian",
}


def _make_enrollment_table(enrolled_ids: set[str]) -> MagicMock:
    """Enrollment table stub — returns items for enrolled vehicleIds only."""
    table = MagicMock()
    items = [{"vehicleId": vid, "fleetId": _FLEET_ID} for vid in enrolled_ids]
    table.scan.return_value = {"Items": items}
    return table


def _make_certs_table(cert_vehicle_ids: set[str]) -> MagicMock:
    """Certificates table stub — returns vin rows."""
    table = MagicMock()
    items = [{"vehicleId": v} for v in cert_vehicle_ids]
    table.scan.return_value = {"Items": items}
    return table


def _make_campaigns_table(running_targets: set[str]) -> MagicMock:
    """Campaigns table stub — returns RUNNING rows for the given targetArns.

    The stub respects the `FilterExpression` for status=RUNNING (the real
    DynamoDB would filter; the stub must too so that ACTIVE/SUSPENDED items
    do not leak into the results).
    """
    table = MagicMock()
    items = [
        {"targetArn": t, "signalsToCollect": [{"name": "Vehicle.Speed"}], "status": "RUNNING"}
        for t in running_targets
    ]
    table.scan.return_value = {"Items": items}
    return table


def _event_get() -> dict:
    return {
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "operator-alice",
                    "cognito:groups": "connected-services",
                }
            }
        },
    }


# A minimal mock DDB resource that routes .Table(name) calls to the right stub.
def _mock_ddb_resource(vehicles_table, enrollment_table, certs_table, campaigns_table):
    """Return a mock DDB resource that dispatches by table name."""
    resource = MagicMock()
    # Map table names to stubs.
    table_map = {
        os.environ.get("VEHICLES_TABLE_NAME", "test-vehicles"): vehicles_table,
        h._FLEET_ENROLLMENT_TABLE: enrollment_table,
        h._VEHICLE_CERTIFICATES_TABLE: certs_table,
        h._CAMPAIGNS_TABLE: campaigns_table,
    }
    resource.Table.side_effect = lambda name: table_map.get(name, MagicMock())
    return resource


# ---------------------------------------------------------------------------
# _fetch_enrolled_vehicle_ids
# ---------------------------------------------------------------------------


class TestFetchEnrolledVehicleIds:
    """Unit tests for the fleet-enrollment scan helper."""

    def _call(self, items_pages: list[list[dict]]) -> frozenset:
        """Drive the helper with paginated responses."""
        table = MagicMock()
        resps = []
        for i, items in enumerate(items_pages):
            resp = {"Items": items}
            if i < len(items_pages) - 1:
                resp["LastEvaluatedKey"] = {"vehicleId": f"cursor-{i}"}
            resps.append(resp)
        table.scan.side_effect = resps
        resource = MagicMock()
        resource.Table.return_value = table
        return h._fetch_enrolled_vehicle_ids(ddb_resource=resource)

    def test_returns_enrolled_vehicle_ids(self):
        result = self._call([[
            {"vehicleId": "VEH-A", "fleetId": _FLEET_ID},
            {"vehicleId": "VEH-B", "fleetId": _FLEET_ID},
        ]])
        assert "VEH-A" in result
        assert "VEH-B" in result

    def test_pagination_follows_last_evaluated_key(self):
        result = self._call([
            [{"vehicleId": "VEH-A", "fleetId": _FLEET_ID}],
            [{"vehicleId": "VEH-B", "fleetId": _FLEET_ID}],
        ])
        assert "VEH-A" in result and "VEH-B" in result

    def test_empty_table_returns_empty_frozenset(self):
        result = self._call([[]])
        assert result == frozenset()

    def test_exception_returns_none_not_frozenset(self):
        """Fail-soft: exception → None (not frozenset()) so callers can distinguish
        'scan failed' from 'table is genuinely empty'.  FG13.T3 (W3).
        """
        table = MagicMock()
        table.scan.side_effect = Exception("network error")
        resource = MagicMock()
        resource.Table.return_value = table
        result = h._fetch_enrolled_vehicle_ids(ddb_resource=resource)
        assert result is None, (
            "Mutation (a) DETECTED: fetcher returns frozenset() on failure — "
            "caller cannot distinguish 'scan failed' from 'empty table'"
        )

    def test_items_with_no_vehicle_id_are_skipped(self):
        """Malformed rows do not crash the helper."""
        result = self._call([[{"fleetId": _FLEET_ID}, {"vehicleId": "VEH-A", "fleetId": _FLEET_ID}]])
        assert "VEH-A" in result
        assert len(result) == 1


# ---------------------------------------------------------------------------
# _fetch_certified_vehicle_ids
# ---------------------------------------------------------------------------


class TestFetchCertifiedVehicleIds:
    """Unit tests for the certificate scan helper."""

    def _call(self, items_pages: list[list[dict]]) -> frozenset:
        table = MagicMock()
        resps = []
        for i, items in enumerate(items_pages):
            resp = {"Items": items}
            if i < len(items_pages) - 1:
                resp["LastEvaluatedKey"] = {"vehicleId": f"cursor-{i}"}
            resps.append(resp)
        table.scan.side_effect = resps
        resource = MagicMock()
        resource.Table.return_value = table
        return h._fetch_certified_vehicle_ids(ddb_resource=resource)

    def test_returns_vehicle_ids_present_in_table(self):
        result = self._call([[{"vehicleId": "VEH-A"}, {"vehicleId": "VEH-B"}]])
        assert "VEH-A" in result and "VEH-B" in result

    def test_pagination_followed(self):
        result = self._call([[{"vehicleId": "VEH-A"}], [{"vehicleId": "VEH-B"}]])
        assert "VEH-A" in result and "VEH-B" in result

    def test_collects_vehicleId_and_IGNORES_vin(self):
        """The join field. A row carrying BOTH must contribute its vehicleId only.

        This is the whole defect: the fetcher used to collect `vin`, an attribute
        with no uniqueness constraint and no writer keeping it in step with the
        vehicles table, while the table is keyed on `vehicleId`. A row whose `vin`
        is stale must NOT leak that stale value into the set, or the old false
        "no device certificate" comes back for whichever vehicle owns it.
        """
        result = self._call([[{"vehicleId": "VEH-MRDN-0015", "vin": "MRDN0000000000011"}]])
        assert result == frozenset({"VEH-MRDN-0015"})
        assert "MRDN0000000000011" not in result

    def test_empty_table_returns_empty(self):
        assert self._call([[]]) == frozenset()

    def test_exception_returns_none_not_frozenset(self):
        """Fail-soft: exception → None (not frozenset()) so callers can distinguish
        'scan failed' from 'table is genuinely empty'.  FG13.T3 (W3).
        """
        table = MagicMock()
        table.scan.side_effect = Exception("boom")
        resource = MagicMock()
        resource.Table.return_value = table
        result = h._fetch_certified_vehicle_ids(ddb_resource=resource)
        assert result is None, (
            "Mutation (a) DETECTED: fetcher returns frozenset() on failure — "
            "caller cannot distinguish 'scan failed' from 'empty table'"
        )

    def test_items_missing_vehicleId_are_skipped(self):
        result = self._call([[{"certificatePem": "---"}, {"vehicleId": "VEH-A"}]])
        assert result == frozenset({"VEH-A"})


# ---------------------------------------------------------------------------
# _fetch_campaign_covered_target_arns
# ---------------------------------------------------------------------------


class TestFetchCampaignCoveredTargetArns:
    """Unit tests for the campaign coverage scan helper."""

    def _call(self, items: list[dict]) -> frozenset:
        table = MagicMock()
        table.scan.return_value = {"Items": items}
        resource = MagicMock()
        resource.Table.return_value = table
        return h._fetch_campaign_covered_target_arns(ddb_resource=resource)

    def _call_pages(self, items_pages: list[list[dict]]) -> frozenset:
        table = MagicMock()
        resps = []
        for i, items in enumerate(items_pages):
            resp = {"Items": items}
            if i < len(items_pages) - 1:
                resp["LastEvaluatedKey"] = {"campaignId": f"cursor-{i}"}
            resps.append(resp)
        table.scan.side_effect = resps
        resource = MagicMock()
        resource.Table.return_value = table
        return h._fetch_campaign_covered_target_arns(ddb_resource=resource)

    def test_running_with_signals_is_included(self):
        result = self._call([{
            "targetArn": "vehicle:VIN-A",
            "signalsToCollect": [{"name": "Vehicle.Speed"}],
            "status": "RUNNING",
        }])
        assert "vehicle:VIN-A" in result

    def test_running_with_empty_signals_is_excluded(self):
        """An empty signalsToCollect campaign produces no telemetry — not coverage."""
        result = self._call([{
            "targetArn": "vehicle:VIN-A",
            "signalsToCollect": [],
            "status": "RUNNING",
        }])
        assert "vehicle:VIN-A" not in result

    def test_running_with_absent_signals_is_excluded(self):
        """A RUNNING row with no signalsToCollect key is treated as empty."""
        result = self._call([{"targetArn": "vehicle:VIN-A", "status": "RUNNING"}])
        assert "vehicle:VIN-A" not in result

    def test_active_template_is_not_coverage(self):
        """Templates carry status='ACTIVE'. The scan FilterExpression restricts to
        RUNNING, so DynamoDB would never return them. We test this by verifying
        the FilterExpression is sent with status=RUNNING.
        """
        table = MagicMock()
        table.scan.return_value = {"Items": []}  # DDB returns nothing when filter applied
        resource = MagicMock()
        resource.Table.return_value = table
        h._fetch_campaign_covered_target_arns(ddb_resource=resource)
        call_kwargs = table.scan.call_args.kwargs or {}
        values = call_kwargs.get("ExpressionAttributeValues", {})
        assert values.get(":running") == "RUNNING", (
            "scan did not filter to status=RUNNING — ACTIVE templates would be included"
        )

    def test_suspended_campaign_is_not_coverage(self):
        """The FilterExpression must restrict to RUNNING only. If a SUSPENDED item
        were returned by DynamoDB (i.e., the FilterExpression was absent), our code
        would not apply a second Python-side filter. Verify the FilterExpression
        is sent.
        """
        table = MagicMock()
        table.scan.return_value = {"Items": []}
        resource = MagicMock()
        resource.Table.return_value = table
        h._fetch_campaign_covered_target_arns(ddb_resource=resource)
        call_kwargs = table.scan.call_args.kwargs or {}
        filter_expr = call_kwargs.get("FilterExpression")
        assert filter_expr is not None, (
            "scan sent no FilterExpression — SUSPENDED campaigns would be counted"
        )

    def test_all_broadcast_target_arn_is_included(self):
        result = self._call([{
            "targetArn": "all",
            "signalsToCollect": [{"name": "Vehicle.Speed"}],
            "status": "RUNNING",
        }])
        assert "all" in result

    def test_fleet_target_arn_is_included(self):
        result = self._call([{
            "targetArn": f"fleet:{_FLEET_ID}",
            "signalsToCollect": [{"name": "Vehicle.Speed"}],
            "status": "RUNNING",
        }])
        assert f"fleet:{_FLEET_ID}" in result

    def test_pagination_followed(self):
        result = self._call_pages([
            [{"targetArn": "vehicle:VIN-A", "signalsToCollect": [{"name": "x"}], "status": "RUNNING"}],
            [{"targetArn": "vehicle:VIN-B", "signalsToCollect": [{"name": "x"}], "status": "RUNNING"}],
        ])
        assert "vehicle:VIN-A" in result and "vehicle:VIN-B" in result

    def test_exception_returns_none_not_frozenset(self):
        """Fail-soft: exception → None (not frozenset()) so callers can distinguish
        'scan failed' from 'no campaigns exist'.  FG13.T3 (W3).
        """
        table = MagicMock()
        table.scan.side_effect = Exception("network error")
        resource = MagicMock()
        resource.Table.return_value = table
        result = h._fetch_campaign_covered_target_arns(ddb_resource=resource)
        assert result is None, (
            "Mutation (a) DETECTED: fetcher returns frozenset() on failure — "
            "caller cannot distinguish 'scan failed' from 'no campaigns'"
        )


# ---------------------------------------------------------------------------
# _annotate_vehicle_readiness
# ---------------------------------------------------------------------------


class TestAnnotateVehicleReadiness:
    """Unit tests for the readiness annotation logic."""

    def _run(self, vehicles, enrolled, certs, campaigns, fleet_map=None):
        """Convenience wrapper."""
        return h._annotate_vehicle_readiness(
            vehicles=vehicles,
            enrolled_ids=frozenset(enrolled),
            cert_vehicle_ids=frozenset(certs),
            campaign_arns=frozenset(campaigns),
            vehicle_id_to_fleet=fleet_map or {},
        )

    def test_ready_vehicle_has_simulation_ready_true_and_empty_reasons(self):
        vehicles, count = self._run(
            [_VEHICLE_READY],
            enrolled={_VID_READY},
            certs={_VID_READY},
            campaigns={f"vehicle:{_VIN_READY}"},
        )
        v = vehicles[0]
        assert v["simulation_ready"] is True
        assert v["not_ready_reasons"] == []
        assert count == 1

    def test_not_fleet_enrolled_adds_reason(self):
        vehicles, count = self._run(
            [_VEHICLE_NOFLEET],
            enrolled=set(),
            certs={_VID_NOFLEET},
            campaigns={f"vehicle:{_VIN_NOFLEET}"},
        )
        assert "not_fleet_enrolled" in vehicles[0]["not_ready_reasons"]
        assert vehicles[0]["simulation_ready"] is False
        assert count == 0

    def test_no_certificate_adds_reason(self):
        vehicles, count = self._run(
            [_VEHICLE_NOCERT],
            enrolled={_VID_NOCERT},
            certs=set(),
            campaigns={f"vehicle:{_VIN_NOCERT}"},
        )
        assert "no_certificate" in vehicles[0]["not_ready_reasons"]
        assert count == 0

    def test_no_campaign_adds_reason_for_vehicle_telemetry(self):
        vehicles, count = self._run(
            [_VEHICLE_NOCAMPAIGN],
            enrolled={_VID_NOCAMPAIGN},
            certs={_VID_NOCAMPAIGN},
            campaigns=set(),
        )
        assert "no_telemetry_campaign" in vehicles[0]["not_ready_reasons"]
        assert count == 0

    def test_cloud_telemetry_skips_campaign_check(self):
        """cloud-telemetry is NOT gated on campaigns — skipping the check
        is the load-bearing property of Accept item 3's second paragraph.

        M13 MUTATION: if cloud-telemetry vehicles are required to have a
        campaign, this vehicle reports not-ready despite meeting all real
        prerequisites — and exactly this test fails.
        """
        vehicles, count = self._run(
            [_VEHICLE_CLOUD],
            enrolled={_VID_CLOUD},
            certs={_VID_CLOUD},
            campaigns=set(),  # no campaigns at all
        )
        # Cloud-telemetry: no cert is missing (enrolled + cert present), no campaign check
        assert "no_telemetry_campaign" not in vehicles[0]["not_ready_reasons"]
        assert vehicles[0]["simulation_ready"] is True
        assert count == 1

    def test_all_failing_prerequisites_reported_together(self):
        """ALL failing prerequisites are reported, not just the first (Accept item 2).

        M14 MUTATION: if only the first reason is returned, this test fails
        because a two-prereq vehicle reports only one reason.
        """
        vehicles, count = self._run(
            [_VEHICLE_ALL_FAIL],
            enrolled=set(),
            certs=set(),
            campaigns=set(),
        )
        reasons = vehicles[0]["not_ready_reasons"]
        assert "not_fleet_enrolled" in reasons
        assert "no_certificate" in reasons
        assert "no_telemetry_campaign" in reasons
        assert len(reasons) == 3
        assert count == 0

    def test_campaign_coverage_via_all_broadcast(self):
        """A RUNNING campaign targeting 'all' covers any vehicle."""
        vehicles, count = self._run(
            [_VEHICLE_READY],
            enrolled={_VID_READY},
            certs={_VID_READY},
            campaigns={"all"},  # broadcast
        )
        assert vehicles[0]["simulation_ready"] is True
        assert count == 1

    def test_campaign_coverage_via_fleet(self):
        """A fleet-scoped campaign covers vehicles in that fleet."""
        vehicles, count = self._run(
            [_VEHICLE_READY],
            enrolled={_VID_READY},
            certs={_VID_READY},
            campaigns={f"fleet:{_FLEET_ID}"},
            fleet_map={_VID_READY: _FLEET_ID},
        )
        assert vehicles[0]["simulation_ready"] is True
        assert count == 1

    def test_ready_count_matches_number_of_ready_vehicles(self):
        vehicles_list = [_VEHICLE_READY, _VEHICLE_NOFLEET, _VEHICLE_NOCERT]
        _, count = self._run(
            vehicles_list,
            enrolled={_VID_READY},
            certs={_VID_READY},
            campaigns={f"vehicle:{_VIN_READY}"},
        )
        assert count == 1

    def test_original_vehicle_dict_is_not_mutated(self):
        """_annotate_vehicle_readiness must not mutate the original dicts."""
        original = dict(_VEHICLE_READY)
        self._run(
            [original],
            enrolled={_VID_READY},
            certs={_VID_READY},
            campaigns={f"vehicle:{_VIN_READY}"},
        )
        assert "simulation_ready" not in original
        assert "not_ready_reasons" not in original

    def test_none_enrolled_ids_yields_simulation_ready_null(self):
        """W3: when enrolled_ids is None (scan failed), emit simulation_ready=None
        rather than False with false prerequisite claims.

        Mutation (a): if the fetcher returns frozenset() on failure (old behaviour),
        enrolled_ids would be frozenset() and the vehicle would report
        not_fleet_enrolled — a false claim.  That is the defect FG13.T3 fixes.
        """
        vehicles, count = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_READY],
            enrolled_ids=None,   # scan failed
            cert_vehicle_ids=frozenset({_VID_READY}),
            campaign_arns=frozenset({f"vehicle:{_VIN_READY}"}),
            vehicle_id_to_fleet={_VID_READY: _FLEET_ID},
        )
        v = vehicles[0]
        assert v["simulation_ready"] is None, (
            "Mutation (a) DETECTED: None enrolled_ids yielded simulation_ready "
            f"{v['simulation_ready']!r} instead of null"
        )
        assert v["not_ready_reasons"] == ["readiness_unavailable"], (
            f"Expected ['readiness_unavailable'], got {v['not_ready_reasons']!r}"
        )
        assert count == 0, "unknowns must not be counted as ready"

    def test_none_cert_vehicle_ids_yields_simulation_ready_null(self):
        """W3: when cert_vehicle_ids is None (scan failed), emit simulation_ready=None."""
        vehicles, count = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_READY],
            enrolled_ids=frozenset({_VID_READY}),
            cert_vehicle_ids=None,  # scan failed
            campaign_arns=frozenset({f"vehicle:{_VIN_READY}"}),
            vehicle_id_to_fleet={_VID_READY: _FLEET_ID},
        )
        v = vehicles[0]
        assert v["simulation_ready"] is None
        assert v["not_ready_reasons"] == ["readiness_unavailable"]
        assert count == 0

    def test_none_campaign_arns_yields_simulation_ready_null(self):
        """W3: when campaign_arns is None (scan failed), emit simulation_ready=None."""
        vehicles, count = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_READY],
            enrolled_ids=frozenset({_VID_READY}),
            cert_vehicle_ids=frozenset({_VID_READY}),
            campaign_arns=None,  # scan failed
            vehicle_id_to_fleet={_VID_READY: _FLEET_ID},
        )
        v = vehicles[0]
        assert v["simulation_ready"] is None
        assert v["not_ready_reasons"] == ["readiness_unavailable"]
        assert count == 0

    def test_none_input_does_not_count_as_ready(self):
        """Mutation (b): if unknowns were counted as ready, ready_count would be >0.

        This pins that ready_count excludes vehicles with unknown readiness.
        """
        vehicles, count = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_READY, _VEHICLE_NOFLEET],
            enrolled_ids=None,  # scan failed
            cert_vehicle_ids=None,
            campaign_arns=None,
            vehicle_id_to_fleet={},
        )
        assert count == 0, (
            f"Mutation (b) DETECTED: unknowns counted as ready — got ready_count={count}"
        )
        for v in vehicles:
            assert v["simulation_ready"] is None
            assert v["not_ready_reasons"] == ["readiness_unavailable"]

    def test_all_none_inputs_does_not_emit_false_prerequisite_claims(self):
        """W3: the prior behaviour emitted three false claims for a fully-provisioned
        vehicle when scans failed.  This test pins that no false claim is emitted.
        """
        vehicles, _ = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_READY],
            enrolled_ids=None,
            cert_vehicle_ids=None,
            campaign_arns=None,
            vehicle_id_to_fleet={},
        )
        reasons = vehicles[0]["not_ready_reasons"]
        assert "not_fleet_enrolled" not in reasons, "false claim: not_fleet_enrolled"
        assert "no_certificate" not in reasons, "false claim: no_certificate"
        assert "no_telemetry_campaign" not in reasons, "false claim: no_telemetry_campaign"
        assert reasons == ["readiness_unavailable"]

    def test_empty_frozenset_is_distinct_from_none(self):
        """An empty frozenset means the table is genuinely empty — vehicles ARE
        reported not-ready with specific reasons.  None means the scan failed.
        The two states must not collapse.
        """
        # Empty frozenset: not ready, specific reasons
        vehicles_empty, _ = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_READY],
            enrolled_ids=frozenset(),
            cert_vehicle_ids=frozenset(),
            campaign_arns=frozenset(),
            vehicle_id_to_fleet={},
        )
        assert vehicles_empty[0]["simulation_ready"] is False
        assert "not_fleet_enrolled" in vehicles_empty[0]["not_ready_reasons"]

        # None: unknown readiness — only readiness_unavailable
        vehicles_none, _ = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_READY],
            enrolled_ids=None,
            cert_vehicle_ids=frozenset(),  # one is None, that's enough
            campaign_arns=frozenset(),
            vehicle_id_to_fleet={},
        )
        assert vehicles_none[0]["simulation_ready"] is None
        assert vehicles_none[0]["not_ready_reasons"] == ["readiness_unavailable"]


# ---------------------------------------------------------------------------
# list_vehicles_handler — readiness fields in the response
# ---------------------------------------------------------------------------


class TestListVehiclesHandlerReadiness:
    """Integration tests for the readiness annotation via list_vehicles_handler."""

    def _call(
        self,
        vehicles: list[dict],
        enrolled: set[str],
        cert_vehicle_ids: set[str],
        campaign_targets: set[str],
    ) -> dict:
        """Call list_vehicles_handler with stubbed DDB tables."""
        from simulate_vehicle.tests.test_handler import _mock_vehicles_table  # noqa: PLC0415

        vehicles_table = _mock_vehicles_table(vehicles)
        enrollment_table = _make_enrollment_table(enrolled)
        certs_table = _make_certs_table(cert_vehicle_ids)
        campaigns_table = _make_campaigns_table(campaign_targets)

        resource = _mock_ddb_resource(
            vehicles_table, enrollment_table, certs_table, campaigns_table
        )

        with patch.object(h, "_get_ddb_resource", return_value=resource):
            return h.list_vehicles_handler(_event_get(), None)

    def test_response_carries_simulation_ready_and_not_ready_reasons(self):
        """Each vehicle has both fields (Accept item 1)."""
        resp = self._call(
            [_VEHICLE_READY],
            enrolled={_VID_READY},
            cert_vehicle_ids={_VID_READY},
            campaign_targets={f"vehicle:{_VIN_READY}"},
        )
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        v = body["vehicles"][0]
        assert "simulation_ready" in v
        assert "not_ready_reasons" in v

    def test_response_carries_ready_count(self):
        """The response includes `ready_count` (Accept item 3)."""
        resp = self._call(
            [_VEHICLE_READY, _VEHICLE_NOFLEET],
            enrolled={_VID_READY},
            cert_vehicle_ids={_VID_READY, _VID_NOFLEET},
            campaign_targets={f"vehicle:{_VIN_READY}", f"vehicle:{_VIN_NOFLEET}"},
        )
        body = json.loads(resp["body"])
        assert "ready_count" in body
        assert body["ready_count"] == 1
        assert body["count"] == 2

    def test_ready_vehicle_is_simulation_ready_true(self):
        resp = self._call(
            [_VEHICLE_READY],
            enrolled={_VID_READY},
            cert_vehicle_ids={_VID_READY},
            campaign_targets={f"vehicle:{_VIN_READY}"},
        )
        v = json.loads(resp["body"])["vehicles"][0]
        assert v["simulation_ready"] is True
        assert v["not_ready_reasons"] == []

    def test_not_enrolled_vehicle_shows_not_fleet_enrolled(self):
        resp = self._call(
            [_VEHICLE_NOFLEET],
            enrolled=set(),
            cert_vehicle_ids={_VID_NOFLEET},
            campaign_targets={f"vehicle:{_VIN_NOFLEET}"},
        )
        v = json.loads(resp["body"])["vehicles"][0]
        assert v["simulation_ready"] is False
        assert "not_fleet_enrolled" in v["not_ready_reasons"]

    def test_no_cert_vehicle_shows_no_certificate(self):
        resp = self._call(
            [_VEHICLE_NOCERT],
            enrolled={_VID_NOCERT},
            cert_vehicle_ids=set(),
            campaign_targets={f"vehicle:{_VIN_NOCERT}"},
        )
        v = json.loads(resp["body"])["vehicles"][0]
        assert "no_certificate" in v["not_ready_reasons"]

    def test_no_campaign_vehicle_telemetry_shows_no_telemetry_campaign(self):
        resp = self._call(
            [_VEHICLE_NOCAMPAIGN],
            enrolled={_VID_NOCAMPAIGN},
            cert_vehicle_ids={_VID_NOCAMPAIGN},
            campaign_targets=set(),
        )
        v = json.loads(resp["body"])["vehicles"][0]
        assert "no_telemetry_campaign" in v["not_ready_reasons"]

    def test_cloud_telemetry_ready_with_no_campaigns(self):
        """cloud-telemetry skips campaign check even with no campaigns at all."""
        resp = self._call(
            [_VEHICLE_CLOUD],
            enrolled={_VID_CLOUD},
            cert_vehicle_ids={_VID_CLOUD},
            campaign_targets=set(),
        )
        v = json.loads(resp["body"])["vehicles"][0]
        assert v["simulation_ready"] is True
        assert "no_telemetry_campaign" not in v["not_ready_reasons"]

    def test_all_three_reasons_reported_for_completely_unready_vehicle(self):
        """All failing prerequisites are reported — not just the first."""
        resp = self._call(
            [_VEHICLE_ALL_FAIL],
            enrolled=set(),
            cert_vehicle_ids=set(),
            campaign_targets=set(),
        )
        reasons = json.loads(resp["body"])["vehicles"][0]["not_ready_reasons"]
        assert set(reasons) == {"not_fleet_enrolled", "no_certificate", "no_telemetry_campaign"}

    def test_count_is_total_and_ready_count_is_subset(self):
        all_vehicles = [
            _VEHICLE_READY, _VEHICLE_NOFLEET, _VEHICLE_NOCERT,
            _VEHICLE_NOCAMPAIGN, _VEHICLE_CLOUD, _VEHICLE_ALL_FAIL,
        ]
        resp = self._call(
            all_vehicles,
            enrolled={_VID_READY, _VID_NOCERT, _VID_NOCAMPAIGN, _VID_CLOUD},
            cert_vehicle_ids={_VID_READY, _VID_NOFLEET, _VID_NOCAMPAIGN, _VID_CLOUD},
            campaign_targets={f"vehicle:{_VIN_READY}", f"vehicle:{_VIN_NOCAMPAIGN}"},
        )
        body = json.loads(resp["body"])
        assert body["count"] == len(all_vehicles)
        # Ready vehicles:
        #   _VEHICLE_READY:     enrolled + cert + campaign(vehicle:{VIN}) → ready
        #   _VEHICLE_NOCAMPAIGN: enrolled + cert + campaign(vehicle:{VIN}) → ready
        #   _VEHICLE_CLOUD:     enrolled + cert + no campaign check (cloud-telemetry) → ready
        # Not ready:
        #   _VEHICLE_NOFLEET:   no enrollment
        #   _VEHICLE_NOCERT:    no cert
        #   _VEHICLE_ALL_FAIL:  no enrollment, no cert, no campaign
        assert body["ready_count"] == 3  # READY + NOCAMPAIGN (has campaign) + CLOUD

    def test_readiness_fetch_failure_emits_null_not_false_prerequisites(self):
        """W3 + W4: when all three readiness tables raise, each vehicle must carry
        ``simulation_ready: null`` and ``not_ready_reasons: ['readiness_unavailable']``,
        NOT ``simulation_ready: false`` with three false prerequisite claims.

        Prior behaviour: fetchers returned frozenset() on exception, so
        _annotate_vehicle_readiness computed 'not enrolled', 'no cert',
        'no campaign' — three concrete claims about provisioning state that were
        all false for a fully-provisioned vehicle.  FG13.T3 (W3/W4).

        Mutation (a) verification: if a fetcher reverts to returning frozenset()
        on failure, simulation_ready becomes False with specific reasons —
        this assertion on None will fail.
        """
        from simulate_vehicle.tests.test_handler import _mock_vehicles_table  # noqa: PLC0415

        vehicles_table = _mock_vehicles_table([_VEHICLE_READY])
        broken_table = MagicMock()
        broken_table.scan.side_effect = Exception("DDB failure")

        resource = _mock_ddb_resource(
            vehicles_table, broken_table, broken_table, broken_table
        )
        with patch.object(h, "_get_ddb_resource", return_value=resource):
            resp = h.list_vehicles_handler(_event_get(), None)

        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert len(body["vehicles"]) == 1
        v = body["vehicles"][0]

        # simulation_ready must be JSON null (Python None), NOT False
        assert v["simulation_ready"] is None, (
            f"Mutation (a) DETECTED: got simulation_ready={v['simulation_ready']!r} "
            "instead of null — fetcher is returning frozenset() on failure"
        )
        # Exactly one advisory reason token, no false prerequisite claims
        assert v["not_ready_reasons"] == ["readiness_unavailable"], (
            f"Expected ['readiness_unavailable'], got {v['not_ready_reasons']!r}"
        )
        # Unknowns do not count as ready
        assert body["ready_count"] == 0, (
            "Mutation (b) DETECTED: unknowns counted as ready — "
            f"ready_count={body['ready_count']} but all readiness scans failed"
        )

    def test_unauthorized_caller_gets_403_and_no_readiness_scan(self):
        """Authz must fire BEFORE any DDB call — including the readiness scans."""
        from simulate_vehicle.tests.test_handler import _mock_vehicles_table  # noqa: PLC0415

        vehicles_table = _mock_vehicles_table([_VEHICLE_READY])
        enrollment_table = _make_enrollment_table({_VID_READY})
        certs_table = _make_certs_table({_VID_READY})
        campaigns_table = _make_campaigns_table({f"vehicle:{_VIN_READY}"})

        resource = _mock_ddb_resource(
            vehicles_table, enrollment_table, certs_table, campaigns_table
        )

        event_unauthed = {"requestContext": {}}
        with patch.object(h, "_get_ddb_resource", return_value=resource):
            resp = h.list_vehicles_handler(event_unauthed, None)

        assert resp["statusCode"] == 403
        # No table should have been called
        vehicles_table.scan.assert_not_called()
        enrollment_table.scan.assert_not_called()
        certs_table.scan.assert_not_called()
        campaigns_table.scan.assert_not_called()


# ---------------------------------------------------------------------------
# Mutation M12: drop the certificate check
# ---------------------------------------------------------------------------
# Applied in test: remove cert check → a vehicle with enrollment and campaign
# but no cert reports ready; this test verifies it currently reports NOT ready.
# M12 would break test_no_cert_vehicle_shows_no_certificate above and also:


class TestMutationM12NoCertCheckDropped:
    """M12: if the certificate check were dropped, a vehicle with no certificate
    would be reported ready.  This class pins that it currently reports not-ready.
    """

    def test_vehicle_without_cert_is_not_ready(self):
        """Drop cert check → simulation_ready becomes True → this test fails.

        M12 verification: delete the `no_certificate` reason from
        _annotate_vehicle_readiness, confirm THIS test fails, then restore.
        """
        vehicles, count = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_NOCERT],
            enrolled_ids=frozenset({_VID_NOCERT}),
            cert_vehicle_ids=frozenset(),  # no certs
            campaign_arns=frozenset({f"vehicle:{_VIN_NOCERT}"}),
            vehicle_id_to_fleet={_VID_NOCERT: _FLEET_ID},
        )
        reasons = vehicles[0]["not_ready_reasons"]
        assert "no_certificate" in reasons, (
            "M12 DETECTED: certificate check dropped — a vehicle with enrollment "
            "and campaign but no cert reports ready when it must not"
        )
        assert vehicles[0]["simulation_ready"] is False
        assert count == 0


# ---------------------------------------------------------------------------
# Mutation M13: require campaign for cloud-telemetry too
# ---------------------------------------------------------------------------


class TestMutationM13CloudTelemetryCampaignRequired:
    """M13: if cloud-telemetry were gated on campaigns, VEH-MRDN-0013 (the only
    cloud-telemetry vehicle in flt-meridian-range-001) would report not-ready,
    and the 11-of-100 live assertion would fail.

    This class pins that cloud-telemetry skips the campaign check.
    """

    def test_cloud_telemetry_vehicle_without_campaign_is_ready(self):
        """Require campaign for cloud-telemetry → simulation_ready becomes False
        → this test fails.

        M13 verification: add the campaign check for cloud-telemetry in
        _annotate_vehicle_readiness, confirm THIS test fails, then restore.
        """
        vehicles, count = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_CLOUD],
            enrolled_ids=frozenset({_VID_CLOUD}),
            cert_vehicle_ids=frozenset({_VID_CLOUD}),
            campaign_arns=frozenset(),  # no campaigns
            vehicle_id_to_fleet={},
        )
        assert vehicles[0]["simulation_ready"] is True, (
            "M13 DETECTED: campaign check added for cloud-telemetry — "
            "MRDN0000000000013 would report not-ready despite being fully simulatable"
        )
        assert "no_telemetry_campaign" not in vehicles[0]["not_ready_reasons"]
        assert count == 1


# ---------------------------------------------------------------------------
# Mutation M14: return only first failing reason
# ---------------------------------------------------------------------------


class TestMutationM14OnlyFirstReason:
    """M14: if only the first failing reason were returned, a vehicle missing
    all three prerequisites would only show one reason rather than three.

    This class pins the all-reasons-reported invariant.
    """

    def test_vehicle_with_all_three_failures_reports_all_three(self):
        """Return only first reason → len(reasons)==1 → this test fails.

        M14 verification: change _annotate_vehicle_readiness to break after
        the first reason is found, confirm THIS test fails, then restore.
        """
        vehicles, _ = h._annotate_vehicle_readiness(
            vehicles=[_VEHICLE_ALL_FAIL],
            enrolled_ids=frozenset(),
            cert_vehicle_ids=frozenset(),
            campaign_arns=frozenset(),
            vehicle_id_to_fleet={},
        )
        reasons = vehicles[0]["not_ready_reasons"]
        assert len(reasons) == 3, (
            f"M14 DETECTED: only {len(reasons)} reason(s) reported — expected 3 "
            "(not_fleet_enrolled, no_certificate, no_telemetry_campaign)"
        )
        assert set(reasons) == {"not_fleet_enrolled", "no_certificate", "no_telemetry_campaign"}



# ---------------------------------------------------------------------------
# Regression: the stale-vin false negative
#
# issues/2026-09-22-cert-readiness-joins-on-unmaintained-vin/
#
# The certificates table is keyed on `vehicleId`; `vin` is an ordinary attribute
# with no uniqueness constraint and no writer keeping it in step with
# cms-{stage}-storage-vehicles. Four of 22 staging rows carried a stale `vin`, and
# each one was disabled in the picker with a false "no device certificate":
#
#   vehicleId       vehicles.vin          certs.vin
#   VEH-MRDN-0015   MRDN0000000000015     MRDN0000000000011
#   VEH-VO-001      1G1FY6S07N4100001     MRDN0000000000012
#   VEH-ENT-001     4T1B11HK0LU98765      WPR00000000000002
#   VEH-MICH-001    MRDN0000000000014     ACME0000000000001
# ---------------------------------------------------------------------------


class TestStaleVinFalseNegative:
    """A cert row with a correct vehicleId and a stale vin must count as certified."""

    # The real staging values, not invented ones — this is the case that shipped.
    _VID = "VEH-MRDN-0015"
    _VEHICLES_VIN = "MRDN0000000000015"
    _CERTS_STALE_VIN = "MRDN0000000000011"

    def _vehicle(self):
        return {
            "vehicleId": self._VID,
            "vin": self._VEHICLES_VIN,
            "dataSource": "vehicle-telemetry",
            "producer": "meridian",
        }

    def test_vehicle_is_ready_despite_a_stale_vin_on_its_cert_row(self):
        """The headline regression.

        The cert set is keyed by vehicleId, so it contains VEH-MRDN-0015 even though
        the row's `vin` attribute says MRDN0000000000011.

        Mutation: reverting the join to `vin not in cert_vehicle_ids` makes this fail
        with reasons == ["no_certificate"] — which is precisely the production
        symptom (VEH-MRDN-0015 disabled in the dropdown as "no device certificate").
        """
        vehicles, ready = h._annotate_vehicle_readiness(
            vehicles=[self._vehicle()],
            enrolled_ids=frozenset({self._VID}),
            # Keyed on vehicleId — what the table actually guarantees.
            cert_vehicle_ids=frozenset({self._VID}),
            campaign_arns=frozenset({f"vehicle:{self._VEHICLES_VIN}"}),
            vehicle_id_to_fleet={self._VID: "MERIDIAN-OEM"},
        )
        assert vehicles[0]["not_ready_reasons"] == []
        assert vehicles[0]["simulation_ready"] is True
        assert ready == 1

    def test_a_vin_in_the_cert_set_does_NOT_certify_its_own_vehicle(self):
        """Discriminates the "union both fields" near-miss fix.

        Passing the vehicle's OWN vin (not its vehicleId) must still yield
        no_certificate, because after this fix the set contains vehicleIds only and a
        vin appearing there identifies nothing.

        Mutation: `vid in set or vin in set` — which fixes the four broken vehicles
        and therefore looks correct — makes this fail. That union is rejected because
        it keeps depending on `vin`, and a stale `vin` on one row can equal a
        DIFFERENT vehicle's real vin, falsely certifying a vehicle that has no cert
        row at all. No such collision exists in staging today (checked: 0), so the
        risk is latent and this test is the only thing that will catch it.

        An earlier version of this test passed a FOREIGN stale vin and did not
        discriminate the union at all — it was found by running the mutation, not by
        reading the test.
        """
        vehicles, ready = h._annotate_vehicle_readiness(
            vehicles=[self._vehicle()],
            enrolled_ids=frozenset({self._VID}),
            cert_vehicle_ids=frozenset({self._VEHICLES_VIN}),
            campaign_arns=frozenset({f"vehicle:{self._VEHICLES_VIN}"}),
            vehicle_id_to_fleet={self._VID: "MERIDIAN-OEM"},
        )
        assert "no_certificate" in vehicles[0]["not_ready_reasons"]
        assert ready == 0

    def test_a_foreign_stale_vin_does_NOT_certify_the_vehicle(self):
        """A set holding only some other row's stale vin certifies nobody."""
        vehicles, ready = h._annotate_vehicle_readiness(
            vehicles=[self._vehicle()],
            enrolled_ids=frozenset({self._VID}),
            cert_vehicle_ids=frozenset({self._CERTS_STALE_VIN}),
            campaign_arns=frozenset({f"vehicle:{self._VEHICLES_VIN}"}),
            vehicle_id_to_fleet={self._VID: "MERIDIAN-OEM"},
        )
        assert "no_certificate" in vehicles[0]["not_ready_reasons"]
        assert ready == 0

    def test_a_genuinely_uncertified_vehicle_is_still_not_ready(self):
        """Anti-vacuity: the fix must not make every vehicle certified."""
        vehicles, ready = h._annotate_vehicle_readiness(
            vehicles=[self._vehicle()],
            enrolled_ids=frozenset({self._VID}),
            cert_vehicle_ids=frozenset(),
            campaign_arns=frozenset({f"vehicle:{self._VEHICLES_VIN}"}),
            vehicle_id_to_fleet={self._VID: "MERIDIAN-OEM"},
        )
        assert "no_certificate" in vehicles[0]["not_ready_reasons"]
        assert ready == 0

    def test_campaign_check_still_joins_on_vin_not_vehicleId(self):
        """The campaign join is a DIFFERENT join and must not be 'fixed' too.

        Campaign targetArns are genuinely VIN-shaped (`vehicle:{vin}`), so that
        check correctly uses vin. A well-meaning sweep that switched every join to
        vehicleId would break it — and the cert test above would still pass, so this
        is the only thing standing between that mistake and a silent regression.
        """
        vehicles, _ = h._annotate_vehicle_readiness(
            vehicles=[self._vehicle()],
            enrolled_ids=frozenset({self._VID}),
            cert_vehicle_ids=frozenset({self._VID}),
            # vehicleId-shaped target: must NOT satisfy the campaign check.
            campaign_arns=frozenset({f"vehicle:{self._VID}"}),
            vehicle_id_to_fleet={self._VID: "MERIDIAN-OEM"},
        )
        assert "no_telemetry_campaign" in vehicles[0]["not_ready_reasons"]



# ---------------------------------------------------------------------------
# Campaign attribution — which campaign will run, not just whether one will
# ---------------------------------------------------------------------------


class TestTelemetryCampaignAttribution:
    """`telemetry_campaigns` reports WHICH campaigns cover a vehicle, and how many
    distinct signals they collect between them.

    Motivation: `no_telemetry_campaign` told an operator something was missing but
    not what would run if it were not.

    **This class was rewritten, not extended.** Its first version asserted a single
    `telemetry_campaign` chosen by most-specific-first precedence, on the assumption
    that one campaign covers a vehicle. That is false: FleetWise runs every matching
    campaign concurrently, 8 targetArns on staging carry more than one, and
    `vehicle:MRDN0000000000015` carries four totalling 295 distinct signals — which
    the singular version reported as "1 signal". Precedence survives as the ORDERING
    of the list; it is no longer a selection.
    """

    VIN = "VIN-ATTRIB-001"
    VID = "VEH-ATTRIB-001"
    FLEET = "flt-attrib-001"

    _DEFAULT = object()   # distinct from None, which must mean "genuinely fleetless"

    def _annotate(self, campaigns, data_source="vehicle-telemetry", fleet=_DEFAULT):
        """`fleet=None` means the vehicle resolves to NO fleet. The sentinel exists
        because the first version of this helper defaulted on `None` and so silently
        substituted the real fleet — which let mutation T3 (dropping the
        `fleet_id is not None` arm) survive."""
        resolved_fleet = self.FLEET if fleet is self._DEFAULT else fleet
        vehicles = [{"vehicleId": self.VID, "vin": self.VIN, "dataSource": data_source}]
        annotated, _ = h._annotate_vehicle_readiness(
            vehicles,
            enrolled_ids=frozenset({self.VID}),
            cert_vehicle_ids=frozenset({self.VID}),
            campaign_arns=campaigns,
            vehicle_id_to_fleet={self.VID: resolved_fleet},
        )
        return annotated[0]

    def _coverage(self, target, name="cms-fleet-gps-10s", cid="camp-1", signals=3,
                  ids=None):
        """One covering campaign, in the list shape the real fetcher returns.

        `ids` defaults to a distinct range per campaign id so a union across two
        campaigns is genuinely larger than either — a shared default would make an
        entry-sum bug and a correct union indistinguishable.
        """
        if ids is None:
            base = abs(hash(cid)) % 1000 * 10
            ids = {str(base + n) for n in range(signals)}
        return {target: [{
            "campaignId": cid, "campaignName": name, "signalCount": signals,
            "signalIds": set(ids),
        }]}

    def _merge(self, *covs):
        """Merge coverage dicts, concatenating lists that share a targetArn."""
        out = {}
        for c in covs:
            for k, v in c.items():
                out.setdefault(k, []).extend(v)
        return out

    def test_per_vehicle_assignment_is_attributed(self):
        v = self._annotate(self._coverage(f"vehicle:{self.VIN}", ids={"1", "2", "3"}))
        assert v["simulation_ready"] is True
        assert v["telemetry_campaigns"] == [{
            "scope": "vehicle", "target": f"vehicle:{self.VIN}",
            "campaignId": "camp-1", "campaignName": "cms-fleet-gps-10s", "signalCount": 3,
        }]
        assert v["telemetry_signal_total"] == 3
        # signalIds is internal and MUST NOT reach the response — it is a set, which
        # json.dumps cannot serialise, so a leak is a 500 on the whole vehicle list.
        assert "signalIds" not in v["telemetry_campaigns"][0]

    def test_fleet_assignment_is_attributed(self):
        v = self._annotate(self._coverage(f"fleet:{self.FLEET}"))
        assert v["simulation_ready"] is True
        assert v["telemetry_campaigns"][0]["scope"] == "fleet"
        assert v["telemetry_campaigns"][0]["target"] == f"fleet:{self.FLEET}"

    def test_broadcast_assignment_is_attributed(self):
        v = self._annotate(self._coverage("all"))
        assert v["telemetry_campaigns"][0]["scope"] == "broadcast"

    def test_every_matching_scope_is_reported_not_just_the_most_specific(self):
        """THE defect this rewrite exists for.

        A vehicle with a per-vehicle AND a fleet AND a broadcast campaign is
        collecting all three — FleetWise does not pick one. The previous
        implementation `break`-ed on the first match and reported 1 of 3.
        """
        cov = self._merge(
            self._coverage("all", name="broadcast-camp", cid="c-all"),
            self._coverage(f"fleet:{self.FLEET}", name="fleet-camp", cid="c-fleet"),
            self._coverage(f"vehicle:{self.VIN}", name="vehicle-camp", cid="c-veh"),
        )
        v = self._annotate(cov)
        assert len(v["telemetry_campaigns"]) == 3
        assert {c["campaignName"] for c in v["telemetry_campaigns"]} == {
            "broadcast-camp", "fleet-camp", "vehicle-camp",
        }

    def test_ordering_is_most_specific_first(self):
        """Precedence survives as ORDERING. The operator's first question is what is
        assigned to this vehicle specifically; inherited coverage comes after.

        Mutation: reordering the scope tuple, or dropping the explicit sort, makes
        this fail.
        """
        cov = self._merge(
            self._coverage("all", name="broadcast-camp", cid="c-all"),
            self._coverage(f"fleet:{self.FLEET}", name="fleet-camp", cid="c-fleet"),
            self._coverage(f"vehicle:{self.VIN}", name="vehicle-camp", cid="c-veh"),
        )
        v = self._annotate(cov)
        assert [c["scope"] for c in v["telemetry_campaigns"]] == [
            "vehicle", "fleet", "broadcast",
        ]

    def test_ordering_is_stable_regardless_of_scan_order(self):
        """DynamoDB scan order is not guaranteed. Two campaigns on the SAME target
        must come back in the same order however the fetcher happened to append
        them, or the panel's headline changes on refresh with no data change.
        """
        a = {"campaignId": "c-a", "campaignName": "a-camp", "signalCount": 9,
             "signalIds": {"1"}}
        b = {"campaignId": "c-b", "campaignName": "b-camp", "signalCount": 40,
             "signalIds": {"2"}}
        forward = self._annotate({f"vehicle:{self.VIN}": [a, b]})
        reverse = self._annotate({f"vehicle:{self.VIN}": [b, a]})
        assert [c["campaignId"] for c in forward["telemetry_campaigns"]] == \
               [c["campaignId"] for c in reverse["telemetry_campaigns"]]
        # Larger first within a scope: the 40-signal campaign is the one that
        # characterises what the vehicle is doing.
        assert forward["telemetry_campaigns"][0]["campaignId"] == "c-b"

    def test_signal_total_is_the_distinct_union_not_the_sum_of_counts(self):
        """THE number the old shape got wrong, and the reason the union is computed
        server-side rather than summed client-side.

        Live: `vehicle:MRDN0000000000015` carries 4 campaigns summing to 304 entries
        but covering 295 distinct signals. Ids repeat both within one campaign and
        across campaigns, so a caller adding up `signalCount` overstates collection.
        """
        overlapping = self._merge(
            self._coverage(f"vehicle:{self.VIN}", cid="c-1", signals=3,
                           ids={"1", "2", "3"}),
            self._coverage(f"fleet:{self.FLEET}", cid="c-2", signals=3,
                           ids={"3", "4", "5"}),
        )
        v = self._annotate(overlapping)
        assert sum(c["signalCount"] for c in v["telemetry_campaigns"]) == 6
        assert v["telemetry_signal_total"] == 5, (
            "id '3' is in both campaigns, so the union is 5 and the entry sum is 6"
        )

    def test_signal_total_dedupes_within_a_single_campaign_too(self):
        """`cms-fleet-gps-10s` is 293 entries and 286 distinct — 7 ids repeat inside
        one campaign. The entry count is kept as-is (it is what the campaigns list
        view shows) while the total reflects reality."""
        v = self._annotate(
            self._coverage(f"vehicle:{self.VIN}", signals=4, ids={"1", "1", "2"}),
        )
        assert v["telemetry_campaigns"][0]["signalCount"] == 4
        assert v["telemetry_signal_total"] == 2

    def test_no_coverage_reports_an_empty_list_and_keeps_the_reason(self):
        v = self._annotate({})
        assert v["telemetry_campaigns"] == []
        assert v["telemetry_signal_total"] == 0
        assert "no_telemetry_campaign" in v["not_ready_reasons"]

    def test_cloud_telemetry_is_not_applicable_and_is_not_flagged(self):
        """A cloud-telemetry vehicle reaches MSK via the rule path and needs no
        FleetWise campaign. Reporting one as missing would be a false alarm.

        Mutation: dropping the `campaign_applicable` guard on the reason append
        makes this fail by flagging a vehicle that is working correctly.
        """
        v = self._annotate({}, data_source="cloud-telemetry")
        assert v["telemetry_campaign_applicable"] is False
        assert "no_telemetry_campaign" not in v["not_ready_reasons"]

    def test_vehicle_telemetry_is_applicable(self):
        v = self._annotate(self._coverage(f"vehicle:{self.VIN}"))
        assert v["telemetry_campaign_applicable"] is True

    def test_a_fleetless_vehicle_does_not_match_a_fleet_campaign(self):
        """`fleet:None` must never be probed as a target.

        Mutation: dropping the `if fleet_id is not None` arm builds the literal
        string "fleet:None", which would match a campaign row targeting that
        string. Fails here because attribution would come back non-None.
        """
        v = self._annotate(self._coverage("fleet:None"), fleet=None)
        assert v["telemetry_campaigns"] == []

    def test_a_set_shaped_coverage_still_attributes_scope_without_inventing_identity(self):
        """Backward compatibility. Every pre-existing readiness test passes a
        frozenset, which carries no payload — scope and target are still reported,
        and the identity fields are None rather than fabricated."""
        v = self._annotate(frozenset({f"vehicle:{self.VIN}"}))
        assert v["telemetry_campaigns"][0]["scope"] == "vehicle"
        assert v["telemetry_campaigns"][0]["campaignId"] is None
        assert v["telemetry_campaigns"][0]["campaignName"] is None
        # No payload to union over, so the total is honestly 0 rather than guessed.
        assert v["telemetry_signal_total"] == 0

    def test_a_dict_shaped_coverage_is_still_accepted(self):
        """The prior single-campaign shape `{target: {...}}`. Accepted so a caller
        mid-deploy cannot produce a false `no_telemetry_campaign`. Dropping the
        `isinstance(entry, dict)` arm makes this fail."""
        v = self._annotate({f"vehicle:{self.VIN}": {
            "campaignId": "c-old", "campaignName": "old-shape", "signalCount": 2,
            "signalIds": {"7", "8"},
        }})
        assert [c["campaignName"] for c in v["telemetry_campaigns"]] == ["old-shape"]
        assert v["telemetry_signal_total"] == 2

    def test_indeterminate_readiness_asserts_no_campaign_at_all(self):
        """When the scan failed, claiming a resolved campaign would be a false
        claim — the fields must be ABSENT, not None-valued, matching the existing
        `readiness_unavailable` sentinel's posture."""
        vehicles = [{"vehicleId": self.VID, "vin": self.VIN, "dataSource": "vehicle-telemetry"}]
        annotated, _ = h._annotate_vehicle_readiness(
            vehicles,
            enrolled_ids=frozenset({self.VID}),
            cert_vehicle_ids=frozenset({self.VID}),
            campaign_arns=None,          # scan failed
            vehicle_id_to_fleet={self.VID: self.FLEET},
        )
        v = annotated[0]
        assert v["not_ready_reasons"] == ["readiness_unavailable"]
        assert "telemetry_campaigns" not in v
        assert "telemetry_signal_total" not in v
        assert "telemetry_campaign_applicable" not in v


class TestCampaignCoverageCarriesIdentity:
    """The fetch returns a mapping keyed by targetArn, so membership still works
    for every existing caller while the values enable attribution."""

    def _call(self, items):
        table = MagicMock()
        table.scan.return_value = {"Items": items}
        resource = MagicMock()
        resource.Table.return_value = table
        return h._fetch_campaign_covered_target_arns(ddb_resource=resource)

    def test_membership_still_works_for_existing_callers(self):
        result = self._call([{
            "targetArn": "vehicle:VIN-A",
            "signalsToCollect": [{"name": "Vehicle.Speed"}],
            "campaignId": "c1", "campaignName": "gps-10s",
        }])
        assert "vehicle:VIN-A" in result

    def test_identity_and_signal_count_are_carried(self):
        """Mutation: dropping campaignId/campaignName from the ProjectionExpression
        makes these None and fails here — and the UI would render a campaign it
        cannot name."""
        result = self._call([{
            "targetArn": "vehicle:VIN-A",
            "signalsToCollect": [{"name": "a"}, {"name": "b"}],
            "campaignId": "c1", "campaignName": "gps-10s",
        }])
        assert result["vehicle:VIN-A"] == [{
            "campaignId": "c1", "campaignName": "gps-10s", "signalCount": 2,
            "signalIds": {"{'name': 'a'}", "{'name': 'b'}"},
        }]

    def test_signalless_campaign_is_still_excluded(self):
        """The enrichment must not weaken the existing filter: a RUNNING campaign
        with no signals produces no telemetry and must not count as coverage."""
        result = self._call([{
            "targetArn": "vehicle:VIN-A", "signalsToCollect": [],
            "campaignId": "c1", "campaignName": "empty",
        }])
        assert "vehicle:VIN-A" not in result

    def test_projection_requests_the_identity_fields(self):
        """Asserts on the ProjectionExpression PASSED TO scan, not on the payload.

        A MagicMock returns whatever items the test supplies regardless of the
        projection, so a payload assertion cannot observe the projection being
        narrowed — mutation T4 (dropping campaignId/campaignName) survived the
        payload-shaped test for exactly that reason. Against the real service the
        fields would simply be absent and the UI would render a campaign it cannot
        name.
        """
        table = MagicMock()
        table.scan.return_value = {"Items": []}
        resource = MagicMock()
        resource.Table.return_value = table
        h._fetch_campaign_covered_target_arns(ddb_resource=resource)
        projection = table.scan.call_args.kwargs["ProjectionExpression"]
        for field in ("targetArn", "signalsToCollect", "campaignId", "campaignName"):
            assert field in projection, (
                f"ProjectionExpression must request {field!r} — without it the "
                f"attribution fields come back None from the real table"
            )

    def test_every_campaign_on_a_duplicate_target_is_kept(self):
        """Rewritten, not deleted. The prior assertion was
        `test_first_writer_wins_on_a_duplicate_target`, which pinned a `setdefault`
        that discarded every campaign after the first on the same targetArn.

        That was the bug: FleetWise runs all of them, so dropping the rest
        understated collection — by 300x on `vehicle:MRDN0000000000015`, which
        carries four campaigns and was reported as collecting 1 signal.
        """
        result = self._call([
            {"targetArn": "all", "signalsToCollect": [{"name": "a"}],
             "campaignId": "first", "campaignName": "one"},
            {"targetArn": "all", "signalsToCollect": [{"name": "b"}],
             "campaignId": "second", "campaignName": "two"},
        ])
        assert [c["campaignId"] for c in result["all"]] == ["first", "second"]

    def test_signal_ids_are_captured_for_the_union(self):
        """Without `signalIds` the distinct union is not computable and a caller has
        to sum the per-campaign counts, which double-counts shared signals."""
        result = self._call([{
            "targetArn": "vehicle:VIN-A", "signalsToCollect": [1, 2, 2, 3],
            "campaignId": "c1", "campaignName": "dupes",
        }])
        entry = result["vehicle:VIN-A"][0]
        assert entry["signalCount"] == 4, "entry count is not de-duplicated"
        assert entry["signalIds"] == {"1", "2", "3"}, "ids are"

    def test_signal_ids_coerce_mixed_numeric_types_to_one_member(self):
        """DynamoDB hands back `Decimal`; a fixture may use `int` or `str`. If those
        do not collapse, the union inflates and the panel overstates collection."""
        from decimal import Decimal
        result = self._call([{
            "targetArn": "vehicle:VIN-A", "signalsToCollect": [Decimal("1"), 1, "1"],
            "campaignId": "c1", "campaignName": "mixed",
        }])
        assert result["vehicle:VIN-A"][0]["signalIds"] == {"1"}
