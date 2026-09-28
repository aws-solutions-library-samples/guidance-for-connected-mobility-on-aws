#!/usr/bin/env python3
"""test_ensure_campaign_suppression.py — Tasks 3.1 + 3.2 (Group 3).

Spec: ``.kiro/specs/2026-09-15-cms-cs-campaign-ownership/tasks.md``
      Task 3.1: Replace coverage scan with paginated targetArn-index query
      Task 3.2: Pin Decision 9 — a RUNNING campaign suppresses the baseline (zero writes)

Decision 9 (decisions.md § "Auto-ensure precedence"):
  User-confirmed verbatim: *"agree with a, we can't attach one on top."*
  When a RUNNING campaign already targets ``vehicle:{vin}``, ``_ensure_telemetry_campaign``
  MUST perform zero writes.  Currently implemented; this suite pins it.

Coverage predicate (decisions.md § "Coverage requires an ACTIVE campaign, which in this
schema means status == 'RUNNING'"):
  status == "RUNNING"  AND  targetArn in ("vehicle:{vin}", "all")
  Templates carry status="ACTIVE" — they do NOT count as coverage.
  SUSPENDED does NOT count.

Mutations required by Task 3.1 Verify (all four must fail when applied):
  (a) make query return empty when a RUNNING vehicle:{vin} row exists
      → the Decision 9 suppression test must fail
  (b) drop pagination follow
      → the multi-page test must fail
  (c) widen predicate to include "ACTIVE"
      → test_filter_expression_does_not_admit_active_status must fail
  (d) widen predicate to include "SUSPENDED"
      → the suspended-still-gets-baseline test must fail

Mutation required by Task 3.2 Verify:
  Remove step 1's early return → test_running_campaign_suppresses_no_put_item FAILS

Run (from repo root):
  PYTHONPATH=services python3 -m pytest services/simulation/tests/test_ensure_campaign_suppression.py -v
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# Path setup — simulation_lambda depends on _lib (bundled via CDK overlay).
# The conftest.py in services/simulation/lambda/ adds this for tests there;
# replicate the same setup here so tests/ can also import simulation_lambda.
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_LAMBDA_DIR = os.path.normpath(os.path.join(_HERE, "..", "lambda"))
_CONNECTOR_ROOT = os.path.normpath(
    os.path.join(_HERE, "..", "..", "connectors", "oem1")
)
for _p in (_LAMBDA_DIR, _CONNECTOR_ROOT):
    if _p not in sys.path:
        sys.path.insert(0, _p)

# ---------------------------------------------------------------------------
# Environment variables simulation_lambda needs at import time
# ---------------------------------------------------------------------------
os.environ.setdefault("AWS_REGION", "us-east-1")
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("DEPLOYMENT_STAGE", "test")
os.environ.setdefault("ECS_CLUSTER", "test-cluster")
os.environ.setdefault(
    "WORKER_TASK_DEF",
    "arn:aws:ecs:us-west-2:111111111111:task-definition/cms-test-worker:1",
)
os.environ.setdefault("WORKER_SUBNETS", "subnet-aaa,subnet-bbb")
os.environ.setdefault("WORKER_SECURITY_GROUP", "sg-zzz")
os.environ.setdefault("SIMULATIONS_TABLE", "cms-test-simulations")
os.environ.setdefault("FWE_TASK_DEF", "cms-test-fwe-agent")
os.environ.setdefault("FWE_SIM_TASK_DEF", "cms-test-fwe-simulator")

import simulation_lambda as sut  # noqa: E402

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

_VIN = "1HGBH41JXMN109186"
_CAMPAIGN_ID = f"cms-fleet-telemetry-30s-{_VIN}"

_RUNNING_VEHICLE_ITEM = {
    "campaignId": _CAMPAIGN_ID,
    "targetArn": f"vehicle:{_VIN}",
    "status": "RUNNING",
}

_RUNNING_ALL_ITEM = {
    "campaignId": "cms-fleet-telemetry-30s-all-broadcast",
    "targetArn": "all",
    "status": "RUNNING",
}

_ACTIVE_TEMPLATE_ITEM = {
    "campaignId": "cms-fleet-gps-10s",
    "targetArn": "template",
    "status": "ACTIVE",
    "signalsToCollect": [{"name": "Vehicle.Speed"}],
    "collectionScheme": {"type": "TIME_BASED", "periodMs": 30000},
    "decoderManifestId": "cms-fleet-v3",
    "campaignName": "cms-fleet-gps-10s",
}

_SUSPENDED_ITEM = {
    "campaignId": _CAMPAIGN_ID,
    "targetArn": f"vehicle:{_VIN}",
    "status": "SUSPENDED",
}


def _make_table_mock():
    return MagicMock()


# ---------------------------------------------------------------------------
# Task 3.2 — Decision 9 pin: zero writes when RUNNING campaign exists
# ---------------------------------------------------------------------------

class TestRunningCampaignSuppressesBaseline(unittest.TestCase):
    """Decision 9: a RUNNING authored campaign suppresses the platform baseline.

    Asserts on write calls (put_item), NOT merely on the return value.
    A test that only checks the returned campaign_id passes even when a redundant
    row was written — which is exactly what Task 3.2 is designed to prevent.

    Mutation to verify (Task 3.2 Verify): remove step 1's early return so a baseline
    is always created → this test MUST fail.
    """

    def setUp(self):
        self.mock_table = _make_table_mock()
        # Query returns a RUNNING vehicle:{vin} row on the first call.
        def _side_effect(**kwargs):
            t = kwargs.get("ExpressionAttributeValues", {}).get(":t")
            if t == f"vehicle:{_VIN}":
                return {"Items": [_RUNNING_VEHICLE_ITEM]}
            return {"Items": []}

        self.mock_table.query.side_effect = _side_effect
        self.mock_table.scan.return_value = {"Items": [_ACTIVE_TEMPLATE_ITEM]}

        self.table_patcher = patch.object(
            sut.ddb, "Table", return_value=self.mock_table
        )
        self.table_patcher.start()

    def tearDown(self):
        self.table_patcher.stop()

    def test_running_campaign_suppresses_no_put_item(self):
        """When a RUNNING campaign targets vehicle:{vin}, _ensure_telemetry_campaign
        MUST issue zero put_item calls (Decision 9: never attach one on top).

        PRIMARY assertion: zero DynamoDB writes.
        A test checking only the return value would pass even when a redundant row
        was written — this test catches that.
        """
        campaign_id, created = sut._ensure_telemetry_campaign(_VIN)

        # PRIMARY: zero writes.
        self.mock_table.put_item.assert_not_called()

        # Secondary: correct return values.
        self.assertEqual(campaign_id, _CAMPAIGN_ID)
        self.assertFalse(created)

    def test_broadcast_all_campaign_also_suppresses_no_put_item(self):
        """A RUNNING 'all' broadcast campaign also satisfies coverage — zero writes."""
        def _all_side_effect(**kwargs):
            t = kwargs.get("ExpressionAttributeValues", {}).get(":t")
            if t == "all":
                return {"Items": [_RUNNING_ALL_ITEM]}
            return {"Items": []}

        self.mock_table.query.side_effect = _all_side_effect

        campaign_id, created = sut._ensure_telemetry_campaign(_VIN)

        self.mock_table.put_item.assert_not_called()
        self.assertEqual(campaign_id, _RUNNING_ALL_ITEM["campaignId"])
        self.assertFalse(created)


# ---------------------------------------------------------------------------
# Task 3.1 mutation (a) — empty queries → baseline IS created
# (verifies the suppression path is not trivially broken)
# ---------------------------------------------------------------------------

class TestCoverageQueryEmptyTriggersCreate(unittest.TestCase):
    """Mutation (a): when both queries return empty, a baseline MUST be created.

    If the implementation were changed so query always returns empty (or the early
    return were removed), test_running_campaign_suppresses_no_put_item would fail
    because put_item would be called.

    This class verifies the create path is correct when no coverage is found.
    """

    def setUp(self):
        self.mock_table = _make_table_mock()
        self.mock_table.query.return_value = {"Items": []}
        self.mock_table.scan.return_value = {"Items": [_ACTIVE_TEMPLATE_ITEM]}
        self.mock_table.put_item.return_value = {}

        self.table_patcher = patch.object(
            sut.ddb, "Table", return_value=self.mock_table
        )
        self.table_patcher.start()

    def tearDown(self):
        self.table_patcher.stop()

    def test_no_coverage_triggers_create(self):
        """Empty queries → baseline is created → put_item IS called once."""
        campaign_id, created = sut._ensure_telemetry_campaign(_VIN)

        self.mock_table.put_item.assert_called_once()
        self.assertTrue(created)
        self.assertIsNotNone(campaign_id)

    def test_created_item_sets_owner_platform(self):
        """The baseline row written by _ensure_telemetry_campaign carries owner='platform'.

        Task 2.2 added this — verify it is still present (never remove or alter).
        """
        sut._ensure_telemetry_campaign(_VIN)

        call_kwargs = self.mock_table.put_item.call_args
        item = (call_kwargs.kwargs.get("Item")
                or (call_kwargs.args[0].get("Item", {}) if call_kwargs.args else {}))
        self.assertEqual(item.get("owner"), "platform",
                         "owner='platform' must be set on the baseline row (Task 2.2)")


# ---------------------------------------------------------------------------
# Task 3.1 mutation (b) — pagination is followed
# ---------------------------------------------------------------------------

class TestCoverageQueryFollowsPagination(unittest.TestCase):
    """Mutation (b): pagination must be followed.

    Simulate a two-page result: page 1 returns no items (but has LastEvaluatedKey),
    page 2 contains the RUNNING item.  The implementation must follow LastEvaluatedKey
    and find the item on page 2.

    If pagination is dropped (single query call, no loop), this test FAILS because
    the RUNNING item on page 2 is never found → put_item IS called instead.
    """

    def setUp(self):
        self.mock_table = _make_table_mock()
        self.mock_table.scan.return_value = {"Items": [_ACTIVE_TEMPLATE_ITEM]}
        self.mock_table.put_item.return_value = {}

        self.table_patcher = patch.object(
            sut.ddb, "Table", return_value=self.mock_table
        )
        self.table_patcher.start()

    def tearDown(self):
        self.table_patcher.stop()

    def _two_page_effect(self, target_arn, running_item):
        """Side-effect: page 1 is empty+cursor, page 2 has the running item."""
        call_count = [0]

        def _effect(**kwargs):
            t = kwargs.get("ExpressionAttributeValues", {}).get(":t")
            if t != target_arn:
                return {"Items": []}
            call_count[0] += 1
            if call_count[0] == 1:
                return {
                    "Items": [],
                    "LastEvaluatedKey": {"targetArn": target_arn, "campaignId": "cursor"},
                }
            return {"Items": [running_item]}

        return _effect

    def test_running_item_on_page2_still_suppresses(self):
        """A RUNNING vehicle:{vin} item on page 2 must be found → zero writes."""
        self.mock_table.query.side_effect = self._two_page_effect(
            f"vehicle:{_VIN}", _RUNNING_VEHICLE_ITEM
        )

        campaign_id, created = sut._ensure_telemetry_campaign(_VIN)

        self.mock_table.put_item.assert_not_called()
        self.assertEqual(campaign_id, _CAMPAIGN_ID)
        self.assertFalse(created)

    def test_broadcast_all_running_item_on_page2_suppresses(self):
        """A RUNNING 'all' item on page 2 must be found → zero writes."""
        self.mock_table.query.side_effect = self._two_page_effect(
            "all", _RUNNING_ALL_ITEM
        )

        campaign_id, created = sut._ensure_telemetry_campaign(_VIN)

        self.mock_table.put_item.assert_not_called()
        self.assertFalse(created)


# ---------------------------------------------------------------------------
# Task 3.1 mutation (c) — widening predicate to ACTIVE must fail
# ---------------------------------------------------------------------------

class TestActiveTemplateIsNotCoverage(unittest.TestCase):
    """Mutation (c): the FilterExpression must NOT admit 'ACTIVE' status.

    The vocabulary trap from Task 1.3: templates carry status='ACTIVE', per-vehicle
    instances carry status='RUNNING'. If 'ACTIVE' were added to the predicate,
    every vehicle would appear covered by any template, suppressing the baseline
    forever and silently reproducing the zero-telemetry failure.

    This test guards the predicate directly: it inspects the ExpressionAttributeValues
    passed to every query() call and asserts 'ACTIVE' is not in the admitted status set.

    Mutation guard: widening to `status IN ('RUNNING', 'ACTIVE')` adds ':active': 'ACTIVE'
    to ExpressionAttributeValues → assertion FAILS immediately.
    """

    def setUp(self):
        self.mock_table = _make_table_mock()
        self.mock_table.query.return_value = {"Items": []}
        self.mock_table.scan.return_value = {"Items": [_ACTIVE_TEMPLATE_ITEM]}
        self.mock_table.put_item.return_value = {}

        self.table_patcher = patch.object(
            sut.ddb, "Table", return_value=self.mock_table
        )
        self.table_patcher.start()

    def tearDown(self):
        self.table_patcher.stop()

    def test_filter_expression_does_not_admit_active_status(self):
        """The ExpressionAttributeValues passed to query() must NOT include 'ACTIVE'
        as an admitted status value.

        Mutation guard (c): adding ':active': 'ACTIVE' to ExpressionAttributeValues
        (widening the predicate) causes this assertion to FAIL.
        """
        sut._ensure_telemetry_campaign(_VIN)

        for c in self.mock_table.query.call_args_list:
            kwargs = c.kwargs if c.kwargs else (c.args[0] if c.args else {})
            eav = kwargs.get("ExpressionAttributeValues", {})
            # Collect all status-like values (non-':t' placeholder values)
            admitted_statuses = {v for k, v in eav.items()
                                 if k.startswith(":") and k != ":t"}
            self.assertNotIn(
                "ACTIVE",
                admitted_statuses,
                f"FilterExpression must NOT admit 'ACTIVE' status — "
                f"ExpressionAttributeValues={eav}. ACTIVE marks templates; "
                "admitting them suppresses the baseline and silently reproduces "
                "zero-telemetry failure."
            )


# ---------------------------------------------------------------------------
# Task 3.1 mutation (d) — SUSPENDED campaign does NOT count as coverage
# ---------------------------------------------------------------------------

class TestSuspendedCampaignStillGetsBaseline(unittest.TestCase):
    """Mutation (d): SUSPENDED does NOT count as coverage.

    A SUSPENDED campaign is not collecting telemetry.  If the predicate were widened
    to include SUSPENDED, a SUSPENDED row would be returned by the query, suppress the
    baseline, and the vehicle would collect nothing during the trip.

    This test uses a filter-aware mock: the side_effect respects the ':running' value
    in ExpressionAttributeValues, returning the SUSPENDED item only when the filter
    value itself is 'SUSPENDED' (i.e. the predicate was widened).

    Mutation guard: widening to include SUSPENDED adds ':running': 'SUSPENDED' or a
    second placeholder ':suspended': 'SUSPENDED' → the mock returns the SUSPENDED item
    → coverage found → put_item NOT called → assertion FAILS.
    """

    def setUp(self):
        self.mock_table = _make_table_mock()
        self.mock_table.put_item.return_value = {}
        self.mock_table.scan.return_value = {"Items": [_ACTIVE_TEMPLATE_ITEM]}

        self.table_patcher = patch.object(
            sut.ddb, "Table", return_value=self.mock_table
        )
        self.table_patcher.start()

    def tearDown(self):
        self.table_patcher.stop()

    def test_suspended_campaign_does_not_suppress_baseline(self):
        """A SUSPENDED vehicle:{vin} campaign must NOT count as coverage.

        The RUNNING-only filter excludes it → coverage not found → baseline IS created.

        FG8.4 extended: the mock honours both the presence of FilterExpression AND
        the attribute-name mapping, so two additional mutations that pass the old
        EAV-only mock now fail:
          (a) deleting FilterExpression entirely → any item for this target_arn
              is returned, including SUSPENDED → suppression occurs → put_item NOT
              called → assertion FAILS.
          (b) mapping #s to 'category' instead of 'status' → status filter is on
              wrong field → SUSPENDED item is returned regardless → same failure.

        Mutation guard (d): the mock side_effect checks whether 'SUSPENDED' appears
        in the admitted status values.  If the predicate were widened to include
        SUSPENDED, the mock returns the SUSPENDED item → suppression occurs →
        put_item NOT called → assertion FAILS.
        """
        def _filter_aware_side_effect(**kwargs):
            """Return SUSPENDED item based on EAV, FilterExpression presence,
            and attribute-name mapping — not just EAV alone.

            Simulates three DynamoDB behaviours:
              1. FilterExpression absent → return all matching items (including SUSPENDED).
              2. FilterExpression present but attribute mapped to wrong field → status
                 filter is not applied → return all matching items.
              3. FilterExpression present and attribute correctly mapped to 'status' →
                 apply status filter on the item's actual status value.
            """
            t = kwargs.get("ExpressionAttributeValues", {}).get(":t")
            if t != f"vehicle:{_VIN}":
                return {"Items": []}

            # Check (a): no FilterExpression → return item unconditionally
            if not kwargs.get("FilterExpression"):
                return {"Items": [_SUSPENDED_ITEM]}

            # Check (b): attribute mapped to wrong field → status filter not applied
            ean = kwargs.get("ExpressionAttributeNames", {})
            # The filter token (#s or similar) should resolve to "status"
            filter_attr_values = set(ean.values()) if ean else set()
            if ean and "status" not in filter_attr_values:
                # Attribute mapped to something other than 'status' → filter misses status
                return {"Items": [_SUSPENDED_ITEM]}

            # Collect all non-':t' placeholder values = admitted statuses (original check)
            eav = kwargs.get("ExpressionAttributeValues", {})
            admitted = {v for k, v in eav.items() if k.startswith(":") and k != ":t"}
            if "SUSPENDED" in admitted:
                return {"Items": [_SUSPENDED_ITEM]}
            return {"Items": []}  # Correct RUNNING-only predicate excludes SUSPENDED

        self.mock_table.query.side_effect = _filter_aware_side_effect

        campaign_id, created = sut._ensure_telemetry_campaign(_VIN)

        self.mock_table.put_item.assert_called_once()
        self.assertTrue(
            created,
            "SUSPENDED campaign must NOT count as coverage: baseline should be created"
        )


# ===========================================================================
# 5.  The ACTIVE/RUNNING terminology trap — BEHAVIOURAL, not shape-based
# ===========================================================================


class TestActiveTemplateIsNotCoverageBehavioural(unittest.TestCase):
    """An ACTIVE *template* row must NOT count as coverage for a vehicle.

    Added by the architect after Group 3, closing an asymmetry: the SUSPENDED
    trap had a behavioural test (put_item IS called) while the ACTIVE trap had
    only ``test_filter_expression_does_not_admit_active_status``, which inspects
    the query's ExpressionAttributeValues rather than exercising the data path.

    ACTIVE is the higher-risk of the two. Per ``decisions.md`` § "Coverage
    requires an ACTIVE campaign, which in this schema means status == 'RUNNING'",
    templates carry ``status: "ACTIVE"`` (10 live rows) and per-vehicle instances carry
    ``status: "RUNNING"`` (15). The user's word for the rule was "active",
    so an implementer reading the decision literally writes ``ACTIVE`` — at
    which point every vehicle appears covered by any template, the baseline
    is never created, and every trip silently collects nothing. That is
    precisely the failure this function exists to prevent
    (``issues/2026-09-01-trip-simulation-zero-telemetry-no-campaign/``).

    After FG8.4: the mock now also honours FilterExpression presence and the
    ExpressionAttributeNames attribute mapping.  "A filter applied to the wrong
    field" (mutation b from FG8.4) IS now caught — the docstring's earlier claim
    that it "cannot catch a filter applied to the wrong field" was made before
    FG8.4 extended the mock.  Both mutations (a: absent FilterExpression,
    b: #s→category) now fail this test.  The shape test
    ``test_filter_expression_does_not_admit_active_status`` catches widenings
    at the value level; this behavioural test catches structural mutations.
    Together they cover the space.
    """

    def setUp(self):
        self.mock_table = _make_table_mock()
        self.mock_table.put_item.return_value = {}
        self.mock_table.scan.return_value = {"Items": [_ACTIVE_TEMPLATE_ITEM]}
        self.table_patcher = patch.object(
            sut.ddb, "Table", return_value=self.mock_table
        )
        self.table_patcher.start()

    def tearDown(self):
        self.table_patcher.stop()

    def test_active_template_does_not_suppress_baseline(self):
        """Only an ACTIVE row exists for this VIN → baseline MUST still be created.

        Mutation guard (c), behavioural form: widen the predicate to admit
        ACTIVE and the mock returns the template → coverage found → put_item is
        NOT called → this assertion FAILS.
        """
        active_vehicle_row = {
            "campaignId": f"cms-fleet-gps-10s-{_VIN}",
            "campaignName": "cms-fleet-gps-10s",
            "targetArn": f"vehicle:{_VIN}",
            "status": "ACTIVE",  # NOT RUNNING — must not count
        }

        def _filter_aware_side_effect(**kwargs):
            """Return ACTIVE item based on EAV, FilterExpression presence,
            and attribute-name mapping — not just EAV alone.

            Mirrors TestSuspendedCampaignStillGetsBaseline: honours the same
            three DynamoDB behaviours so mutations (a) and (b) are also caught
            for the ACTIVE case.

            FG8.4: the original EAV-only mock passed for mutations (a) and (b)
            because deleting FilterExpression or mapping #s→category doesn't
            change the EAV check — the ACTIVE item would still be returned
            (or not) based solely on whether 'ACTIVE' was in admitted values.
            """
            eav = kwargs.get("ExpressionAttributeValues", {})
            if eav.get(":t") != f"vehicle:{_VIN}":
                return {"Items": []}

            # Check (a): no FilterExpression → return item unconditionally
            if not kwargs.get("FilterExpression"):
                return {"Items": [active_vehicle_row]}

            # Check (b): attribute mapped to wrong field → status filter not applied
            ean = kwargs.get("ExpressionAttributeNames", {})
            filter_attr_values = set(ean.values()) if ean else set()
            if ean and "status" not in filter_attr_values:
                return {"Items": [active_vehicle_row]}

            admitted = {v for k, v in eav.items() if k.startswith(":") and k != ":t"}
            if "ACTIVE" in admitted:
                return {"Items": [active_vehicle_row]}
            return {"Items": []}

        self.mock_table.query.side_effect = _filter_aware_side_effect

        campaign_id, created = sut._ensure_telemetry_campaign(_VIN)

        self.assertTrue(
            self.mock_table.put_item.called,
            "an ACTIVE (non-RUNNING) row must NOT count as coverage — the baseline "
            "must still be created. If this fails, the predicate admits ACTIVE and "
            "every vehicle now appears covered by any template.",
        )
        self.assertTrue(created, "created flag must be True when no RUNNING coverage exists")
        self.assertIsNotNone(campaign_id)



if __name__ == "__main__":
    # FG8.6: relocated from mid-file (was at line 428, before the last class)
    # so that `python3 test_ensure_campaign_suppression.py` runs all 9 tests.
    unittest.main(verbosity=2)
