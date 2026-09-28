"""Synth guards for the `/simulate/start` → simulation-Lambda invoke grant (T6.1).

Spec: `.kiro/specs/2026-09-15-cms-cs-campaign-ownership/` Group 6, Task 6.1.

Why this file exists
--------------------
Task 6.1 adds the ONE genuinely new IAM permission in this spec: the
`/simulate/start` Lambda may now invoke the simulation Lambda. Group 3's security
review established that its GSI `Query` grant already existed, so this is the only
grant a reviewer has to reason about from scratch.

**The property under test is the grant's SCOPE, not its presence.** A test that
asserts "an `lambda:InvokeFunction` statement exists" passes just as happily when
the resource is `"*"` — and this spec has already shipped one test that pinned a
construct's presence while the property it existed to protect stayed mutable
(RESUME.md § "The lesson this session added"). So every assertion below is
**exact-set or exact-value**, never `in` / `contains`:

* widening `Resource` to `"*"` must FAIL
* widening `Resource` to a `:*` version/alias suffix must FAIL
* adding a second action (`lambda:UpdateFunctionCode`, `lambda:GetFunction`, …)
  must FAIL
* pointing the grant at a different function must FAIL

Expectations are written out longhand and NOT imported from
`stacks.subscriptions_stack`. If they were derived from the implementation, a
mutation would move both sides together and every assertion would still pass —
the assertion would be a tautology rather than a contract.

All identifiers are synthetic; the account is the AWS-documentation placeholder.
This file is not publish-excluded, so the real account id must never appear here.

Run with (from deployment/):
    .venv/bin/python -m pytest stacks/tests/test_subscriptions_stack_simulate_invoke_grant.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — mirrors test_ui_stack_fleet_intelligence_adp_grants.py
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

# ── Expectations, restated independently of the implementation ────────────────

# simulation_stack.py:866 sets `function_name=f"{prefix}-simulation-api"` with the
# same `cms-{stage}` prefix. Restated here rather than imported.
_SIMULATION_FUNCTION_NAME = f"cms-{_STAGE}-simulation-api"
_EXPECTED_INVOKE_ARN = (
    f"arn:aws:lambda:{_REGION}:{_ACCOUNT}:function:{_SIMULATION_FUNCTION_NAME}"
)

# Invoke only. Every one of these would be a privilege escalation from a route
# whose only gate is one Cognito group.
_FORBIDDEN_ACTIONS = {
    "lambda:*",
    "lambda:UpdateFunctionCode",
    "lambda:UpdateFunctionConfiguration",
    "lambda:AddPermission",
    "lambda:GetFunction",
    "lambda:CreateFunction",
    "lambda:DeleteFunction",
    "lambda:InvokeAsync",
}

# The role that gets the grant, and the one that must NOT.
_START_FN_NAME = f"cms-{_STAGE}-subscriptions-simulate-start"
_LIST_FN_NAME = f"cms-{_STAGE}-subscriptions-simulate-list-vehicles"


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
    """The logical id of the IAM::Role attached to `function_name`."""
    fn_id = _function_logical_id(template, function_name)
    role_node = template["Resources"][fn_id]["Properties"]["Role"]
    # `Role` is {"Fn::GetAtt": ["<RoleLogicalId>", "Arn"]}
    assert "Fn::GetAtt" in role_node, f"unexpected Role shape: {role_node!r}"
    return role_node["Fn::GetAtt"][0]


def _statements_for_role(template: dict, role_logical_id: str) -> list[dict]:
    """Every policy statement attached to a role, across all its IAM::Policy docs."""
    statements: list[dict] = []
    for _, pol in _resources_of_type(template, "AWS::IAM::Policy").items():
        roles = json.dumps(pol["Properties"].get("Roles", []))
        if role_logical_id in roles:
            statements.extend(pol["Properties"]["PolicyDocument"]["Statement"])
    return statements


def _lambda_invoke_statements(statements: list[dict]) -> list[dict]:
    return [
        s for s in statements
        if any(str(a).startswith("lambda:") for a in _as_set(s.get("Action", [])))
    ]


# ---------------------------------------------------------------------------
# Anti-vacuity — the premises these assertions rest on must be real
# ---------------------------------------------------------------------------


class TestPremises:
    def test_stack_synthesizes_with_resources(self, template):
        assert len(template.get("Resources", {})) > 20, (
            "subscriptions stack synthesized implausibly few resources"
        )

    def test_both_simulate_functions_exist(self, template):
        """If either is renamed this must fail rather than silently stop checking."""
        for name in (_START_FN_NAME, _LIST_FN_NAME):
            _function_logical_id(template, name)

    def test_start_role_has_statements_at_all(self, template):
        role = _role_logical_id_for(template, _START_FN_NAME)
        assert _statements_for_role(template, role), (
            "found no policy statements for the simulate-start role — the role "
            "lookup is broken and every assertion below would pass vacuously"
        )

    def test_arn_expectation_is_a_literal_not_a_token(self):
        """Guards the expectation itself: a CFN token here would compare equal to
        nothing and the scope assertions would be untestable."""
        assert _EXPECTED_INVOKE_ARN.startswith("arn:aws:lambda:")
        assert "${Token" not in _EXPECTED_INVOKE_ARN
        assert _EXPECTED_INVOKE_ARN.endswith(f":function:{_SIMULATION_FUNCTION_NAME}")


# ---------------------------------------------------------------------------
# The grant, and its scope
# ---------------------------------------------------------------------------


class TestSimulateStartInvokeGrant:
    """The grant exists, and it is narrow. The second half is the point."""

    def _invoke_statement(self, template) -> dict:
        role = _role_logical_id_for(template, _START_FN_NAME)
        matches = _lambda_invoke_statements(_statements_for_role(template, role))
        assert len(matches) == 1, (
            f"expected exactly one lambda:* statement on the simulate-start role, "
            f"found {len(matches)}: {json.dumps(matches, indent=2)}"
        )
        return matches[0]

    def test_grant_exists(self, template):
        self._invoke_statement(template)

    def test_action_is_exactly_invoke_function(self, template):
        """MUTATION: add `lambda:GetFunction` alongside → this fails."""
        actions = _as_set(self._invoke_statement(template)["Action"])
        assert actions == {"lambda:InvokeFunction"}, (
            f"expected exactly {{'lambda:InvokeFunction'}}, got {sorted(actions)}"
        )

    def test_no_forbidden_action_anywhere_on_the_role(self, template):
        """Belt-and-braces: none of the escalation actions appears in ANY statement
        on this role, not merely outside the invoke statement."""
        role = _role_logical_id_for(template, _START_FN_NAME)
        all_actions: set = set()
        for s in _statements_for_role(template, role):
            all_actions |= {str(a) for a in _as_set(s.get("Action", []))}
        overlap = all_actions & _FORBIDDEN_ACTIONS
        assert overlap == set(), (
            f"simulate-start role carries escalation actions: {sorted(overlap)}"
        )

    def test_resource_is_exactly_the_simulation_function_arn(self, template):
        """THE scope assertion.

        MUTATION: change the resource to `"*"` → this fails.
        MUTATION: append `:*` for versions/aliases → this fails.
        MUTATION: point it at a different function → this fails.
        """
        resources = _as_set(self._invoke_statement(template)["Resource"])
        assert resources == {_EXPECTED_INVOKE_ARN}, (
            f"expected exactly {{{_EXPECTED_INVOKE_ARN!r}}}, got {sorted(resources)}"
        )

    def test_resource_is_not_a_wildcard(self, template):
        """Stated separately from the exact-set assertion above so the failure
        message names the actual hazard when someone widens it."""
        resources = _as_set(self._invoke_statement(template)["Resource"])
        for r in resources:
            assert r != "*", (
                "simulate-start may invoke EVERY function in the account. "
                "`/simulate/start` is gated by one Cognito group; this role must "
                "reach exactly one function."
            )
            assert not str(r).endswith(":*"), (
                f"resource {r!r} ends in ':*', granting invoke on every version and "
                "alias. This stack invokes $LATEST by name."
            )
            assert not str(r).endswith(":function:*"), (
                f"resource {r!r} wildcards the function name"
            )

    def test_effect_is_allow_and_carries_no_condition(self, template):
        """Named for what the body checks. An `Allow` with a `Condition` the reader
        has not seen is a different grant than the one this file claims to pin, so
        assert its absence rather than leaving the name to imply it."""
        stmt = self._invoke_statement(template)
        assert stmt["Effect"] == "Allow"
        assert "Condition" not in stmt, (
            f"invoke statement carries an unreviewed Condition: {stmt.get('Condition')!r}"
        )

    def test_list_vehicles_role_has_no_invoke_grant(self, template):
        """Least privilege between siblings: the picker reads the vehicles table and
        must not be able to start anything. The two roles are separate constructs
        precisely so a future change cannot widen both at once."""
        role = _role_logical_id_for(template, _LIST_FN_NAME)
        matches = _lambda_invoke_statements(_statements_for_role(template, role))
        assert matches == [], (
            "the simulate-list-vehicles role holds a lambda invoke grant: "
            f"{json.dumps(matches, indent=2)}"
        )


# ---------------------------------------------------------------------------
# Env threading — a grant with no function name to invoke is dead config
# ---------------------------------------------------------------------------


class TestSimulationFunctionNameThreaded:
    """`handler._required_env("SIMULATION_FUNCTION_NAME")` raises rather than
    guessing, so the grant is useless unless the stack sets the key.

    This is the defect class behind
    `issues/2026-09-13-cs-consumer-context-keys-never-threaded/`: a declared input
    with no compiler and no default owner, where the CDK side and the reader side
    live in files no single tool reads together.
    """

    def _env(self, template, function_name) -> dict:
        fn_id = _function_logical_id(template, function_name)
        env = template["Resources"][fn_id]["Properties"].get("Environment", {})
        return env.get("Variables", {})

    def test_start_function_has_simulation_function_name(self, template):
        env = self._env(template, _START_FN_NAME)
        assert "SIMULATION_FUNCTION_NAME" in env, (
            "SIMULATION_FUNCTION_NAME is absent from the simulate-start Lambda's "
            f"env. Keys present: {sorted(env)}. The handler raises on an unset "
            "value, so every start would 500."
        )

    def test_simulation_function_name_matches_the_granted_arn(self, template):
        """The env var and the IAM resource must name the SAME function. If they
        drift, IAM denies at runtime and the failure surfaces only on a live call —
        the two halves are in the same file but nothing else couples them."""
        env = self._env(template, _START_FN_NAME)
        assert _resolve(env["SIMULATION_FUNCTION_NAME"]) == _SIMULATION_FUNCTION_NAME
        assert _EXPECTED_INVOKE_ARN.endswith(
            f":function:{_resolve(env['SIMULATION_FUNCTION_NAME'])}"
        ), (
            "the granted ARN and SIMULATION_FUNCTION_NAME name different functions"
        )

    def test_start_function_has_deployment_stage(self, template):
        """`DEPLOYMENT_STAGE` is the SECOND key governing the dispatch — it is a
        component of `rule_name`, i.e. of the routing destination, and the handler
        now reads it fail-closed (`_dispatch_stage`). T6.1's first pass guarded only
        `SIMULATION_FUNCTION_NAME`; review cycle 1 (W1) found that a deploy losing
        this key would 500 every start with no synth guard catching it first.

        MUTATION: strip `DEPLOYMENT_STAGE` from this function's env → this fails.
        """
        env = self._env(template, _START_FN_NAME)
        assert "DEPLOYMENT_STAGE" in env, (
            "DEPLOYMENT_STAGE is absent from the simulate-start Lambda's env. Keys "
            f"present: {sorted(env)}. The dispatch reads it fail-closed, so every "
            "start would 500 — and were it to fall back instead, CS-product "
            "telemetry would silently route onto the CMS-native topic."
        )

    def test_deployment_stage_matches_the_stage_in_the_granted_arn(self, template):
        """The same coupling assertion made for the function name, on the stage.

        The callee validates `rule_name` against an allowlist built from its own
        stage, so a stage that disagrees with the deployed one is not an error — it
        is a silent reroute to the CMS-native rule.
        """
        env = self._env(template, _START_FN_NAME)
        assert _resolve(env["DEPLOYMENT_STAGE"]) == _STAGE
        assert f"cms-{_resolve(env['DEPLOYMENT_STAGE'])}-simulation-api" == (
            _SIMULATION_FUNCTION_NAME
        ), (
            "the Lambda's DEPLOYMENT_STAGE and the granted function's stage disagree"
        )

    def test_list_function_does_not_advertise_the_target(self, template):
        """The picker holds no invoke grant, so it must not carry the target's name
        either — config that reads as load-bearing while no consumer exists."""
        env = self._env(template, _LIST_FN_NAME)
        assert "SIMULATION_FUNCTION_NAME" not in env, (
            "the list-vehicles Lambda advertises SIMULATION_FUNCTION_NAME but holds "
            "no grant for it"
        )


# ---------------------------------------------------------------------------
# The route form — POST must exist on /simulate/start itself
# ---------------------------------------------------------------------------


class TestSimulateStartRouteForms:
    """`add_resource` without `add_method` yields a resource that answers 404 to an
    authed caller and 401 to an unauth one, so an unauthenticated probe reads as
    "route works". `/simulate/start` was in exactly that state.
    """

    def _resource_ids_by_path_part(self, template) -> dict:
        out: dict[str, list[str]] = {}
        for k, v in _resources_of_type(template, "AWS::ApiGateway::Resource").items():
            out.setdefault(v["Properties"]["PathPart"], []).append(k)
        return out

    def _methods_on(self, template, resource_logical_id: str) -> set:
        return {
            v["Properties"]["HttpMethod"]
            for v in _resources_of_type(template, "AWS::ApiGateway::Method").values()
            if v["Properties"].get("ResourceId", {}).get("Ref") == resource_logical_id
        }

    def _method_resources_on(self, template, resource_logical_id: str) -> list[dict]:
        return [
            v for v in _resources_of_type(template, "AWS::ApiGateway::Method").values()
            if v["Properties"].get("ResourceId", {}).get("Ref") == resource_logical_id
        ]

    def test_start_resource_exists(self, template):
        assert self._resource_ids_by_path_part(template).get("start"), (
            "no ApiGateway::Resource with PathPart 'start'"
        )

    def test_start_resource_has_a_post_method(self, template):
        """MUTATION: delete the `simulate_start_res.add_method("POST", ...)` line →
        this fails. `subscriptionsClient.ts` posts to `/simulate/start`."""
        ids = self._resource_ids_by_path_part(template)["start"]
        assert len(ids) == 1, f"expected one 'start' resource, got {ids}"
        methods = self._methods_on(template, ids[0])
        assert "POST" in methods, (
            f"/simulate/start has no POST method; methods present: {sorted(methods)}. "
            "An authed caller gets 404 and an unauth caller gets 401, so an "
            "unauthenticated probe reads as success."
        )

    def test_vid_resource_still_has_a_post_method(self, template):
        """The `{vid}` form is pre-existing deployed surface; T6.1 keeps it working
        rather than removing it.

        Anchored on ParentId == the `start` resource, not on PathPart alone. Today
        `{vid}` is the only one in this stack, but `{vin}` appears three times — so
        an unanchored `any()` over PathPart would be exact only by accident (S3).
        """
        start_ids = self._resource_ids_by_path_part(template)["start"]
        assert len(start_ids) == 1, f"expected one 'start' resource, got {start_ids}"
        start_id = start_ids[0]

        children = {
            k: v for k, v in
            _resources_of_type(template, "AWS::ApiGateway::Resource").items()
            if v["Properties"].get("ParentId", {}).get("Ref") == start_id
        }
        vid_ids = [k for k, v in children.items()
                   if v["Properties"]["PathPart"] == "{vid}"]
        assert len(vid_ids) == 1, (
            f"expected exactly one '{{vid}}' child of /simulate/start, got {vid_ids}"
        )
        methods = self._methods_on(template, vid_ids[0])
        assert "POST" in methods, (
            f"/simulate/start/{{vid}} lost its POST method; present: {sorted(methods)}"
        )

    def test_both_post_methods_require_the_cognito_authorizer(self, template):
        """The route's presence was pinned; its AUTHORIZATION was not.

        Security review cycle 1, Suggestion (a): dropping `**auth_kwargs` from
        either `add_method` call synth-passed the whole suite. Runtime
        `_require_operator` still refuses, so this is defense-in-depth rather than
        the only gate — but an unauthenticated request reaching the handler at all
        is a different exposure than one rejected at the edge, and T6.1 added a
        brand-new POST here.

        MUTATION: remove `**auth_kwargs` from either add_method → this fails.
        """
        by_part = self._resource_ids_by_path_part(template)
        start_id = by_part["start"][0]
        vid_ids = [
            k for k, v in
            _resources_of_type(template, "AWS::ApiGateway::Resource").items()
            if v["Properties"].get("ParentId", {}).get("Ref") == start_id
            and v["Properties"]["PathPart"] == "{vid}"
        ]
        assert len(vid_ids) == 1

        for label, rid in (("/simulate/start", start_id),
                           ("/simulate/start/{vid}", vid_ids[0])):
            posts = [m for m in self._method_resources_on(template, rid)
                     if m["Properties"]["HttpMethod"] == "POST"]
            assert len(posts) == 1, f"{label}: expected one POST, got {len(posts)}"
            props = posts[0]["Properties"]
            assert props.get("AuthorizationType") == "COGNITO_USER_POOLS", (
                f"{label} POST has AuthorizationType "
                f"{props.get('AuthorizationType')!r} — expected COGNITO_USER_POOLS. "
                "An unauthenticated caller would reach the Lambda."
            )
            assert props.get("AuthorizerId"), (
                f"{label} POST declares COGNITO_USER_POOLS with no AuthorizerId"
            )
