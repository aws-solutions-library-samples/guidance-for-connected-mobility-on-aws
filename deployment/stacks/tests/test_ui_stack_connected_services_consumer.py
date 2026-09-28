# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Synth guards for T2.3's prerequisite: the proxy's env + IAM surface on ui_stack.

Spec: `.kiro/specs/2026-09-10-cms-connected-services-consumer/` T2.3a (red phase).

WHAT THIS PINS

Three env vars on the main_api Lambda and exactly one new IAM statement on its
execution role. All four are runtime-only failures if wrong — the stack deploys
`UPDATE_COMPLETE` either way — which is the precise reason this file exists.
`deployment/stacks/test_dms_api_endpoint_env.py` records the precedent in the
repo's own words: an env var set under the wrong key shipped green through
forty-two tests "because the handler tests stub the client and the CDK tests
asserted CDK's own names."

So the key names here are **derived from the handler-side constants**
(`connected_services_proxy.ENV_*`) rather than restated as literals. A rename on
either side then fails this file instead of failing on the first live call. That
is the one place a tautology is the right trade: the constants ARE the contract,
and both sides import them.

The IAM assertions go against the **synthesized template**, never source text: a
grep proves a line exists, not that CloudFormation receives it. Assertions are
exact-value or exact-set, never `in`/`contains` — this spec's recurring defect is
a test named for a contract that asserts something adjacent to it and passes
regardless (five instances so far; see `decisions.md`).

WHAT THIS DELIBERATELY DOES NOT PIN

No DynamoDB statement for the feed-cache table. `lambda_role`'s existing
`AppAccess` policy already grants Get/Put/Update/Delete/Query/Scan on
`table/*` in this account+region, which covers that table; and no code reads or
writes the cache yet (`connected_services_proxy.py` has zero DynamoDB
references). A statement for an absent consumer is the "live grants with no
reachable consumer" shape, so `test_no_batchwriteitem_grant_without_a_consumer`
asserts its ABSENCE instead, and will need revisiting by whichever task
actually implements the cache write.

All identifiers are synthetic. The account is the AWS-documentation placeholder;
the real account id must never appear in this file, which is not
publish-excluded.

Run (from deployment/):
    .venv/bin/python -m pytest stacks/tests/test_ui_stack_connected_services_consumer.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent          # deployment/stacks/tests/
_STACKS = _HERE.parent                           # deployment/stacks/
_DEPLOYMENT = _STACKS.parent                     # deployment/
_REPO_ROOT = _DEPLOYMENT.parent

for _dir in (str(_DEPLOYMENT), str(_STACKS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

_MAIN_API = (
    _REPO_ROOT / "modules" / "cms_ui" / "source" / "handlers" / "main_api"
)
if str(_MAIN_API) not in sys.path:
    sys.path.insert(0, str(_MAIN_API))

_STAGE = "staging"
_ACCOUNT = "123456789012"   # AWS-documentation placeholder — never a real account
_REGION = "us-west-2"
_UI_STACK = f"cms-{_STAGE}-ui"

#: The producer's CFN export, restated independently of `ui_stack.py`.
#: `subscriptions_stack.py:786` publishes `SubscriptionsApiUrl` with
#: `export_name=f"{construct_id}-api-endpoint"`, and `app.py:327` builds that
#: construct_id as `f"{stack_prefix}-subscriptions"`.
_PRODUCER_EXPORT = f"cms-{_STAGE}-subscriptions-api-endpoint"

#: The subscriber credential's secret, as `scripts/provision-cms-subscriber.py:193`
#: names it. Restated here rather than imported so a rename on the provisioning
#: side cannot silently move the IAM grant with it.
_SECRET_NAME = (
    f"cms-{_STAGE}-connected-services-subscriber-{_REGION}-{_ACCOUNT}"
)

_SID = "ConnectedServicesSubscriberSecretRead"


# ── Handler-side constants: the contract both sides must agree on ────────────

def _proxy_env_constants() -> dict[str, str]:
    """Import the proxy module's env-var names.

    Imported, not restated. See the module docstring: these constants ARE the
    contract, so deriving from them is what lets a rename on either side fail
    here rather than at runtime.
    """
    import connected_services_proxy as proxy  # noqa: PLC0415

    return {
        "endpoint": proxy.ENV_PRODUCER_ENDPOINT,
        "subscription_id": proxy.ENV_CMS_SUBSCRIPTION_ID,
    }


# ── Synth helpers ───────────────────────────────────────────────────────────

def _resolve(node):
    """Collapse a synthesized ARN node to a plain string where possible.

    Mirrors `test_ui_stack_fleet_intelligence_adp_grants.py::_resolve`. CDK emits
    `Stack.format_arn`-built ARNs as `Fn::Join` with `{"Ref": "AWS::Partition"}`
    elements; resolving the partition to `aws` lets assertions compare against
    readable literals instead of template plumbing.
    """
    if isinstance(node, str):
        return node
    if isinstance(node, dict):
        if set(node) == {"Ref"} and node["Ref"] == "AWS::Partition":
            return "aws"
        if "Fn::Join" in node:
            sep, parts = node["Fn::Join"]
            return sep.join(_resolve(p) for p in parts)
        if "Fn::ImportValue" in node:
            return f"IMPORT:{_resolve(node['Fn::ImportValue'])}"
        return node
    if isinstance(node, list):
        return [_resolve(p) for p in node]
    return node


def _as_set(node) -> set:
    resolved = _resolve(node)
    if isinstance(resolved, str):
        return {resolved}
    return set(resolved)


def _synth(context: dict | None = None, env_overrides: dict | None = None) -> dict:
    """Synthesize `cms-staging-ui` and return its template as plain JSON.

    Not a module-scoped fixture: three of the tests below need DIFFERENT
    `DEPLOY_SUBSCRIPTIONS` / context inputs, and a cached template cannot
    express that. The cost is a few seconds per synth.
    """
    from aws_cdk import App, Environment

    from stacks.ui_stack import UIStack  # noqa: PLC0415

    saved = {}
    overrides = {"DEPLOYMENT_STAGE": _STAGE, "DRIVER_SELF_GUARD_ENABLED": "true"}
    overrides.update(env_overrides or {})
    for key, value in overrides.items():
        saved[key] = os.environ.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
    try:
        app = App(context=context or {})
        UIStack(app, _UI_STACK, env=Environment(account=_ACCOUNT, region=_REGION))
        raw = app.synth().get_stack_by_name(_UI_STACK).template
        return json.loads(json.dumps(raw))
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _main_api_env(template: dict) -> dict:
    """Return the main_api Lambda's Environment.Variables map.

    Selected by `Handler == 'index.handler'` plus the main_api-only
    `FLEET_ENROLLMENT_TABLE_NAME` key, rather than by logical id: CDK derives
    the logical id from the construct path with a hash suffix, so matching on it
    would break on any unrelated reordering.
    """
    candidates = []
    for logical_id, resource in template.get("Resources", {}).items():
        if resource.get("Type") != "AWS::Lambda::Function":
            continue
        props = resource.get("Properties", {})
        if props.get("Handler") != "index.handler":
            continue
        env = (props.get("Environment") or {}).get("Variables") or {}
        if "FLEET_ENROLLMENT_TABLE_NAME" in env:
            candidates.append((logical_id, env))
    assert len(candidates) == 1, (
        f"expected exactly one main_api Lambda in {_UI_STACK}, found "
        f"{[c[0] for c in candidates]} — the selector below is measuring the "
        "wrong resource"
    )
    return candidates[0][1]


def _role_statements(template: dict) -> list[dict]:
    """Every inline-policy statement on the main_api execution role.

    Covers both the `AWS::IAM::Role` inline `Policies` block (where `AppAccess`
    lives) and any `AWS::IAM::Policy` CDK attaches separately — a statement
    added via `role.add_to_policy()` lands in the latter, so a test that only
    read the former would silently miss it.
    """
    statements: list[dict] = []
    for resource in template.get("Resources", {}).values():
        rtype = resource.get("Type")
        props = resource.get("Properties", {})
        if rtype == "AWS::IAM::Role":
            for policy in props.get("Policies", []) or []:
                statements.extend(
                    (policy.get("PolicyDocument") or {}).get("Statement", []) or []
                )
        elif rtype == "AWS::IAM::Policy":
            statements.extend(
                (props.get("PolicyDocument") or {}).get("Statement", []) or []
            )
    return statements


# ── Premise guards ──────────────────────────────────────────────────────────

def test_premise_proxy_module_exposes_the_two_env_constants() -> None:
    """An absence/derivation test over unimportable constants passes vacuously."""
    constants = _proxy_env_constants()
    assert constants["endpoint"] == "CS_PRODUCER_API_ENDPOINT"
    assert constants["subscription_id"] == "CS_SUBSCRIPTION_ID"


def test_premise_proxy_reads_exactly_two_env_vars() -> None:
    """`ProxyConfig.from_env` must read exactly the two keys pinned below.

    If it grows a third, the ui_stack assertions become incomplete without
    failing — the route would 502 `config_missing` on a key nobody set.
    """
    import inspect

    import connected_services_proxy as proxy  # noqa: PLC0415

    src = inspect.getsource(proxy.ProxyConfig.from_env)
    referenced = {
        name
        for name in ("ENV_PRODUCER_ENDPOINT", "ENV_CMS_SUBSCRIPTION_ID")
        if name in src
    }
    assert referenced == {"ENV_PRODUCER_ENDPOINT", "ENV_CMS_SUBSCRIPTION_ID"}
    # `source[...]` subscripts, one per required key. A third would mean a third
    # required var.
    assert src.count("source[") == 2, (
        f"from_env reads {src.count('source[')} env vars; this file pins 2"
    )


# ── Env-var wiring ──────────────────────────────────────────────────────────

def test_producer_endpoint_reaches_the_lambda_from_the_cfn_export() -> None:
    """The endpoint is imported, not typed.

    `subscriptions_stack.py` already exports the deployed API URL, so importing
    it makes drift impossible; a literal would be a per-stage value baked into a
    file that ships (see `~/.kiro/steering/public-mirror-publish.md` on CVX's
    `vsa.ts`).
    """
    key = _proxy_env_constants()["endpoint"]
    env = _main_api_env(_synth(env_overrides={"DEPLOY_SUBSCRIPTIONS": "true"}))
    assert key in env, (
        f"{key} is not set on the main_api Lambda — every Connected Services "
        "route would 502 config_missing, which is indistinguishable from an "
        "intentionally unconfigured stage"
    )
    assert _resolve(env[key]) == f"IMPORT:{_PRODUCER_EXPORT}", (
        f"{key} must resolve from Fn::ImportValue {_PRODUCER_EXPORT!r}, got "
        f"{_resolve(env[key])!r}"
    )


def test_producer_endpoint_is_empty_when_the_producer_is_not_deployed() -> None:
    """The unconditional UI stack must not import an export that may not exist.

    This is the load-bearing test in this file. `cms-{stage}-ui` deploys on every
    stage; `cms-{stage}-subscriptions` is opt-in behind `DEPLOY_SUBSCRIPTIONS`
    (`app.py:323`). An UNGATED `Fn::ImportValue` would therefore (a) fail
    `cdk deploy cms-{stage}-ui` with "No export named ... found" on every stage
    where the producer is absent — including prod today and every clean-deploy
    run — and (b) permanently block the producer stack from being deleted or
    from changing that output, because CloudFormation refuses to break a live
    import.

    Bricking the highest-blast-radius stack in the repo to save one env var is
    not a trade worth making, so the import is gated on the same signal that
    decides whether the export exists at all. Empty is a supported
    configuration: the route fails closed at runtime with an explanatory 502,
    exactly as `DMS_API_ENDPOINT` does (`ui_stack.py:2211` and
    `test_dms_api_endpoint_env.py::test_empty_value_does_not_fail_the_synth`).
    """
    key = _proxy_env_constants()["endpoint"]
    env = _main_api_env(_synth(env_overrides={"DEPLOY_SUBSCRIPTIONS": None}))
    assert env.get(key) == "", (
        f"with DEPLOY_SUBSCRIPTIONS unset, {key} must synthesize to '' rather "
        f"than an Fn::ImportValue; got {env.get(key)!r}. An ungated import "
        "makes cms-{stage}-ui un-deployable wherever the producer is absent."
    )


def test_context_override_beats_the_export() -> None:
    """An operator must be able to point at a producer this stack cannot import.

    Same escape hatch `dmsApiEndpoint` provides — a producer in another account
    or region has no importable export, and requiring one would make that
    topology undeployable.
    """
    key = _proxy_env_constants()["endpoint"]
    env = _main_api_env(
        _synth(
            context={"csProducerApiEndpoint": "https://producer.example.test/prod"},
            env_overrides={"DEPLOY_SUBSCRIPTIONS": "true"},
        )
    )
    assert env[key] == "https://producer.example.test/prod"


def test_subscription_id_reaches_the_lambda_and_is_not_baked() -> None:
    key = _proxy_env_constants()["subscription_id"]
    env = _main_api_env(
        _synth(context={"csSubscriptionId": "01ARZ3NDEKTSV4RRFFQ69G5FAV"})
    )
    assert env[key] == "01ARZ3NDEKTSV4RRFFQ69G5FAV"

    # Absent context AND absent shell var -> '' (supported, fails closed at
    # runtime), never a hardcoded id.
    env_bare = _main_api_env(_synth(env_overrides={"CS_SUBSCRIPTION_ID": None}))
    assert env_bare.get(key) == "", (
        f"{key} must default to '' rather than to a literal subscription id — "
        "CMS's staging subscription id is a per-environment value and does not "
        "belong in a file that ships"
    )


def test_subscriber_secret_name_reaches_the_lambda() -> None:
    """The reader gets the same string the IAM grant is built from.

    The handler cannot derive this name: it needs the ACCOUNT id, which is not
    available to a Lambda without an STS call. Passing it explicitly is also
    what makes the grant and the reader un-driftable — both are built from one
    expression in one file, which is the property
    `test_dms_api_endpoint_env.py` exists to protect.
    """
    env = _main_api_env(_synth())
    assert env.get("CS_SUBSCRIBER_SECRET_NAME") == _SECRET_NAME, (
        "CS_SUBSCRIBER_SECRET_NAME must be the provisioning script's secret "
        f"name; got {env.get('CS_SUBSCRIBER_SECRET_NAME')!r}"
    )


def test_no_literal_producer_url_or_subscription_id_in_ui_stack_source() -> None:
    """No per-stage value baked into a file that ships."""
    src = (_STACKS / "ui_stack.py").read_text(encoding="utf-8")
    key = _proxy_env_constants()["endpoint"]
    idx = src.index(f"'{key}'")
    # Window covers the assignment and its comment block.
    block = src[max(0, idx - 3000): idx + 500]
    assert "execute-api" not in block, (
        "a literal API Gateway URL is baked into ui_stack.py"
    )
    # CMS's real staging subscription id (a ULID) must not appear anywhere.
    assert "01M2BG77STPWC5N9953BKVENS9" not in src


def test_does_not_set_the_cs_portal_specs_variable() -> None:
    """`CONNECTED_SERVICES_API_ENDPOINT` belongs to another spec.

    `deployment/config/staging.env:207` sets it to the placeholder
    `https://api.example.invalid` for `2026-09-03-cms-connected-services-portal`.
    Two specs sharing one variable is how one team's deploy silently changes
    another team's runtime. The handler-side twin of this assertion
    AST-parses the proxy module; this is the CDK-side half.
    """
    src = (_STACKS / "ui_stack.py").read_text(encoding="utf-8")
    assert "'CONNECTED_SERVICES_API_ENDPOINT'" not in src
    assert '"CONNECTED_SERVICES_API_ENDPOINT"' not in src


# ── IAM ─────────────────────────────────────────────────────────────────────

def test_secret_read_statement_is_present_and_exactly_scoped() -> None:
    """One action, one resource, and the resource carries the suffix wildcard.

    The `-??????` suffix is REQUIRED, not defensive: Secrets Manager appends a
    6-character random suffix at creation, IAM ARN matching has no implicit
    trailing wildcard, and so granting the suffix-less ARN authorises NOTHING —
    `GetSecretValue` returns AccessDenied against the real secret. `ui_stack.py`
    records the same finding at its other secret grant, where it cost a prod
    deploy.
    """
    statements = _role_statements(_synth())
    matching = [s for s in statements if s.get("Sid") == _SID]
    assert len(matching) == 1, (
        f"expected exactly one statement with Sid {_SID!r}, found {len(matching)}"
    )
    statement = matching[0]

    assert _as_set(statement["Action"]) == {"secretsmanager:GetSecretValue"}, (
        "the subscriber-secret statement must grant exactly GetSecretValue — "
        f"got {_as_set(statement['Action'])}"
    )
    assert statement.get("Effect") == "Allow"

    resources = {_resolve(r) for r in _as_set(statement["Resource"])}
    expected = {
        f"arn:aws:secretsmanager:{_REGION}:{_ACCOUNT}:secret:{_SECRET_NAME}-??????"
    }
    assert resources == expected, (
        f"expected exactly {expected}, got {resources}"
    )


def test_secret_grant_is_not_widened_to_a_prefix_wildcard() -> None:
    """`secret:cms-staging-*` would read every CMS secret on the stage.

    Asserted separately from the exact-set test above because the exact-set test
    would also fail for a harmless reordering, and this property is worth its
    own name: the failure it guards is a privilege widening, not a formatting
    change.
    """
    for statement in _role_statements(_synth()):
        if statement.get("Sid") != _SID:
            continue
        for resource in _as_set(statement["Resource"]):
            resolved = _resolve(resource)
            assert resolved.endswith("-??????"), (
                f"subscriber-secret resource {resolved!r} does not end in the "
                "6-character suffix wildcard"
            )
            assert "*" not in resolved.replace("-??????", ""), (
                f"subscriber-secret resource {resolved!r} contains a prefix "
                "wildcard — that would grant read on unrelated CMS secrets"
            )


def test_no_batchwriteitem_grant_without_a_consumer() -> None:
    """The cache landed in T2.6 and STILL must not need `BatchWriteItem`.

    **Updated by T2.6, deliberately not deleted.** The original rationale —
    "`connected_services_proxy.py` has zero DynamoDB references" — is now false:
    `DynamoFeedCache` reads and writes the feed cache, and
    `get_subscription_feed` serves from it. What has not changed is the
    conclusion, and the reason is worth keeping: the cache writes ONE row with a
    single `PutItem`, which the existing `AppAccess` statement already covers
    alongside Get/Query on `table/*`. Batching was the only thing that would have
    needed a new action, and T2.6's task text says to prefer a single `PutItem`
    and add no grant at all.

    So this assertion outlives the condition that motivated it, with a stronger
    meaning than before: not "no consumer exists yet" but "the consumer that
    exists is deliberately shaped to need no additional privilege". A
    `BatchWriteItem` grant appearing now would mean someone switched the cache to
    a batched write — a design change that should be visible, not incidental.

    Deleting it once the cache landed would have been the tempting move and the
    wrong one: the grant's absence is the enforcement of the design decision.
    """
    for statement in _role_statements(_synth()):
        actions = _as_set(statement.get("Action", []))
        assert "dynamodb:BatchWriteItem" not in actions, (
            "a BatchWriteItem grant appeared. T2.6's feed cache writes a single "
            "row with PutItem, covered by the existing AppAccess grant, so this "
            "action should still be unnecessary. If the cache moved to a batched "
            "write, say so here and justify the new privilege."
        )


@pytest.mark.parametrize("forbidden", ["cognito-idp:AdminInitiateAuth"])
def test_no_admin_auth_grant(forbidden: str) -> None:
    """The token path uses `InitiateAuth`, which needs no IAM at all.

    `connected_services_proxy.COGNITO_AUTH_FLOW` records why the admin flow was
    rejected: it is not enabled on `CMSUserPoolClient`, and enabling it would
    mean editing the app client ~9 prod Federate admins authenticate through.
    An `AdminInitiateAuth` grant appearing here is the signal that someone
    reversed that decision in the Lambda without reversing it in the docstring.
    """
    for statement in _role_statements(_synth()):
        assert forbidden not in _as_set(statement.get("Action", [])), (
            f"{forbidden} was granted to the main_api role; the Connected "
            "Services token path deliberately uses the non-admin InitiateAuth "
            "flow, which requires no IAM permission"
        )



# ── T2.6: the feed cache's table name ──────────────────────────────────────

def test_feed_cache_table_name_reaches_the_lambda() -> None:
    """`CS_FEED_CACHE_TABLE_NAME` must be set, and must be the derived name.

    Unlike `CS_PRODUCER_API_ENDPOINT`, empty is NOT a supported value here: the
    name is always computable from stage/region/account. Whether the TABLE exists
    is a different question, and the cache answers it as a permanent miss rather
    than an error — so pointing at a not-yet-created table is safe.
    """
    import connected_services_proxy as proxy  # noqa: PLC0415

    env = _main_api_env(_synth())
    value = env.get(proxy.ENV_FEED_CACHE_TABLE)
    assert value, f"{proxy.ENV_FEED_CACHE_TABLE} is not set on the main_api Lambda"
    resolved = _resolve(value)
    assert resolved == (
        f"cms-{_STAGE}-storage-cs-feed-cache-{_REGION}-{_ACCOUNT}"
    ), resolved


def test_feed_cache_table_name_matches_the_consumer_stacks_expression() -> None:
    """The two derivations of one table name must agree — SYNTHESISED, not grepped.

    `ui_stack.py` derives the name for the READER; `connected_services_consumer_
    stack.py` derives it for the `dynamodb.Table` that CREATES it. Two f-strings
    in two files, and **a mismatch is not a synth error**: it is a `GetItem`
    against a nonexistent table, which `DynamoFeedCache` swallows by design. The
    only symptom would be a cache that never hits and a feed that is merely slow
    — a silent, permanent degradation, which is what this spec keeps producing
    when two sites must agree and nothing checks that they do (the subscriber
    secret name has three such sites).

    This synthesises BOTH stacks and compares the resolved strings. An earlier
    draft compared the literal f-string prefixes instead, reasoning that the
    consumer stack is opt-in behind `DEPLOY_CONNECTED_SERVICES_CONSUMER` so a
    synth-based check "would silently pass by not running". That reasoning was
    wrong on the facts: the stack class can be instantiated directly in a test
    regardless of the deploy flag, exactly as `_synth()` already does for
    `UIStack`. Comparing prefixes cannot see a divergence in the interpolated
    parts — which is the whole region where region/account suffixes live, i.e.
    precisely where a cross-region-namespace mistake would appear.
    """
    from aws_cdk import App, Environment  # noqa: PLC0415

    from stacks.connected_services_consumer_stack import (  # noqa: PLC0415
        ConnectedServicesConsumerStack,
    )

    import connected_services_proxy as proxy  # noqa: PLC0415

    reader = _resolve(_main_api_env(_synth()).get(proxy.ENV_FEED_CACHE_TABLE))

    saved = os.environ.get("DEPLOYMENT_STAGE")
    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    try:
        app = App()
        ConnectedServicesConsumerStack(
            app,
            f"cms-{_STAGE}-connected-services-consumer",
            env=Environment(account=_ACCOUNT, region=_REGION),
        )
        template = json.loads(
            json.dumps(
                app.synth()
                .get_stack_by_name(f"cms-{_STAGE}-connected-services-consumer")
                .template
            )
        )
    finally:
        if saved is None:
            os.environ.pop("DEPLOYMENT_STAGE", None)
        else:
            os.environ["DEPLOYMENT_STAGE"] = saved

    created = [
        resource["Properties"].get("TableName")
        for resource in template.get("Resources", {}).values()
        if resource.get("Type") == "AWS::DynamoDB::Table"
    ]
    assert created, "the consumer stack synthesised no DynamoDB table"
    assert reader in created, (
        f"ui_stack.py tells the Lambda to read {reader!r}, but the consumer "
        f"stack creates {created!r}. A mismatch is NOT a synth error — it is a "
        "GetItem against a table that does not exist, which the cache swallows, "
        "leaving a cache that never hits and a feed that is merely slow."
    )

    # Cross-region-namespace discipline: the name must carry BOTH suffixes, or it
    # collides across regions in one account and silently reuses another region's
    # cache rows.
    assert reader.endswith(f"{_REGION}-{_ACCOUNT}"), reader


def test_feed_cache_ttl_reaches_the_lambda_as_a_positive_integer() -> None:
    """A cache with an unparseable TTL is a cache that silently does nothing.

    The handler falls back to its own default on a malformed value (so this can
    never be why the feed breaks), which is exactly why the deployed value needs
    asserting here — the fallback would hide a typo forever.
    """
    import connected_services_proxy as proxy  # noqa: PLC0415

    env = _main_api_env(_synth())
    raw = env.get(proxy.ENV_FEED_CACHE_TTL_SECONDS)
    assert raw is not None, f"{proxy.ENV_FEED_CACHE_TTL_SECONDS} is not set"
    assert int(_resolve(raw)) > 0, (
        f"{proxy.ENV_FEED_CACHE_TTL_SECONDS}={raw!r} — a non-positive window "
        "disables the cache, which is a valid choice but should not be the "
        "default a deploy silently lands on"
    )
