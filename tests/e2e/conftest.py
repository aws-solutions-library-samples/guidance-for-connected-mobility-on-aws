"""E2E test configuration and fixtures."""
import os
import pytest
import boto3
import requests
import time

AWS_PROFILE = os.environ.get("AWS_PROFILE", "givenand-CMS")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
SIM_API_URL = os.environ.get("SIM_API_URL", "http://localhost:5001")
FLEET_API_URL = os.environ.get(
    "FLEET_API_URL",
    "https://m69g1bjadl.execute-api.us-east-1.amazonaws.com/prod",
)

# DynamoDB table names
TRIPS_TABLE = "cms-dev-storage-trips"
VEHICLES_TABLE = "cms-dev-storage-vehicles"
DRIVERS_TABLE = "cms-dev-storage-drivers"
SAFETY_TABLE = "cms-dev-storage-safety-events"
MAINTENANCE_TABLE = "cms-dev-storage-maintenance-alerts"


@pytest.fixture(scope="session")
def boto_session():
    return boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)


@pytest.fixture(scope="session")
def dynamodb(boto_session):
    return boto_session.resource("dynamodb")


@pytest.fixture(scope="session")
def fleet_api_url():
    return FLEET_API_URL


@pytest.fixture(scope="session")
def sim_api_url():
    return SIM_API_URL


@pytest.fixture(scope="session")
def run_simulation(sim_api_url):
    """Run a single-vehicle, single-trip simulation and wait for completion."""
    # Pick a real vehicle from DynamoDB
    session = boto3.Session(profile_name=AWS_PROFILE, region_name=AWS_REGION)
    ddb = session.resource("dynamodb")
    vehicles_table = ddb.Table(VEHICLES_TABLE)
    scan = vehicles_table.scan(Limit=5)
    items = scan.get("Items", [])
    assert items, "No vehicles in DynamoDB — seed vehicles first"

    vehicle = items[0]
    vehicle_id = vehicle["vehicleId"]

    config = {
        "vehicle_source": "real_vehicles",
        "vehicles": [vehicle_id],
        "trips": 1,
        "city": "seattle",
        "safety_rate": 0.5,  # high rate to guarantee safety events
        "interval": 5,
        "force_engine_overheat": True,  # guarantee maintenance alert
        "force_safety_event": "hard_braking",
        "progressive_degradation": True,
    }

    # Start simulation
    resp = requests.post(f"{sim_api_url}/api/simulation/start", json=config, timeout=10)
    assert resp.status_code == 200, f"Failed to start simulation: {resp.text}"
    data = resp.json()
    sim_id = data["simulation_id"]

    # Poll until completed or timeout (5 minutes)
    deadline = time.time() + 300
    status = None
    while time.time() < deadline:
        time.sleep(10)
        sr = requests.get(f"{sim_api_url}/api/simulation/{sim_id}/status", timeout=10)
        if sr.status_code == 200:
            status = sr.json()
            if status.get("status") in ("completed", "stopped", "error"):
                break

    # Give Flink extra time to process the last messages
    time.sleep(30)

    return {
        "simulation_id": sim_id,
        "vehicle_id": vehicle_id,
        "status": status,
    }
