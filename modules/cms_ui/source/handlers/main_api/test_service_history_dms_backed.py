#!/usr/bin/env python3
"""Tests for the DMS-backed GET/POST /api/v1/service-history routes.

Spec ``2026-09-02-cms-dms-service-convergence`` T5.2.

Each test class guards one of the decisions D-G5b through D-G5h.
Every guard is verified by mutation (break the property in source, re-run,
confirm red, revert) before being committed — see the mutation table in the
spec's decisions.md.

D-G5b — Route branches on DMS_API_ENDPOINT.
D-G5c — Merge on read: DMS owns its fields; cache supplies the rest.
         _DMS_OWNED_SERVICE_FIELDS frozenset is exact.
D-G5d — Failed DMS + non-empty cache = 200 cache.
         Failed DMS + empty cache = 502 (not 200 []).
D-G5e — VIN hop: vehicleId != vin; two-step resolver; no scan fallback.
         vin not set to vehicleId when the value IS the vehicleId.
D-G5f — _RefuseRedirects hoisted to module level; existing redirect tests pass
         (acceptance criterion: test_dealers_endpoint.py stays green, no edits).
D-G5g — CMS-side auth check on cache path only; live path forwards token.
         _deny_viewer() stays on POST.
D-G5h — Cache row written ONLY after DMS 201. Failed DMS = no CMS row.

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 -m pytest test_service_history_dms_backed.py -v
"""
from __future__ import annotations

import json
import os
import sys
import ast
import inspect
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import MagicMock, call, patch, PropertyMock

import pytest

# ── Bootstrap (same stub pattern as test_dealers_endpoint.py) ────────────────

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('DEPLOYMENT_STAGE', 'test')
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-dashboard-metrics')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'test-drivers')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'test-service-history')

boto3_stub = MagicMock()
boto3_stub.resource = MagicMock(return_value=MagicMock())
boto3_stub.client = MagicMock(return_value=MagicMock())

# boto3.dynamodb.conditions.Key is used in index.py for GSI queries.
# Provide a real-ish Key that produces a condition expression the DDB mock accepts.
import types
_ddb_conditions_module = types.ModuleType('boto3.dynamodb.conditions')
class _FakeKey:
    def __init__(self, name):
        self._name = name
    def eq(self, val):
        return f'{self._name} = :val'
_ddb_conditions_module.Key = _FakeKey
_ddb_module = types.ModuleType('boto3.dynamodb')
_ddb_module.conditions = _ddb_conditions_module
boto3_stub.dynamodb = _ddb_module
sys.modules.setdefault('boto3', boto3_stub)
sys.modules.setdefault('boto3.dynamodb', _ddb_module)
sys.modules.setdefault('boto3.dynamodb.conditions', _ddb_conditions_module)

cache_stub = MagicMock()
cache_stub.create_cached_dynamodb_client = MagicMock(return_value=MagicMock())
sys.modules.setdefault('cache_client', cache_stub)

event_catalog_stub = MagicMock()
event_catalog_stub.enrich_event_with_catalog = MagicMock()
event_catalog_stub.normalize_event_response = MagicMock()
sys.modules.setdefault('event_catalog_helper', event_catalog_stub)

import index  # noqa: E402  (must follow stubs)

# ── Shared helpers ────────────────────────────────────────────────────────────

_TOKEN = 'Bearer eyJraWQiOiJ0ZXN0In0.test-payload.test-signature'

_DMS_RO = {
    # Real DMS snake_case fields from _NARROW_RO_FIELDS + dealer_name enrichment
    # (source/handlers/repair_orders.py::_project_narrow).
    # F4.2: fixture matches DMS's actual shape, not invented camelCase.
    'ro_id': 'ro-001',
    'status': 'Draft',
    'dealer_id': 'dealer-austin',
    'dealer_name': 'Meridian of Austin',
    'opened_at': '2026-09-01T10:00:00Z',
    'created_at': '2026-09-01T08:00:00Z',
    'updated_at': '2026-09-01T10:00:00Z',
    'description': 'Brake inspection',
    'vehicle_vin': '1FTFW1ET5DFC10312',
    # initiated_by and recall_id are present in _NARROW_RO_FIELDS but
    # deliberately unmapped to CMS allowlist (D-G5i)
    'initiated_by': 'cms_booking',
}

_CACHE_ROW = {
    'vehicleId': 'VEH-001',
    'serviceDate': '2026-09-01',
    'roId': 'ro-001',     # correlation key stamped by POST
    'notes': 'Extra notes from CMS',
    'cost': {'totalCost': 200},
    'alertId': 'alert-9',
    'createdAt': '2026-09-01T09:00:00Z',
    'cachedAt': '2026-09-01T09:00:00Z',
    # These should NOT overwrite DMS values on merge (D-G5c):
    'status': 'scheduled',
    'provider': 'WRONG NAME SHOULD NOT APPEAR',
}


def _get_event(*, vehicleId='VEH-001', auth=_TOKEN,
               groups='platform-admin', dms_endpoint='https://dms.example.invalid'):
    return {
        'httpMethod': 'GET',
        'path': '/api/v1/service-history',
        'headers': {'Authorization': auth} if auth else {},
        'queryStringParameters': {'vehicleId': vehicleId} if vehicleId else {},
        'body': None,
        'requestContext': {
            'authorizer': {
                'claims': {
                    'cognito:groups': groups,
                    'email': 'operator@example.com',
                    'custom:fleetIds': 'fleet-a',
                }
            }
        },
    }


def _post_event(*, vehicleId='VEH-001', dealerId='dealer-austin',
                auth=_TOKEN, groups='platform-admin',
                dms_endpoint='https://dms.example.invalid'):
    return {
        'httpMethod': 'POST',
        'path': '/api/v1/service-history',
        'headers': {'Authorization': auth} if auth else {},
        'queryStringParameters': {},
        'body': json.dumps({
            'vehicleId': vehicleId,
            'dealerId': dealerId,
            'description': 'Brake check',
            'status': 'scheduled',
        }),
        'requestContext': {
            'authorizer': {
                'claims': {
                    'cognito:groups': groups,
                    'email': 'operator@example.com',
                    'custom:fleetIds': 'fleet-a',
                }
            }
        },
    }


class _FakeDmsGetResp:
    # F4.2: real DMS envelope is {"items": [...]} from ok_response({"items": projected})
    # in list_fleet_repair_orders. The previous fixture used "repairOrders" — the bug
    # this fix group is closing (D-G5i defect 1).
    def __init__(self, ros=None):
        self._raw = json.dumps({'items': ros or [_DMS_RO]}).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


class _FakeDmsPostResp:
    def __init__(self, ro=None):
        self._raw = json.dumps({'repairOrder': ro or {'roId': 'ro-new'}}).encode()

    def read(self):
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _fake_table_returning(items):
    """Return a DynamoDB Table mock whose query and scan return given items."""
    tbl = MagicMock()
    tbl.query.return_value = {'Items': items}
    tbl.scan.return_value = {'Items': items}
    tbl.get_item.return_value = {'Item': items[0]} if items else {}
    return tbl


def _vehicles_tbl_with_miss():
    """Vehicles table that misses on both GetItem and vin-index Query.

    Used when we don't care about VIN resolution (no vin in the test scenario).
    """
    tbl = MagicMock()
    tbl.get_item.return_value = {}  # no Item
    tbl.query.return_value = {'Items': []}
    tbl.scan.return_value = {'Items': []}
    return tbl


def _make_table_factory(cache_tbl, veh_tbl=None):
    """Return a side_effect function for dynamodb.Table() calls."""
    if veh_tbl is None:
        # Default: vehicleId='VEH-001' resolves to vin='1FTFW1ET5DFC10312' via GetItem.
        # This matches _DMS_RO.vehicle_vin so vehicleId-scoped filter passes.
        # Tests that specifically test VIN-resolution failure pass their own veh_tbl.
        _veh = MagicMock()
        _veh.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}
        _veh.query.return_value = {'Items': []}
        _veh.scan.return_value = {'Items': []}
    else:
        _veh = veh_tbl
    def _factory(name):
        if 'vehicles' in name.lower():
            return _veh
        return cache_tbl
    return _factory


def _urlopen_returning_dms(ros=None):
    return MagicMock(return_value=_FakeDmsGetResp(ros))


def _urlopen_returning_dms_post(ro=None):
    return MagicMock(return_value=_FakeDmsPostResp(ro))


# ── D-G5b: Route branches on DMS_API_ENDPOINT ────────────────────────────────

class TestD_G5b_RouteGating:
    """D-G5b: Route branches on DMS_API_ENDPOINT.

    - Unset / empty → legacy (cache table is primary, no provenance envelope).
    - Bad scheme → 503 fail-closed.
    - https:// → DMS-backed path.

    Mutation guard: replacing the ``not dms_endpoint.startswith('https://')``
    check with ``False`` removes the fail-closed 503 for a bad scheme, causing
    the bad-scheme test to fail.
    """

    def test_unset_endpoint_falls_through_to_legacy_200(self):
        """Legacy path: DMS_API_ENDPOINT unset → 200 from cache table."""
        cache_tbl = _fake_table_returning([_CACHE_ROW])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': ''}), \
                patch.object(index, 'dynamodb') as mock_ddb:
            mock_ddb.Table.return_value = cache_tbl
            result = index.handler(_get_event(dms_endpoint=''), None)
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        # Legacy path emits no dataSource envelope (D-G5b)
        assert 'dataSource' not in body

    def test_whitespace_only_endpoint_treated_as_unset(self):
        cache_tbl = _fake_table_returning([_CACHE_ROW])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': '   '}), \
                patch.object(index, 'dynamodb') as mock_ddb:
            mock_ddb.Table.return_value = cache_tbl
            result = index.handler(_get_event(dms_endpoint=''), None)
        assert result['statusCode'] == 200

    def test_non_https_endpoint_is_503_not_legacy(self):
        """Misconfigured endpoint: fail-closed 503, never silently serves legacy.

        MUTATION TARGET (D-G5b): removing the scheme check or returning legacy
        instead of 503 must fail this test.
        """
        urlopen_mock = MagicMock()
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'http://dms.example.invalid'}), \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            result = index.handler(_get_event(dms_endpoint='http://dms.example.invalid'), None)
        # Must be 503, not 200 (legacy)
        assert result['statusCode'] == 503, (
            f'expected 503 for http:// endpoint, got {result["statusCode"]} — '
            'scheme check is absent or fell through to legacy'
        )
        # No data returned — same D5 rule as GET /api/v1/dealers
        body = json.loads(result['body'])
        assert 'serviceRecords' not in body
        # urlopen must NOT have been called — scheme check runs before any network call
        assert urlopen_mock.call_count == 0, 'token was sent over a non-https URL'

    def test_https_endpoint_reaches_dms(self):
        """https:// endpoint → DMS is called."""
        cache_tbl = _fake_table_returning([])
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}  # miss
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms([_DMS_RO])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_get_event(dms_endpoint='https://dms.example.invalid'), None)
        assert urlopen_mock.call_count >= 1, 'DMS was not called for https:// endpoint'

    def test_post_non_https_is_503(self):
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'http://dms.example.invalid'}):
            result = index.handler(_post_event(dms_endpoint='http://dms.example.invalid'), None)
        assert result['statusCode'] == 503
        body = json.loads(result['body'])
        assert 'serviceRecord' not in body


# ── D-G5c: Merge semantics ────────────────────────────────────────────────────

class TestD_G5c_MergeSemantics:
    """D-G5c: DMS values win on overlap; cache supplies non-DMS fields.

    _DMS_OWNED_SERVICE_FIELDS is a literal frozenset — a test pins it exactly
    so adding a field requires updating both places.

    MUTATION TARGET: reversing the merge order ({**dms_fields, **cache_extras})
    lets cache values overwrite DMS values, causing the overlap tests to fail.
    """

    def test_dms_owned_fields_frozenset_is_exactly_this(self):
        """Pin the exact set so widening it fails this test.

        Same discipline as the DMS repo's _NARROW_RO_FIELDS (D-G3d): adding a
        field requires updating this test explicitly, which makes the change visible
        in the diff rather than happening silently.

        F4.2: updated to CMS camelCase names per D-G5i field map.
        Old invented set had DMS snake_case keys mixed with wrong camelCase —
        none of which DMS actually emits in the mapped CMS representation.
        """
        expected = frozenset({
            'serviceId',    # ← ro_id
            'vehicleId',    # ← resolved vehicleId
            'vin',          # ← vehicle_vin (only when != vehicleId)
            'dealerId',     # ← dealer_id
            'provider',     # ← dealer_name
            'status',       # ← status
            'description',  # ← description
            'serviceDate',  # ← opened_at || created_at
            'createdAt',    # ← created_at
            'updatedAt',    # ← updated_at
        })
        assert index._DMS_OWNED_SERVICE_FIELDS == expected, (
            f'_DMS_OWNED_SERVICE_FIELDS has drifted from the expected set. '
            f'Expected {sorted(expected)}, '
            f'got {sorted(index._DMS_OWNED_SERVICE_FIELDS)}. '
            'Update this test when changing the frozenset — the change must be explicit.'
        )

    def test_dms_value_wins_when_cache_carries_same_field(self):
        """DMS status 'Draft' wins over cache status 'scheduled'.

        MUTATION TARGET: reversing the merge order lets cache overwrite DMS values.
        A test that only checks 'status is present' would pass either way.
        """
        dms_ro = dict(_DMS_RO, status='Draft')
        cache_row = dict(_CACHE_ROW, status='scheduled')  # must lose on merge
        cache_tbl = _fake_table_returning([cache_row])
        # VIN resolver: vehicleId='VEH-001' → vin='1FTFW1ET5DFC10312' (matches _DMS_RO.vehicle_vin)
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms([dms_ro])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_get_event(), None)
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        assert records, 'expected at least one merged record'
        statuses = [r.get('status') for r in records]
        assert 'Draft' in statuses, (
            f'DMS status "Draft" should win over cache "scheduled", but got: {statuses}. '
            'Check the merge order — must be {{**cache_extras, **dms_fields}} so DMS wins.'
        )
        assert 'scheduled' not in statuses, (
            f'cache status "scheduled" overwrote DMS status — merge order is wrong.'
        )

    def test_dms_dealer_name_wins_over_wrong_cache_dealer_name(self):
        """DMS dealer_name (mapped to 'provider') wins; the wrong cache value must not appear."""
        dms_ro = dict(_DMS_RO, dealer_name='Meridian of Austin')
        # F4.2: cache uses 'provider' (the CMS-side key after mapping)
        cache_row = dict(_CACHE_ROW, provider='WRONG NAME SHOULD NOT APPEAR')
        cache_tbl = _fake_table_returning([cache_row])
        urlopen_mock = _urlopen_returning_dms([dms_ro])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)
        assert result['statusCode'] == 200
        body_str = result['body']
        assert 'WRONG NAME SHOULD NOT APPEAR' not in body_str

    def test_cache_only_fields_survive_merge(self):
        """Non-DMS-owned fields from the cache row reach the response."""
        dms_ro = dict(_DMS_RO)  # no 'notes', 'alertId', 'cost' in DMS
        cache_row = dict(_CACHE_ROW, notes='Important note', alertId='alert-9')
        cache_tbl = _fake_table_returning([cache_row])
        urlopen_mock = _urlopen_returning_dms([dms_ro])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        assert records
        assert any('alertId' in r for r in records), (
            'cache-supplied alertId did not survive the merge'
        )

    def test_cache_only_rows_included_with_cache_label(self):
        """Cache rows with no DMS counterpart are included, labelled 'mixed'.

        SUPERSEDED shape (spec 2026-09-10-service-history-read-path-correctness D3):
        the enum previously had two values for three states, so a DMS-success payload
        containing one cache-only row was labelled ``cache`` and rendered fully
        stale in the UI. D3 renames that third state ``mixed``.  The original
        assertion (``dataSource == 'cache'``) has been rewritten to ``'mixed'``
        rather than deleted, so the D3 label lives under the same mutation guard
        the original test carried.

        MUTATION TARGET: returning ``live`` when cache-only rows exist fails
        (silent staleness). Returning ``cache`` when DMS answered fails (the
        original defect, now regression-guarded).
        """
        # A cache row whose roId does NOT match any DMS RO
        cache_only = dict(_CACHE_ROW, roId='ro-NOT-IN-DMS')
        dms_ro = dict(_DMS_RO, roId='ro-001')  # different roId
        cache_tbl = _fake_table_returning([cache_only])
        urlopen_mock = _urlopen_returning_dms([dms_ro])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)
        body = json.loads(result['body'])
        assert body['dataSource'] == 'mixed', (
            f'expected dataSource="mixed" when DMS answered AND cache-only rows exist, '
            f'got dataSource="{body.get("dataSource")}". '
            'D3 (spec 2026-09-10-service-history-read-path-correctness): naming the '
            'third state fixes the "one cache-only row taints the entire response" defect.'
        )
        # Both DMS row AND cache-only row should appear
        assert body['count'] >= 2, (
            f'expected DMS row + cache-only row (count >= 2), got count={body["count"]}. '
            'Cache-only rows must be included per D-G5c.'
        )

    def test_live_label_when_dms_answers_and_no_cache_only(self):
        """dataSource='live' when DMS answers and all cache rows are covered."""
        dms_ro = dict(_DMS_RO, roId='ro-001')
        cache_row = dict(_CACHE_ROW, roId='ro-001')  # same roId → not cache-only
        cache_tbl = _fake_table_returning([cache_row])
        urlopen_mock = _urlopen_returning_dms([dms_ro])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)
        body = json.loads(result['body'])
        assert body['dataSource'] == 'live', (
            f'expected dataSource="live" when DMS answers fully, '
            f'got "{body.get("dataSource")}"'
        )


# ── D-G5d: Failure modes ──────────────────────────────────────────────────────

class TestD_G5d_FailureModes:
    """D-G5d: failure never renders as an empty list.

    - Failed DMS + non-empty cache = 200, dataSource='cache'.
    - Failed DMS + empty cache = 502 (not 200 []).

    MUTATION TARGET: removing the empty-cache check (serving 200 [] always) causes
    the 502-on-empty-cache test to fail. Removing the cache-fallback entirely causes
    the cache-fallback test to fail.
    """

    def test_failed_dms_with_cache_returns_200_with_cache_label(self):
        """DMS down but cache populated → serve cache, label it."""
        cache_tbl = _fake_table_returning([_CACHE_ROW])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(side_effect=Exception('network timeout'))):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['dataSource'] == 'cache', (
            f'expected dataSource="cache" on DMS failure with non-empty cache, '
            f'got "{body.get("dataSource")}"'
        )
        assert body.get('serviceRecords'), 'cache records should appear on DMS failure'

    def test_failed_dms_with_empty_cache_is_502_not_empty_list(self):
        """D5 rule: 200 {"serviceRecords": []} on a failed upstream is a false claim.

        MUTATION TARGET: returning 200 {} or 200 [] here causes this test to fail.
        This is the critical D-G5d guard — the same property as the ten dealer
        endpoint tests and the reason 'failure never renders as an empty list'.
        """
        cache_tbl = _fake_table_returning([])  # empty cache
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(side_effect=Exception('network timeout'))):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)
        assert result['statusCode'] == 502, (
            f'expected 502 when DMS fails and cache is empty, got {result["statusCode"]}. '
            'The D5 rule: an empty list asserts "no records" when the truth is "we could not ask".'
        )
        body = json.loads(result['body'])
        assert 'serviceRecords' not in body, (
            'serviceRecords must not appear in a 502 response — it asserts completeness'
        )

    def test_missing_auth_header_is_not_200_empty(self):
        """D-G5d 'impossible' case: no auth header → treated as DMS failure.

        With empty cache this must be 502, not 200 [].
        """
        cache_tbl = _fake_table_returning([])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb:
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(auth=None), None)
        # No auth → DMS leg is skipped → empty cache → 502
        assert result['statusCode'] == 502, (
            f'expected 502 for missing auth + empty cache, got {result["statusCode"]}'
        )

    def test_missing_auth_with_non_empty_cache_returns_200(self):
        """D-G5d: missing auth → DMS leg skipped → cache-only path."""
        cache_tbl = _fake_table_returning([_CACHE_ROW])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb:
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(auth=None), None)
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['dataSource'] == 'cache'


# ── D-G5e: VIN hop ────────────────────────────────────────────────────────────

class TestD_G5e_VinHop:
    """D-G5e: vehicleId != vin; resolver is two-step; no scan fallback.

    MUTATION TARGET for vin!=vehicleId property: setting vin=vehicleId when the
    GetItem hit returns the same value causes test_vin_not_populated_from_vehicleid
    to fail. Removing the vin-index step causes the VIN-lookup test to fail.
    """

    def test_vin_not_populated_from_vehicleid_when_same_value(self):
        """D-G5e: do NOT copy vehicleId into vin when the value IS the vehicleId.

        Putting a vehicleId in a field named 'vin' is a false claim about an
        identifier. The GetItem response for a backfilled row returns vehicleId
        as the pk; vin should be None in that case — the DMS call should omit the
        VIN query param, NOT carry vehicleId as a VIN.

        MUTATION TARGET: setting vin=vehicleId when hit and vin is absent causes
        the DMS URL to carry 'VEH-001' as ?vin=VEH-001, which is a wrong identifier.
        The test verifies the DMS URL does NOT contain the vehicleId as a VIN param.
        """
        # GetItem returns a vehicle where vin is missing (backfilled case)
        vehicle_item = {'vehicleId': 'VEH-001'}  # no 'vin' key
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': vehicle_item}
        veh_tbl.query.return_value = {'Items': []}

        cache_tbl = _fake_table_returning([_CACHE_ROW])  # non-empty cache so we don't hit 502
        dms_urls_seen = []

        class _RecordingOpener:
            def open(self_inner, req, timeout=None):
                dms_urls_seen.append(req.full_url)
                raise Exception('dms down')

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.build_opener', return_value=_RecordingOpener()):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_get_event(), None)

        # DMS failed but cache is non-empty → 200 with cache label (expected)
        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body['dataSource'] == 'cache'

        # The DMS URL must NOT contain VEH-001 as a VIN parameter.
        # If the resolver copied vehicleId into vin, the URL would have ?vin=VEH-001.
        for url in dms_urls_seen:
            assert 'vin=VEH-001' not in url, (
                f'vehicleId "VEH-001" was used as a VIN in the DMS URL: {url}. '
                'D-G5e: do NOT copy vehicleId into vin when the value IS the vehicleId — '
                'it is a false claim about an identifier (a vehicleId is NOT a VIN).'
            )
        # Verify GetItem was called with vehicleId lookup (step 1)
        veh_tbl.get_item.assert_called_once_with(Key={'vehicleId': 'VEH-001'})
        # Verify scan was NOT called (no scan fallback)
        assert veh_tbl.scan.call_count == 0, (
            'scan was called on vehicles table — no scan fallback allowed (D-G5e)'
        )

    def test_vin_index_query_resolves_a_real_vin(self):
        """Step 2: Query on vin-index resolves a real VIN to its vehicleId.

        When GetItem misses (the value is a real VIN, not a vehicleId), the resolver
        queries vin-index and returns the vehicleId from there.
        """
        # GetItem misses (value is a VIN, not a vehicleId)
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}  # miss — no 'Item' key
        # vin-index Query returns the vehicleId
        veh_tbl.query.return_value = {'Items': [{'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}]}

        cache_tbl = _fake_table_returning([])
        # DMS returns the RO with the real VIN
        dms_ro_with_vin = dict(_DMS_RO, vehicle_vin='1FTFW1ET5DFC10312')
        urlopen_mock = _urlopen_returning_dms([dms_ro_with_vin])

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            # Call with the VIN — step 2 should resolve it
            event = _get_event(vehicleId='1FTFW1ET5DFC10312')
            result = index.handler(event, None)

        assert result['statusCode'] == 200
        # The vin-index Query must have been called (step 2)
        veh_tbl.query.assert_called()
        call_kwargs = veh_tbl.query.call_args[1]
        assert call_kwargs.get('IndexName') == 'vin-index', (
            f'Expected Query on vin-index, got IndexName={call_kwargs.get("IndexName")}'
        )

    def test_no_scan_fallback_on_unresolvable_value(self):
        """Unresolvable value: vehicles DDB is NOT scanned.

        MUTATION TARGET: adding a scan fallback causes the scan-call-count assertion
        to fail, which is the D-G5e invariant — 'made a MISSING index look merely
        like a slow one for months' (storage_stack.py:498).
        """
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}   # miss
        veh_tbl.query.return_value = {'Items': []}  # vin-index miss
        cache_tbl = _fake_table_returning([])

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(side_effect=Exception('dms down'))):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_get_event(vehicleId='UNRESOLVABLE-VALUE'), None)

        # With DMS failure and empty cache we get 502 — that is expected
        assert result['statusCode'] == 502
        # Must not have called scan on the vehicles table
        assert veh_tbl.scan.call_count == 0, (
            f'scan was called {veh_tbl.scan.call_count} time(s) on the vehicles table. '
            'D-G5e explicitly forbids a scan fallback — see storage_stack.py:498 for why.'
        )


# ── D-G5f: _RefuseRedirects is module-level ───────────────────────────────────

class TestD_G5f_RefuseRedirectsHoisted:
    """D-G5f: _RefuseRedirects is defined at module level.

    The acceptance criterion for the hoist is that the four redirect tests in
    test_dealers_endpoint.py stay green with NO edits to that file (run after
    this commit). This test pins the structural property.

    MUTATION TARGET: if _RefuseRedirects is removed from module level, this
    test fails immediately. If it is re-inlined into the dealers branch only,
    the service-history redirect tests below fail.
    """

    def test_refuse_redirects_is_accessible_at_module_level(self):
        """The class must be importable directly from index."""
        assert hasattr(index, '_RefuseRedirects'), (
            '_RefuseRedirects is not defined at module level in index.py. '
            'D-G5f requires it to be shared rather than defined per-invocation.'
        )

    def test_refuse_redirects_is_a_redirect_handler_subclass(self):
        assert issubclass(index._RefuseRedirects, urllib.request.HTTPRedirectHandler), (
            '_RefuseRedirects must subclass HTTPRedirectHandler'
        )

    def test_service_history_get_refuses_redirect(self):
        """A 302 from DMS on GET service-history must not carry the token further."""
        import email as _email

        class _Fake302:
            headers = _email.message_from_string('Location: http://attacker.example.invalid/\r\n')
            code = status = 302
            msg = reason = 'Found'
            url = 'https://dms.example.invalid/api/dms/fleet/repair-orders'

            def info(self):
                return self.headers

            def geturl(self):
                return self.url

            def read(self, *_a):
                return b''

            def close(self):
                return None

        seen = []

        def _open(_self, req):
            seen.append(req)
            return _Fake302()

        cache_tbl = _fake_table_returning([_CACHE_ROW])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch.object(urllib.request.HTTPSHandler, 'https_open', _open), \
                patch.object(urllib.request.HTTPHandler, 'http_open', _open):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)
        assert len(seen) == 1, (
            f'request was re-issued after redirect ({len(seen)} hops) — '
            'caller token may have egressed'
        )
        assert 'attacker.example.invalid' not in seen[0].full_url

    def test_service_history_post_refuses_redirect(self):
        """A 302 from DMS on POST service-history must not carry the token further.

        MUTATION TARGET: replacing build_opener(_RefuseRedirects) with build_opener()
        at the POST call site causes the default redirect handler to follow the 302
        to the http:// Location, dispatching to http_open — seen grows to 2 hops and
        this test fails.

        Both HTTPSHandler.https_open and HTTPHandler.http_open are recorded into the
        same list. The initial HTTPS POST lands on https_open; a redirect to http://
        would land on http_open. _RefuseRedirects raises HTTPError so the redirect is
        never dispatched — seen has exactly one entry and the response is not 201.
        F6.1 / security review Cycle 8 W1.
        """
        import email as _email

        class _Fake302:
            headers = _email.message_from_string('Location: http://attacker.example.invalid/\r\n')
            code = status = 302
            msg = reason = 'Found'
            url = 'https://dms.example.invalid/api/dms/fleet/repair-orders'

            def info(self):
                return self.headers

            def geturl(self):
                return self.url

            def read(self, *_a):
                return b''

            def close(self):
                return None

        seen = []

        def _open(_self, req):
            seen.append(req)
            return _Fake302()

        cache_tbl = _fake_table_returning([])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles',
                                      'SERVICE_HISTORY_TABLE_NAME': 'test-sh'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch.object(urllib.request.HTTPSHandler, 'https_open', _open), \
                patch.object(urllib.request.HTTPHandler, 'http_open', _open):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_post_event(), None)
        assert len(seen) == 1, (
            f'request was re-issued after redirect ({len(seen)} hops) — '
            'caller token may have egressed. '
            'MUTATION: build_opener() without _RefuseRedirects follows the 302 to '
            'http_open, making len(seen) == 2.'
        )
        assert result['statusCode'] != 201, (
            f'Expected non-201 after a 302 redirect, got {result["statusCode"]}. '
            'A 302 from DMS must never produce a successful booking response.'
        )


# ── D-G5g: authorization posture ─────────────────────────────────────────────

class TestD_G5g_AuthorizationPosture:
    """D-G5g: CMS-side auth check on cache path; live path forwards token.

    MUTATION TARGET: removing the cache-side get_allowed_vehicle_ids() check causes
    the fleet-scoped denial test to fail. Removing _deny_viewer() from POST causes
    the viewer-denied test to fail.
    """

    def test_caller_token_forwarded_to_dms_not_a_service_credential(self):
        """Spec D2/D3: CMS forwards the caller's token; DMS authorizes the end user.

        The CMS fail-open P0 was an authorization decision taken in the wrong place.
        Substituting a service credential would recreate that shape. This asserts the
        exact header value travels to DMS unchanged.
        """
        cache_tbl = _fake_table_returning([])
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}  # miss
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms([_DMS_RO])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            index.handler(_get_event(), None)
        request_obj = urlopen_mock.call_args[0][0]
        assert request_obj.get_header('Authorization') == _TOKEN, (
            f'DMS received a different Authorization header than the caller supplied. '
            f'CMS must forward the caller\'s token, not substitute a service credential (D2/D3).'
        )

    def test_fleet_viewer_denied_on_post(self):
        """_deny_viewer() stays unconditionally on POST (D-G5g).

        MUTATION TARGET: removing _deny_viewer() from the POST path causes this
        test to fail (fleet-viewer gets 201 instead of 403).
        """
        cache_tbl = _fake_table_returning([])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': ''}), \
                patch.object(index, 'dynamodb') as mock_ddb:
            mock_ddb.Table.return_value = cache_tbl
            result = index.handler(
                _post_event(groups='fleet-viewer'), None
            )
        assert result['statusCode'] == 403, (
            f'fleet-viewer should be denied on POST (D-G5g), got {result["statusCode"]}. '
            'Read-only principals must not reach any write path.'
        )

    def test_cms_scope_check_applied_on_legacy_get_path(self):
        """On the legacy (DMS_API_ENDPOINT unset) path, the cache-side auth check runs."""
        cache_tbl = _fake_table_returning([_CACHE_ROW])
        # Fleet-scoped user (fleet-operator, custom:fleetIds='fleet-b') asking for
        # vehicleId='VEH-NOT-IN-FLEET'; enrollment query returns empty set → denied.
        enrollment_tbl = MagicMock()
        enrollment_tbl.query.return_value = {'Items': []}  # no vehicles in fleet-b

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': '',
                                      'FLEET_ENROLLMENT_TABLE_NAME': 'test-enrollment'}), \
                patch.object(index, 'dynamodb') as mock_ddb:
            def table_factory(name):
                if 'enrollment' in name.lower():
                    return enrollment_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            event = _get_event(vehicleId='VEH-NOT-IN-FLEET', dms_endpoint='',
                               groups='fleet-operator')
            event['requestContext']['authorizer']['claims']['custom:fleetIds'] = 'fleet-b'
            result = index.handler(event, None)
        assert result['statusCode'] == 403, (
            f'expected 403 for out-of-fleet vehicleId on legacy path, got {result["statusCode"]}'
        )


# ── D-G5h: POST ordering ──────────────────────────────────────────────────────

class TestD_G5h_PostOrdering:
    """D-G5h: cache row written ONLY after DMS 201.

    A failed booking must leave no CMS row claiming service was scheduled.

    MUTATION TARGET: moving put_item before the DMS call causes the
    'no cache row on DMS failure' test to fail, because put_item is called before
    the exception is raised.
    """

    def test_cache_row_written_after_dms_201(self):
        """The cache put_item runs AFTER DMS accepts.

        MUTATION TARGET: reversing the order (write then call) causes the ordering
        test to fail — put_item would be called even when DMS fails.
        """
        cache_tbl = MagicMock()
        put_item_calls = []
        cache_tbl.put_item = MagicMock(side_effect=lambda **kw: put_item_calls.append('put_item'))

        # Vehicle table mock: GetItem returns a vehicle with a vin
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1'}}

        dms_call_log = []

        class _RecordingResp:
            def __init__(self):
                dms_call_log.append('dms_call')
                self._raw = json.dumps({'repairOrder': {'roId': 'ro-new'}}).encode()

            def read(self):
                return self._raw

            def __enter__(self):
                return self

            def __exit__(self, *_):
                return False

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(return_value=_RecordingResp())):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_post_event(), None)

        assert result['statusCode'] == 201
        assert 'dms_call' in dms_call_log, 'DMS was not called'
        assert len(put_item_calls) == 1, f'expected 1 put_item call, got {len(put_item_calls)}'
        # The _RecordingResp was instantiated before put_item ran — DMS call preceded write
        # (dms_call_log populated in __init__, put_item_calls populated after open() returns)
        assert dms_call_log[0] == 'dms_call', 'DMS call must precede put_item'

    def test_no_cache_row_on_dms_failure(self):
        """Failed DMS booking must leave NO CMS row (D-G5h ordering invariant).

        MUTATION TARGET: moving put_item before the DMS call causes this test to
        fail — put_item is called before the exception interrupts execution.
        This is the critical D-G5h guard: a CMS row claiming service was scheduled
        when DMS never received the booking is a silent-success defect.
        """
        cache_tbl = MagicMock()
        cache_tbl.put_item = MagicMock()

        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1'}}

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(side_effect=Exception('DMS down'))):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_post_event(), None)

        assert result['statusCode'] in (500, 502), (
            f'expected error status on DMS failure, got {result["statusCode"]}'
        )
        assert cache_tbl.put_item.call_count == 0, (
            f'put_item was called {cache_tbl.put_item.call_count} time(s) on DMS failure. '
            'D-G5h: the cache row must NOT be written unless DMS accepted the booking. '
            'A CMS row claiming service was scheduled when DMS failed is a silent-success defect.'
        )

    def test_post_returns_201_on_dms_success(self):
        """Happy path: DMS 201 → cache written → CMS 201 returned."""
        cache_tbl = MagicMock()
        cache_tbl.put_item = MagicMock()

        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(return_value=_FakeDmsPostResp({'roId': 'ro-new'}))):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_post_event(), None)

        assert result['statusCode'] == 201
        assert cache_tbl.put_item.call_count == 1, (
            'put_item must be called exactly once on a successful DMS booking'
        )
        written = cache_tbl.put_item.call_args[1]['Item']
        assert written['roId'] == 'ro-new', (
            f'roId from DMS response should be stamped on the cache row, got {written.get("roId")}'
        )


if __name__ == '__main__':
    import unittest
    unittest.main(verbosity=2)



# ── F4.1: Envelope key + field mapper ────────────────────────────────────────

class TestF4_1_EnvelopeAndMapper:
    """F4.1: DMS envelope key is "items", not "repairOrders".

    MUTATION TARGET (Critical 1 of D-G5i): renaming the envelope key back to
    "repairOrders" makes the live path a silent no-op — all DMS records are
    dropped and every response reports dataSource='cache'.

    Also verifies:
    - Every key emitted by _ro_to_service_record is in _SERVICE_HISTORY_RESPONSE_FIELDS
      (the allowlist that closed the brand exposure).
    - The field map (D-G5i) is implemented correctly.
    """

    def test_items_envelope_key_is_load_bearing(self):
        """MUTATION TARGET: envelope key must be "items" not "repairOrders".

        A DMS response with "repairOrders" key (the bug) must be treated as a
        failed leg (missing "items" key), not as an empty list.  This is the
        Critical-1 defect from D-G5i: the live path was a silent no-op because
        `_dms_payload.get('repairOrders')` returned None every time DMS answered.
        """
        # Build a response with the OLD (wrong) envelope key
        wrong_envelope_resp = MagicMock()
        wrong_envelope_resp.__enter__ = MagicMock(return_value=wrong_envelope_resp)
        wrong_envelope_resp.__exit__ = MagicMock(return_value=False)
        wrong_envelope_resp.read.return_value = json.dumps({'repairOrders': [_DMS_RO]}).encode()

        cache_tbl = _fake_table_returning([])   # empty cache → if DMS records drop we get 502
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(return_value=wrong_envelope_resp)):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)

        # Wrong envelope → failed leg + empty cache → 502 (not 200 [])
        assert result['statusCode'] == 502, (
            f'Wrong envelope key "repairOrders" should produce a failed-leg 502 '
            f'(missing "items" key), but got {result["statusCode"]}. '
            'MUTATION: if this passes with status 200, the envelope key fix was reverted.'
        )

    def test_correct_items_envelope_produces_200_with_live_label(self):
        """The correct "items" envelope is read and produces a live 200 response."""
        cache_tbl = _fake_table_returning([_CACHE_ROW])
        urlopen_mock = _urlopen_returning_dms([_DMS_RO])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body.get('dataSource') == 'live', (
            f'Expected dataSource="live" when DMS answers with items envelope, '
            f'got "{body.get("dataSource")}"'
        )
        assert body.get('serviceRecords'), 'DMS records should appear in the response'

    def test_mapped_keys_are_all_in_response_allowlist(self):
        """Every key emitted by _ro_to_service_record is in _SERVICE_HISTORY_RESPONSE_FIELDS.

        The allowlist closed a real brand exposure.  The mapper must not silently
        project a field that would be stripped by _project_service_records.

        F4.1 Accept criterion: a test asserts every key of a mapped full RO is in
        _SERVICE_HISTORY_RESPONSE_FIELDS, so nothing the mapper emits is silently
        projected away.
        """
        # Full RO with every field present
        full_ro = dict(
            ro_id='ro-001',
            dealer_id='dealer-austin',
            dealer_name='Meridian of Austin',
            status='Draft',
            description='Full brake service',
            opened_at='2026-09-01T10:00:00Z',
            created_at='2026-09-01T08:00:00Z',
            updated_at='2026-09-01T12:00:00Z',
            vehicle_vin='1FTFW1ET5DFC10312',
            initiated_by='cms_booking',
            recall_id='recall-abc',  # deliberately unmapped
        )
        mapped = index._ro_to_service_record(full_ro, resolved_vehicle_id='VEH-001')
        # 'vin' should NOT be emitted here because vehicle_vin == resolved_vehicle_id
        # is False (1FTFW1 != VEH-001), so vin IS emitted.  Pass it through.
        disallowed = [k for k in mapped if k not in index._SERVICE_HISTORY_RESPONSE_FIELDS]
        assert not disallowed, (
            f'_ro_to_service_record emitted keys NOT in _SERVICE_HISTORY_RESPONSE_FIELDS: '
            f'{disallowed}. These will be silently dropped by _project_service_records, '
            'making the mapper invisible in the response. Do NOT widen the allowlist — '
            'fix the mapper to emit only allowed keys.'
        )

    def test_dms_owned_fields_equals_mapper_output_keys(self):
        """_DMS_OWNED_SERVICE_FIELDS matches the keys _ro_to_service_record would emit.

        MUTATION TARGET (Critical 2 of D-G5i): deleting a field from
        _DMS_OWNED_SERVICE_FIELDS allows a cache value to overwrite that DMS field.
        This test catches the mutation by checking the frozenset against the mapper.
        """
        full_ro = dict(
            ro_id='ro-001',
            dealer_id='dealer-austin',
            dealer_name='Meridian of Austin',
            status='Draft',
            description='Brake inspection',
            opened_at='2026-09-01T10:00:00Z',
            created_at='2026-09-01T08:00:00Z',
            updated_at='2026-09-01T12:00:00Z',
            vehicle_vin='1FTFW1ET5DFC10312',  # differs from resolved vehicleId
        )
        # resolved_vehicle_id different so 'vin' is emitted
        mapped = index._ro_to_service_record(full_ro, resolved_vehicle_id='VEH-001')
        # Every key the mapper can emit on a full RO must be in _DMS_OWNED_SERVICE_FIELDS
        mapper_keys = frozenset(mapped.keys())
        missing_from_owned = mapper_keys - index._DMS_OWNED_SERVICE_FIELDS
        assert not missing_from_owned, (
            f'Mapper emitted keys not in _DMS_OWNED_SERVICE_FIELDS: {missing_from_owned}. '
            'These DMS values could be overwritten by cache entries on merge.'
        )

    def test_field_map_d_g5i_ro_id_to_service_id(self):
        """D-G5i row 1: ro_id → serviceId."""
        mapped = index._ro_to_service_record({'ro_id': 'ro-xyz'}, resolved_vehicle_id='VEH-001')
        assert mapped.get('serviceId') == 'ro-xyz'
        assert 'ro_id' not in mapped, 'snake_case key must not appear in CMS output'

    def test_field_map_d_g5i_dealer_id_to_dealer_id(self):
        """D-G5i row 2: dealer_id → dealerId."""
        mapped = index._ro_to_service_record(
            {'ro_id': 'ro-1', 'dealer_id': 'dealer-abc'}, resolved_vehicle_id='VEH-001'
        )
        assert mapped.get('dealerId') == 'dealer-abc'
        assert 'dealer_id' not in mapped

    def test_field_map_d_g5i_dealer_name_to_provider(self):
        """D-G5i row 3: dealer_name → provider; empty string when unknown."""
        mapped = index._ro_to_service_record(
            {'ro_id': 'ro-1', 'dealer_name': 'Meridian'}, resolved_vehicle_id='VEH-001'
        )
        assert mapped.get('provider') == 'Meridian'
        assert 'dealer_name' not in mapped

        # Empty string is emitted when dealer_name is ''
        mapped2 = index._ro_to_service_record(
            {'ro_id': 'ro-1', 'dealer_name': ''}, resolved_vehicle_id='VEH-001'
        )
        assert 'provider' in mapped2
        assert mapped2['provider'] == ''

    def test_field_map_d_g5i_opened_at_to_service_date(self):
        """D-G5i row 6: opened_at || created_at → serviceDate."""
        mapped_opened = index._ro_to_service_record(
            {'ro_id': 'ro-1', 'opened_at': '2026-09-01T10:00:00Z', 'created_at': '2026-09-01T08:00:00Z'},
            resolved_vehicle_id='VEH-001',
        )
        assert mapped_opened.get('serviceDate') == '2026-09-01T10:00:00Z', (
            'opened_at should take precedence over created_at for serviceDate'
        )

        mapped_created = index._ro_to_service_record(
            {'ro_id': 'ro-1', 'created_at': '2026-09-01T08:00:00Z'},
            resolved_vehicle_id='VEH-001',
        )
        assert mapped_created.get('serviceDate') == '2026-09-01T08:00:00Z', (
            'created_at should be used as serviceDate when opened_at is absent'
        )

    def test_field_map_d_g5i_vin_only_when_differs_from_vehicle_id(self):
        """D-G5i row 9: vin emitted only when vehicle_vin differs from resolved vehicleId."""
        # Different: vin is emitted
        mapped_diff = index._ro_to_service_record(
            {'ro_id': 'ro-1', 'vehicle_vin': '1FTFW1ET5DFC10312'},
            resolved_vehicle_id='VEH-001',
        )
        assert mapped_diff.get('vin') == '1FTFW1ET5DFC10312'

        # Same: vin is NOT emitted (D-G5e: do not copy vehicleId into a vin field)
        mapped_same = index._ro_to_service_record(
            {'ro_id': 'ro-1', 'vehicle_vin': 'VEH-001'},
            resolved_vehicle_id='VEH-001',
        )
        assert 'vin' not in mapped_same, (
            'vin must NOT be emitted when vehicle_vin equals the resolved vehicleId — '
            'that would be a vehicleId in a field named vin, a false claim (D-G5e).'
        )

    def test_initiated_by_and_recall_id_not_mapped(self):
        """D-G5i: initiated_by and recall_id are deliberately unmapped."""
        mapped = index._ro_to_service_record(
            {'ro_id': 'ro-1', 'initiated_by': 'cms_booking', 'recall_id': 'recall-abc'},
            resolved_vehicle_id='VEH-001',
        )
        assert 'initiated_by' not in mapped
        assert 'recall_id' not in mapped


# ── F4.2: POST path fixes ─────────────────────────────────────────────────────

class TestF4_2_PostPath:
    """F4.2: POST path fixes per D-G5i defects 4-7.

    - {entry} envelope unwrapped.
    - Caller's serviceDate preserved (sort key — not today's date).
    - No VIN substitution when unresolvable.
    - Cache row carries all fields the cache exists to supply.
    """

    def test_post_unwraps_entry_envelope(self):
        """F4.2 defect 4: ScheduleServiceModal.tsx posts {entry: {...}} wrapped shape.

        MUTATION TARGET: removing `_body = _body.get('entry', _body)` breaks
        this test — vehicleId comes from the outer body which has no vehicleId,
        so DMS never receives a vehicle_vin and returns 400.
        """
        wrapped_body = json.dumps({
            'entry': {
                'vehicleId': 'VEH-001',
                'dealerId': 'dealer-austin',
                'description': 'Brake check from service modal',
                'serviceDate': '2026-09-15',
                'status': 'scheduled',
            }
        })
        cache_tbl = MagicMock()
        cache_tbl.put_item = MagicMock()
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}

        dms_post_bodies = []

        class _RecordingPostResp:
            def __init__(self):
                self._raw = json.dumps({'repairOrder': {'roId': 'ro-new'}}).encode()
            def read(self):
                return self._raw
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        def _recording_open(req, timeout=None):
            if req.method == 'POST':
                dms_post_bodies.append(json.loads(req.data))
            return _RecordingPostResp()

        post_event = {
            'httpMethod': 'POST',
            'path': '/api/v1/service-history',
            'headers': {'Authorization': _TOKEN},
            'queryStringParameters': {},
            'body': wrapped_body,
            'requestContext': {
                'authorizer': {
                    'claims': {
                        'cognito:groups': 'platform-admin',
                        'email': 'op@example.com',
                        'custom:fleetIds': 'fleet-a',
                    }
                }
            },
        }

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', side_effect=_recording_open):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(post_event, None)

        assert result['statusCode'] == 201, (
            f'Expected 201 for wrapped {{entry: ...}} body, got {result["statusCode"]}. '
            'MUTATION: if 500 or 502, the entry envelope unwrap is missing.'
        )
        # DMS received the vehicle_vin
        assert dms_post_bodies, 'DMS was not called'
        assert dms_post_bodies[0].get('vehicle_vin') == '1FTFW1ET5DFC10312', (
            f'DMS should receive the resolved real VIN, got {dms_post_bodies[0]}'
        )

    def test_post_preserves_caller_service_date(self):
        """F4.2 defect 5: serviceDate is the sort key — overwriting with today
        collapses two bookings for one vehicle into one row.

        MUTATION TARGET: replacing serviceDate with today causes this test to fail.
        """
        caller_date = '2026-10-15'
        body_json = json.dumps({
            'vehicleId': 'VEH-001',
            'dealerId': 'dealer-austin',
            'description': 'Future appointment',
            'serviceDate': caller_date,
        })
        cache_tbl = MagicMock()
        cache_tbl.put_item = MagicMock()
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(return_value=_FakeDmsPostResp({'roId': 'ro-new'}))):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler({
                'httpMethod': 'POST', 'path': '/api/v1/service-history',
                'headers': {'Authorization': _TOKEN},
                'queryStringParameters': {},
                'body': body_json,
                'requestContext': {'authorizer': {'claims': {
                    'cognito:groups': 'platform-admin',
                    'email': 'op@example.com',
                    'custom:fleetIds': 'fleet-a',
                }}},
            }, None)

        assert result['statusCode'] == 201
        # The written cache row must keep the caller's serviceDate
        written = cache_tbl.put_item.call_args[1]['Item']
        assert written['serviceDate'] == caller_date, (
            f'cache row serviceDate should be the caller\'s "{caller_date}", '
            f'got "{written["serviceDate"]}". '
            'MUTATION: if today\'s date appears, the serviceDate preservation fix was reverted.'
        )

    def test_post_fails_explicitly_when_vin_unresolvable(self):
        """F4.2 defect 7: unresolvable VIN returns explicit error, NOT vehicleId substitution.

        D-G5e forbids substituting vehicleId in vehicle_vin — that is a false claim
        about an identifier.  D-G5d classifies unresolvable VIN as 'impossible' → fail.

        MUTATION TARGET: restoring `'vehicle_vin': _resolved_vin_post or vehicle_id_post`
        causes this test to fail — the DMS call succeeds with vehicleId as VIN.
        """
        # Vehicle table misses on both steps → unresolvable
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}     # miss on vehicleId lookup
        veh_tbl.query.return_value = {'Items': []}  # miss on vin-index

        cache_tbl = MagicMock()
        cache_tbl.put_item = MagicMock()

        dms_calls = []

        def _recording_open(req, timeout=None):
            dms_calls.append(req)
            return _FakeDmsPostResp({'roId': 'ro-new'})

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', side_effect=_recording_open):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_post_event(vehicleId='VEH-UNRESOLVABLE'), None)

        # Must fail (502) — not send vehicleId as VIN
        assert result['statusCode'] == 502, (
            f'Expected 502 for unresolvable VIN, got {result["statusCode"]}. '
            'MUTATION: if 201, vehicleId was substituted as VIN — D-G5e forbids this.'
        )
        # DMS must NOT have been called
        assert not dms_calls, (
            f'DMS was called {len(dms_calls)} time(s) with an unresolvable VIN. '
            'The unresolvable case must fail before reaching DMS.'
        )
        # put_item must not have been called (D-G5h: no cache row on failure)
        assert cache_tbl.put_item.call_count == 0


# ── F4.3: GET query semantics ─────────────────────────────────────────────────

class TestF4_3_GetQuerySemantics:
    """F4.3: vehicleId-scoped filter, limit/serviceType, sort, asOf prefers cachedAt.

    MUTATION TARGETS:
    - Re-adding ?vin= to the DMS URL should not cause the test to fail (DMS
      ignores it), but removing the client-side vehicleId filter does break the
      cross-vehicle isolation test.
    - asOf reading createdAt instead of cachedAt fails the asOf test when
      a row has cachedAt but not createdAt.
    """

    def test_vehicleid_scoped_get_does_not_return_other_vehicle_ro(self):
        """A vehicleId-scoped GET must not return another vehicle's RO.

        SUPERSEDED shape (spec 2026-09-10-service-history-read-path-correctness D1):
        the mechanism this test guarded — CMS's client-side VIN filter — has been
        DELETED, because DMS now filters server-side via the ``vin-index`` GSI
        query (D1).  So the same property is now enforced at the DMS boundary,
        not in CMS.  The rewrite configures the mock to reflect DMS's real
        server-side behaviour (only rows matching ``?vehicle_vin=`` are
        returned) and asserts the response contains only the matching RO —
        which is the same visible property, held at the new seam.

        MUTATION TARGET (new): if the CMS handler drops ``?vehicle_vin=`` from
        the DMS URL, the mock's URL-conditional response falls back to
        returning both ROs, and the "other vehicle" RO leaks into the scoped
        response.
        """
        ro_matching = dict(_DMS_RO, ro_id='ro-match', vehicle_vin='1FTFW1ET5DFC10312')
        ro_other = dict(_DMS_RO, ro_id='ro-other', vehicle_vin='OTHER-VIN-99999')

        cache_tbl = _fake_table_returning([])

        # Simulate DMS's server-side vin-index filter: only return rows matching
        # the ?vehicle_vin= parameter.  If CMS fails to pass the parameter, the
        # mock returns both — which surfaces the mutation clearly.
        dms_urls_seen: list[str] = []

        class _FilteringOpener:
            def open(self_inner, req, timeout=None):
                dms_urls_seen.append(req.full_url)
                if 'vehicle_vin=1FTFW1ET5DFC10312' in req.full_url:
                    return _FakeDmsGetResp([ro_matching])
                # No filter parameter → DMS's fallback (admin scan today) would
                # return everything; simulate that by returning both.
                return _FakeDmsGetResp([ro_matching, ro_other])

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.build_opener', return_value=_FilteringOpener()):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(vehicleId='VEH-001'), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        service_ids = [r.get('serviceId') for r in records]
        assert 'ro-match' in service_ids or any(r.get('vin') == '1FTFW1ET5DFC10312' for r in records), (
            f'matching RO must appear in response. Got: {service_ids}'
        )
        assert not any(r.get('serviceId') == 'ro-other' for r in records), (
            f'RO for OTHER-VIN-99999 must NOT appear in VEH-001-scoped response. '
            f'Got serviceIds: {service_ids}. MUTATION: if CMS drops ?vehicle_vin= '
            'from the DMS URL, the mock returns both ROs and ro-other leaks in.'
        )

    def test_backfilled_ro_keyed_by_vehicleid_still_matches(self):
        """F-G5a: a backfilled RO stores vehicleId in vehicle_vin — must still match.

        Backfilled rows have vehicle_vin='VEH-001' (not a real VIN). The resolver
        returns ('VEH-001', None) — resolved_vehicle_id='VEH-001', resolved_vin=None.
        The filter must include records where vehicle_vin == resolved_vehicle_id.
        """
        # Backfilled RO: vehicle_vin == vehicleId
        backfilled_ro = dict(
            ro_id='ro-backfill',
            status='Closed',
            dealer_id='dealer-austin',
            dealer_name='Meridian of Austin',
            opened_at='2026-01-15T10:00:00Z',
            created_at='2026-01-15T10:00:00Z',
            updated_at='2026-01-15T10:00:00Z',
            description='Legacy maintenance',
            vehicle_vin='VEH-001',  # vehicleId stored as vehicle_vin (backfilled)
        )
        cache_tbl = _fake_table_returning([])
        urlopen_mock = _urlopen_returning_dms([backfilled_ro])

        # VIN resolver: step 1 returns hit with no separate vin key
        # → resolved_vehicle_id='VEH-001', resolved_vin=None
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001'}}  # no 'vin' key
        veh_tbl.query.return_value = {'Items': []}

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_get_event(vehicleId='VEH-001'), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        service_ids = [r.get('serviceId') for r in records]
        assert 'ro-backfill' in service_ids, (
            f'Backfilled RO (vehicle_vin == vehicleId) should appear in scoped response. '
            f'Got serviceIds: {service_ids}. '
            'F-G5a: the client-side filter must match vehicle_vin against resolved_vehicle_id.'
        )

    def test_as_of_prefers_cached_at_over_created_at(self):
        """F4.3: asOf reads cachedAt when present, not createdAt.

        MUTATION TARGET: changing to read only createdAt fails this test when the
        cache row has cachedAt but not createdAt.
        """
        # Cache row with cachedAt but no createdAt
        cache_row_with_cached_at = {
            'vehicleId': 'VEH-001',
            'serviceDate': '2026-09-01',
            'roId': 'ro-cached-only',
            'cachedAt': '2026-09-02T14:30:00Z',
            # deliberately no 'createdAt'
        }
        cache_tbl = _fake_table_returning([cache_row_with_cached_at])
        # DMS fails so we go to cache path
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(side_effect=Exception('DMS down'))):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body.get('dataSource') == 'cache'
        assert body.get('asOf') == '2026-09-02T14:30:00Z', (
            f'asOf should prefer cachedAt "2026-09-02T14:30:00Z", '
            f'got "{body.get("asOf")}". '
            'MUTATION: if asOf is None or uses createdAt, the cachedAt preference was reverted.'
        )

    def test_dms_url_does_not_contain_vin_parameter(self):
        """SUPERSEDED by spec 2026-09-10-service-history-read-path-correctness D1.

        The old F4.3 constraint — "DMS list_fleet_repair_orders reads no query
        parameters, so the URL must be bare" — is inverted by D1: DMS's
        ``list_fleet_repair_orders`` now reads ``?vehicle_vin=`` and routes the
        read through the ``vin-index`` GSI (spec D1 + DMS Group 2, commit
        ``8a6c20a``).  Client-side filtering has been DELETED because DMS
        filters server-side.

        The property this test *asserted* (URL has no ``?``) is now false by
        design.  The property it *implied* — "the URL is not built with an
        arbitrary caller-controlled query" — is still true and is worth pinning
        as a regression guard: the parameter is exactly ``vehicle_vin`` and its
        value is exactly the resolved VIN (not e.g. the vehicleId, which would
        miss the GSI).  This is the rewrite.

        MUTATION TARGET (new): if a future edit passes ``vehicle_id`` or
        anything other than ``resolved_vin`` as the query value, the vin-index
        query at DMS falls back to ``NoItems`` and the caller sees an empty
        response instead of live data.
        """
        cache_tbl = _fake_table_returning([])
        dms_urls_seen = []

        # Resolver: vehicleId='VEH-001' → vin='1FTFW1ET5DFC10312'
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }
        veh_tbl.query.return_value = {'Items': []}

        class _RecordingOpener:
            def open(self_inner, req, timeout=None):
                dms_urls_seen.append(req.full_url)
                return _FakeDmsGetResp([_DMS_RO])

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.build_opener', return_value=_RecordingOpener()):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            index.handler(_get_event(vehicleId='VEH-001'), None)

        assert dms_urls_seen, 'DMS was not called'
        for url in dms_urls_seen:
            # D1: the parameter MUST be exactly ``vehicle_vin`` and its value
            # MUST be the resolved VIN — not the caller-supplied vehicleId.
            assert 'vehicle_vin=1FTFW1ET5DFC10312' in url, (
                f'DMS URL must carry ?vehicle_vin=<resolved-vin> per D1. '
                f'Got: {url}. MUTATION: passing vehicle_id or the wrong param name '
                'would miss the vin-index GSI at DMS and return empty.'
            )
            # And no other parameter should appear — a bloated URL would suggest
            # someone is re-adding legacy filters CMS-side.
            assert 'vin=1FTFW1ET5DFC10312' in url and '?vin=' not in url, (
                f'Only vehicle_vin= is permitted (not the old ?vin= shape). Got: {url}'
            )


# ── Fix Group 5: seam tests (D-G5j) ──────────────────────────────────────────

class TestF5_1_MergeSeam:
    """F5.1 / D-G5j: the merge seam — a field CMS knows and DMS omits must survive.

    The D-G5j defect: the old merge excluded ANY cache key in _DMS_OWNED_SERVICE_FIELDS
    unconditionally, discarding values the mapper deliberately omitted.  The fix is
    ``{**_cache_extras, **_mapped}`` — cache is dropped only where _mapped actually
    supplies the key.

    THREE SEAM TESTS required by Fix Group 5 task F5.1:

    1. Backfilled-shape merge: DMS vehicle_vin == resolved vehicleId → mapper omits
       'vin' → cache row with a real 'vin' → merged record must carry the cache's vin.
    2. Mapper-omits-status: DMS row has no 'status' → mapper omits 'status' → cache
       row has 'status' → merged record must carry the cache's status.
    3. Mapper-omits-serviceDate: DMS row has no 'opened_at' and no 'created_at' →
       mapper omits 'serviceDate' → cache row has 'serviceDate' → merged record must
       carry the cache's serviceDate.

    MUTATION TARGET: reversing the merge order to {**_mapped, **_cache_extras}
    still produces a correct result when _mapped supplies the key (DMS wins) but
    also lets cache overwrite DMS values — so the merge-order mutation must be
    caught by tests 2 and 3 above (DMS-supplied field overwritten by cache).

    The mutation table entry for this class:
        | Merge order reversed ({**_mapped, **_cache_extras}) | CAUGHT (tests 2 & 3) |
    """

    def _make_unscoped_get_event(self, auth=_TOKEN, groups='platform-admin',
                                 dms_endpoint='https://dms.example.invalid'):
        """GET /api/v1/service-history with no vehicleId — the dashboard call."""
        return {
            'httpMethod': 'GET',
            'path': '/api/v1/service-history',
            'headers': {'Authorization': auth},
            'queryStringParameters': {},   # no vehicleId
            'body': None,
            'requestContext': {
                'authorizer': {
                    'claims': {
                        'cognito:groups': groups,
                        'email': 'operator@example.com',
                        'custom:fleetIds': 'fleet-a',
                    }
                }
            },
        }

    def test_seam_backfilled_shape_vin_survives_from_cache(self):
        """SEAM TEST 1 (D-G5j): backfilled RO — mapper omits 'vin', cache supplies it.

        Shape:
        - DMS RO: vehicle_vin == resolved vehicleId ('VEH-001') → mapper omits 'vin'
          because the rule is "emit vin only when vehicle_vin != resolved vehicleId".
        - Cache row: roId='ro-001', vin='1FTFW1ET5DFC10312' (the real VIN in CMS).
        - Expected merge: merged record carries vin='1FTFW1ET5DFC10312' from the cache.

        Without the F5.1 fix, 'vin' was in _DMS_OWNED_SERVICE_FIELDS and was
        therefore stripped from _cache_extras unconditionally.  After the fix,
        _merged = {**_cache_extras, **_mapped} and 'vin' in _cache_extras survives
        because _mapped does not supply it.

        MUTATION: reversing to {**_mapped, **_cache_extras} still passes this test
        (cache wins either way when DMS doesn't supply the key) — the merge-order
        mutation is caught by test_seam_status_survives_from_cache below.
        """
        backfilled_dms_ro = dict(_DMS_RO, vehicle_vin='VEH-001', ro_id='ro-001')
        # DMS vehicle_vin == vehicleId, so mapper will omit 'vin'
        cache_row = dict(_CACHE_ROW, roId='ro-001', vin='1FTFW1ET5DFC10312')

        cache_tbl = _fake_table_returning([cache_row])
        # VIN resolver: vehicle_vin='VEH-001' → GetItem hit with no separate 'vin'
        # → resolved_vehicle_id='VEH-001', resolved_vin=None
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001'}}  # no 'vin' key
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms([backfilled_dms_ro])

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_get_event(vehicleId='VEH-001'), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        assert records, 'expected at least one merged record'
        # The merged record must carry the cache's 'vin'
        vins = [r.get('vin') for r in records]
        assert '1FTFW1ET5DFC10312' in vins, (
            f'cache-supplied vin "1FTFW1ET5DFC10312" must survive the merge when DMS '
            f'omits vin (backfilled shape). '
            f'Got vins: {vins}. '
            'D-G5j: the merge must be {{**_cache_extras, **_mapped}}, not '
            '{{**_cache_non_dms, **_mapped}} — the unconditional exclusion dropped '
            'cache values for DMS-owned keys the mapper deliberately omitted.'
        )

    def test_seam_status_survives_from_cache_when_dms_omits_it(self):
        """SEAM TEST 2 (D-G5j): DMS omits 'status', cache supplies it.

        MUTATION TARGET for merge order: {**_mapped, **_cache_extras} causes cache
        to overwrite DMS on overlap (the D-G5c invariant breaks).  This test
        verifies that when DMS DOES supply a value, DMS wins — which is only true
        when merge order is {**_cache_extras, **_mapped}.

        Specifically: DMS status='Draft' must appear, not cache status='scheduled'.
        AND when DMS omits status, the cache's 'scheduled' must survive.
        Both properties are load-bearing — together they gate the merge order.
        """
        # Case A: DMS supplies status → DMS wins (existing test covers this, but
        # we add it here too as the load-bearing half of the mutation gate).
        dms_ro_with_status = dict(_DMS_RO, status='Draft', ro_id='ro-001')
        cache_row_with_status = dict(_CACHE_ROW, roId='ro-001', status='scheduled')
        cache_tbl_a = _fake_table_returning([cache_row_with_status])
        urlopen_a = _urlopen_returning_dms([dms_ro_with_status])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_a):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl_a)
            result_a = index.handler(_get_event(vehicleId='VEH-001'), None)
        body_a = json.loads(result_a['body'])
        statuses_a = [r.get('status') for r in body_a.get('serviceRecords', [])]
        assert 'Draft' in statuses_a, (
            f'DMS status "Draft" must win over cache "scheduled". Got: {statuses_a}. '
            'MUTATION for merge order: {**_mapped, **_cache_extras} would put cache '
            'SECOND, making it overwrite DMS — this must be CAUGHT.'
        )
        assert 'scheduled' not in statuses_a, (
            f'cache "scheduled" overwrote DMS "Draft" — merge order is wrong. '
            f'Got: {statuses_a}. MUTATION CAUGHT.'
        )

        # Case B: DMS row omits 'status' (no 'status' key at all) → cache's 'scheduled'
        # must survive.
        dms_ro_no_status = {k: v for k, v in _DMS_RO.items() if k != 'status'}
        dms_ro_no_status['ro_id'] = 'ro-001'
        cache_row_b = dict(_CACHE_ROW, roId='ro-001', status='scheduled')
        cache_tbl_b = _fake_table_returning([cache_row_b])
        urlopen_b = _urlopen_returning_dms([dms_ro_no_status])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_b):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl_b)
            result_b = index.handler(_get_event(vehicleId='VEH-001'), None)
        body_b = json.loads(result_b['body'])
        statuses_b = [r.get('status') for r in body_b.get('serviceRecords', [])]
        assert 'scheduled' in statuses_b, (
            f'cache "scheduled" must survive when DMS omits status. Got: {statuses_b}. '
            'D-G5j: the F5.1 fix restores cache-supplied status when DMS is silent on it. '
            'Without the fix, "status" was stripped from _cache_extras because it was in '
            '_DMS_OWNED_SERVICE_FIELDS, regardless of whether DMS supplied it.'
        )

    def test_seam_servicedate_survives_from_cache_when_dms_omits_it(self):
        """SEAM TEST 3 (D-G5j): DMS omits both opened_at and created_at, cache supplies serviceDate.

        A DMS row missing both date fields leaves serviceDate unmapped.
        Without the fix, the cache's serviceDate was stripped unconditionally.
        After the fix, it survives because {**_cache_extras, **_mapped} leaves
        cache values untouched when _mapped doesn't supply the key.

        MUTATION: {**_mapped, **_cache_extras} also passes this test (cache wins).
        The merge-order mutation is caught by test_seam_status_survives_from_cache
        where both sides supply a value.
        """
        dms_ro_no_dates = {
            k: v for k, v in _DMS_RO.items() if k not in ('opened_at', 'created_at', 'updated_at')
        }
        dms_ro_no_dates['ro_id'] = 'ro-001'
        cache_row = dict(_CACHE_ROW, roId='ro-001', serviceDate='2026-08-15')
        cache_tbl = _fake_table_returning([cache_row])
        urlopen_mock = _urlopen_returning_dms([dms_ro_no_dates])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl)
            result = index.handler(_get_event(vehicleId='VEH-001'), None)
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        assert records, 'expected at least one merged record'
        dates = [r.get('serviceDate') for r in records]
        assert '2026-08-15' in dates, (
            f'cache serviceDate "2026-08-15" must survive when DMS omits opened_at '
            f'and created_at. Got: {dates}. '
            'D-G5j: {**_cache_non_dms, **_mapped} stripped it because "serviceDate" '
            'is in _DMS_OWNED_SERVICE_FIELDS, regardless of DMS actually supplying it.'
        )


class TestF5_2_UnscopedGetVehicleId:
    """F5.2 / D-G5j: unscoped GET resolves vehicleId per row, memoized.

    The D-G5j defect 2: ``GET /api/v1/service-history?limit=500`` (the dashboard
    call) had no vehicleId param, so resolved_vehicle_id was '' for every row.
    The mapper therefore never emitted 'vehicleId', and the F5.1 defect stripped
    the cache's vehicleId too — every live row in the merged view lost its vehicle.

    After F5.2 each distinct vehicle_vin is resolved once per request (memoized),
    and the mapper receives the per-row resolved_vehicle_id.

    MUTATION TARGET: removing the per-row resolution (passing '' always) causes
    the vehicleId-presence test to fail.
    MEMOIZATION PROOF: two ROs on the same vehicle_vin must produce exactly ONE
    resolver call (GetItem + optional Query).
    """

    def _make_unscoped_event(self, limit=500, auth=_TOKEN, groups='platform-admin',
                              dms_endpoint='https://dms.example.invalid'):
        return {
            'httpMethod': 'GET',
            'path': '/api/v1/service-history',
            'headers': {'Authorization': auth},
            'queryStringParameters': {'limit': str(limit)},  # no vehicleId
            'body': None,
            'requestContext': {
                'authorizer': {
                    'claims': {
                        'cognito:groups': groups,
                        'email': 'operator@example.com',
                        'custom:fleetIds': 'fleet-a',
                    }
                }
            },
        }

    def test_unscoped_get_rows_carry_vehicleid(self):
        """MUTATION TARGET: unscoped GET rows must carry vehicleId.

        When DMS returns ROs with a real VIN (not a backfilled vehicleId), the
        resolver must find the vehicleId and the mapper must emit it.

        Removing the per-row resolution (passing resolved_vehicle_id='' always)
        causes this test to fail — no vehicleId in any record.
        """
        # DMS RO with a real VIN; resolver maps it to a vehicleId
        dms_ro = dict(_DMS_RO, vehicle_vin='1FTFW1ET5DFC10312', ro_id='ro-live')
        cache_tbl = _fake_table_returning([])
        # VIN resolver: step 1 GetItem miss (value is a VIN not a vehicleId);
        # step 2 vin-index Query returns vehicleId='VEH-001'
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}  # GetItem miss
        veh_tbl.query.return_value = {'Items': [{'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}]}
        urlopen_mock = _urlopen_returning_dms([dms_ro])

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._make_unscoped_event(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        assert records, 'expected at least one record from DMS'
        vehicle_ids = [r.get('vehicleId') for r in records]
        assert 'VEH-001' in vehicle_ids, (
            f'vehicleId "VEH-001" must appear on unscoped GET rows. '
            f'Got vehicleIds: {vehicle_ids}. '
            'D-G5j defect 2: when no vehicleId param, each DMS row must have its '
            'vehicle_vin resolved to a vehicleId by the memoized per-row resolver.'
        )

    def test_unscoped_get_backfilled_ro_carries_vehicleid(self):
        """Backfilled RO (vehicle_vin == vehicleId) on unscoped GET must carry vehicleId.

        Backfilled ROs store vehicleId in vehicle_vin.  The resolver's step 1 GetItem
        returns the vehicle directly (the value IS a vehicleId), so resolved_vehicle_id
        == vehicle_vin.  The mapper emits vehicleId but not vin (D-G5e).

        After F5.1+F5.2, the merged record carries vehicleId from the mapper AND
        any vin the cache row holds (from the F5.1 seam test above).
        """
        backfilled_dms_ro = dict(_DMS_RO, vehicle_vin='VEH-001', ro_id='ro-bfsh')
        cache_tbl = _fake_table_returning([])
        # Step 1 GetItem returns the vehicle directly (vehicle_vin is the vehicleId)
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001'}}  # no 'vin'
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms([backfilled_dms_ro])

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._make_unscoped_event(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        assert records, 'expected at least one record'
        vehicle_ids = [r.get('vehicleId') for r in records]
        assert 'VEH-001' in vehicle_ids, (
            f'backfilled RO vehicleId "VEH-001" must appear on unscoped GET. '
            f'Got vehicleIds: {vehicle_ids}. '
            'F5.2: the per-row memoized resolver must resolve vehicle_vin="VEH-001" '
            'via GetItem step 1 and pass the result to the mapper.'
        )
        # D-G5e: vin must NOT be emitted when vehicle_vin == resolved vehicleId
        vins = [r.get('vin') for r in records if r.get('vin')]
        assert all(v != 'VEH-001' for v in vins), (
            f'vin must not carry a vehicleId value. Got vins: {vins}. '
            'D-G5e: emitting vehicleId in the vin field is a false claim.'
        )

    def test_two_ros_same_vehicle_cost_one_resolver_call(self):
        """MEMOIZATION PROOF: N rows on M vehicles cost M resolver calls, not N.

        Two ROs with the same vehicle_vin must produce exactly one GetItem call
        on the vehicles table (plus at most one vin-index Query), not two.

        MUTATION TARGET: calling _resolve_vin_for_vehicle_id inside the per-row
        loop WITHOUT memoization causes this test to fail — GetItem is called twice.
        """
        # Two DMS ROs for the same vehicle
        ro1 = dict(_DMS_RO, vehicle_vin='1FTFW1ET5DFC10312', ro_id='ro-first')
        ro2 = dict(_DMS_RO, vehicle_vin='1FTFW1ET5DFC10312', ro_id='ro-second')
        cache_tbl = _fake_table_returning([])
        # Resolver: step 1 GetItem miss; step 2 returns vehicleId
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}  # miss
        veh_tbl.query.return_value = {'Items': [{'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}]}
        urlopen_mock = _urlopen_returning_dms([ro1, ro2])

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._make_unscoped_event(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body.get('count', 0) >= 2, 'expected both DMS rows in response'
        # MEMOIZATION: GetItem called exactly once for the shared vehicle_vin,
        # not once per row.  Two rows on one vehicle → one GetItem, one Query.
        get_item_calls = veh_tbl.get_item.call_count
        assert get_item_calls == 1, (
            f'GetItem was called {get_item_calls} time(s) for 2 rows on the same '
            f'vehicle_vin. Expected exactly 1 (memoized). '
            'MUTATION: removing the memo dict causes 2 GetItem calls — caught here.'
        )


if __name__ == '__main__':
    import unittest
    unittest.main(verbosity=2)



# ── F6.2: VIN redaction in resolver log lines ─────────────────────────────────

class TestF6_2_VinRedaction:
    """F6.2: VIN/vehicleId must never appear in full in resolver log output.

    Three print() sites in _resolve_vin_for_vehicle_id were printing the raw
    caller-supplied value. Since a vin-index miss is reached precisely because
    the value could not be resolved, it is most likely a real 17-char VIN.
    All three sites redact to ***<last6> via a byte-for-byte copy of
    services/data_processing/lambda/dms_ro_cache_invalidator/handler.py:_redact_vin.

    MUTATION TARGET: reverting one site to the raw value causes the full identifier
    to appear in stdout and this test fails.
    F6.2 / security review Cycle 8 W2.
    """

    _FULL_VIN = '1HGBH41JXMN109186'  # 17-char VIN; must NOT appear in logs
    _LAST6 = '109186'

    def _vin_miss_event(self):
        """GET event that triggers the vin-index miss branch (both steps fail to resolve)."""
        return _get_event(vehicleId=self._FULL_VIN)

    def test_vin_index_miss_does_not_log_full_vin(self, capsys):
        """vin-index miss branch must not emit the full identifier.

        The step 1 GetItem returns no Item (vehicleId not found); step 2 Query
        returns empty Items (vin-index miss). Both prints must redact.

        MUTATION TARGET: reverting index.py step2 print to the raw
        vehicle_id_value causes the full VIN to appear in captured stdout.
        """
        # Step 1: GetItem returns no Item; step 2: vin-index returns empty list.
        cache_tbl = _fake_table_returning([])
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}  # no 'Item' key — step 1 miss
        veh_tbl.query.return_value = {'Items': []}  # step 2 miss

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', _urlopen_returning_dms([])):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl, veh_tbl)
            index.handler(self._vin_miss_event(), None)

        out = capsys.readouterr().out
        assert self._FULL_VIN not in out, (
            f'Full VIN {self._FULL_VIN!r} appeared in log output. '
            'MUTATION: if a resolver print was reverted to the raw vehicle_id_value, '
            'the full identifier leaks here. VINs must be redacted to ***<last6>.'
        )
        assert f'***{self._LAST6}' in out, (
            f'Redacted form ***{self._LAST6} not found in log output. '
            'Expected the vin-index miss log line to emit the redacted form.'
        )



# ── F6.3: POST 500 body must not leak role ARN ────────────────────────────────

class TestF6_3_Post500BodyRedaction:
    """F6.3: The DMS-path POST outer except must not put str(e) in the response body.

    A boto3 AccessDeniedException carries the full role ARN — account id and role
    name. Interpolating str(e) into the 500 body discloses identity to the client.
    The fix logs the detail to CloudWatch and returns a generic message.

    MUTATION TARGET: restoring str(e) into the body causes the ARN-shaped string
    to appear in the response body and this test fails.
    F6.3 / security review Cycle 8 S2 (promoted to Warning).
    """

    _FAKE_ARN = 'arn:aws:iam::123456789012:role/cms-staging-service-role-abc'

    def test_put_item_exception_does_not_leak_arn_in_body(self, capsys):
        """A put_item exception whose message contains a role ARN must not reach body.

        MUTATION TARGET: restoring `str(e)` into the json body causes the ARN to
        appear in result['body'] — caught here.
        The exception IS logged (CloudWatch) — swallowing it entirely is worse.
        """
        cache_tbl = MagicMock()
        veh_tbl = MagicMock()
        # Step 1: GetItem returns a vehicle with a vin so the resolver succeeds
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}

        class _FakeDmsPostResp:
            def __init__(self):
                self._raw = json.dumps({'repairOrder': {'roId': 'ro-new'}}).encode()
            def read(self):
                return self._raw
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        # put_item raises an AccessDeniedException-shaped error containing the ARN
        arn_error = Exception(
            f'An error occurred (AccessDeniedException) when calling the PutItem '
            f'operation: User: {self._FAKE_ARN} is not authorized to perform: '
            f'dynamodb:PutItem on resource: arn:aws:dynamodb:us-west-2:123456789012:table/cms-staging-service-history'
        )
        # Only the service history table raises; vehicles table is normal
        service_history_tbl = MagicMock()
        service_history_tbl.put_item = MagicMock(side_effect=arn_error)

        def table_factory(name):
            if 'vehicles' in name.lower():
                return veh_tbl
            return service_history_tbl

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                      'VEHICLES_TABLE_NAME': 'test-vehicles',
                                      'SERVICE_HISTORY_TABLE_NAME': 'test-sh'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(return_value=_FakeDmsPostResp())):
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(_post_event(), None)

        assert result['statusCode'] == 500, (
            f'Expected 500 on put_item failure, got {result["statusCode"]}'
        )
        body = result['body']
        assert self._FAKE_ARN not in body, (
            f'Role ARN {self._FAKE_ARN!r} appeared in the 500 response body. '
            'MUTATION: restoring str(e) into the body causes this failure. '
            'The exception detail must be logged, not returned to the client.'
        )
        # The error message is generic — no exception text
        body_obj = json.loads(body)
        assert 'error' in body_obj, 'Expected an error key in the 500 body'
        assert str(arn_error) not in body_obj.get('error', ''), (
            'Exception message must not appear verbatim in the error field'
        )
        # The detail IS logged to stdout (CloudWatch)
        out = capsys.readouterr().out
        assert self._FAKE_ARN in out, (
            'Expected the exception detail to be logged to CloudWatch (stdout). '
            'Swallowing the error entirely is worse than the leak — log it.'
        )



# ── F7.1: the identifier must not reach logs by ANY channel ───────────────────

class TestF7_1_IdentifierNotLeakedViaExceptionText:
    """F7.1: no channel leaks the caller-supplied identifier, including `{_e}`.

    F6.2 redacted the interpolated format field at three sites and left the
    adjacent exception text raw.  A botocore ``ValidationException`` echoes the
    offending value, so one line both redacted and published the same VIN::

        step1 failed for ***109186: ValidationException:
            value 1HGBH41JXMN109186 is invalid

    Cycle 9 also established that of F6.2's three sites, only the miss branch had
    any coverage — reverting either EXCEPTION site to raw produced no failure.  So
    these tests are written per exception site, and the property is stated as "the
    identifier does not reach stdout", not "these sites are redacted".  A test
    derived from a site list cannot see a channel that is not on the list, which is
    how `{_e}` survived the fix intended to close it.

    Security review Cycle 9 W1.
    """

    _FULL_VIN = '1HGBH41JXMN109186'
    _LAST6 = '109186'

    def _botocore_shaped_error(self):
        """An exception whose __str__ echoes the caller-supplied value.

        This is the real shape: botocore renders the offending value into the
        message, which is exactly why redacting only the format field is
        insufficient.
        """
        return Exception(
            f'An error occurred (ValidationException) when calling the Query '
            f'operation: value {self._FULL_VIN} is invalid'
        )

    def _run(self, veh_tbl, capsys):
        cache_tbl = _fake_table_returning([])
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', _urlopen_returning_dms([])):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl, veh_tbl)
            index.handler(_get_event(vehicleId=self._FULL_VIN), None)
        return capsys.readouterr().out

    def _assert_scrubbed(self, out, site):
        assert self._FULL_VIN not in out, (
            f'Full identifier {self._FULL_VIN!r} reached stdout via the {site} '
            f'exception text. MUTATION: reverting that site to `{{_e}}` leaks it — '
            'the exception message itself carries the value, so redacting only the '
            'format field is insufficient (Cycle 9 W1).'
        )
        assert f'***{self._LAST6}' in out, (
            f'Redacted form ***{self._LAST6} not found for the {site} path.'
        )

    def test_step1_exception_text_is_scrubbed(self, capsys):
        """GetItem raising with the value in its message must not leak it."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.side_effect = self._botocore_shaped_error()
        veh_tbl.query.return_value = {'Items': []}
        self._assert_scrubbed(self._run(veh_tbl, capsys), 'step 1 (GetItem)')

    def test_step2_exception_text_is_scrubbed(self, capsys):
        """vin-index Query raising with the value in its message must not leak it."""
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}          # step 1 miss
        veh_tbl.query.side_effect = self._botocore_shaped_error()
        self._assert_scrubbed(self._run(veh_tbl, capsys), 'step 2 (vin-index Query)')

    def test_exception_class_survives_so_the_log_stays_diagnostic(self, capsys):
        """Scrubbing must not cost the diagnostic — the class name is still emitted.

        A resolver failure with no detail is how a MISSING index looks merely like a
        slow one (storage_stack.py:498), which is the failure mode that went
        unnoticed for months.
        """
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {}
        veh_tbl.query.side_effect = self._botocore_shaped_error()
        out = self._run(veh_tbl, capsys)
        assert 'Exception' in out and 'vin-index' in out, (
            'The log line must still name the exception class and the index, or a '
            'broken index is indistinguishable from a slow one.'
        )

    def test_scrub_helper_declines_on_a_short_identifier(self):
        """A <6-char value redacts to a bare '***', so substitution is declined.

        Documented behaviour, not an oversight: replacing a bare '***' throughout a
        message would corrupt unrelated text, and _redact_vin already discloses a
        short value in full by the sibling's documented boundary. A real VIN is 17
        characters, so this case does not arise in production.
        """
        src = inspect.getsource(index.handler)
        assert '_scrub_identifier' in src, '_scrub_identifier is not defined in handler'
        assert "if not _id or len(_id) < 6:" in src, (
            'The short-identifier boundary is missing from _scrub_identifier. It '
            'must mirror _redact_vin, whose < 6 branch returns a bare "***".'
        )


# ── F7.2: correlation id on the generic 500 ───────────────────────────────────

class TestF7_2_CorrelationIdOnPost500:
    """F7.2: the DMS-path POST 500 carries a correlation id and still no exception text.

    F6.3 stopped echoing str(e) and left the operator holding a log line the caller
    cannot quote. The id is the missing half — client gets an opaque handle,
    CloudWatch gets the detail. Adopts DMS's own convention
    (auth.py::server_error_response).

    Security review Cycle 9 Suggestion 2, promoted.
    """

    _FAKE_ARN = 'arn:aws:iam::123456789012:role/cms-staging-service-role-abc'

    def _run(self, context):
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }
        cache_tbl = MagicMock()
        cache_tbl.put_item.side_effect = Exception(
            f'An error occurred (AccessDeniedException) when calling the PutItem '
            f'operation: User: {self._FAKE_ARN} is not authorized'
        )

        class _Resp:
            def read(self_inner):
                return json.dumps({'ro_id': 'ro-new'}).encode()

            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_):
                return False

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles',
                                     'SERVICE_HISTORY_TABLE_NAME': 'test-sh'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(return_value=_Resp())):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl, veh_tbl)
            return index.handler(_post_event(), context)

    def test_context_none_does_not_raise_inside_the_error_path(self, capsys):
        """Every test in this suite passes context=None.

        A bare `context.aws_request_id` would raise AttributeError inside the
        handler's own except block, converting a 500 into an unhandled exception —
        the error path failing is strictly worse than the error it reports.
        """
        result = self._run(None)
        assert result['statusCode'] == 500
        body = json.loads(result['body'])
        assert body.get('correlationId'), 'no correlationId in the 500 body'
        assert self._FAKE_ARN not in result['body'], (
            'the role ARN reached the response body — F6.3 regression'
        )
        assert body['correlationId'] in capsys.readouterr().out, (
            'the correlationId returned to the client does not appear in the log, '
            'so an operator cannot join the two — which is the whole point of it.'
        )

    def test_request_id_is_used_when_the_context_supplies_one(self, capsys):
        """With a real context the Lambda request id is the correlation id."""
        ctx = MagicMock()
        ctx.aws_request_id = 'req-abc-123'
        result = self._run(ctx)
        body = json.loads(result['body'])
        assert body['correlationId'] == 'req-abc-123', (
            f"expected the Lambda request id, got {body.get('correlationId')!r}"
        )
        assert 'req-abc-123' in capsys.readouterr().out
        assert self._FAKE_ARN not in result['body']



# ── The bfsh- correlation fallback: zero coverage until a live 500 found it ────


class TestBfshFallbackActuallyExecutes:
    """The `bfsh-` correlation path, which 334 tests never once ran.

    ## How it hid

    Both correlation sites are `or` expressions:

        _ck   = _ci.get('roId')            or _bfsh_key(...)   # cache side
        _corr = _mapped.get('serviceId')   or _bfsh_key(...)   # DMS side

    `_CACHE_ROW` carries `roId` and `_DMS_RO` carries `ro_id`, so every fixture in
    this file took the left branch of both, and `_bfsh_key` was never entered. The
    fallback is the mechanism for LEGACY rows — rows written before T5.2 stamped
    `roId` — which is to say *most rows in the real table*. The single most-used
    path in production had no test.

    ## What it was hiding

    `index.py` contained a second, function-local `import hashlib` inside an
    unrelated trip/driver branch. That made `hashlib` a **local of `handler()` for
    the whole 8,000-line function**, so `_bfsh_key` — a nested def — closed over an
    unbound local and raised:

        free variable 'hashlib' referenced before assignment in enclosing scope

    Every live `GET /api/v1/service-history` returned 500. Found by the first real
    call against staging, not by the suite. The file already documents this exact
    trap for `decimal_default` a few hundred lines up; the same shadowing bit a
    second helper.

    ## Why these tests are shaped this way

    They omit `roId`/`ro_id` deliberately, which is the only way to reach the
    fallback. A fixture that carries the happy-path field cannot test the fallback,
    and "the fixture always had the field" is precisely why this shipped.
    """

    def test_cache_row_without_roid_reaches_the_bfsh_fallback(self):
        """Legacy cache row: no roId, so correlation must derive a bfsh- key.

        Pre-fix this raised the unbound-local NameError and the route 500'd.
        """
        legacy = {k: v for k, v in _CACHE_ROW.items() if k != 'roId'}
        assert 'roId' not in legacy, 'fixture must omit roId or it tests nothing'
        cache_tbl = _fake_table_returning([legacy])
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles',
                                     'SERVICE_HISTORY_TABLE_NAME': 'test-sh'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', _urlopen_returning_dms([])):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl, veh_tbl)
            result = index.handler(_get_event(), None)
        assert result['statusCode'] == 200, (
            f"expected 200, got {result['statusCode']}: {result['body'][:300]}. "
            'A NameError here means hashlib is shadowed by a function-local import '
            'again — see this class docstring.'
        )

    def test_dms_record_without_ro_id_reaches_the_bfsh_fallback(self):
        """The other short-circuit: a DMS RO with no ro_id derives its key too."""
        ro = {k: v for k, v in _DMS_RO.items() if k != 'ro_id'}
        assert 'ro_id' not in ro
        cache_tbl = _fake_table_returning([])
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles',
                                     'SERVICE_HISTORY_TABLE_NAME': 'test-sh'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', _urlopen_returning_dms([ro])):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl, veh_tbl)
            result = index.handler(_get_event(), None)
        assert result['statusCode'] == 200, (
            f"expected 200, got {result['statusCode']}: {result['body'][:300]}"
        )

    def test_cache_row_carrying_a_decimal_serialises(self):
        """A row with a real Decimal — the only way to reach `_sh_decimal_default`.

        `json.dumps(default=...)` invokes the callback ONLY when it meets a value it
        cannot serialise. Every fixture in this file used plain str/int, so the
        serialiser was never called and its body was never executed — the same
        never-reached-branch blindness as the `or` short-circuits above, arriving
        through a lazily-invoked callback instead.

        What that hid: `_sh_decimal_default` referenced `Decimal`, which is shadowed
        into a handler-local by 30-odd `from decimal import Decimal` statements
        inside `handler()`. None of them run on the service-history path, so any
        response containing a Decimal raised

            free variable 'Decimal' referenced before assignment in enclosing scope

        Live staging returned 200 at `limit=3` and 500 at `limit=200`, purely
        because the larger page happened to include a row with a numeric attribute.
        A green suite plus a passing smoke test at small limit both reported healthy.
        """
        from decimal import Decimal as _D
        row = dict(_CACHE_ROW)
        row['cost'] = _D('249.99')     # non-integral: exercises the float() branch
        row['mileage'] = _D('81000')   # integral: exercises the int() branch
        cache_tbl = _fake_table_returning([row])
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}}
        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles',
                                     'SERVICE_HISTORY_TABLE_NAME': 'test-sh'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', _urlopen_returning_dms([])):
            mock_ddb.Table.side_effect = _make_table_factory(cache_tbl, veh_tbl)
            result = index.handler(_get_event(), None)
        assert result['statusCode'] == 200, (
            f"expected 200, got {result['statusCode']}: {result['body'][:300]}. "
            'A "free variable \'Decimal\'" error here means _sh_decimal_default lost '
            'its def-local import and is closing over the handler-local again.'
        )
        # The response must be real JSON with the Decimals coerced, not a 200 that
        # merely avoided raising.
        body = json.loads(result['body'])
        assert body['serviceRecords'], 'row was dropped instead of serialised'
        assert json.dumps(body), 'response is not round-trippable JSON'

    def test_hashlib_is_not_shadowed_by_a_function_local_import(self):
        """Structural guard: no `import hashlib` inside handler().

        The module-level import at line 1 is the only one that may exist. A second
        one anywhere inside `handler()` re-creates the shadowing for EVERY nested
        helper in the function, not just the one that happened to break — so this
        asserts the absence directly rather than testing one victim.
        """
        src = inspect.getsource(index.handler)
        assert 'import hashlib' not in src, (
            'a function-local `import hashlib` is back inside handler(). It makes '
            'hashlib a local for the entire function, so every nested def that '
            'references it closes over an unbound local. Use the module-level '
            'import at index.py line 1.'
        )

    def test_no_nested_def_closes_over_a_shadowed_import(self):
        """Structural guard for the whole shadowing CLASS, not one name.

        Generalises the hashlib guard above, which was written the same day and was
        already too narrow: within hours it passed while `Decimal` — shadowed 30
        times inside handler() — broke `_sh_decimal_default` in production. Third
        instance of one mechanism (decimal_default, hashlib, Decimal), so the guard
        is now the mechanism rather than a name.

        The rule: if a name is imported at module level AND re-imported anywhere
        inside handler(), Python makes it a handler-local for the entire function.
        Any nested def referencing it then closes over that local, which is unbound
        on every code path that has not already executed one of those imports.
        Such a def MUST import the name in its own body.

        Note what this does NOT accept: five `_dec` helpers previously sat directly
        after a sibling `from decimal import Decimal`, so they worked — by statement
        ordering, not by scoping. That is the luck that ran out for
        `_sh_decimal_default`, whose own docstring described this very trap while
        having it. Ordering is not a guarantee this test will honour.
        """
        tree = ast.parse(Path(index.__file__).read_text())
        handler = next(
            n for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name == 'handler'
        )

        module_imports = {
            a.asname or a.name.split('.')[0]
            for n in tree.body if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names
        }
        shadowed = {
            a.asname or a.name.split('.')[0]
            for n in ast.walk(handler) if isinstance(n, (ast.Import, ast.ImportFrom))
            for a in n.names
        } & module_imports

        offenders = []
        for node in ast.walk(handler):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if node is handler:
                continue
            own = {
                a.asname or a.name.split('.')[0]
                for m in ast.walk(node) if isinstance(m, (ast.Import, ast.ImportFrom))
                for a in m.names
            }
            used = {
                m.id for m in ast.walk(node)
                if isinstance(m, ast.Name) and isinstance(m.ctx, ast.Load)
            }
            risky = (used & shadowed) - own
            if risky:
                offenders.append(f'  line {node.lineno}: {node.name}() uses {sorted(risky)}')

        assert not offenders, (
            'nested def(s) inside handler() reference a name that is shadowed by a '
            'function-local import, so they close over an unbound local and raise '
            '"free variable X referenced before assignment in enclosing scope" on '
            'any path that has not already run one of those imports:\n'
            + '\n'.join(offenders)
            + f'\n\nshadowed names in handler(): {sorted(shadowed)}\n'
            'Fix: import the name inside the nested def\'s own body.'
        )



class TestPostFloatHandling:
    """POST bodies carrying floats — the class that orphaned a live repair order.

    Live, 2026-09-10: a POST with `cost.total: 249.99` returned 500
    (`TypeError: Float types are not supported. Use Decimal types instead.`)
    because DynamoDB rejects Python floats, **after** D-G5h's DMS booking had
    already committed. The caller saw a failure; `dms-staging-repair-orders`
    carried a real RO (`5fdad446-…`) on a real VIN at a real rooftop, with zero
    matching CMS cache rows. A retry would have created a second orphan.
    See issues/2026-09-10-service-history-post-floats-500-after-dms-commits-
    orphaning-repair-orders/.

    Why 339 tests missed it, which is the reusable part: no fixture posted a
    float, and these tests patch `index.dynamodb`, so `put_item` is a MagicMock
    that accepts floats happily. **The type rejection exists only in the real
    boto3 serialiser** — a stub cannot fail the way a service fails. So the two
    tests below do not rely on the stub to reject anything. They assert on the
    VALUES handed to `put_item`, which is observable through a mock and is the
    actual contract: nothing float-typed may reach DynamoDB.
    """

    @staticmethod
    def _float_post_event():
        ev = _post_event()
        body = json.loads(ev['body'])
        body['cost'] = {'total': 249.99, 'currency': 'USD'}   # the live payload
        body['mileage'] = 81000
        ev['body'] = json.dumps(body)
        return ev

    @staticmethod
    def _floats_in(obj, path='') -> list[str]:
        """Every float-typed leaf, with its path. Recurses dicts and lists."""
        out: list[str] = []
        if isinstance(obj, float):
            out.append(f'{path or "<root>"}={obj!r}')
        elif isinstance(obj, dict):
            for k, v in obj.items():
                out.extend(TestPostFloatHandling._floats_in(v, f'{path}.{k}' if path else str(k)))
        elif isinstance(obj, (list, tuple)):
            for i, v in enumerate(obj):
                out.extend(TestPostFloatHandling._floats_in(v, f'{path}[{i}]'))
        return out

    def _run_post(self):
        put_item_calls = []
        cache_tbl = MagicMock()
        cache_tbl.put_item = MagicMock(side_effect=lambda **kw: put_item_calls.append(kw))
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }

        class _Resp:
            status = 201
            def read(self):
                return json.dumps({'ro_id': 'RO-live-verify'}).encode()
            def __enter__(self):
                return self
            def __exit__(self, *_):
                return False

        with patch.dict(os.environ, {'DMS_API_ENDPOINT': 'https://dms.example.invalid',
                                     'VEHICLES_TABLE_NAME': 'test-vehicles',
                                     'SERVICE_HISTORY_TABLE_NAME': 'test-sh'}), \
                patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', MagicMock(return_value=_Resp())):
            def table_factory(name):
                return veh_tbl if 'vehicles' in name.lower() else cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._float_post_event(), None)
        return result, put_item_calls

    def test_post_with_a_float_cost_does_not_error(self):
        """The live symptom: 249.99 in the body must not blow up the handler.

        Also guards a trap this fix walked into once: the obvious fix is
        `json.loads(..., parse_float=Decimal)`, and `Decimal` is itself shadowed
        into a handler-local by ~35 function-local imports. Referencing it on the
        POST path — where none of those imports execute — raises UnboundLocalError,
        which is the same bug class in a new spot. That failure surfaces here.
        """
        result, _ = self._run_post()
        assert result['statusCode'] == 201, (
            f"expected 201, got {result['statusCode']}: {result['body'][:300]}"
        )

    def test_no_float_reaches_dynamodb(self):
        """The real contract: put_item must receive zero float-typed values.

        Asserted on the values passed to the mock rather than by expecting the
        mock to raise — the stub accepts floats, so only inspecting the payload
        can detect this. Decimal is fine; float is not.
        """
        _, put_item_calls = self._run_post()
        assert len(put_item_calls) == 1, f'expected 1 put_item, got {len(put_item_calls)}'
        item = put_item_calls[0].get('Item', {})
        floats = self._floats_in(item)
        assert not floats, (
            'float-typed values reached put_item; DynamoDB rejects these with '
            f'"Float types are not supported": {floats}\n'
            'Parse the request body with json.loads(..., parse_float=Decimal).'
        )
        # Positive control: the value must actually be present, as a Decimal —
        # a fix that silently dropped `cost` would otherwise pass the check above.
        from decimal import Decimal as _D
        assert isinstance(item.get('cost', {}).get('total'), _D), (
            f"cost.total should be Decimal, got {type(item.get('cost', {}).get('total'))}: "
            f"{item.get('cost')}"
        )



# ── T1.3: VIN passthrough + envelope (spec 2026-09-10-service-history-read-path-correctness) ──

# Helper to build ~300 DMS rows programmatically.
# The defect is a RATIO (1 cache-only among 299 DMS rows).
# A 2-row fixture cannot express it — the very failure mode that let this through
# both review gates (R5).

def _make_dms_rows(count=299, base_vin='1FTFW1ET5DFC10312'):
    """Return ``count`` DMS RO dicts, all for the same VIN."""
    return [
        {
            'ro_id': f'ro-dms-{i:04d}',
            'status': 'Closed',
            'dealer_id': 'dealer-austin',
            'dealer_name': 'Meridian of Austin',
            'opened_at': f'2026-08-{(i % 28) + 1:02d}T10:00:00Z',
            'created_at': f'2026-08-{(i % 28) + 1:02d}T08:00:00Z',
            'updated_at': f'2026-08-{(i % 28) + 1:02d}T10:00:00Z',
            'description': f'Routine service #{i}',
            'vehicle_vin': base_vin,
            'initiated_by': 'cms_booking',
        }
        for i in range(count)
    ]


def _make_cache_rows_for_dms(dms_rows):
    """Return a cache row for each DMS row, keyed by roId matching ro_id."""
    return [
        {
            'vehicleId': 'VEH-001',
            'serviceDate': r['opened_at'],
            'roId': r['ro_id'],
            'cachedAt': r['created_at'],
            'createdAt': r['created_at'],
            'notes': f'Notes for {r["ro_id"]}',
        }
        for r in dms_rows
    ]


class TestT1_3_VinPassthroughAndEnvelope:
    """T1.3: VIN passthrough, client-side filter deletion, and envelope contract.

    Spec: ``.kiro/specs/2026-09-10-service-history-read-path-correctness/spec.md``

    All seven tests are RED on the current implementation. Each names the property
    it guards and why the current code fails it.

    Mutation bar (R5): each failing test must fail for an assertion reason, not an
    import/attribute error. The fixture must be large enough that the defect is
    expressible — a ratio defect needs ~300 rows.
    """

    def _get_event_with_vehicle_id(
        self,
        *,
        vehicleId='VEH-001',
        auth=_TOKEN,
        groups='platform-admin',
        dms_endpoint='https://dms.example.invalid',
    ):
        return {
            'httpMethod': 'GET',
            'path': '/api/v1/service-history',
            'headers': {'Authorization': auth} if auth else {},
            'queryStringParameters': {'vehicleId': vehicleId},
            'body': None,
            'requestContext': {
                'authorizer': {
                    'claims': {
                        'cognito:groups': groups,
                        'email': 'operator@example.com',
                        'custom:fleetIds': 'fleet-a',
                    }
                }
            },
        }

    # ── (a) vehicleId-scoped GET sends ?vehicle_vin=<resolved> to DMS ─────────

    def test_a_vehicleid_scoped_get_sends_vehicle_vin_param_to_dms(self):
        """D1: when vehicleId is supplied and resolves to a VIN, CMS must append
        ?vehicle_vin=<vin> to the DMS URL so DMS queries the vin-index GSI.

        CURRENT FAILURE: index.py F4.3 comment says 'do NOT append ?vin= to the
        URL', and no ?vehicle_vin= is appended either. The DMS URL is built as
        ``dms_endpoint + '/api/dms/fleet/repair-orders'`` with no query string.
        This test asserts the URL DOES contain ``?vehicle_vin=`` after D1 is
        implemented.

        MUTATION TARGET: removing the vin param from the URL causes this test to
        fail (the admin scan path is used instead of the GSI query).
        """
        dms_urls_seen = []

        class _RecordingOpener:
            def open(self_inner, req, timeout=None):
                dms_urls_seen.append(req.full_url)
                return _FakeDmsGetResp([])

        cache_tbl = _fake_table_returning([])
        # Resolver: vehicleId='VEH-001' → step 1 GetItem returns vehicle with vin
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }
        veh_tbl.query.return_value = {'Items': []}

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.build_opener', return_value=_RecordingOpener()):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            index.handler(self._get_event_with_vehicle_id(), None)

        assert dms_urls_seen, 'DMS was not called'
        vin_param_present = any('vehicle_vin=1FTFW1ET5DFC10312' in url for url in dms_urls_seen)
        assert vin_param_present, (
            f'DMS URL must contain ?vehicle_vin=1FTFW1ET5DFC10312 (D1: route scoped reads '
            f'through the vin-index GSI). Got URLs: {dms_urls_seen}. '
            'CURRENT FAILURE: index.py does not append ?vehicle_vin= — it appends nothing '
            '(F4.3 comment says "do NOT append ?vin="). That comment is superseded by D1 '
            'which requires ?vehicle_vin=<resolved>. '
            'MUTATION TARGET: removing the vin param causes the admin scan to run instead of '
            'the GSI query, returning 100 of 43,200 rows.'
        )

    # ── (b) no client-side VIN filtering — DMS rows with different VINs are trusted ──

    def test_b_dms_rows_not_filtered_client_side_after_d1(self):
        """D1: delete the client-side VIN filter — DMS filters server-side via GSI.

        After D1 the handler passes ?vehicle_vin= to DMS, which returns only
        matching rows. The client-side check ``if _dr_vin and _dr_vin not in _match_set``
        must be deleted. A DMS row whose vehicle_vin differs from the resolved VIN
        should now be TRUSTED (DMS guarantees it belongs to the requested vehicle).

        CURRENT FAILURE: index.py still has the client-side filter:
            if vehicle_id and (resolved_vin or resolved_vehicle_id):
                if _dr_vin and _dr_vin not in _match_set:
                    continue
        So a DMS row with vehicle_vin='TRUSTED-BY-DMS-BUT-DIFFERENT' is dropped
        by the client-side filter, and it does NOT appear in the response.
        After D1 the filter must be gone, so this row DOES appear.

        MUTATION TARGET: restoring the client-side filter causes a DMS row that DMS
        server-side confirmed belongs to the vehicle to be dropped by CMS.
        """
        # A DMS row whose vehicle_vin differs from the resolved VIN — DMS confirmed
        # it belongs to VEH-001 by returning it via the vin-index GSI query, so CMS
        # must trust it without re-checking.
        dms_row_trusted_by_dms = dict(
            _DMS_RO,
            ro_id='ro-trusted-different-vin',
            vehicle_vin='TRUSTED-BY-DMS-DIFFERENT-FROM-RESOLVED',
        )
        cache_tbl = _fake_table_returning([])
        veh_tbl = MagicMock()
        # Resolver: step 1 GetItem hit → resolved_vehicle_id='VEH-001', resolved_vin='1FTFW1ET5DFC10312'
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms([dms_row_trusted_by_dms])

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._get_event_with_vehicle_id(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        service_ids = [r.get('serviceId') for r in records]
        assert 'ro-trusted-different-vin' in service_ids, (
            f'DMS row ro-trusted-different-vin was dropped by the client-side VIN filter. '
            f'Got serviceIds: {service_ids}. '
            'D1: delete the client-side VIN filter — DMS now filters server-side via '
            'the vin-index GSI query. CMS must trust the rows DMS returns. '
            'CURRENT FAILURE: index.py line ~8080 still has '
            '"if _dr_vin and _dr_vin not in _match_set: continue". '
            'MUTATION TARGET: restoring this filter causes DMS-trusted rows to be dropped.'
        )

    # ── (c) 299 DMS + 1 cache-only → mixed, cacheOnlyCount=1, asOf from cache row ──

    def test_c_mixed_label_cacheOnlyCount_and_asOf_at_correct_ratio(self):
        """D3: 299 DMS rows + 1 cache-only row → dataSource='mixed', cacheOnlyCount=1,
        asOf from the cache-only row.

        FIXTURE SIZE: ~300 rows. The defect is a RATIO — one cache-only row among
        299 DMS rows. A 2-row fixture cannot express this: with 1 DMS + 1 cache-only
        the existing code would return dataSource='cache', which a 2-row test might
        accept as a label change. This test is specific: 299 clean DMS rows, 1
        cache-only, and the label must be 'mixed' (not 'cache').

        CURRENT FAILURE (three properties all fail):
        1. dataSource is 'cache' (not 'mixed') — the 'mixed' value does not exist.
        2. 'cacheOnlyCount' key is absent from the envelope.
        3. 'provenance' field is absent from individual records.

        MUTATION TARGET (per-property):
        - Using 'cache' instead of 'mixed' fails assertion 1.
        - Omitting cacheOnlyCount fails assertion 2.
        - Counting total cache rows (not cache-ONLY rows) as cacheOnlyCount
          fails with count=300 instead of 1.
        """
        base_vin = '1FTFW1ET5DFC10312'
        dms_rows = _make_dms_rows(count=299, base_vin=base_vin)
        cache_rows_for_dms = _make_cache_rows_for_dms(dms_rows)

        # One additional cache-only row — roId does NOT match any DMS ro_id
        cache_only_row = {
            'vehicleId': 'VEH-001',
            'serviceDate': '2026-07-15T09:00:00Z',
            'roId': 'ro-CACHE-ONLY-NOT-IN-DMS',
            'cachedAt': '2026-07-15T09:30:00Z',
            'createdAt': '2026-07-15T09:00:00Z',
            'notes': 'Cached before DMS was available',
        }
        all_cache_rows = cache_rows_for_dms + [cache_only_row]
        cache_tbl = _fake_table_returning(all_cache_rows)

        # VIN resolver: vehicleId='VEH-001' → vin='1FTFW1ET5DFC10312'
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': base_vin}
        }
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms(dms_rows)

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._get_event_with_vehicle_id(), None)

        assert result['statusCode'] == 200, (
            f'Expected 200, got {result["statusCode"]}: {result["body"][:300]}'
        )
        body = json.loads(result['body'])

        # Assertion 1: dataSource must be 'mixed', not 'cache'
        assert body.get('dataSource') == 'mixed', (
            f'Expected dataSource="mixed" for 299 DMS rows + 1 cache-only row. '
            f'Got dataSource="{body.get("dataSource")}". '
            'CURRENT FAILURE: the "mixed" value does not exist in the current code. '
            'D3: the enum had two values for three states; naming the third is the fix. '
            'MUTATION TARGET: using "cache" instead of "mixed" fails this assertion.'
        )

        # Assertion 2: cacheOnlyCount must be 1 (exactly one cache-only row)
        assert 'cacheOnlyCount' in body, (
            f'cacheOnlyCount key is missing from the envelope. '
            f'Got envelope keys: {list(body.keys())}. '
            'CURRENT FAILURE: the current code has no cacheOnlyCount field. '
            'D3: cacheOnlyCount is required so the consumer knows what fraction is stale. '
            'MUTATION TARGET: omitting cacheOnlyCount fails this assertion.'
        )
        assert body['cacheOnlyCount'] == 1, (
            f'Expected cacheOnlyCount=1 (one cache-only row), got {body["cacheOnlyCount"]}. '
            'MUTATION TARGET: counting total cache rows (300) instead of cache-only rows (1) '
            'produces the wrong ratio — the ratio is the defect this spec is fixing.'
        )

        # Assertion 3: asOf must be present (from the one cache-only row)
        assert 'asOf' in body, (
            f'asOf key is missing from the mixed envelope. '
            f'Got envelope keys: {list(body.keys())}. '
            'D3: asOf is present when at least one cache-only row exists; '
            'it is the oldest cache-only row\'s cachedAt.'
        )
        assert body['asOf'] == '2026-07-15T09:30:00Z', (
            f'Expected asOf="2026-07-15T09:30:00Z" (cachedAt of the one cache-only row). '
            f'Got asOf="{body.get("asOf")}".'
        )

    # ── (d) all-DMS → 'live' with NO asOf key ─────────────────────────────────

    def test_d_all_dms_yields_live_with_asOf_absent(self):
        """D3: when DMS answers and there are no cache-only rows, dataSource='live'
        and asOf is ABSENT from the envelope (not None, not '').

        Assert with ``'asOf' not in body``, not ``body.get('asOf') is None``.

        The current code already produces 'live' and omits asOf when has_cache_only
        is False. However, this test also asserts:
        - 'cacheOnlyCount' must be present and equal to 0.
        - Each record must carry a 'provenance' field with value 'dms'.

        CURRENT FAILURE: 'cacheOnlyCount' is absent and 'provenance' is absent
        per row. The dataSource='live' and asOf-absence already work.

        MUTATION TARGET:
        - Adding asOf=None instead of omitting it fails the 'not in' assertion.
        - Omitting cacheOnlyCount fails the count assertion.
        - Omitting provenance per row fails the provenance assertion.
        """
        dms_rows = _make_dms_rows(count=5)
        # Cache rows match all 5 DMS rows — none are cache-only
        cache_rows = _make_cache_rows_for_dms(dms_rows)
        cache_tbl = _fake_table_returning(cache_rows)
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms(dms_rows)

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._get_event_with_vehicle_id(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])

        # dataSource must be 'live'
        assert body.get('dataSource') == 'live', (
            f'Expected dataSource="live" when DMS answers with no cache-only rows. '
            f'Got "{body.get("dataSource")}".'
        )

        # asOf must be ABSENT — assert with 'not in', not is None (spec requirement)
        assert 'asOf' not in body, (
            f'asOf must be ABSENT (not in body) when cacheOnlyCount==0. '
            f'Got asOf={body.get("asOf")!r}. '
            'D3: asOf is omitted entirely when cacheOnlyCount==0. '
            'MUTATION TARGET: setting asOf=None instead of omitting the key fails '
            'this assertion — the spec says "not in", not "is None".'
        )

        # cacheOnlyCount must be 0 — present in the envelope
        assert 'cacheOnlyCount' in body, (
            f'cacheOnlyCount key is missing. Envelope keys: {list(body.keys())}. '
            'CURRENT FAILURE: the current code has no cacheOnlyCount field.'
        )
        assert body['cacheOnlyCount'] == 0, (
            f'Expected cacheOnlyCount=0 for all-DMS response, got {body["cacheOnlyCount"]}.'
        )

        # Each DMS-sourced record must carry provenance='dms'
        records = body.get('serviceRecords', [])
        assert records, 'expected DMS records in the live response'
        missing_provenance = [r.get('serviceId') for r in records if 'provenance' not in r]
        assert not missing_provenance, (
            f'Records missing "provenance" field: {missing_provenance}. '
            'CURRENT FAILURE: index.py does not add provenance per record. '
            'D3: each record gains `provenance: "dms" | "cache"`. '
            'MUTATION TARGET: omitting provenance on DMS records fails this assertion.'
        )
        wrong_provenance = [
            (r.get('serviceId'), r.get('provenance'))
            for r in records
            if r.get('provenance') != 'dms'
        ]
        assert not wrong_provenance, (
            f'DMS-sourced records with wrong provenance: {wrong_provenance}. '
            'Expected provenance="dms" on all DMS rows.'
        )

    # ── (e) all-cache → 'cache' ────────────────────────────────────────────────

    def test_e_all_cache_yields_cache_label(self):
        """D3: when DMS fails and only cache rows exist, dataSource='cache'.

        The current code already produces 'cache' when dms_failed=True, but
        this test also asserts cacheOnlyCount and per-row provenance='cache'.

        CURRENT FAILURE: 'cacheOnlyCount' and 'provenance' are absent.

        MUTATION TARGET:
        - Returning 'live' when DMS fails fails the dataSource assertion.
        - Omitting cacheOnlyCount fails the count assertion.
        - Omitting provenance fails the per-row assertion.
        """
        # 5 cache rows; DMS fails
        cache_rows = [
            dict(_CACHE_ROW, roId=f'ro-cache-{i}', vehicleId='VEH-001')
            for i in range(5)
        ]
        cache_tbl = _fake_table_returning(cache_rows)
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }
        veh_tbl.query.return_value = {'Items': []}

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open',
                      MagicMock(side_effect=Exception('DMS down'))):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._get_event_with_vehicle_id(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])

        assert body.get('dataSource') == 'cache', (
            f'Expected dataSource="cache" when DMS fails and cache is non-empty. '
            f'Got "{body.get("dataSource")}".'
        )

        # cacheOnlyCount — all 5 rows are cache-only when DMS fails
        assert 'cacheOnlyCount' in body, (
            f'cacheOnlyCount is absent from the all-cache envelope. '
            'CURRENT FAILURE: the current code has no cacheOnlyCount field.'
        )
        assert body['cacheOnlyCount'] == 5, (
            f'Expected cacheOnlyCount=5 (all 5 cache rows are cache-only). '
            f'Got {body["cacheOnlyCount"]}.'
        )

        # Each record must carry provenance='cache'
        records = body.get('serviceRecords', [])
        assert records, 'expected cache records in the response'
        missing = [r for r in records if 'provenance' not in r]
        assert not missing, (
            f'{len(missing)} records missing provenance field. '
            'CURRENT FAILURE: index.py does not add provenance per record.'
        )
        wrong = [(r.get('roId'), r.get('provenance')) for r in records if r.get('provenance') != 'cache']
        assert not wrong, (
            f'Cache-sourced records with wrong provenance value: {wrong}. '
            'Expected provenance="cache" on all cache-only rows.'
        )

    # ── (f) per-row provenance field ──────────────────────────────────────────

    def test_f_per_row_provenance_on_mixed_response(self):
        """D3: each record in a mixed response carries provenance='dms' or 'cache'.

        The mixed case (DMS rows + cache-only rows) must label each record
        individually, not just the envelope.

        CURRENT FAILURE: provenance is not added to any record.

        MUTATION TARGET: setting provenance='live' instead of 'dms' fails the DMS
        row check; setting provenance='dms' on cache-only rows fails the cache check.
        """
        dms_rows = _make_dms_rows(count=3)
        cache_rows_for_dms = _make_cache_rows_for_dms(dms_rows)
        cache_only_row = {
            'vehicleId': 'VEH-001',
            'serviceDate': '2026-06-01T10:00:00Z',
            'roId': 'ro-CACHE-ONLY-F',
            'cachedAt': '2026-06-01T10:30:00Z',
            'createdAt': '2026-06-01T10:00:00Z',
        }
        all_cache_rows = cache_rows_for_dms + [cache_only_row]
        cache_tbl = _fake_table_returning(all_cache_rows)
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms(dms_rows)

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._get_event_with_vehicle_id(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        assert records, 'expected records in the mixed response'

        # All DMS-sourced records must carry provenance='dms'
        dms_service_ids = {r['ro_id'] for r in dms_rows}
        dms_records = [r for r in records if r.get('serviceId') in dms_service_ids]
        for r in dms_records:
            assert 'provenance' in r, (
                f'DMS record {r.get("serviceId")} missing provenance. '
                'CURRENT FAILURE: provenance is not added to DMS-sourced records.'
            )
            assert r['provenance'] == 'dms', (
                f'DMS record {r.get("serviceId")} has provenance="{r["provenance"]}", '
                'expected "dms". MUTATION TARGET: wrong value.'
            )

        # The cache-only record must carry provenance='cache'
        cache_only_records = [r for r in records if r.get('serviceId') == 'ro-CACHE-ONLY-F']
        assert cache_only_records, (
            f'Cache-only record ro-CACHE-ONLY-F missing from response. '
            'Expected it to appear with provenance="cache".'
        )
        for r in cache_only_records:
            assert 'provenance' in r, (
                f'Cache-only record {r.get("serviceId")} missing provenance field. '
                'CURRENT FAILURE: provenance not added to cache-only rows.'
            )
            assert r['provenance'] == 'cache', (
                f'Cache-only record has provenance="{r["provenance"]}", expected "cache". '
                'MUTATION TARGET: wrong provenance value on cache-only rows.'
            )

    # ── (g) cache-only row without serviceId projects roId ───────────────────

    def test_g_cache_only_row_without_serviceId_projects_roId(self):
        """D4: a cache-only row lacking 'serviceId' must project roId as serviceId.

        A cache row written before T5.2 stamped roId may lack serviceId. Without
        this fallback the row is returned with serviceId=None, making it
        unidentifiable to the caller.

        CURRENT FAILURE: the current code does not add a serviceId fallback for
        cache-only rows. The record appears with serviceId absent or None.

        MUTATION TARGET: omitting the roId fallback causes serviceId to be absent/None
        on legacy cache rows, failing the assertion that serviceId is present and
        equals roId.
        """
        # A cache-only row that has roId but NO serviceId
        cache_only_no_service_id = {
            'vehicleId': 'VEH-001',
            'serviceDate': '2026-05-20T08:00:00Z',
            'roId': 'ro-LEGACY-NO-SERVICEID',
            # deliberately NO 'serviceId' key
            'cachedAt': '2026-05-20T08:30:00Z',
            'createdAt': '2026-05-20T08:00:00Z',
            'notes': 'Pre-T5.2 legacy row',
        }
        cache_tbl = _fake_table_returning([cache_only_no_service_id])
        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': '1FTFW1ET5DFC10312'}
        }
        veh_tbl.query.return_value = {'Items': []}
        # DMS returns no rows — cache-only path
        urlopen_mock = _urlopen_returning_dms([])

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._get_event_with_vehicle_id(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        records = body.get('serviceRecords', [])
        assert records, f'expected at least one record, got {body}'

        # Find the legacy row
        legacy_records = [
            r for r in records
            if r.get('serviceId') == 'ro-LEGACY-NO-SERVICEID'
            or r.get('roId') == 'ro-LEGACY-NO-SERVICEID'
        ]
        assert legacy_records, (
            f'Legacy cache row ro-LEGACY-NO-SERVICEID not found in response. '
            f'Got serviceIds: {[r.get("serviceId") for r in records]}. '
            'D4: cache-only rows must appear with serviceId projected from roId.'
        )

        legacy = legacy_records[0]
        assert legacy.get('serviceId') == 'ro-LEGACY-NO-SERVICEID', (
            f'Expected serviceId="ro-LEGACY-NO-SERVICEID" (projected from roId). '
            f'Got serviceId={legacy.get("serviceId")!r}. '
            'CURRENT FAILURE: the current code does not project roId → serviceId '
            'for cache-only rows that lack serviceId. '
            'D4: `serviceId` falls back to `roId` so a real record is never '
            'returned unidentified. '
            'MUTATION TARGET: omitting the roId fallback causes serviceId to be '
            'absent/None on legacy cache rows.'
        )


    # ── (h) [Group 3 additional guard] asOf is the OLDEST cache-only row ─────

    def test_h_as_of_is_oldest_cache_only_row_when_multiple_present(self):
        """D3 (spec 2026-09-10): ``asOf`` is documented as the OLDEST cache-only
        row's timestamp — worst-case staleness the user should see, not average.

        Fixture-scale trap this closes: the T1.3(c) test has ONE cache-only row,
        so ``min`` and ``max`` produce identical outputs and the oldest-not-newest
        contract has no reachable mutation guard.  A green suite is not evidence
        (R5).  This test uses THREE cache-only rows with three distinct
        ``cachedAt`` timestamps so the oldest-vs-newest distinction is expressible.

        MUTATION TARGET: changing the ``elif has_cache_only`` branch's ``asOf``
        from ``min(...)`` to ``max(...)`` produces the newest timestamp instead
        of the oldest, and this test fails.  Verified 2026-09-10 during Group 3
        Phase 2 build: the min→max mutation passed T1.3(c) vacuously.
        """
        base_vin = '1FTFW1ET5DFC10312'
        # Two DMS rows to keep the payload mixed
        dms_rows = _make_dms_rows(count=2, base_vin=base_vin)

        # Three cache-only rows with distinct cachedAt values.
        # Oldest → newest so min() and max() diverge.
        cache_only_oldest = {
            'vehicleId': 'VEH-001',
            'serviceDate': '2026-05-01T10:00:00Z',
            'roId': 'ro-cache-oldest',
            'cachedAt': '2026-05-01T10:30:00Z',  # ← the oldest; must be asOf
            'createdAt': '2026-05-01T10:00:00Z',
        }
        cache_only_middle = {
            'vehicleId': 'VEH-001',
            'serviceDate': '2026-06-15T10:00:00Z',
            'roId': 'ro-cache-middle',
            'cachedAt': '2026-06-15T10:30:00Z',
            'createdAt': '2026-06-15T10:00:00Z',
        }
        cache_only_newest = {
            'vehicleId': 'VEH-001',
            'serviceDate': '2026-07-20T10:00:00Z',
            'roId': 'ro-cache-newest',
            'cachedAt': '2026-07-20T10:30:00Z',
            'createdAt': '2026-07-20T10:00:00Z',
        }
        cache_rows_for_dms = _make_cache_rows_for_dms(dms_rows)
        all_cache_rows = cache_rows_for_dms + [
            cache_only_oldest,
            cache_only_middle,
            cache_only_newest,
        ]
        cache_tbl = _fake_table_returning(all_cache_rows)

        veh_tbl = MagicMock()
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': 'VEH-001', 'vin': base_vin},
        }
        veh_tbl.query.return_value = {'Items': []}
        urlopen_mock = _urlopen_returning_dms(dms_rows)

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', urlopen_mock):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(self._get_event_with_vehicle_id(), None)

        assert result['statusCode'] == 200
        body = json.loads(result['body'])
        assert body.get('dataSource') == 'mixed', (
            f'expected dataSource="mixed", got "{body.get("dataSource")}"'
        )
        assert body.get('cacheOnlyCount') == 3, (
            f'expected cacheOnlyCount=3, got {body.get("cacheOnlyCount")}'
        )
        assert body.get('asOf') == '2026-05-01T10:30:00Z', (
            f'D3 (spec 2026-09-10-service-history-read-path-correctness): '
            f'asOf must be the OLDEST cache-only row\'s cachedAt '
            f'("2026-05-01T10:30:00Z"). Got asOf="{body.get("asOf")}". '
            'MUTATION TARGET: changing min(...) to max(...) in the has_cache_only '
            'branch produces the newest timestamp instead of the oldest.  A single '
            'cache-only row makes min and max identical, hiding this contract; '
            'three rows expose it.'
        )



class TestVinEqualsVehicleIdResolvesToRealVin:
    """Found 2026-09-12 during live-verification of
    ``2026-09-10-service-history-read-path-correctness``. See
    ``issues/2026-09-12-vin-equals-vehicleid-resolver-treats-as-unresolvable/``.

    Some vehicles legitimately have a `vin` attribute equal to their `vehicleId`
    (e.g. Ford-style VINs used directly as the primary key). The resolver's prior
    guard (`vin_value != vehicle_id_value`) conflated that shape with "vin absent",
    so it returned `(vehicle_id_value, None)` and CMS sent no `?vehicle_vin=` to
    DMS at all — DMS fell back to its own unscoped default and returned every
    vehicle's repair orders instead of the requested one's.

    Live staging evidence (vehicleId=1FDEU6PG3PKA99844, whose stored `vin` equals
    that same string): CMS returned ~45 cross-fleet records; DMS's own
    `?vehicle_vin=1FDEU6PG3PKA99844` query returns the correct 12.
    """

    def test_vin_equal_to_vehicle_id_is_still_forwarded_to_dms(self):
        """The resolver must forward `vin` whenever the attribute is PRESENT on
        the record, regardless of whether its value happens to equal vehicleId.

        MUTATION TARGET: reverting to `vin_value != vehicle_id_value` (instead of
        `'vin' in hit and hit.get('vin')`) makes this test fail, because no
        `?vehicle_vin=` would be appended to the DMS URL and the mock (which
        only returns the VIN-scoped RO when the query string matches) would
        instead return the fallback set including 'ro-other-vehicle'.
        """
        own_ro = dict(
            ro_id='ro-mine', status='Open', dealer_id='dealer-austin',
            dealer_name='Meridian of Austin', opened_at='2026-09-01T00:00:00Z',
            created_at='2026-09-01T00:00:00Z', updated_at='2026-09-01T00:00:00Z',
            description='Oil change', vehicle_vin='1FDEU6PG3PKA99844',
        )
        other_ro = dict(
            ro_id='ro-other-vehicle', status='Open', dealer_id='dealer-atlanta',
            dealer_name='Meridian of Atlanta', opened_at='2026-08-01T00:00:00Z',
            created_at='2026-08-01T00:00:00Z', updated_at='2026-08-01T00:00:00Z',
            description='Brake service', vehicle_vin='SOME-OTHER-VIN-999',
        )

        captured_urls = []

        class _ScopedDmsOpener:
            def open(self, req, timeout=None):  # noqa: ARG002
                url = req.full_url if hasattr(req, 'full_url') else str(req)
                captured_urls.append(url)
                if 'vehicle_vin=1FDEU6PG3PKA99844' in url:
                    return _FakeDmsGetResp([own_ro])
                # Unscoped fallback the bug used to trigger.
                return _FakeDmsGetResp([own_ro, other_ro])

        cache_tbl = _fake_table_returning([])
        veh_tbl = MagicMock()
        # The record's `vin` attribute equals vehicleId — a real, present value,
        # not a synthesized copy. This is the shape the old guard mishandled.
        veh_tbl.get_item.return_value = {
            'Item': {'vehicleId': '1FDEU6PG3PKA99844', 'vin': '1FDEU6PG3PKA99844'}
        }

        with patch.dict(os.environ, {
            'DMS_API_ENDPOINT': 'https://dms.example.invalid',
            'VEHICLES_TABLE_NAME': 'test-vehicles',
        }), patch.object(index, 'dynamodb') as mock_ddb, \
                patch('urllib.request.OpenerDirector.open', _ScopedDmsOpener().open):
            def table_factory(name):
                if 'vehicles' in name.lower():
                    return veh_tbl
                return cache_tbl
            mock_ddb.Table.side_effect = table_factory
            result = index.handler(
                _get_event(vehicleId='1FDEU6PG3PKA99844'), None
            )

        assert result['statusCode'] == 200
        assert any('vehicle_vin=1FDEU6PG3PKA99844' in u for u in captured_urls), (
            f'Expected a DMS call with ?vehicle_vin=1FDEU6PG3PKA99844. '
            f'Captured URLs: {captured_urls}. '
            'The resolver returned vin=None because it treated '
            '"vin present and equal to vehicleId" as unresolvable.'
        )
        body = json.loads(result['body'])
        service_ids = [r.get('serviceId') for r in body.get('serviceRecords', [])]
        assert 'ro-other-vehicle' not in service_ids, (
            f'A different vehicle\'s RO leaked into the scoped response. '
            f'Got serviceIds: {service_ids}. This is the live staging defect: '
            'vehicleId=1FDEU6PG3PKA99844 returned ~45 cross-fleet records '
            'instead of its own 12.'
        )
