"""Synth-based guard for the Connected Services distribution's WAF attachment.

Spec: .kiro/specs/2026-09-05-cms-connected-services-auth-integration/ H1.3, H4.1 (§ D6)

The Connected Services distribution had no WebACL while the primary CMS UI distribution
carries `cms-<stage>-ui-waf`. Closing that divergence is § D6.

Distribution IDs are deliberately not written in this file: it ships to a public mirror
and the publish scanner flags them (severity warning). The live IDs are in the spec and
the issue, both publish-excluded.

**§ D6, restated here because this file is where the temptation lands**: a WAF does
not authenticate. This is hardening, it is NOT the remediation for Talos
`69d7f6e6` (`UnauthWebService`), and its presence must not be read as grounds to
re-enable the distribution. The remediation is the app-layer auth; the re-enable is
gated on § D7.

Assertions are against the synthesized CloudFormation template, never against source
text: a `grep` of the stack file proves a line exists, not that CloudFormation
receives it. The 2026-08-10 CMS precedent is the reason — a "prod has a WAF attached"
check went green while the endpoint it named stayed unprotected, because the check
matched the wrong thing (see `ui_stack.py:740-753`).

All values below are synthetic. Per this suite's established convention the pool ID
keeps fewer than 8 characters after the `_` so it cannot trip the publish scanner's
`cognito_user_pool_id` rule, hostnames use the RFC 2606 `.invalid` TLD, and the
account ID is the AWS-documentation placeholder.

Run with (from deployment/):
    .venv/bin/python -m pytest stacks/tests/test_connected_services_waf.py -v
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — mirrors test_connected_services_ui_stack.py exactly
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
_STACK_NAME = f"cms-{_STAGE}-connected-services-ui"

# The context key ui_stack.py already reads (`ui_stack.py:1787`). Reusing the same
# key rather than inventing a CS-specific one is deliberate — H4.1 requires matching
# ui_stack.py's behaviour, and one key means one `-c` flag covers both stacks.
_WAF_CONTEXT_KEY = "wafWebAclArn"
_WAF_ARN = (
    f"arn:aws:wafv2:us-east-1:{_ACCOUNT}:global/webacl/"
    "cms-staging-ui-waf/00000000-0000-0000-0000-000000000000"
)

_BASE_CONTEXT: dict[str, str] = {
    "connectedServicesUiCognitoUserPoolId": "us-west-2_EXAMPL",
    "connectedServicesUiCognitoClientId": "testclientid00000001",
    "connectedServicesUiCognitoDomain": "placeholder.example.invalid",
    "connectedServicesUiApiEndpoint": "https://api.example.invalid",
    "connectedServicesUiCallbackOrigin": "https://example.invalid",
}


def _synth(context: dict[str, str]) -> dict:
    """Synthesise the CS UI stack with the given context and return its template."""
    from aws_cdk import App, Environment

    from stacks.connected_services_ui_stack import ConnectedServicesUiStack  # noqa: PLC0415

    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    app = App(context={**_BASE_CONTEXT, **context})
    ConnectedServicesUiStack(
        app,
        _STACK_NAME,
        stage=_STAGE,
        env=Environment(account=_ACCOUNT, region=_REGION),
    )
    raw = app.synth().get_stack_by_name(_STACK_NAME).template
    return json.loads(json.dumps(raw))


def _distribution_config(template: dict) -> dict:
    """Return the single CloudFront DistributionConfig from a synthesized template."""
    distributions = [
        r["Properties"]["DistributionConfig"]
        for r in template["Resources"].values()
        if r["Type"] == "AWS::CloudFront::Distribution"
    ]
    assert len(distributions) == 1, (
        f"expected exactly one CloudFront distribution, found {len(distributions)}"
    )
    return distributions[0]


# ---------------------------------------------------------------------------
# (a) With the context key set, the WebACL reaches the template
# ---------------------------------------------------------------------------


class TestWafAttachedWhenContextSet:
    def test_web_acl_id_is_present_and_non_empty(self) -> None:
        """The attach path — the one that is red until H4.1 lands."""
        config = _distribution_config(_synth({_WAF_CONTEXT_KEY: _WAF_ARN}))
        web_acl = config.get("WebACLId")
        assert web_acl, (
            "DistributionConfig has no non-empty WebACLId. The Connected Services "
            "distribution would deploy unprotected while primary CMS's carries "
            f"cms-{_STAGE}-ui-waf. Context key passed: {_WAF_CONTEXT_KEY}."
        )

    def test_web_acl_id_is_the_arn_that_was_passed(self) -> None:
        """CloudFront's `web_acl_id` prop takes the WAFv2 *ARN*, not an id.

        The prop name is historical — it accepted a WAF Classic id. Passing an id
        where an ARN is required is accepted at synth and fails at deploy.
        """
        config = _distribution_config(_synth({_WAF_CONTEXT_KEY: _WAF_ARN}))
        assert config.get("WebACLId") == _WAF_ARN

    def test_web_acl_arn_is_a_wafv2_global_arn(self) -> None:
        """A REGIONAL WebACL cannot be attached to CloudFront.

        CloudFront requires scope=CLOUDFRONT, which is expressed as a us-east-1
        `global/webacl/...` ARN. This asserts the fixture and the plumbing agree on
        that shape, so a regional ARN cannot be threaded through unnoticed.
        """
        config = _distribution_config(_synth({_WAF_CONTEXT_KEY: _WAF_ARN}))
        web_acl = config.get("WebACLId", "")
        assert ":global/webacl/" in web_acl, (
            f"WebACLId is not a CloudFront-scoped (global) WAFv2 ARN: {web_acl!r}"
        )
        assert ":wafv2:us-east-1:" in web_acl, (
            f"CloudFront WebACLs must live in us-east-1: {web_acl!r}"
        )


# ---------------------------------------------------------------------------
# (b) Absent context key — synth still succeeds, no WAF. Mirrors ui_stack.py.
# ---------------------------------------------------------------------------


class TestNoWafWhenContextAbsent:
    def test_synth_succeeds_without_the_context_key(self) -> None:
        """Absent WAF must not be a synth error.

        `ui_stack.py:1787-1790` treats the key as optional and prints a notice. H4.1
        requires matching that behaviour rather than inventing a stricter one, so a
        developer synthing without the ARN is not blocked.
        """
        config = _distribution_config(_synth({}))
        assert config.get("WebACLId") in (None, ""), (
            "A WebACLId appeared with no context key set — the ARN is being sourced "
            "from somewhere other than context, which makes the attachment "
            "untraceable at the call site."
        )

    def test_empty_string_context_is_treated_as_absent(self) -> None:
        """`-c wafWebAclArn=` must mean "no WAF", not an empty WebACLId.

        A CFN token is always truthy and never empty, which is the token-truthiness
        trap recorded in active-projects.md; this stack's convention is explicit
        `bool(x.strip())` guards. An empty `WebACLId` in a template is rejected by
        CloudFormation at deploy, so the distinction is load-bearing.
        """
        config = _distribution_config(_synth({_WAF_CONTEXT_KEY: "   "}))
        assert config.get("WebACLId") in (None, "")


# ---------------------------------------------------------------------------
# (c) Anti-vacuity — prove these assertions can fail
# ---------------------------------------------------------------------------


class TestGuardCanFail:
    def test_the_helper_would_notice_a_missing_web_acl(self) -> None:
        """The attach assertion is not passing because it cannot see the field.

        `_distribution_config` is the shared accessor for both the positive and
        negative cases above. If it silently returned `{}` — wrong resource type,
        renamed key, changed template shape — the negative case would pass and the
        positive case's failure would be indistinguishable from a real defect. This
        pins the accessor against a template it is known to be able to read.
        """
        config = _distribution_config(_synth({_WAF_CONTEXT_KEY: _WAF_ARN}))
        # Fields unrelated to WAF that must be present for the accessor to be sane.
        assert config.get("DefaultRootObject") == "index.html", (
            "_distribution_config did not return a recognisable DistributionConfig; "
            "every WAF assertion in this file is therefore untrustworthy."
        )
        assert "DefaultCacheBehavior" in config


# ---------------------------------------------------------------------------
# (d) § D7 — the takedown must be declared state, not drift
# ---------------------------------------------------------------------------


class TestDistributionDefaultsToDisabled:
    """Found 2026-09-05: the deployed template said `Enabled: true` while the live
    distribution was `false`.

    The 2026-09-04 Talos takedown was applied out-of-band with the CLI, so it was drift
    rather than IaC state, and the next `cdk deploy` of this stack would have silently
    re-enabled a surface taken down for a live Critical — with no operator decision in
    the path, and in direct contradiction of the deploy task's own claim that the
    distribution stays disabled.

    These assertions make the takedown declarative and keep § D7's gate on the only
    path that can lift it.
    """

    def test_default_is_disabled(self) -> None:
        config = _distribution_config(_synth({_WAF_CONTEXT_KEY: _WAF_ARN}))
        assert config.get("Enabled") is False, (
            "The distribution synthesises as ENABLED by default. Deploying would revert "
            "the Talos takedown without an operator decision. Default must be disabled; "
            "re-enable via -c connectedServicesUiEnabled=true."
        )

    def test_explicit_true_enables(self) -> None:
        """The one path that may enable it — explicit, reviewable, greppable."""
        config = _distribution_config(
            _synth({_WAF_CONTEXT_KEY: _WAF_ARN, "connectedServicesUiEnabled": "true"})
        )
        assert config.get("Enabled") is True

    @pytest.mark.parametrize("value", ["", "  ", "false", "False", "1", "yes", "TRUE ", "tru"])
    def test_anything_other_than_true_fails_closed(self, value: str) -> None:
        """A typo or a truthy-looking value must not enable a taken-down surface.

        `"TRUE "` and `"true"` differ only in case and whitespace, both of which are
        normalised, so `"TRUE "` enables — it is excluded from this list. Everything
        here is a value someone might plausibly pass expecting it to work.
        """
        if value.strip().lower() == "true":
            pytest.skip("normalised to the enabling value by design")
        config = _distribution_config(
            _synth({_WAF_CONTEXT_KEY: _WAF_ARN, "connectedServicesUiEnabled": value})
        )
        assert config.get("Enabled") is False, (
            f"context value {value!r} enabled the distribution. Only the exact string "
            '"true" (case- and whitespace-normalised) may enable it.'
        )

    def test_waf_presence_does_not_imply_enabled(self) -> None:
        """§ D6 — attaching a security control is not grounds to re-enable.

        Executable rather than commented, because "a security control is attached" is
        precisely the argument that turns a partial mitigation into a reopened finding.
        """
        config = _distribution_config(_synth({_WAF_CONTEXT_KEY: _WAF_ARN}))
        assert config.get("WebACLId") == _WAF_ARN
        assert config.get("Enabled") is False, (
            "WAF presence is coupled to the enabled state. These must stay independent: "
            "a WAF does not authenticate, and § D7 owns the re-enable."
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
