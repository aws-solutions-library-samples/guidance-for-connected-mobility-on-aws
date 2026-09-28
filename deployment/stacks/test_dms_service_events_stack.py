"""Guards for the CMS→DMS-inbound service-event subscriber (T4.4).

Spec: `2026-09-02-cms-dms-service-convergence`, T4.4.

Three classes of assertion, in descending order of what they protect:

1. **The event selectors are correct.** A rule whose `source` or `detail-type`
   does not match the emitter deploys perfectly happily and fires never. There is
   no runtime error, no failed deploy, no log line — the subscriber is simply
   inert, which is the state the spec's own Context table describes the event as
   already being in ("Emitted, unconsumed"). Landing a second inert subscriber
   while believing the gap closed is the worst available outcome here, so the
   literals are asserted against the DMS emitter's values.

2. **The context gate holds in the absent direction.** Same reasoning as
   `test_dms_alert_publisher.py`: a leaked gate gives every stage a rule and a
   Lambda pointed at a bus that does not exist.

3. **Grants are scoped and one-directional.** An over-broad grant works perfectly
   in every test and every deploy, so scope has to be asserted rather than
   observed. The write-only posture on the markers table is asserted too — the
   reader is Group 5's code in a different role, and a read grant here would make
   the data flow's direction unreadable from the policy.

Run with:
  cd deployment && python3 -m pytest stacks/test_dms_service_events_stack.py -v
"""
from __future__ import annotations

import pathlib
import re

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from stacks.dms_service_events_stack import (
    DMS_EVENT_SOURCE,
    DMS_RO_STATUS_DETAIL_TYPE,
    VIN_INDEX_NAME,
    DmsServiceEventsStack,
)

_STAGE = "test"
_BUS = "dms-test-events"
_VEHICLES = "cms-test-storage-vehicles"

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_HANDLER = (
    _REPO_ROOT
    / "services"
    / "data_processing"
    / "lambda"
    / "dms_ro_cache_invalidator"
    / "handler.py"
)


def _synth(with_bus: bool) -> Template:
    ctx = {"dmsEventBusName": _BUS} if with_bus else {}
    app = cdk.App(context=ctx)
    stack = DmsServiceEventsStack(
        app,
        f"cms-{_STAGE}-dms-service-events",
        stage=_STAGE,
        vehicles_table_name=_VEHICLES,
        env=cdk.Environment(account="111111111111", region="us-west-2"),
    )
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def with_bus() -> Template:
    return _synth(True)


@pytest.fixture(scope="module")
def without_bus() -> Template:
    return _synth(False)


def _rules(template: Template) -> dict:
    return template.find_resources("AWS::Events::Rule")


def _fns(template: Template) -> dict:
    return {
        lid: res
        for lid, res in template.find_resources("AWS::Lambda::Function").items()
        if "DmsRoCacheInvalidator" in lid
    }


# ── 1. The selectors — an inert rule is the failure mode ─────────────────────

def test_rule_matches_the_dms_emitters_source_and_detail_type(with_bus: Template) -> None:
    """Pins WIRING, not values — and the distinction matters.

    This compares the template against the module's own constants, so it cannot
    catch a wrong constant: rename `DMS_EVENT_SOURCE` and both sides move
    together. Verified by mutation — changing the constant to
    `"dms.service_lane"` leaves this test green.

    What it does catch, and what nothing else would, is a constant that is
    correct but never reaches the pattern. Verified the same way: hardcoding
    `source=["dms.something-else"]` in the stack fails here and only here.

    The VALUES are pinned by the literal assertions in the next test. Two tests,
    two properties; neither is redundant and neither should be read as covering
    the other's job.
    """
    rules = _rules(with_bus)
    assert rules, "no EventBridge rule was created"
    patterns = [r["Properties"]["EventPattern"] for r in rules.values()]
    matching = [
        p for p in patterns
        if p.get("source") == [DMS_EVENT_SOURCE]
        and p.get("detail-type") == [DMS_RO_STATUS_DETAIL_TYPE]
    ]
    assert matching, (
        f"no rule matches source={DMS_EVENT_SOURCE!r} "
        f"detail-type={DMS_RO_STATUS_DETAIL_TYPE!r}. Patterns found: {patterns}. "
        "A rule whose selectors do not match the emitter deploys cleanly and "
        "fires never — an inert subscriber, which is the state this task exists "
        "to change."
    )


def test_selector_constants_are_the_values_dms_actually_publishes(with_bus: Template) -> None:
    """Pinned by literal, because a rename on either side is silent.

    DMS `source/_lib/events.py` publishes `Source="dms.service-lane"` /
    `DetailType="dms.ro.status_changed"`. Asserting the constants here means a
    CMS-side edit is caught; the cross-repo direction is inherently unpinnable
    from this repo, which is why the values are stated rather than derived.
    """
    assert DMS_EVENT_SOURCE == "dms.service-lane"
    assert DMS_RO_STATUS_DETAIL_TYPE == "dms.ro.status_changed"


def test_rule_does_not_filter_on_any_detail_field(with_bus: Template) -> None:
    """Spec D4: the event is a trigger, not a payload.

    A `detail` filter would couple the rule to the event's payload shape, so a
    future allowlist change would silently stop matching.
    """
    for lid, rule in _rules(with_bus).items():
        pattern = rule["Properties"]["EventPattern"]
        assert "detail" not in pattern, (
            f"{lid} filters on a detail field: {pattern.get('detail')!r}. D4 keeps "
            "CMS a subscriber that reacts to the fact of a change, not to its "
            "contents."
        )


def test_rule_targets_the_invalidator_lambda(with_bus: Template) -> None:
    targets = [
        t
        for rule in _rules(with_bus).values()
        for t in rule["Properties"].get("Targets", [])
    ]
    assert targets, "the rule has no target — nothing would run on an event"
    assert len(targets) == 1, f"expected exactly one target, found {len(targets)}"


def test_rule_subscribes_to_the_dms_bus_and_does_not_create_one(with_bus: Template) -> None:
    # The DMS bus is owned by DMS. Creating one here would mean subscribing to a
    # CMS-local bus nothing publishes to — inert again, and harder to spot
    # because the rule itself would look correct.
    with_bus.resource_count_is("AWS::Events::EventBus", 0)
    buses = {
        rule["Properties"].get("EventBusName")
        for rule in _rules(with_bus).values()
    }
    assert buses == {_BUS}, f"rule is not attached to the DMS bus: {buses}"


# ── 2. The context gate, absent direction ───────────────────────────────────

def test_no_rule_without_a_configured_dms_bus(without_bus: Template) -> None:
    assert not _rules(without_bus), (
        "an EventBridge rule was created with no DMS bus configured — it would "
        "reference a bus that does not exist on that stage"
    )


def test_no_lambda_without_a_configured_dms_bus(without_bus: Template) -> None:
    assert not _fns(without_bus)


def test_no_marker_table_without_a_configured_dms_bus(without_bus: Template) -> None:
    without_bus.resource_count_is("AWS::DynamoDB::Table", 0)


def test_gated_stack_synthesizes_an_empty_template_rather_than_failing(
    without_bus: Template,
) -> None:
    """An opted-in stage with no bus produces an empty stack, not a synth error.

    A first draft of this asserted `... == {} or True`, which is vacuous — the
    `or True` makes the whole expression pass whatever the template contains.
    Recorded rather than quietly fixed, because it is the same
    assertion-that-cannot-fail shape this spec has now found three times, and
    finding it in my own test is the argument for the mutation pass.

    An empty CloudFormation template omits `Resources` entirely rather than
    carrying `{}`, so the check has to accept both.
    """
    resources = without_bus.to_json().get("Resources", {})
    assert resources == {}, f"gated stack is not empty: {sorted(resources)}"


# ── 3. Grants: scoped, and one-directional ──────────────────────────────────

def _policy_statements(template: Template) -> list[dict]:
    out: list[dict] = []
    for pol in template.find_resources("AWS::IAM::Policy").values():
        out.extend(pol["Properties"]["PolicyDocument"]["Statement"])
    return out


def test_vin_resolution_grant_queries_the_index_and_cannot_scan(with_bus: Template) -> None:
    """Query on the `vin-index` GSI, scoped to the INDEX ARN.

    Review Cycle 4 caught the handler issuing an unpaginated `scan`: `scan`
    applies its filter AFTER reading up to 1 MB, so a VIN past the first page
    resolved to nothing, the marker was dropped, and the read path then concluded
    the cache was fresh. Fixed by querying the GSI that already exists for
    exactly this lookup (`storage_stack.py:512`, PK `vin`, KEYS_ONLY).

    The grant is asserted to be Query-on-index and NOT Scan-on-table, so a revert
    to scanning fails on permissions at runtime instead of degrading quietly —
    which matters because that GSI's own comment records a prior case where a
    scan fallback "made a MISSING index look merely like a slow one for months".
    """
    stmts = [
        s for s in _policy_statements(with_bus)
        if s.get("Sid") == "DmsRoCacheInvalidatorResolveVin"
    ]
    assert stmts, "DmsRoCacheInvalidatorResolveVin statement not found"
    for s in stmts:
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        assert actions == ["dynamodb:Query"], (
            f"expected exactly dynamodb:Query, got {actions}"
        )
        resources = s["Resource"] if isinstance(s["Resource"], list) else [s["Resource"]]
        assert all(r != "*" for r in resources), f"unscoped resource: {resources}"
        assert any(_VEHICLES in str(r) for r in resources), (
            f"grant does not name the vehicles table: {resources}"
        )
        assert any(f"/index/{VIN_INDEX_NAME}" in str(r) for r in resources), (
            f"grant is not scoped to the {VIN_INDEX_NAME} GSI: {resources}. A GSI "
            "query needs the index ARN, so a table-only ARN would fail at runtime."
        )


def test_no_scan_grant_so_a_revert_to_scanning_cannot_run_silently(
    with_bus: Template,
) -> None:
    # Asserted across ALL statements, not just the named one: a second policy
    # granting Scan would satisfy the test above and still let the defect run.
    for s in _policy_statements(with_bus):
        actions = s.get("Action")
        actions = actions if isinstance(actions, list) else [actions]
        for a in actions:
            assert a not in ("dynamodb:Scan", "dynamodb:*"), (
                f"role can {a} — a revert to the unpaginated scan would run and "
                "silently drop markers rather than failing"
            )


def test_stack_and_handler_agree_on_the_index_name(with_bus: Template) -> None:
    """The IAM grant and the handler must name the SAME index.

    Two literals for one index is how they drift: a grant on `vin-index` with a
    handler querying `vinIndex` fails only at runtime, and fails as a dropped
    marker rather than as an error. Derived from the handler source rather than
    restated, same reasoning as the env-var test.
    """
    source = _HANDLER.read_text(encoding="utf-8")
    match = re.search(r'_VIN_INDEX\s*=\s*"([^"]+)"', source)
    assert match, "premise failed: _VIN_INDEX not found in the handler source"
    assert match.group(1) == VIN_INDEX_NAME, (
        f"handler queries {match.group(1)!r} but the grant is scoped to "
        f"{VIN_INDEX_NAME!r}"
    )


def test_marker_grant_is_write_only(with_bus: Template) -> None:
    """No read grant. The reader is Group 5's code in a different role.

    Least privilege is cheap here, and it makes the direction of the data flow
    legible from the policy alone — this Lambda produces markers and never
    consumes them.
    """
    stmts = [
        s for s in _policy_statements(with_bus)
        if s.get("Sid") == "DmsRoCacheInvalidatorWriteMarker"
    ]
    assert stmts, "DmsRoCacheInvalidatorWriteMarker statement not found"
    for s in stmts:
        actions = s["Action"] if isinstance(s["Action"], list) else [s["Action"]]
        assert actions == ["dynamodb:PutItem"], f"unexpected actions: {actions}"


def test_no_grant_anywhere_permits_reading_a_marker(with_bus: Template) -> None:
    """Scoped to the MARKERS table, not to the action globally.

    A first draft asserted no `dynamodb:Query` appeared in ANY statement. That
    broke when the VIN resolution correctly moved from Scan-on-table to
    Query-on-GSI (Fix Group 2), because it was pinning a proxy — "no read verbs
    anywhere" — instead of the property, which is "cannot read a marker". The
    proxy was both too broad, blocking a legitimate grant, and too narrow, since
    it would have missed a `BatchGetItem` on the markers table.

    So: find every statement whose resources reference the markers table, and
    assert those statements carry only the write verb.
    """
    marker_lids = list(with_bus.find_resources("AWS::DynamoDB::Table").keys())
    assert marker_lids, "premise failed: no marker table in the template"

    def _mentions_markers(resource) -> bool:
        blob = str(resource)
        return any(lid in blob for lid in marker_lids)

    checked = 0
    for s in _policy_statements(with_bus):
        resources = s.get("Resource")
        resources = resources if isinstance(resources, list) else [resources]
        if not any(_mentions_markers(r) for r in resources):
            continue
        checked += 1
        actions = s.get("Action")
        actions = actions if isinstance(actions, list) else [actions]
        assert actions == ["dynamodb:PutItem"], (
            f"a statement on the markers table grants {actions} — this Lambda "
            "only writes markers; the reader is Group 5's code in another role"
        )
    assert checked >= 1, (
        "no policy statement references the markers table, so the assertion above "
        "never ran — the write grant has gone missing or the ARN shape changed"
    )


def test_no_events_putevents_grant(with_bus: Template) -> None:
    """This direction is inbound only.

    The outbound CMS→DMS publishers live in `storage_stack.py` and hold their own
    scoped `events:PutEvents`. A grant here would mean this stack had quietly
    become bidirectional.
    """
    for s in _policy_statements(with_bus):
        actions = s.get("Action")
        actions = actions if isinstance(actions, list) else [actions]
        assert "events:PutEvents" not in actions


# ── Lambda env: the names must match what the handler reads ──────────────────

def test_env_var_names_match_the_handler_source(with_bus: Template) -> None:
    """Derived from the handler, not restated.

    `storage_stack.py:2118` records why in this repo's own words: the sibling
    publisher shipped with `VEHICLES_TABLE` set while the handler read
    `VEHICLES_TABLE_NAME`, so every lookup hit a nonexistent table and the
    function reported success with zero failures. Forty-two tests passed through
    it, "because the handler tests stub the client and the CDK tests asserted
    CDK's own names".
    """
    source = _HANDLER.read_text(encoding="utf-8")
    required = set(re.findall(r'os\.environ\.get\(\s*"([A-Z_]+)"', source))
    # AWS_REGION is provided by the Lambda runtime, not by this stack.
    required.discard("AWS_REGION")
    assert required, "premise failed: no env vars found in the handler source"

    fns = _fns(with_bus)
    assert fns, "invalidator Lambda not found"
    for lid, fn in fns.items():
        configured = set(fn["Properties"]["Environment"]["Variables"].keys())
        missing = required - configured
        assert not missing, (
            f"{lid} does not set {sorted(missing)}, which the handler reads. "
            "A name mismatch here is invisible at deploy time."
        )


def test_marker_table_name_is_stage_scoped(with_bus: Template) -> None:
    # A stage-agnostic name would have staging write markers a prod read path
    # consults — the defect `issues/2026-08-10-cms-vfo-action-queue-hardcoded-prod-table/`
    # records for the VFO action queue.
    tables = with_bus.find_resources("AWS::DynamoDB::Table")
    assert tables, "marker table not found"
    names = [t["Properties"]["TableName"] for t in tables.values()]
    assert all(_STAGE in str(n) for n in names), f"table name is not stage-scoped: {names}"
