#!/usr/bin/env python3
"""Tests for the IoT lifecycle event handler.

Issue: ``cms/issues/2026-09-23-iot-lifecycle-processor-dead-58m-sqs-backlog/report.md``

The handler had never successfully run. Four stacked defects, only two of which the
issue report identified:

  D1 (report)  ``Code.from_asset`` shipped the source dir with no ``pip install``, so
               every invocation died on ``Runtime.ImportModuleError:
               No module named 'aws_lambda_powertools'``.
               Fixed in ``deployment/stacks/iot_stack.py``; guarded by
               ``deployment/stacks/tests/test_iot_lifecycle_bundling.py``, not here.

  D2 (report)  ``handle_subscribed_event`` read ``event_data['topicName']``. No such
               field exists in the AWS subscribe/unsubscribe lifecycle payload — it
               carries ``topics`` as an **array**.

  D3 (missed)  ``handle_unsubscribed_event`` had the identical ``topicName`` defect.
               The report flagged only the subscribe path.

  D4/D5 (missed)  The handler wrote the attribute names of the SQLAlchemy models in
               ``iot_api/utils/models/`` rather than the DynamoDB key schemas it
               actually writes to:
                 * topics table PK is ``topic_name``      — handler wrote ``name``
                 * subscriptions SK is ``topic_filter``   — handler wrote ``topic_name``
               Both would raise ``ValidationException`` on every subscribe, so fixing
               D1+D2 alone would have swapped one retry loop for another.

These tests run against **moto-backed DynamoDB tables carrying the real deployed key
schemas**, so D4/D5 are caught by DynamoDB's own key validation rather than by an
assertion that could be written to agree with the bug.

Key schemas below are mirrored from ``deployment/stacks/iot_stack.py`` (the source of
truth). ``deployment/stacks/tests/test_iot_lifecycle_bundling.py`` asserts the
synthesized template still matches them, so stack-side drift fails there.

Run from this directory::

    python3 -m pytest test_lifecycle_handler.py -v
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

_HANDLER_PATH = Path(__file__).with_name("lambda_function.py")

CONNECTIONS_TABLE = "cms-test-iot-connections"
SUBSCRIPTIONS_TABLE = "cms-test-iot-subscriptions"
TOPICS_TABLE = "cms-test-iot-topics"
REGION = "us-west-2"


def _create_tables(ddb):
    """Create the three tables with the schemas iot_stack.py deploys."""
    ddb.create_table(
        TableName=CONNECTIONS_TABLE,
        KeySchema=[{"AttributeName": "client_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "client_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName=SUBSCRIPTIONS_TABLE,
        KeySchema=[
            {"AttributeName": "client_id", "KeyType": "HASH"},
            # NOT topic_name — this is the D5 defect
            {"AttributeName": "topic_filter", "KeyType": "RANGE"},
        ],
        AttributeDefinitions=[
            {"AttributeName": "client_id", "AttributeType": "S"},
            {"AttributeName": "topic_filter", "AttributeType": "S"},
        ],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName=TOPICS_TABLE,
        # NOT name — this is the D4 defect
        KeySchema=[{"AttributeName": "topic_name", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "topic_name", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )


@pytest.fixture
def handler(monkeypatch):
    """Load the handler under moto with the real table schemas in place.

    The handler binds ``dynamodb.Table(...)`` at import time, so the mock and the env
    vars must both be live before the module is executed. Loaded by explicit path
    under a unique module name because three different handlers in this repo each
    define ``lambda_function.py`` — a bare ``import lambda_function`` binds to
    whichever was imported first.
    """
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("CONNECTIONS_TABLE", CONNECTIONS_TABLE)
    monkeypatch.setenv("SUBSCRIPTIONS_TABLE", SUBSCRIPTIONS_TABLE)
    monkeypatch.setenv("TOPICS_TABLE", TOPICS_TABLE)
    monkeypatch.setenv("POWERTOOLS_TRACE_DISABLED", "true")

    with mock_aws():
        ddb = boto3.client("dynamodb", region_name=REGION)
        _create_tables(ddb)

        name = "iot_lifecycle_events_handler_under_test"
        spec = importlib.util.spec_from_file_location(name, _HANDLER_PATH)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[name] = mod
        try:
            spec.loader.exec_module(mod)
            yield mod
        finally:
            sys.modules.pop(name, None)


def _resource():
    return boto3.resource("dynamodb", region_name=REGION)


def _subscribe_event(client_id="186b5", topics=None, timestamp=1460065214626):
    """A subscribe payload shaped per the AWS lifecycle-event documentation.

    https://docs.aws.amazon.com/iot/latest/developerguide/life-cycle-events.html
    """
    return {
        "clientId": client_id,
        "thingName": "exampleThing",
        "timestamp": timestamp,
        "eventType": "subscribed",
        "sessionIdentifier": "00000000-0000-0000-0000-000000000000",
        "principalIdentifier": "12345678901234567890123456789012",
        "topics": ["foo/bar", "device/data", "dog/cat"] if topics is None else topics,
    }


# ── D2: subscribe writes one row per topic in the array ──────────────────────

def test_subscribe_writes_one_row_per_topic(handler):
    """The array must be iterated — three topics means three subscription rows."""
    handler.handle_subscribed_event(_subscribe_event())

    rows = _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Items"]
    assert len(rows) == 3, f"expected 3 subscription rows, got {len(rows)}"
    assert {r["topic_filter"] for r in rows} == {"foo/bar", "device/data", "dog/cat"}
    assert all(r["client_id"] == "186b5" for r in rows)
    assert all(r["status"] == "SUBSCRIBED" for r in rows)


def test_subscribe_registers_each_topic(handler):
    """Each filter is registered in the topics table under the topic_name key."""
    handler.handle_subscribed_event(_subscribe_event())

    rows = _resource().Table(TOPICS_TABLE).scan()["Items"]
    assert {r["topic_name"] for r in rows} == {"foo/bar", "device/data", "dog/cat"}


def test_subscribe_single_topic(handler):
    handler.handle_subscribed_event(_subscribe_event(topics=["fleet/telemetry"]))

    rows = _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Items"]
    assert len(rows) == 1
    assert rows[0]["topic_filter"] == "fleet/telemetry"


def test_subscribe_carries_session_and_timestamp(handler):
    handler.handle_subscribed_event(_subscribe_event(topics=["a/b"], timestamp=1700000000000))

    row = _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Items"][0]
    assert row["subscribe_timestamp"] == 1700000000000
    assert row["session_identifier"] == "00000000-0000-0000-0000-000000000000"


def test_duplicate_subscribe_does_not_duplicate_topic(handler):
    """ConditionalCheckFailed on an already-registered topic is swallowed, not raised."""
    handler.handle_subscribed_event(_subscribe_event(topics=["foo/bar"]))
    handler.handle_subscribed_event(_subscribe_event(client_id="other", topics=["foo/bar"]))

    topics = _resource().Table(TOPICS_TABLE).scan()["Items"]
    assert len(topics) == 1, "topic should be registered exactly once"
    subs = _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Items"]
    assert len(subs) == 2, "both clients keep their own subscription row"


def test_duplicate_subscribe_preserves_first_seen_created_at(handler):
    """The ConditionExpression must actually guard — row count alone cannot prove it.

    An unconditional ``put_item`` also leaves exactly one row (same partition key
    overwrites), so counting rows passes whether or not the condition is present.
    What the condition protects is the *first-seen* timestamp. A sentinel value is
    written between the two subscribes so the check is deterministic rather than
    relying on clock resolution.
    """
    handler.handle_subscribed_event(_subscribe_event(topics=["foo/bar"]))

    table = _resource().Table(TOPICS_TABLE)
    table.update_item(
        Key={"topic_name": "foo/bar"},
        UpdateExpression="SET created_at = :s",
        ExpressionAttributeValues={":s": "SENTINEL-FIRST-SEEN"},
    )

    handler.handle_subscribed_event(_subscribe_event(client_id="other", topics=["foo/bar"]))

    row = table.get_item(Key={"topic_name": "foo/bar"})["Item"]
    assert row["created_at"] == "SENTINEL-FIRST-SEEN", (
        "re-subscribe overwrote the topic's first-seen timestamp — "
        "the attribute_not_exists condition is missing or ineffective"
    )


# ── D3: unsubscribe has the same array contract ──────────────────────────────

def test_unsubscribe_marks_each_topic(handler):
    """The unsubscribe path must iterate topics too — the report missed this site."""
    handler.handle_subscribed_event(_subscribe_event(topics=["foo/bar", "dog/cat"]))
    handler.handle_unsubscribed_event({
        "clientId": "186b5",
        "timestamp": 1460065300000,
        "eventType": "unsubscribed",
        "sessionIdentifier": "00000000-0000-0000-0000-000000000000",
        "topics": ["foo/bar", "dog/cat"],
    })

    rows = _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Items"]
    assert len(rows) == 2, "unsubscribe must update in place, not create new rows"
    assert all(r["status"] == "UNSUBSCRIBED" for r in rows)
    assert all(r["unsubscribe_timestamp"] == 1460065300000 for r in rows)


def test_unsubscribe_only_targets_named_topics(handler):
    handler.handle_subscribed_event(_subscribe_event(topics=["keep/me", "drop/me"]))
    handler.handle_unsubscribed_event({
        "clientId": "186b5",
        "timestamp": 1460065300000,
        "topics": ["drop/me"],
    })

    by_filter = {r["topic_filter"]: r for r in _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Items"]}
    assert by_filter["drop/me"]["status"] == "UNSUBSCRIBED"
    assert by_filter["keep/me"]["status"] == "SUBSCRIBED"


# ── Schema surprises must fail loudly, not silently record nothing ───────────

def test_legacy_topicname_payload_fails_loudly(handler):
    """A payload shaped like the old (wrong) assumption must raise, naming the field.

    This is the shape the handler was written against. If it ever arrives, recording
    nothing silently would be worse than failing — the message should go back to the
    queue and the error should say which field was missing.

    The assertion checks the *explanatory* message, not merely that some KeyError
    mentioning 'topics' was raised: a bare ``event_data['topics']`` also produces
    ``KeyError: 'topics'``, so a substring check on the field name alone cannot tell
    the guarded path from the unguarded one.
    """
    with pytest.raises(KeyError) as exc:
        handler.handle_subscribed_event({
            "clientId": "186b5",
            "timestamp": 1460065214626,
            "sessionIdentifier": "s",
            "topicName": "foo/bar",  # the field that does not exist
        })
    msg = str(exc.value)
    assert "missing required field" in msg, f"not the explanatory error: {msg}"
    assert "'topics'" in msg
    # The message must enumerate what DID arrive, so the real shape is diagnosable.
    assert "topicName" in msg, f"error does not report the observed fields: {msg}"
    assert _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Count"] == 0


def test_missing_required_field_reports_observed_fields(handler):
    """_require must name the missing field AND list what was present."""
    event = _subscribe_event()
    del event["sessionIdentifier"]
    with pytest.raises(KeyError) as exc:
        handler.handle_subscribed_event(event)
    msg = str(exc.value)
    assert "missing required field" in msg
    assert "'sessionIdentifier'" in msg
    assert "clientId" in msg and "topics" in msg, (
        f"error should enumerate the fields that did arrive: {msg}"
    )


def test_topics_not_a_list_fails_loudly(handler):
    """A bare string would otherwise iterate character by character."""
    with pytest.raises(TypeError) as exc:
        handler.handle_subscribed_event(_subscribe_event(topics="foo/bar"))
    assert "list" in str(exc.value)
    assert _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Count"] == 0


def test_missing_client_id_names_the_field(handler):
    event = _subscribe_event()
    del event["clientId"]
    with pytest.raises(KeyError) as exc:
        handler.handle_subscribed_event(event)
    msg = str(exc.value)
    assert "missing required field" in msg
    assert "'clientId'" in msg


def test_empty_topics_writes_nothing_and_does_not_raise(handler):
    """An empty array is well-formed; it simply has no rows to write."""
    handler.handle_subscribed_event(_subscribe_event(topics=[]))
    assert _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Count"] == 0


# ── Presence paths: regression guards (these were always sound) ──────────────

def test_connected_event_records_connection(handler):
    handler.handle_connected_event({
        "clientId": "186b5",
        "timestamp": 1460065214626,
        "eventType": "connected",
        "sessionIdentifier": "s-1",
        "principalIdentifier": "p-1",
        "ipAddress": "192.168.1.1",
        "versionNumber": 0,
    })

    rows = _resource().Table(CONNECTIONS_TABLE).scan()["Items"]
    assert len(rows) == 1
    assert rows[0]["client_id"] == "186b5"
    assert rows[0]["status"] == "CONNECTED"


def test_disconnected_event_updates_status(handler):
    handler.handle_connected_event({
        "clientId": "186b5",
        "timestamp": 1460065214626,
        "sessionIdentifier": "s-1",
        "principalIdentifier": "p-1",
    })
    handler.handle_disconnected_event({
        "clientId": "186b5",
        "timestamp": 1460065300000,
        "disconnectReason": "CLIENT_INITIATED_DISCONNECT",
        "clientInitiatedDisconnect": True,
    })

    row = _resource().Table(CONNECTIONS_TABLE).scan()["Items"][0]
    assert row["status"] == "DISCONNECTED"
    assert row["disconnect_reason"] == "CLIENT_INITIATED_DISCONNECT"


# ── Dispatch ────────────────────────────────────────────────────────────────

def test_record_handler_dispatches_subscribed(handler):
    class _Rec:
        body = json.dumps(_subscribe_event(topics=["foo/bar"]))

    handler.record_handler(_Rec())
    assert _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Count"] == 1


def test_record_handler_ignores_unknown_event_type(handler):
    class _Rec:
        body = json.dumps({"eventType": "somethingElse", "clientId": "x"})

    handler.record_handler(_Rec())  # must not raise
    assert _resource().Table(SUBSCRIPTIONS_TABLE).scan()["Count"] == 0
