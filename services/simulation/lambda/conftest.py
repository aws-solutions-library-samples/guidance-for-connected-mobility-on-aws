"""
conftest.py for services/simulation/lambda tests.

Adds the OEM1 connector directory to sys.path so that
`_lib.fleet_membership` (overlaid by the CDK bundle helper at deploy time)
is importable during local test runs without duplicating the source.

Source-of-truth for _lib/ is services/connectors/oem1/_lib/.
This conftest merely reflects the same overlay that _bundle_sim_lambda()
applies to the Lambda asset at synth time.
"""
import os
import sys

# The connector package root contains `_lib/` as a direct child.
# Adding it to sys.path makes `import _lib.fleet_membership` resolve to
# services/connectors/oem1/_lib/fleet_membership.py — exactly what the
# CDK bundle overlays into the Lambda asset.
_CONNECTOR_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "connectors", "oem1")
)
if _CONNECTOR_ROOT not in sys.path:
    sys.path.insert(0, _CONNECTOR_ROOT)
