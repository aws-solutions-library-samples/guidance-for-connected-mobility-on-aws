# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""T2.6 — the feed cache: D3's freshness half.

Spec `.kiro/specs/2026-09-10-cms-connected-services-consumer/`.

Before this task, `cms-{stage}-storage-cs-feed-cache-*` was deployed (ACTIVE, TTL
enabled) and **nothing read or wrote it**. Every feed read was a live producer
call. D3's authorization property held regardless — the fleet-scope check is
CMS-side and independent of where the payload came from — but its freshness
property did not exist, and T2.0b's own text described "the table T2.3's routes
read and write", which was not true.

THE ASSERTION THAT MATTERS MOST
-------------------------------
`test_cached_payload_is_never_the_filtered_view`. The cache's primary key is
`(subscription_id, 'latest')` with **no caller component**, which is correct only
because the cached value is the producer-shaped, unfiltered payload that
`index.py` then filters per caller. Cache the *filtered* view under that same key
and one operator's rows are served to the next — a cross-fleet leak with a
cache-hit-rate justification. This file pins the invariant rather than trusting
the comment that states it.
"""

from __future__ import annotations

import json
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import connected_services_proxy as proxy  # noqa: E402

SUB_ID = "01M2BG77STPWC5N9953BKVENS9"
VIN_A = "MRDN0000000000005"
VIN_B = "MRDN0000000000009"


def _feed(*vins: str) -> dict:
    """A producer-shaped records payload. `count` must equal len(records) —
    the proxy's own validator enforces it on the way in."""
    records = [
        {
            "vin": v,
            "vehicleId": f"VEH-{v[-4:]}",
            "signals": {"odometer_km": 41234.5},
            "dataSourceRoute": "cs-meridian",
            "timestamp": 1789245554121,
        }
        for v in vins
    ]
    return {
        "subscription_id": SUB_ID,
        "records": records,
        "count": len(records),
        "vins_in_scope": list(vins),
        "unresolved_vins": [],
        "quota": {"records_per_call": 100},
    }


class CountingHttpClient:
    """Producer double that counts calls, so "one producer call" is measurable."""

    def __init__(self, payload: dict, status: int = 200):
        self.payload = payload
        self.status = status
        self.calls = 0

    def request(self, *, method, url, headers, body=None):  # noqa: ANN001
        self.calls += 1
        return self.status, dict(self.payload)


class InMemoryCache:
    """A `FeedCache` double with real TTL semantics and call counters."""

    def __init__(self):
        self.rows: dict[tuple[str, str], dict] = {}
        self.gets = 0
        self.puts = 0

    def get_latest(self, subscription_id):  # noqa: ANN001
        self.gets += 1
        row = self.rows.get((subscription_id, proxy.FEED_CACHE_LATEST_KEY))
        if row is None:
            return None
        if row["ttl"] <= int(time.time()):
            return None
        return json.loads(row["payload"])

    def put_latest(self, subscription_id, payload, ttl_seconds):  # noqa: ANN001
        self.puts += 1
        self.rows[(subscription_id, proxy.FEED_CACHE_LATEST_KEY)] = {
            "payload": json.dumps(payload),
            "ttl": int(time.time()) + ttl_seconds,
        }


class StaticTokens:
    def get_token(self):
        return "token"


def _config(**kw) -> proxy.ProxyConfig:
    base = dict(
        producer_endpoint="https://producer.example.test/prod",
        cms_subscription_id=SUB_ID,
        feed_cache_table="cms-test-storage-cs-feed-cache-us-west-2-000000000000",
        feed_cache_ttl_seconds=60,
    )
    base.update(kw)
    return proxy.ProxyConfig(**base)


def _get(config, http, cache):  # noqa: ANN001
    return proxy.get_subscription_feed(
        config=config,
        token_provider=StaticTokens(),
        http_client=http,
        cache=cache,
    )


# ── The Verify clause's three required proofs ──────────────────────────────

def test_two_reads_inside_the_ttl_make_one_producer_call():
    http = CountingHttpClient(_feed(VIN_A, VIN_B))
    cache = InMemoryCache()
    config = _config()

    first = _get(config, http, cache)
    second = _get(config, http, cache)

    assert first.status_code == 200 and second.status_code == 200
    assert http.calls == 1, f"expected 1 producer call inside the TTL, got {http.calls}"
    assert cache.puts == 1
    assert second.body["records"] == first.body["records"]


def test_a_read_after_the_ttl_expires_calls_the_producer_again():
    http = CountingHttpClient(_feed(VIN_A))
    cache = InMemoryCache()
    # Negative window: already-expired the instant it is written, without a sleep.
    # A `time.sleep` here would trade a second of suite time for no extra
    # confidence, and a monkeypatched clock would test the patch.
    config = _config(feed_cache_ttl_seconds=-1)

    _get(config, http, cache)
    _get(config, http, cache)

    # cache_enabled is False for a non-positive window, so the cache is bypassed
    # entirely rather than written-and-immediately-stale.
    assert http.calls == 2
    assert cache.puts == 0


def test_expired_row_is_a_miss_not_a_stale_hit():
    """TTL is enforced on READ, not left to DynamoDB's eviction."""
    http = CountingHttpClient(_feed(VIN_A))
    cache = InMemoryCache()
    config = _config()

    _get(config, http, cache)
    # Age the row past its TTL in place.
    key = (SUB_ID, proxy.FEED_CACHE_LATEST_KEY)
    cache.rows[key]["ttl"] = int(time.time()) - 1

    _get(config, http, cache)
    assert http.calls == 2, "an expired row must be re-fetched, not served"


def test_cached_payload_is_never_the_filtered_view():
    """The invariant the primary key depends on.

    `(subscription_id, 'latest')` has no caller component. That is correct ONLY
    because the value is unfiltered. This asserts the stored bytes contain rows
    for BOTH VINs — i.e. what the producer returned — so a future change that
    cached a per-operator view would fail here rather than leak across fleets.
    """
    http = CountingHttpClient(_feed(VIN_A, VIN_B))
    cache = InMemoryCache()

    _get(_config(), http, cache)

    stored = json.loads(cache.rows[(SUB_ID, proxy.FEED_CACHE_LATEST_KEY)]["payload"])
    assert {r["vin"] for r in stored["records"]} == {VIN_A, VIN_B}
    assert stored["count"] == 2
    assert stored["vins_in_scope"] == [VIN_A, VIN_B]


def test_operator_a_and_operator_b_both_see_the_unfiltered_cache():
    """The leak scenario, end to end at this layer.

    Two callers, one cache. Both must receive the SAME unfiltered payload from
    `get_subscription_feed`, because scoping is `index.py`'s job and happens
    after. If this ever returns a narrower payload for the second caller, the
    filter has moved to the write side.
    """
    http = CountingHttpClient(_feed(VIN_A, VIN_B))
    cache = InMemoryCache()
    config = _config()

    a = _get(config, http, cache)
    b = _get(config, http, cache)

    assert a.body["records"] == b.body["records"]
    assert len(b.body["records"]) == 2


# ── Not-cacheable responses ────────────────────────────────────────────────

@pytest.mark.parametrize("status", [500, 503])
def test_producer_failures_are_not_cached(status):
    """A cached 502 would pin a transient outage for the whole TTL window.

    Worse: a `producer_shape_drift` body has no `records` key, so a cached one
    would later be filtered into something that reads as "this vehicle has no
    telemetry" — the substitution every layer of this spec refuses.
    """
    http = CountingHttpClient({"error": "boom"}, status=status)
    cache = InMemoryCache()

    result = _get(_config(), http, cache)

    assert result.status_code != 200
    assert cache.puts == 0, "an error response must never be cached"


def test_shape_drift_is_not_cached():
    http = CountingHttpClient({"records": "not-a-list", "count": 0})
    cache = InMemoryCache()

    result = _get(_config(), http, cache)

    assert result.status_code == 502
    assert result.body["error"] == proxy.ERROR_PRODUCER_SHAPE_DRIFT
    assert cache.puts == 0


# ── Disabled / absent cache behaves exactly as T2.3 shipped ────────────────

def test_no_cache_argument_means_live_every_call():
    http = CountingHttpClient(_feed(VIN_A))
    config = _config()

    proxy.get_subscription_feed(
        config=config, token_provider=StaticTokens(), http_client=http, cache=None
    )
    proxy.get_subscription_feed(
        config=config, token_provider=StaticTokens(), http_client=http, cache=None
    )

    assert http.calls == 2


def test_empty_table_name_disables_the_cache_even_if_one_is_passed():
    """The env-unset case: consumer stack not deployed on this stage."""
    http = CountingHttpClient(_feed(VIN_A))
    cache = InMemoryCache()
    config = _config(feed_cache_table="")

    assert config.cache_enabled is False
    _get(config, http, cache)
    _get(config, http, cache)

    assert http.calls == 2
    assert cache.gets == 0 and cache.puts == 0


# ── Config parsing ─────────────────────────────────────────────────────────

def test_from_env_treats_the_cache_vars_as_optional():
    """The two producer vars still raise; the two cache vars must not.

    Making them required would break a capability that currently WORKS, on every
    stage where the consumer stack is absent, in order to add an optimisation.
    """
    config = proxy.ProxyConfig.from_env(
        {
            proxy.ENV_PRODUCER_ENDPOINT: "https://producer.example.test/prod",
            proxy.ENV_CMS_SUBSCRIPTION_ID: SUB_ID,
        }
    )
    assert config.feed_cache_table == ""
    assert config.cache_enabled is False
    assert config.feed_cache_ttl_seconds == proxy.DEFAULT_FEED_CACHE_TTL_SECONDS


def test_from_env_reads_the_cache_vars_when_present():
    config = proxy.ProxyConfig.from_env(
        {
            proxy.ENV_PRODUCER_ENDPOINT: "https://producer.example.test/prod",
            proxy.ENV_CMS_SUBSCRIPTION_ID: SUB_ID,
            proxy.ENV_FEED_CACHE_TABLE: "cms-test-storage-cs-feed-cache-r-0",
            proxy.ENV_FEED_CACHE_TTL_SECONDS: "30",
        }
    )
    assert config.feed_cache_table == "cms-test-storage-cs-feed-cache-r-0"
    assert config.feed_cache_ttl_seconds == 30
    assert config.cache_enabled is True


@pytest.mark.parametrize("raw", ["60s", "sixty", "", "   ", None])
def test_malformed_ttl_falls_back_to_the_default_not_to_disabled(raw):
    """A typo must not silently switch the cache off.

    0 disables the cache, so returning 0 for `"60s"` would turn a feature off
    while every response stayed correct — the silent-degradation shape this spec
    keeps finding. The default plus a WARNING keeps the feed fast and the mistake
    visible.
    """
    assert proxy._parse_ttl_seconds(raw) == proxy.DEFAULT_FEED_CACHE_TTL_SECONDS


def test_explicit_zero_and_negative_are_honoured_as_disabled():
    """Unambiguous intent, unlike a typo."""
    assert proxy._parse_ttl_seconds("0") == 0
    assert proxy._parse_ttl_seconds("-1") == -1
    assert _config(feed_cache_ttl_seconds=0).cache_enabled is False


# ── DynamoFeedCache: the real implementation, against a stub client ────────

class StubDynamo:
    def __init__(self, item=None, raise_on=None):
        self.item = item
        self.raise_on = raise_on or set()
        self.put_items = []
        self.get_calls = 0

    def get_item(self, **kw):
        self.get_calls += 1
        if "get" in self.raise_on:
            raise RuntimeError("dynamo down")
        return {"Item": self.item} if self.item else {}

    def put_item(self, **kw):
        if "put" in self.raise_on:
            raise RuntimeError("dynamo down")
        self.put_items.append(kw)


def _row(payload: dict, ttl_offset: int = 60, cached_at: str = "2026-09-13T10:00:00Z"):
    return {
        "subscription_id": {"S": SUB_ID},
        "record_key": {"S": proxy.FEED_CACHE_LATEST_KEY},
        "payload": {"S": json.dumps(payload)},
        "cached_at": {"S": cached_at},
        "ttl": {"N": str(int(time.time()) + ttl_offset)},
    }


def test_dynamo_cache_writes_one_putitem_with_a_ttl():
    """One PutItem, not a batch — which is why no BatchWriteItem grant is needed."""
    stub = StubDynamo()
    proxy.DynamoFeedCache("tbl", client=stub).put_latest(SUB_ID, _feed(VIN_A), 60)

    assert len(stub.put_items) == 1
    item = stub.put_items[0]["Item"]
    assert item["subscription_id"]["S"] == SUB_ID
    assert item["record_key"]["S"] == proxy.FEED_CACHE_LATEST_KEY
    assert int(item["ttl"]["N"]) > int(time.time())
    assert json.loads(item["payload"]["S"])["count"] == 1
    assert item["cached_at"]["S"].endswith("Z")


def test_dynamo_cache_surfaces_cached_at_so_staleness_is_visible():
    stub = StubDynamo(item=_row(_feed(VIN_A), cached_at="2026-09-13T10:00:00Z"))
    got = proxy.DynamoFeedCache("tbl", client=stub).get_latest(SUB_ID)

    assert got is not None
    assert got["cached_at"] == "2026-09-13T10:00:00Z"


def test_dynamo_cache_treats_an_expired_row_as_a_miss():
    stub = StubDynamo(item=_row(_feed(VIN_A), ttl_offset=-5))
    assert proxy.DynamoFeedCache("tbl", client=stub).get_latest(SUB_ID) is None


@pytest.mark.parametrize(
    "broken",
    [
        {"no_ttl": True},
        {"bad_ttl": True},
        {"bad_payload": True},
        {"payload_not_object": True},
    ],
)
def test_dynamo_cache_treats_an_unusable_row_as_a_miss(broken):
    row = _row(_feed(VIN_A))
    if broken.get("no_ttl"):
        del row["ttl"]
    if broken.get("bad_ttl"):
        row["ttl"] = {"N": "not-a-number"}
    if broken.get("bad_payload"):
        row["payload"] = {"S": "{not json"}
    if broken.get("payload_not_object"):
        row["payload"] = {"S": "[1, 2, 3]"}

    stub = StubDynamo(item=row)
    assert proxy.DynamoFeedCache("tbl", client=stub).get_latest(SUB_ID) is None


@pytest.mark.parametrize("op", ["get", "put"])
def test_dynamo_cache_never_raises(op):
    """A cache outage must not turn a working feed into a 502.

    This is the one place in the module where swallowing is right, and it is
    bounded to the cache — producer failures still surface.
    """
    cache = proxy.DynamoFeedCache("tbl", client=StubDynamo(raise_on={op}))
    if op == "get":
        assert cache.get_latest(SUB_ID) is None
    else:
        cache.put_latest(SUB_ID, _feed(VIN_A), 60)  # must not raise


def test_a_broken_cache_still_serves_the_feed_live():
    """End to end: cache raising on both operations degrades to T2.3 behaviour."""
    http = CountingHttpClient(_feed(VIN_A))
    broken = proxy.DynamoFeedCache("tbl", client=StubDynamo(raise_on={"get", "put"}))

    result = _get(_config(), http, broken)

    assert result.status_code == 200
    assert result.body["count"] == 1
    assert http.calls == 1


def test_dynamo_cache_requests_no_consistent_read():
    """Eventually-consistent is correct AND half the cost for a TTL'd cache."""
    stub = StubDynamo(item=_row(_feed(VIN_A)))
    proxy.DynamoFeedCache("tbl", client=stub).get_latest(SUB_ID)
    assert stub.get_calls == 1



# ── The ordering invariant, guarded structurally ───────────────────────────
#
# Everything above tests the proxy. The cross-fleet-leak invariant, though, is a
# property of the ORDER of two calls in `index.py`: the cache is passed to
# `get_subscription_feed` (which writes the producer-shaped payload), and
# `_cs_filter_feed` runs on the RESULT. Swap those and one operator's filtered
# view is cached under a subscription-scoped key with no caller component, and
# served to the next operator.
#
# No unit test of the proxy can see that, because the filter is not in the proxy.
# So it is asserted against `index.py`'s source, the same technique T2.3 used for
# `test_every_cs_response_goes_through_proxyresult` after three separate response
# paths bypassed the SG5 headers.

def _index_source() -> str:
    path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "index.py"
    )
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def _cs_route_block(src: str) -> str:
    """The Connected Services route block, bounded so unrelated code cannot
    accidentally satisfy an assertion about it."""
    start = src.index("if path == _CS_FEED_ROUTE")
    end = src.index("'error': 'Endpoint not found'", start)
    block = src[start:end]
    assert len(block) > 500, "route-block extraction looks wrong"
    return block


def test_premise_route_block_contains_both_calls():
    """Anti-vacuity: an extraction that found neither call would pass below."""
    block = _cs_route_block(_index_source())
    assert "get_subscription_feed(" in block
    assert "_cs_filter_feed(" in block


def test_filter_runs_after_the_cached_call_not_before():
    """THE cross-fleet invariant: cache the producer view, filter per caller.

    If `_cs_filter_feed` ever appears BEFORE `get_subscription_feed` in this
    block, the payload handed to the cache has been narrowed to one operator's
    fleet — and the cache key `(subscription_id, 'latest')` has no caller
    component, so the next operator gets those rows. That is a cross-fleet leak
    with a cache-hit-rate justification, and it would look like a performance win
    in review.
    """
    block = _cs_route_block(_index_source())
    assert block.index("get_subscription_feed(") < block.index("_cs_filter_feed("), (
        "_cs_filter_feed runs BEFORE get_subscription_feed — the cache would "
        "store one operator's filtered view under a subscription-scoped key and "
        "serve it to every other operator"
    )


def test_the_cache_is_handed_to_the_proxy_call_itself():
    """`cache=` must be an argument to `get_subscription_feed`, not applied later.

    Passing it anywhere downstream of the filter would put the filtered payload
    on the write path, which is the same defect as the ordering above arrived at
    by a different route.
    """
    block = _cs_route_block(_index_source())
    call_start = block.index("get_subscription_feed(")
    call = block[call_start: block.index(")", block.index("cache=", call_start))]
    assert "cache=" in call, (
        "get_subscription_feed is called without a cache= argument; the feed "
        "cache is deployed but nothing would read or write it — the exact state "
        "T2.6 exists to end"
    )
    assert "_cs_filter_feed" not in call, (
        "the cache= argument's expression mentions the fleet filter"
    )



def test_cached_at_survives_the_fleet_scope_filter():
    """Review Cycle 1 Suggestion: `cached_at` reaches the UI only if the filter
    preserves it, and today that holds by construction rather than by contract.

    `_cs_filter_feed` starts from `dict(payload)`, so every key it does not
    rewrite passes through. A refactor to an explicit allowlist — a plausible
    hardening, since the function's job is to withhold things — would silently
    drop `cached_at`, and the symptom would be a cached feed rendering as live.
    That is a correctness regression with no error and no failing test, so it gets
    a test.

    Imported here rather than at module scope: `index.py` is a ~9700-line handler
    whose import cost is real, and only this one test needs it.
    """
    sys.path.insert(
        0, os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    )
    import index  # noqa: PLC0415

    payload = _feed(VIN_A, VIN_B)
    payload["cached_at"] = "2026-09-13T10:00:00Z"

    # Scoped caller: only VIN_A's vehicle is in fleet scope.
    filtered = index._cs_filter_feed(payload, {f"VEH-{VIN_A[-4:]}"})

    assert filtered["cached_at"] == "2026-09-13T10:00:00Z", (
        "the fleet-scope filter dropped cached_at — a cached feed would render "
        "as live"
    )
    # And the filter still did its actual job, so this is not passing because the
    # filter became a no-op.
    assert [r["vin"] for r in filtered["records"]] == [VIN_A]
    assert filtered["count"] == 1

    # Unscoped caller: payload returned untouched, cached_at included.
    assert index._cs_filter_feed(payload, None)["cached_at"] == "2026-09-13T10:00:00Z"
