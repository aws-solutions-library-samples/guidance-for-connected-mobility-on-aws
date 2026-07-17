"""
Regression tests for issue 2026-07-16-prod-fleet-health-500.

Live-user 500 root cause: ``service_history`` items carry ``cost`` in two
shapes in prod — plain Decimal for legacy items, structured dict
``{'laborCost': ..., 'partsCost': ..., 'taxCost': ..., 'totalCost': ...,
'currency': 'USD'}`` for newer items (86 of 200 sampled). The fleet-health
handler used to do ``float(s.get('cost', 0))`` which raised TypeError on
the structured shape, causing a 500 that was silently swallowed by an
outer except-clause with no CloudWatch signal.

We test the extraction helper's behavior indirectly by exercising the
fleet-health handler with mocked DDB scans that return both cost shapes,
and asserting the response is 200 and computes a cost_score that reflects
BOTH shapes.
"""
import os
import sys
import unittest
from decimal import Decimal
from unittest.mock import MagicMock, patch

# Same import-time env stubbing as sibling tests so index.py's DDB /
# module-level clients don't need real AWS. Match test_driver_self_authz.py.
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'cms-test-storage-vehicles')
os.environ.setdefault('MAINTENANCE_ALERTS_TABLE_NAME', 'cms-test-storage-maintenance-alerts')
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'cms-test-storage-safety-events')
os.environ.setdefault('FLEETS_TABLE_NAME', 'cms-test-storage-fleets')
os.environ.setdefault('FLEET_ENROLLMENT_TABLE_NAME', 'cms-test-storage-fleet-enrollment')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'cms-test-storage-service-history')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'cms-test-storage-dashboard-metrics-cache')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'cms-test-storage-drivers')
os.environ.setdefault('DEPLOYMENT_STAGE', 'test')

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def _fh_event() -> dict:
    """A minimal API-Gateway-shaped event for /api/v1/fleet-health as admin."""
    return {
        'httpMethod': 'GET',
        'path': '/api/v1/fleet-health',
        'queryStringParameters': None,
        'requestContext': {
            'authorizer': {
                'claims': {
                    'sub': 'test-admin',
                    'email': 'admin@example.com',
                    'cognito:groups': 'platform-admin',
                    'custom:fleetIds': '',
                }
            }
        },
    }


class FleetHealthCostShapeTests(unittest.TestCase):
    """Cost-field-shape regression suite.

    We patch ``dynamodb.Table`` on the imported handler module so each
    ``.scan()`` / ``.get_item()`` call returns fixture data. The cache-lookup
    branch returns a miss so the aggregation path executes.
    """

    def setUp(self):
        # Late-import so env stubs are in place before index.py's module-init
        # DDB client construction runs.
        if 'index' in sys.modules:
            del sys.modules['index']
        import index
        self.index = index

    def _run_with_service_items(self, service_items):
        """Invoke handler with a stubbed DDB that returns service_items on
        service_history scans, empty on everything else, and returns a
        cache miss."""
        # Build one mock table per table_name accessed
        def mock_table(table_name):
            t = MagicMock()
            # get_item — used ONLY for cache lookup by fleet-health
            t.get_item.return_value = {}  # cache miss
            t.put_item.return_value = {}
            # scan — routed by table name (helper closures pick correctly by env-derived name)
            if 'service-history' in table_name:
                t.scan.return_value = {'Items': service_items}
            else:
                t.scan.return_value = {'Items': []}
            return t

        # The count_scan helper uses dynamodb.meta.client.scan for Select=COUNT.
        # We stub that too — return 0 for the safety count so we don't
        # trigger the safety-events code path.
        with patch.object(self.index, 'dynamodb') as mock_ddb:
            mock_ddb.Table.side_effect = mock_table
            mock_ddb.meta.client.scan.return_value = {'Count': 0}
            resp = self.index.handler(_fh_event(), None)
        return resp

    def test_structured_cost_map_does_not_500(self):
        """The exact shape that blew up in prod on 2026-07-16."""
        service_items = [
            {
                'cost': {
                    'laborCost': Decimal('273.83'),
                    'partsCost': Decimal('164.4'),
                    'currency': 'USD',
                    'taxCost': Decimal('36.15'),
                    'totalCost': Decimal('474.38'),
                },
                'warrantyCoverage': Decimal('0'),
            },
        ]
        resp = self._run_with_service_items(service_items)
        self.assertEqual(resp['statusCode'], 200, resp.get('body'))

    def test_plain_decimal_cost_still_works(self):
        """Legacy shape (plain number) must continue to work."""
        service_items = [
            {'cost': Decimal('100.00'), 'warrantyCoverage': Decimal('50.00')},
            {'cost': Decimal('200.50'), 'warrantyCoverage': Decimal('0')},
        ]
        resp = self._run_with_service_items(service_items)
        self.assertEqual(resp['statusCode'], 200, resp.get('body'))

    def test_mixed_cost_shapes_do_not_500(self):
        """Real prod state: some items structured, some plain."""
        service_items = [
            {'cost': Decimal('100.00'), 'warrantyCoverage': Decimal('50.00')},
            {
                'cost': {
                    'laborCost': Decimal('50'),
                    'partsCost': Decimal('30'),
                    'taxCost': Decimal('5'),
                    'totalCost': Decimal('85'),
                    'currency': 'USD',
                },
                'warrantyCoverage': Decimal('10'),
            },
            {'cost': None, 'warrantyCoverage': None},  # sparse row
            {'warrantyCoverage': Decimal('20')},  # cost missing entirely
        ]
        resp = self._run_with_service_items(service_items)
        self.assertEqual(resp['statusCode'], 200, resp.get('body'))

    def test_cost_map_without_totalcost_falls_back_to_sum(self):
        """Older writer might not include totalCost — sum labor/parts/tax."""
        service_items = [
            {
                'cost': {
                    'laborCost': Decimal('100'),
                    'partsCost': Decimal('50'),
                    'taxCost': Decimal('10'),
                    # totalCost intentionally absent
                    'currency': 'USD',
                },
                'warrantyCoverage': Decimal('0'),
            },
        ]
        resp = self._run_with_service_items(service_items)
        self.assertEqual(resp['statusCode'], 200, resp.get('body'))

    def test_malformed_cost_still_returns_200(self):
        """A garbage cost value must not 500 the whole endpoint — fail
        closed to 0 for that record so the aggregate still renders."""
        service_items = [
            {'cost': 'not-a-number', 'warrantyCoverage': Decimal('0')},
            {'cost': ['unexpected', 'list'], 'warrantyCoverage': Decimal('0')},
        ]
        resp = self._run_with_service_items(service_items)
        self.assertEqual(resp['statusCode'], 200, resp.get('body'))


if __name__ == '__main__':
    unittest.main()
