"""
Mock OEM1 REST enrollment server for integration tests (T6.1).

Implements the 6 REST endpoints required by T6.2-T6.8 + /reset:
  POST /enrollment/v2/enroll
  POST /enrollment/v2/unenroll
  POST /enrollment/v2/status/latest
  POST /enrollment/v2/liteCheck
  POST /selfserve/v1/vehicleData
  POST /selfserve/v1/vehicleState
  POST /reset

Failure injection:
  Header X-Mock-Fcs-Code: <int>  — next enroll returns 202 but status/latest
                                    returns this fcs_code for that request_id
  Query param ?force_status=<int> — same effect (for curl / query-param callers)

In-memory state, call /reset between tests.
Listens on OEM1_MOCK_REST_PORT (default 8080).
"""

import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

# ---------------------------------------------------------------------------
# In-memory state (module-level; reset via /reset)
# ---------------------------------------------------------------------------
_lock = threading.Lock()
_state: dict = {}


def _reset_state() -> None:
    global _state
    with _lock:
        _state = {
            "next_request_id": 1,
            # request_id -> {"fcs_code": int, "vins": list, "request_type": str}
            "enrollments": {},
            # injected fcs_code for the NEXT enroll call (consumed once)
            "inject_fcs_code": None,
            # request_id -> int call count for status/latest
            "status_call_counts": {},
            # 429 after N enroll calls: None = disabled, int = enroll count threshold
            "enroll_429_after_n": None,
            # running count of enroll calls (for 429_after_n)
            "enroll_call_count": 0,
        }


_reset_state()


def _next_request_id() -> int:
    with _lock:
        rid = _state["next_request_id"]
        _state["next_request_id"] += 1
        return rid


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # silence default access log
        pass

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    def _inject_fcs(self, query_params: dict) -> int | None:
        """Return injected fcs_code from header or query param (consumed once)."""
        # Header takes precedence
        header_val = self.headers.get("X-Mock-Fcs-Code")
        if header_val is not None:
            try:
                return int(header_val)
            except ValueError:
                pass
        # Query param
        qv = query_params.get("force_status", [None])[0]
        if qv is not None:
            try:
                return int(qv)
            except ValueError:
                pass
        # Persistent inject (set earlier via /reset body or inject endpoint)
        with _lock:
            code = _state.get("inject_fcs_code")
        return code

    def _send_json(self, status: int, body) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")
        query = parse_qs(parsed.query)
        body = self._read_body()

        if path == "/reset":
            self._handle_reset(body)
        elif path == "/enrollment/v2/enroll":
            self._handle_enroll(body, query)
        elif path == "/enrollment/v2/unenroll":
            self._handle_unenroll(body, query)
        elif path == "/enrollment/v2/status/latest":
            self._handle_status_latest(body)
        elif path == "/enrollment/v2/liteCheck":
            self._handle_lite_check(body)
        elif path == "/selfserve/v1/vehicleData":
            self._handle_vehicle_data(body)
        elif path == "/selfserve/v1/vehicleState":
            self._handle_vehicle_state(body)
        else:
            self._send_json(404, {"error": f"unknown path {path}"})

    # ------------------------------------------------------------------
    # /reset
    # ------------------------------------------------------------------

    def _handle_reset(self, body: dict) -> None:
        _reset_state()
        with _lock:
            # Allow pre-seeding inject_fcs_code via reset body
            if "inject_fcs_code" in body:
                _state["inject_fcs_code"] = body["inject_fcs_code"]
            # Allow pre-seeding 429_after_n: return 429 on the Nth+ enroll call
            if "enroll_429_after_n" in body:
                _state["enroll_429_after_n"] = int(body["enroll_429_after_n"])
        self._send_json(200, {"status": "reset"})

    # ------------------------------------------------------------------
    # POST /enrollment/v2/enroll
    # Shape: {"product": "SKU-X", "vehicles": [{"name": "...", "vin": "..."}]}
    # ------------------------------------------------------------------

    def _handle_enroll(self, body: dict, query: dict) -> None:
        # Validate singular `product` key (AQ1)
        if "product" not in body:
            self._send_json(400, {"error": "missing 'product' field"})
            return
        if "vehicles" not in body or not isinstance(body["vehicles"], list):
            self._send_json(400, {"error": "missing or invalid 'vehicles'"})
            return

        fcs_inject = self._inject_fcs(query)
        rid = _next_request_id()
        vins = [v.get("vin", "") for v in body["vehicles"]]

        with _lock:
            _state["enroll_call_count"] += 1
            call_n = _state["enroll_call_count"]
            after_n = _state.get("enroll_429_after_n")

        # 429_after_n: return 429 on the Nth+ call (before creating enrollment state)
        if after_n is not None and call_n > after_n:
            self._send_json(
                429,
                {"error": "OEM1 hourly enroll quota exhausted; retry after next hour"},
            )
            return

        # per-call fcs injection (header or query param)
        if fcs_inject == 429:
            self._send_json(429, {"error": "OEM1 hourly enroll quota exhausted"})
            return

        with _lock:
            _state["enrollments"][rid] = {
                "fcs_code": fcs_inject,  # None = healthy (fcs_code 0 initially)
                "vins": vins,
                "request_type": "ENROLL",
                "sku": body["product"],
            }
            _state["status_call_counts"][rid] = 0
            # consume persistent inject
            _state["inject_fcs_code"] = None

        self._send_json(202, {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S.000"),
            "request_id": rid,
        })

    # ------------------------------------------------------------------
    # POST /enrollment/v2/unenroll
    # Shape: {"products": ["SKU-X"], "vins": [...]}
    # ------------------------------------------------------------------

    def _handle_unenroll(self, body: dict, query: dict) -> None:
        # Validate plural `products` key (decision 005)
        if "products" not in body or not isinstance(body["products"], list):
            self._send_json(400, {"error": "missing or invalid 'products' array"})
            return
        if "vins" not in body or not isinstance(body["vins"], list):
            self._send_json(400, {"error": "missing 'vins'"})
            return

        fcs_inject = self._inject_fcs(query)
        rid = _next_request_id()
        vins = body["vins"]

        with _lock:
            _state["enrollments"][rid] = {
                "fcs_code": fcs_inject,
                "vins": vins,
                "request_type": "UN_ENROLL",
                "sku": body["products"][0] if body["products"] else "",
            }
            _state["status_call_counts"][rid] = 0
            _state["inject_fcs_code"] = None

        if fcs_inject == 429:
            self._send_json(429, {"error": "OEM1 hourly unenroll quota exhausted"})
            return

        self._send_json(202, {
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S.000"),
            "request_id": rid,
        })

    # ------------------------------------------------------------------
    # POST /enrollment/v2/status/latest
    # Filter matrix: request_ids / vins / statuses / request_type (§ 3.3)
    # ------------------------------------------------------------------

    def _handle_status_latest(self, body: dict) -> None:
        request_ids = body.get("request_ids")
        vins_filter = set(body.get("vins") or [])
        status_filter = set(body.get("statuses") or [])
        type_filter = body.get("request_type")

        results = []
        with _lock:
            enrollments = dict(_state["enrollments"])
            for rid, info in enrollments.items():
                # Filter by request_ids if provided
                if request_ids is not None and rid not in request_ids:
                    continue
                # Filter by vins if provided
                if vins_filter and not vins_filter.intersection(info["vins"]):
                    continue
                # Filter by request_type if provided
                if type_filter and info["request_type"] != type_filter:
                    continue

                fcs_code = info["fcs_code"]
                # Default fcs_code 0 (pending) when none injected
                effective_code = fcs_code if fcs_code is not None else 0

                status, message = _fcs_to_status(effective_code, info["request_type"])

                # Filter by statuses if provided
                if status_filter and status not in status_filter:
                    continue

                _state["status_call_counts"][rid] = (
                    _state["status_call_counts"].get(rid, 0) + 1
                )

                for vin in info["vins"]:
                    row = {
                        "vin": vin,
                        "vehicleId": vin,
                        "request_id": rid,
                        "requestId": rid,
                        "request_type": info["request_type"],
                        "product_sku": info["sku"],
                        "status": status,
                        "fcs_code": f"TC{effective_code}",
                        "fcsCode": effective_code,
                        "message": message,
                        "statusMessage": message,
                        "last_updated": time.strftime("%Y-%m-%d %H:%M:%S.000"),
                    }
                    if effective_code == 3:
                        row["subscription_service_activation_date"] = (
                            time.strftime("%Y-%m-%d %H:%M:%S.000")
                        )
                    results.append(row)

        self._send_json(200, results)

    # ------------------------------------------------------------------
    # POST /enrollment/v2/liteCheck
    # Max 10 VINs; returns pdSkus on isCapable=true
    # ------------------------------------------------------------------

    def _handle_lite_check(self, body: dict) -> None:
        vins = body.get("vin", [])
        if len(vins) > 10:
            self._send_json(400, {"error": "max 10 VINs per liteCheck request"})
            return

        results = []
        for vin in vins:
            # All VINs capable by default; inject incapable via a global flag
            with _lock:
                incapable_vins = _state.get("incapable_vins", set())
            capable = vin not in incapable_vins
            row = {
                "vin": vin,
                "productSku": body.get("productSku", [""])[0],
                "isCapable": capable,
            }
            if capable:
                row["pdSkus"] = ["PD-00007"]
            else:
                row["reason"] = "Vehicle not capable of product"
            results.append(row)

        self._send_json(200, {"data": results})

    # ------------------------------------------------------------------
    # POST /selfserve/v1/vehicleData
    # Max 5000 VINs; returns modelInfo
    # ------------------------------------------------------------------

    def _handle_vehicle_data(self, body: dict) -> None:
        vins = body.get("vins", [])
        if len(vins) > 5000:
            self._send_json(400, {"error": "max 5000 VINs per vehicleData request"})
            return

        results = [
            {
                "vin": vin,
                "make": "Ford",
                "model": "F-150",
                "year": "2022",
                "fuelType": ["GASOLINE"],
                "engineType": "ICE",
            }
            for vin in vins
        ]
        self._send_json(200, {"data": results})

    # ------------------------------------------------------------------
    # POST /selfserve/v1/vehicleState
    # Max 5000 VINs; returns actionRequired/actionCategory
    # ------------------------------------------------------------------

    def _handle_vehicle_state(self, body: dict) -> None:
        vins = body.get("vins", [])
        if len(vins) > 5000:
            self._send_json(400, {"error": "max 5000 VINs per vehicleState request"})
            return

        results = [
            {
                "vin": vin,
                "actionRequired": False,
                "actionCategory": None,
                "message": "Vehicle ready",
            }
            for vin in vins
        ]
        self._send_json(200, {"data": results})


# ---------------------------------------------------------------------------
# fcs_code → OEM1 status string
# ---------------------------------------------------------------------------

_FCS_STATUS_MAP = {
    0: ("PENDING", "Request is being processed"),
    1: ("PENDING", "Request is being processed"),
    2: ("IN_PROGRESS", "Enrollment in progress"),
    3: ("COMPLETED", "Vehicle has been successfully enrolled"),
    5: ("PENDING", "Request submitted"),
    6: ("UN_ENROLL_IN_PROGRESS", "Unenrollment in progress"),
    7: ("COMPLETED", "Vehicle has been successfully unenrolled"),
    429: ("FAILED", "OEM1 hourly quota exceeded"),
    1001: ("IN_PROGRESS", "Vehicle requires engine start"),
    1002: ("FAILED", "Vehicle not eligible for SKU"),
    1003: ("IN_PROGRESS", "Vehicle lifecycle ineligible (10-day window)"),
    8010: ("FAILED", "Vehicle not capable of product"),
    8020: ("FAILED", "7-day key-on timeout"),
    8030: ("FAILED", "VIN not in OEM1 ecosystem"),
    8040: ("FAILED", "Capability check service unavailable"),
    9999: ("FAILED", "Please retry the request"),
}


def _fcs_to_status(fcs_code: int, request_type: str) -> tuple[str, str]:
    if fcs_code in _FCS_STATUS_MAP:
        status, msg = _FCS_STATUS_MAP[fcs_code]
        if request_type == "UN_ENROLL" and fcs_code == 7:
            return "UNENROLLED", msg
        return status, msg
    return "UNKNOWN", f"Unknown fcs_code {fcs_code}"


# ---------------------------------------------------------------------------
# Server helpers (used by tests to start/stop in-process)
# ---------------------------------------------------------------------------

def make_server(port: int = 0) -> HTTPServer:
    """Create (but don't start) an HTTPServer. port=0 → OS picks free port."""
    server = HTTPServer(("127.0.0.1", port), _Handler)
    return server


def start_server_thread(port: int = 0) -> tuple[HTTPServer, int]:
    """Start server in a daemon thread; return (server, actual_port)."""
    server = make_server(port)
    actual_port = server.server_address[1]
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, actual_port


# ---------------------------------------------------------------------------
# CLI entry-point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    port = int(os.environ.get("OEM1_MOCK_REST_PORT", 8080))
    server, actual_port = start_server_thread(port)
    print(f"OEM1 mock REST server listening on http://127.0.0.1:{actual_port}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
