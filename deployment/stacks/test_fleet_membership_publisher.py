"""Guards for the fleet-membership publisher wiring.

Spec: DMS `2026-09-05-dms-fleet-membership-projection`, T4.2.

Structurally identical to `test_dms_alert_publisher.py`, because the wiring
mirrors the pattern that spec's own review-cycle-7 refined. Two directions of
the `dmsEventBusName` gate; the absent direction is the one that protects
prod. Grants asserted at the SID level, scoped, non-wildcard. Env-var supply
parsed from the handler source, not restated — the drift a restated set
would silently allow was the cycle-7 Critical elsewhere in this file.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/test_fleet_membership_publisher.py -v
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
        if "FleetMembershipPublisher" in lid
    }


def _fleet_enrollment_mappings(template: Template) -> dict:
    """Event-source mappings whose stream is the fleet-enrollment table.

    Filters via the logical id of the Fn::GetAtt to the enrollment table's
    StreamArn — matches whichever mapping consumes that specific stream, so
    the sibling DmsAlertPublisher mapping (on maintenance_events) is
    excluded.
    """
    out: dict = {}
    for lid, res in template.find_resources(
        "AWS::Lambda::EventSourceMapping"
    ).items():
        src = res["Properties"].get("EventSourceArn", {})
        src_str = str(src)
        if "FleetEnrollmentTable" in src_str:
            out[lid] = res
    return out


# ── The absent direction — the one that protects production ─────────────────

def test_no_publisher_when_dms_bus_is_not_configured(without_bus: Template) -> None:
    assert not _publisher_fns(without_bus), (
        "FleetMembershipPublisher was created without a configured DMS bus. A "
        "stage with no DMS would then run a stream consumer publishing to a "
        "nonexistent bus."
    )


def test_no_stream_consumer_when_dms_bus_is_not_configured(
    without_bus: Template,
) -> None:
    assert not _fleet_enrollment_mappings(without_bus), (
        "An event-source mapping on the fleet-enrollment stream exists without "
        "a DMS bus configured. Streams cap concurrent readers; adding one "
        "unconditionally consumes a slot on every stage for no benefit."
    )


# ── The stream itself must be enabled with the right view type ──────────────

def test_fleet_enrollment_table_has_new_and_old_images_stream(
    with_bus: Template,
) -> None:
    """The stream is load-bearing in two directions.

    NEW_IMAGE alone loses REMOVE (OldImage is where the removed row lives).
    NEW_AND_OLD_IMAGES also lets the handler detect a MODIFY that changes
    fleetId by diffing the two images. Both are spec D3 correctness
    requirements — a projection that only ever grows is a projection that
    leaks.
    """
    with_bus.has_resource(
        "AWS::DynamoDB::Table",
        {
            "Properties": Match.object_like(
                {
                    "TableName": f"cms-{_STAGE}-storage-fleet-enrollment",
                    "StreamSpecification": {
                        "StreamViewType": "NEW_AND_OLD_IMAGES"
                    },
                }
            ),
            "DeletionPolicy": "Retain",
        },
    )


def test_fleet_enrollment_stream_is_also_absent_when_bus_is_unset(
    without_bus: Template,
) -> None:
    """The stream stays enabled even without the bus.

    The bus gate protects the CONSUMER (publisher + event-source mapping) —
    not the stream itself. Enabling the stream is a one-time property; if
    dmsEventBusName is added later, the mapping picks up from
    StartingPosition.LATEST and prior events are not replayed. Turning the
    stream off when the bus is absent would break that promise for staged
    rollouts. So the stream must be ON in both directions.
    """
    without_bus.has_resource(
        "AWS::DynamoDB::Table",
        {
            "Properties": Match.object_like(
                {
                    "TableName": f"cms-{_STAGE}-storage-fleet-enrollment",
                    "StreamSpecification": {
                        "StreamViewType": "NEW_AND_OLD_IMAGES"
                    },
                }
            ),
        },
    )


# ── The present direction ───────────────────────────────────────────────────

def test_publisher_is_created_when_configured(with_bus: Template) -> None:
    fns = _publisher_fns(with_bus)
    assert len(fns) == 1, f"expected exactly 1 publisher function, found {len(fns)}"
    props = next(iter(fns.values()))["Properties"]
    assert props["Handler"] == "handler.handler"
    env = props["Environment"]["Variables"]
    assert env["DMS_EVENT_BUS_NAME"] == _BUS


def test_cdk_supplies_every_env_var_the_handler_reads(with_bus: Template) -> None:
    """Guard against the env-var name drift that made 42 tests pass in the
    sibling publisher's cycle 7. Parses the handler for os.environ.get()
    calls and asserts each key is supplied by the CDK.
    """
    handler_src = (
        pathlib.Path(__file__).resolve().parents[2]
        / "services/data_processing/lambda/fleet_membership_publisher/handler.py"
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

    # AWS_REGION is injected by the Lambda runtime.
    required -= {"AWS_REGION"}

    assert required, (
        "Parsed no os.environ.get() calls from the handler — non-vacuity guard."
    )

    env = next(iter(_publisher_fns(with_bus).values()))["Properties"][
        "Environment"
    ]["Variables"]
    missing = required - set(env)
    assert not missing, (
        f"The handler reads {sorted(missing)} but the CDK does not supply them."
    )


def test_stream_mapping_reports_batch_item_failures(with_bus: Template) -> None:
    """The handler returns batchItemFailures; without this flag it is
    silently ignored and one un-publishable record forces the whole batch
    to retry.
    """
    mappings = _fleet_enrollment_mappings(with_bus)
    assert mappings, "no event-source mapping found on fleet_enrollment stream"
    props = next(iter(mappings.values()))["Properties"]
    assert props.get("FunctionResponseTypes") == ["ReportBatchItemFailures"], (
        f"FunctionResponseTypes is {props.get('FunctionResponseTypes')!r}"
    )


def test_putevents_is_scoped_to_the_single_dms_bus(with_bus: Template) -> None:
    """A wildcard here would work in every test and every deploy — hence the
    dedicated SID assertion. Same posture as the sibling alert publisher.
    """
    found = False
    for policy in with_bus.find_resources("AWS::IAM::Policy").values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") != "FleetMembershipPublisherPutEvents":
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
    assert found, "FleetMembershipPublisherPutEvents statement not found"


def test_vin_lookup_grant_is_getitem_only(with_bus: Template) -> None:
    """The publisher reads one vehicle row per pair. Must not hold Query/Scan."""
    found = False
    for policy in with_bus.find_resources("AWS::IAM::Policy").values():
        for stmt in policy["Properties"]["PolicyDocument"]["Statement"]:
            if stmt.get("Sid") != "FleetMembershipPublisherResolveVin":
                continue
            found = True
            actions = stmt["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            assert actions == ["dynamodb:GetItem"], (
                f"VIN-resolution grant is {sorted(actions)}; GetItem is sufficient"
            )
    assert found, "FleetMembershipPublisherResolveVin statement not found"
