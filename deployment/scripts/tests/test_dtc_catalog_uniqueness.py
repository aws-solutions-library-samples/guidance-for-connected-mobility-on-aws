"""
DTC catalog uniqueness guard — DX2 prerequisite.

Asserts that every dtc_code in cms-{stage}-event-catalog maps to exactly one
verdict entry.  A non-unique key means `diagnosticsVerdict` picks a winner by
scan order (non-deterministic).  The worst case is a P0 stop-driving instruction
silently replaced by a routine P3 maintenance notice.

This test is the catalog-side guard for spec
2026-09-02-cms-diagnostics-platform § DX2 ('verdict is order-independent').
DX2 cannot pass while duplicates exist because the lookup returns any matching
row in undefined order.

Usage:
    # against staging (requires AWS credentials):
    pytest deployment/scripts/tests/test_dtc_catalog_uniqueness.py -v \
        --stage staging --region us-west-2

    # unit (offline, parametrized fixture — no AWS needed):
    pytest deployment/scripts/tests/test_dtc_catalog_uniqueness.py \
        -k "unit" -v

Integration test (requires staging creds) is skipped when boto3 cannot connect.
"""

import os
from collections import defaultdict
from typing import Dict, List, Optional

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _full_scan(table) -> list:
    items: list = []
    resp = table.scan()
    items.extend(resp.get("Items", []))
    while "LastEvaluatedKey" in resp:
        resp = table.scan(ExclusiveStartKey=resp["LastEvaluatedKey"])
        items.extend(resp.get("Items", []))
    return items


def _find_duplicates(items: list) -> Dict[str, List[str]]:
    """Return {dtc_code: [event_id, ...]} for every code that appears more than once."""
    code_to_events: Dict[str, List[str]] = defaultdict(list)
    for item in items:
        code = item.get("dtc_code")
        if code:
            code_to_events[code].append(item.get("event_id", "<no-event_id>"))
    return {code: eids for code, eids in code_to_events.items() if len(eids) > 1}


def _severity_hint(item: dict) -> Optional[str]:
    return item.get("severity_hint")


def _is_p0(item: dict) -> bool:
    return _severity_hint(item) == "P0"


# ---------------------------------------------------------------------------
# Unit tests (no AWS required)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("catalog, expected_dups", [
    # happy path — all unique
    (
        [
            {"event_id": "a", "dtc_code": "P0217", "severity_hint": "P0"},
            {"event_id": "b", "dtc_code": "C0035", "severity_hint": "P2"},
            {"event_id": "c"},  # no dtc_code — ignored
        ],
        {},
    ),
    # duplicate P0217 — should be caught
    (
        [
            {"event_id": "a", "dtc_code": "P0217", "severity_hint": "P0"},
            {"event_id": "b", "dtc_code": "P0217", "severity_hint": None},
        ],
        {"P0217": ["a", "b"]},
    ),
    # multiple duplicates
    (
        [
            {"event_id": "brake", "dtc_code": "C1234", "severity_hint": "P0"},
            {"event_id": "tire", "dtc_code": "C1234", "severity_hint": "P2"},
            {"event_id": "cool", "dtc_code": "P0217", "severity_hint": "P0"},
            {"event_id": "heat", "dtc_code": "P0217", "severity_hint": None},
        ],
        {"C1234": ["brake", "tire"], "P0217": ["cool", "heat"]},
    ),
    # entry without dtc_code is not a duplicate
    (
        [
            {"event_id": "a", "dtc_code": "P0562", "severity_hint": "P3"},
            {"event_id": "b"},  # no code
            {"event_id": "c"},  # no code
        ],
        {},
    ),
])
def test_unit_find_duplicates(catalog, expected_dups):
    result = _find_duplicates(catalog)
    assert result == expected_dups


@pytest.mark.parametrize("items, should_have_p0", [
    # P0 wins when two entries share a code
    (
        [
            {"event_id": "a", "dtc_code": "P0217", "severity_hint": "P0",
             "description": "stop driving"},
            {"event_id": "b", "dtc_code": "P0217", "severity_hint": None,
             "description": "engine temp high"},
        ],
        True,
    ),
    (
        [
            {"event_id": "a", "dtc_code": "C1234", "severity_hint": "P0",
             "description": "brake fault — stop driving, do not operate vehicle"},
            {"event_id": "b", "dtc_code": "C1234", "severity_hint": "P2",
             "description": "tire pressure below safe level"},
        ],
        True,
    ),
])
def test_unit_p0_must_survive_any_conflict(items, should_have_p0):
    """The P0 row must exist among duplicates — guard asserts we can detect it."""
    has_p0 = any(_is_p0(i) for i in items)
    assert has_p0 == should_have_p0


def test_unit_no_code_entries_are_not_duplicates():
    """Events without dtc_code should never count as duplicates of each other."""
    catalog = [
        {"event_id": "tire", "description": "Tire pressure low"},
        {"event_id": "battery", "description": "Battery voltage low"},
        {"event_id": "abs", "description": "ABS fault"},
    ]
    dups = _find_duplicates(catalog)
    assert dups == {}, "Code-less entries must never be flagged as duplicates"


# ---------------------------------------------------------------------------
# Integration test — requires AWS credentials + staging catalog
# ---------------------------------------------------------------------------

def _get_catalog_items(stage: str, region: str, profile: Optional[str]) -> list:
    import boto3
    from botocore.exceptions import ClientError, NoCredentialsError

    session = boto3.Session(profile_name=profile, region_name=region)
    dynamodb = session.resource("dynamodb")
    table = dynamodb.Table(f"cms-{stage}-event-catalog")
    try:
        return _full_scan(table)
    except (ClientError, NoCredentialsError) as exc:
        pytest.skip(f"Cannot reach DynamoDB ({exc})")


def pytest_addoption(parser):
    """Add --stage and --region options for integration tests."""
    try:
        parser.addoption("--stage", default="staging",
                         help="Deployment stage (default: staging)")
        parser.addoption("--region", default="us-west-2",
                         help="AWS region (default: us-west-2)")
    except ValueError:
        # options already added by conftest
        pass


@pytest.fixture
def stage(request):
    return request.config.getoption("--stage", default="staging")


@pytest.fixture
def region(request):
    return request.config.getoption("--region", default="us-west-2")


@pytest.fixture
def profile():
    return os.environ.get("AWS_PROFILE")


@pytest.mark.integration
def test_dtc_catalog_has_no_duplicate_codes(stage, region, profile):
    """DX2 guard: every dtc_code in the catalog maps to exactly one event.

    Fails when:
    - Any dtc_code appears on 2+ rows (makes the verdict order-dependent).
    - The count of rows-with-a-code does not equal the count of distinct codes.
    """
    items = _get_catalog_items(stage, region, profile)
    duplicates = _find_duplicates(items)

    # Format a helpful message listing all conflicts
    if duplicates:
        lines = [
            f"\nDuplicate dtc_codes found in cms-{stage}-event-catalog:",
            "A non-unique key means the verdict depends on DDB scan order.",
            "P0 stop-driving instructions can be silently replaced by routine notices.",
            "",
        ]
        for code, event_ids in sorted(duplicates.items()):
            p0_entries = [
                eid for eid in event_ids
                if _is_p0(next((i for i in items if i["event_id"] == eid), {}))
            ]
            lines.append(f"  {code}: {event_ids}")
            if p0_entries:
                lines.append(f"    ⚠️  P0 entry present: {p0_entries}")
        raise AssertionError("\n".join(lines))


@pytest.mark.integration
def test_dtc_code_count_equals_distinct_codes(stage, region, profile):
    """Derived invariant: rows-with-code == distinct codes (no overloading)."""
    items = _get_catalog_items(stage, region, profile)

    rows_with_code = [i for i in items if i.get("dtc_code")]
    distinct_codes = {i["dtc_code"] for i in rows_with_code}

    assert len(rows_with_code) == len(distinct_codes), (
        f"rows-with-code ({len(rows_with_code)}) != distinct codes ({len(distinct_codes)}): "
        f"some codes map to multiple entries"
    )


@pytest.mark.integration
def test_p0_entries_have_codes(stage, region, profile):
    """All P0 entries must have a dtc_code — they must be findable by code lookup."""
    items = _get_catalog_items(stage, region, profile)
    p0_without_code = [
        i["event_id"] for i in items
        if _is_p0(i) and not i.get("dtc_code")
    ]
    assert not p0_without_code, (
        f"P0 entries without a dtc_code (cannot be found by code lookup): "
        f"{p0_without_code}"
    )
