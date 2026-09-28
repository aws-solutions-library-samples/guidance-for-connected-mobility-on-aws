"""
conftest.py — extend sys.path so tests can import commands_lambda directly.

The commands_lambda module co-locates command_request_pb2 (protobuf) in the
same directory, and the `package/` subdir ships the vendored `google.protobuf`
runtime that Lambda uses.  Add both so the tests import the same shape the
Lambda executes.

Also mirrors the OEM1 connector overlay so that `from _lib.fleet_membership import ...`
resolves during local test runs — the same overlay that the CDK bundle helper applies
at deploy time.  Mirrors services/simulation/lambda/conftest.py.

Also adds the `services/` root so `from _shared.routine_catalog import ...`
(which the deploy bundle stages via `deployment/stacks/commands_stack.py` and
`_stage_commands_asset`) resolves during local test runs.
"""
import os
import sys

_COMMANDS_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_PACKAGE_DIR = os.path.join(_COMMANDS_DIR, "package")

for p in (_PACKAGE_DIR, _COMMANDS_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)

# OEM1 connector overlay — mirrors services/simulation/lambda/conftest.py.
# The connector package root contains `_lib/` as a direct child.
# Adding it to sys.path makes `import _lib.fleet_membership` resolve to
# services/connectors/oem1/_lib/fleet_membership.py — exactly what the
# CDK bundle overlays into the Lambda asset.
_CONNECTOR_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "connectors", "oem1")
)
if _CONNECTOR_ROOT not in sys.path:
    sys.path.insert(0, _CONNECTOR_ROOT)

# services/ root — makes `from _shared.routine_catalog import ...` resolve
# by finding services/_shared/routine_catalog.py. The deploy bundle
# (`_stage_commands_asset` in deployment/stacks/commands_stack.py) copies
# services/_shared/ into the Lambda asset root at `_shared/`, so this
# mirrors the same shape locally.
_SERVICES_ROOT = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..")
)
if _SERVICES_ROOT not in sys.path:
    sys.path.insert(0, _SERVICES_ROOT)
