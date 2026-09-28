"""Guards a coupling that spans two trees with no reference between them.

`PresenceLoop._HEARTBEAT_INTERVAL_S` (services/simulation/) sets how often a parked
vehicle refreshes `lastSeenAt`. `STALENESS_WINDOW_S`
(modules/cms_ui/source/handlers/main_api/connection_status.py) sets how long the API
believes a stored `connected`. If the heartbeat interval ever rises to meet that
window, every parked vehicle reads offline while being perfectly connected and
commandable — the exact user-visible symptom investigated on 2026-08-19.

Nothing in either file's imports connects them, so only an executable assertion can
hold the invariant. A comment cannot: the `_MAX_TRIPS_PER_INTENT` "if either
changes, change both" note in the simulator is the pattern this replaces, and that
convention is precisely what drifted.

Spec: .kiro/specs/2026-08-19-cms-connection-status-single-source/

Run from services/simulation/::

    python3 -m pytest tests/test_staleness_coupling.py -v
"""
from __future__ import annotations

import os
import sys
import unittest

_SIM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(os.path.dirname(_SIM_DIR))
_MAIN_API = os.path.join(
    _REPO_ROOT, "modules", "cms_ui", "source", "handlers", "main_api"
)

sys.path.insert(0, _SIM_DIR)
sys.path.insert(0, _MAIN_API)

from realtime_telemetry_simulator import PresenceLoop  # noqa: E402
from connection_status import STALENESS_WINDOW_S  # noqa: E402

# The API must tolerate at least this many consecutive missed heartbeats before it
# stops believing a stored 'connected'. Three, not two: heartbeat() advances its
# throttle stamp only on a successful write and retries on the next 9s tick, so
# isolated failures self-heal, but a short DynamoDB brownout should not flip an
# entire fleet offline.
_REQUIRED_MISSED_HEARTBEATS = 3


class StalenessCouplingTest(unittest.TestCase):
    def test_heartbeat_interval_is_well_below_staleness_window(self) -> None:
        heartbeat = PresenceLoop._HEARTBEAT_INTERVAL_S
        self.assertLessEqual(
            heartbeat * _REQUIRED_MISSED_HEARTBEATS,
            STALENESS_WINDOW_S,
            "\nCOUPLED CONSTANTS OUT OF RANGE.\n"
            f"  PresenceLoop._HEARTBEAT_INTERVAL_S = {heartbeat}s\n"
            "    services/simulation/realtime_telemetry_simulator.py\n"
            f"  STALENESS_WINDOW_S = {STALENESS_WINDOW_S}s\n"
            "    modules/cms_ui/source/handlers/main_api/connection_status.py\n"
            f"  Required: heartbeat * {_REQUIRED_MISSED_HEARTBEATS} <= window\n"
            "\nWith the heartbeat this slow relative to the window, a parked vehicle\n"
            "that is connected and commandable will be reported disconnected once\n"
            "its last heartbeat ages out. Either lower the heartbeat interval or\n"
            "raise the staleness window — and change them in the same commit.",
        )

    def test_both_constants_are_positive_numbers(self) -> None:
        self.assertGreater(PresenceLoop._HEARTBEAT_INTERVAL_S, 0)
        self.assertGreater(STALENESS_WINDOW_S, 0)


if __name__ == "__main__":
    unittest.main()
