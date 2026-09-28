# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""T2.3 — the three Connected Services routes in ``main_api.index``.

Spec ``.kiro/specs/2026-09-10-cms-connected-services-consumer/`` T2.3.

## What T2.3 owns that the proxy deliberately does not

``connected_services_proxy.py``'s three public functions do NOT filter by fleet
scope — their docstrings say so explicitly, and that is by design: the DMS proxy
sets the precedent that CMS's own check is applied on CMS's side of the call
(``index.py:8046``), not folded into the upstream request. So the entire
authorization contract for this feature lives in ``index.py``, and therefore in
this file.

Two authorization surfaces, and they are NOT the same check:

* **GET** — a *filter*. An operator scoped to fleet A asking for the feed gets a
  200 containing only their own vehicles, not a 403. Returning 403 would be
  wrong (they are entitled to the route) and returning everything would be a
  cross-fleet leak.
* **POST / DELETE** — a *gate*. Enrolling or unenrolling names one VIN, so the
  answer is binary: 403 unless that VIN resolves to a vehicle in scope.

## The identifier hop that makes this non-trivial

``get_allowed_vehicle_ids()`` returns **vehicleIds**. The producer speaks
**VINs**. This repo has documented cases where the two diverge
(``issues/2026-09-05-vehicleid-diverges-from-vin/``) AND cases where a VIN is
used *as* the vehicleId (``issues/2026-09-12-vin-equals-vehicleid-resolver-
treats-as-unresolvable/``). Both shapes are live, so the tests below assert on
both, and an unresolvable VIN must be DENIED rather than compared as a string —
see ``test_unresolvable_vin_is_denied_not_string_compared``.

## Why these are the assertions and not others

Each test below is named for the property it checks and asserts THAT property,
not something adjacent to it. This spec has produced five contract divergences
that passed a green suite (``decisions.md``), and every one of them was a test
whose name promised more than its assertion delivered — most sharply a test
called ``test_snapshot_date_must_not_be_hardcoded`` that asserted only
``sd >= "2026-01-01"``, which any hardcoded 2026 date satisfies. So: the
filter tests assert the excluded VIN is ABSENT (not merely that the included one
is present), and the header test reads the header values (not merely that a
headers dict exists).

Run from ``modules/cms_ui/source/handlers/main_api/``::

    python3 -m pytest tests/test_connected_services_routes.py -v
"""
from __future__ import annotations

import json
import os
import sys
from unittest.mock import MagicMock

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_MAIN_API = os.path.dirname(_HERE)
for _d in (_MAIN_API, _HERE):
    if _d not in sys.path:
        sys.path.insert(0, _d)

# ── Module-level env + stubs, mirroring test_fail_open_authz.py ──────────────
#
# The six table names are required by the env-var guard that precedes the route
# chain (index.py:2372). Without them every request 500s BEFORE reaching any
# route, and the assertions below would be measuring the guard rather than the
# authorization check — the trap test_fail_open_authz.py documents in its own
# header.
os.environ.setdefault('AWS_REGION', 'us-east-1')
os.environ.setdefault('REDIS_ENDPOINT', '')
os.environ.setdefault('DRIVER_SELF_GUARD_ENABLED', 'false')
os.environ.setdefault('SAFETY_EVENTS_TABLE_NAME', 'test-safety-events')
os.environ.setdefault('VEHICLES_TABLE_NAME', 'test-vehicles')
os.environ.setdefault('FLEETS_TABLE_NAME', 'test-fleets')
os.environ.setdefault('DASHBOARD_METRICS_CACHE_TABLE', 'test-cache')
os.environ.setdefault('DRIVERS_TABLE_NAME', 'test-drivers')
os.environ.setdefault('SERVICE_HISTORY_TABLE_NAME', 'test-service-history')
os.environ.setdefault('FLEET_ENROLLMENT_TABLE_NAME', 'test-enrollment')
os.environ.setdefault('USER_POOL_ID', 'us-east-2_EXAMPLE')
os.environ.setdefault('CLIENT_ID', 'exampleclientid')

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

import connected_services_proxy as proxy  # noqa: E402
import index  # noqa: E402

_ROUTE = '/api/v1/connected-services/subscription-feed'

# Two VINs, both ISO 3779 valid. IN is inside the caller's fleet; OUT is not.
_VIN_IN = '1HGBH41JXMN109186'
_VIN_OUT = '5YJ3E1EA7HF000337'
#: A VIN whose vehicleId is the VIN itself — the live shape recorded in
#: issues/2026-09-12-vin-equals-vehicleid-resolver-treats-as-unresolvable/.
_VIN_SELF_KEYED = '1FDEU6PG3PKA99844'
_VEHICLE_IN = 'VEH-IN-001'
_VEHICLE_OUT = 'VEH-OUT-001'


def _claims(groups=None, fleet_ids=None):
    c: dict = {'email': 'operator@example.com'}
    if groups is not None:
        c['cognito:groups'] = groups
    if fleet_ids is not None:
        c['custom:fleetIds'] = fleet_ids
    return c


def _event(method, path, claims, body=None):
    return {
        'httpMethod': method,
        'path': path,
        'body': json.dumps(body) if body is not None else None,
        'requestContext': {'authorizer': {'claims': claims}},
        'queryStringParameters': {},
        'pathParameters': None,
        'headers': {},
    }


def _operator():
    """A fleet-operator scoped to exactly one fleet."""
    return _claims(groups='fleet-operator', fleet_ids='FLEET-A')


def _admin():
    return _claims(groups='platform-admin')


def _viewer():
    return _claims(groups='fleet-viewer')


def _fleetless_operator():
    """A fleet-operator whose `custom:fleetIds` is absent.

    `get_allowed_vehicle_ids()` returns an EMPTY SET for this caller, not None —
    it loops over `user_fleet_ids` and finds nothing. Empty-set and None are
    different answers ("you may see nothing" vs "you may see everything") and
    conflating them is exactly the defect
    `issues/2026-08-05-main-api-fail-open-authz-defaults/` records, where a
    fleetless caller received unscoped cross-fleet access. That was a P0 on this
    repo, live in prod.
    """
    return _claims(groups='fleet-operator')


def _records_body(vins_and_vehicles, unresolved=()):
    """A producer records payload matching the live-captured contract.

    Shape and key set come from `producer-live-shapes.json` via
    `proxy._RECORDS_REQUIRED_KEYS`, so a producer contract change breaks this
    fixture rather than letting it drift into fiction.
    """
    records = []
    for vin, vehicle_id in vins_and_vehicles:
        record = {
            'vin': vin,
            'signals': {'odometer': 12345},
            'timestamp': 1789245554121,
            'dataSourceRoute': 'cs-meridian',
        }
        if vehicle_id is not None:
            record['vehicleId'] = vehicle_id
        records.append(record)
    return {
        'subscription_id': '01ARZ3NDEKTSV4RRFFQ69G5FAV',
        'records': records,
        'count': len(records),
        'vins_in_scope': [v for v, _ in vins_and_vehicles] + list(unresolved),
        'unresolved_vins': list(unresolved),
        'quota': {'records_per_day': 10000, 'used_today': len(records)},
    }


@pytest.fixture(autouse=True)
def _proxy_config(monkeypatch):
    """Configure the proxy so tests exercise authorization, not config errors."""
    monkeypatch.setenv(proxy.ENV_PRODUCER_ENDPOINT, 'https://producer.example.test/prod')
    monkeypatch.setenv(proxy.ENV_CMS_SUBSCRIPTION_ID, '01ARZ3NDEKTSV4RRFFQ69G5FAV')
    monkeypatch.setenv('CS_SUBSCRIBER_SECRET_NAME', 'cms-test-cs-subscriber')


@pytest.fixture(autouse=True)
def _stub_dependencies(monkeypatch):
    """Stub only at the AWS and HTTP boundaries — never the logic under test.

    Deliberately NOT test-only injection hooks. An earlier draft of this file
    monkeypatched an `index._CS_TEST_ALLOWED` set and an `index._CS_TEST_VIN_MAP`
    dict, which would have meant the real `get_allowed_vehicle_ids()` and the
    real VIN resolver never executed — and those two functions ARE the contract
    (the resolver in particular is where the fail-open lives). So the fake sits
    at `index.dynamodb`, dispatching by table name, and the production code path
    runs end to end above it.
    """
    calls: dict = {'http': []}

    class _Http:
        def request(self, method, url, headers, body=None, timeout=8):
            calls['http'].append(
                {'method': method, 'url': url, 'body': body,
                 'headers': dict(headers)}
            )
            if method == 'GET':
                return (200, _records_body([(_VIN_IN, _VEHICLE_IN),
                                            (_VIN_OUT, _VEHICLE_OUT)]))
            if method == 'POST':
                return (200, {'subscription_id': 'sub', 'added': [body['vin']],
                              'already_present': [], 'scope_size': 1})
            return (200, {'subscription_id': 'sub', 'vin': url.rsplit('/', 1)[-1],
                          'removed': True, 'scope_size': 0})

    monkeypatch.setattr(index, '_cs_http_client', lambda: _Http(), raising=False)
    monkeypatch.setattr(
        index, '_cs_token_provider',
        lambda: proxy.StaticTokenProvider(token='test-token'), raising=False,
    )
    index._CS_TEST_CALLS = calls
    yield calls


class _FakeTable:
    """Minimal DynamoDB Table double for the two tables these routes touch."""

    def __init__(self, name, state):
        self._name = name
        self._state = state

    def query(self, **kwargs):
        if 'enrollment' in self._name:
            # `get_allowed_vehicle_ids()` builds its KeyConditionExpression with
            # a boto3 `Key()` object, which is a MagicMock here and therefore
            # opaque. Returning the whole enrollment list is exact anyway: the
            # test callers hold exactly one fleet.
            return {'Items': [{'vehicleId': v}
                              for v in self._state['enrollment']]}
        if kwargs.get('IndexName') == 'vin-index':
            vin = (kwargs.get('ExpressionAttributeValues') or {}).get(':vin')
            vehicle_id = self._state['vin_index'].get(vin)
            return {'Items': [{'vehicleId': vehicle_id}] if vehicle_id else []}
        return {'Items': []}

    def get_item(self, Key=None, **kwargs):  # noqa: N803 — boto3's own casing
        key = (Key or {}).get('vehicleId')
        if key and key in self._state['vehicle_ids']:
            return {'Item': {'vehicleId': key}}
        return {}


@pytest.fixture(autouse=True)
def _fake_dynamodb(monkeypatch):
    """Wire a table-name-dispatching fake into `index.dynamodb`.

    Default state: the caller's fleet holds _VEHICLE_IN and _VIN_SELF_KEYED.
    _VIN_OUT resolves fine — it is a real vehicle — it is just someone else's,
    which is the case that matters. A VIN that resolved to nothing would test
    the resolver, not the scope check.
    """
    state = {
        'enrollment': [_VEHICLE_IN, _VIN_SELF_KEYED],
        'vin_index': {_VIN_IN: _VEHICLE_IN, _VIN_OUT: _VEHICLE_OUT},
        # _VIN_SELF_KEYED is absent from vin-index and present as a primary key:
        # the `vin == vehicleId` shape, resolved by step 2 of the resolver.
        'vehicle_ids': {_VEHICLE_IN, _VEHICLE_OUT, _VIN_SELF_KEYED},
    }

    class _FakeDdb:
        def Table(self, name):  # noqa: N802 — boto3's own casing
            return _FakeTable(name, state)

    monkeypatch.setattr(index, 'dynamodb', _FakeDdb())
    return state


def _invoke(event):
    return index.handler(event, MagicMock())


def _body(response):
    return json.loads(response['body'])


# ── Premise guards ──────────────────────────────────────────────────────────

def test_premise_routes_are_reachable_at_all() -> None:
    """A 404 here means every assertion below is measuring the fallback route.

    The first version of this suite would have passed several tests against the
    generic "Endpoint not found" response, because a 404 is not a 200 and the
    negative assertions were written as "the out-of-scope VIN is absent".
    """
    response = _invoke(_event('GET', _ROUTE, _admin()))
    assert response['statusCode'] != 404, (
        'GET the feed route returned 404 — the route is not wired, so the '
        'authorization assertions below cannot be measuring anything'
    )


def test_premise_fixture_feed_contains_both_an_in_and_out_of_scope_vehicle() -> None:
    """A filter test over a single-vehicle feed passes trivially."""
    payload = _records_body([(_VIN_IN, _VEHICLE_IN), (_VIN_OUT, _VEHICLE_OUT)])
    vins = {r['vin'] for r in payload['records']}
    assert vins == {_VIN_IN, _VIN_OUT}
    proxy.validate_records_shape(payload)  # fixture must satisfy the real contract


# ── GET: a filter, not a gate ───────────────────────────────────────────────

def test_get_filters_records_to_the_callers_fleet() -> None:
    response = _invoke(_event('GET', _ROUTE, _operator()))
    assert response['statusCode'] == 200
    vins = {r['vin'] for r in _body(response)['records']}
    assert _VIN_IN in vins, 'the in-scope vehicle was filtered out'
    assert _VIN_OUT not in vins, (
        'a vehicle outside the caller\'s fleet scope reached the response — '
        'cross-fleet leak'
    )


def test_get_recomputes_count_after_filtering() -> None:
    """`count` must describe what CMS returns, not what the producer sent.

    Passing the producer's `count` through unchanged would make the card say
    "2 records" above a list of 1. The proxy's own shape validator enforces
    count == len(records) on the way IN for exactly this reason; the same
    invariant has to hold on the way out.
    """
    response = _invoke(_event('GET', _ROUTE, _operator()))
    payload = _body(response)
    assert payload['count'] == len(payload['records']) == 1


def test_get_filters_the_vin_lists_not_just_the_records() -> None:
    """`vins_in_scope` leaks VINs even when `records` is filtered.

    The subscription's scope list is a set of VINs CMS's subscriber account
    holds — which is a superset of any one operator's fleet. Filtering
    `records` while passing `vins_in_scope` through verbatim would leak exactly
    the identifiers the filter exists to withhold, while looking correct in the
    UI because the card renders records.
    """
    response = _invoke(_event('GET', _ROUTE, _operator()))
    payload = _body(response)
    assert _VIN_OUT not in payload.get('vins_in_scope', []), (
        'vins_in_scope leaked an out-of-scope VIN'
    )
    assert _VIN_IN in payload.get('vins_in_scope', [])


def test_get_drops_unresolved_vins_for_a_scoped_caller(monkeypatch) -> None:
    """An unresolved VIN cannot be authorized, so a scoped caller must not see it.

    `unresolved_vins` is real and non-empty in the live capture: a VIN can be in
    the subscription's scope and still resolve to no vehicle. With no vehicleId
    there is nothing to compare against `get_allowed_vehicle_ids()`, so the only
    fail-closed answer is to withhold it. An unscoped caller still sees them —
    that is `test_admin_sees_everything_including_unresolved`.
    """
    unresolved = '3VWFE21C04M000001'

    class _Http:
        def request(self, method, url, headers, body=None, timeout=8):
            return (200, _records_body([(_VIN_IN, _VEHICLE_IN)],
                                       unresolved=(unresolved,)))

    monkeypatch.setattr(index, '_cs_http_client', lambda: _Http(), raising=False)
    payload = _body(_invoke(_event('GET', _ROUTE, _operator())))
    assert unresolved not in payload.get('unresolved_vins', [])
    assert unresolved not in payload.get('vins_in_scope', [])


def test_get_drops_a_record_with_no_vehicleid(monkeypatch) -> None:
    """`vehicleId` is not a required key, so its absence must fail closed.

    `proxy._RECORD_REQUIRED_KEYS` is `{vin, signals, timestamp}` — `vehicleId`
    is present-in-practice but deliberately not enforced, so that a producer
    legitimately omitting it for one record does not black out the whole feed.
    That makes it a value this filter cannot rely on, and a record CMS cannot
    attribute to a vehicle is a record it cannot authorize.
    """
    class _Http:
        def request(self, method, url, headers, body=None, timeout=8):
            return (200, _records_body([(_VIN_IN, None)]))

    monkeypatch.setattr(index, '_cs_http_client', lambda: _Http(), raising=False)
    payload = _body(_invoke(_event('GET', _ROUTE, _operator())))
    assert payload['records'] == [], (
        'a record with no vehicleId was returned to a scoped caller — it cannot '
        'be attributed to a vehicle, so it cannot be authorized'
    )


def test_admin_sees_everything_including_unresolved(monkeypatch) -> None:
    """Unscoped access means no filter, matching `get_allowed_vehicle_ids()`'s None."""
    unresolved = '3VWFE21C04M000001'

    class _Http:
        def request(self, method, url, headers, body=None, timeout=8):
            return (200, _records_body([(_VIN_IN, _VEHICLE_IN),
                                        (_VIN_OUT, _VEHICLE_OUT)],
                                       unresolved=(unresolved,)))

    monkeypatch.setattr(index, '_cs_http_client', lambda: _Http(), raising=False)
    payload = _body(_invoke(_event('GET', _ROUTE, _admin())))
    vins = {r['vin'] for r in payload['records']}
    assert vins == {_VIN_IN, _VIN_OUT}
    assert unresolved in payload['unresolved_vins']


# ── POST / DELETE: a gate ───────────────────────────────────────────────────

@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_mutating_routes_deny_a_vin_outside_scope(method) -> None:
    response = _invoke(_event(method, f'{_ROUTE}/{_VIN_OUT}', _operator()))
    assert response['statusCode'] == 403, (
        f'{method} on an out-of-scope VIN returned {response["statusCode"]}; '
        'a fleet operator must not mutate another fleet\'s subscription scope'
    )


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_mutating_routes_allow_a_vin_in_scope(method) -> None:
    """The positive control. Without it, a route that 403s unconditionally passes."""
    response = _invoke(_event(method, f'{_ROUTE}/{_VIN_IN}', _operator()))
    assert response['statusCode'] == 200, (
        f'{method} on an in-scope VIN returned {response["statusCode"]}'
    )


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_mutating_routes_never_reach_the_producer_when_denied(method) -> None:
    """A 403 must be decided before the call, not after.

    Calling the producer and then discarding the result would still mutate CMS's
    subscription scope upstream — the enroll would have happened and only the
    response would be withheld. For DELETE that is a silent cross-fleet
    unenrollment.
    """
    index._CS_TEST_CALLS['http'].clear()
    _invoke(_event(method, f'{_ROUTE}/{_VIN_OUT}', _operator()))
    assert index._CS_TEST_CALLS['http'] == [], (
        f'{method} reached the producer despite being denied — the scope check '
        'must gate the call, not filter its result'
    )


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_vin_used_as_its_own_vehicleid_is_allowed(method) -> None:
    """The `vin == vehicleId` shape is live and must not be treated as a miss.

    issues/2026-09-12-vin-equals-vehicleid-resolver-treats-as-unresolvable/
    records the cost of getting this backwards: a resolver that inferred
    "unresolvable" from `vin == vehicleId` dropped the scope filter and returned
    ~45 cross-fleet rows for a vehicle with 12.
    """
    response = _invoke(_event(method, f'{_ROUTE}/{_VIN_SELF_KEYED}', _operator()))
    assert response['statusCode'] == 200


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_unresolvable_vin_is_denied_not_string_compared(method, _fake_dynamodb) -> None:
    """THE fail-open this route must not have.

    `_resolve_vin_for_vehicle_id` (index.py, owned by the service-history spec)
    returns `(vehicle_id_value, None)` for an unresolvable value — it echoes its
    input back as the resolved id. That is benign for its own cache-read caller
    and fail-open-shaped for an authorization check: "resolved" and
    "unresolvable" become the same return value, so the deny decision collapses
    into whether the VIN string happens to collide with an allowed vehicleId.
    Since `vin == vehicleId` is a real shape here, that collision is not
    hypothetical.

    So the resolver behind these routes must return None on a miss, and a miss
    must be a 403. Asserted by making a VIN that IS in the allowed set
    unresolvable — removing it from both the index and the primary-key set while
    leaving it in the fleet's enrollment list. A string comparison admits it; a
    real resolution denies it.
    """
    _fake_dynamodb['vin_index'].pop(_VIN_SELF_KEYED, None)
    _fake_dynamodb['vehicle_ids'].discard(_VIN_SELF_KEYED)
    assert _VIN_SELF_KEYED in _fake_dynamodb['enrollment'], (
        'premise: the VIN must still be in the allowed set, or this test '
        'proves nothing about string comparison'
    )
    response = _invoke(_event(method, f'{_ROUTE}/{_VIN_SELF_KEYED}', _operator()))
    assert response['statusCode'] == 403, (
        'an unresolvable VIN was admitted because its string matched an allowed '
        'vehicleId — the resolver is echoing its input instead of resolving'
    )


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_read_only_principals_are_denied_on_mutating_routes(method) -> None:
    """fleet-viewer is unscoped-READ, not unscoped-write.

    `has_unscoped_access` is `is_admin or is_viewer`, so a viewer passes the
    scope check. Gating only on scope would therefore let a read-only auditor
    mutate CMS's subscription. `_deny_viewer()` is the single predicate that
    covers both fleet-viewer and fleet-guest; index.py records that five routes
    once inlined `if is_viewer:` and adding fleet-guest missed every one.
    """
    response = _invoke(_event(method, f'{_ROUTE}/{_VIN_IN}', _viewer()))
    assert response['statusCode'] == 403


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_fleetless_operator_cannot_mutate_any_vin(method, _fake_dynamodb) -> None:
    """An empty allowed-set must deny everything, not permit everything.

    Added because mutation M10b survived the suite: gating the scope check on
    `if not has_unscoped_access and get_allowed_vehicle_ids():` reads as a
    harmless short-circuit and is a fail-open. A fleet-operator with no
    `custom:fleetIds` resolves to an empty set, the guard goes falsy, the check
    is skipped, and that caller can enroll or unenroll ANY VIN in CMS's
    subscription.

    This is the exact shape of `issues/2026-08-05-main-api-fail-open-authz-
    defaults/` — fleetless treated as unscoped — which was a P0 live in prod on
    this repo. It is worth its own test rather than trusting the `or set()`
    form to keep reading correctly to whoever edits it next.
    """
    _fake_dynamodb['enrollment'] = []
    response = _invoke(_event(method, f'{_ROUTE}/{_VIN_IN}', _fleetless_operator()))
    assert response['statusCode'] == 403, (
        'a fleet-operator with no fleets mutated a VIN — empty allowed-set was '
        'treated as unscoped access'
    )


def test_fleetless_operator_sees_no_records(_fake_dynamodb) -> None:
    """The GET half of the same property: empty scope filters everything out."""
    _fake_dynamodb['enrollment'] = []
    payload = _body(_invoke(_event('GET', _ROUTE, _fleetless_operator())))
    assert payload['records'] == []
    assert payload['count'] == 0
    assert payload['vins_in_scope'] == []


def test_read_only_principal_may_still_read_the_feed() -> None:
    """Positive control for the test above — viewer is read-ONLY, not no-access."""
    assert _invoke(_event('GET', _ROUTE, _viewer()))['statusCode'] == 200


@pytest.mark.parametrize('bad_vin', ['NOTAVIN', '1HGBH41JXMN10918', 'AAAAAAAAAAAAAAAAI'])
def test_malformed_vin_is_rejected(bad_vin) -> None:
    """Too short, wrong length, and a disallowed ISO 3779 character.

    The proxy allow-lists exactly 17 chars from `[A-HJ-NPR-Z0-9]`, matching the
    producer. A 16-char VIN once passed CMS, earned a producer 400, and surfaced
    as a 502 `producer_unavailable` — an upstream-outage story for a bad input.
    """
    response = _invoke(_event('POST', f'{_ROUTE}/{bad_vin}', _admin()))
    assert response['statusCode'] == 400, (
        f'malformed VIN {bad_vin!r} returned {response["statusCode"]}'
    )


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_lowercase_in_scope_vin_is_authorized(method) -> None:
    """Normalization must happen BEFORE the scope check, not only inside the proxy.

    Added because mutation M11 — deleting the route's `_normalize_vin` call —
    survived the suite. `test_malformed_vin_is_rejected` could not catch it:
    `enroll_vin`/`unenroll_vin` normalize again internally and return the same
    400, so the status code is identical whichever layer rejects.

    What M11 actually breaks is the AUTHORIZATION step, which runs first and
    would then resolve the raw string. The producer normalizes case
    (`v.strip().upper()`), and T2.1 found that CMS and the producer had already
    disagreed about case once. So a lowercase VIN is a VIN the caller owns —
    it must resolve, and it must be allowed. Un-normalized it misses the
    vin-index, resolves to None, and is denied 403: a permissions error for a
    vehicle the operator actually holds. Fails closed, so not a leak, but wrong.
    """
    response = _invoke(_event(method, f'{_ROUTE}/{_VIN_IN.lower()}', _operator()))
    assert response['statusCode'] == 200, (
        f'a lowercase in-scope VIN returned {response["statusCode"]} — the VIN '
        'reached the scope check before being normalized, so it missed the '
        'vin-index and was denied'
    )


@pytest.mark.parametrize('method', ['POST', 'DELETE'])
def test_normalized_vin_is_what_travels_to_the_producer(method) -> None:
    """The producer must receive the canonical form, not the caller's casing.

    For DELETE the VIN is interpolated into the URL path, so sending a lowercase
    value would make the producer's `removed: bool` answer about a VIN it does
    not store — reporting a miss as a successful no-op.
    """
    index._CS_TEST_CALLS['http'].clear()
    _invoke(_event(method, f'{_ROUTE}/{_VIN_IN.lower()}', _operator()))
    assert index._CS_TEST_CALLS['http'], 'nothing reached the producer'
    call = index._CS_TEST_CALLS['http'][0]
    travelled = json.dumps(call['body']) if method == 'POST' else call['url']
    assert _VIN_IN in travelled
    assert _VIN_IN.lower() not in travelled


# ── Config, errors, headers ─────────────────────────────────────────────────

def test_missing_config_is_a_502_not_a_500(monkeypatch) -> None:
    """`ProxyConfig.from_env()` raises KeyError; the route maps it to 502.

    A 500 would read as an application bug. The distinction matters on demo day:
    `config_missing` names the actual remediation (an unset env var on this
    stage), and it is the state a stage with no producer deployed is IN, by
    design — `CS_PRODUCER_API_ENDPOINT` is legitimately empty there.
    """
    monkeypatch.delenv(proxy.ENV_PRODUCER_ENDPOINT, raising=False)
    response = _invoke(_event('GET', _ROUTE, _admin()))
    assert response['statusCode'] == 502
    assert _body(response)['error'] == proxy.ERROR_CONFIG_MISSING


def test_empty_endpoint_is_config_missing_not_a_crash(monkeypatch) -> None:
    """The gated-import case: the var is SET but empty on a producer-less stage.

    `ProxyConfig.__post_init__` raises ValueError (not KeyError) for an empty
    endpoint, so a handler catching only KeyError would turn the most likely
    real-world configuration into a 500.
    """
    monkeypatch.setenv(proxy.ENV_PRODUCER_ENDPOINT, '')
    response = _invoke(_event('GET', _ROUTE, _admin()))
    assert response['statusCode'] == 502
    assert _body(response)['error'] == proxy.ERROR_CONFIG_MISSING


def test_shape_drift_does_not_render_as_no_telemetry(monkeypatch) -> None:
    """A 502 producer_shape_drift must survive the filter, not become an empty list.

    The filter runs over `records`; a drifted 502 body has no `records` key. A
    filter that treated a missing key as an empty list would convert "we could
    not read the feed" into "this vehicle has no telemetry" — the exact
    substitution R1 and the DMS proxy's D5 rule both exist to prevent.
    """
    class _Http:
        def request(self, method, url, headers, body=None, timeout=8):
            return (200, {'records': [], 'count': 0})  # missing 4 required keys

    monkeypatch.setattr(index, '_cs_http_client', lambda: _Http(), raising=False)
    response = _invoke(_event('GET', _ROUTE, _operator()))
    assert response['statusCode'] == 502
    assert _body(response)['error'] == proxy.ERROR_PRODUCER_SHAPE_DRIFT


@pytest.mark.parametrize(
    'method,path',
    [('GET', _ROUTE), ('POST', f'{_ROUTE}/{_VIN_IN}'), ('DELETE', f'{_ROUTE}/{_VIN_IN}'),
     # Review cycle 1: the three above are the SUPPORTED methods, and
     # parametrizing only those left the 405 path unexercised — it was building
     # a raw dict and shipping without any of these headers. An unsupported
     # method on the collection AND on the item path, since they are separate
     # branches.
     ('PUT', _ROUTE), ('PUT', f'{_ROUTE}/{_VIN_IN}')],
)
def test_security_headers_are_stamped_on_every_response(method, path) -> None:
    """Security-review SG5, on every response this route can produce.

    A browser caching a 502 from a transient producer outage would keep showing
    the operator an outage that has since cleared. `nosniff` matters because the
    error path interpolates a producer-supplied `detail` string into a JSON body.
    """
    headers = _invoke(_event(method, path, _admin()))['headers']
    assert headers.get('Cache-Control') == 'no-store'
    assert headers.get('X-Content-Type-Options') == 'nosniff'
    assert headers.get('Content-Type') == 'application/json'


def test_unsupported_method_is_405_not_404() -> None:
    """Premise for the PUT cases above.

    If PUT fell through to the generic 404 the header assertions would be
    measuring the fallback route, which does not carry these headers and never
    claimed to — the test would fail for the wrong reason and be "fixed" by
    weakening it.
    """
    assert _invoke(_event('PUT', _ROUTE, _admin()))['statusCode'] == 405


@pytest.mark.parametrize(
    'label,method,path,claims,expected_status',
    [
        # The two DENIAL paths. Both were found shipping without the SG5
        # headers — the 405 by review cycle 1, the scope-denial 403 by security
        # review cycle 1 — because the parametrized header test above used
        # `_admin()` claims and so never reached either branch. A denial is the
        # response most worth not caching: it is the one whose answer changes
        # when an operator's fleet assignment changes.
        ('scope-denied operator', 'POST', f'{_ROUTE}/{_VIN_OUT}', _operator, 403),
        ('scope-denied operator', 'DELETE', f'{_ROUTE}/{_VIN_OUT}', _operator, 403),
        ('read-only principal', 'POST', f'{_ROUTE}/{_VIN_IN}', _viewer, 403),
        ('read-only principal', 'DELETE', f'{_ROUTE}/{_VIN_IN}', _viewer, 403),
        ('malformed vin', 'POST', f'{_ROUTE}/NOTAVIN', _admin, 400),
    ],
)
def test_security_headers_are_stamped_on_denial_paths(
    label, method, path, claims, expected_status
) -> None:
    response = _invoke(_event(method, path, claims()))
    assert response['statusCode'] == expected_status, (
        f'{label}: expected {expected_status}, got {response["statusCode"]} — '
        'this test is not landing on the branch it means to'
    )
    headers = response['headers']
    assert headers.get('Cache-Control') == 'no-store', (
        f'{label} ({method}) response is cacheable'
    )
    assert headers.get('X-Content-Type-Options') == 'nosniff'
    assert headers.get('Content-Type') == 'application/json'


def test_every_cs_response_goes_through_proxyresult() -> None:
    """Structural: no hand-built response dict in the Connected Services block.

    Three instances of one shape were found in this task — the 405 path, the
    scope-denial 403, and `_deny_viewer()`'s dict returned verbatim — each
    caught by a different reader, each after the previous one was fixed. At
    three, the answer stops being "fix the third site" and becomes "make a
    fourth site detectable without a reviewer".

    So this parses the route block and asserts every `return` in it either
    yields a `ProxyResult(...).to_lambda_response(...)` or hands back a value
    produced by one. A raw `{'statusCode': ...}` literal anywhere in the block
    fails here, which is what SG5's "visible in one place" actually requires.

    Scoped to the block by its own sentinel comments rather than by line
    numbers, so it survives edits elsewhere in this 9000-line file.
    """
    import ast
    import inspect

    src = inspect.getsource(index)
    start_marker = "# ── Connected Services subscription feed (spec"
    assert src.count(start_marker) == 1, (
        'route-block sentinel not found exactly once — this test can no longer '
        'locate the region it audits'
    )
    start = src.index(start_marker)
    # End at the generic 404, which is the next statement AFTER this block and
    # legitimately hand-built — it predates this spec and belongs to no route.
    # Anchored on the `return {` plus its status line so the marker is unique
    # and so the 404's own lines fall outside the audited slice.
    end_marker = "        return {\n            'statusCode': 404,"
    assert src.count(end_marker) == 1, (
        'end sentinel not found exactly once — the audited slice may now be '
        'wider or narrower than the route block'
    )
    block = src[start: src.index(end_marker)]

    # Premise: the block must actually contain the routes, or an absence check
    # over the wrong slice passes trivially.
    assert '_cs_filter_feed' in block and 'enroll_vin' in block, (
        'the extracted block does not contain the route bodies'
    )

    # `'statusCode':` with the colon matches a dict-literal KEY. It deliberately
    # does not match `_cs_denied['statusCode']`, which is a READ of another
    # function's response in order to re-wrap it through ProxyResult — the fix,
    # not the defect.
    offenders = [
        line.strip()
        for line in block.splitlines()
        if "'statusCode':" in line and not line.strip().startswith('#')
    ]
    assert offenders == [], (
        'hand-built response dict(s) in the Connected Services route block — '
        'every response must go through ProxyResult.to_lambda_response so the '
        f'SG5 headers cannot be forgotten: {offenders}'
    )
    # And the block must parse — an aborted edit in this spec once left a body
    # unreachable under a `raise` while still parsing cleanly, so parsing is a
    # floor, not a ceiling.
    ast.parse(inspect.getsource(index))


def test_security_headers_do_not_displace_cors() -> None:
    """CORS must survive the addition, or every browser call fails opaquely."""
    headers = _invoke(_event('GET', _ROUTE, _admin()))['headers']
    assert headers.get('Access-Control-Allow-Origin') == '*'


def test_security_headers_are_stamped_on_the_error_path() -> None:
    """The caching hazard SG5 names IS the error path, so assert it there too."""
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv(proxy.ENV_PRODUCER_ENDPOINT, raising=False)
        response = _invoke(_event('GET', _ROUTE, _admin()))
    assert response['statusCode'] == 502
    assert response['headers'].get('Cache-Control') == 'no-store'


def test_no_credential_or_token_reaches_the_response_body() -> None:
    """CMS's subscriber credential is server-side only (spec D2, hard constraint).

    Exposing it would let any CMS frontend user act as CMS's subscriber account
    directly against the producer, bypassing the fleet-scope filter entirely.
    """
    for event in (
        _event('GET', _ROUTE, _admin()),
        _event('POST', f'{_ROUTE}/{_VIN_IN}', _admin()),
    ):
        raw = _invoke(event)['body'].lower()
        for forbidden in ('test-token', 'password', 'bearer', 'authorization',
                          'idtoken', 'refreshtoken'):
            assert forbidden not in raw, (
                f'{forbidden!r} appeared in a response body'
            )


def test_caller_jwt_is_never_forwarded_to_the_producer() -> None:
    """Spec D2: CMS authenticates as itself, never as the end user.

    Asserted on the wire rather than by inspecting signatures — the proxy module
    already has a signature-level guard, and this is the complementary check
    that the route did not hand the caller's own header through some other path.
    """
    index._CS_TEST_CALLS['http'].clear()
    event = _event('GET', _ROUTE, _admin())
    event['headers'] = {'Authorization': 'Bearer caller-jwt-must-not-travel'}
    _invoke(event)
    for call in index._CS_TEST_CALLS['http']:
        assert 'caller-jwt-must-not-travel' not in json.dumps(call)


def test_full_vin_is_not_logged(capsys) -> None:
    """VINs are never written to logs in full — `_redact_vin`'s stated contract.

    The denial path is the one that logs, and it is the one holding a VIN the
    caller was not entitled to.
    """
    _invoke(_event('POST', f'{_ROUTE}/{_VIN_OUT}', _operator()))
    captured = capsys.readouterr()
    assert _VIN_OUT not in (captured.out + captured.err), (
        'a full VIN reached stdout/stderr'
    )
