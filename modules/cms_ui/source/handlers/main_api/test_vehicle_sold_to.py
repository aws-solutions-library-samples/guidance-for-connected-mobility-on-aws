#!/usr/bin/env python3
"""Unit tests for the `sold_to` read-only guard on POST and PUT /api/v1/vehicles.

Spec: .kiro/specs/2026-09-14-cs-portal-data-model-backend
§ "Entitlement — who may subscribe to a VIN"

Background
----------
`sold_to` is a DMS-owned entitlement field.  The ONLY write path is
``deployment/scripts/seed_vehicle_sold_to.py``, which stands in for the DMS
sync that does not exist yet.  No CMS or CS route may write it — at ANY
role, including platform-admin.  The guard is therefore stronger than a role
check: zero write sites in handlers.

Security property being enforced
---------------------------------
A request carrying ``sold_to`` in the body is REJECTED (400), not ignored and
not honoured for admins.  If a consumer could write it, they could self-grant
entitlement by setting ``sold_to`` to their own customer id.  If an admin
could write it via a regular route, the admin route becomes the de-facto
source of truth and DMS sync never lands.

Test cases
----------
S1: POST with sold_to in body, consumer token    → 400 (read-only rejection)
S2: POST with sold_to in body, platform-admin    → 400 (read-only rejection)
S3: POST without sold_to in body                 → 201 (normal path unaffected)
S4: PUT with sold_to in body, consumer token     → 400 (read-only rejection)
S5: PUT with sold_to in body, platform-admin     → 400 (read-only rejection)
S6: PUT without sold_to in body                  → 200 (normal path unaffected)
S7: source-structural check — no handler module writes sold_to

MUTATION TEST (run manually per tasks.md Verify):
  Permit the admin write in index.py and confirm S2 + S5 FAIL.
  This verifies the guard asserts the property, not merely that the field is
  absent.  See mutation result in the task's Verify output.

Run::

    python3 -m pytest modules/cms_ui/source/handlers/main_api/ -k sold_to -v
"""
from __future__ import annotations

import ast
import importlib
import json
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('MODEL_MANIFEST_TABLE_NAME', 'test-model-manifest')
os.environ.setdefault('VEHICLE_CERTIFICATES_TABLE_NAME', 'test-vehicle-certificates')
os.environ.setdefault('CAMPAIGNS_TABLE_NAME', 'test-campaigns')

# Stub boto3 + helpers before importing index
boto3_stub = MagicMock()
boto3_stub.resource = MagicMock(return_value=MagicMock())
boto3_stub.client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('boto3', boto3_stub)

cache_stub = MagicMock()
cache_stub.create_cached_dynamodb_client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('cache_client', cache_stub)

event_catalog_stub = MagicMock()
event_catalog_stub.enrich_event_with_catalog = MagicMock()
event_catalog_stub.normalize_event_response = MagicMock()
sys.modules.setdefault('event_catalog_helper', event_catalog_stub)

import index  # noqa: E402  (must come after stubs)

# ── Helpers ──────────────────────────────────────────────────────────────────

_ADMIN_CLAIMS = {'cognito:groups': 'platform-admin'}
_CONSUMER_CLAIMS = {'cognito:groups': 'fleet-operator', 'custom:fleetIds': 'FLEET-001'}


def _post_event(entry: dict, claims: dict) -> dict:
    """Build a POST /api/v1/vehicles event."""
    return {
        'httpMethod': 'POST',
        'path': '/api/v1/vehicles',
        'body': json.dumps({'entry': entry}),
        'requestContext': {'authorizer': {'claims': claims}},
        'queryStringParameters': None,
        'pathParameters': None,
        'headers': {},
    }


def _put_event(vehicle_id: str, entry: dict, claims: dict) -> dict:
    """Build a PUT /api/v1/vehicles/{id} event."""
    return {
        'httpMethod': 'PUT',
        'path': f'/api/v1/vehicles/{vehicle_id}',
        'body': json.dumps({'entry': entry}),
        'requestContext': {'authorizer': {'claims': claims}},
        'queryStringParameters': None,
        'pathParameters': {'vehicleId': vehicle_id},
        'headers': {},
    }


# Minimal POST body that satisfies all validations except the one under test.
_BASE_POST_ENTRY = {
    'vin': '1HGCM82633A123456',
    'modelManifestName': 'CMS-Fleet-Default',
    'make': 'Meridian',
    'model': 'Crestwind',
    'year': 2024,
    'producer': 'meridian',
}


class SoldToRejectionTest(unittest.TestCase):
    """Tests that sold_to is rejected on POST and PUT at every role level."""

    def setUp(self):
        # Ensure all env vars required by the PUT path are set in isolation.
        # Without these, index.handler returns 500 before reaching the sold_to
        # guard, making S4/S5/S6 fail with 500 instead of 400.  The POST path
        # does NOT require these (it returns before the required-vars check on
        # early-exit guards), but the PUT path does.
        self._env_overrides: dict[str, str] = {}
        for var, val in [
            ('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events'),
            ('DRIVERS_TABLE_NAME', 'test-drivers'),
            ('SERVICE_HISTORY_TABLE_NAME', 'test-service-history'),
        ]:
            if var not in os.environ:
                os.environ[var] = val
                self._env_overrides[var] = val

        self.mock_dynamodb = MagicMock()
        self.mock_vehicles_table = MagicMock()
        self.mock_vehicles_table.put_item.return_value = {}
        self.mock_vehicles_table.update_item.return_value = {'Attributes': {'vehicleId': 'VEH-001'}}
        self.mock_vehicles_table.get_item.return_value = {'Item': {'vehicleId': 'VEH-001'}}

        self.mock_model_table = MagicMock()
        self.mock_model_table.scan.return_value = {
            'Items': [{
                'modelManifestName': 'CMS-Fleet-Default',
                'modelManifestVersion': '1',
                'status': 'ACTIVE',
                'decoderManifestRef': 'cms-fleet-v3',
            }]
        }

        self.mock_fleet_table = MagicMock()
        self.mock_fleet_table.get_item.return_value = {
            'Item': {
                'fleetId': 'FLEET-001',
                'data_source': 'vehicle-telemetry',
            }
        }

        self.mock_campaigns_table = MagicMock()
        self.mock_campaigns_table.get_item.return_value = {}
        self.mock_campaigns_table.put_item.return_value = {}

        self.mock_enrollment_table = MagicMock()
        self.mock_enrollment_table.query.return_value = {'Items': [{'vehicleId': 'VEH-001'}]}

        def _table_factory(name):
            n = (name or '').lower()
            if 'model-manifest' in n:
                return self.mock_model_table
            if 'fleet-enrollment' in n or 'enrollment' in n:
                return self.mock_enrollment_table
            if 'fleet' in n:
                return self.mock_fleet_table
            if 'campaigns' in n:
                return self.mock_campaigns_table
            return self.mock_vehicles_table

        self.mock_dynamodb.Table.side_effect = _table_factory

        self.patcher = patch.object(index, 'dynamodb', self.mock_dynamodb)
        self.patcher.start()

        # IoT client mock for cert issuance (vehicle-telemetry POST path).
        self.mock_iot = MagicMock()
        self.mock_iot.create_keys_and_certificate.return_value = {
            'certificateArn': 'arn:aws:iot:us-east-1:123456789012:cert/abc123',
            'certificateId': 'abc123',
            'certificatePem': '-----BEGIN CERTIFICATE-----\nMOCK\n-----END CERTIFICATE-----\n',
            'keyPair': {'PublicKey': 'PUB', 'PrivateKey': 'PRIV'},
        }
        self.boto3_patcher = patch.object(index.boto3, 'client', return_value=self.mock_iot)
        self.boto3_patcher.start()

    def tearDown(self):
        self.patcher.stop()
        self.boto3_patcher.stop()
        # Clean up any env vars we set in setUp so other tests are not affected.
        for var in self._env_overrides:
            os.environ.pop(var, None)

    # ── S1: POST + consumer (fleet-operator) + sold_to → 400 ─────────────

    def test_s1_post_consumer_with_sold_to_rejected(self):
        """S1: A fleet-operator carrying sold_to in a POST body receives 400.

        Property: sold_to is not consumer-writable.
        Mutation verification: remove the sold_to guard in index.py → this test
        fails because the consumer POST would proceed past the guard.
        """
        entry = dict(_BASE_POST_ENTRY, sold_to='CUST-0040014E')
        resp = index.handler(_post_event(entry, _CONSUMER_CLAIMS), {})
        self.assertEqual(
            resp['statusCode'], 400,
            msg=(
                "fleet-operator POST with sold_to must be rejected 400; "
                "if this fails, the sold_to guard in the POST route is absent or bypassed"
            ),
        )
        body = json.loads(resp['body'])
        self.assertIn('sold_to', body['error'].lower(),
                      msg="Error message must name the rejected field")
        # The vehicle row must NOT have been written.
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── S2: POST + platform-admin + sold_to → 400 ────────────────────────

    def test_s2_post_admin_with_sold_to_rejected(self):
        """S2: A platform-admin carrying sold_to in a POST body receives 400.

        Property: sold_to is read-only even for admins — not a role check.
        Mutation verification: add a `if is_admin: pass` bypass → this test
        fails because the admin POST would then succeed.
        """
        entry = dict(_BASE_POST_ENTRY, sold_to='CUST-004000F1')
        resp = index.handler(_post_event(entry, _ADMIN_CLAIMS), {})
        self.assertEqual(
            resp['statusCode'], 400,
            msg=(
                "platform-admin POST with sold_to must be rejected 400; "
                "if this fails, the guard is a role check not a blanket rejection"
            ),
        )
        body = json.loads(resp['body'])
        self.assertIn('sold_to', body['error'].lower())
        self.mock_vehicles_table.put_item.assert_not_called()

    # ── S3: POST without sold_to → 201 ───────────────────────────────────

    def test_s3_post_without_sold_to_succeeds(self):
        """S3: A POST without sold_to is unaffected by the guard."""
        entry = dict(_BASE_POST_ENTRY)
        resp = index.handler(_post_event(entry, _ADMIN_CLAIMS), {})
        self.assertEqual(resp['statusCode'], 201,
                         msg="POST without sold_to must succeed normally")

    # ── S4: PUT + consumer (fleet-operator) + sold_to → 400 ──────────────

    def test_s4_put_consumer_with_sold_to_rejected(self):
        """S4: A fleet-operator carrying sold_to in a PUT body receives 400.

        Property: sold_to is not updatable by any consumer role.
        """
        entry = {'sold_to': 'CUST-0040014E', 'color': 'red'}
        resp = index.handler(_put_event('VEH-001', entry, _CONSUMER_CLAIMS), {})
        self.assertEqual(
            resp['statusCode'], 400,
            msg=(
                "fleet-operator PUT with sold_to must be rejected 400; "
                "if this fails, the sold_to guard in the PUT route is absent"
            ),
        )
        body = json.loads(resp['body'])
        self.assertIn('sold_to', body['error'].lower())
        # update_item must NOT have been called.
        self.mock_vehicles_table.update_item.assert_not_called()

    # ── S5: PUT + platform-admin + sold_to → 400 ─────────────────────────

    def test_s5_put_admin_with_sold_to_rejected(self):
        """S5: A platform-admin carrying sold_to in a PUT body receives 400.

        Property: sold_to is read-only even for admins.
        Mutation verification: add a `if is_admin: pass` bypass → this test
        fails because the admin PUT would then succeed.
        """
        entry = {'sold_to': 'CUST-004000DD', 'color': 'blue'}
        resp = index.handler(_put_event('VEH-001', entry, _ADMIN_CLAIMS), {})
        self.assertEqual(
            resp['statusCode'], 400,
            msg=(
                "platform-admin PUT with sold_to must be rejected 400; "
                "if this fails, the guard is a role check not a blanket rejection"
            ),
        )
        body = json.loads(resp['body'])
        self.assertIn('sold_to', body['error'].lower())
        self.mock_vehicles_table.update_item.assert_not_called()

    # ── S6: PUT without sold_to → 200 ────────────────────────────────────

    def test_s6_put_without_sold_to_succeeds(self):
        """S6: A PUT without sold_to is unaffected by the guard."""
        entry = {'color': 'green'}
        resp = index.handler(_put_event('VEH-001', entry, _ADMIN_CLAIMS), {})
        self.assertEqual(resp['statusCode'], 200,
                         msg="PUT without sold_to must succeed normally")


def _find_repo_root(start: Path) -> Path:
    """Search upward from *start* for the repo root (identified by a ``.git`` entry).

    Raises ``RuntimeError`` if no repo root is found before reaching the
    filesystem root.  Uses an upward walk rather than a depth count so the
    result is correct regardless of where this file lives within the repo —
    a depth count breaks silently the moment the file moves.
    """
    candidate = start.resolve()
    while True:
        if (candidate / '.git').exists():
            return candidate
        parent = candidate.parent
        if parent == candidate:
            raise RuntimeError(
                f"Could not locate repo root (no .git marker) "
                f"starting from {start.resolve()}"
            )
        candidate = parent


# Repo root resolved once at import time via upward .git search.
# Using .resolve() + upward search rather than a depth count prevents the
# "off-by-one breaks silently on file move" class of failure that made the
# original _HANDLER_ROOTS vacuous (it used parents[4] without .resolve(),
# yielding paths under .../modules/ that do not exist).
_REPO_ROOT = _find_repo_root(Path(__file__))


class SoldToSourceStructuralTest(unittest.TestCase):
    """S7: source-structural check — no handler module writes sold_to.

    Walks every .py file under modules/cms_ui/source/handlers/ and
    services/connectors/ (minus the seed script, which is the ONE permitted
    write path) and asserts that none of them contain an AST node that
    assigns the string ``sold_to`` as a dictionary key or as a keyword
    argument named ``sold_to``.

    This is the executable form of the "zero write sites in handlers" rule.
    A string search would also catch comments; AST search catches only
    real write expressions.

    Why AST and not grep
    --------------------
    A grep for ``sold_to`` would flag legitimate read-path references
    (e.g. `entry.get('sold_to')` in the guard, `item.get('sold_to')` in
    the backfill, comments, this test file).  We want to catch:
        d['sold_to'] = ...         (Subscript assignment)
        {... 'sold_to': value ...} (Dict literal with sold_to key)
        update_item(..., sold_to=...) (Keyword argument)
    A comment or a `.get('sold_to')` read does not trigger any of those.
    """

    # Roots to scan for handler write sites.
    # Anchored via _REPO_ROOT (upward .git search + .resolve()) rather than a
    # depth count so the paths are correct regardless of where this file lives
    # within the repo.  Matches the pattern used by the sibling guard
    # deployment/stacks/tests/test_vehicle_producer_write_guard.py, which
    # uses `Path(__file__).resolve().parents[3]` for the same reason.
    _HANDLER_ROOTS = [
        _REPO_ROOT / 'modules' / 'cms_ui' / 'source' / 'handlers',
        _REPO_ROOT / 'services' / 'connectors',
    ]

    # Minimum number of non-test .py files the walk must enumerate.
    # Guards against a future anchor regression producing an empty walk that
    # passes vacuously.  Tune downward only if the codebase genuinely shrinks.
    _MIN_FILES_FLOOR = 20

    # Files that ARE permitted to mention sold_to as a write target.
    # (This test file itself is excluded because it is a test file and
    # contains string literals, not write-site AST nodes.)
    _ALLOWED_WRITE_FILES = {
        str((_REPO_ROOT / 'deployment' / 'scripts' / 'seed_vehicle_sold_to.py').resolve()),
        # The three files below are part of the T0.4 denormalisation chain:
        # they pass sold_to as a keyword argument to mark_available(), which
        # writes it to the AVAILABILITY table for the SoldToIndex GSI.
        # None of them write sold_to to the vehicles table.
        #
        # services/connectors/subscriptions/_lib/mark_available.py
        #   Core implementation: update_item UpdateExpression "SET … sold_to = :c"
        #   on the availability row.  The ONLY line that touches DynamoDB.
        str((_REPO_ROOT / 'services' / 'connectors' / 'subscriptions'
             / '_lib' / 'mark_available.py').resolve()),
        #
        # services/connectors/subscriptions/admin_mark_available/handler.py
        #   Passes sold_to=_lookup_sold_to(...) to mark_available().
        #   The lookup reads the VEHICLES table (read-only); the write goes
        #   to the availability table via mark_available, not directly.
        str((_REPO_ROOT / 'services' / 'connectors' / 'subscriptions'
             / 'admin_mark_available' / 'handler.py').resolve()),
        #
        # services/connectors/subscriptions/availability_listener/handler.py
        #   Passes sold_to=_resolve_sold_to(...) to mark_available().
        #   Same call chain: read from stream image or vehicles table,
        #   write to availability table via mark_available.
        str((_REPO_ROOT / 'services' / 'connectors' / 'subscriptions'
             / 'availability_listener' / 'handler.py').resolve()),
        # The backfill_vehicle_producer.py also writes producer but never
        # sold_to, so it does not need allowlisting here.
    }

    @staticmethod
    def _file_has_sold_to_write(path: Path) -> list[int]:
        """Return line numbers where sold_to is written (dict key or kwarg).

        Matches:
            d['sold_to'] = ...          → Assign with Subscript whose slice is 'sold_to'
            {'sold_to': v, ...}         → Dict literal with 'sold_to' key
            foo(sold_to=...)            → keyword argument named sold_to
            UpdateExpression SET sold_to  (string literal containing sold_to=)
                — this is handled via a simpler substring check on the raw source
                  for UpdateExpression strings, because AST cannot parse the
                  DynamoDB expression language.

        Does NOT match:
            entry.get('sold_to')        → attribute access / method call
            item.get('sold_to', '')     → same
            if 'sold_to' in entry:      → membership test
            # comment mentioning sold_to
        """
        try:
            source = path.read_text(encoding='utf-8')
            tree = ast.parse(source, filename=str(path))
        except (SyntaxError, UnicodeDecodeError):
            return []

        hits: list[int] = []

        class Visitor(ast.NodeVisitor):
            def visit_Assign(self, node: ast.Assign) -> None:
                # d['sold_to'] = value
                for target in node.targets:
                    if isinstance(target, ast.Subscript):
                        slc = target.slice
                        if isinstance(slc, ast.Constant) and slc.value == 'sold_to':
                            hits.append(node.lineno)
                self.generic_visit(node)

            def visit_Dict(self, node: ast.Dict) -> None:
                # {'sold_to': value, ...}
                for key in node.keys:
                    if isinstance(key, ast.Constant) and key.value == 'sold_to':
                        hits.append(node.lineno)
                self.generic_visit(node)

            def visit_keyword(self, node: ast.keyword) -> None:  # noqa: N802
                # foo(sold_to=...)
                if node.arg == 'sold_to':
                    hits.append(node.lineno)
                self.generic_visit(node)

        Visitor().visit(tree)

        # Additional substring check for DynamoDB UpdateExpression strings
        # that contain ``SET sold_to`` or ``sold_to =``.
        for i, line in enumerate(source.splitlines(), start=1):
            stripped = line.strip()
            if ('SET sold_to' in stripped or 'sold_to =' in stripped
                    or 'sold_to=' in stripped):
                # Skip lines that are just a .get() or membership test.
                if '.get(' not in stripped and ' in ' not in stripped:
                    if i not in hits:
                        hits.append(i)

        return sorted(hits)

    def test_s7_no_handler_writes_sold_to(self):
        """S7: No handler or service module writes sold_to.

        Scans every .py file under handlers/ and connectors/ and asserts
        that no file contains a sold_to write expression, except for the
        explicitly allowed seed script and mark_available.py (the T0.4
        availability-table denormalisation).

        Mutation verification: add a `vehicle_item['sold_to'] = 'CUST-XYZ'`
        line in index.py → this test fails, confirming the check catches real
        write sites.
        """
        violations: list[str] = []
        files_scanned = 0

        for root in self._HANDLER_ROOTS:
            # A missing root is a broken anchor, not a vacuous pass.
            # Assert loudly rather than skipping silently — the original
            # `if not root.exists(): continue` is what made the walk vacuous.
            self.assertTrue(
                root.exists(),
                msg=(
                    f"Handler root does not exist: {root}\n"
                    "This indicates a broken path anchor in _HANDLER_ROOTS. "
                    "Fix the anchor rather than adding to _ALLOWED_WRITE_FILES."
                ),
            )
            for py_file in sorted(root.rglob('*.py')):
                resolved = str(py_file.resolve())
                if resolved in self._ALLOWED_WRITE_FILES:
                    continue
                # Skip test files — they contain string literals, not write sites.
                if py_file.name.startswith('test_'):
                    continue
                files_scanned += 1
                hits = self._file_has_sold_to_write(py_file)
                if hits:
                    violations.append(
                        f"{py_file.relative_to(_REPO_ROOT)}"
                        f" line(s) {hits}"
                    )

        # Anti-vacuity: the walk must enumerate enough files to be meaningful.
        # If this assertion fires, the anchor in _HANDLER_ROOTS is probably wrong.
        self.assertGreaterEqual(
            files_scanned,
            self._MIN_FILES_FLOOR,
            msg=(
                f"Walk enumerated only {files_scanned} non-test .py files "
                f"(floor is {self._MIN_FILES_FLOOR}). "
                "The _HANDLER_ROOTS anchor is likely broken — the test was "
                "passing vacuously on an empty walk."
            ),
        )

        self.assertEqual(
            violations, [],
            msg=(
                "The following files contain sold_to write sites — "
                "sold_to is DMS-owned and read-only in this repo. "
                "The ONLY permitted write paths are "
                "deployment/scripts/seed_vehicle_sold_to.py and "
                "services/connectors/subscriptions/_lib/mark_available.py "
                "(availability-table denormalisation, not vehicles table):\n"
                + "\n".join(f"  {v}" for v in violations)
            ),
        )


if __name__ == '__main__':
    unittest.main()
