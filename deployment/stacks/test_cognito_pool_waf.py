"""Tests for CognitoPoolWaf (deployment/stacks/cognito_pool_waf.py).

The construct exists because the CloudFront web ACL's signup rate rule is on a surface
signup never reaches (`decisions.md` 2026-08-10 § "WAF is deployed and attached, but the
signup rate-limit rule is on the wrong surface"). So the assertions that matter most here
are the ones that would have caught THAT defect and its two service-level traps:

  * scope is REGIONAL and the association targets the pool ARN — a CLOUDFRONT-scoped ACL
    cannot be associated with a regional resource, and an ACL with no association protects
    nothing while looking deployed;
  * the rate rule's scope-down matches both routes a registration can arrive by — the
    managed-login paths AND the user-pool API operation headers. Matching only one is the
    same class of miss as matching the wrong distribution;
  * `AWSManagedRulesATPRuleSet` is ABSENT — AWS refuses to associate a web ACL that uses
    it with a Cognito user pool, so its presence would break the association at deploy;
  * no rule uses the CAPTCHA action — per the AWS docs a CAPTCHA in a pool-associated web
    ACL causes an unrecoverable error in managed-login TOTP registration.

The last two are absences, which cannot be proven by exercising the happy path; they are
asserted directly against the synthesised rule list.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/test_cognito_pool_waf.py -v
"""
from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from stacks.cognito_pool_waf import CognitoPoolWaf

# Placeholder pool id in angle brackets, not a realistic region-prefixed shape: this file
# ships publicly and a realistic shape is a critical publish-scanner finding.
_POOL_ARN = "arn:aws:cognito-idp:us-west-2:111111111111:userpool/<user-pool-id>"


def _synth(stage: str) -> Template:
    app = cdk.App()
    stack = cdk.Stack(app, f"cms-{stage}-ui")
    CognitoPoolWaf(stack, "PoolWaf", stage=stage, user_pool_arn=_POOL_ARN)
    return Template.from_stack(stack)


@pytest.fixture(scope="module")
def staging_template() -> Template:
    return _synth("staging")


@pytest.fixture(scope="module")
def prod_template() -> Template:
    return _synth("prod")


def _web_acl(template: Template) -> dict:
    acls = template.find_resources("AWS::WAFv2::WebACL")
    assert len(acls) == 1, f"expected exactly one web ACL, found {len(acls)}"
    return next(iter(acls.values()))["Properties"]


def _rules(template: Template) -> dict[str, dict]:
    return {r["Name"]: r for r in _web_acl(template)["Rules"]}


# ---------------------------------------------------------------------------
# Scope + association — the defect this construct exists to fix
# ---------------------------------------------------------------------------

class TestScopeAndAssociation:
    @pytest.mark.parametrize("stage", ["staging", "prod"])
    def test_scope_is_regional(self, stage: str) -> None:
        """REGIONAL, not CLOUDFRONT. A CLOUDFRONT ACL cannot bind to a pool."""
        assert _web_acl(_synth(stage))["Scope"] == "REGIONAL"

    @pytest.mark.parametrize("stage", ["staging", "prod"])
    def test_association_targets_the_user_pool(self, stage: str) -> None:
        """An unassociated web ACL protects nothing while appearing deployed."""
        template = _synth(stage)
        assocs = template.find_resources("AWS::WAFv2::WebACLAssociation")
        assert len(assocs) == 1, f"expected one association, found {len(assocs)}"
        props = next(iter(assocs.values()))["Properties"]
        assert props["ResourceArn"] == _POOL_ARN
        assert "WebACLArn" in props

    def test_default_action_is_allow(self, staging_template: Template) -> None:
        """Default-deny on an auth surface would lock every user out."""
        assert "Allow" in _web_acl(staging_template)["DefaultAction"]

    def test_acl_name_is_stage_scoped(self) -> None:
        """Distinct from the CloudFront ACL's name so the two are never confused."""
        assert _web_acl(_synth("prod"))["Name"] == "cms-prod-pool-waf"
        assert _web_acl(_synth("staging"))["Name"] == "cms-staging-pool-waf"


# ---------------------------------------------------------------------------
# Hard service constraints, asserted as absences
# ---------------------------------------------------------------------------

class TestServiceConstraints:
    @pytest.mark.parametrize("stage", ["staging", "prod"])
    def test_atp_managed_rule_group_is_absent(self, stage: str) -> None:
        """A web ACL using ATP cannot be associated with a Cognito user pool at all.

        Adding it would not fail synth — it would fail the association at deploy, leaving
        the pool unprotected. Hence an explicit absence assertion.
        """
        rendered = str(_web_acl(_synth(stage))["Rules"])
        assert "AWSManagedRulesATPRuleSet" not in rendered

    @pytest.mark.parametrize("stage", ["staging", "prod"])
    def test_no_rule_uses_captcha_action(self, stage: str) -> None:
        """A CAPTCHA in a pool-associated ACL breaks managed-login TOTP registration."""
        for name, rule in _rules(_synth(stage)).items():
            action = rule.get("Action", {})
            assert "Captcha" not in action, f"rule {name} uses a CAPTCHA action"


# ---------------------------------------------------------------------------
# Rule inventory + stage posture
# ---------------------------------------------------------------------------

class TestRuleInventory:
    _EXPECTED = {
        "AmazonIPReputation": 0,
        "KnownBadInputs": 1,
        "CommonRuleSet": 2,
        "SignupRateLimit": 10,
        "AuthRateLimit": 11,
    }

    @pytest.mark.parametrize("stage", ["staging", "prod"])
    def test_all_rules_present_at_expected_priority(self, stage: str) -> None:
        rules = _rules(_synth(stage))
        assert set(rules) == set(self._EXPECTED)
        for name, priority in self._EXPECTED.items():
            assert rules[name]["Priority"] == priority, name

    @pytest.mark.parametrize(
        "rule_name", ["AmazonIPReputation", "KnownBadInputs", "CommonRuleSet"]
    )
    def test_staging_managed_groups_count(
        self, staging_template: Template, rule_name: str
    ) -> None:
        assert "Count" in _rules(staging_template)[rule_name]["OverrideAction"]

    @pytest.mark.parametrize("rule_name", ["AmazonIPReputation", "KnownBadInputs"])
    def test_prod_low_false_positive_groups_block(
        self, prod_template: Template, rule_name: str
    ) -> None:
        """`None` override = use the group's own actions, i.e. BLOCK."""
        assert "None" in _rules(prod_template)[rule_name]["OverrideAction"]

    def test_prod_common_ruleset_stays_in_count(self, prod_template: Template) -> None:
        """Deliberate divergence from the CloudFront ACL's posture.

        Cognito forwards the request BODY for user-pool API calls, so OWASP body rules
        would inspect JSON carrying passwords and JWTs. A false positive there is a user
        who cannot sign in, and there is no traffic baseline for this surface yet.
        Graduating to BLOCK is a follow-on gated on COUNT metrics — if this test is
        changed, that graduation decision is being made, so make it deliberately.
        """
        assert "Count" in _rules(prod_template)["CommonRuleSet"]["OverrideAction"]

    def test_unrecognised_stage_fails_safe_to_count(self) -> None:
        """Anything that is not exactly "prod" gets COUNT, not BLOCK."""
        rules = _rules(_synth("preprod"))
        for name in ("AmazonIPReputation", "KnownBadInputs", "CommonRuleSet"):
            assert "Count" in rules[name]["OverrideAction"], name


# ---------------------------------------------------------------------------
# Rate rules — the actual control
# ---------------------------------------------------------------------------

class TestSignupRateRule:
    def _rate(self, template: Template) -> dict:
        return _rules(template)["SignupRateLimit"]["Statement"]["RateBasedStatement"]

    @pytest.mark.parametrize("stage", ["staging", "prod"])
    def test_blocks_on_every_stage(self, stage: str) -> None:
        """COUNT here would leave the observation window unprotected."""
        assert "Block" in _rules(_synth(stage))["SignupRateLimit"]["Action"]

    def test_limit_and_window(self, staging_template: Template) -> None:
        rate = self._rate(staging_template)
        assert rate["Limit"] == 100
        assert rate["EvaluationWindowSec"] == 300
        assert rate["AggregateKeyType"] == "IP"

    def test_scope_down_covers_managed_login_paths(
        self, staging_template: Template
    ) -> None:
        statements = self._rate(staging_template)["ScopeDownStatement"]["OrStatement"][
            "Statements"
        ]
        paths = {
            s["ByteMatchStatement"]["SearchString"]
            for s in statements
            if "UriPath" in s["ByteMatchStatement"]["FieldToMatch"]
        }
        assert paths == {"/signup", "/confirm"}

    def test_scope_down_covers_user_pool_api_operation_headers(
        self, staging_template: Template
    ) -> None:
        """The API route is how an SDK-driven SignUp arrives; paths alone miss it.

        Asserts the PascalCase `Name` key deliberately. `CfnWebACL.SingleHeaderProperty`
        renders lowercase `name`, which CloudFormation rejects (cfn-lint E3003 + E3002,
        confirmed 2026-08-11 on aws-cdk-lib 2.257.0), so the construct passes a raw dict.
        Reverting to the typed property would still synthesise and would still look
        correct in a shape test keyed on the value — this test fails on the key.
        """
        statements = self._rate(staging_template)["ScopeDownStatement"]["OrStatement"][
            "Statements"
        ]
        single_headers = [
            s["ByteMatchStatement"]["FieldToMatch"]["SingleHeader"]
            for s in statements
            if "SingleHeader" in s["ByteMatchStatement"]["FieldToMatch"]
        ]
        assert single_headers, "no SingleHeader matcher in the signup scope-down"
        for sh in single_headers:
            assert list(sh) == ["Name"], (
                f"SingleHeader key must be PascalCase 'Name'; CloudFormation rejects "
                f"the CDK typed property's lowercase 'name'. Got: {sh}"
            )
        assert {sh["Name"] for sh in single_headers} == {
            "x-amz-target",
            "x-amzn-cognito-operation-name",
        }

    def test_scope_down_matchers_are_case_folded(
        self, staging_template: Template
    ) -> None:
        """`/SignUp` and `AWSCognitoIdentityProviderService.SignUp` must both match."""
        statements = self._rate(staging_template)["ScopeDownStatement"]["OrStatement"][
            "Statements"
        ]
        for s in statements:
            transforms = s["ByteMatchStatement"]["TextTransformations"]
            assert any(t["Type"] == "LOWERCASE" for t in transforms)
            assert s["ByteMatchStatement"]["SearchString"].islower()


class TestAuthRateRule:
    def _rate(self, template: Template) -> dict:
        return _rules(template)["AuthRateLimit"]["Statement"]["RateBasedStatement"]

    @pytest.mark.parametrize("stage", ["staging", "prod"])
    def test_blocks_on_every_stage(self, stage: str) -> None:
        assert "Block" in _rules(_synth(stage))["AuthRateLimit"]["Action"]

    def test_is_unscoped_so_it_reaches_the_sign_in_surface(
        self, staging_template: Template
    ) -> None:
        """Credential stuffing lands on /login and InitiateAuth, not on /signup.

        A scope-down here would reintroduce the original defect in miniature: a rule
        aimed at the wrong requests.
        """
        assert "ScopeDownStatement" not in self._rate(staging_template)

    def test_limit_is_above_interactive_use(self, staging_template: Template) -> None:
        """Corporate NAT puts many legitimate users behind one address."""
        rate = self._rate(staging_template)
        assert rate["Limit"] == 2000
        assert rate["EvaluationWindowSec"] == 300


# ---------------------------------------------------------------------------
# Visibility — a COUNT rule with metrics off is invisible
# ---------------------------------------------------------------------------

class TestVisibility:
    @pytest.mark.parametrize("stage", ["staging", "prod"])
    def test_every_rule_emits_metrics_and_samples(self, stage: str) -> None:
        for name, rule in _rules(_synth(stage)).items():
            vis = rule["VisibilityConfig"]
            assert vis["CloudWatchMetricsEnabled"] is True, name
            assert vis["SampledRequestsEnabled"] is True, name

    def test_web_acl_itself_emits_metrics(self, staging_template: Template) -> None:
        vis = _web_acl(staging_template)["VisibilityConfig"]
        assert vis["CloudWatchMetricsEnabled"] is True
        assert vis["MetricName"] == "cms-staging-pool-waf"
