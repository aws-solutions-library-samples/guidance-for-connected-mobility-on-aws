"""Tier A trip-intent parameter overrides: city / routeLength (spec Group 2, T2.2).

Spec:  .kiro/specs/2026-09-20-trip-intent-param-contract/
Issue: issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/

**These tests are the behavioural control for the contract guard's § "Known
limitations 2".** `scripts/check_trip_intent_contract.py` proves only that a key name
appears in a `.get()` call on the intent map — a reader that binds a key and discards
the value satisfies it completely, and once T2.1 shrinks `_DEFERRED_READERS` the
ratchet is one-way and no structural signal can ever report these keys ineffective
again. So every assertion here is on an applied **value**, never on "a read happened".

Scope honesty: the DDB/MQTT boundary is stubbed, and a stub cannot fail the way a warm
ECS container fails — this defect was itself found by a live gate after a fully green
suite. T3.2 (second trip on a warm container) is the live control; these tests do not
claim to cover it.
"""

import os
import sys
import threading
import unittest
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# sys.path: simulation package must be importable
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

from realtime_telemetry_simulator import (  # noqa: E402 — must stay after sys.path
    CITY_COORDINATES,
    PresenceLoop,
    RealtimeTelemetrySimulator,
)

_VEHICLE_ID = "VEH-TEST-OVERRIDE-001"

# The container's argv-derived configuration, i.e. what a task launched with
# `--city seattle --route-length 20` carries. Every "falls back to the container
# default" assertion below is made against these exact values, by value, so a
# regression to the clamp-yields-5 behaviour fails loudly rather than reading as
# "unchanged".
#
# "The container default" is per-container, not global — confirmed live 2026-09-20
# (spec Group 3, T3.2). Two containers run a PresenceLoop for the same vehicle and
# they are launched differently:
#   * the per-trip `fwe-simulator` task gets `--city`/`--route-length` from the
#     caller via simulation_lambda.py::_start, so its defaults are whatever the
#     first start requested; while
#   * the resident `vehicle-ecu` sidecar is launched
#     `--mode can --skip-mqtt --commands-mqtt --trips 0 --vehicles 1` with **no**
#     `--city` and **no** `--route-length`, so its defaults are the argparse ones:
#     `nyc` and 20.
# So "restores the container default" means different observable values depending on
# which presence loop serviced the trip. These tests fix one container's values and
# assert against them; they do not and cannot pin which container wins a live
# consume race (see issues/2026-09-20-dual-presence-loops-poll-same-tripintent/).
_CONTAINER_ROUTE_LENGTH = 20
_CONTAINER_CITY = "seattle"
_CONTAINER_LAT, _CONTAINER_LNG = CITY_COORDINATES[_CONTAINER_CITY]
_CONTAINER_CITY_NAME = _CONTAINER_CITY.upper()


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


def _make_simulator():
    """A RealtimeTelemetrySimulator with AWS/MQTT wiring stubbed.

    Built with `__new__` so `__init__`'s boto3/CAN setup never runs; the attributes
    the override path touches are then set explicitly to the container-argv values.
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

    # Container-argv configuration (main() sets exactly these four).
    sim.route_length = _CONTAINER_ROUTE_LENGTH
    sim.city_lat = _CONTAINER_LAT
    sim.city_lng = _CONTAINER_LNG
    sim.current_city = _CONTAINER_CITY_NAME
    return sim


def _make_presence(sim=None):
    """PresenceLoop with the DDB simulation-row writes stubbed out."""
    sim = sim if sim is not None else _make_simulator()
    pl = PresenceLoop(sim, _VEHICLE_ID, FakeMqttClient())
    pl._mark_simulation_running = MagicMock()
    pl._mark_simulation_completed = MagicMock()
    return pl, sim


def _drive_one_intent(pl, intent, *, run_trips_raises=False):
    """Run the presence loop for exactly one intent and return what the trip saw.

    Returns a dict with the simulator attribute values *observed inside run_trips*
    (i.e. what the trip would actually drive with), plus whether run_trips was
    reached at all.
    """
    observed = {"called": False}

    def spy_run_trips(trips_count, **kwargs):
        observed["called"] = True
        observed["trips_count"] = trips_count
        observed["route_length"] = pl._simulator.route_length
        observed["city_lat"] = getattr(pl._simulator, "city_lat", None)
        observed["city_lng"] = getattr(pl._simulator, "city_lng", None)
        observed["current_city"] = getattr(pl._simulator, "current_city", None)
        # The route cache as the first trip of this intent would find it. Empty is
        # what allows generate_telemetry_data to rebuild it from the values above.
        observed["route_at_trip_start"] = list(pl.vehicle_state.route)
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
        _t.sleep(0.1)
        stop.set()

    stopper = threading.Thread(target=stop_soon)
    stopper.start()
    pl.run(idle_interval=0, _stop_event=stop)
    stopper.join(timeout=2)
    return observed


class TestOverridesApplied(unittest.TestCase):
    """(a) An intent carrying both parameters applies both, by value."""

    def test_city_and_route_length_both_reach_the_trip(self):
        pl, sim = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-a", "tripsCount": 1,
                 "city": "munich", "routeLength": 45},
        )

        self.assertTrue(observed["called"], "run_trips must still be reached")
        munich_lat, munich_lng = CITY_COORDINATES["munich"]
        self.assertEqual(observed["route_length"], 45)
        self.assertEqual(observed["city_lat"], munich_lat)
        self.assertEqual(observed["city_lng"], munich_lng)
        self.assertEqual(observed["current_city"], "MUNICH")

    def test_city_is_case_insensitive_and_whitespace_tolerant(self):
        pl, sim = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-a2", "tripsCount": 1, "city": "  Atlanta "},
        )
        atl_lat, atl_lng = CITY_COORDINATES["atlanta"]
        self.assertEqual(observed["city_lat"], atl_lat)
        self.assertEqual(observed["city_lng"], atl_lng)
        self.assertEqual(observed["current_city"], "ATLANTA")


class TestRestoreAfterTrip(unittest.TestCase):
    """(b)/(c) The override is reverted after the trip — including on exception."""

    def test_state_restored_exactly_after_a_clean_trip(self):
        pl, sim = _make_presence()
        _drive_one_intent(
            pl, {"simulationId": "sim-b", "tripsCount": 1,
                 "city": "munich", "routeLength": 45},
        )
        self.assertEqual(sim.route_length, _CONTAINER_ROUTE_LENGTH)
        self.assertEqual(sim.city_lat, _CONTAINER_LAT)
        self.assertEqual(sim.city_lng, _CONTAINER_LNG)
        self.assertEqual(sim.current_city, _CONTAINER_CITY_NAME)

    def test_state_restored_even_when_run_trips_raises(self):
        """M5's target: without the `finally`, an override leaks into the next trip."""
        pl, sim = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-c", "tripsCount": 1,
                 "city": "munich", "routeLength": 60},
            run_trips_raises=True,
        )
        self.assertTrue(observed["called"])
        self.assertEqual(
            sim.route_length, _CONTAINER_ROUTE_LENGTH,
            "an exception mid-trip must not leak routeLength into the next trip",
        )
        self.assertEqual(sim.city_lat, _CONTAINER_LAT)
        self.assertEqual(sim.city_lng, _CONTAINER_LNG)
        self.assertEqual(sim.current_city, _CONTAINER_CITY_NAME)

    def test_a_partial_apply_is_still_fully_undone(self):
        """Review Cycle 1, Suggestion 1: snapshot precedes apply, apply is inside the try.

        If `_apply_trip_overrides` raises part-way, the attributes it already set must
        still be restored. Folding the snapshot into the apply made that impossible,
        because the snapshot was the apply's return value and an exception discarded it.
        Simulated by making the second `setattr` fail.
        """
        pl, sim = _make_presence()
        real_apply = pl._apply_trip_overrides

        def half_apply(overrides):
            # Apply one attribute, then fail — the shape of a part-way exception.
            real_apply({"route_length": 45})
            raise RuntimeError("injected failure mid-apply")

        pl._apply_trip_overrides = half_apply
        _drive_one_intent(
            pl, {"simulationId": "sim-s1", "tripsCount": 1,
                 "city": "munich", "routeLength": 45},
        )
        self.assertEqual(
            sim.route_length, _CONTAINER_ROUTE_LENGTH,
            "an exception mid-apply must not leave a half-applied override behind",
        )
        self.assertEqual(sim.city_lat, _CONTAINER_LAT)

    def test_absent_city_attributes_are_restored_to_absent(self):
        """A container with no --city leaves city_lat/city_lng unset.

        `generate_telemetry_data` reads them behind `hasattr` and falls back to the
        vehicle's own location, so "absent" is a distinct behaviour. Restoring them
        as None would not be a restore.
        """
        sim = _make_simulator()
        del sim.city_lat
        del sim.city_lng
        pl, sim = _make_presence(sim)
        _drive_one_intent(
            pl, {"simulationId": "sim-c2", "tripsCount": 1, "city": "munich"},
        )
        self.assertFalse(hasattr(sim, "city_lat"))
        self.assertFalse(hasattr(sim, "city_lng"))


class TestClampAndFallbacks(unittest.TestCase):
    """(d)/(e)/(f) Clamp bounds, and fail-soft on malformed or unknown input."""

    def test_route_length_above_range_clamps_to_60(self):
        """M6's target: widening the clamp to [1, 999] must fail this."""
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-d", "tripsCount": 1, "routeLength": 600},
        )
        self.assertEqual(observed["route_length"], 60)

    def test_route_length_below_range_clamps_to_5(self):
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-d2", "tripsCount": 1, "routeLength": 2},
        )
        self.assertEqual(observed["route_length"], 5)

    def test_malformed_route_length_keeps_container_default_and_trip_runs(self):
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-e", "tripsCount": 1, "routeLength": "abc"},
        )
        self.assertTrue(observed["called"], "a malformed parameter must not drop the trip")
        self.assertEqual(observed["route_length"], _CONTAINER_ROUTE_LENGTH)

    def test_unknown_city_keeps_container_default_and_trip_runs(self):
        """M7's target: raising instead of falling back must fail this."""
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-f", "tripsCount": 1, "city": "atlantis"},
        )
        self.assertTrue(observed["called"], "an unknown city must not drop the trip")
        self.assertEqual(observed["city_lat"], _CONTAINER_LAT)
        self.assertEqual(observed["city_lng"], _CONTAINER_LNG)
        self.assertEqual(observed["current_city"], _CONTAINER_CITY_NAME)

    def test_a_bad_city_does_not_suppress_a_good_route_length(self):
        """The two parameters are resolved independently."""
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-f2", "tripsCount": 1,
                 "city": "atlantis", "routeLength": 30},
        )
        self.assertEqual(observed["route_length"], 30)
        self.assertEqual(observed["city_lat"], _CONTAINER_LAT)


class TestResolverIsFailSoftByItself(unittest.TestCase):
    """`_resolve_trip_overrides` must fall back *without raising* — asserted directly.

    Why this class exists: `run()` wraps the resolver in a last-resort `except` so a
    malformed intent can never drop a trip. That makes the end-to-end assertion
    "the trip still ran with container defaults" **over-determined** — it is satisfied
    either by the resolver falling back or by the safety net swallowing a raise. When
    mutation M7 (unrecognised city raises instead of falling back) was run against
    that end-to-end test, the test stayed green.

    So these tests pin the resolver's own contract, and the test below them pins M7's
    real cost: an exception mid-resolve discards a perfectly valid parameter that
    arrived in the same intent.
    """

    def test_unknown_city_returns_no_override_rather_than_raising(self):
        pl, _ = _make_presence()
        overrides, catalog_scenarios = pl._resolve_trip_overrides(
            {"simulationId": "sim-r1", "city": "atlantis"},
        )
        self.assertEqual(
            overrides, {},
            "an unrecognised city must resolve to 'no override', not an exception",
        )

    def test_malformed_route_length_returns_no_override_rather_than_raising(self):
        pl, _ = _make_presence()
        overrides, catalog_scenarios = pl._resolve_trip_overrides(
            {"simulationId": "sim-r2", "routeLength": "abc"},
        )
        self.assertEqual(overrides, {})

    def test_unknown_city_still_yields_the_valid_route_length_from_the_same_intent(self):
        """M7's real cost: raising mid-resolve throws away a good parameter too."""
        pl, _ = _make_presence()
        overrides, catalog_scenarios = pl._resolve_trip_overrides(
            {"simulationId": "sim-r3", "city": "atlantis", "routeLength": 30},
        )
        self.assertEqual(
            overrides, {"route_length": 30},
            "a bad city must not suppress a valid routeLength in the same intent",
        )


class TestCityKeySetCoupling(unittest.TestCase):
    """The city key set is declared in three places and must stay identical.

    T2.1 promoted the coordinate map from a local in `main()` to the module-level
    `CITY_COORDINATES` so the intent path and the argv path share one map. That makes
    three coupled declarations:

      1. `CITY_COORDINATES` (resolves a name to coordinates),
      2. the `--city` argparse `choices` (the argv path's validation), and
      3. `_ALLOWED_CITIES` in the subscriptions handler (the API's validation).

    Spec § Constraints records these as "verified no drift; keep it that way", and the
    source carries a COUPLED KEY SET comment. A coupling rule with no executable form is
    not a control — this spec has already been bitten three times by prose that promised
    more than the code delivered. So it is asserted here.

    The failure this prevents is concrete and is this spec's own defect class: a city
    accepted by `_ALLOWED_CITIES` but absent from `CITY_COORDINATES` passes API
    validation, reaches `_resolve_trip_overrides`, resolves to None, and silently falls
    back to the container default — a caller's choice discarded with a 200.

    Read from source rather than imported: the handler pulls boto3 clients at import
    time, the same reason `check_trip_intent_contract.py` parses instead of importing.
    """

    @staticmethod
    def _repo_root() -> str:
        return os.path.dirname(os.path.dirname(_SIM_DIR))

    def _argparse_city_choices(self) -> set:
        import ast
        src = open(
            os.path.join(_SIM_DIR, "realtime_telemetry_simulator.py"), encoding="utf-8"
        ).read()
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            if getattr(node.func, "attr", "") != "add_argument":
                continue
            if not node.args or getattr(node.args[0], "value", None) != "--city":
                continue
            for kw in node.keywords:
                if kw.arg == "choices":
                    return {elt.value for elt in kw.value.elts}
        self.fail("could not locate the --city argparse choices")

    def _handler_allowed_cities(self) -> set:
        import ast
        path = os.path.join(
            self._repo_root(),
            "services/connectors/subscriptions/simulate_vehicle/handler.py",
        )
        src = open(path, encoding="utf-8").read()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.Assign) and any(
                getattr(t, "id", "") == "_ALLOWED_CITIES" for t in node.targets
            ):
                call = node.value                      # frozenset({...})
                inner = call.args[0] if isinstance(call, ast.Call) else call
                # Fail with the shape, not an AttributeError. This test guards a
                # coupling; a cryptic crash invites "fixing" it by deletion, whereas a
                # named shape change invites updating the extractor deliberately.
                # (Review Cycle 1, Suggestion 4.)
                elts = getattr(inner, "elts", None)
                if elts is None:
                    self.fail(
                        f"_ALLOWED_CITIES is no longer a literal set/frozenset — got "
                        f"{type(inner).__name__}. Update this extractor deliberately; "
                        f"do not delete the coupling assertion."
                    )
                if not all(isinstance(e, ast.Constant) for e in elts):
                    self.fail(
                        "_ALLOWED_CITIES contains non-literal members, so the key set "
                        "cannot be compared statically. Update this extractor "
                        "deliberately; do not delete the coupling assertion."
                    )
                return {e.value for e in elts}
        self.fail("could not locate _ALLOWED_CITIES in the subscriptions handler")

    def test_coordinate_map_matches_the_argv_city_choices(self):
        self.assertEqual(set(CITY_COORDINATES), self._argparse_city_choices())

    def test_coordinate_map_matches_the_api_allowlist(self):
        """An API-accepted city with no coordinates is silently discarded — this spec's
        own defect class, re-introduced at a new site."""
        self.assertEqual(set(CITY_COORDINATES), self._handler_allowed_cities())

    def test_every_coordinate_is_a_plausible_lat_lng_pair(self):
        for city, coords in CITY_COORDINATES.items():
            self.assertEqual(len(coords), 2, f"{city} must be a (lat, lng) pair")
            lat, lng = coords
            self.assertTrue(-90 <= lat <= 90, f"{city} latitude {lat} out of range")
            self.assertTrue(-180 <= lng <= 180, f"{city} longitude {lng} out of range")


class TestAbsentParameters(unittest.TestCase):
    """(g)/(h) Absent means absent — and the writer's sentinels are absent."""

    def test_intent_with_neither_key_changes_nothing(self):
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-g", "tripsCount": 1},
        )
        self.assertEqual(observed["route_length"], _CONTAINER_ROUTE_LENGTH)
        self.assertEqual(observed["city_lat"], _CONTAINER_LAT)
        self.assertEqual(observed["current_city"], _CONTAINER_CITY_NAME)

    def test_writer_sentinels_leave_container_values_untouched(self):
        """M8's target, and the defect review Cycle 1 caught in T2.1's own Accept.

        `_write_trip_intent` emits `config.get("route_length") or 0` and
        `config.get("city") or ""`, so an omitted parameter arrives as 0 / "".
        Mapping those to "absent" must happen BEFORE the clamp: `max(5, min(60, 0))`
        is **5**, which would silently give a caller who asked for nothing the
        shortest possible trip.

        Asserted by value against 20, not as "unchanged", so the 5 is caught.
        """
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-h", "tripsCount": 1,
                 "city": "", "routeLength": 0},
        )
        self.assertEqual(
            observed["route_length"], 20,
            "routeLength 0 is the writer's absent-sentinel: the trip must run the "
            "container default of 20, not the clamp floor of 5",
        )
        self.assertNotEqual(
            observed["route_length"], 5,
            "route_length == 5 means the sentinel reached the [5, 60] clamp",
        )
        self.assertEqual(observed["city_lat"], _CONTAINER_LAT)
        self.assertEqual(observed["current_city"], _CONTAINER_CITY_NAME)

    def test_string_zero_is_treated_as_absent_not_clamped_to_five(self):
        """`"0" == 0` is False in Python, so the pre-coercion sentinel check misses it.

        Without the post-coercion check it reaches the clamp and yields 5 — the M8
        defect via a different spelling of the same absent input. Surfaced by the
        Group 2 security review.
        """
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-h4", "tripsCount": 1, "routeLength": "0"},
        )
        self.assertEqual(observed["route_length"], 20)
        self.assertNotEqual(observed["route_length"], 5)

    def test_fractional_route_length_below_one_is_treated_as_absent(self):
        """`int(Decimal("0.9"))` truncates to 0, which must mean absent, not 5."""
        from decimal import Decimal
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-h5", "tripsCount": 1,
                 "routeLength": Decimal("0.9")},
        )
        self.assertEqual(observed["route_length"], 20)

    def test_negative_route_length_is_treated_as_absent(self):
        """A negative value is not a short trip request; it is nonsense input.

        Clamping it to 5 would silently invent a trip length the caller never asked
        for, which is the same substitution the sentinel checks exist to prevent.
        """
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-h6", "tripsCount": 1, "routeLength": -10},
        )
        self.assertEqual(observed["route_length"], 20)

    def test_a_genuinely_small_route_length_still_clamps_up_to_five(self):
        """Positive control — the post-coercion check must not swallow real values.

        2 is a real (if small) request and must still clamp to the floor of 5, not fall
        back to 20. Without this, widening the absent-test to `<= 5` would look correct.
        """
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-h7", "tripsCount": 1, "routeLength": 2},
        )
        self.assertEqual(observed["route_length"], 5)

    def test_decimal_zero_from_dynamodb_is_also_treated_as_absent(self):
        """boto3's resource API returns numbers as `Decimal`, not `int`."""
        from decimal import Decimal
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-h2", "tripsCount": 1,
                 "routeLength": Decimal("0")},
        )
        self.assertEqual(observed["route_length"], 20)

    def test_decimal_route_length_from_dynamodb_is_applied(self):
        from decimal import Decimal
        pl, _ = _make_presence()
        observed = _drive_one_intent(
            pl, {"simulationId": "sim-h3", "tripsCount": 1,
                 "routeLength": Decimal("45")},
        )
        self.assertEqual(observed["route_length"], 45)


class TestStaleRouteInvalidation(unittest.TestCase):
    """The mechanism that makes the override effective at all on the warm path.

    `generate_telemetry_data` rebuilds the route only when the owned VehicleState
    has none. At trip end it clears `trip_started`/`route_index` but leaves `route`
    populated, and `run_trips` breaks after its last trip before the between-trip
    `reset()`. So without invalidation the first trip of every later intent re-drives
    the PREVIOUS route — stale city, stale point count — and the override is read by
    nobody. Deleting `_invalidate_cached_route`'s body makes these fail.
    """

    def test_stale_route_is_cleared_before_an_overridden_trip(self):
        pl, _ = _make_presence()
        # Simulate a warm container: a completed trip left its route behind.
        pl.vehicle_state.route = [{"lat": 47.6, "lng": -122.3}] * 20
        pl.vehicle_state.route_index = 19

        observed = _drive_one_intent(
            pl, {"simulationId": "sim-i", "tripsCount": 1,
                 "city": "munich", "routeLength": 45},
        )
        self.assertEqual(
            observed["route_at_trip_start"], [],
            "a stale route would be re-driven, discarding the override entirely",
        )
        self.assertEqual(pl.vehicle_state.route_index, 0)

    def test_stale_route_is_cleared_even_when_no_override_is_requested(self):
        """Restore is only observable if the no-parameter path also rebuilds.

        After a trip that overrode the city, an intent carrying no parameters must
        fall back to the container default rather than re-driving the overridden
        route. T3.2's third start checks this live.
        """
        pl, _ = _make_presence()
        pl.vehicle_state.route = [{"lat": 48.1, "lng": 11.5}] * 45
        pl.vehicle_state.route_index = 44

        observed = _drive_one_intent(
            pl, {"simulationId": "sim-i2", "tripsCount": 1},
        )
        self.assertEqual(observed["route_at_trip_start"], [])
        self.assertEqual(observed["route_length"], _CONTAINER_ROUTE_LENGTH)


class TestRouteGenerationConsumesTheOverride(unittest.TestCase):
    """The strongest by-value link available without a live container.

    The tests above assert the simulator carries the requested values when the trip
    starts. This one goes one step further and drives the *real* read site —
    `generate_telemetry_data`'s route-init branch — to prove the override reaches
    `generate_route_points`' arguments rather than merely sitting on an attribute.
    That is the "binds a key and discards it" failure the contract guard cannot see.
    """

    def _route_call_for(self, overrides: dict):
        from realtime_telemetry_simulator import VehicleState
        sim = _make_simulator()
        pl = PresenceLoop(sim, _VEHICLE_ID, FakeMqttClient())

        resolved, _catalog = pl._resolve_trip_overrides(overrides)
        snapshot = pl._snapshot_sim_attrs()
        pl._apply_trip_overrides(resolved)
        pl._invalidate_cached_route()

        calls = []
        sim.generate_route_points = lambda lat, lon, num_points=20: (
            calls.append({"lat": lat, "lon": lon, "num_points": num_points})
            or [{"lat": lat, "lng": lon}] * max(2, num_points)
        )

        vs = pl.vehicle_state
        vs.current_driver_id = "DRV-TEST-1"   # skip the drivers-table read
        try:
            sim.generate_telemetry_data(
                {"vehicleId": _VEHICLE_ID, "make": "Meridian", "model": "Zephyr",
                 "location": {"latitude": 1.0, "longitude": 2.0}},
                vs,
                False,
            )
        finally:
            pl._restore_trip_overrides(snapshot)
        self.assertEqual(len(calls), 1, "the route-init branch must have run exactly once")
        return calls[0]

    def test_overridden_city_and_route_length_reach_generate_route_points(self):
        call = self._route_call_for(
            {"simulationId": "sim-j", "city": "atlanta", "routeLength": 10},
        )
        atl_lat, atl_lng = CITY_COORDINATES["atlanta"]
        self.assertEqual(call["num_points"], 10)
        self.assertEqual(call["lat"], atl_lat)
        self.assertEqual(call["lon"], atl_lng)

    def test_sentinel_intent_reaches_generate_route_points_as_the_container_default(self):
        call = self._route_call_for(
            {"simulationId": "sim-j2", "city": "", "routeLength": 0},
        )
        self.assertEqual(
            call["num_points"], 20,
            "the sentinel must not reach the clamp — 5 points here is the M8 defect",
        )
        self.assertEqual(call["lat"], _CONTAINER_LAT)
        self.assertEqual(call["lon"], _CONTAINER_LNG)


class TestMainFailsClosedOnUnsetStage(unittest.TestCase):
    """FG14.T2 — main() must not default DEPLOYMENT_STAGE to 'prod'.

    Both call sites that previously read os.environ.get('DEPLOYMENT_STAGE', 'prod')
    now fail closed (sys.exit(1)) when the variable is unset.  This test pins that
    property so a future 'prod' default regression is caught immediately.

    The mutation to verify: restore one os.environ.get('DEPLOYMENT_STAGE', 'prod')
    default at either call site → this test must fail (the mock EventCatalogDriver
    would be called with stage='prod' rather than the process exiting).
    """

    def _run_main_with_args(self, extra_args, *, env_overrides):
        """Invoke main() with a controlled env and return (exit_code, stage_used).

        Stubs out everything that would actually hit AWS or the filesystem.
        Returns the stage that would have been passed to EventCatalogDriver, or
        'exited:<code>' if main() called sys.exit before constructing the driver.
        """
        import realtime_telemetry_simulator as rts

        captured = {}

        class _FakeECD:
            def __init__(self, *, region, stage, profile=None):
                captured['stage'] = stage
                captured['constructed'] = True

            def get_safe_ranges(self):
                return {}

            def list_events(self, category=None):
                return []

        argv = ['realtime_telemetry_simulator.py'] + extra_args
        exit_code = None

        with patch.dict(os.environ, env_overrides, clear=False), \
             patch.object(sys, 'argv', argv), \
             patch.dict('sys.modules', {'event_catalog_driver': MagicMock(
                 EventCatalogDriver=_FakeECD)}):
            # Patch the local import inside main() by pre-loading our stub
            import importlib
            # Ensure clean state for the in-function `from event_catalog_driver import`
            with patch('builtins.__import__', side_effect=lambda name, *a, **k: (
                type('_M', (), {'EventCatalogDriver': _FakeECD})() if name == 'event_catalog_driver' else __import__(name, *a, **k)
            )):
                try:
                    rts.main()
                except SystemExit as exc:
                    exit_code = exc.code

        if exit_code is not None and not captured.get('constructed'):
            return f'exited:{exit_code}'
        return captured.get('stage', 'never_reached')

    def test_unset_deployment_stage_does_not_reach_prod_catalog(self):
        """When DEPLOYMENT_STAGE is absent, main() exits non-zero without constructing
        EventCatalogDriver — a 'prod' default would construct it with stage='prod',
        which is the defect this test pins."""
        env = dict(os.environ)
        env.pop('DEPLOYMENT_STAGE', None)
        with patch.dict(os.environ, {}, clear=True):
            # Re-apply everything except DEPLOYMENT_STAGE
            env_without_stage = {k: v for k, v in env.items() if k != 'DEPLOYMENT_STAGE'}
            import realtime_telemetry_simulator as rts

            captured_stage = None
            exited_code = None

            class _SentinelECD:
                def __init__(self, *, region, stage, profile=None):
                    nonlocal captured_stage
                    captured_stage = stage

                def get_safe_ranges(self):
                    return {}

            argv = ['realtime_telemetry_simulator.py',
                    '--vehicles', '1', '--trips', '1',
                    '--region', 'us-east-1']

            with patch.dict(os.environ, env_without_stage, clear=True), \
                 patch.object(sys, 'argv', argv):
                # Stub the in-function import via sys.modules
                import sys as _sys
                fake_ecd_mod = MagicMock()
                fake_ecd_mod.EventCatalogDriver = _SentinelECD
                orig = _sys.modules.get('event_catalog_driver')
                _sys.modules['event_catalog_driver'] = fake_ecd_mod
                try:
                    rts.main()
                except SystemExit as exc:
                    exited_code = exc.code
                finally:
                    if orig is None:
                        _sys.modules.pop('event_catalog_driver', None)
                    else:
                        _sys.modules['event_catalog_driver'] = orig

            # The guard must have fired before EventCatalogDriver was constructed.
            # If a 'prod' default is restored, captured_stage == 'prod' and this fails.
            self.assertIsNone(
                captured_stage,
                f"DEPLOYMENT_STAGE was unset but EventCatalogDriver was constructed "
                f"with stage={captured_stage!r} — a 'prod' default was applied",
            )
            self.assertIsNotNone(exited_code, "main() must exit non-zero when DEPLOYMENT_STAGE is unset")
            self.assertNotEqual(exited_code, 0, "exit code must be non-zero (fail-closed)")

    def test_set_deployment_stage_is_forwarded_to_catalog(self):
        """Sanity check: when DEPLOYMENT_STAGE='staging', main() constructs the driver
        with stage='staging' and does not exit early."""
        import realtime_telemetry_simulator as rts

        captured_stage = None
        exited_code = None

        class _SentinelECD:
            def __init__(self, *, region, stage, profile=None):
                nonlocal captured_stage
                captured_stage = stage

            def get_safe_ranges(self):
                return {}

            def set_active_events(self, events):
                pass

            def compute_degradation_targets(self):
                return {}

        argv = ['realtime_telemetry_simulator.py',
                '--vehicles', '1', '--trips', '1',
                '--region', 'us-east-1']

        import sys as _sys
        fake_ecd_mod = MagicMock()
        fake_ecd_mod.EventCatalogDriver = _SentinelECD
        orig = _sys.modules.get('event_catalog_driver')
        _sys.modules['event_catalog_driver'] = fake_ecd_mod
        try:
            with patch.dict(os.environ, {'DEPLOYMENT_STAGE': 'staging'}, clear=False), \
                 patch.object(sys, 'argv', argv), \
                 patch.object(rts, 'RealtimeTelemetrySimulator') as mock_sim_cls:
                mock_sim_cls.return_value = MagicMock()
                mock_sim_cls.return_value.run = MagicMock(side_effect=SystemExit(0))
                try:
                    rts.main()
                except SystemExit as exc:
                    exited_code = exc.code
        finally:
            if orig is None:
                _sys.modules.pop('event_catalog_driver', None)
            else:
                _sys.modules['event_catalog_driver'] = orig

        self.assertEqual(
            captured_stage, 'staging',
            f"Expected stage='staging' to be forwarded, got {captured_stage!r}",
        )


if __name__ == "__main__":
    unittest.main()
