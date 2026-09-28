# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for simulate_vehicle/handler.py — spec Group 6 (T6.1 + T6.2).

Verify plan (from tasks.md T6.1 + T6.2):

T6.1 — path derived from dataSource:
  - One case per `dataSource` value:
      vehicle-telemetry → FWE/MQTT path resolved
      cloud-telemetry   → ingest API path resolved
  - Missing dataSource → 400 with explicit reason, NOT defaulted to either path
  - No caller-supplied path/transport parameter accepted (mutations a)
  - No defaulting of missing dataSource (mutation b)
  - Picker lists ALL vehicles regardless of producer (mutation c)

T6.2 — identity-write guard:
  - Post-simulation vehicle record is byte-identical on producer/sold_to/oem_source
  - Source-structural check: this module has no write of those fields
  - Mutation d: adding a producer write is detected

Four mutations, each confirmed to fail:
  (a) accept a caller-supplied path and honour it
  (b) default a missing dataSource to 'cloud-telemetry'
  (c) filter the vehicle picker to producer == meridian
  (d) add a producer write in a simulation path
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# sys.path: make simulate_vehicle importable
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
# Fixtures
# ---------------------------------------------------------------------------

_VID_VT = "VEH-MRDN-001"  # vehicle-telemetry
_VID_CT = "VEH-OEM1-001"  # cloud-telemetry — OEM1 vehicle, intentionally
_VID_NONE = "VEH-NO-DS-001"  # no dataSource
_VID_UNKNOWN_DS = "VEH-UNKNOWN-DS-001"  # unknown dataSource

_VEHICLE_VT = {
    "vehicleId": _VID_VT,
    "vin": "1MRDN0000000001",
    "dataSource": "vehicle-telemetry",
    "producer": "meridian",
}
_VEHICLE_CT = {
    "vehicleId": _VID_CT,
    "vin": "1MRDN0000000037",
    "dataSource": "cloud-telemetry",
    # Group 8: was `producer: "oem1"`. The fixture encoded an assumption that
    # cloud-telemetry implies oem1, which is false — live staging carries 16
    # meridian cloud-telemetry vehicles, and the `cloud-oem1` -> `cloud-telemetry`
    # rename (spec 2026-06-09-cms-data-source-model-refactor) decoupled the two
    # axes on purpose. VEH-CS-DEMO-0037 is exactly this shape: Meridian, offboard.
    # It has to be Meridian now because the producer gate rejects anything else,
    # and this fixture's job is exercising the cloud DISPATCH, not the gate.
    "producer": "meridian",
    "sold_to": "CUST-00400001",
    "oem_source": "",
}
# Group 8 gate fixtures. Separate from _VEHICLE_CT so the dispatch tests and the
# producer-gate tests cannot silently trade places: a dispatch test that starts
# failing because its vehicle became non-simulatable is a confusing way to learn
# that the gate works.
_VEHICLE_CT_OEM1 = {
    "vehicleId": "VEH-OEM1-GATE-001",
    "vin": "1OEM10000000001",
    "dataSource": "cloud-telemetry",  # valid transport — only producer disqualifies it
    "producer": "oem1",
    "oem_source": "oem1",
}
_VEHICLE_CT_TESLA = {
    "vehicleId": "VEH-TESLA-GATE-001",
    "vin": "5YJ30000000000001",
    "dataSource": "cloud-telemetry",
    "producer": "tesla",
}
_VEHICLE_NO_PRODUCER = {
    "vehicleId": "VEH-NOPROD-GATE-001",
    "vin": "1NOPRD000000001",
    "dataSource": "vehicle-telemetry",
    # deliberately no `producer` — absent attribution must fail closed, not be
    # read as Meridian ownership
}
_VEHICLE_NO_DS_OEM1 = {
    "vehicleId": "VEH-OEM1-NODS-001",
    "vin": "1OEM10000000002",
    "producer": "oem1",
    # deliberately BOTH non-Meridian and missing dataSource — pins the check
    # ORDER: the actionable defect (no dataSource) must win over the scoping rule
}
_VEHICLE_NO_DS = {
    "vehicleId": _VID_NONE,
    "vin": "1NODS0000000001",
    "producer": "meridian",
    # deliberately no `dataSource`
}
_VEHICLE_UNKNOWN_DS = {
    "vehicleId": _VID_UNKNOWN_DS,
    "vin": "1UNKN0000000001",
    "dataSource": "mqtt-direct",  # not a recognised value
    "producer": "meridian",
}


def _event_post(vehicle_id: str, extra_body: dict | None = None) -> dict:
    """Minimal API Gateway event for POST /simulate/start — operator-authenticated.

    Carries `connected-services` group claims so authz passes by default.
    Use ``_unauthed_event_post`` to test unauthorized paths.
    """
    body: dict = {"vehicle_id": vehicle_id}
    if extra_body:
        body.update(extra_body)
    return {
        "body": json.dumps(body),
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "operator-alice",
                    "cognito:groups": "connected-services",
                }
            }
        },
    }


def _event_get() -> dict:
    """Minimal API Gateway event for GET /simulate/vehicles — operator-authenticated.

    Carries `connected-services` group claims so authz passes by default.
    Use ``_unauthed_event_get`` to test unauthorized paths.
    """
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


def _unauthed_event_get() -> dict:
    """API Gateway event with no Cognito claims — simulates unauthenticated caller."""
    return {"requestContext": {}}


def _unauthed_event_post(vehicle_id: str) -> dict:
    """API Gateway event with no Cognito claims — simulates unauthenticated caller."""
    return {"body": json.dumps({"vehicle_id": vehicle_id}), "requestContext": {}}


def _mock_vehicles_table(vehicles: list[dict]) -> MagicMock:
    """Stub vehicles table.

    get_item is keyed on vehicleId.
    scan returns one page, and **honours a `producer` FilterExpression** the way
    DynamoDB does.

    Group 8 note on why the filter is simulated rather than ignored: the picker's
    Meridian-only property is an OUTCOME (non-Meridian rows must not come back).
    A mock that ignored `FilterExpression` would make that outcome untestable,
    leaving only "the scan carried a FilterExpression" — an assertion about
    PRESENCE, not about the property, and one that passes just as happily if the
    filter names the wrong producer or the wrong attribute. Both kinds of
    assertion now exist: this simulation backs the outcome tests, and
    `test_list_vehicles_filters_server_side_not_in_python` separately pins that
    the filtering happens in DynamoDB rather than in Python afterwards.

    Only the single-attribute equality form this module emits is supported. A
    more complex FilterExpression raises rather than silently returning
    everything, so a future filter change cannot quietly make these tests vacuous.
    """
    table = MagicMock()
    by_id = {v["vehicleId"]: v for v in vehicles}

    def _get_item(Key):  # noqa: N803
        item = by_id.get(Key.get("vehicleId"))
        return {"Item": item} if item else {}

    def _scan(**kwargs):
        items = list(vehicles)
        filter_expr = kwargs.get("FilterExpression")
        if filter_expr is not None:
            names = kwargs.get("ExpressionAttributeNames") or {}
            values = kwargs.get("ExpressionAttributeValues") or {}
            expr = str(filter_expr).strip()
            # Expect exactly `#x = :y`; resolve #x via ExpressionAttributeNames.
            parts = expr.split("=")
            if len(parts) != 2:
                raise AssertionError(
                    f"_mock_vehicles_table only simulates `#attr = :val` filters; "
                    f"got {expr!r}. Extend this stub rather than letting the "
                    "outcome tests silently stop filtering."
                )
            lhs, rhs = parts[0].strip(), parts[1].strip()
            attr = names.get(lhs, lhs.lstrip("#"))
            expected = values.get(rhs)
            items = [v for v in items if v.get(attr) == expected]
        return {"Items": items}

    table.get_item.side_effect = _get_item
    table.scan.side_effect = _scan
    return table


# ---------------------------------------------------------------------------
# Simulation-Lambda invoke stub (T6.1)
# ---------------------------------------------------------------------------
#
# `simulate_start_handler` now invokes the simulation Lambda, so every happy-path
# test needs the invoke stubbed AND `SIMULATION_FUNCTION_NAME` set — the handler
# reads it via `_required_env`, which raises rather than guessing.
#
# The stub returns an API-Gateway-shaped payload because that is what the real
# callee returns (`simulation_lambda._resp`).  Returning a bare dict here would
# let the handler's body-parsing regress undetected.

_SIM_FUNCTION_NAME = "cms-test-simulation-api"


def _sim_payload(status_code: int = 200, body: dict | None = None) -> MagicMock:
    """A stand-in for the streaming `Payload` object `invoke()` returns."""
    if body is None:
        # The REALISTIC callee success shape, `task_arn` included
        # (`simulation_lambda.py:1807`, `:1896`). The earlier default omitted it —
        # the one ARN-bearing field — which is why no happy-path test noticed the
        # success body was being forwarded verbatim (review cycle 2, W1). Keeping it
        # here means every happy-path test now implicitly checks it is stripped.
        body = {
            "success": True,
            "simulation_id": "sim-12345",
            "status": "running",
            "task_arn": (
                "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/"
                "abc123def4567890abc123def4567890"
            ),
        }
    stream = MagicMock()
    stream.read.return_value = json.dumps(
        {"statusCode": status_code, "body": json.dumps(body)}
    ).encode("utf-8")
    return stream


def _mock_lambda_client(status_code: int = 200, body: dict | None = None,
                        function_error: str | None = None) -> MagicMock:
    """Stub Lambda client whose `invoke` mimics a RequestResponse round trip.

    `invoke` is the API the code under test actually calls — naming any other
    method here would produce a test that passes because its assertion never
    fires (the stub-names-the-wrong-API trap recorded in RESUME.md).
    """
    client = MagicMock()
    result: dict = {"StatusCode": 200, "Payload": _sim_payload(status_code, body)}
    if function_error:
        result["FunctionError"] = function_error
    client.invoke.return_value = result
    return client


@pytest.fixture(autouse=True)
def sim_lambda():
    """Stub the simulation-Lambda invoke for every test in this module.

    Autouse so the pre-T6.1 tests keep exercising the paths they were written for
    without each one growing invoke plumbing.  It is deliberately NOT a no-op:
    tests that must prove no dispatch happened (the 403 cases) assert
    ``invoke.call_count == 0`` against this same stub, which is a stronger check
    than the absence of a stub would give.

    It also pins ``_STAGE`` and ``DEPLOYMENT_STAGE``.  ``_STAGE`` is bound at import
    time with a ``"staging"`` fallback, so whether this module's
    ``os.environ.setdefault`` won the race depended on which test module imported the
    handler first — the rule-name assertions below passed alone and failed in the
    full suite.  ``DEPLOYMENT_STAGE`` is pinned too because the dispatch now reads it
    fail-closed at call time via ``_dispatch_stage()`` (review cycle 1, W1).  Both are
    pinned so the expectations stay written longhand instead of derived from the
    handler's own values, which would make them tautological.

    Tests that assert on the dispatch payload take this fixture by name.
    """
    client = _mock_lambda_client()
    with patch.object(h, "_get_lambda_client", return_value=client), \
            patch.object(h, "_STAGE", "test"), \
            patch.dict(os.environ, {"DEPLOYMENT_STAGE": "test"}):
        yield client


# ---------------------------------------------------------------------------
# T6.1 — path derived from dataSource
# ---------------------------------------------------------------------------


class TestSimulateStartHandler:
    """simulate_start_handler — path is a consequence of dataSource."""

    def _start(self, vehicle_id: str, vehicles: list[dict] | None = None,
                extra_body: dict | None = None):
        if vehicles is None:
            vehicles = [_VEHICLE_VT, _VEHICLE_CT, _VEHICLE_NO_DS]
        table = _mock_vehicles_table(vehicles)
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(vehicle_id, extra_body), None)
        return resp, table

    # -- Happy paths ----------------------------------------------------------

    def test_vehicle_telemetry_resolves_fwe_path(self):
        """vehicle-telemetry → FWE/MQTT path, 200."""
        resp, _ = self._start(_VID_VT)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["data_source"] == "vehicle-telemetry"
        assert "FWE" in body["dispatch"] or "MQTT" in body["dispatch"]

    def test_cloud_telemetry_dispatch_names_the_rule_path(self):
        """cloud-telemetry → 200, and the `dataSource` is the vehicle's.

        The `dispatch` field's correctness is owned by
        `TestDispatchDescriptionMatchesTheDispatch`, which asserts the
        description/config correspondence properly. This test deliberately does NOT
        re-assert it: cycle 3 (S3) found the two had become byte-identical
        duplicates, and two owners for one property means the weaker one sets the
        bar. Originally `test_cloud_telemetry_resolves_ingest_path`, authored against
        the pre-T6.1 resolve-only contract.
        """
        resp, _ = self._start(_VID_CT)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["data_source"] == "cloud-telemetry"

    def test_vehicle_telemetry_response_names_the_vehicle_id(self):
        resp, _ = self._start(_VID_VT)
        body = json.loads(resp["body"])
        assert body["vehicle_id"] == _VID_VT

    def test_cloud_telemetry_response_names_the_vehicle_id(self):
        resp, _ = self._start(_VID_CT)
        body = json.loads(resp["body"])
        assert body["vehicle_id"] == _VID_CT

    # -- Missing dataSource → rejected, NOT defaulted -------------------------

    def test_missing_data_source_returns_400_explicit_reason(self):
        """A vehicle with no dataSource is REJECTED, not defaulted to either path."""
        resp, _ = self._start(_VID_NONE)
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "no_data_source"
        assert "no" in body["error"].lower() and "dataSource" in body["error"]

    def test_missing_data_source_error_names_the_vehicle_id(self):
        resp, _ = self._start(_VID_NONE)
        body = json.loads(resp["body"])
        assert _VID_NONE in body["error"]

    def test_unknown_data_source_returns_400(self):
        """An unrecognised dataSource is rejected, not silently treated as either path."""
        resp, _ = self._start(_VID_UNKNOWN_DS, vehicles=[_VEHICLE_UNKNOWN_DS])
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason", "").startswith("unknown_data_source")

    # -- Not found ------------------------------------------------------------

    def test_unknown_vehicle_id_returns_404(self):
        resp, _ = self._start("VEH-DOES-NOT-EXIST")
        assert resp["statusCode"] == 404
        body = json.loads(resp["body"])
        assert "not found" in body["error"].lower()

    # -- Bad request ----------------------------------------------------------

    def test_missing_vehicle_id_returns_400(self):
        table = _mock_vehicles_table([_VEHICLE_VT])
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(
                {
                    "body": "{}",
                    "requestContext": {
                        "authorizer": {
                            "claims": {
                                "sub": "operator-alice",
                                "cognito:groups": "connected-services",
                            }
                        }
                    },
                },
                None,
            )
        assert resp["statusCode"] == 400

    def test_invalid_json_body_returns_400(self):
        table = _mock_vehicles_table([_VEHICLE_VT])
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(
                {
                    "body": "not-json",
                    "requestContext": {
                        "authorizer": {
                            "claims": {
                                "sub": "operator-alice",
                                "cognito:groups": "connected-services",
                            }
                        }
                    },
                },
                None,
            )
        assert resp["statusCode"] == 400


# ---------------------------------------------------------------------------
# Mutation (a): accept a caller-supplied path and honour it
# ---------------------------------------------------------------------------

class TestMutationA:
    """Mutation (a): if the handler accepted a caller-supplied 'data_source' param
    and honoured it, it would bypass the derived-path invariant.  Verify the handler
    IGNORES any caller-supplied transport parameter.
    """

    def test_caller_supplied_data_source_is_ignored(self):
        """Even if the caller sends 'data_source': 'cloud-telemetry' for a
        vehicle-telemetry vehicle, the handler returns the VEHICLE'S dataSource.
        """
        table = _mock_vehicles_table([_VEHICLE_VT])
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            # Caller tries to override: vehicle is vehicle-telemetry but caller
            # injects 'cloud-telemetry' in the body.
            event = _event_post(_VID_VT, extra_body={"data_source": "cloud-telemetry"})
            resp = h.simulate_start_handler(event, None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        # Must honour the VEHICLE's dataSource, not the caller's injection
        assert body["data_source"] == "vehicle-telemetry", (
            "MUTATION (a) DETECTED: handler honoured caller-supplied data_source "
            "instead of deriving from the vehicle record"
        )

    def test_resolve_simulation_path_ignores_override_field(self):
        """resolve_simulation_path derives from the vehicle dict only — no second arg."""
        import inspect
        sig = inspect.signature(h.resolve_simulation_path)
        params = list(sig.parameters)
        assert len(params) == 1, (
            f"MUTATION (a) DETECTED: resolve_simulation_path has {len(params)} "
            "parameters — it should accept only the vehicle dict, never an override"
        )


# ---------------------------------------------------------------------------
# Mutation (b): default a missing dataSource to 'cloud-telemetry'
# ---------------------------------------------------------------------------

class TestMutationB:
    """Mutation (b): if the handler defaulted a missing dataSource to cloud-telemetry,
    it would silently route the vehicle through the wrong transport.
    """

    def test_missing_data_source_is_not_defaulted_to_cloud_telemetry(self):
        """A vehicle with no dataSource must NOT resolve to cloud-telemetry."""
        table = _mock_vehicles_table([_VEHICLE_NO_DS])
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_NONE), None)
        assert resp["statusCode"] != 200, (
            "MUTATION (b) DETECTED: missing dataSource was silently defaulted to a path "
            "instead of being rejected with an explicit error"
        )
        body = json.loads(resp["body"])
        # The rejection must be explicit — not silently resolved
        assert body.get("reason") == "no_data_source", (
            f"MUTATION (b) DETECTED: reason is {body.get('reason')!r}, not 'no_data_source'"
        )

    def test_missing_data_source_is_not_defaulted_to_vehicle_telemetry(self):
        """Also guard the other default direction."""
        table = _mock_vehicles_table([_VEHICLE_NO_DS])
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_NONE), None)
        assert resp["statusCode"] == 400, (
            "MUTATION (b) DETECTED: missing dataSource defaulted to vehicle-telemetry "
            "instead of being rejected"
        )

    def test_resolve_simulation_path_no_default(self):
        """resolve_simulation_path with no dataSource returns error, never a path."""
        vehicle_no_ds = {"vehicleId": "VEH-X", "producer": "meridian"}
        data_source, reason = h.resolve_simulation_path(vehicle_no_ds)
        assert reason is not None and reason != "", (
            "MUTATION (b) DETECTED: resolve_simulation_path returned no reason "
            "for a vehicle with no dataSource"
        )
        assert data_source == "", (
            f"MUTATION (b) DETECTED: got data_source={data_source!r} for a vehicle "
            "with no dataSource — should be empty string"
        )


# ---------------------------------------------------------------------------
# Group 8: picker IS filtered to producer == meridian (reverses mutation (c))
# ---------------------------------------------------------------------------

class TestPickerIsMeridianOnly:
    """The picker must list ONLY `producer == meridian` vehicles.

    **This class was `TestMutationC`, and it asserted the exact opposite.** It
    existed to guard spec T6.1's deliberately-unfiltered picker, whose docstring
    read: *"if list_vehicles_handler filtered on producer==meridian, it would
    exclude OEM1 vehicles that may legitimately be simulated."* The assertions
    are INVERTED rather than deleted, because the property really was
    mutation-guarded and the reversal is a product decision, not a bug fix — a
    future surface could legitimately want T6.1 back.

    Why it reversed (user, 2026-09-20): there is no oem1 or tesla simulation path.
    Simulating a `cloud-telemetry` vehicle publishes over MQTT basic-ingest to the
    CS product rule and does NOT drive that vehicle's real ingest route, so
    listing oem1/tesla vehicles advertised capability that does not exist. *"CS is
    a meridian OEM owned portal, not a tesla portal."*

    Filtering is distinct from authorization — these tests do NOT imply any caller
    may access the endpoint. See ``TestAuthorizationListVehicles``.
    """

    _ALL_VEHICLES = [_VEHICLE_VT, _VEHICLE_CT, _VEHICLE_NO_DS,
                     _VEHICLE_CT_OEM1, _VEHICLE_CT_TESLA, _VEHICLE_NO_PRODUCER]

    def _list(self):
        table = _mock_vehicles_table(self._ALL_VEHICLES)
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.list_vehicles_handler(_event_get(), None)
        return resp, table

    def test_list_vehicles_excludes_oem1_vehicles(self):
        """Inverted from test_list_vehicles_includes_oem1_vehicles."""
        resp, _ = self._list()
        assert resp["statusCode"] == 200
        ids = {v["vehicleId"] for v in json.loads(resp["body"])["vehicles"]}
        assert _VEHICLE_CT_OEM1["vehicleId"] not in ids, (
            "an oem1 vehicle reached the picker — CS can only simulate Meridian "
            "vehicles, and there is no oem1 simulation path for it to use"
        )

    def test_list_vehicles_excludes_tesla_vehicles(self):
        resp, _ = self._list()
        ids = {v["vehicleId"] for v in json.loads(resp["body"])["vehicles"]}
        assert _VEHICLE_CT_TESLA["vehicleId"] not in ids

    def test_list_vehicles_excludes_producerless_vehicles(self):
        """Absent attribution is not evidence of Meridian ownership."""
        resp, _ = self._list()
        ids = {v["vehicleId"] for v in json.loads(resp["body"])["vehicles"]}
        assert _VEHICLE_NO_PRODUCER["vehicleId"] not in ids

    def test_list_vehicles_includes_meridian_vehicles(self):
        """Anti-vacuity floor: the filter must not exclude everything.

        Without this, every exclusion test above passes against a picker that
        returns nothing at all.
        """
        resp, _ = self._list()
        assert resp["statusCode"] == 200
        ids = {v["vehicleId"] for v in json.loads(resp["body"])["vehicles"]}
        assert _VID_VT in ids, "Meridian onboard vehicle missing from the picker"
        assert _VID_CT in ids, "Meridian OFFBOARD vehicle missing — cloud-telemetry is not oem1-only"

    def test_list_vehicles_reports_the_filter_was_applied(self):
        """Inverted: `filtered_on_producer` was asserted False, now must be True.

        The flag is part of the response contract, so leaving it False after
        filtering would make the payload describe itself incorrectly.
        """
        resp, _ = self._list()
        body = json.loads(resp["body"])
        assert body.get("filtered_on_producer") is True, (
            "filtered_on_producer must be True now that the scan filters on "
            f"producer; got {body.get('filtered_on_producer')!r}"
        )

    def test_list_vehicles_filters_server_side_not_in_python(self):
        """Inverted from test_list_vehicles_no_scan_filter_expression.

        Asserts the DynamoDB scan itself carries the FilterExpression. Filtering
        in Python after an unfiltered scan would produce the same response body
        while still reading every OEM's rows out of the table, so a body-only
        assertion cannot tell the two apart.

        T5.0 note: list_vehicles_handler now issues multiple Table() + scan()
        calls for the readiness helpers (enrollment, certs, campaigns). Only the
        VEHICLES table scan must carry the producer FilterExpression; the others
        carry their own unrelated filters. We assert the property on scans that
        carry the `ExpressionAttributeValues` key `:producer` — those are the
        vehicles-table scans.
        """
        _, table = self._list()
        scan_calls = table.scan.call_args_list
        assert scan_calls, "scan was not called"
        # Check only the vehicles-table scan calls (those carrying :producer).
        vehicles_scans = [
            call for call in scan_calls
            if (call.kwargs or (call.args[0] if call.args else {})).get(
                "ExpressionAttributeValues", {}
            ).get(":producer") is not None
        ]
        assert vehicles_scans, (
            "no scan call carried :producer in ExpressionAttributeValues — "
            "the vehicles table scan must filter on producer server-side"
        )
        for call in vehicles_scans:
            kwargs = call.kwargs or (call.args[0] if call.args else {})
            filter_expr = kwargs.get("FilterExpression")
            assert filter_expr is not None, (
                "vehicles scan was called without a FilterExpression — the producer filter "
                "must be applied server-side, not after the fact in Python"
            )
            values = kwargs.get("ExpressionAttributeValues") or {}
            assert values.get(":producer") == "meridian", (
                f"scan filtered on an unexpected producer value: {values!r}"
            )

    def test_pagination_survives_an_empty_filtered_page(self):
        """A FilterExpression is applied per page AFTER the read, so a page can
        return zero Items with a LastEvaluatedKey still set. The loop must keep
        going; stopping on an empty page silently truncates the picker.

        T5.0 note: list_vehicles_handler now calls scan() on 3 additional tables
        for readiness checks. The `side_effect` list supplies 2 vehicles-scan
        responses; remaining calls (readiness tables) use a fresh MagicMock
        which returns a valid-but-empty scan response via its implicit .return_value.
        We check that the second vehicles-page vehicle reaches the response rather
        than asserting a fixed scan call count.
        """
        table = MagicMock()
        table.scan.side_effect = [
            {"Items": [], "LastEvaluatedKey": {"vehicleId": "cursor-1"}},
            {"Items": [_VEHICLE_VT]},
            # Remaining calls are readiness scans — return empty pages
            {"Items": []},
            {"Items": []},
            {"Items": []},
        ]
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.list_vehicles_handler(_event_get(), None)
        ids = {v["vehicleId"] for v in json.loads(resp["body"])["vehicles"]}
        assert _VID_VT in ids, (
            "the vehicle on page 2 was lost — the pagination loop stopped on an "
            "empty filtered page instead of following LastEvaluatedKey"
        )

    def test_list_all_vehicles_filters_at_the_source(self):
        """Inverted from test_list_all_vehicles_does_not_filter_source.

        `_list_all_vehicles` is the shared read; asserting at this level rather
        than only through the handler keeps the property pinned if another caller
        is added later.
        """
        vehicles = [_VEHICLE_VT, _VEHICLE_CT, _VEHICLE_NO_DS, _VEHICLE_UNKNOWN_DS,
                    _VEHICLE_CT_OEM1, _VEHICLE_CT_TESLA, _VEHICLE_NO_PRODUCER]
        table = _mock_vehicles_table(vehicles)
        result = h._list_all_vehicles(table)
        returned_ids = {v["vehicleId"] for v in result}
        for v in (_VEHICLE_CT_OEM1, _VEHICLE_CT_TESLA, _VEHICLE_NO_PRODUCER):
            assert v["vehicleId"] not in returned_ids, (
                f"non-Meridian vehicle {v['vehicleId']!r} "
                f"(producer={v.get('producer')!r}) reached the picker"
            )
        for v in (_VEHICLE_VT, _VEHICLE_CT, _VEHICLE_NO_DS, _VEHICLE_UNKNOWN_DS):
            assert v["vehicleId"] in returned_ids, (
                f"Meridian vehicle {v['vehicleId']!r} was filtered out — the "
                "producer filter must not also filter on dataSource"
            )


# ---------------------------------------------------------------------------
# T6.2 + Mutation (d): identity-write guard
# ---------------------------------------------------------------------------


class TestIdentityWriteGuard:
    """T6.2 — the simulator never writes producer, sold_to, or oem_source."""

    def test_simulate_start_does_not_update_vehicle_record(self):
        """After simulate_start_handler, the vehicle record is unchanged on
        producer, sold_to, and oem_source.
        """
        original_vehicle = dict(_VEHICLE_CT)  # OEM1 vehicle with all three fields
        table = _mock_vehicles_table([original_vehicle])

        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_CT), None)

        assert resp["statusCode"] == 200, json.loads(resp["body"])

        # Verify NO update calls were made on the vehicle table
        assert table.update_item.call_count == 0, (
            "MUTATION (d) DETECTED: simulate_start_handler called update_item — "
            "it must never write back to the vehicle record"
        )
        assert table.put_item.call_count == 0, (
            "MUTATION (d) DETECTED: simulate_start_handler called put_item — "
            "it must never write back to the vehicle record"
        )

    def test_identity_fields_byte_identical_after_simulation(self):
        """post-simulation: producer, sold_to, oem_source unchanged."""
        original = dict(_VEHICLE_CT)
        captured_writes: list[dict] = []

        table = MagicMock()
        table.get_item.return_value = {"Item": dict(original)}
        # Intercept any write attempts
        table.update_item.side_effect = lambda **kw: captured_writes.append(("update", kw))
        table.put_item.side_effect = lambda **kw: captured_writes.append(("put", kw))

        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            h.simulate_start_handler(_event_post(_VID_CT), None)

        # No write that touches any identity field
        for write_type, kwargs in captured_writes:
            update_expr = kwargs.get("UpdateExpression", "")
            for field in ("producer", "sold_to", "oem_source"):
                assert field not in update_expr, (
                    f"MUTATION (d) DETECTED: {write_type} contained {field!r} "
                    "in UpdateExpression — identity fields must never be written"
                )

    def test_list_vehicles_does_not_write_to_vehicle_record(self):
        """list_vehicles_handler is read-only — no write operations on the vehicle table."""
        table = _mock_vehicles_table([_VEHICLE_VT, _VEHICLE_CT])
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            h.list_vehicles_handler(_event_get(), None)
        assert table.update_item.call_count == 0
        assert table.put_item.call_count == 0


class TestSourceStructuralNoIdentityWrites:
    """Source-structural check: no write of producer, sold_to, oem_source anywhere
    in this module.

    This is the T6.2 executable assertion.  A comment-level rule is not
    sufficient — this assertion catches a future developer adding a
    'convenience' write to the handler.
    """

    _HANDLER_FILE = Path(__file__).resolve().parent.parent / "handler.py"

    def _source_text(self) -> str:
        assert self._HANDLER_FILE.exists(), (
            f"Handler file not found: {self._HANDLER_FILE} — "
            "fix the path anchor before trusting this test"
        )
        return self._HANDLER_FILE.read_text(encoding="utf-8")

    def test_handler_file_is_found_and_non_empty(self):
        """Anti-vacuity: we actually found the file."""
        text = self._source_text()
        assert len(text) > 200, (
            f"Handler file suspiciously short ({len(text)} chars) — "
            "fix the path anchor"
        )

    def test_no_write_of_producer_in_update_expression(self):
        """No `UpdateExpression` in the handler contains 'producer'.

        Mutation (d): add `UpdateExpression='SET producer = :p'` to a write
        path → this test fails.
        """
        source = self._source_text()
        # Look for DynamoDB write patterns that set producer.
        # Split into logical lines for context-aware search.
        lines = source.splitlines()
        write_lines = [
            (i + 1, line)
            for i, line in enumerate(lines)
            if any(kw in line for kw in ("update_item", "put_item", "UpdateExpression"))
        ]
        # None of those lines should reference 'producer' as a written field.
        for lineno, line in write_lines:
            if "producer" in line and not line.strip().startswith("#"):
                # The line could legitimately READ producer in a FilterExpression.
                # Fail only if it appears in a write context.
                assert False, (
                    f"MUTATION (d) DETECTED at line {lineno}: source line "
                    f"{line.strip()!r} writes 'producer' on the vehicle record"
                )

    def test_no_write_of_sold_to_in_handler(self):
        """No write of sold_to on the vehicle record in simulation paths."""
        source = self._source_text()
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            # A write pattern that sets sold_to on a vehicle table operation
            if ("sold_to" in line and
                    any(kw in line for kw in ("update_item", "put_item", "UpdateExpression"))):
                assert False, (
                    f"MUTATION (d) DETECTED at line {i}: source line {stripped!r} "
                    "writes 'sold_to' on the vehicle record"
                )

    def test_no_write_of_oem_source_in_handler(self):
        """No write of oem_source on the vehicle record in simulation paths."""
        source = self._source_text()
        lines = source.splitlines()
        for i, line in enumerate(lines, 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ("oem_source" in line and
                    any(kw in line for kw in ("update_item", "put_item", "UpdateExpression"))):
                assert False, (
                    f"MUTATION (d) DETECTED at line {i}: source line {stripped!r} "
                    "writes 'oem_source' on the vehicle record"
                )

    def test_handler_contains_update_item_zero_calls(self):
        """The handler source has no `update_item(` call at all.

        There is no scenario in which a simulation entry point should write
        back to the vehicle record.  This is the strongest structural form:
        the call site cannot exist.
        """
        source = self._source_text()
        # Strip comments to avoid false positives from the comment block
        uncommented_lines = [
            line for line in source.splitlines()
            if not line.strip().startswith("#")
        ]
        uncommented = "\n".join(uncommented_lines)
        assert "update_item(" not in uncommented, (
            "MUTATION (d) DETECTED: handler source contains `update_item(` — "
            "simulation path must NEVER write to the vehicle record"
        )
        assert "put_item(" not in uncommented, (
            "MUTATION (d) DETECTED: handler source contains `put_item(` — "
            "simulation path must NEVER write to the vehicle record"
        )



# ---------------------------------------------------------------------------
# FG2.1 — authorization tests (new, Fix Group 2)
# ---------------------------------------------------------------------------


def _event_with_groups(groups) -> dict:
    """Build a GET-style event with the given Cognito group list.

    ``groups`` may be a list/tuple of strings, a CSV string, or None/empty to
    simulate a groupless token.
    """
    if isinstance(groups, (list, tuple)):
        groups_str = ",".join(groups)
    elif groups is None:
        groups_str = ""
    else:
        groups_str = str(groups)
    return {
        "requestContext": {
            "authorizer": {
                "claims": {
                    "sub": "test-sub",
                    "cognito:groups": groups_str,
                }
            }
        }
    }


def _post_event_with_groups(vehicle_id: str, groups) -> dict:
    """Build a POST-style event with the given Cognito group list."""
    base = _event_with_groups(groups)
    base["body"] = json.dumps({"vehicle_id": vehicle_id})
    return base


class TestAuthorizationListVehicles:
    """FG2.1 — list_vehicles_handler requires the `connected-services` group.

    These cases establish that authorization is the FIRST gate — DynamoDB is
    never called on a rejected request.  The cases are per-shape as required:
    no claims, no cognito:groups key, empty groups, non-operator group, and a
    positive control for a valid operator.
    """

    def _call(self, event: dict) -> tuple[dict, MagicMock]:
        table = _mock_vehicles_table([_VEHICLE_VT, _VEHICLE_CT])
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.list_vehicles_handler(event, None)
        return resp, table

    # -- Unauthorized cases --------------------------------------------------

    def test_no_claims_returns_403(self):
        """A caller with no requestContext at all gets 403 before any DDB call."""
        resp, table = self._call({"requestContext": {}})
        assert resp["statusCode"] == 403
        assert json.loads(resp["body"]) == {"error": "Forbidden"}
        table.scan.assert_not_called()

    def test_no_cognito_groups_claim_returns_403(self):
        """A token with a `sub` but no `cognito:groups` key gets 403."""
        event = {
            "requestContext": {
                "authorizer": {"claims": {"sub": "some-user"}}
            }
        }
        resp, table = self._call(event)
        assert resp["statusCode"] == 403
        table.scan.assert_not_called()

    def test_empty_groups_returns_403(self):
        """A token with an empty `cognito:groups` string gets 403."""
        resp, table = self._call(_event_with_groups(""))
        assert resp["statusCode"] == 403
        table.scan.assert_not_called()

    def test_empty_groups_list_returns_403(self):
        """A token with an empty groups list gets 403."""
        resp, table = self._call(_event_with_groups([]))
        assert resp["statusCode"] == 403
        table.scan.assert_not_called()

    def test_non_operator_group_returns_403(self):
        """A caller in `subscriber` (but not `connected-services`) gets 403."""
        resp, table = self._call(_event_with_groups(["subscriber"]))
        assert resp["statusCode"] == 403
        assert json.loads(resp["body"]) == {"error": "Forbidden"}
        table.scan.assert_not_called()

    def test_fleet_operator_group_returns_403(self):
        """fleet-operator is not the simulation operator group — gets 403."""
        resp, table = self._call(_event_with_groups(["fleet-operator"]))
        assert resp["statusCode"] == 403
        table.scan.assert_not_called()

    # -- Authorized case (positive control) ----------------------------------

    def test_operator_group_returns_200_and_all_vehicles(self):
        """An authorized operator in `connected-services` gets 200 with all vehicles."""
        resp, table = self._call(_event_get())
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        vehicle_ids = {v["vehicleId"] for v in body["vehicles"]}
        assert _VID_VT in vehicle_ids
        assert _VID_CT in vehicle_ids
        table.scan.assert_called()

    def test_operator_sees_only_meridian_with_a_ddb_filter(self):
        """Inverted from test_operator_sees_all_producers_no_ddb_filter (Group 8).

        Previously asserted `filtered_on_producer is False` and no
        FilterExpression. Both flipped when the picker became Meridian-only.

        T5.0 note: list_vehicles_handler now issues multiple scan() calls for
        readiness helpers. We assert filtered_on_producer is True (response-level
        check) and that at least ONE scan carries a FilterExpression — the vehicles
        scan. We do not assert ALL scans carry FilterExpression since the readiness
        scans have their own, unrelated filters.
        """
        resp, table = self._call(_event_get())
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body.get("filtered_on_producer") is True
        assert table.scan.call_args_list, "scan was not called"
        # At least one scan must carry a FilterExpression (the vehicles table scan)
        any_with_filter = any(
            (call.kwargs or (call.args[0] if call.args else {})).get("FilterExpression") is not None
            for call in table.scan.call_args_list
        )
        assert any_with_filter, "no scan call carried a FilterExpression"

    def test_403_body_is_exact(self):
        """The 403 body is exactly `{"error": "Forbidden"}` — same as the sibling handler."""
        resp, _ = self._call(_unauthed_event_get())
        assert json.loads(resp["body"]) == {"error": "Forbidden"}


class TestAuthorizationSimulateStart:
    """FG2.1 — simulate_start_handler requires the `connected-services` group.

    Authorization is the first gate — DynamoDB is never called on a rejected
    request.
    """

    def _call(self, event: dict, vehicles=None) -> tuple[dict, MagicMock]:
        if vehicles is None:
            vehicles = [_VEHICLE_VT, _VEHICLE_CT]
        table = _mock_vehicles_table(vehicles)
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(event, None)
        return resp, table

    # -- Unauthorized cases --------------------------------------------------

    def test_no_claims_returns_403(self):
        """A caller with no requestContext gets 403 before any DDB call."""
        resp, table = self._call(_unauthed_event_post(_VID_VT))
        assert resp["statusCode"] == 403
        assert json.loads(resp["body"]) == {"error": "Forbidden"}
        table.get_item.assert_not_called()

    def test_no_cognito_groups_claim_returns_403(self):
        """A token with no `cognito:groups` key gets 403."""
        event = _post_event_with_groups(_VID_VT, None)
        del event["requestContext"]["authorizer"]["claims"]["cognito:groups"]
        resp, table = self._call(event)
        assert resp["statusCode"] == 403
        table.get_item.assert_not_called()

    def test_empty_groups_returns_403(self):
        """An empty group string gets 403."""
        resp, table = self._call(_post_event_with_groups(_VID_VT, ""))
        assert resp["statusCode"] == 403
        table.get_item.assert_not_called()

    def test_non_operator_group_returns_403(self):
        """A caller in `subscriber` but not `connected-services` gets 403."""
        resp, table = self._call(_post_event_with_groups(_VID_VT, ["subscriber"]))
        assert resp["statusCode"] == 403
        assert json.loads(resp["body"]) == {"error": "Forbidden"}
        table.get_item.assert_not_called()

    def test_fleet_operator_group_returns_403(self):
        """fleet-operator is not the simulation operator group — gets 403."""
        resp, table = self._call(_post_event_with_groups(_VID_VT, ["fleet-operator"]))
        assert resp["statusCode"] == 403
        table.get_item.assert_not_called()

    # -- Authorized case (positive control) ----------------------------------

    def test_operator_group_resolves_vehicle_telemetry(self):
        """An authorized operator gets 200 and the correct dataSource."""
        resp, table = self._call(_event_post(_VID_VT))
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["data_source"] == "vehicle-telemetry"
        table.get_item.assert_called_once()

    def test_403_body_is_exact(self):
        """The 403 body is exactly `{"error": "Forbidden"}`."""
        resp, _ = self._call(_unauthed_event_post(_VID_VT))
        assert json.loads(resp["body"]) == {"error": "Forbidden"}

    def test_authz_before_ddb_for_unknown_vehicle(self):
        """Authz check happens before the DDB lookup — 403 even for unknown vehicle_id."""
        resp, table = self._call(
            _post_event_with_groups("VEH-DOES-NOT-EXIST", ["subscriber"])
        )
        assert resp["statusCode"] == 403
        table.get_item.assert_not_called()



# ---------------------------------------------------------------------------
# T6.1 — /simulate/start dispatches, and does not report success without it
# ---------------------------------------------------------------------------


class TestSimulateStartDispatches:
    """`/simulate/start` invokes the simulation Lambda and returns its result.

    The property under test is not "an invoke call exists" — it is that the route
    cannot report success without having dispatched. Before T6.1 this handler
    returned 200 with a resolved-path description and started nothing, which is a
    success-shaped failure: the caller, the UI and any smoke test reading the
    status code all see a started simulation. `test_success_requires_a_dispatch`
    below is the assertion that pins that, and it is the one to keep if any other
    in this class is ever trimmed.
    """

    def _start(self, vehicle_id: str, vehicles: list[dict] | None = None,
               extra_body: dict | None = None, event: dict | None = None):
        if vehicles is None:
            vehicles = [_VEHICLE_VT, _VEHICLE_CT, _VEHICLE_NO_DS]
        table = _mock_vehicles_table(vehicles)
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(
                event if event is not None else _event_post(vehicle_id, extra_body),
                None,
            )
        return resp, table

    @staticmethod
    def _sent_config(sim_lambda) -> dict:
        """The `config` body the handler sent to the simulation Lambda."""
        assert sim_lambda.invoke.call_count == 1, (
            f"expected exactly one invoke, got {sim_lambda.invoke.call_count}"
        )
        payload = json.loads(sim_lambda.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        return json.loads(payload["body"])

    @staticmethod
    def _sent_event(sim_lambda) -> dict:
        payload = sim_lambda.invoke.call_args.kwargs["Payload"]
        return json.loads(payload.decode("utf-8"))

    # -- The load-bearing assertion -------------------------------------------

    def test_success_requires_a_dispatch(self, sim_lambda):
        """A 200 from this route implies the simulation Lambda was invoked.

        MUTATION: restore the pre-T6.1 body — return 200 with
        `{"vehicle_id", "data_source", "dispatch", "message"}` and delete the
        invoke — and this fails. A test that only checked `statusCode == 200`
        would pass against both implementations, which is exactly how the
        resolve-only route survived to be deployed.
        """
        resp, _ = self._start(_VID_VT)
        assert resp["statusCode"] == 200
        assert sim_lambda.invoke.call_count == 1, (
            "route returned 200 without invoking the simulation Lambda — this is "
            "the resolve-only behaviour T6.1 replaced"
        )
        body = json.loads(resp["body"])
        assert body.get("simulation_id") == "sim-12345", (
            "a successful start must surface the simulation Lambda's own "
            f"simulation_id; got {body!r}"
        )

    def test_response_carries_the_callee_result_not_a_description(self, sim_lambda):
        """The body is the simulation Lambda's result, not a resolved-path blurb."""
        resp, _ = self._start(_VID_VT)
        body = json.loads(resp["body"])
        assert body["status"] == "running"
        assert "resolved" not in json.dumps(body).lower(), (
            "response still describes a resolved path rather than a started "
            f"simulation: {body!r}"
        )

    # -- The dispatch target --------------------------------------------------

    def test_invokes_the_named_simulation_function(self, sim_lambda):
        """FunctionName comes from SIMULATION_FUNCTION_NAME, not a guess."""
        self._start(_VID_VT)
        assert sim_lambda.invoke.call_args.kwargs["FunctionName"] == _SIM_FUNCTION_NAME

    def test_invocation_is_request_response(self, sim_lambda):
        """Event-type invocation would return before the callee authorized or
        started anything, making every call look successful."""
        self._start(_VID_VT)
        assert sim_lambda.invoke.call_args.kwargs["InvocationType"] == "RequestResponse"

    def test_dispatch_path_reaches_the_start_arm_not_agent_start(self, sim_lambda):
        """The callee dispatches on `path.endswith("/start")` after an earlier arm
        has claimed `/agent/start` — so the path must end in `/start` and must not
        end in `/agent/start`."""
        self._start(_VID_VT)
        sent = self._sent_event(sim_lambda)
        assert sent["httpMethod"] == "POST"
        assert sent["path"].endswith("/start")
        assert not sent["path"].endswith("/agent/start")

    # -- The two config axes --------------------------------------------------

    def test_vehicle_telemetry_dispatches_fwe_mode_and_cms_native_rule(self, sim_lambda):
        """vehicle-telemetry is fed onboard: FWE mode, CMS-native rule."""
        self._start(_VID_VT)
        config = self._sent_config(sim_lambda)
        assert config["mode"] == "fwe"
        assert config["rule_name"] == "cms_test_iot_msk_rule"

    def test_cloud_telemetry_dispatches_mqtt_direct_and_cs_product_rule(self, sim_lambda):
        """cloud-telemetry arrives as a producer feed: no onboard agent, CS product rule."""
        self._start(_VID_CT)
        config = self._sent_config(sim_lambda)
        assert config["mode"] == "mqtt_direct"
        assert config["rule_name"] == "cms_test_cs_product_meridian_ev_rule"

    def test_the_two_data_sources_dispatch_differently(self, sim_lambda):
        """Anti-vacuity for the two tests above: if a refactor collapsed the
        mapping to one arm they would still each pass in isolation only if the
        constants happened to match. Compare the two dispatches directly."""
        self._start(_VID_VT)
        vt = self._sent_config(sim_lambda)
        sim_lambda.invoke.reset_mock()
        self._start(_VID_CT)
        ct = self._sent_config(sim_lambda)
        assert vt["mode"] != ct["mode"], (
            "both dataSources dispatched the same `mode` — the mapping collapsed"
        )
        assert vt["rule_name"] != ct["rule_name"], (
            "both dataSources dispatched the same `rule_name` — the routing "
            "destination is no longer derived from dataSource"
        )

    def test_rule_names_are_on_the_callee_allowlist(self, sim_lambda):
        """`_resolve_rule_name` silently falls back to the CMS-native default for
        any value not on `_ALLOWED_RULE_SUFFIXES`, so a typo here would route CS
        data onto the CMS-native topic with no error anywhere. Restate the
        allowlist longhand rather than importing it — importing would make a
        change to the allowlist change both sides at once."""
        allowed = {"cms_test_iot_msk_rule", "cms_test_cs_product_meridian_ev_rule"}
        for vid in (_VID_VT, _VID_CT):
            sim_lambda.invoke.reset_mock()
            self._start(vid)
            assert self._sent_config(sim_lambda)["rule_name"] in allowed

    def test_dispatch_names_the_vehicle_by_id_and_vin(self, sim_lambda):
        """The callee normalizes bare strings by re-reading DDB; sending both
        avoids a second lookup and pins the VIN this handler already read."""
        self._start(_VID_CT)
        vehicles = self._sent_config(sim_lambda)["vehicles"]
        assert vehicles == [{"vehicleId": _VID_CT, "vin": _VEHICLE_CT["vin"]}]

    def test_dispatch_carries_no_caller_supplied_override(self, sim_lambda):
        """A caller injecting `mode` or `rule_name` must not reach the callee —
        `_SIMULATION_DISPATCH` is the only source for both axes."""
        self._start(_VID_VT, extra_body={
            "mode": "mqtt_direct",
            "rule_name": "cms_test_cs_product_meridian_ev_rule",
            "data_source": "cloud-telemetry",
        })
        config = self._sent_config(sim_lambda)
        assert config["mode"] == "fwe", (
            "caller-supplied `mode` reached the simulation Lambda — the vehicle is "
            "vehicle-telemetry and the derived mode is the only permitted value"
        )
        assert config["rule_name"] == "cms_test_iot_msk_rule", (
            "caller-supplied `rule_name` reached the simulation Lambda — a caller "
            "could route another product's data onto the CS product topic"
        )

    # -- Identity forwarding: the callee re-authorizes the real caller ---------

    def test_caller_claims_are_forwarded_verbatim(self, sim_lambda):
        """The callee reads identity from `requestContext.authorizer.claims` and
        runs its own `_authorize_per_vin`. Omitting the claims would make that
        check deny; substituting a synthetic caller would make this handler an
        authorization bypass for a deployed route."""
        event = _event_post(_VID_VT)
        self._start(_VID_VT, event=event)
        sent = self._sent_event(sim_lambda)
        original = event["requestContext"]["authorizer"]["claims"]
        assert sent["requestContext"]["authorizer"]["claims"] == original

    def test_forwarded_claims_are_not_widened(self, sim_lambda):
        """No group is added on the way through. Injecting `platform-admin` here
        would silently satisfy the callee's admin-only arms."""
        self._start(_VID_VT)
        sent = self._sent_event(sim_lambda)
        groups = sent["requestContext"]["authorizer"]["claims"]["cognito:groups"]
        assert groups == "connected-services"
        assert "platform-admin" not in groups

    # -- Fail closed: no dispatch on any refusal ------------------------------

    def test_unauthorized_caller_causes_no_dispatch(self, sim_lambda):
        """MUTATION (a): remove `_require_operator` and this fails — an
        unauthenticated caller would reach the invoke."""
        resp, table = self._start(_VID_VT, event=_unauthed_event_post(_VID_VT))
        assert resp["statusCode"] == 403
        assert sim_lambda.invoke.call_count == 0, (
            "MUTATION (a) DETECTED: a caller with no claims reached the simulation "
            "Lambda invoke"
        )
        table.get_item.assert_not_called()

    def test_non_operator_group_causes_no_dispatch(self, sim_lambda):
        """A wrong-group caller is refused before dispatch, not just before DDB."""
        resp, _ = self._start(
            _VID_VT, event=_post_event_with_groups(_VID_VT, ["fleet-operator"])
        )
        assert resp["statusCode"] == 403
        assert sim_lambda.invoke.call_count == 0

    def test_missing_data_source_causes_no_dispatch(self, sim_lambda):
        """The dataSource rejection is stricter than the callee's own default
        (`item.get('dataSource', 'vehicle-telemetry')`). That divergence is the
        point of this module, so the refusal must happen before the invoke —
        otherwise the callee's default silently re-enables what we rejected."""
        resp, _ = self._start(_VID_NONE)
        assert resp["statusCode"] == 400
        assert sim_lambda.invoke.call_count == 0, (
            "a vehicle with no dataSource reached the simulation Lambda, whose own "
            "default would resolve it to vehicle-telemetry"
        )

    def test_unknown_data_source_causes_no_dispatch(self, sim_lambda):
        resp, _ = self._start(_VID_UNKNOWN_DS, vehicles=[_VEHICLE_UNKNOWN_DS])
        assert resp["statusCode"] == 400
        assert sim_lambda.invoke.call_count == 0

    def test_unknown_vehicle_causes_no_dispatch(self, sim_lambda):
        resp, _ = self._start("VEH-DOES-NOT-EXIST")
        assert resp["statusCode"] == 404
        assert sim_lambda.invoke.call_count == 0

    # -- Callee failure is not reported as success ----------------------------

    def test_callee_403_propagates_as_403(self):
        """The callee runs its own per-VIN authorization. Flattening its refusal
        to 200 — or to 500 — would hide a real authorization outcome."""
        client = _mock_lambda_client(status_code=403, body={"error": "Forbidden"})
        table = _mock_vehicles_table([_VEHICLE_VT])
        with patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_VT), None)
        assert resp["statusCode"] == 403
        assert json.loads(resp["body"])["error"] == "Forbidden"

    def test_callee_409_propagates(self):
        """`_ensure_telemetry_campaign` can refuse a start with 409
        no_telemetry_campaign. That must reach the operator as 409."""
        client = _mock_lambda_client(
            status_code=409, body={"error": "no telemetry campaign",
                                   "reason": "no_telemetry_campaign"})
        table = _mock_vehicles_table([_VEHICLE_VT])
        with patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_VT), None)
        assert resp["statusCode"] == 409
        assert json.loads(resp["body"])["reason"] == "no_telemetry_campaign"

    def test_function_error_is_502_not_200(self):
        """`invoke()` returns StatusCode 200 for a callee that RAISED; the failure
        is reported in `FunctionError`. Reading only the HTTP status of the invoke
        would report a crashed simulation as a successful start."""
        client = _mock_lambda_client(function_error="Unhandled")
        table = _mock_vehicles_table([_VEHICLE_VT])
        with patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_VT), None)
        assert resp["statusCode"] == 502
        assert json.loads(resp["body"])["reason"] == "simulation_invoke_failed"

    def test_unparseable_callee_payload_is_502(self):
        client = MagicMock()
        stream = MagicMock()
        stream.read.return_value = b"<html>gateway timeout</html>"
        client.invoke.return_value = {"StatusCode": 200, "Payload": stream}
        table = _mock_vehicles_table([_VEHICLE_VT])
        with patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_VT), None)
        assert resp["statusCode"] == 502
        assert json.loads(resp["body"])["reason"] == "simulation_response_unparseable"

    # -- The {vid} route form -------------------------------------------------

    def test_path_parameter_vid_is_accepted(self, sim_lambda):
        """`POST /simulate/start/{vid}` is wired in the CDK but the handler only
        ever read the body, so that route could not succeed for any input."""
        event = {
            "body": "{}",
            "pathParameters": {"vid": _VID_VT},
            "requestContext": {"authorizer": {"claims": {
                "sub": "operator-alice", "cognito:groups": "connected-services"}}},
        }
        resp, _ = self._start(_VID_VT, event=event)
        assert resp["statusCode"] == 200
        assert self._sent_config(sim_lambda)["vehicles"][0]["vehicleId"] == _VID_VT

    def test_body_wins_over_path_parameter(self, sim_lambda):
        """The body is the documented contract; `{vid}` is the fallback."""
        event = {
            "body": json.dumps({"vehicle_id": _VID_VT}),
            "pathParameters": {"vid": _VID_CT},
            "requestContext": {"authorizer": {"claims": {
                "sub": "operator-alice", "cognito:groups": "connected-services"}}},
        }
        self._start(_VID_VT, event=event)
        assert self._sent_config(sim_lambda)["vehicles"][0]["vehicleId"] == _VID_VT

    def test_neither_body_nor_path_still_400s(self, sim_lambda):
        event = {
            "body": "{}",
            "requestContext": {"authorizer": {"claims": {
                "sub": "operator-alice", "cognito:groups": "connected-services"}}},
        }
        resp, _ = self._start(_VID_VT, event=event)
        assert resp["statusCode"] == 400
        assert sim_lambda.invoke.call_count == 0

    # -- Env threading --------------------------------------------------------

    def test_missing_function_name_env_fails_closed(self):
        """`SIMULATION_FUNCTION_NAME` has no default. A declared-but-unthreaded
        env key is the defect class behind
        issues/2026-09-13-cs-consumer-context-keys-never-threaded/ — this route
        must fail loudly rather than invoke a guessed function name."""
        table = _mock_vehicles_table([_VEHICLE_VT])
        client = _mock_lambda_client()
        with patch.dict(os.environ, {"SIMULATION_FUNCTION_NAME": ""}), \
                patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_VT), None)
        assert resp["statusCode"] == 500
        assert client.invoke.call_count == 0, (
            "the handler reached the invoke with no function name configured"
        )


class TestExtendedTripParametersForwarding:
    """RED PHASE (spec `2026-09-19-cs-trip-simulator-parity` T1.3) — the
    request body carries only `vehicle_id` today; the CS Trip Simulator
    feature adds `city`, `trips`, `route_length`, `safety_scenarios`,
    `maintenance_scenarios` as optional pass-through fields, while `mode`
    and `rule_name` stay 100% server-derived (spec.md's central Constraint).

    All six tests below are expected to FAIL until T2.1 ships. They pin the
    exact contract spec.md's Design section and Risks section state:
    byte-for-byte backward compatibility for a vehicle-id-only request, and
    a structural lock-out of client-supplied mode/rule_name — written BEFORE
    the forwarding code exists so a naive `sim_config.update(body)`
    implementation in T2.1 fails test_mode_and_rule_name_cannot_be_overridden
    immediately, per `~/.kiro/steering/testing.md`'s mutation-testing
    discipline for security-relevant properties.

    Uses the module's own `sim_lambda` autouse fixture by name (per this
    file's own documented convention) rather than re-patching
    `_get_lambda_client`/`_STAGE` independently — the fixture already pins
    both, and a second, competing patch risks the exact "which test module
    imported the handler first" race its own docstring describes.
    """

    def _start(self, sim_lambda, vehicle_id: str, extra_body: dict | None = None,
               vehicles: list[dict] | None = None):
        if vehicles is None:
            vehicles = [_VEHICLE_VT, _VEHICLE_CT]
        table = _mock_vehicles_table(vehicles)
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(
                _event_post(vehicle_id, extra_body), None,
            )
        return resp, sim_lambda

    @staticmethod
    def _sent_config(client) -> dict:
        payload = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        return json.loads(payload["body"])

    def test_vehicle_id_only_request_still_defaults_to_city_seattle_trips_3_route_length_20(self, sim_lambda):
        """Backward-compat pin. `simulation_lambda.py::_start`'s own defaults
        today are city="seattle", trips=3, route_length=20 (confirmed by
        direct read, spec.md's Design section) — a vehicle-id-only request
        (today's only supported shape) must dispatch identically after this
        feature ships as it did before it."""
        resp, client = self._start(sim_lambda, _VID_VT)
        assert resp["statusCode"] == 200
        config = self._sent_config(client)
        assert config.get("city") == "seattle"
        assert config.get("trips") == 3
        assert config.get("route_length") == 20

    def test_city_route_length_trips_are_forwarded_when_present(self, sim_lambda):
        resp, client = self._start(sim_lambda, _VID_VT, extra_body={
            "city": "atlanta", "trips": 1, "route_length": 10,
        })
        assert resp["statusCode"] == 200
        config = self._sent_config(client)
        assert config["city"] == "atlanta"
        assert config["trips"] == 1
        assert config["route_length"] == 10

    def test_safety_scenarios_forwarded_and_safety_rate_derived(self, sim_lambda):
        """Mirrors TripSimulatorModal.tsx's own rule:
        `selectedSafety.length > 0 ? 0.9 : 0.15`, reproduced server-side."""
        resp, client = self._start(sim_lambda, _VID_VT, extra_body={
            "safety_scenarios": ["hard_braking_event"],
        })
        assert resp["statusCode"] == 200
        config = self._sent_config(client)
        assert config["safety_scenarios"] == ["hard_braking_event"]
        assert config["safety_rate"] == 0.9

    def test_maintenance_scenarios_forwarded_and_progressive_degradation_derived(self, sim_lambda):
        resp, client = self._start(sim_lambda, _VID_VT, extra_body={
            "maintenance_scenarios": ["low_tire_pressure_P0520"],
        })
        assert resp["statusCode"] == 200
        config = self._sent_config(client)
        assert config["maintenance_scenarios"] == ["low_tire_pressure_P0520"]
        assert config["progressive_degradation"] is True

    def test_unrecognised_city_is_rejected_with_400(self, sim_lambda):
        """The allowlist check must happen BEFORE dispatch, not after a
        failed invoke — asserted by requiring zero invoke calls, not just
        the 400 status, so a future implementation that validates AFTER
        invoking cannot pass this test by accident."""
        resp, client = self._start(sim_lambda, _VID_VT, extra_body={"city": "atlantis"})
        assert resp["statusCode"] == 400
        assert client.invoke.call_count == 0, (
            "the simulation Lambda was invoked despite an unrecognised city — "
            "the allowlist check must gate dispatch, not merely be present "
            "somewhere in the response"
        )

    def test_mode_and_rule_name_cannot_be_overridden_by_the_request_body(self, sim_lambda):
        """The structural guard for spec.md's central Constraint. _VEHICLE_VT
        has dataSource='vehicle-telemetry', which correctly derives mode='fwe'
        server-side. A request body attempting to override both to the
        cloud-telemetry values must have NO EFFECT on the dispatched config —
        not merged, not partially honoured, not logged-and-ignored-differently
        for one field vs the other."""
        resp, client = self._start(sim_lambda, _VID_VT, extra_body={
            "mode": "mqtt_direct",
            "rule_name": "cms_test_cs_product_meridian_ev_rule",
        })
        assert resp["statusCode"] == 200
        config = self._sent_config(client)
        assert config["mode"] == "fwe", (
            f"client-supplied mode leaked into the dispatched config: {config!r}"
        )
        assert config["rule_name"] == "cms_test_iot_msk_rule", (
            f"client-supplied rule_name leaked into the dispatched config: {config!r}"
        )


class TestDispatchStageIsFailClosed:
    """`DEPLOYMENT_STAGE` is the SECOND key governing the dispatch, and T6.1's
    first pass guarded only the first (review cycle 1, W1).

    The stage is a component of `rule_name`, i.e. of the routing destination. The
    callee validates `rule_name` against an allowlist built from ITS OWN stage
    (`simulation_lambda.py:1419`) and silently returns the CMS-native default for
    anything off that list (`:1413`, `:1422`). So a stage mismatch does not error —
    it routes CS-product telemetry onto the CMS-native topic, and this handler logs
    the value it sent rather than the value the callee resolved.

    The property is therefore not "a stage appears in the rule name" but "the stage
    is the DEPLOYED one, and an absent one refuses rather than guesses".
    """

    def _start_with_stage(self, stage_value, vehicle=_VEHICLE_VT, vid=_VID_VT):
        """Run a start with `DEPLOYMENT_STAGE` set to `stage_value`.

        `_STAGE` is deliberately left at its import-time value so these tests
        distinguish reading the env from reading the module constant.
        """
        table = _mock_vehicles_table([vehicle])
        client = _mock_lambda_client()
        env = {"DEPLOYMENT_STAGE": stage_value}
        with patch.dict(os.environ, env), \
                patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(vid), None)
        return resp, client

    @staticmethod
    def _rule_name(client) -> str:
        payload = json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))
        return json.loads(payload["body"])["rule_name"]

    def test_rule_name_uses_the_deployed_stage(self):
        """MUTATION: build the rule name from `_STAGE` (which defaults to
        "staging") instead of the fail-closed read → this fails, because the
        handler would emit `cms_staging_...` on a prod Lambda.
        """
        resp, client = self._start_with_stage("prod")
        assert resp["statusCode"] == 200
        assert self._rule_name(client) == "cms_prod_iot_msk_rule"

    def test_rule_name_does_not_silently_fall_back_to_staging(self):
        """The specific misroute W1 describes: a non-staging deploy emitting a
        staging rule name, which the callee cannot match and silently replaces."""
        _, client = self._start_with_stage("prod")
        assert "staging" not in self._rule_name(client)

    def test_cs_product_rule_also_tracks_the_deployed_stage(self):
        """The CS-product arm is the one that misroutes on a mismatch, so pin it
        separately rather than trusting the CMS-native arm to cover both."""
        _, client = self._start_with_stage("prod", vehicle=_VEHICLE_CT, vid=_VID_CT)
        assert self._rule_name(client) == "cms_prod_cs_product_meridian_ev_rule"

    def test_absent_deployment_stage_refuses_rather_than_guessing(self):
        """MUTATION: fall back to `_STAGE` when the env var is unset → this fails.

        A 500 here is the correct outcome: the alternative is a 200 whose telemetry
        silently landed on the wrong topic.
        """
        resp, client = self._start_with_stage("")
        assert resp["statusCode"] == 500
        assert client.invoke.call_count == 0, (
            "the handler dispatched with a guessed stage — the callee would have "
            "silently rewritten the rule name to the CMS-native default"
        )


class TestDispatchDescriptionMatchesTheDispatch:
    """`dispatch` is returned on success and is the only field telling the caller
    which transport was used. Review cycle 1 (W2) found it naming the production
    producer feed while the dispatch publishes over MQTT basic ingest.

    T6.2 is about to give this response a UI consumer, so the string is a contract.
    """

    def _dispatch_field(self, vid) -> str:
        table = _mock_vehicles_table([_VEHICLE_VT, _VEHICLE_CT])
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(vid), None)
        assert resp["statusCode"] == 200
        return json.loads(resp["body"])["dispatch"]

    def test_cloud_telemetry_dispatch_names_the_transport_actually_used(self):
        """The description must AGREE with the config the same `dataSource` emits.

        Cycle 3 (W1) found the previous form — `"rule" in desc or "msk" in desc`,
        plus `"ingest api" not in desc` — admitted three descriptions that name the
        ONBOARD FWE transport on the cloud arm ("FWE agent + collection scheme →
        MQTT → Flink rule" and two variants). Each is exactly the error cycle 1
        raised as W2, and each passed: they contain `rule`, they lack the two-word
        phrase `ingest api`, and they differ from the vehicle-telemetry string. A
        wider substring allowlist is still a substring allowlist.

        The asymmetry cycle 3 named is the fix. The vehicle-telemetry arm is
        protected for free because `fwe` is a token unique to the onboard path, so
        asserting its presence there also excludes it from the other arm. The cloud
        arm has no such token — `rule` reads perfectly naturally in a sentence about
        the onboard path — so the exclusion has to be explicit.

        What is asserted here is the CORRESPONDENCE between two independently
        authored artifacts: `_DISPATCH_DESCRIPTION` (prose, shown to the caller) and
        `_SIMULATION_DISPATCH` (config, sent to the callee). Neither side is derived
        from the other, so this is not tautological — and it fails when they
        disagree, which is the actual property and the one thing a substring check
        cannot express.
        """
        desc = self._dispatch_field(_VID_CT).lower()

        # Premise: the config really does route this arm at the CS product rule.
        # If that changes, this assertion fires first and names the reason.
        suffix = h._SIMULATION_DISPATCH["cloud-telemetry"]["rule_suffix"]
        assert "cs_product" in suffix, (
            f"cloud-telemetry rule_suffix is {suffix!r} — no longer the CS product "
            "rule, so the expectation below is stale rather than wrong"
        )

        assert "cs product" in desc or "cs_product" in desc, (
            f"cloud-telemetry dispatch description {desc!r} does not name the CS "
            f"product rule, but the config routes it at {suffix!r} — the description "
            "and the config disagree"
        )
        assert "fwe" not in desc, (
            f"cloud-telemetry dispatch description {desc!r} names the onboard FWE "
            "agent, which this arm does not use. This is cycle 1's W2 defect: the "
            "field claims a transport other than the one dispatched."
        )
        assert "ingest api" not in desc, (
            f"cloud-telemetry dispatch description {desc!r} still names the ingest "
            "API, which this dispatch does not use — that is how the vehicle is fed "
            "in production, not what this route started"
        )
        assert desc.count("→") >= 2, (
            f"cloud-telemetry dispatch description {desc!r} is not a journey. A bare "
            "token like 'msk' satisfies every keyword check while telling the caller "
            "nothing about the path taken."
        )

    def test_vehicle_telemetry_dispatch_still_names_fwe(self):
        """The onboard arm, with the symmetric exclusion. `fwe` alone protected this
        arm already; `cs product` absent is asserted so a config/description swap
        between the two arms fails on BOTH sides rather than only on the cloud one.
        """
        desc = self._dispatch_field(_VID_VT).lower()
        suffix = h._SIMULATION_DISPATCH["vehicle-telemetry"]["rule_suffix"]
        assert suffix == "iot_msk_rule", (
            f"vehicle-telemetry rule_suffix is {suffix!r} — expectation is stale"
        )
        assert "fwe" in desc
        assert "cs product" not in desc and "cs_product" not in desc, (
            f"vehicle-telemetry dispatch description {desc!r} names the CS product "
            f"rule, but the config routes it at {suffix!r}"
        )
        assert desc.count("→") >= 2

    def test_the_two_descriptions_differ(self):
        """Anti-vacuity: a single shared string would satisfy both tests above only
        if it mentioned FWE and a rule, so compare them directly."""
        assert self._dispatch_field(_VID_VT) != self._dispatch_field(_VID_CT)


class TestCalleeErrorBodyIsNotPassedThroughOn5xx:
    """The callee's 5xx body must not reach the operator verbatim.

    Security review cycle 1 (W1): the callee's outer catchall is
    `_resp(500, {"error": str(e)})` (`simulation_lambda.py:334`, plus five more
    sites), so an unhandled boto3 error arrives as a rendered `ClientError` —
    carrying the account id, the role ARN, the resource ARN and the action name.
    This handler's own catchall already returns a fixed "Internal server error", so
    forwarding the callee's was the one asymmetric path.

    4xx must still pass through: those are policy statements the operator acts on.
    """

    # A realistic rendered boto3 ClientError. The account id here is the
    # AWS-documentation placeholder — never a real one, since this file is not
    # publish-excluded.
    _LEAKY = (
        "An error occurred (AccessDeniedException) when calling the RunTask "
        "operation: User: arn:aws:sts::123456789012:assumed-role/"
        "cms-staging-subscriptions-simulate-start/x is not authorized to perform: "
        "ecs:RunTask on resource: arn:aws:ecs:us-west-2:123456789012:"
        "task-definition/cms-staging-simulation-worker:31"
    )

    def _start_with_callee(self, status_code, body):
        table = _mock_vehicles_table([_VEHICLE_VT])
        client = _mock_lambda_client(status_code=status_code, body=body)
        with patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_VT), None)
        return resp, json.loads(resp["body"])

    def test_callee_500_body_does_not_reach_the_caller(self):
        """MUTATION: merge `sim_body` verbatim on 5xx → this fails."""
        resp, body = self._start_with_callee(500, {"error": self._LEAKY})
        assert resp["statusCode"] == 500
        rendered = json.dumps(body)
        assert self._LEAKY not in rendered
        assert "arn:aws" not in rendered, (
            f"callee 5xx body leaked an ARN to the operator: {rendered}"
        )
        assert "assumed-role" not in rendered
        assert "AccessDeniedException" not in rendered

    def test_callee_500_leaks_no_account_id(self):
        """Pinned separately from the ARN check: an account id can appear without
        a full ARN (e.g. in a plain sts message)."""
        _, body = self._start_with_callee(500, {"error": self._LEAKY})
        assert "123456789012" not in json.dumps(body)

    def test_callee_500_still_reports_a_500_not_a_200(self):
        """Sanitizing must not also swallow the failure."""
        resp, body = self._start_with_callee(500, {"error": self._LEAKY})
        assert resp["statusCode"] == 500
        assert "error" in body

    def test_callee_502_and_503_are_also_sanitized(self):
        """The rule is >= 500, not == 500."""
        for status in (502, 503):
            _, body = self._start_with_callee(status, {"error": self._LEAKY})
            assert "arn:aws" not in json.dumps(body), f"status {status} leaked"

    def test_machine_reason_token_survives_a_500(self):
        """A short token is safe and useful for the UI; keep it."""
        _, body = self._start_with_callee(
            500, {"error": self._LEAKY, "reason": "worker_launch_failed"})
        assert body["reason"] == "worker_launch_failed"
        assert "arn:aws" not in json.dumps(body)

    def test_sentence_shaped_reason_is_dropped_on_500(self):
        """`reason` is not a safe channel by virtue of its name — an exception
        string placed there must not slip past the `error` sanitization."""
        _, body = self._start_with_callee(
            500, {"reason": self._LEAKY})
        assert "arn:aws" not in json.dumps(body)
        assert body.get("reason") != self._LEAKY

    # -- 4xx must still pass through --------------------------------------------

    def test_callee_403_message_still_passes_through(self):
        resp, body = self._start_with_callee(403, {"error": "Forbidden"})
        assert resp["statusCode"] == 403
        assert body["error"] == "Forbidden"

    def test_callee_409_reason_still_passes_through(self):
        """The operator needs this one to act — it names a missing campaign."""
        resp, body = self._start_with_callee(
            409, {"error": "no telemetry campaign", "reason": "no_telemetry_campaign"})
        assert resp["statusCode"] == 409
        assert body["reason"] == "no_telemetry_campaign"
        assert body["error"] == "no telemetry campaign"

    def test_callee_404_message_still_passes_through(self):
        _, body = self._start_with_callee(404, {"error": "Vehicle not found: 'X'"})
        assert "not found" in body["error"]


class TestCalleeSuccessBodyIsAllowlisted:
    """The 2xx body is allowlisted, not passed through.

    Review cycle 2 (W1): sanitization was gated on `status >= 400`, so the success
    arm never reached `_safe_callee_body`. Both callee success arms return
    `task_arn` (`simulation_lambda.py:1807`, `:1896`) — a full ECS task ARN, so
    every successful start handed the account id and the cluster and
    task-definition names to a `connected-services` operator.

    The suite could not see it for two reasons, both worth keeping in mind: the
    default stub's success body omitted `task_arn`, the one ARN-bearing field, while
    every 5xx test used a realistically rendered `ClientError`; and no test pinned
    the success body in either direction, so narrowing it changed no outcome.
    """

    _TASK_ARN = (
        "arn:aws:ecs:us-west-2:123456789012:task/cms-staging-simulation/"
        "abc123def4567890abc123def4567890"
    )

    def _start_success(self, body):
        table = _mock_vehicles_table([_VEHICLE_VT])
        client = _mock_lambda_client(status_code=200, body=body)
        with patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_VT), None)
        assert resp["statusCode"] == 200
        return json.loads(resp["body"])

    # This is the realistic callee success shape — the one the deployed Lambda
    # actually returns, `task_arn` included.
    def _realistic(self, **extra):
        body = {"success": True, "simulation_id": "sim-12345",
                "task_arn": self._TASK_ARN}
        body.update(extra)
        return body

    def test_success_does_not_leak_the_task_arn(self):
        """MUTATION: merge `sim_body` verbatim on success → this fails."""
        body = self._start_success(self._realistic())
        assert "task_arn" not in body, (
            f"successful start returned task_arn to the operator: {body!r}"
        )
        assert self._TASK_ARN not in json.dumps(body)

    def test_success_leaks_no_arn_or_account_id_in_any_field(self):
        """Pinned on the rendered response rather than on the key name, so moving
        the ARN to a differently-named field is still caught."""
        rendered = json.dumps(self._start_success(self._realistic()))
        assert "arn:aws" not in rendered
        assert "123456789012" not in rendered

    def test_success_still_carries_the_simulation_id(self):
        """Sanitizing must not break T6.3, which polls
        `GET /api/simulation/status/{id}` with this value."""
        body = self._start_success(self._realistic())
        assert body["simulation_id"] == "sim-12345"

    def test_success_carries_the_campaign_warning(self):
        """The advisory names only the VIN the caller supplied
        (`simulation_lambda.py:1592-1596`) and the UI surfaces it, so it is on the
        allowlist. If it were dropped, an FWE agent starting without a campaign
        would look like an unqualified success."""
        warning = "No active campaign assigned to 1MRDN0000000001 — FWE agent will start"
        body = self._start_success(self._realistic(warning=warning))
        assert body["warning"] == warning

    def test_success_allowlist_is_exact(self):
        """The forwarded key set is exactly the allowlist ∩ what the callee sent.

        MUTATION: add any key to `_SUCCESS_PASSTHROUGH_KEYS` → this fails. An
        allowlist that quietly grows is a denylist.
        """
        body = self._start_success(self._realistic(status="running"))
        from_callee = set(body) - {"vehicle_id", "data_source", "dispatch"}
        assert from_callee == {"success", "simulation_id", "status"}, (
            f"unexpected keys forwarded from the callee: {sorted(from_callee)}"
        )

    def test_unknown_future_callee_field_is_not_forwarded(self):
        """The reason this is an allowlist and not a `task_arn` denylist: the next
        ARN-bearing field the callee adds must be excluded by default."""
        body = self._start_success(self._realistic(
            cluster_arn="arn:aws:ecs:us-west-2:123456789012:cluster/cms-staging",
            log_stream="ecs/worker/abc123",
        ))
        assert "cluster_arn" not in body
        assert "log_stream" not in body

    def test_handler_own_fields_survive(self):
        """The three fields this handler contributes are not affected."""
        body = self._start_success(self._realistic())
        assert body["vehicle_id"] == _VID_VT
        assert body["data_source"] == "vehicle-telemetry"
        assert "dispatch" in body

    def test_sub_400_non_2xx_status_is_also_allowlisted(self):
        """`>= 400` left everything below it passing through unexamined. A 399 or a
        302 is not a status this callee emits, but it must not be a hole."""
        table = _mock_vehicles_table([_VEHICLE_VT])
        client = _mock_lambda_client(status_code=399, body=self._realistic())
        with patch.object(h, "_get_lambda_client", return_value=client), \
                patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(_event_post(_VID_VT), None)
        assert "arn:aws" not in json.dumps(json.loads(resp["body"]))

    def test_reason_token_rejects_a_trailing_newline(self):
        """`$` matches before a trailing newline; `\\Z` does not (cycle 2, S1)."""
        assert h._REASON_TOKEN.match("worker_launch_failed") is not None
        assert h._REASON_TOKEN.match("worker_launch_failed\n") is None



# ---------------------------------------------------------------------------
# FG1.T1 — trips / route_length / scenario element type + cap validation
# ---------------------------------------------------------------------------


class TestTripParameterValidation:
    """FG1.T1 (spec `2026-09-19-cs-trip-simulator-parity`, Fix Group 1).

    T2.1 opened `trips`, `route_length`, and the scenario lists as optional body
    fields.  Before this task none had type or cap validation in the CS wrapper.
    The callee passes `trips` through as `str(config.get("trips", 3))` with no
    clamp (`simulation_lambda.py:1546`), so an operator could post
    `trips: 1000000000` and start effectively unbounded ECS work.

    This class pins the guard: wrong type or out-of-range → 400 with a `reason`
    key, BEFORE the simulation Lambda is invoked (assert_not_called).  Bool must
    NOT pass as int — `isinstance(True, int)` is True in Python.
    """

    def _start(self, sim_lambda, extra_body: dict, vehicle=_VEHICLE_VT, vid=_VID_VT):
        vehicles = [vehicle]
        table = _mock_vehicles_table(vehicles)
        with patch.object(h, "_get_ddb_resource") as mock_res:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(
                _event_post(vid, extra_body), None,
            )
        return resp, sim_lambda

    # ── trips ──────────────────────────────────────────────────────────────

    def test_trips_string_is_rejected_with_400(self, sim_lambda):
        """A non-integer `trips` value must be rejected before dispatch."""
        resp, client = self._start(sim_lambda, {"trips": "lots"})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_trips"
        assert client.invoke.call_count == 0, (
            "simulation Lambda was invoked despite invalid trips type"
        )

    def test_trips_float_is_rejected_with_400(self, sim_lambda):
        """A float is not a valid trip count even if it looks like an integer."""
        resp, client = self._start(sim_lambda, {"trips": 3.0})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_trips"
        assert client.invoke.call_count == 0

    def test_trips_bool_true_is_rejected_with_400(self, sim_lambda):
        """`True` is an `int` in Python (`isinstance(True, int)` is True).
        The bool guard must fire BEFORE the int check or True would pass as 1."""
        resp, client = self._start(sim_lambda, {"trips": True})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_trips"
        assert client.invoke.call_count == 0

    def test_trips_bool_false_is_rejected_with_400(self, sim_lambda):
        """`False` is also a bool/int alias — must be rejected."""
        resp, client = self._start(sim_lambda, {"trips": False})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_trips"
        assert client.invoke.call_count == 0

    def test_trips_zero_is_rejected_with_400(self, sim_lambda):
        """Zero trips is not positive — must be rejected."""
        resp, client = self._start(sim_lambda, {"trips": 0})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_trips"
        assert client.invoke.call_count == 0

    def test_trips_negative_is_rejected_with_400(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"trips": -1})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_trips"
        assert client.invoke.call_count == 0

    def test_trips_over_cap_is_rejected_with_400(self, sim_lambda):
        """Cap is 100 — 101 must be rejected."""
        resp, client = self._start(sim_lambda, {"trips": 101})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_trips"
        assert client.invoke.call_count == 0

    def test_trips_at_cap_is_accepted(self, sim_lambda):
        """Exactly 100 is the boundary — must pass."""
        resp, client = self._start(sim_lambda, {"trips": 100})
        assert resp["statusCode"] == 200
        config_body = json.loads(
            json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))["body"]
        )
        assert config_body["trips"] == 100

    def test_trips_at_one_is_accepted(self, sim_lambda):
        """1 is the minimum valid value — must pass."""
        resp, client = self._start(sim_lambda, {"trips": 1})
        assert resp["statusCode"] == 200

    def test_trips_none_absent_uses_default(self, sim_lambda):
        """When `trips` is absent from the body the default (3) is still used."""
        resp, client = self._start(sim_lambda, {})
        assert resp["statusCode"] == 200
        config_body = json.loads(
            json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))["body"]
        )
        assert config_body["trips"] == 3

    # ── route_length ────────────────────────────────────────────────────────

    def test_route_length_string_is_rejected_with_400(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"route_length": "far"})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_route_length"
        assert client.invoke.call_count == 0

    def test_route_length_float_is_rejected_with_400(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"route_length": 10.5})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_route_length"
        assert client.invoke.call_count == 0

    def test_route_length_bool_true_is_rejected_with_400(self, sim_lambda):
        """`True` is an int alias — must be rejected by the bool guard."""
        resp, client = self._start(sim_lambda, {"route_length": True})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_route_length"
        assert client.invoke.call_count == 0

    def test_route_length_bool_false_is_rejected_with_400(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"route_length": False})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_route_length"
        assert client.invoke.call_count == 0

    def test_route_length_zero_is_rejected_with_400(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"route_length": 0})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_route_length"
        assert client.invoke.call_count == 0

    def test_route_length_negative_is_rejected_with_400(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"route_length": -5})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_route_length"
        assert client.invoke.call_count == 0

    def test_route_length_over_cap_is_rejected_with_400(self, sim_lambda):
        """101 exceeds the cap of 100 — must be rejected."""
        resp, client = self._start(sim_lambda, {"route_length": 101})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_route_length"
        assert client.invoke.call_count == 0

    def test_route_length_at_cap_is_accepted(self, sim_lambda):
        """Exactly 100 — boundary value, must pass."""
        resp, client = self._start(sim_lambda, {"route_length": 100})
        assert resp["statusCode"] == 200

    def test_route_length_at_one_is_accepted(self, sim_lambda):
        """1 is the minimum valid route length in the wrapper."""
        resp, client = self._start(sim_lambda, {"route_length": 1})
        assert resp["statusCode"] == 200

    def test_route_length_absent_uses_default(self, sim_lambda):
        resp, client = self._start(sim_lambda, {})
        assert resp["statusCode"] == 200
        config_body = json.loads(
            json.loads(client.invoke.call_args.kwargs["Payload"].decode("utf-8"))["body"]
        )
        assert config_body["route_length"] == 20

    # ── safety_scenarios elements ────────────────────────────────────────────

    def test_safety_scenarios_non_list_is_rejected_with_400(self, sim_lambda):
        """A non-list `safety_scenarios` value must be rejected before dispatch."""
        resp, client = self._start(sim_lambda, {"safety_scenarios": "hard_braking"})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_safety_scenarios"
        assert client.invoke.call_count == 0

    def test_safety_scenarios_dict_is_rejected_with_400(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"safety_scenarios": {"type": "hard_braking"}})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_safety_scenarios"
        assert client.invoke.call_count == 0

    def test_safety_scenarios_list_with_non_string_element_is_rejected(self, sim_lambda):
        """`[{}, 5, None]` must be rejected — elements must all be strings."""
        resp, client = self._start(sim_lambda, {"safety_scenarios": ["hard_braking", 5]})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_safety_scenarios"
        assert client.invoke.call_count == 0

    def test_safety_scenarios_list_with_none_element_is_rejected(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"safety_scenarios": [None]})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_safety_scenarios"
        assert client.invoke.call_count == 0

    def test_safety_scenarios_exceeds_cap_is_rejected(self, sim_lambda):
        """A list of 101 strings must be rejected."""
        resp, client = self._start(sim_lambda, {"safety_scenarios": [f"s{i}" for i in range(101)]})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_safety_scenarios"
        assert client.invoke.call_count == 0

    def test_safety_scenarios_at_cap_is_accepted(self, sim_lambda):
        """Exactly 100 string elements — boundary value, must pass."""
        resp, client = self._start(sim_lambda, {"safety_scenarios": [f"s{i}" for i in range(100)]})
        assert resp["statusCode"] == 200

    def test_safety_scenarios_empty_list_is_accepted(self, sim_lambda):
        """An empty list is a valid (though unusual) input — must pass."""
        resp, client = self._start(sim_lambda, {"safety_scenarios": []})
        assert resp["statusCode"] == 200

    # ── maintenance_scenarios elements ────────────────────────────────────────

    def test_maintenance_scenarios_non_list_is_rejected_with_400(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"maintenance_scenarios": "low_tire_pressure"})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_maintenance_scenarios"
        assert client.invoke.call_count == 0

    def test_maintenance_scenarios_list_with_non_string_element_is_rejected(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"maintenance_scenarios": ["ok_scenario", {}]})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_maintenance_scenarios"
        assert client.invoke.call_count == 0

    def test_maintenance_scenarios_list_with_int_element_is_rejected(self, sim_lambda):
        resp, client = self._start(sim_lambda, {"maintenance_scenarios": [42]})
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_maintenance_scenarios"
        assert client.invoke.call_count == 0

    def test_maintenance_scenarios_exceeds_cap_is_rejected(self, sim_lambda):
        resp, client = self._start(sim_lambda, {
            "maintenance_scenarios": [f"m{i}" for i in range(101)]
        })
        assert resp["statusCode"] == 400
        body = json.loads(resp["body"])
        assert body.get("reason") == "invalid_maintenance_scenarios"
        assert client.invoke.call_count == 0

    def test_maintenance_scenarios_at_cap_is_accepted(self, sim_lambda):
        resp, client = self._start(sim_lambda, {
            "maintenance_scenarios": [f"m{i}" for i in range(100)]
        })
        assert resp["statusCode"] == 200

    # ── invalid fields cause no dispatch ─────────────────────────────────────

    def test_validation_failure_causes_no_dispatch_for_any_field(self, sim_lambda):
        """Anti-composite: all four invalid-field shapes refuse before invoking."""
        bad_bodies = [
            {"trips": "not_an_int"},
            {"route_length": True},
            {"safety_scenarios": "should_be_a_list"},
            {"maintenance_scenarios": [None, 1]},
        ]
        for body in bad_bodies:
            sim_lambda.invoke.reset_mock()
            resp, _ = self._start(sim_lambda, body)
            assert resp["statusCode"] == 400, f"expected 400 for body {body!r}"
            assert sim_lambda.invoke.call_count == 0, (
                f"Lambda was invoked despite invalid body {body!r}"
            )

    # ── 400 responses carry a reason key ─────────────────────────────────────

    def test_all_validation_400s_carry_a_reason_key(self, sim_lambda):
        """Each rejection must carry a machine-readable `reason` key so the UI
        can give a specific error message rather than a generic 'bad request'."""
        bad_bodies = [
            {"trips": "bad"},
            {"trips": 0},
            {"trips": 101},
            {"trips": True},
            {"route_length": "bad"},
            {"route_length": 0},
            {"route_length": 101},
            {"route_length": False},
            {"safety_scenarios": "not_a_list"},
            {"safety_scenarios": [1, 2]},
            {"safety_scenarios": [f"s{i}" for i in range(101)]},
            {"maintenance_scenarios": "not_a_list"},
            {"maintenance_scenarios": [None]},
            {"maintenance_scenarios": [f"m{i}" for i in range(101)]},
        ]
        for body in bad_bodies:
            sim_lambda.invoke.reset_mock()
            resp, _ = self._start(sim_lambda, body)
            assert resp["statusCode"] == 400, f"expected 400 for {body!r}"
            parsed = json.loads(resp["body"])
            assert "reason" in parsed, (
                f"400 response for body {body!r} has no 'reason' key: {parsed!r}"
            )



# ---------------------------------------------------------------------------
# Group 8: the START path enforces the producer rule independently
# ---------------------------------------------------------------------------

class TestStartPathProducerGate:
    """`resolve_simulation_path` must refuse a non-Meridian vehicle.

    This is the half that makes the change a control rather than cosmetics. The
    picker and the start path do NOT share a code path — `list_vehicles_handler`
    calls `_list_all_vehicles` directly, while only the start path routes through
    `resolve_simulation_path`. Filtering the list alone would leave
    `POST /simulate/start` accepting an oem1 `vehicleId` from a hand-built body,
    which is the same guard-on-one-path shape as
    issues/2026-09-13-simulation-start-empty-vehicles-bypasses-fleet-scoping/ —
    a defect fixed in this same repo six days earlier.
    """

    def test_oem1_vehicle_is_refused(self):
        data_source, reason = h.resolve_simulation_path(_VEHICLE_CT_OEM1)
        assert data_source == ""
        assert reason == "not_simulatable_producer:oem1", (
            f"expected a producer refusal naming oem1; got {reason!r}"
        )

    def test_tesla_vehicle_is_refused(self):
        _, reason = h.resolve_simulation_path(_VEHICLE_CT_TESLA)
        assert reason == "not_simulatable_producer:tesla"

    def test_missing_producer_fails_closed(self):
        """Absent attribution must not be read as Meridian ownership."""
        _, reason = h.resolve_simulation_path(_VEHICLE_NO_PRODUCER)
        assert reason == "not_simulatable_producer:unset", (
            f"a producerless vehicle must fail closed; got {reason!r}"
        )

    def test_meridian_onboard_still_resolves(self):
        """Anti-vacuity floor: the gate must not refuse everything."""
        data_source, reason = h.resolve_simulation_path(_VEHICLE_VT)
        assert reason is None and data_source == "vehicle-telemetry"

    def test_meridian_offboard_still_resolves(self):
        """cloud-telemetry is NOT oem1-only — a Meridian offboard vehicle resolves.

        Live staging carries 16 of these, and VEH-CS-DEMO-0037 (named by T5.3's
        live-verification script) is one.
        """
        data_source, reason = h.resolve_simulation_path(_VEHICLE_CT)
        assert reason is None and data_source == "cloud-telemetry"

    def test_data_source_checks_win_over_the_producer_check(self):
        """ORDER pin: a row that is both non-Meridian AND missing dataSource must
        report `no_data_source`.

        The actionable defect is the missing transport — an operator can fix that.
        Reporting the producer instead would hide a broken row behind a scoping
        rule, and the row would still be broken after being re-attributed.
        """
        _, reason = h.resolve_simulation_path(_VEHICLE_NO_DS_OEM1)
        assert reason == "no_data_source", (
            "check order regressed: the producer gate is masking a missing "
            f"dataSource. Got {reason!r}"
        )

    def test_unknown_data_source_also_wins_over_the_producer_check(self):
        vehicle = dict(_VEHICLE_CT_OEM1, dataSource="mqtt-direct")
        _, reason = h.resolve_simulation_path(vehicle)
        assert reason == "unknown_data_source:mqtt-direct"

    def test_refusal_reason_is_prefixed_for_the_ui_to_match(self):
        """The UI matches this family with `startsWith("not_simulatable_producer")`.

        Pinned because the UI cannot enumerate every producer, so the prefix is
        the contract; renaming the token without updating
        SimulateVehicleView.tsx's `simulationStartErrorMessage` would silently
        drop the actionable message and fall through to a raw error string.
        """
        for vehicle in (_VEHICLE_CT_OEM1, _VEHICLE_CT_TESLA, _VEHICLE_NO_PRODUCER):
            _, reason = h.resolve_simulation_path(vehicle)
            assert reason.startswith("not_simulatable_producer"), (
                f"{vehicle['vehicleId']}: reason {reason!r} breaks the UI's prefix match"
            )

    def test_simulate_start_handler_returns_the_refusal(self):
        """End-to-end through the handler, not just the resolver — the refusal has
        to actually reach the caller and must not dispatch anything.
        """
        table = _mock_vehicles_table([_VEHICLE_CT_OEM1])
        with patch.object(h, "_get_ddb_resource") as mock_res, \
                patch.object(h, "_get_lambda_client") as mock_lambda:
            mock_res.return_value.Table.return_value = table
            resp = h.simulate_start_handler(
                _event_post(_VEHICLE_CT_OEM1["vehicleId"]), None
            )
            mock_lambda.return_value.invoke.assert_not_called()
        body = json.loads(resp["body"])
        assert resp["statusCode"] >= 400, f"expected a refusal, got {resp['statusCode']}"
        assert "not_simulatable_producer" in json.dumps(body), (
            f"the refusal reason did not reach the caller: {body!r}"
        )



class TestProducerRefusalMessageIsNotMisleading:
    """The refusal SENTENCE must not blame the dataSource.

    Found by live smoke test against deployed staging, not by any test here: the
    `not_simulatable_producer` reason fell through to the generic
    "has an unknown dataSource" branch, so the API returned

        "vehicle '1FDEU...' has an unknown dataSource.  not_simulatable_producer:oem1"

    for a vehicle whose dataSource was a perfectly valid `cloud-telemetry`. The
    `reason` token was correct throughout, which is exactly why the existing tests
    passed — they assert the token and never read the human sentence. A wrong
    diagnosis is worse than a terse one: it sends an operator to edit the wrong
    field on a healthy row.
    """

    def _start(self, vehicle):
        table = _mock_vehicles_table([vehicle])
        with patch.object(h, "_get_ddb_resource") as mock_res, \
                patch.object(h, "_get_lambda_client"):
            mock_res.return_value.Table.return_value = table
            return h.simulate_start_handler(_event_post(vehicle["vehicleId"]), None)

    def test_producer_refusal_does_not_mention_datasource(self):
        body = json.loads(self._start(_VEHICLE_CT_OEM1)["body"])
        assert body["reason"] == "not_simulatable_producer:oem1"
        assert "dataSource" not in body["error"], (
            "the producer refusal is still blaming the dataSource:\n"
            f"  {body['error']}"
        )

    def test_producer_refusal_names_the_producer_and_the_rule(self):
        body = json.loads(self._start(_VEHICLE_CT_OEM1)["body"])
        assert "oem1" in body["error"], "the message should name the offending producer"
        assert "Meridian" in body["error"], (
            "the message should state the rule, so the operator knows this is scope "
            "and not a broken vehicle record"
        )

    def test_producerless_refusal_reads_sensibly(self):
        """`unset` rather than `None` leaking into operator-facing text."""
        body = json.loads(self._start(_VEHICLE_NO_PRODUCER)["body"])
        assert body["reason"] == "not_simulatable_producer:unset"
        assert "None" not in body["error"]

    def test_unknown_datasource_still_blames_the_datasource(self):
        """Anti-regression floor: the generic branch must keep its own wording.

        Without this, 'fixing' the message above by making it generic for every
        reason would pass the assertions above and lose the real dataSource
        diagnosis.
        """
        body = json.loads(self._start(_VEHICLE_UNKNOWN_DS)["body"])
        assert body["reason"].startswith("unknown_data_source")
        assert "unknown" in body["error"] and "dataSource" in body["error"]
