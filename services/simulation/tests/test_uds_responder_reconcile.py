"""Unit tests for the UDS responder reconciler seam (Group 2).

Contains two task groups:

1. **Responder reconciler contract** (RED PHASE — will FAIL until Group 3
   implements ``ensure_uds_responder``).

   These tests pin ``ensure_uds_responder(desired_map)`` in
   ``realtime_telemetry_simulator`` and will fail with:

       ImportError: cannot import name 'ensure_uds_responder' from
       'realtime_telemetry_simulator' (…/realtime_telemetry_simulator.py)

   That is the *correct* failure.  A failure on import errors, syntax errors,
   or missing fixtures is NOT acceptable.

2. **Single-spawn-site and no-override structural guards (AST)** (GREEN
   IMMEDIATELY — these assert invariants the CURRENT code already holds).

   These AST tests pass RIGHT NOW and are intended to STAY green.  A green
   result here is NOT a sign of a broken test — it is the expected outcome
   because the invariants are already enforced by the current implementation.
   They will catch any future regression that violates these invariants.

============================================================
Import guard (prior art: test_presence_loop.py:62-77)
============================================================

``ensure_uds_responder`` does not exist yet, so the import is guarded.
Collection succeeds; each reconciler test calls ``_require_reconciler()``
which re-raises the original ImportError as a per-test FAIL.

============================================================
Run
============================================================

    cd services/simulation
    python3 -m pytest tests/test_uds_responder_reconcile.py -q

    # Only the structural tests:
    python3 -m pytest tests/test_uds_responder_reconcile.py -q -k structural
"""

import ast
import os
import sys
import unittest
from unittest.mock import MagicMock, patch, call

# ---------------------------------------------------------------------------
# sys.path: simulation package must be importable
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_SIM_DIR = os.path.dirname(_HERE)
if _SIM_DIR not in sys.path:
    sys.path.insert(0, _SIM_DIR)

# ---------------------------------------------------------------------------
# Guarded import of the NOT-YET-EXISTING reconciler seam.
# Collection MUST succeed even when ensure_uds_responder is absent.
# Each reconciler test calls _require_reconciler() which raises the original
# ImportError so pytest reports a per-test FAIL rather than a collection ERROR.
# ---------------------------------------------------------------------------

try:
    from realtime_telemetry_simulator import (  # noqa: E402
        ensure_uds_responder,
        _reset_uds_responder_state,
    )
    _RECONCILER_IMPORT_ERROR: "ImportError | None" = None
except ImportError as _e:
    ensure_uds_responder = None  # type: ignore[assignment]
    _reset_uds_responder_state = None  # type: ignore[assignment]
    _RECONCILER_IMPORT_ERROR = _e


def _require_reconciler():
    """Raise the original ImportError inside a test body so pytest counts it
    as a per-test failure rather than a collection error."""
    if _RECONCILER_IMPORT_ERROR is not None:
        raise _RECONCILER_IMPORT_ERROR


# ---------------------------------------------------------------------------
# Path helpers (used by structural tests)
# ---------------------------------------------------------------------------

_RTS_PATH = os.path.join(_SIM_DIR, "realtime_telemetry_simulator.py")
_LAMBDA_DIR = os.path.join(_SIM_DIR, "lambda")
_LAMBDA_PATH = os.path.join(_LAMBDA_DIR, "simulation_lambda.py")


# ===========================================================================
# Task 1 — Responder reconciler contract (RED PHASE)
# ===========================================================================

class TestReconcilerEmptyOrNone(unittest.TestCase):
    """(a) Empty/None desired with no current responder → no spawn."""

    def setUp(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def tearDown(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def test_none_desired_no_responder_no_spawn(self):
        """Calling ensure_uds_responder(None) when nothing is running must not spawn."""
        _require_reconciler()
        with patch("subprocess.Popen") as mock_popen:
            ensure_uds_responder(None)
            mock_popen.assert_not_called()

    def test_empty_dict_desired_no_responder_no_spawn(self):
        """Calling ensure_uds_responder({}) when nothing is running must not spawn."""
        _require_reconciler()
        with patch("subprocess.Popen") as mock_popen:
            ensure_uds_responder({})
            mock_popen.assert_not_called()


class TestReconcilerNonEmptySpawnsOnce(unittest.TestCase):
    """(b) Non-empty desired → exactly one spawn, child receives the serialised map."""

    def setUp(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def tearDown(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def _desired_map(self):
        return {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}}

    def test_non_empty_desired_spawns_exactly_one_process(self):
        """ensure_uds_responder with a non-empty map must call Popen exactly once."""
        _require_reconciler()
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None  # running
            mock_popen.return_value = mock_proc

            ensure_uds_responder(self._desired_map())

            self.assertEqual(
                mock_popen.call_count, 1,
                f"Expected exactly 1 Popen call; got {mock_popen.call_count}",
            )

    def test_non_empty_desired_child_receives_serialised_map(self):
        """The spawned child must receive UDS_DTC_MAP in its environment."""
        _require_reconciler()
        import json

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            desired = self._desired_map()
            ensure_uds_responder(desired)

            self.assertEqual(mock_popen.call_count, 1)
            _, kwargs = mock_popen.call_args
            env = kwargs.get("env", {})
            self.assertIn(
                "UDS_DTC_MAP", env,
                "The child process environment must contain UDS_DTC_MAP",
            )
            # The value must be valid JSON that round-trips to the desired map.
            try:
                parsed = json.loads(env["UDS_DTC_MAP"])
            except (json.JSONDecodeError, TypeError) as exc:
                self.fail(f"UDS_DTC_MAP is not valid JSON: {exc}")
            self.assertEqual(
                parsed, desired,
                "UDS_DTC_MAP JSON must round-trip to the desired map",
            )


class TestReconcilerIdempotent(unittest.TestCase):
    """(c) Called twice with an unchanged map → still exactly one process, zero extra spawns."""

    def setUp(self):
        # Reset module-level UDS state so each test starts with no running proc.
        # Without this reset, the mock proc left by the first call's Popen would
        # survive into the second call (or into a sibling test), making the
        # idempotency check skip the spawn even against real production logic.
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def tearDown(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def _desired_map(self):
        return {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}}

    def test_unchanged_map_second_call_no_extra_spawn(self):
        """Two calls with the same map must produce at most one Popen, not two."""
        _require_reconciler()
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None  # still running after first call
            mock_popen.return_value = mock_proc

            desired = self._desired_map()
            ensure_uds_responder(desired)
            ensure_uds_responder(desired)

            self.assertEqual(
                mock_popen.call_count, 1,
                "Two calls with an unchanged map must result in exactly one Popen; "
                f"got {mock_popen.call_count}",
            )


class TestReconcilerChangedMap(unittest.TestCase):
    """(d) Called with a changed map → previous process terminated, new one spawned."""

    def setUp(self):
        # Reset module-level UDS state so the test exercises real production
        # reconciler logic with a clean slate.
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def tearDown(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def test_changed_map_kills_old_and_spawns_new(self):
        """Changing the map must terminate the old process and spawn a fresh one."""
        _require_reconciler()
        with patch("subprocess.Popen") as mock_popen:
            # First process — still running
            proc1 = MagicMock()
            proc1.poll.return_value = None
            # Second process
            proc2 = MagicMock()
            proc2.poll.return_value = None
            mock_popen.side_effect = [proc1, proc2]

            map_v1 = {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}}
            map_v2 = {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0171"]}}

            ensure_uds_responder(map_v1)
            ensure_uds_responder(map_v2)

            # Old process must have been signalled (SIGTERM or kill)
            self.assertTrue(
                proc1.send_signal.called or proc1.terminate.called or proc1.kill.called,
                "The old process must be terminated when the map changes",
            )
            # A second process must have been spawned
            self.assertEqual(
                mock_popen.call_count, 2,
                f"Changed map must result in a second Popen; got {mock_popen.call_count}",
            )


class TestReconcilerClearWhileRunning(unittest.TestCase):
    """(e) Called with empty desired while a responder runs → terminated, not respawned."""

    def setUp(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def tearDown(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def test_empty_desired_terminates_running_responder(self):
        """ensure_uds_responder({}) while a responder runs must terminate it."""
        _require_reconciler()
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None  # running
            mock_popen.return_value = proc

            desired = {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}}
            ensure_uds_responder(desired)
            ensure_uds_responder({})

            self.assertTrue(
                proc.send_signal.called or proc.terminate.called or proc.kill.called,
                "Clearing the map must terminate the running responder",
            )
            # Must NOT have spawned a second process
            self.assertEqual(
                mock_popen.call_count, 1,
                "Clearing the map must not spawn a new responder; "
                f"Popen was called {mock_popen.call_count} time(s)",
            )

    def test_none_desired_terminates_running_responder(self):
        """ensure_uds_responder(None) while a responder runs must terminate it."""
        _require_reconciler()
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.poll.return_value = None
            mock_popen.return_value = proc

            desired = {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}}
            ensure_uds_responder(desired)
            ensure_uds_responder(None)

            self.assertTrue(
                proc.send_signal.called or proc.terminate.called or proc.kill.called,
                "None desired must terminate the running responder",
            )
            self.assertEqual(mock_popen.call_count, 1)


class TestReconcilerSpawnFailure(unittest.TestCase):
    """(f) A spawn failure is logged and does not raise."""

    def setUp(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def tearDown(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def test_spawn_failure_does_not_raise(self):
        """If Popen raises, ensure_uds_responder must catch it and not propagate."""
        _require_reconciler()
        with patch("subprocess.Popen", side_effect=OSError("no such file")):
            # Must not raise
            try:
                desired = {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}}
                ensure_uds_responder(desired)
            except Exception as exc:
                self.fail(
                    f"ensure_uds_responder must not raise on spawn failure; got {exc!r}"
                )


# ===========================================================================
# Task 2 — Structural guards (AST) — GREEN IMMEDIATELY
# ===========================================================================

class TestSingleSpawnSiteStructural(unittest.TestCase):
    """(a) realtime_telemetry_simulator.py contains exactly ONE subprocess.Popen
    call site, and that site resides within the responder-spawn function.

    This test asserts an invariant the CURRENT code already holds:
    ``_spawn_uds_responder`` (the precursor to ``ensure_uds_responder``) is the
    only function in the file that calls ``subprocess.Popen``.  A green result is
    NOT a sign of a broken test — the invariant is already true.  This test guards
    against any future regression that adds a second Popen site outside the
    dedicated spawn function.

    Design: We count ALL ``subprocess.Popen(…)`` calls in the file via AST and
    assert the count is exactly 1.  We also assert that the one Popen call is
    lexically inside a function whose name contains "uds_responder" (the current
    ``_spawn_uds_responder`` or the forthcoming ``ensure_uds_responder``), not at
    module top-level or inside an unrelated function.

    Parsed with ``ast`` rather than text search: the string ``'uds_dtc_responder'``
    appears in docstrings and comments throughout the file; text search cannot
    distinguish code from prose.  Prior art:
    ``test_presence_loop.py::test_production_call_site_passes_the_resolved_writer_not_a_literal_none``.
    """

    def _find_popen_calls(self, tree):
        """Return list of (popen_node, enclosing_function_name) tuples."""
        # Build a mapping from ast node id → enclosing FunctionDef name.
        # Walk the tree depth-first, tracking the current function scope.
        results = []

        def _walk_with_scope(node, enclosing_fn):
            if isinstance(node, ast.FunctionDef):
                current_fn = node.name
            else:
                current_fn = enclosing_fn

            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "Popen"
            ):
                results.append((node, current_fn))

            for child in ast.iter_child_nodes(node):
                _walk_with_scope(child, current_fn)

        _walk_with_scope(tree, None)
        return results

    def test_structural_single_responder_spawn_site(self):
        """Exactly ONE subprocess.Popen call exists in realtime_telemetry_simulator.py,
        and it is inside the dedicated responder-spawn function.

        NOTE: This test asserts an invariant the current code already holds.
        A green result here is expected and correct — this test becomes a
        regression guard once ensure_uds_responder is implemented in Group 3
        (which will rename _spawn_uds_responder to ensure_uds_responder but keep
        the single-Popen invariant intact).
        """
        with open(_RTS_PATH, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source)

        popen_calls = self._find_popen_calls(tree)

        self.assertEqual(
            len(popen_calls), 1,
            f"Expected exactly ONE subprocess.Popen call in realtime_telemetry_simulator.py; "
            f"found {len(popen_calls)}. "
            f"``ensure_uds_responder`` must be the sole construction site for the "
            f"responder subprocess — a second Popen site is a process bug. "
            f"Found at: {[(node.lineno, fn) for node, fn in popen_calls]}",
        )

        _popen_node, enclosing_fn = popen_calls[0]
        self.assertIsNotNone(
            enclosing_fn,
            "The subprocess.Popen call must be inside a named function, "
            "not at module top-level.",
        )
        self.assertTrue(
            "uds_responder" in enclosing_fn or "uds_dtc" in enclosing_fn,
            f"The single Popen call must be inside the responder-spawn function "
            f"(name must contain 'uds_responder' or 'uds_dtc'); "
            f"found it inside '{enclosing_fn}'. "
            f"This ensures the spawn site is the dedicated reconciler, not an "
            f"unrelated function that happens to call Popen.",
        )


class TestNoUdsDtcMapOnVehicleEcuOverrideStructural(unittest.TestCase):
    """(b) simulation_lambda.py's ``_build_fwe_container_overrides`` does NOT add
    ``UDS_DTC_MAP`` to the ``vehicle-ecu`` override, and does not add
    ``CERTIFICATE`` or ``PRIVATE_KEY`` either.

    This test asserts invariants the CURRENT code already holds — the
    ``_build_fwe_container_overrides`` function was written without these env vars
    on the ``vehicle-ecu`` side, and a HARD GATE comment in the code documents this
    explicitly.  A green result is NOT a sign of a broken test — the invariants are
    already true, and this test is a regression guard.

    Parsed with ``ast``: the strings ``UDS_DTC_MAP``, ``CERTIFICATE``, and
    ``PRIVATE_KEY`` appear in comments and docstrings throughout the file; text
    search cannot distinguish code from prose.
    """

    def _get_vehicle_ecu_env_keys(self):
        """Return the list of env var name strings set on the vehicle-ecu override
        inside _build_fwe_container_overrides, parsed from AST."""
        with open(_LAMBDA_PATH, encoding="utf-8") as fh:
            source = fh.read()
        tree = ast.parse(source)

        # Locate the _build_fwe_container_overrides function.
        target_fn = None
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "_build_fwe_container_overrides":
                target_fn = node
                break

        if target_fn is None:
            return None, "Could not find _build_fwe_container_overrides in simulation_lambda.py"

        # Within the function, find the vehicle_ecu_override dict literal.
        # Strategy: find all dict literals that contain "vehicle-ecu" as a value.
        env_keys = []

        def _collect_env_keys_from_dict(d_node):
            """Given an ast.Dict node for a container override, extract env var name strings."""
            # Look for an 'environment' key whose value is a List of dicts with 'name' keys.
            for k, v in zip(d_node.keys, d_node.values):
                if not (isinstance(k, ast.Constant) and k.value == "environment"):
                    continue
                if not isinstance(v, ast.List):
                    continue
                for entry in v.elts:
                    if not isinstance(entry, ast.Dict):
                        continue
                    for ek, ev in zip(entry.keys, entry.values):
                        if (isinstance(ek, ast.Constant) and ek.value == "name"
                                and isinstance(ev, ast.Constant)):
                            env_keys.append(ev.value)

        # Walk assignments inside the function looking for vehicle_ecu_override = {...}
        for node in ast.walk(target_fn):
            if not isinstance(node, ast.Assign):
                continue
            # Check that the target is named 'vehicle_ecu_override'
            for target in node.targets:
                if not (isinstance(target, ast.Name) and target.id == "vehicle_ecu_override"):
                    continue
                if isinstance(node.value, ast.Dict):
                    _collect_env_keys_from_dict(node.value)

        return env_keys, None

    def test_structural_no_uds_dtc_map_on_vehicle_ecu_override(self):
        """_build_fwe_container_overrides must NOT add UDS_DTC_MAP to vehicle-ecu.

        NOTE: This test asserts an invariant the current code already holds.
        A green result here is expected and correct — UDS_DTC_MAP is delivered
        via the sidecar's own environment (inherited from the presence-loop
        process), not via a container override. This test guards against any
        future regression that accidentally adds it here.
        """
        env_keys, err = self._get_vehicle_ecu_env_keys()
        if err:
            self.skipTest(err)
        self.assertNotIn(
            "UDS_DTC_MAP",
            env_keys,
            "_build_fwe_container_overrides must NOT add UDS_DTC_MAP to the "
            "vehicle-ecu container override. UDS_DTC_MAP is delivered via the "
            "presence-loop environment, not via a container override. "
            "See spec § *Why UDS_DTC_MAP is deliberately not added to the sidecar override*.",
        )

    def test_structural_no_certificate_on_vehicle_ecu_override(self):
        """_build_fwe_container_overrides must NOT add CERTIFICATE to vehicle-ecu.

        NOTE: This test asserts an invariant the current code already holds.
        A green result is expected — CERTIFICATE goes on fwe-agent ONLY.
        This is a HARD GATE from the predecessor spec's security review.
        """
        env_keys, err = self._get_vehicle_ecu_env_keys()
        if err:
            self.skipTest(err)
        self.assertNotIn(
            "CERTIFICATE",
            env_keys,
            "_build_fwe_container_overrides must NOT add CERTIFICATE to the "
            "vehicle-ecu override (HARD GATE from security review Cycle 1, Warning 2). "
            "CERTIFICATE goes on fwe-agent ONLY.",
        )

    def test_structural_no_private_key_on_vehicle_ecu_override(self):
        """_build_fwe_container_overrides must NOT add PRIVATE_KEY to vehicle-ecu.

        NOTE: This test asserts an invariant the current code already holds.
        A green result is expected — PRIVATE_KEY goes on fwe-agent ONLY.
        This is a HARD GATE from the predecessor spec's security review.
        See ~/.kiro/steering/secrets-handling.md § *Runtime launch parameters*.
        """
        env_keys, err = self._get_vehicle_ecu_env_keys()
        if err:
            self.skipTest(err)
        self.assertNotIn(
            "PRIVATE_KEY",
            env_keys,
            "_build_fwe_container_overrides must NOT add PRIVATE_KEY to the "
            "vehicle-ecu override (HARD GATE from security review Cycle 1, Warning 2). "
            "PRIVATE_KEY goes on fwe-agent ONLY. "
            "See ~/.kiro/steering/secrets-handling.md § *Runtime launch parameters*.",
        )


class TestEnsureUdsResponderExplicitEnv(unittest.TestCase):
    """Fix Group 1 Task 2 — Suggestion 2: ensure_uds_responder passes an
    explicit env allowlist to subprocess.Popen rather than inheriting
    os.environ in its entirety.

    When os.environ contains a secret-shaped variable (e.g. a leaked key
    that a future change might add to vehicle-ecu), the inherited-env pattern
    would silently flow it into the responder's address space where it could
    appear in diagnostic dumps or logs.

    The remediation (passed env dict) must:
      - contain UDS_DTC_MAP (the mandatory payload)
      - contain CAN_BUS0 (channel default fallback)
      - contain PATH (needed for Python executable lookup)
      - NOT be the full os.environ (i.e. the dict passed must NOT be a
        superset of os.environ with extra keys — it must be a separate, smaller
        allowlist)
    """

    def setUp(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def tearDown(self):
        if _reset_uds_responder_state is not None:
            _reset_uds_responder_state()

    def test_popen_receives_explicit_env_not_full_os_environ(self):
        """ensure_uds_responder must pass an explicit env dict to Popen, not
        dict(os.environ, UDS_DTC_MAP=...).

        Inject a canary var into os.environ before the call.  If the
        implementation uses dict(os.environ, ...), the canary leaks into the
        child.  If it uses an explicit allowlist, the canary is absent.
        """
        _require_reconciler()
        import os as _os

        canary_key = "_TEST_SECRET_CANARY_DO_NOT_FORWARD"
        canary_val = "LEAKED_SECRET_VALUE"

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            # Temporarily inject a canary secret into the process env.
            _os.environ[canary_key] = canary_val
            try:
                ensure_uds_responder({"ECU2": {"req": "0x7E1", "resp": "0x7E9",
                                               "dtcs": ["P0420"]}})
            finally:
                _os.environ.pop(canary_key, None)

            self.assertEqual(mock_popen.call_count, 1)
            _, kwargs = mock_popen.call_args
            child_env = kwargs.get("env", {})

            self.assertNotIn(
                canary_key,
                child_env,
                f"The child process env must NOT contain the canary secret "
                f"{canary_key!r} — ensure_uds_responder must pass an explicit "
                f"env allowlist, not dict(os.environ, ...).",
            )

    def test_popen_env_contains_uds_dtc_map(self):
        """The explicit env passed to the child must contain UDS_DTC_MAP."""
        _require_reconciler()
        import json as _json

        desired = {"ECU2": {"req": "0x7E1", "resp": "0x7E9", "dtcs": ["P0420"]}}
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            ensure_uds_responder(desired)

            _, kwargs = mock_popen.call_args
            child_env = kwargs.get("env", {})
            self.assertIn(
                "UDS_DTC_MAP", child_env,
                "Explicit env must include UDS_DTC_MAP",
            )
            # Round-trip the value to confirm it's valid JSON.
            try:
                parsed = _json.loads(child_env["UDS_DTC_MAP"])
            except (json.JSONDecodeError, TypeError) as exc:
                self.fail(f"UDS_DTC_MAP is not valid JSON: {exc}")
            self.assertEqual(parsed, desired)

    def test_popen_env_contains_path(self):
        """The explicit env must contain PATH so the Python executable resolves."""
        _require_reconciler()

        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.poll.return_value = None
            mock_popen.return_value = mock_proc

            ensure_uds_responder({"ECU2": {"req": "0x7E1", "resp": "0x7E9",
                                           "dtcs": ["P0420"]}})

            _, kwargs = mock_popen.call_args
            child_env = kwargs.get("env", {})
            self.assertIn(
                "PATH", child_env,
                "Explicit env must include PATH so the Python executable resolves",
            )
            self.assertTrue(
                child_env["PATH"],
                "PATH in child env must be non-empty",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)
