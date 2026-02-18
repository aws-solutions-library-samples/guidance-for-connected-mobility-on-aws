"""
E2E Integration Tests for Connected Mobility Solution.

Runs a real simulation against the deployed AWS stack and verifies:
- Trips are created in DynamoDB with correct fields
- Vehicle records are updated by Flink
- Safety events are generated
- Maintenance alerts are generated
- Fleet API returns correct data
- Driver scoring produces realistic values

Prerequisites:
  - Simulation API running at localhost:5001
  - All Flink processors RUNNING
  - AWS profile configured
  - At least one vehicle in DynamoDB

Usage:
  cd /Users/givenand/connected-mobility-guidance-on-aws
  pytest tests/e2e/ -v --tb=short
"""
import time
import pytest
import requests
from boto3.dynamodb.conditions import Key
from conftest import (
    TRIPS_TABLE, VEHICLES_TABLE, DRIVERS_TABLE,
    SAFETY_TABLE, MAINTENANCE_TABLE,
)


# ---------------------------------------------------------------------------
# Trip assertions
# ---------------------------------------------------------------------------
class TestTrips:
    """Verify Flink TripProcessor wrote correct trip data."""

    def _get_trips(self, dynamodb, vehicle_id):
        table = dynamodb.Table(TRIPS_TABLE)
        resp = table.query(
            IndexName="vehicleId-index",
            KeyConditionExpression=Key("vehicleId").eq(vehicle_id),
        )
        return resp.get("Items", [])

    def test_trip_created(self, dynamodb, run_simulation):
        trips = self._get_trips(dynamodb, run_simulation["vehicle_id"])
        assert len(trips) >= 1, "No trips found for vehicle"

    def test_trip_status_completed(self, dynamodb, run_simulation):
        trips = self._get_trips(dynamodb, run_simulation["vehicle_id"])
        completed = [t for t in trips if t.get("status") == "COMPLETED"]
        assert completed, "No COMPLETED trips found"

    def test_trip_has_distance(self, dynamodb, run_simulation):
        trips = self._get_trips(dynamodb, run_simulation["vehicle_id"])
        completed = [t for t in trips if t.get("status") == "COMPLETED"]
        for t in completed:
            dist = float(t.get("totalDistance", 0))
            assert dist > 0, f"Trip {t['tripId']} has totalDistance=0"

    def test_trip_has_route_points(self, dynamodb, run_simulation):
        trips = self._get_trips(dynamodb, run_simulation["vehicle_id"])
        completed = [t for t in trips if t.get("status") == "COMPLETED"]
        for t in completed:
            route = t.get("route", [])
            assert len(route) > 1, f"Trip {t['tripId']} has {len(route)} route points (expected >1)"

    def test_trip_has_max_speed(self, dynamodb, run_simulation):
        trips = self._get_trips(dynamodb, run_simulation["vehicle_id"])
        completed = [t for t in trips if t.get("status") == "COMPLETED"]
        for t in completed:
            assert float(t.get("maxSpeed", 0)) > 0, f"Trip {t['tripId']} has maxSpeed=0"

    def test_trip_has_driver(self, dynamodb, run_simulation):
        trips = self._get_trips(dynamodb, run_simulation["vehicle_id"])
        completed = [t for t in trips if t.get("status") == "COMPLETED"]
        for t in completed:
            assert t.get("driverId"), f"Trip {t['tripId']} missing driverId"

    def test_trip_has_telemetry_count(self, dynamodb, run_simulation):
        trips = self._get_trips(dynamodb, run_simulation["vehicle_id"])
        completed = [t for t in trips if t.get("status") == "COMPLETED"]
        for t in completed:
            assert int(t.get("telemetryCount", 0)) > 0, f"Trip {t['tripId']} has telemetryCount=0"


# ---------------------------------------------------------------------------
# Vehicle assertions
# ---------------------------------------------------------------------------
class TestVehicle:
    """Verify Flink updated the vehicle record after trip completion."""

    def _get_vehicle(self, dynamodb, vehicle_id):
        table = dynamodb.Table(VEHICLES_TABLE)
        resp = table.get_item(Key={"vehicleId": vehicle_id})
        return resp.get("Item")

    def test_vehicle_exists(self, dynamodb, run_simulation):
        v = self._get_vehicle(dynamodb, run_simulation["vehicle_id"])
        assert v, "Vehicle not found in DynamoDB"

    def test_vehicle_idle_after_trip(self, dynamodb, run_simulation):
        v = self._get_vehicle(dynamodb, run_simulation["vehicle_id"])
        assert v.get("activityStatus") == "idle", f"Expected idle, got {v.get('activityStatus')}"

    def test_vehicle_connected(self, dynamodb, run_simulation):
        v = self._get_vehicle(dynamodb, run_simulation["vehicle_id"])
        assert v.get("connectionStatus") == "connected"

    def test_vehicle_has_tire_pressure(self, dynamodb, run_simulation):
        v = self._get_vehicle(dynamodb, run_simulation["vehicle_id"])
        for field in ("tire_fl", "tire_fr", "tire_rl", "tire_rr"):
            val = v.get(field)
            assert val is not None, f"Vehicle missing {field}"
            assert float(val) > 0, f"Vehicle {field}={val}"

    def test_vehicle_has_odometer(self, dynamodb, run_simulation):
        v = self._get_vehicle(dynamodb, run_simulation["vehicle_id"])
        assert float(v.get("odometer", 0)) > 0

    def test_vehicle_has_last_updated(self, dynamodb, run_simulation):
        v = self._get_vehicle(dynamodb, run_simulation["vehicle_id"])
        assert v.get("lastUpdated"), "Vehicle missing lastUpdated"


# ---------------------------------------------------------------------------
# Driver scoring
# ---------------------------------------------------------------------------
class TestDriverScoring:
    """Verify weighted driver scoring produces realistic values."""

    def test_driver_score_in_range(self, dynamodb, run_simulation):
        trips_table = dynamodb.Table(TRIPS_TABLE)
        resp = trips_table.query(
            IndexName="vehicleId-index",
            KeyConditionExpression=Key("vehicleId").eq(run_simulation["vehicle_id"]),
        )
        completed = [t for t in resp.get("Items", []) if t.get("status") == "COMPLETED"]
        assert completed, "No completed trips to check scoring"
        for t in completed:
            score = t.get("driverScore")
            assert score is not None, f"Trip {t['tripId']} missing driverScore"
            score = float(score)
            assert 15 <= score <= 100, f"Driver score {score} outside expected range [15,100]"


# ---------------------------------------------------------------------------
# Safety events
# ---------------------------------------------------------------------------
class TestSafetyEvents:
    """Verify SafetyProcessor wrote safety events."""

    def test_safety_events_exist(self, dynamodb, run_simulation):
        table = dynamodb.Table(SAFETY_TABLE)
        # Try GSI first
        try:
            resp = table.query(
                IndexName="vehicleId-index",
                KeyConditionExpression=Key("vehicleId").eq(run_simulation["vehicle_id"]),
            )
            events = resp.get("Items", [])
        except Exception:
            # Fallback to scan
            resp = table.scan(
                FilterExpression=Key("vehicleId").eq(run_simulation["vehicle_id"]),
            )
            events = resp.get("Items", [])
        assert len(events) >= 1, "No safety events found for vehicle"

    def test_safety_event_has_type(self, dynamodb, run_simulation):
        table = dynamodb.Table(SAFETY_TABLE)
        try:
            resp = table.query(
                IndexName="vehicleId-index",
                KeyConditionExpression=Key("vehicleId").eq(run_simulation["vehicle_id"]),
            )
        except Exception:
            resp = table.scan(
                FilterExpression=Key("vehicleId").eq(run_simulation["vehicle_id"]),
            )
        for ev in resp.get("Items", []):
            assert ev.get("eventType") or ev.get("alertType") or ev.get("type"), \
                f"Safety event missing type field: {ev.get('eventId', 'unknown')}"


# ---------------------------------------------------------------------------
# Maintenance alerts
# ---------------------------------------------------------------------------
class TestMaintenanceAlerts:
    """Verify MaintenanceProcessor wrote maintenance alerts."""

    def test_maintenance_alerts_exist(self, dynamodb, run_simulation):
        table = dynamodb.Table(MAINTENANCE_TABLE)
        resp = table.scan()
        # Filter for our vehicle
        alerts = [a for a in resp.get("Items", [])
                  if a.get("vehicleId") == run_simulation["vehicle_id"]]
        assert len(alerts) >= 1, "No maintenance alerts found for vehicle"


# ---------------------------------------------------------------------------
# Fleet API assertions
# ---------------------------------------------------------------------------
class TestFleetAPI:
    """Verify the Fleet API Lambda returns correct data."""

    def test_api_vehicle_detail(self, fleet_api_url, run_simulation):
        vid = run_simulation["vehicle_id"]
        resp = requests.get(f"{fleet_api_url}/api/v1/vehicles/{vid}", timeout=10)
        assert resp.status_code == 200, f"API returned {resp.status_code}: {resp.text}"
        data = resp.json()
        vehicle = data.get("vehicle", data)
        assert vehicle.get("vehicleId") == vid

    def test_api_vehicle_trips(self, fleet_api_url, run_simulation):
        vid = run_simulation["vehicle_id"]
        resp = requests.get(f"{fleet_api_url}/api/v1/vehicles/{vid}/trips", timeout=10)
        assert resp.status_code == 200
        data = resp.json()
        trips = data.get("trips", [])
        assert len(trips) >= 1, "API returned no trips for vehicle"

    def test_api_vehicle_safety_events(self, fleet_api_url, run_simulation):
        vid = run_simulation["vehicle_id"]
        resp = requests.get(f"{fleet_api_url}/api/v1/vehicles/{vid}/safety-events", timeout=10)
        assert resp.status_code == 200

    def test_api_vehicle_maintenance_alerts(self, fleet_api_url, run_simulation):
        vid = run_simulation["vehicle_id"]
        resp = requests.get(f"{fleet_api_url}/api/v1/vehicles/{vid}/maintenance-alerts", timeout=10)
        assert resp.status_code == 200

    def test_api_trip_detail_has_driver(self, fleet_api_url, dynamodb, run_simulation):
        """Verify trip detail endpoint resolves driver name."""
        vid = run_simulation["vehicle_id"]
        trips_table = dynamodb.Table(TRIPS_TABLE)
        resp = trips_table.query(
            IndexName="vehicleId-index",
            KeyConditionExpression=Key("vehicleId").eq(vid),
        )
        completed = [t for t in resp.get("Items", []) if t.get("status") == "COMPLETED"]
        if not completed:
            pytest.skip("No completed trips")
        trip = completed[0]
        trip_id = trip["tripId"]
        r = requests.get(f"{fleet_api_url}/api/v1/vehicles/{vid}/trips/{trip_id}", timeout=10)
        assert r.status_code == 200
        data = r.json()
        trip_data = data.get("trip", data)
        assert trip_data.get("driverName") or trip_data.get("driverId"), \
            "Trip detail missing driver info"
