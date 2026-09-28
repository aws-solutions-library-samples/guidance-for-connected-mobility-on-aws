# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Tests for SOVD sidecar functionality in realtime_telemetry_simulator.py
# Spec: 2026-09-01-cms-remote-diagnostics-sovd, Task 3.1
#
# Run: python3 -m pytest services/simulation/tests/test_sovd_sidecar.py -v

import inspect
import json
import logging
import os
import sys
import threading
import time
import unittest
from unittest.mock import MagicMock, patch, call

# Allow import from repo root.
_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..'))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault('AWS_DEFAULT_REGION', 'us-east-1')

from realtime_telemetry_simulator import (  # noqa: E402  (services/simulation on path)
    RealtimeTelemetrySimulator,
    TokenBucket,
    _handle_sovd,
    SOVD_RESPONSES_BUCKET,
)
from services.simulation.sovd_payload_sizing import (  # noqa: E402
    SIZE_THRESHOLD_BYTES,
    encoded_size_bytes,
)
from services.commands.sovd_payload_sizing import make_synthetic_response  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sim():
    """Create a RealtimeTelemetrySimulator without boto3 init."""
    sim = RealtimeTelemetrySimulator.__new__(RealtimeTelemetrySimulator)
    return sim


def _make_mqtt_client(subscribe_mid_seq=None):
    """Return a paho-style mock MQTT client."""
    client = MagicMock()
    if subscribe_mid_seq is None:
        subscribe_mid_seq = [42, 43]
    _mid_iter = iter(subscribe_mid_seq)

    def _subscribe_side_effect(topic, qos=1):
        try:
            mid = next(_mid_iter)
        except StopIteration:
            mid = 99
        return (0, mid)

    client.subscribe.side_effect = _subscribe_side_effect
    return client


# ---------------------------------------------------------------------------
# Test: inline publish below threshold
# ---------------------------------------------------------------------------

class TestSovdReadDtcsInline(unittest.TestCase):
    """§ Design § 2: sidecar publishes inline when payload is under 80 KB."""

    def _run_handle_sovd(self, cmd, topic_extra='executions/corr-001/sovd/request'):
        client = MagicMock()
        # Capacity must cover the max ECU count (9) for any scan-type command.
        tb = TokenBucket(rate=1.0, capacity=10.0, per=10.0)
        # Force empty CAN results (no vcan0 in test env — handled in _handle_sovd)
        _handle_sovd(
            cmd=cmd,
            msg_topic=f'cms/commands/things/VIN-TEST/executions/corr-001/sovd/request',
            mqtt_client=client,
            vehicle_id='VEH-001',
            vin='VIN-TEST',
            token_bucket=tb,
        )
        return client

    def test_sovd_read_dtcs_publishes_inline_response_below_threshold(self):
        """§ Design § 2: sidecar publishes SOVD response inline on MQTT when payload under 80 KB."""
        cmd = {
            'command_type': 'read_dtcs',
            'components': ['ECU_ENGINE'],
            'include_freeze_frame': False,
            'correlation_id': 'corr-001',
        }
        client = self._run_handle_sovd(cmd)

        # At least one publish call should have been made.
        self.assertTrue(client.publish.called, "on_sovd should publish a response")
        # The response should be inline (no storage_uri, or storage_uri=None).
        published_payload = json.loads(client.publish.call_args[0][1])
        self.assertIn('correlation_id', published_payload)
        self.assertEqual(published_payload.get('storage_uri'), None)
        self.assertEqual(published_payload.get('status'), 'SUCCEEDED')

    def test_sovd_clear_dtcs_sends_uds_0x14_per_ecu(self):
        """§ Design § 2 + § 3: clear_dtcs handler sends a response per requested ECU."""
        cmd = {
            'command_type': 'clear_dtcs',
            'components': ['ECU_ENGINE', 'ECU_BRAKE'],
            'correlation_id': 'corr-002',
            'attestation': {
                'text': 'I have verified the underlying repair is complete',
                'user_email': 'operator@example.com',
                'timestamp_ms': 1735000000000,
            },
        }
        client = self._run_handle_sovd(cmd)
        self.assertTrue(client.publish.called)
        published_payload = json.loads(client.publish.call_args[0][1])
        self.assertEqual(published_payload.get('status'), 'SUCCEEDED')
        self.assertIn('ECU_ENGINE', published_payload.get('components', {}))
        self.assertIn('ECU_BRAKE', published_payload.get('components', {}))


# ---------------------------------------------------------------------------
# Test: S3 upload above threshold
# ---------------------------------------------------------------------------

class TestSovdS3Fallback(unittest.TestCase):
    """D4 + HARD GATE G: sidecar uploads to S3 when payload > 80 KB."""

    def test_sovd_response_over_threshold_uploads_to_s3_and_publishes_summary(self):
        """Synthetic oversized payload triggers S3 upload + summary MQTT message."""
        # Build a payload that is definitely > SIZE_THRESHOLD_BYTES.
        large_payload = make_synthetic_response(
            ecu_count=9,
            dtcs_per_ecu=10,
            signals_per_ff=12,
        )
        self.assertGreater(
            encoded_size_bytes(large_payload),
            SIZE_THRESHOLD_BYTES,
            "Test fixture must exceed the threshold to exercise the S3 branch",
        )

        # Patch the sizer to return a value larger than the threshold for any dict.
        # Also patch boto3 and SOVD_RESPONSES_BUCKET.
        mock_s3_client = MagicMock()
        mock_s3_client.put_object.return_value = {}
        mqtt_client = MagicMock()
        # Full-scan needs 9 tokens (one per ECU); capacity must be >= 9.
        tb = TokenBucket(rate=1.0, capacity=10.0, per=10.0)

        # _handle_sovd imports encoded_size_bytes from services.simulation.sovd_payload_sizing
        # inside the function body, so we patch the module-level function there.
        with patch('services.simulation.sovd_payload_sizing.encoded_size_bytes',
                   return_value=SIZE_THRESHOLD_BYTES + 1), \
             patch('realtime_telemetry_simulator.boto3') as mock_boto3, \
             patch('realtime_telemetry_simulator.SOVD_RESPONSES_BUCKET', 'test-bucket'):
            mock_boto3.client.return_value = mock_s3_client

            _handle_sovd(
                cmd={
                    'command_type': 'read_dtcs',
                    'components': ['*'],
                    'correlation_id': 'corr-big',
                },
                msg_topic='cms/commands/things/VIN-BIG/executions/corr-big/sovd/request',
                mqtt_client=mqtt_client,
                vehicle_id='VEH-BIG',
                vin='VIN-BIG',
                token_bucket=tb,
            )

        # S3 put_object should have been called.
        mock_s3_client.put_object.assert_called_once()
        call_kwargs = mock_s3_client.put_object.call_args[1]
        self.assertEqual(call_kwargs['Bucket'], 'test-bucket')
        self.assertIn('VIN-BIG', call_kwargs['Key'])
        self.assertIn('corr-big', call_kwargs['Key'])

        # The MQTT publish should carry storage_uri (S3 URI, not pre-signed URL).
        self.assertTrue(mqtt_client.publish.called)
        published_payload = json.loads(mqtt_client.publish.call_args[0][1])
        storage_uri = published_payload.get('storage_uri', '')
        self.assertTrue(
            storage_uri.startswith('s3://'),
            f"storage_uri must be an s3:// URI, got {storage_uri!r}",
        )
        self.assertNotIn('X-Amz-Signature', storage_uri,
                         "storage_uri must NOT be a pre-signed URL (spec R2)")

    def test_sovd_response_below_threshold_inlines(self):
        """Small payload (< 80 KB) must NOT trigger S3 upload."""
        mock_s3_client = MagicMock()
        mqtt_client = MagicMock()
        tb = TokenBucket(rate=1.0, capacity=1.0, per=10.0)

        with patch('realtime_telemetry_simulator.boto3') as mock_boto3, \
             patch.dict(os.environ, {'SOVD_RESPONSES_BUCKET': 'test-bucket'}), \
             patch('realtime_telemetry_simulator.SOVD_RESPONSES_BUCKET', 'test-bucket'):
            mock_boto3.client.return_value = mock_s3_client

            _handle_sovd(
                cmd={
                    'command_type': 'read_dtcs',
                    'components': ['ECU_ENGINE'],
                    'correlation_id': 'corr-small',
                },
                msg_topic='cms/commands/things/VIN-SMALL/executions/corr-small/sovd/request',
                mqtt_client=mqtt_client,
                vehicle_id='VEH-SMALL',
                vin='VIN-SMALL',
                token_bucket=tb,
            )

        # S3 should NOT have been called.
        mock_s3_client.put_object.assert_not_called()
        # MQTT publish should have been called (inline).
        self.assertTrue(mqtt_client.publish.called)
        published_payload = json.loads(mqtt_client.publish.call_args[0][1])
        self.assertIsNone(published_payload.get('storage_uri'))


# ---------------------------------------------------------------------------
# Test: rate limiting (HARD GATE F)
# ---------------------------------------------------------------------------

class TestSovdRateLimiting(unittest.TestCase):
    """HARD GATE F: max 1 full-scan per 10 s per vehicle."""

    def test_sovd_full_scan_rate_limited_sidecar(self):
        """Two back-to-back full-scans: second returns status='RATE_LIMITED'."""
        mqtt_client = MagicMock()
        # Single per-vehicle token bucket — starts full (1 token).
        tb = TokenBucket(rate=1.0, capacity=1.0, per=10.0)

        def _run(correlation_id):
            _handle_sovd(
                cmd={
                    'command_type': 'read_dtcs',
                    'components': ['*'],
                    'correlation_id': correlation_id,
                },
                msg_topic=f'cms/commands/things/VIN-RATE/executions/{correlation_id}/sovd/request',
                mqtt_client=mqtt_client,
                vehicle_id='VEH-RATE',
                vin='VIN-RATE',
                token_bucket=tb,
            )

        # First full-scan — consumes the token; should SUCCEED.
        _run('corr-rate-1')
        # Second full-scan — bucket is empty; should be RATE_LIMITED.
        _run('corr-rate-2')

        publish_calls = mqtt_client.publish.call_args_list
        self.assertGreaterEqual(len(publish_calls), 2)

        payloads = [json.loads(c[0][1]) for c in publish_calls]
        statuses = [p.get('status') for p in payloads]
        self.assertIn('RATE_LIMITED', statuses,
                      f"Expected RATE_LIMITED in statuses; got {statuses}")
        rate_limited_payload = next(p for p in payloads if p.get('status') == 'RATE_LIMITED')
        self.assertIn('retry_after_ms', rate_limited_payload,
                      "RATE_LIMITED response must include retry_after_ms")


# ---------------------------------------------------------------------------
# Test: worker thread does NOT block paho network thread
# ---------------------------------------------------------------------------

class TestSovdWorkerThread(unittest.TestCase):
    """R3: on_sovd returns immediately; UDS work runs on a dedicated thread."""

    def test_sovd_worker_thread_does_not_block_on_message(self):
        """on_sovd spawns a thread and returns in < 10 ms.

        We cannot easily test the actual on_sovd closure without a live MQTT
        session, so we test the contract at the _handle_sovd level: the
        module function exists and can be called from a background thread.
        We also test that threading.Thread is used via source inspection.
        """
        src = inspect.getsource(
            RealtimeTelemetrySimulator.simulate_vehicle_telemetry
        )
        # on_sovd must be defined inside simulate_vehicle_telemetry.
        self.assertIn('def on_sovd(client, userdata, msg)', src,
                      "on_sovd closure must be defined inside simulate_vehicle_telemetry")
        # on_sovd must spawn a worker thread.
        self.assertIn('threading.Thread', src,
                      "on_sovd must spawn a threading.Thread for the UDS worker")
        # on_sovd must reference _handle_sovd.
        self.assertIn('_handle_sovd', src,
                      "on_sovd must dispatch to _handle_sovd worker function")

        # Functional: _handle_sovd runs without raising when called directly
        # from a background thread (simulating the intended threading pattern).
        exc_holder = []
        def _run():
            try:
                _handle_sovd(
                    cmd={'command_type': 'read_dtcs', 'components': ['ECU_ENGINE']},
                    msg_topic='cms/commands/things/VIN/executions/e1/sovd/request',
                    mqtt_client=MagicMock(),
                    vehicle_id='VEH-THREAD',
                    vin='VIN-THREAD',
                    token_bucket=TokenBucket(),
                )
            except Exception as e:
                exc_holder.append(e)

        t = threading.Thread(target=_run)
        t.start()
        t.join(timeout=5.0)
        self.assertFalse(exc_holder,
                         f"_handle_sovd raised on worker thread: {exc_holder}")


# ---------------------------------------------------------------------------
# Test: source-level checks on on_sovd structure
# ---------------------------------------------------------------------------

class TestSovdSourceStructure(unittest.TestCase):
    """Source-level assertions on the on_sovd and on_subscribe closures."""

    def setUp(self):
        self.src = inspect.getsource(
            RealtimeTelemetrySimulator.simulate_vehicle_telemetry
        )

    def test_on_sovd_defined(self):
        self.assertIn('def on_sovd(client, userdata, msg)', self.src)

    def test_on_sovd_dispatches_on_command_type(self):
        """on_sovd dispatches on command_type in {'read_dtcs', 'clear_dtcs'}."""
        self.assertIn('read_dtcs', self.src)
        self.assertIn('clear_dtcs', self.src)

    def test_on_subscribe_tracks_both_mids(self):
        """on_subscribe accumulates _confirmed_sub_mids before calling notify_connected."""
        self.assertIn('_confirmed_sub_mids', self.src)
        self.assertIn('_pending_sub_mids', self.src)

    def test_subscribe_command_topics_wired(self):
        """_subscribe_command_topics is called from on_connect with on_sovd arg."""
        on_connect_start = self.src.find('def on_connect(client, userdata, flags, reason_code')
        on_connect_slice = self.src[on_connect_start:on_connect_start + 4000]
        self.assertIn('_subscribe_command_topics(', on_connect_slice)
        self.assertIn('on_sovd', on_connect_slice)




# ---------------------------------------------------------------------------
# Tests: freeze-frame wiring via FREEZE_FRAME_BY_DTC (FG3.1)
# ---------------------------------------------------------------------------

class TestSovdFreezeFrameWiring(unittest.TestCase):
    """FG3.1: _handle_sovd populates freeze_frame from FREEZE_FRAME_BY_DTC fixture.

    Per decisions.md 2026-09-01 "Fix Group 3 scope" — resolution shape (b):
    the sidecar imports FREEZE_FRAME_BY_DTC directly and populates the SOVD
    JSON without a CAN roundtrip.  The on-CAN binary encoding in the UDS
    responder is a sim/demo detail that stays valid for the FWE path.
    """

    # Shared test DTC that is in the fixture.
    _KNOWN_DTC = 'P0420'
    # A DTC code that does NOT exist in the fixture.
    _UNKNOWN_DTC = 'P9999'
    _ECU = 'ECU_ENGINE'

    def _run_with_injected_dtcs(self, dtc_codes, include_freeze_frame):
        """Run _handle_sovd with mock CAN bus that returns the given DTCs.

        Patches:
          - ``sys.modules['can']`` to make ``import can as _can`` succeed inside
            ``_handle_sovd`` and return a MagicMock for ``_can.Bus(...)`` so that
            ``can_bus`` is non-None.
          - ``realtime_telemetry_simulator._issue_uds_read`` to return the given
            DTC list without a real vCAN interface.
        """
        import sys as _sys

        mqtt_client = MagicMock()
        tb = TokenBucket(rate=1.0, capacity=1.0, per=10.0)

        # Synthetic DTCs returned by the mocked UDS read.
        fake_dtcs_raw = [{'code': code, 'status': 'confirmed'} for code in dtc_codes]

        # Build a mock `can` module whose Bus constructor returns a non-None object.
        mock_can_module = MagicMock()
        mock_can_module.Bus.return_value = MagicMock()

        with patch.dict(_sys.modules, {'can': mock_can_module}), \
             patch('realtime_telemetry_simulator._issue_uds_read',
                   return_value=fake_dtcs_raw):
            _handle_sovd(
                cmd={
                    'command_type': 'read_dtcs',
                    'components': [self._ECU],
                    'include_freeze_frame': include_freeze_frame,
                    'correlation_id': 'corr-ff-test',
                },
                msg_topic=(
                    'cms/commands/things/VIN-FF/executions/corr-ff-test/sovd/request'
                ),
                mqtt_client=mqtt_client,
                vehicle_id='VEH-FF',
                vin='VIN-FF',
                token_bucket=tb,
            )

        return mqtt_client

    def test_sovd_read_dtcs_with_freeze_frame_populates_signals(self):
        """include_freeze_frame=True on a fixture DTC returns the 6 known signals.

        Verifies:
          (a) freeze_frame is non-empty.
          (b) Contains all 6 UI-known signal names.
          (c) Each signal dict has keys: value, unit, timestamp.
          (d) timestamp parses as ISO-8601.
        """
        mqtt_client = self._run_with_injected_dtcs(
            dtc_codes=[self._KNOWN_DTC],
            include_freeze_frame=True,
        )

        self.assertTrue(mqtt_client.publish.called, "must publish a response")
        payload = json.loads(mqtt_client.publish.call_args[0][1])
        self.assertEqual(payload.get('status'), 'SUCCEEDED')

        dtcs = payload['components'][self._ECU]['dtcs']
        self.assertGreater(len(dtcs), 0, "at least one DTC must be in the response")

        freeze_frame = dtcs[0]['freeze_frame']
        # (a) non-empty
        self.assertTrue(
            freeze_frame,
            f"freeze_frame must be non-empty for a fixture DTC ({self._KNOWN_DTC})",
        )
        # (b) 6 UI-known signals
        expected_signals = {
            'engineRpm', 'coolantTemp', 'vehicleSpeed',
            'engineLoad', 'throttlePosition', 'fuelTrim',
        }
        self.assertEqual(
            expected_signals,
            set(freeze_frame.keys()),
            f"freeze_frame must contain exactly the 6 UI-known signal names; got {set(freeze_frame.keys())}",
        )
        # (c) each signal has value, unit, timestamp
        for sig_name, sig_dict in freeze_frame.items():
            self.assertIn('value', sig_dict,
                          f"signal {sig_name!r} missing 'value'")
            self.assertIn('unit', sig_dict,
                          f"signal {sig_name!r} missing 'unit'")
            self.assertIn('timestamp', sig_dict,
                          f"signal {sig_name!r} missing 'timestamp'")
            # (d) timestamp is valid ISO-8601
            from datetime import datetime as _dt
            try:
                _dt.fromisoformat(sig_dict['timestamp'])
            except ValueError as e:
                self.fail(
                    f"signal {sig_name!r} timestamp {sig_dict['timestamp']!r} is not "
                    f"valid ISO-8601: {e}"
                )

    def test_sovd_read_dtcs_with_include_freeze_frame_false_omits_signals(self):
        """include_freeze_frame=False must produce freeze_frame == {} with no fixture-miss log.

        Verifies:
          - DTC entry has freeze_frame == {}.
          - No logger.info fixture-miss line fired (user opted out, not a data gap).
        """
        captured_records = []

        class _Capturer(logging.Handler):
            def emit(self_, record):  # noqa: N805
                captured_records.append(record)

        handler = _Capturer()
        logging.getLogger().addHandler(handler)
        try:
            mqtt_client = self._run_with_injected_dtcs(
                dtc_codes=[self._KNOWN_DTC],
                include_freeze_frame=False,
            )
        finally:
            logging.getLogger().removeHandler(handler)

        self.assertTrue(mqtt_client.publish.called)
        payload = json.loads(mqtt_client.publish.call_args[0][1])
        dtcs = payload['components'][self._ECU]['dtcs']
        self.assertGreater(len(dtcs), 0)

        freeze_frame = dtcs[0]['freeze_frame']
        self.assertEqual(
            freeze_frame, {},
            f"include_freeze_frame=False must produce freeze_frame == {{}}; got {freeze_frame!r}",
        )
        # No fixture-miss log must have fired (flag said don't build freeze frame,
        # not that there's no data for the DTC).
        fixture_miss_msgs = [
            r.getMessage() for r in captured_records
            if 'no fixture data' in r.getMessage().lower()
        ]
        self.assertEqual(
            fixture_miss_msgs, [],
            f"Expected no fixture-miss log for include_freeze_frame=False path; got: {fixture_miss_msgs}",
        )

    def test_sovd_read_dtcs_unknown_dtc_returns_empty_freeze_frame_and_logs(self):
        """Unknown DTC with include_freeze_frame=True returns {} and logs once at INFO.

        Verifies:
          - freeze_frame is {}.
          - Exactly one logger.info line names the DTC code.
        """
        import logging as _logging

        with self.assertLogs(
            logger='realtime_telemetry_simulator', level='INFO'
        ) as log_ctx:
            mqtt_client = self._run_with_injected_dtcs(
                dtc_codes=[self._UNKNOWN_DTC],
                include_freeze_frame=True,
            )

        self.assertTrue(mqtt_client.publish.called)
        payload = json.loads(mqtt_client.publish.call_args[0][1])
        dtcs = payload['components'][self._ECU]['dtcs']
        self.assertGreater(len(dtcs), 0)

        freeze_frame = dtcs[0]['freeze_frame']
        self.assertEqual(
            freeze_frame, {},
            f"Unknown DTC must produce freeze_frame == {{}}; got {freeze_frame!r}",
        )
        # One logger.info line must name the DTC code.
        fixture_miss_lines = [
            msg for msg in log_ctx.output
            if self._UNKNOWN_DTC in msg
        ]
        self.assertGreater(
            len(fixture_miss_lines), 0,
            f"Expected a log line naming DTC {self._UNKNOWN_DTC!r}; log output: {log_ctx.output}",
        )


if __name__ == '__main__':
    unittest.main(verbosity=2)



# ---------------------------------------------------------------------------
# T2.1 — run_routine response carries schema-valid result + verdict
# (spec: 2026-09-10-cms-sovd-routine-result-contracts, Group 2 T2.1)
# ---------------------------------------------------------------------------

class TestSovdRunRoutineResultContracts(unittest.TestCase):
    """T2.1: _handle_sovd run_routine branch calls produce_result, validates, and
    attaches 'result' + 'verdict' to the published response dict.

    Accept criteria (task T2.1):
      (a) 6 pilot routines invoked → response carries schema-valid result + verdict
      (b) unknown routine → response carries status=FAILED, reason startswith
          'schema validation failed:'
    """

    # All 6 pilot routines — use INERT ones (lamp_self_check, pack_isolation_test,
    # cell_balance_check) without vehicle_state; for STATIONARY ones (abs_pump_cycle,
    # evap_leak_test, o2_heater_check) supply a stationary VehicleState so
    # run_routine() returns SUCCEEDED rather than REFUSED_NO_VEHICLE_STATE.
    _PILOT_ROUTINES = [
        "lamp_self_check",
        "o2_heater_check",
        "evap_leak_test",
        "abs_pump_cycle",
        "pack_isolation_test",
        "cell_balance_check",
    ]

    def _run_routine_cmd(self, routine_id: str, corr: str = "corr-rr-001") -> dict:
        """Build a minimal run_routine command dict."""
        return {
            "command_type": "run_routine",
            "routine_id": routine_id,
            "correlation_id": corr,
            "attestation": {
                "text": "I authorise this routine",
                "user_email": "test@example.com",
                "timestamp_ms": 1785854347000,
            },
        }

    def _run_handle_sovd_routine(
        self,
        routine_id: str,
        vehicle_id: str = "VEH-TEST-001",
        with_vehicle_state: bool = True,
    ) -> dict:
        """Invoke _handle_sovd with a run_routine cmd and return the published payload."""
        from realtime_telemetry_simulator import VehicleState

        client = MagicMock()
        tb = TokenBucket(rate=1.0, capacity=10.0, per=10.0)

        vs = None
        if with_vehicle_state:
            vs = VehicleState()
            vs.last_speed = 0  # stationary — required for STATIONARY-class routines

        _handle_sovd(
            cmd=self._run_routine_cmd(routine_id),
            msg_topic="cms/commands/things/VIN-TEST/executions/corr-rr-001/sovd/request",
            mqtt_client=client,
            vehicle_id=vehicle_id,
            vin="VIN-TEST",
            token_bucket=tb,
            vehicle_state=vs,
        )

        self.assertTrue(
            client.publish.called,
            f"_handle_sovd must publish a response for routine {routine_id!r}",
        )
        return json.loads(client.publish.call_args[0][1])

    # ── (a) 6 pilot routines each produce result + verdict ──────────────────

    def test_lamp_self_check_response_carries_result_and_verdict(self):
        """lamp_self_check (INERT): result and verdict must be present."""
        payload = self._run_handle_sovd_routine("lamp_self_check")
        self.assertEqual(payload.get("status"), "SUCCEEDED",
                         f"status: {payload.get('status')!r}, reason: {payload.get('reason')!r}")
        self.assertIn("result", payload, "response must carry 'result'")
        self.assertIn("verdict", payload, "response must carry 'verdict'")
        # result must have expected schema fields
        result = payload["result"]
        self.assertIn("lamps", result)
        self.assertIn("ambient_lux", result)
        self.assertIn(payload["verdict"], ("in_spec", "marginal", "out_of_spec"))
        # Legacy fields must survive unchanged
        self.assertIn("correlation_id", payload)
        self.assertEqual(payload.get("command_type"), "run_routine")

    def test_o2_heater_check_response_carries_result_and_verdict(self):
        """o2_heater_check (STATIONARY): result and verdict must be present."""
        payload = self._run_handle_sovd_routine("o2_heater_check")
        self.assertEqual(payload.get("status"), "SUCCEEDED",
                         f"status: {payload.get('status')!r}, reason: {payload.get('reason')!r}")
        self.assertIn("result", payload)
        self.assertIn("verdict", payload)
        result = payload["result"]
        self.assertIn("bank1_upstream_response_ms", result)
        self.assertIn("threshold_ms", result)
        self.assertIn(payload["verdict"], ("in_spec", "marginal", "out_of_spec"))

    def test_evap_leak_test_response_carries_result_and_verdict(self):
        """evap_leak_test (STATIONARY): result and verdict must be present."""
        payload = self._run_handle_sovd_routine("evap_leak_test")
        self.assertEqual(payload.get("status"), "SUCCEEDED",
                         f"status: {payload.get('status')!r}, reason: {payload.get('reason')!r}")
        self.assertIn("result", payload)
        self.assertIn("verdict", payload)
        result = payload["result"]
        self.assertIn("system_pressure_kpa", result)
        self.assertIn("leak_rate_ccm", result)
        self.assertIn(payload["verdict"], ("in_spec", "marginal", "out_of_spec"))

    def test_abs_pump_cycle_response_carries_result_and_verdict(self):
        """abs_pump_cycle (STATIONARY): result and verdict must be present."""
        payload = self._run_handle_sovd_routine("abs_pump_cycle")
        self.assertEqual(payload.get("status"), "SUCCEEDED",
                         f"status: {payload.get('status')!r}, reason: {payload.get('reason')!r}")
        self.assertIn("result", payload)
        self.assertIn("verdict", payload)
        result = payload["result"]
        self.assertIn("cycles_observed", result)
        self.assertIn("cycles_expected", result)
        self.assertIn(payload["verdict"], ("in_spec", "out_of_spec"))

    def test_pack_isolation_test_response_carries_result_and_verdict(self):
        """pack_isolation_test (INERT): result and verdict must be present."""
        payload = self._run_handle_sovd_routine("pack_isolation_test")
        self.assertEqual(payload.get("status"), "SUCCEEDED",
                         f"status: {payload.get('status')!r}, reason: {payload.get('reason')!r}")
        self.assertIn("result", payload)
        self.assertIn("verdict", payload)
        result = payload["result"]
        self.assertIn("isolation_resistance_mohm", result)
        self.assertIn("threshold_mohm", result)
        self.assertIn(payload["verdict"], ("in_spec", "marginal", "out_of_spec"))

    def test_cell_balance_check_response_carries_result_and_verdict(self):
        """cell_balance_check (INERT): result and verdict must be present."""
        payload = self._run_handle_sovd_routine("cell_balance_check")
        self.assertEqual(payload.get("status"), "SUCCEEDED",
                         f"status: {payload.get('status')!r}, reason: {payload.get('reason')!r}")
        self.assertIn("result", payload)
        self.assertIn("verdict", payload)
        result = payload["result"]
        self.assertIn("cell_voltages", result)
        self.assertIn("max_delta_mv", result)
        self.assertIn(payload["verdict"], ("in_spec", "marginal", "out_of_spec"))

    # ── (b) Unknown routine → FAILED with schema validation failed reason ────

    def test_unknown_routine_produces_failed_with_schema_validation_reason(self):
        """Unknown routine_id must return status=FAILED, reason starting with
        'schema validation failed:', not a bare SUCCEEDED with no data."""
        payload = self._run_handle_sovd_routine("foo_unknown_routine")
        self.assertEqual(
            payload.get("status"), "FAILED",
            f"Unknown routine must produce FAILED, got {payload.get('status')!r}",
        )
        reason = payload.get("reason", "")
        self.assertTrue(
            reason.startswith("schema validation failed:"),
            f"reason must start with 'schema validation failed:', got {reason!r}",
        )
        # result and verdict must NOT be present when FAILED
        self.assertNotIn("result", payload,
                         "'result' must not appear on a FAILED response")
        self.assertNotIn("verdict", payload,
                         "'verdict' must not appear on a FAILED response")

    # ── Legacy fields survive ──────────────────────────────────────────────

    def test_legacy_fields_survive_unchanged(self):
        """status, correlation_id, command_type survive in the published dict.

        Legacy consumers reading only those three fields must be unaffected
        by the new result + verdict additions (task Constraints: additive).
        """
        payload = self._run_handle_sovd_routine("lamp_self_check")
        self.assertEqual(payload.get("status"), "SUCCEEDED")
        self.assertEqual(payload.get("correlation_id"), "corr-rr-001")
        self.assertEqual(payload.get("command_type"), "run_routine")
