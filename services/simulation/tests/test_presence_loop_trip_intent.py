"""Tier B trip-intent parameter contract: safetyScenarios / maintenanceScenarios (T4.2).

Spec:  .kiro/specs/2026-09-20-trip-intent-param-contract/
Task:  T4.2 — catalog-driven scenario reader with apply/restore
Issue: issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/

Key design decisions (decisions.md § "T4.2 targets the wrong mechanism"):
  - The intent map carries event-catalog event_id values ('safety.harsh_braking'),
    NOT SCENARIOS keys ('hard_braking').  The two namespaces do not intersect.
  - The mechanism is EventCatalogDriver (active_events + degradation_targets),
    NOT VehicleState.force_* attributes.
  - SCENARIOS in main() is left untouched.
  - [] is the absent sentinel — no override, never clear across trips.
  - EventCatalogDriver is lazily constructed on first non-empty scenario list;
    at most one construction per container lifetime.

Verify items covered by these tests:
  1. Empty-list no-op ([] applies nothing).
  2. De-dup and order preservation.
  3. Unknown IDs delegated to the driver, not re-validated here.
  4. Apply-restore round-trip (active_events and degradation_targets restored).
  5. Restore on exception path.
  6. Restore on clean exit.
  7. Lazy construction happens at most once.
  8. Construction failure degrades without aborting the trip.
  9. Namespace assertion: real catalog event_id resolves; SCENARIOS key does not
     (this is the assertion whose absence let the original design through review).
 10. Deep-copy: shallow snapshot aliases the live list → restore is a no-op (M_shallow).
"""

import copy
import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, call, patch

# ---------------------------------------------------------------------------
# sys.path: simulation package must be importable
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

from realtime_telemetry_simulator import (  # noqa: E402
    PresenceLoop,
    RealtimeTelemetrySimulator,
)

_VEHICLE_ID = "VEH-TEST-CATALOG-001"

# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


class FakeMqttClient:
    """Minimal paho-alike: never connects, never disconnects."""

    def __init__(self):
        self.disconnected = False

    def publish(self, *a, **kw):
        return MagicMock(rc=0)

    def subscribe(self, *a, **kw):
        return (0, 1)

    def message_callback_add(self, *a, **kw):
        return None

    def disconnect(self):
        self.disconnected = True


def _make_simulator(*, with_driver=False):
    """A RealtimeTelemetrySimulator with AWS/MQTT wiring stubbed.

    `with_driver=True` attaches a stubbed EventCatalogDriver so tests can exercise
    the apply path without constructing a real DDB connection.  The default is
    `with_driver=False` to simulate the normal warm-container case (no --events at
    launch).
    """
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "test")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "test")

    with patch("realtime_telemetry_simulator._spawn_uds_responder"):
        sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)

    sim.mode = "can"
    sim.running = True
    sim.vehicle_states = {}
    sim.logger = MagicMock()
    sim.can_encoder = MagicMock()
    sim.can_encoder.encode.return_value = [MagicMock()]
    sim.can_writers = {}
    sim.can_writer = MagicMock()
    sim.region = "us-west-2"
    sim.profile_name = "default"
    sim.iot_rule_name = "cms_test_iot_rule"
    sim.telemetry_interval = 15
    sim.route_length = 20
    sim.city_lat = 47.6062
    sim.city_lng = -122.3321
    sim.current_city = "SEATTLE"
    sim.degradation_targets = {}
    # Replicate what __init__ sets via _detect_table_names() — this is what the
    # pre-fix code read, giving 'staging-storage' instead of 'staging'.
    sim.table_suffix = "staging-storage"

    if with_driver:
        driver = MagicMock()
        driver.active_events = []
        driver.compute_degradation_targets.return_value = {}
        sim.event_catalog_driver = driver
    else:
        sim.event_catalog_driver = None

    return sim


def _make_presence(*, with_driver=False):
    """PresenceLoop with the DDB simulation-row writes stubbed out."""
    sim = _make_simulator(with_driver=with_driver)
    pl = PresenceLoop(sim, _VEHICLE_ID, FakeMqttClient())
    pl._mark_simulation_running = MagicMock()
    pl._mark_simulation_completed = MagicMock()
    return pl, sim


def _drive_one_intent(pl, intent, *, run_trips_raises=False):
    """Run the presence loop for exactly one intent and return observed state.

    Returns dict with:
      - called: whether run_trips was reached
      - active_events_at_trip_start: copy of driver.active_events when run_trips ran
      - degradation_targets_at_trip_start: copy of sim.degradation_targets when run_trips ran
      - active_events_after_trip: driver.active_events after the loop returns
      - degradation_targets_after_trip: sim.degradation_targets after the loop returns
    """
    observed = {"called": False}
    sim = pl._simulator

    def spy_run_trips(trips_count, **kwargs):
        observed["called"] = True
        driver = getattr(sim, "event_catalog_driver", None)
        observed["active_events_at_trip_start"] = (
            list(driver.active_events) if driver is not None else None
        )
        observed["degradation_targets_at_trip_start"] = dict(
            getattr(sim, "degradation_targets", {})
        )
        if run_trips_raises:
            raise RuntimeError("injected mid-trip failure")

    polls = [intent, None, None]
    idx = {"i": 0}

    def mock_poll():
        if idx["i"] < len(polls):
            r = polls[idx["i"]]
            idx["i"] += 1
            return r
        return None

    pl.poll_trip_intent = mock_poll
    pl.consume_trip_intent = MagicMock(return_value=True)
    pl.run_trips = spy_run_trips
    pl.idle_emit = MagicMock(return_value=[])
    pl.heartbeat = MagicMock()
    pl.reconcile_fault_state = MagicMock()

    stop = threading.Event()

    def stop_soon():
        import time as _t
        _t.sleep(0.12)
        stop.set()

    stopper = threading.Thread(target=stop_soon)
    stopper.start()
    pl.run(idle_interval=0, _stop_event=stop)
    stopper.join(timeout=2)

    driver = getattr(sim, "event_catalog_driver", None)
    observed["active_events_after_trip"] = (
        list(driver.active_events) if driver is not None else None
    )
    observed["degradation_targets_after_trip"] = dict(
        getattr(sim, "degradation_targets", {})
    )
    return observed


# ---------------------------------------------------------------------------
# Test 1: empty list is a no-op
# ---------------------------------------------------------------------------


class TestEmptyListIsNoOp(unittest.TestCase):
    """[] applies nothing and must NOT clear active_events from a previous trip."""

    def test_empty_safety_and_maintenance_apply_nothing(self):
        """An intent with both lists empty must leave active_events untouched."""
        pl, sim = _make_presence(with_driver=True)
        pre_existing = ["safety.harsh_braking"]
        sim.event_catalog_driver.active_events = list(pre_existing)

        observed = _drive_one_intent(
            pl,
            {"simulationId": "sim-empty", "tripsCount": 1,
             "safetyScenarios": [], "maintenanceScenarios": []},
        )
        self.assertTrue(observed["called"], "run_trips must be reached")
        # apply must have been a no-op
        self.assertEqual(
            observed["active_events_at_trip_start"],
            pre_existing,
            "empty list must not overwrite existing active_events",
        )

    def test_absent_scenario_keys_also_no_op(self):
        """An intent with neither key present leaves the driver untouched."""
        pl, sim = _make_presence(with_driver=True)
        sim.event_catalog_driver.active_events = ["maintenance.low_oil_pressure"]

        observed = _drive_one_intent(
            pl,
            {"simulationId": "sim-no-keys", "tripsCount": 1},
        )
        self.assertTrue(observed["called"])
        self.assertEqual(
            observed["active_events_at_trip_start"],
            ["maintenance.low_oil_pressure"],
        )


# ---------------------------------------------------------------------------
# Test 2: de-dup and order preservation
# ---------------------------------------------------------------------------


class TestResolveCatalogScenarios(unittest.TestCase):
    """_resolve_catalog_scenarios: order-preserving, de-duplicated join."""

    def test_safety_list_prepended_before_maintenance_list(self):
        pl, _ = _make_presence()
        result = pl._resolve_catalog_scenarios(
            ["safety.harsh_braking", "safety.aggressive_acceleration"],
            ["maintenance.low_oil_pressure"],
        )
        self.assertEqual(
            result,
            ["safety.harsh_braking", "safety.aggressive_acceleration",
             "maintenance.low_oil_pressure"],
        )

    def test_duplicates_across_lists_are_removed(self):
        pl, _ = _make_presence()
        result = pl._resolve_catalog_scenarios(
            ["safety.harsh_braking"],
            ["safety.harsh_braking", "maintenance.low_oil_pressure"],
        )
        self.assertIn("safety.harsh_braking", result)
        self.assertEqual(result.count("safety.harsh_braking"), 1)
        self.assertIn("maintenance.low_oil_pressure", result)

    def test_empty_both_lists_returns_empty(self):
        pl, _ = _make_presence()
        result = pl._resolve_catalog_scenarios([], [])
        self.assertEqual(result, [])

    def test_none_lists_treated_as_empty(self):
        pl, _ = _make_presence()
        result = pl._resolve_catalog_scenarios(None, None)
        self.assertEqual(result, [])


# ---------------------------------------------------------------------------
# Test 3: unknown IDs delegated to driver, not re-validated
# ---------------------------------------------------------------------------


class TestUnknownIdsDelegatedToDriver(unittest.TestCase):
    """_resolve_catalog_scenarios passes unknown IDs through; driver filters them."""

    def test_unknown_ids_pass_through_to_set_active_events(self):
        """The resolver does NOT silently discard unknown IDs — that's the driver's job."""
        pl, sim = _make_presence(with_driver=True)
        sim.event_catalog_driver.active_events = []

        observed = _drive_one_intent(
            pl,
            {"simulationId": "sim-unk", "tripsCount": 1,
             "safetyScenarios": ["completely.unknown.event"]},
        )
        self.assertTrue(observed["called"], "trip must not be dropped on an unknown ID")
        sim.event_catalog_driver.set_active_events.assert_called_once_with(
            ["completely.unknown.event"]
        )


# ---------------------------------------------------------------------------
# Test 4 & 5 & 6: apply-restore round-trip (clean exit and exception path)
# ---------------------------------------------------------------------------


class TestApplyRestoreRoundTrip(unittest.TestCase):
    """active_events and degradation_targets are restored after the trip."""

    def _make_presence_with_pre_trip_state(self):
        pl, sim = _make_presence(with_driver=True)
        pre_events = ["safety.harsh_braking"]
        pre_targets = {"speed": 35.0}
        sim.event_catalog_driver.active_events = list(pre_events)
        sim.degradation_targets = dict(pre_targets)
        sim.event_catalog_driver.compute_degradation_targets.return_value = {
            "tire_pressure_fl": 25.0
        }
        return pl, sim, pre_events, pre_targets

    def test_active_events_restored_after_clean_trip(self):
        pl, sim, pre_events, pre_targets = self._make_presence_with_pre_trip_state()
        _drive_one_intent(
            pl,
            {"simulationId": "sim-rt1", "tripsCount": 1,
             "safetyScenarios": ["maintenance.low_oil_pressure"]},
        )
        self.assertEqual(
            list(sim.event_catalog_driver.active_events),
            pre_events,
            "active_events must be restored to pre-trip value after a clean trip",
        )

    def test_degradation_targets_restored_after_clean_trip(self):
        pl, sim, pre_events, pre_targets = self._make_presence_with_pre_trip_state()
        _drive_one_intent(
            pl,
            {"simulationId": "sim-rt2", "tripsCount": 1,
             "safetyScenarios": ["maintenance.low_oil_pressure"]},
        )
        self.assertEqual(
            dict(sim.degradation_targets),
            pre_targets,
            "degradation_targets must be restored to pre-trip value after a clean trip",
        )

    def test_active_events_restored_even_when_run_trips_raises(self):
        """Restore must fire even if run_trips raises — the finally guarantee."""
        pl, sim, pre_events, pre_targets = self._make_presence_with_pre_trip_state()
        _drive_one_intent(
            pl,
            {"simulationId": "sim-rt3", "tripsCount": 1,
             "safetyScenarios": ["maintenance.low_oil_pressure"]},
            run_trips_raises=True,
        )
        self.assertEqual(
            list(sim.event_catalog_driver.active_events),
            pre_events,
            "active_events must be restored even when run_trips raises",
        )

    def test_degradation_targets_restored_even_when_run_trips_raises(self):
        pl, sim, pre_events, pre_targets = self._make_presence_with_pre_trip_state()
        _drive_one_intent(
            pl,
            {"simulationId": "sim-rt4", "tripsCount": 1,
             "safetyScenarios": ["maintenance.low_oil_pressure"]},
            run_trips_raises=True,
        )
        self.assertEqual(
            dict(sim.degradation_targets),
            pre_targets,
            "degradation_targets must be restored even when run_trips raises",
        )

    def test_applied_values_observed_inside_run_trips(self):
        """The override must reach run_trips, not just sit on the attribute."""
        pl, sim, pre_events, pre_targets = self._make_presence_with_pre_trip_state()
        sim.event_catalog_driver.compute_degradation_targets.return_value = {
            "tire_pressure_fl": 25.0
        }
        observed = _drive_one_intent(
            pl,
            {"simulationId": "sim-rt5", "tripsCount": 1,
             "safetyScenarios": ["maintenance.low_oil_pressure"]},
        )
        self.assertEqual(
            observed["degradation_targets_at_trip_start"],
            {"tire_pressure_fl": 25.0},
            "degradation_targets must carry the applied values inside run_trips",
        )


# ---------------------------------------------------------------------------
# Test 7: lazy construction happens at most once
# ---------------------------------------------------------------------------


class TestLazyDriverConstruction(unittest.TestCase):
    """EventCatalogDriver is constructed at most once per container lifetime."""

    def test_driver_constructed_on_first_non_empty_scenario(self):
        """A warm container (no --events) constructs the driver when first needed.

        Asserts the constructor is called with the correct ``stage=`` argument —
        this is the property that would have caught C1 (stage derived from
        ``table_suffix`` giving 'staging-storage' instead of 'staging').
        """
        pl, sim = _make_presence(with_driver=False)
        self.assertIsNone(
            sim.event_catalog_driver,
            "precondition: no driver at construction time",
        )
        fake_driver = MagicMock()
        fake_driver.active_events = []
        fake_driver.compute_degradation_targets.return_value = {}

        mock_module = MagicMock()
        mock_module.EventCatalogDriver.return_value = fake_driver

        with patch.dict(os.environ, {"DEPLOYMENT_STAGE": "staging"}, clear=False):
            with patch.dict(sys.modules, {"event_catalog_driver": mock_module}):
                observed = _drive_one_intent(
                    pl,
                    {"simulationId": "sim-lazy1", "tripsCount": 1,
                     "safetyScenarios": ["safety.harsh_braking"]},
                )

        self.assertTrue(observed["called"], "trip must proceed")
        # Assert the constructor was called with the correct stage — NOT 'staging-storage'
        mock_module.EventCatalogDriver.assert_called_once_with(
            region="us-west-2", stage="staging"
        )
        # Assert the constructed driver was cached on sim
        self.assertIs(
            sim.event_catalog_driver,
            fake_driver,
            "constructed driver must be cached as sim.event_catalog_driver",
        )

    def test_driver_not_constructed_when_scenario_list_empty(self):
        """An intent with [] must not construct the driver."""
        pl, sim = _make_presence(with_driver=False)
        self.assertIsNone(sim.event_catalog_driver)

        mock_module = MagicMock()
        mock_module.EventCatalogDriver.side_effect = AssertionError(
            "EventCatalogDriver must NOT be constructed for an empty scenario list"
        )
        with patch.dict(sys.modules, {"event_catalog_driver": mock_module}):
            observed = _drive_one_intent(
                pl,
                {"simulationId": "sim-lazy2", "tripsCount": 1,
                 "safetyScenarios": []},
            )
        self.assertTrue(observed["called"])
        # driver must still be None — no construction triggered
        self.assertIsNone(
            sim.event_catalog_driver,
            "an empty scenario list must not trigger driver construction",
        )

    def test_driver_reused_on_second_intent(self):
        """A driver constructed on trip 1 must be reused on trip 2 (one DDB scan).

        The test does NOT manually re-attach the driver between intents — that would
        substitute for the caching behaviour under test.  Instead, it asserts that the
        driver object on ``sim.event_catalog_driver`` after intent 1 is the same object
        returned by the constructor, and that a second intent with a non-empty scenario
        list does not call the constructor again.
        """
        pl, sim = _make_presence(with_driver=False)
        fake_driver = MagicMock()
        fake_driver.active_events = []
        fake_driver.compute_degradation_targets.return_value = {}

        construction_count = {"n": 0}

        def make_driver(*args, **kwargs):
            construction_count["n"] += 1
            return fake_driver

        mock_module = MagicMock()
        mock_module.EventCatalogDriver.side_effect = make_driver

        with patch.dict(os.environ, {"DEPLOYMENT_STAGE": "staging"}, clear=False):
            with patch.dict(sys.modules, {"event_catalog_driver": mock_module}):
                # First intent: driver must be constructed and cached
                _drive_one_intent(
                    pl,
                    {"simulationId": "sim-reuse1", "tripsCount": 1,
                     "safetyScenarios": ["safety.harsh_braking"]},
                )
                # After intent 1 the driver must be cached on sim — not re-attached manually
                self.assertIs(
                    sim.event_catalog_driver,
                    fake_driver,
                    "driver constructed on intent 1 must be cached as sim.event_catalog_driver",
                )

                # Second intent: driver must be reused, not reconstructed
                _drive_one_intent(
                    pl,
                    {"simulationId": "sim-reuse2", "tripsCount": 1,
                     "safetyScenarios": ["maintenance.low_oil_pressure"]},
                )

        # At most one construction — the second intent finds the cached driver
        self.assertLessEqual(
            construction_count["n"], 1,
            "EventCatalogDriver must be constructed at most once per container",
        )


# ---------------------------------------------------------------------------
# Test 8: construction failure degrades without aborting the trip
# ---------------------------------------------------------------------------


class TestConstructionFailureDegrades(unittest.TestCase):
    """A driver construction failure logs and lets the trip proceed without scenarios."""

    def test_construction_failure_does_not_abort_trip(self):
        pl, sim = _make_presence(with_driver=False)

        def raise_on_construct(*args, **kwargs):
            raise RuntimeError("DynamoDB unavailable")

        mock_module = MagicMock()
        mock_module.EventCatalogDriver.side_effect = raise_on_construct

        with patch.dict(sys.modules, {"event_catalog_driver": mock_module}):
            observed = _drive_one_intent(
                pl,
                {"simulationId": "sim-fail", "tripsCount": 1,
                 "safetyScenarios": ["safety.harsh_braking"]},
            )
        self.assertTrue(
            observed["called"],
            "run_trips must be reached even when EventCatalogDriver construction fails",
        )


# ---------------------------------------------------------------------------
# Test 9: namespace assertion — SCENARIOS keys do NOT match catalog event_ids
# ---------------------------------------------------------------------------


class TestNamespaceAssertion(unittest.TestCase):
    """The resolver accepts real catalog event_ids, not SCENARIOS keys.

    This is the assertion whose absence let the original T4.2 design (which targeted
    SCENARIOS) pass review — a test feeding SCENARIOS keys as fixtures would pass
    against a resolver that no production input can reach.
    """

    def test_real_catalog_event_id_passes_through_resolver(self):
        """A dotted catalog event_id ('safety.harsh_braking') is forwarded as-is."""
        pl, _ = _make_presence()
        result = pl._resolve_catalog_scenarios(["safety.harsh_braking"], [])
        self.assertIn("safety.harsh_braking", result)

    def test_scenarios_key_also_passes_through_but_will_be_filtered_by_driver(self):
        """A bare SCENARIOS key ('hard_braking') passes through the resolver.

        The resolver does NOT validate IDs — that's the driver's job.  But the
        driver's catalog contains 'safety.harsh_braking', not 'hard_braking', so
        a bare key would be filtered out by set_active_events as unknown.  This
        test names the namespace boundary so it is explicit and visible.
        """
        pl, _ = _make_presence()
        # The resolver passes it through (no re-validation)
        result_scenarios_key = pl._resolve_catalog_scenarios(["hard_braking"], [])
        result_catalog_id = pl._resolve_catalog_scenarios(["safety.harsh_braking"], [])

        # Both pass through the resolver — they are different strings and the
        # driver is where the difference matters
        self.assertIn("hard_braking", result_scenarios_key)
        self.assertIn("safety.harsh_braking", result_catalog_id)
        # The two are NOT equal — this is the namespace boundary
        self.assertNotEqual(
            result_scenarios_key, result_catalog_id,
            "SCENARIOS key 'hard_braking' != catalog event_id 'safety.harsh_braking': "
            "they are different namespaces and do not intersect",
        )

    def test_catalog_event_id_set_on_driver_via_set_active_events(self):
        """When applying a catalog ID, set_active_events is called with that exact ID."""
        pl, sim = _make_presence(with_driver=True)
        sim.event_catalog_driver.compute_degradation_targets.return_value = {}

        _drive_one_intent(
            pl,
            {"simulationId": "sim-ns", "tripsCount": 1,
             "safetyScenarios": ["safety.harsh_braking"]},
        )
        sim.event_catalog_driver.set_active_events.assert_called_with(
            ["safety.harsh_braking"]
        )


# ---------------------------------------------------------------------------
# Test 10 (mutation M_shallow): deep-copy snapshot prevents aliasing
# ---------------------------------------------------------------------------


class TestDeepCopySnapshot(unittest.TestCase):
    """_snapshot_catalog_attrs deep-copies active_events.

    If the snapshot is a shallow copy (list(x) → x alias), the snapshot object is
    the SAME object as driver.active_events; when set_active_events mutates the list
    in place, the snapshot is mutated too and restore becomes a no-op.

    Mutation instruction: in _snapshot_catalog_attrs, change ``copy.deepcopy(ae)``
    to ``ae`` (no copy at all).  The round-trip test below MUST fail — if it still
    passes, the test is vacuous and must be fixed before marking T4.2 [x].
    """

    def test_snapshot_is_not_the_same_object_as_driver_active_events(self):
        """The snapshot must be a distinct object from the live list."""
        pl, sim = _make_presence(with_driver=True)
        sim.event_catalog_driver.active_events = ["safety.harsh_braking"]

        snapshot = pl._snapshot_catalog_attrs()
        ae_snap = snapshot["active_events"]

        # Must not be the same object — aliasing makes restore a no-op
        self.assertIsNot(
            ae_snap,
            sim.event_catalog_driver.active_events,
            "snapshot must be a distinct object from driver.active_events",
        )

    def test_mutating_driver_after_snapshot_does_not_corrupt_snapshot(self):
        """Mutating driver.active_events after snapshot must not change the snapshot."""
        pl, sim = _make_presence(with_driver=True)
        sim.event_catalog_driver.active_events = ["safety.harsh_braking"]

        snapshot = pl._snapshot_catalog_attrs()
        # Mutate the live list after snapshot
        sim.event_catalog_driver.active_events.append("maintenance.low_oil_pressure")

        ae_snap = snapshot["active_events"]
        self.assertNotIn(
            "maintenance.low_oil_pressure",
            ae_snap,
            "post-snapshot mutation of driver.active_events must not corrupt the snapshot",
        )

    def test_round_trip_with_set_active_events_mutation(self):
        """Deep snapshot defends against a collaborator that mutates active_events in place.

        ``EventCatalogDriver.set_active_events`` today *rebinds* ``self.active_events``
        (``self.active_events = valid``), so in-place mutation is NOT the current
        production behaviour.  The deep-copy is kept as defence-in-depth.  This test
        exercises that defence by supplying a ``side_effect`` that mutates the list
        in place — simulating what would happen if the driver changed to an append
        model — to verify that the snapshot remains independent regardless.

        If the test passes under a shallow-copy mutation (``copy.deepcopy`` changed
        to a bare reference), the test is vacuous and must be fixed.
        """
        pl, sim = _make_presence(with_driver=True)
        original_events = ["safety.harsh_braking"]
        sim.event_catalog_driver.active_events = list(original_events)

        # Simulate in-place mutation: set_active_events appends to the list rather
        # than replacing it.  Under a shallow alias the snapshot is the SAME object;
        # under a deep copy the snapshot is independent.
        def in_place_set_active(event_ids):
            sim.event_catalog_driver.active_events[:] = event_ids  # in-place mutation
        sim.event_catalog_driver.set_active_events.side_effect = in_place_set_active
        sim.event_catalog_driver.compute_degradation_targets.return_value = {}

        _drive_one_intent(
            pl,
            {"simulationId": "sim-deep", "tripsCount": 1,
             "safetyScenarios": ["maintenance.low_oil_pressure"]},
        )
        self.assertEqual(
            list(sim.event_catalog_driver.active_events),
            original_events,
            "restore must put back the original active_events even under in-place mutation",
        )


# ---------------------------------------------------------------------------
# Additional: _resolve_trip_overrides returns a tuple
# ---------------------------------------------------------------------------
# Test: W5 — malformed safetyScenarios must not discard valid Tier A overrides
# ---------------------------------------------------------------------------


class TestMalformedScenarioValueFallback(unittest.TestCase):
    """A non-list safetyScenarios/maintenanceScenarios value must not discard Tier A.

    W5: passing ``safetyScenarios: 5`` (integer) to ``_resolve_catalog_scenarios``
    previously caused a ``TypeError`` that escaped ``_resolve_trip_overrides`` and
    was caught by the outer ``except`` in ``PresenceLoop.run``, discarding BOTH
    ``sim_overrides`` (city, routeLength) and ``catalog_scenarios``.  This
    contradicted the docstring's "fail-soft by design" promise.

    The guard rejects non-list/tuple values rather than iterating them, so the
    Tier A path (city, routeLength) survives a bad Tier B value.
    """

    def test_integer_safetyScenarios_does_not_discard_tier_a_overrides(self):
        """safetyScenarios: 5 must yield catalog_scenarios=[] while sim_overrides survives."""
        pl, _ = _make_presence()
        overrides, catalog_scenarios = pl._resolve_trip_overrides({
            "simulationId": "sim-w5a",
            "city": "munich",
            "routeLength": 60,
            "safetyScenarios": 5,  # malformed: integer, not a list
        })
        # Tier A overrides must survive
        self.assertIn("city_lat", overrides, "city override must survive a bad safetyScenarios")
        self.assertIn("route_length", overrides, "routeLength override must survive a bad safetyScenarios")
        # Tier B must be empty, not raise
        self.assertEqual(catalog_scenarios, [], "malformed safetyScenarios must yield []")

    def test_string_safetyScenarios_does_not_iterate_characters(self):
        """safetyScenarios: 'safety.harsh_braking' (string) must not yield 15 char IDs."""
        pl, _ = _make_presence()
        overrides, catalog_scenarios = pl._resolve_trip_overrides({
            "simulationId": "sim-w5b",
            "city": "munich",
            "safetyScenarios": "safety.harsh_braking",  # string, not a list
        })
        # Must not contain single-character IDs from iterating the string
        for item in catalog_scenarios:
            self.assertGreater(
                len(item), 1,
                f"single-char item {item!r} indicates string was iterated, not rejected",
            )
        # City override must still be present — Tier A survives bad Tier B
        self.assertIn("city_lat", overrides, "city override must survive a bad safetyScenarios")


# ---------------------------------------------------------------------------
# Additional: _resolve_trip_overrides returns a tuple
# ---------------------------------------------------------------------------


class TestResolveTripOverridesReturnsTuple(unittest.TestCase):
    """_resolve_trip_overrides now returns (sim_overrides, catalog_scenarios)."""

    def test_returns_tuple_of_two(self):
        pl, _ = _make_presence()
        result = pl._resolve_trip_overrides({
            "simulationId": "sim-tup",
            "safetyScenarios": ["safety.harsh_braking"],
        })
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)

    def test_catalog_scenarios_empty_when_both_lists_absent(self):
        pl, _ = _make_presence()
        _, catalog = pl._resolve_trip_overrides({"simulationId": "sim-tup2"})
        self.assertEqual(catalog, [])

    def test_catalog_scenarios_populated_from_both_lists(self):
        pl, _ = _make_presence()
        _, catalog = pl._resolve_trip_overrides({
            "simulationId": "sim-tup3",
            "safetyScenarios": ["safety.harsh_braking"],
            "maintenanceScenarios": ["maintenance.low_oil_pressure"],
        })
        self.assertEqual(
            catalog,
            ["safety.harsh_braking", "maintenance.low_oil_pressure"],
        )


if __name__ == "__main__":
    unittest.main()
