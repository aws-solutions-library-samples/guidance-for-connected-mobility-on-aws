"""Guard: the user-pool app client must not let clients write custom attributes.

Verified empirically against live staging Cognito on 2026-08-10: with `WriteAttributes`
unset, a self-registered user's own `SignUp` call could set
`custom:driverId=INJECTED-DRIVER-1` and the value PERSISTED on the resulting user.

That is a privilege-escalation path, not a tidiness issue: `custom:driverId` is an
authorization input — `main_api`'s `_classify_driver_self` grants driver-scoped access on
the strength of it — so a stranger registering through Phase B could obtain driver-self
access with no group at all, bypassing `fleet-guest` and every group-based control.

The AWS docs do not settle this: `WriteAttributes` is documented in terms of ACCESS-TOKEN
writes, and the unset default is documented as "the Standard attributes". Neither statement
covers `SignUp`, which happens before authentication. Hence the live probe, and hence this
test — the property is a synth-output fact, so it is asserted against the template.

Two directions are asserted, because getting this wrong either way is a real failure:
  * no custom attribute is client-writable  (the security property)
  * `email` and `name` REMAIN writable      (the availability property — the AmazonFederate
    IdP maps both, and a client lacking write access to a mapped attribute makes Cognito
    throw on IdP sign-in, which would lock out all 9 prod platform-admins)
"""
from __future__ import annotations

import unittest

import json
import os
import subprocess
import tempfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEPLOYMENT = os.path.abspath(os.path.join(_HERE, os.pardir))

# Every custom attribute defined on the pool. None may be client-writable.
_CUSTOM_ATTRS = [
    "custom:fleetIds",
    "custom:tenantId",
    "custom:driverId",
    "custom:role",
    "custom:vehicleId",
    "custom:provisionedVia",
    # AVX standing identity contract § 5.1 (2): customerId must not be
    # client-writable. A standing claim written by the caller cannot be
    # trusted for authorization; only admin-written values are trusted.
    "custom:customerId",
]

# IdP-mapped attributes that MUST stay writable or Federate sign-in breaks.
_REQUIRED_WRITABLE = ["email", "name"]


def _synth_template() -> dict:
    """Synthesise cms-staging-ui and return its template as a dict.

    Uses the canonical invocation (stage env + domain guard context); the abbreviated
    `cdk synth` form does not run. See tasks.md § "Canonical cdk synth invocation".
    """
    env = dict(os.environ)
    cfg = os.path.join(_DEPLOYMENT, "config", "staging.env")
    if os.path.isfile(cfg):
        with open(cfg) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.replace("_", "").isalnum():
                    env[k] = v
    # Synth into a PRIVATE output dir. The shared `cdk.out` is locked by whichever CLI
    # is synthing, so sharing it made this test SKIP whenever a concurrent session (or an
    # earlier command in the same session) held the lock — observed 2026-08-11:
    # "Another CLI (PID=...) is currently synthing to cdk.out". A security test that
    # silently skips under contention is a test that is absent exactly when it is needed.
    outdir = tempfile.mkdtemp(prefix="cdkout-writeattrs-")
    args = [
        "npx", "cdk", "synth", "cms-staging-ui", "--json", "--output", outdir,
        "-c", f"uiCustomDomain={env.get('UI_CUSTOM_DOMAIN','')}",
        "-c", f"uiCustomDomainCertArn={env.get('UI_CUSTOM_DOMAIN_CERT_ARN','')}",
        "-c", f"uiCustomDomainRegion={env.get('UI_CUSTOM_DOMAIN_REGION','')}",
        "-c", "uiCustomDomainManageDns=false",
        "-c", "cms.allow_unauth_map_auth=true",
        "-c", "cms.allow_self_signup=true",
    ]
    proc = subprocess.run(
        args, cwd=_DEPLOYMENT, env=env, capture_output=True, text=True, timeout=600
    )
    if proc.returncode != 0:
        pytest.skip(f"cdk synth unavailable in this environment: {proc.stderr[-400:]}")
    # `--json` still emits warnings on stderr; stdout is the template.
    start = proc.stdout.find("{")
    return json.loads(proc.stdout[start:])


@pytest.fixture(scope="module")
def client_props() -> dict:
    t = _synth_template()
    clients = [
        r["Properties"]
        for r in t["Resources"].values()
        if r["Type"] == "AWS::Cognito::UserPoolClient"
    ]
    assert clients, "no AWS::Cognito::UserPoolClient in the synthesised template"
    assert len(clients) == 1, f"expected exactly one app client, found {len(clients)}"
    return clients[0]


def test_write_attributes_is_explicitly_set(client_props):
    """An UNSET WriteAttributes is the vulnerable state — it must be specified."""
    assert "WriteAttributes" in client_props, (
        "WriteAttributes is unset, which permits a self-registered user to set custom "
        "attributes in their own SignUp call (verified live 2026-08-10)."
    )


@pytest.mark.parametrize("attr", _CUSTOM_ATTRS)
def test_no_custom_attribute_is_client_writable(client_props, attr):
    """The security property, asserted per attribute so a failure names the culprit."""
    writable = client_props.get("WriteAttributes", [])
    assert attr not in writable, (
        f"{attr!r} is client-writable. If it feeds an authorization decision "
        f"(custom:driverId does, via _classify_driver_self) this is a "
        f"privilege-escalation path at signup. Writable set: {writable}"
    )


@pytest.mark.parametrize("attr", _REQUIRED_WRITABLE)
def test_idp_mapped_attributes_remain_writable(client_props, attr):
    """The availability property — omitting these breaks Federate sign-in.

    The AmazonFederate IdP maps email -> EMAIL and name -> GIVEN_NAME. Per the AWS docs,
    a client without write access to a mapped attribute causes Cognito to throw when it
    updates that attribute on IdP sign-in, which would lock out all 9 prod
    platform-admins. Over-restricting is as much a failure as under-restricting.
    """
    writable = client_props.get("WriteAttributes", [])
    assert attr in writable, (
        f"{attr!r} is IdP-mapped but not client-writable — Federate sign-in would "
        f"throw. Writable set: {writable}"
    )


def test_writable_set_is_minimal(client_props):
    """Nothing beyond the IdP-mapped minimum, so additions are deliberate."""
    writable = sorted(client_props.get("WriteAttributes", []))
    assert writable == sorted(_REQUIRED_WRITABLE), (
        f"the writable set drifted from the intended minimum {sorted(_REQUIRED_WRITABLE)}: "
        f"{writable}. Adding an attribute here is a security decision — re-check whether "
        f"it feeds an authorization path."
    )


class TestProvisioningIamLeastPrivilege(unittest.TestCase):
    """The provisioning trigger's IAM must grant exactly what the handler calls.

    AdminListGroupsForUser was granted until 2026-08-11 and never called — the handler
    relies on AdminAddUserToGroup being idempotent instead. This test pins the action set
    to the handler's actual call sites so a stale grant cannot creep back in.
    """

    def test_action_set_matches_handler_call_sites(self) -> None:
        import re
        from pathlib import Path

        handler = Path(__file__).parent.parent / (
            "lambdas/cognito_triggers/provisioning/handler.py"
        )
        src = handler.read_text()
        # Strip comments/docstrings so a mention in prose is not mistaken for a call.
        code = re.sub(r'"""[\s\S]*?"""', "", src)
        code = "\n".join(l.split("#", 1)[0] for l in code.splitlines())
        called = {m for m in re.findall(r"admin_[a-z_]+(?=\()", code)}
        self.assertEqual(
            called,
            {"admin_add_user_to_group", "admin_update_user_attributes"},
            "handler call sites changed — update the IAM grant in ui_stack.py to match",
        )
        self.assertNotIn(
            "admin_list_groups_for_user", called,
            "AdminListGroupsForUser is not needed: AdminAddUserToGroup is idempotent",
        )

        stack_src = (Path(__file__).parent / "ui_stack.py").read_text()
        i = stack_src.index("cognito-idp:AdminAddUserToGroup")
        window = stack_src[i - 200 : i + 400]
        self.assertNotIn(
            "cognito-idp:AdminListGroupsForUser", window,
            "unused AdminListGroupsForUser grant reintroduced on the provisioning policy",
        )
