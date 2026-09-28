"""Wire-type contract tests for the command catalog's ``responseTimeout``.

Defect these pin — issue
2026-08-19-cms-command-catalog-response-timeout-contract:

`_get_catalog` emitted ``act.get('responseTimeout', 5000)``. Catalog items store
the value as a DynamoDB **string** (`"3000"`), so an item that HAS the attribute
serialised to a JSON string while an item MISSING it fell back to a raw **int**.

The iOS client decodes `responseTimeout` as `String?`, and Swift's
`decodeIfPresent(String.self)` raises `DecodingError.typeMismatch` on a
present-but-numeric value rather than yielding nil. Because the entries are
decoded as an array inside `CommandCatalogResponse`, one numeric value fails the
WHOLE response — a single catalog item missing one attribute emptied the entire
command sheet. Verified against a real Swift decoder before the fix:

    string "3000": DECODED ok
    number 5000  : THREW DecodingError.typeMismatch
    mixed batch  : THREW  (one bad entry kills the good one)
    absent       : DECODED ok   <- absent is harmless; the default caused the bug

So the invariant is not "responseTimeout is present" — absence is safe. It is
**"if present, it is always the same JSON type."**
"""
import importlib
import sys

import boto3
import pytest
from moto import mock_aws

_REGION = "us-west-2"
_CATALOG_TABLE = "cms-test-signal-catalog"


@pytest.fixture
def catalog_lambda(monkeypatch):
    monkeypatch.setenv("SIGNAL_CATALOG_TABLE", _CATALOG_TABLE)
    monkeypatch.setenv("AWS_DEFAULT_REGION", _REGION)
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=_REGION)
        ddb.create_table(
            TableName=_CATALOG_TABLE,
            KeySchema=[{"AttributeName": "json_field", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "json_field", "AttributeType": "S"}
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        sys.modules.pop("commands_lambda", None)
        import commands_lambda as mod  # noqa: E402

        importlib.reload(mod)
        yield mod


def _put(table_name, json_field, actuator):
    boto3.resource("dynamodb", region_name=_REGION).Table(table_name).put_item(
        Item={
            "json_field": json_field,
            "signal_name": json_field,
            "vss_path": f"Vehicle.{json_field}",
            "signal_group": "body",
            "actuator": actuator,
        }
    )


def _entries(mod):
    resp = mod._get_catalog()
    import json as _json

    body = _json.loads(resp["body"])
    return [e for group in body["actuators"].values() for e in group]


def test_response_timeout_is_string_when_catalog_supplies_it(catalog_lambda):
    _put(_CATALOG_TABLE, "all_doors_locked", {
        "commandName": "lock_all_doors",
        "label": "Lock all doors",
        "category": "doors",
        "responseTimeout": "3000",
    })
    entries = _entries(catalog_lambda)
    assert len(entries) == 1
    assert isinstance(entries[0]["responseTimeout"], str)
    assert entries[0]["responseTimeout"] == "3000"


def test_response_timeout_is_string_when_defaulted(catalog_lambda):
    """The regression. Attribute absent -> default must not be a raw int."""
    _put(_CATALOG_TABLE, "charge_port_open", {
        "commandName": "open_charge_port",
        "label": "Open charge port",
        "category": "charging",
        # responseTimeout deliberately absent
    })
    entries = _entries(catalog_lambda)
    assert len(entries) == 1
    value = entries[0]["responseTimeout"]
    assert isinstance(value, str), (
        f"responseTimeout defaulted to {type(value).__name__} {value!r}; a numeric "
        "value fails the entire iOS catalog decode, not just this entry"
    )
    assert value == "5000"


def test_response_timeout_type_is_uniform_across_mixed_entries(catalog_lambda):
    """The shape that actually broke: one entry with, one without.

    A mixed batch is what a real catalog looks like after someone adds an
    actuator and forgets the attribute. Every entry must serialise identically.
    """
    _put(_CATALOG_TABLE, "all_doors_locked", {
        "commandName": "lock_all_doors",
        "label": "Lock all doors",
        "category": "doors",
        "responseTimeout": "3000",
    })
    _put(_CATALOG_TABLE, "charge_port_open", {
        "commandName": "open_charge_port",
        "label": "Open charge port",
        "category": "charging",
    })
    entries = _entries(catalog_lambda)
    assert len(entries) == 2
    types = {type(e["responseTimeout"]).__name__ for e in entries}
    assert types == {"str"}, f"non-uniform responseTimeout types: {types}"
