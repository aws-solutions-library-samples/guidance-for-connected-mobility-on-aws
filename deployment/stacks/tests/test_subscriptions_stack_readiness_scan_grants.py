"""The `/simulate/vehicles` list Lambda's readiness-annotation Scan grants.

Spec: `.kiro/specs/2026-09-20-trip-intent-param-contract/` T5.0, closing
security-review S1 (session 3, 2026-09-21).

Why this file exists
--------------------
T5.0 added a readiness annotation to `list_vehicles_handler`: each vehicle carries
`simulation_ready` / `not_ready_reasons`, and the response carries `ready_count`. The
handler computes it from **three table scans** beyond the vehicles table it already
scanned — fleet enrollment, vehicle certificates, and campaigns.

`simulate_list_role` was granted `dynamodb:Scan` on the vehicles table **only**. The
live staging role was verified read-only on 2026-09-21 and held exactly that one
statement, so the gap was real and not theoretical: the next deploy would have shipped
a Lambda that AccessDenies on all three reads.

**The failure mode is security-safe and functionally total**, which is precisely why it
needed a test rather than a comment. Each fetcher returns `None` on exception, so the
response degrades to `simulation_ready: null` + `readiness_unavailable`; the UI leaves
options selectable and `simulate_start_handler` stays the authoritative gate. Nothing
is exposed. But every row reports the unknown state, so T5.0's entire output would be
"could not determine" forever, and no test anywhere would have failed — the handler's
own unit tests stub DynamoDB, and the live check that validated T5.0's logic
(10 ready of 100) was run with admin credentials, which proved the arithmetic and said
nothing about the Lambda's own permissions.

That is the gap this file closes: an IAM statement whose ABSENCE is invisible to every
other test in the repo.

**The property under test is the exact ARN set, not the presence of a Scan.** Per
`~/.kiro/steering/testing.md` § "Mutation testing at the green boundary", and per the
sibling `test_subscriptions_stack_simulate_invoke_grant.py`, a test asserting "a Scan
statement exists" passes just as happily when the resource is `"*"` or
`table/cms-staging-*`. So every assertion below is **exact-set**, never `in` /
`contains`:

* dropping any one of the four ARNs must FAIL
* widening any resource to `"*"` or a `table/cms-staging-*` prefix must FAIL
* adding a write action (`dynamodb:PutItem`, `DeleteItem`, …) must FAIL
* granting the readiness scans to the START role instead must FAIL

Expectations are written longhand and NOT imported from `stacks.subscriptions_stack`.
Deriving them from the implementation would move both sides together under mutation and
every assertion would be a tautology.

Table names mirror `services/connectors/subscriptions/simulate_vehicle/handler.py:336-342`,
which resolves each from an env var with these as defaults.

All identifiers are synthetic; the account is the AWS-documentation placeholder. This
file is not publish-excluded, so the real account id must never appear here.

Run with (from deployment/):
    .venv/bin/python -m pytest stacks/tests/test_subscriptions_stack_readiness_scan_grants.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — mirrors test_subscriptions_stack_simulate_invoke_grant.py
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent          # deployment/stacks/tests/
_STACKS = _HERE.parent                           # deployment/stacks/
_DEPLOYMENT = _STACKS.parent                     # deployment/

for _dir in (str(_DEPLOYMENT), str(_STACKS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

_STAGE = "staging"
_ACCOUNT = "123456789012"   # AWS-documentation placeholder — never a real account
_REGION = "us-west-2"

_STACK = f"cms-{_STAGE}-subscriptions"

_LIST_FN_NAME = f"cms-{_STAGE}-subscriptions-simulate-list-vehicles"
_START_FN_NAME = f"cms-{_STAGE}-subscriptions-simulate-start"


def _table_arn(name: str) -> str:
    return f"arn:aws:dynamodb:{_REGION}:{_ACCOUNT}:table/{name}"


# ── Expectations, restated independently of the implementation ────────────────

_VEHICLES_ARN = _table_arn(f"cms-{_STAGE}-storage-vehicles")
_FLEET_ENROLLMENT_ARN = _table_arn(f"cms-{_STAGE}-storage-fleet-enrollment")
_VEHICLE_CERTIFICATES_ARN = _table_arn(f"cms-{_STAGE}-storage-vehicle-certificates")
_CAMPAIGNS_ARN = _table_arn(f"cms-{_STAGE}-campaigns")

#: Every table the list Lambda may Scan, and no others.
_EXPECTED_SCAN_ARNS = {
    _VEHICLES_ARN,
    _FLEET_ENROLLMENT_ARN,
    _VEHICLE_CERTIFICATES_ARN,
    _CAMPAIGNS_ARN,
}

#: The three added by T5.0, named separately so a failure says which is missing.
_READINESS_ARNS = {
    _FLEET_ENROLLMENT_ARN,
    _VEHICLE_CERTIFICATES_ARN,
    _CAMPAIGNS_ARN,
}

#: Readiness is a read-only advisory annotation (T5.0 Accept 5). Any of these on this
#: role would let a route whose only gate is one Cognito group mutate fleet
#: provisioning state.
_FORBIDDEN_ACTIONS = {
    "dynamodb:*",
    "dynamodb:PutItem",
    "dynamodb:UpdateItem",
    "dynamodb:DeleteItem",
    "dynamodb:BatchWriteItem",
    "dynamodb:CreateTable",
    "dynamodb:DeleteTable",
    "dynamodb:UpdateTable",
}


# ---------------------------------------------------------------------------
# Synth
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def template() -> dict:
    from aws_cdk import App, Environment

    from stacks.subscriptions_stack import SubscriptionsStack  # noqa: PLC0415

    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    app = App(context={})
    SubscriptionsStack(
        app,
        _STACK,
        stage=_STAGE,
        env=Environment(account=_ACCOUNT, region=_REGION),
    )
    raw = app.synth().get_stack_by_name(_STACK).template
    return json.loads(json.dumps(raw))


def _resolve(node):
    """Collapse a synthesized ARN node to a plain string where possible."""
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if set(node) == {"Ref"} and node["Ref"] == "AWS::Partition":
            return "aws"
        if "Fn::Join" in node:
            sep, parts = node["Fn::Join"]
            return sep.join(_resolve(p) for p in parts)
        return node
    if isinstance(node, list):
        return [_resolve(p) for p in node]
    return node


def _as_set(node) -> set:
    resolved = _resolve(node)
    if isinstance(resolved, str):
        return {resolved}
    return set(resolved)


def _resources_of_type(template: dict, cfn_type: str) -> dict:
    return {k: v for k, v in template["Resources"].items()
            if v["Type"] == cfn_type}


def _function_logical_id(template: dict, function_name: str) -> str:
    fns = {
        k: v for k, v in _resources_of_type(template, "AWS::Lambda::Function").items()
        if v["Properties"].get("FunctionName") == function_name
    }
    assert len(fns) == 1, (
        f"expected exactly one Lambda named {function_name!r}, found {sorted(fns)}. "
        "If it was renamed, update the constant — do NOT relax this assertion."
    )
    return next(iter(fns))


def _role_logical_id_for(template: dict, function_name: str) -> str:
    fn_id = _function_logical_id(template, function_name)
    role_node = template["Resources"][fn_id]["Properties"]["Role"]
    assert "Fn::GetAtt" in role_node, f"unexpected Role shape: {role_node!r}"
    return role_node["Fn::GetAtt"][0]


def _statements_for_role(template: dict, role_logical_id: str) -> list[dict]:
    statements: list[dict] = []
    for _, pol in _resources_of_type(template, "AWS::IAM::Policy").items():
        roles = json.dumps(pol["Properties"].get("Roles", []))
        if role_logical_id in roles:
            statements.extend(pol["Properties"]["PolicyDocument"]["Statement"])
    return statements


def _scan_statements(statements: list[dict]) -> list[dict]:
    return [
        s for s in statements
        if "dynamodb:Scan" in _as_set(s.get("Action", []))
    ]


def _all_dynamodb_statements(statements: list[dict]) -> list[dict]:
    return [
        s for s in statements
        if any(str(a).startswith("dynamodb:") for a in _as_set(s.get("Action", [])))
    ]


def _list_role_statements(template: dict) -> list[dict]:
    return _statements_for_role(
        template, _role_logical_id_for(template, _LIST_FN_NAME)
    )


# ---------------------------------------------------------------------------
# The grant set
# ---------------------------------------------------------------------------


def test_scan_grant_arns_are_exactly_the_four_tables(template: dict) -> None:
    """Exact set. A missing ARN fails; an extra ARN also fails.

    This is the assertion whose absence let S1 ship: the role held one ARN while the
    handler read four, and nothing in the repo compared the two numbers.
    """
    scans = _scan_statements(_list_role_statements(template))
    assert scans, (
        "the list Lambda's role has NO dynamodb:Scan statement at all — it cannot "
        "even list vehicles"
    )

    granted: set[str] = set()
    for s in scans:
        granted |= _as_set(s.get("Resource", []))

    assert granted == _EXPECTED_SCAN_ARNS, (
        "the Scan grant set is not exactly the four tables the handler reads.\n"
        f"  missing: {sorted(_EXPECTED_SCAN_ARNS - granted)}\n"
        f"  unexpected: {sorted(granted - _EXPECTED_SCAN_ARNS)}\n"
        "A missing readiness ARN makes every vehicle report "
        "`readiness_unavailable` at runtime with no test failing. An unexpected ARN "
        "widens a route gated only by a Cognito group. Do NOT relax to a subset check."
    )


@pytest.mark.parametrize(
    "arn",
    sorted(_READINESS_ARNS),
    ids=lambda a: a.rsplit("/", 1)[-1],
)
def test_each_readiness_table_is_granted_individually(template: dict, arn: str) -> None:
    """One case per readiness table, so a failure names the table rather than a diff.

    Parametrized rather than folded into the set assertion above: when this breaks, the
    useful output is "fleet-enrollment is missing", not a three-way set difference.
    """
    granted: set[str] = set()
    for s in _scan_statements(_list_role_statements(template)):
        granted |= _as_set(s.get("Resource", []))
    table = arn.rsplit("/", 1)[-1]
    assert arn in granted, (
        f"no dynamodb:Scan grant for {table!r}. The handler scans it to compute "
        "readiness (see handler.py:336-342); without the grant the fetcher "
        "AccessDenies, returns None, and every vehicle reports "
        "`simulation_ready: null` + `readiness_unavailable`."
    )


def test_no_scan_resource_is_a_wildcard_or_prefix(template: dict) -> None:
    """`"*"` and `table/cms-staging-*` both satisfy a presence check. Neither is allowed.

    The mutation this exists for: replacing the four exact ARNs with one prefix ARN
    keeps the handler working, so every behavioural test stays green while the role
    gains read access to every table in the stage.
    """
    for s in _scan_statements(_list_role_statements(template)):
        for res in _as_set(s.get("Resource", [])):
            assert res != "*", "dynamodb:Scan on '*' — every table in the account"
            assert "*" not in res, (
                f"wildcard in Scan resource {res!r}. Each table must be named "
                "exactly; a prefix grant reads tables the handler never touches and "
                "no behavioural test would notice."
            )


# ---------------------------------------------------------------------------
# Read-only, and on the right role
# ---------------------------------------------------------------------------


def test_list_role_holds_no_dynamodb_write_action(template: dict) -> None:
    """Readiness is advisory and read-only (T5.0 Accept 5)."""
    for s in _all_dynamodb_statements(_list_role_statements(template)):
        actions = _as_set(s.get("Action", []))
        offending = actions & _FORBIDDEN_ACTIONS
        assert not offending, (
            f"the list Lambda's role holds write action(s) {sorted(offending)}. "
            "This route computes a read-only advisory annotation; a write grant here "
            "would let one Cognito group mutate fleet provisioning state."
        )
        for a in actions:
            assert a != "dynamodb:*", "dynamodb:* on the readiness role"


def test_readiness_scans_are_not_granted_to_the_start_role(template: dict) -> None:
    """The start role must not inherit the readiness reads.

    Two roles exist so the two operations cannot widen each other (see the stack's own
    comment 2). `simulate_start_handler` does not compute readiness — T5.0 Accept 5
    requires it stay untouched — so these ARNs appearing here would mean the grant was
    attached to the wrong role.
    """
    start_statements = _statements_for_role(
        template, _role_logical_id_for(template, _START_FN_NAME)
    )
    granted: set[str] = set()
    for s in _all_dynamodb_statements(start_statements):
        granted |= _as_set(s.get("Resource", []))

    leaked = granted & _READINESS_ARNS
    assert not leaked, (
        f"the START role has access to readiness table(s) {sorted(leaked)}. "
        "Readiness is the list route's concern; the start route authorizes and "
        "dispatches. Grant them separately or the two-role split buys nothing."
    )


def test_the_two_roles_are_distinct(template: dict) -> None:
    """Positive control for the test above — it is vacuous if both names resolve to one role."""
    list_role = _role_logical_id_for(template, _LIST_FN_NAME)
    start_role = _role_logical_id_for(template, _START_FN_NAME)
    assert list_role != start_role, (
        "the list and start Lambdas share one IAM role, so the separation asserted by "
        "test_readiness_scans_are_not_granted_to_the_start_role cannot hold and that "
        "test is vacuous."
    )
