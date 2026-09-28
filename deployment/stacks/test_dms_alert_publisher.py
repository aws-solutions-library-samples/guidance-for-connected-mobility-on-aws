"""Guards for the DMS alert publisher wiring (T13.5 option c1).

Spec: `.kiro/specs/2026-08-07-cms-account-provisioning-model/tasks.md`, T13.5.

Both directions of the context gate are asserted, and the absent direction is the
one that matters. `storage_stack.py` is deployed in PRODUCTION, and this change adds
a Lambda plus a DynamoDB stream consumer to it. If the gate leaked — if the function
were created regardless of whether a DMS bus is configured — every stage would get a
stream consumer publishing to a bus that does not exist. So "not created without the
context flag" is a correctness assertion, not tidiness.

The positive direction also checks the grants are SCOPED, because the failure mode
for an over-broad grant is invisible: a `*` on `events:PutEvents` works perfectly in
every test and every deploy.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/test_dms_alert_publisher.py -v
"""
from __future__ import annotations

import ast
import pathlib

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks.storage_stack import StorageStack

_STAGE = "test"
_BUS = "dms-test-events"


def _synth(with_bus: bool) -> Template:
    ctx = {"dmsEventBusName": _BUS} if with_bus else {}
    app = cdk.App(context=ctx)
    stack = StorageStack(
        app,
        f"cms-{_STAGE}-storage",
        env=cdk.Environment(account="111111111111", region="us-west-2"),
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def with_bus() -> Template:
    return _synth(True)


@pytest.fixture(scope="module")
def without_bus() -> Template:
    return _synth(False)


def _publisher_fns(template: Template) -> dict:
    return {
        lid: res
        for lid, res in template.find_resources("AWS::Lambda::Function").items()
        if "DmsAlertPublisher" in lid
    }


def _stream_mappings(template: Template) -> dict:
    return {
        lid: res
        for lid, res in template.find_resources(
            "AWS::Lambda::EventSourceMapping"
        ).items()
        if "DynamoDB" in str(res["Properties"].get("EventSourceArn", ""))
        or "StreamArn" in str(res["Properties"])
    }


# ── The absent direction — the one that protects production ──────────────────

def test_no_publisher_when_dms_bus_is_not_configured(without_bus: Template) -> None:
    assert not _publisher_fns(without_bus), (
        "DmsAlertPublisher was created without a configured DMS bus. A stage with no "
        "DMS would then run a stream consumer publishing to a nonexistent bus."
    )


def test_no_stream_consumer_when_dms_bus_is_not_configured(
    without_bus: Template,
) -> None:
    """No event-source mapping at all, so the alerts stream keeps zero consumers.

    DynamoDB Streams support a small number of concurrent readers, so an
    unconditionally-created mapping would consume one of them on every stage for no
    benefit.
    """
    assert not _stream_mappings(without_bus), (
        "An event-source mapping on the alerts stream exists without a DMS bus "
        "configured."
    )


# ── The present direction ────────────────────────────────────────────────────

def test_publisher_is_created_when_configured(with_bus: Template) -> None:
    fns = _publisher_fns(with_bus)
    assert len(fns) == 1, f"expected exactly 1 publisher function, found {len(fns)}"
    props = next(iter(fns.values()))["Properties"]
    assert props["Handler"] == "handler.handler"
    env = props["Environment"]["Variables"]
    assert env["DMS_EVENT_BUS_NAME"] == _BUS


def test_cdk_supplies_every_env_var_the_handler_reads(with_bus: Template) -> None:
    """The guard that would have caught review cycle 7's Critical.

    The CDK set `VEHICLES_TABLE` while the handler read `VEHICLES_TABLE_NAME`. Every
    VIN lookup therefore hit a nonexistent default table, every alert was skipped as
    "VIN not resolved", and the Lambda reported success with zero batch failures
    while DMS received nothing. **42 tests passed through that**: the handler tests
    stub the boto3 client so they never exercise the env lookup, and the CDK tests
    asserted CDK's own names against themselves.

    So this does not restate the names — restating them is what failed. It PARSES the
    handler source for `os.environ.get("...")` and asserts the synthesized template
    supplies each one. Rename either side and this fails.
    """
    handler_src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "services/data_processing/lambda/dms_alert_publisher/handler.py"
    )
    assert handler_src.is_file(), f"handler not found at {handler_src}"

    tree = ast.parse(handler_src.read_text())
    required: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Attribute)
            and node.func.value.attr == "environ"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            required.add(node.args[0].value)

    # AWS_REGION is injected by the Lambda runtime, not by our template.
    required -= {"AWS_REGION"}

    assert required, (
        "Parsed no os.environ.get() calls from the handler — non-vacuity guard. If "
        "the handler stopped reading env vars this test would otherwise pass "
        "trivially while proving nothing."
    )

    env = next(iter(_publisher_fns(with_bus).values()))["Properties"]["Environment"][
        "Variables"
    ]
    missing = required - set(env)
    assert not missing, (
        f"The handler reads {sorted(missing)} but the CDK does not supply them. Each "
        "would silently fall back to its in-code default at runtime — for the "
        "vehicles table that means a nonexistent table and every alert dropped, "
        "while the Lambda reports success."
    )


def test_stream_mapping_reports_batch_item_failures(with_bus: Template) -> None:
    """`FunctionResponseTypes` must include ReportBatchItemFailures.

    The handler returns `batchItemFailures`. Without this property the field is
    silently ignored and one unpublishable alert forces a retry of its entire
    batch — so the handler's partial-failure handling would be decorative.
    """
    mappings = _stream_mappings(with_bus)
    assert mappings, "no DynamoDB event-source mapping found"
    props = next(iter(mappings.values()))["Properties"]
    assert props.get("FunctionResponseTypes") == ["ReportBatchItemFailures"], (
        f"FunctionResponseTypes is {props.get('FunctionResponseTypes')!r}; the "
        "handler's batchItemFailures return value has no effect without it."
    )


def test_putevents_is_scoped_to_the_single_dms_bus(with_bus: Template) -> None:
    """A wildcard here would work in every test and every deploy — hence the check."""
    found = False
    for policy in with_bus.find_resources("AWS::IAM::Policy").values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") != "DmsAlertPublisherPutEvents":
                continue
            found = True
            actions = stmt["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            assert actions == ["events:PutEvents"]
            resources = stmt["Resource"]
            resources = [resources] if isinstance(resources, str) else resources
            assert resources != ["*"], "events:PutEvents granted on *"
            assert any(f"event-bus/{_BUS}" in str(r) for r in resources), (
                f"PutEvents resource does not name the DMS bus: {resources}"
            )
    assert found, "DmsAlertPublisherPutEvents statement not found"


def test_vin_lookup_grant_is_getitem_only(with_bus: Template) -> None:
    """The publisher reads one vehicle row. It must not hold Query or Scan."""
    found = False
    for policy in with_bus.find_resources("AWS::IAM::Policy").values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") != "DmsAlertPublisherResolveVin":
                continue
            found = True
            actions = stmt["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            assert actions == ["dynamodb:GetItem"], (
                f"VIN-resolution grant is {sorted(actions)}; GetItem is sufficient "
                "and anything wider reads rows this Lambda has no need for."
            )
    assert found, "DmsAlertPublisherResolveVin statement not found"


def test_alerts_table_keeps_its_stream_and_retain_policy(with_bus: Template) -> None:
    """Non-vacuity: the consumer is worthless if the stream or the table changes.

    RETAIN is asserted because this table holds ~2k live alert rows on staging and
    is read by four UI components; a policy flip to DESTROY would delete them on the
    next stack replacement.
    """
    with_bus.has_resource(
        "AWS::DynamoDB::Table",
        {
            "Properties": Match.object_like(
                {
                    "TableName": f"cms-{_STAGE}-storage-maintenance-alerts",
                    "StreamSpecification": {
                        "StreamViewType": "NEW_AND_OLD_IMAGES"
                    },
                }
            ),
            "DeletionPolicy": "Retain",
        },
    )
