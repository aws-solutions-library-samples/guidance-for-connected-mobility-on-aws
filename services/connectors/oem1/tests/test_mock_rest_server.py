"""
Tests for mock_rest_server.py — covers all 6 REST endpoints + /reset,
singular/plural enroll/unenroll asymmetry, failure injection, and status
filter matrix.

Runs without any live OEM1 access; in-process mock server on a random port.
"""

import json
import sys
import time
from pathlib import Path

import pytest
import requests

# Ensure mock_rest_server is importable from this test directory
_ROOT = Path(__file__).parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import mock_rest_server as mrs


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def server():
    """Start one mock server for the whole module; reset between tests."""
    srv, port = mrs.start_server_thread(port=0)
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


@pytest.fixture(autouse=True)
def reset(server):
    """Reset server state before each test."""
    resp = requests.post(f"{server}/reset")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def post(server, path, body=None, headers=None):
    return requests.post(f"{server}{path}", json=body or {}, headers=headers or {})


# ---------------------------------------------------------------------------
# /reset
# ---------------------------------------------------------------------------

def test_reset_clears_state(server):
    # Enroll something, then reset, then status/latest should return empty
    post(server, "/enrollment/v2/enroll", {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "V0001"}]})
    post(server, "/reset")
    r = post(server, "/enrollment/v2/status/latest", {"request_ids": [1]})
    assert r.status_code == 200
    assert r.json() == []


# ---------------------------------------------------------------------------
# POST /enrollment/v2/enroll — singular `product` (AQ1)
# ---------------------------------------------------------------------------

def test_enroll_happy_path(server):
    r = post(server, "/enrollment/v2/enroll", {
        "product": "SKU-X",
        "vehicles": [{"name": "T", "vin": "1FTFW1E16JFD55835"}],
    })
    assert r.status_code == 202
    data = r.json()
    assert isinstance(data["request_id"], int)
    assert data["request_id"] >= 1


def test_enroll_requires_singular_product(server):
    """Enroll must reject payloads using plural 'products' (wrong field)."""
    r = post(server, "/enrollment/v2/enroll", {
        "products": ["SKU-X"],  # WRONG — should be singular 'product'
        "vehicles": [{"name": "T", "vin": "V0001"}],
    })
    assert r.status_code == 400


def test_enroll_missing_vehicles(server):
    r = post(server, "/enrollment/v2/enroll", {"product": "SKU-X"})
    assert r.status_code == 400


def test_enroll_increments_request_id(server):
    r1 = post(server, "/enrollment/v2/enroll", {"product": "SKU-X", "vehicles": [{"name": "A", "vin": "V001"}]})
    r2 = post(server, "/enrollment/v2/enroll", {"product": "SKU-X", "vehicles": [{"name": "B", "vin": "V002"}]})
    assert r2.json()["request_id"] == r1.json()["request_id"] + 1


# ---------------------------------------------------------------------------
# POST /enrollment/v2/unenroll — plural `products` (decision 005)
# ---------------------------------------------------------------------------

def test_unenroll_happy_path(server):
    r = post(server, "/enrollment/v2/unenroll", {
        "products": ["SKU-X"],
        "vins": ["1FTFW1E16JFD55835"],
    })
    assert r.status_code == 202
    assert isinstance(r.json()["request_id"], int)


def test_unenroll_requires_plural_products(server):
    """Unenroll must reject payloads using singular 'product' (wrong field)."""
    r = post(server, "/enrollment/v2/unenroll", {
        "product": "SKU-X",  # WRONG — should be plural 'products'
        "vins": ["V0001"],
    })
    assert r.status_code == 400


def test_enroll_unenroll_asymmetry_explicit(server):
    """
    Explicitly assert the singular/plural asymmetry:
      enroll  → 'product' (singular scalar)   → 202
      unenroll → 'products' (plural array) → 202
    Cross-shapes → 400.
    """
    enroll_ok = post(server, "/enrollment/v2/enroll",
                     {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "V1"}]})
    assert enroll_ok.status_code == 202, "enroll with singular 'product' should be 202"

    unenroll_ok = post(server, "/enrollment/v2/unenroll",
                       {"products": ["SKU-X"], "vins": ["V1"]})
    assert unenroll_ok.status_code == 202, "unenroll with plural 'products' should be 202"

    enroll_wrong = post(server, "/enrollment/v2/enroll",
                        {"products": ["SKU-X"], "vehicles": [{"name": "T", "vin": "V1"}]})
    assert enroll_wrong.status_code == 400, "enroll with plural 'products' should be 400"

    unenroll_wrong = post(server, "/enrollment/v2/unenroll",
                          {"product": "SKU-X", "vins": ["V1"]})
    assert unenroll_wrong.status_code == 400, "unenroll with singular 'product' should be 400"


# ---------------------------------------------------------------------------
# POST /enrollment/v2/status/latest — filter matrix (§ 3.3)
# ---------------------------------------------------------------------------

def test_status_latest_by_request_id(server):
    r = post(server, "/enrollment/v2/enroll", {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VINTEST01"}]})
    rid = r.json()["request_id"]

    s = post(server, "/enrollment/v2/status/latest", {"request_ids": [rid]})
    assert s.status_code == 200
    rows = s.json()
    assert len(rows) == 1
    assert rows[0]["vin"] == "VINTEST01"
    assert rows[0]["request_id"] == rid


def test_status_latest_by_vin(server):
    post(server, "/enrollment/v2/enroll", {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN_BYVIN"}]})
    s = post(server, "/enrollment/v2/status/latest", {"vins": ["VIN_BYVIN"]})
    assert s.status_code == 200
    rows = s.json()
    assert any(r["vin"] == "VIN_BYVIN" for r in rows)


def test_status_latest_by_request_type(server):
    enroll_r = post(server, "/enrollment/v2/enroll", {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN_E"}]})
    unenroll_r = post(server, "/enrollment/v2/unenroll", {"products": ["SKU-X"], "vins": ["VIN_U"]})

    enroll_results = post(server, "/enrollment/v2/status/latest", {"request_type": "ENROLL"}).json()
    unenroll_results = post(server, "/enrollment/v2/status/latest", {"request_type": "UN_ENROLL"}).json()

    enroll_rids = {r["request_id"] for r in enroll_results}
    unenroll_rids = {r["request_id"] for r in unenroll_results}

    assert enroll_r.json()["request_id"] in enroll_rids
    assert unenroll_r.json()["request_id"] in unenroll_rids
    # No cross-contamination
    assert unenroll_r.json()["request_id"] not in enroll_rids


def test_status_latest_default_fcs_code_pending(server):
    r = post(server, "/enrollment/v2/enroll", {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN_PEND"}]})
    rid = r.json()["request_id"]
    s = post(server, "/enrollment/v2/status/latest", {"request_ids": [rid]}).json()
    assert s[0]["fcs_code"] == "TC0"  # default pending


def test_status_latest_empty_for_unknown_request_id(server):
    s = post(server, "/enrollment/v2/status/latest", {"request_ids": [99999]})
    assert s.json() == []


# ---------------------------------------------------------------------------
# POST /enrollment/v2/liteCheck
# ---------------------------------------------------------------------------

def test_lite_check_happy_path(server):
    vins = [f"VIN{i:04d}" for i in range(5)]
    r = post(server, "/enrollment/v2/liteCheck", {"productSku": ["SKU-X"], "vin": vins})
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 5
    assert all(d["isCapable"] for d in data)
    assert all("pdSkus" in d for d in data)


def test_lite_check_max_10_vins(server):
    vins = [f"VIN{i:04d}" for i in range(11)]
    r = post(server, "/enrollment/v2/liteCheck", {"productSku": ["SKU-X"], "vin": vins})
    assert r.status_code == 400


def test_lite_check_exactly_10_vins(server):
    vins = [f"VIN{i:04d}" for i in range(10)]
    r = post(server, "/enrollment/v2/liteCheck", {"productSku": ["SKU-X"], "vin": vins})
    assert r.status_code == 200
    assert len(r.json()["data"]) == 10


# ---------------------------------------------------------------------------
# POST /selfserve/v1/vehicleData
# ---------------------------------------------------------------------------

def test_vehicle_data_returns_model_info(server):
    vins = ["VIN001", "VIN002"]
    r = post(server, "/selfserve/v1/vehicleData", {"vins": vins, "categories": ["modelInfo"]})
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 2
    assert all("make" in d and "model" in d and "year" in d for d in data)


def test_vehicle_data_max_5000_vins(server):
    vins = [f"V{i:05d}" for i in range(5001)]
    r = post(server, "/selfserve/v1/vehicleData", {"vins": vins})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# POST /selfserve/v1/vehicleState
# ---------------------------------------------------------------------------

def test_vehicle_state_returns_action_required(server):
    vins = ["VIN001"]
    r = post(server, "/selfserve/v1/vehicleState", {"vins": vins})
    assert r.status_code == 200
    data = r.json()["data"]
    assert len(data) == 1
    assert "actionRequired" in data[0]
    assert "actionCategory" in data[0]


def test_vehicle_state_max_5000_vins(server):
    vins = [f"V{i:05d}" for i in range(5001)]
    r = post(server, "/selfserve/v1/vehicleState", {"vins": vins})
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# Failure injection (for I3, I7) — X-Mock-Fcs-Code header
# ---------------------------------------------------------------------------

def test_failure_injection_via_header_429(server):
    """Injecting 429 via header returns 429 directly from enroll."""
    r = post(server, "/enrollment/v2/enroll",
             {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN429"}]},
             headers={"X-Mock-Fcs-Code": "429"})
    assert r.status_code == 429


def test_failure_injection_fcs_code_in_status(server):
    """Injecting fcs_code 9999 via header: enroll returns 202 but status shows TC9999."""
    r = post(server, "/enrollment/v2/enroll",
             {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN9999"}]},
             headers={"X-Mock-Fcs-Code": "9999"})
    assert r.status_code == 202
    rid = r.json()["request_id"]

    s = post(server, "/enrollment/v2/status/latest", {"request_ids": [rid]}).json()
    assert s[0]["fcs_code"] == "TC9999"
    assert s[0]["status"] == "FAILED"


def test_failure_injection_8030(server):
    r = post(server, "/enrollment/v2/enroll",
             {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN8030"}]},
             headers={"X-Mock-Fcs-Code": "8030"})
    assert r.status_code == 202
    rid = r.json()["request_id"]

    s = post(server, "/enrollment/v2/status/latest", {"request_ids": [rid]}).json()
    assert s[0]["fcs_code"] == "TC8030"
    assert s[0]["status"] == "FAILED"


def test_failure_injection_8040(server):
    r = post(server, "/enrollment/v2/enroll",
             {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN8040"}]},
             headers={"X-Mock-Fcs-Code": "8040"})
    assert r.status_code == 202
    rid = r.json()["request_id"]

    s = post(server, "/enrollment/v2/status/latest", {"request_ids": [rid]}).json()
    assert s[0]["fcs_code"] == "TC8040"
    assert s[0]["status"] == "FAILED"


def test_failure_injection_via_reset_body(server):
    """Seeding inject_fcs_code via /reset body: next enroll uses it."""
    post(server, "/reset", {"inject_fcs_code": 8020})
    r = post(server, "/enrollment/v2/enroll",
             {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN8020"}]})
    assert r.status_code == 202
    rid = r.json()["request_id"]

    s = post(server, "/enrollment/v2/status/latest", {"request_ids": [rid]}).json()
    assert s[0]["fcs_code"] == "TC8020"


# ---------------------------------------------------------------------------
# Completion smoke — fcs_code 3 (COMPLETED) includes activation date
# ---------------------------------------------------------------------------

def test_completed_status_includes_activation_date(server):
    r = post(server, "/enrollment/v2/enroll",
             {"product": "SKU-X", "vehicles": [{"name": "T", "vin": "VIN_OK"}]},
             headers={"X-Mock-Fcs-Code": "3"})
    assert r.status_code == 202
    rid = r.json()["request_id"]

    s = post(server, "/enrollment/v2/status/latest", {"request_ids": [rid]}).json()
    assert s[0]["status"] == "COMPLETED"
    assert "subscription_service_activation_date" in s[0]
