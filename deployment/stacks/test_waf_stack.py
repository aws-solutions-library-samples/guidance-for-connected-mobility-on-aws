"""Tests for WafStack (deployment/stacks/waf_stack.py).

Verification criteria from tasks.md Group 7 task 1:

  (1) All four rules present in correct priority order (0, 1, 2, 10)
  (2) staging = COUNT mode; prod = BLOCK mode for the three managed groups
  (3) Rate-based rule's limit (100) + URI-path scope-down (/signup, /confirm-signup)
  (4) SSM parameter exists at /cms/{stage}/ui-waf/web-acl-arn
  (5) Scope == CLOUDFRONT (added per task requirements)

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/test_waf_stack.py -v
"""
from __future__ import annotations

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Match, Template

from stacks.waf_stack import WafStack


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _synth(stage: str) -> Template:
    """Synthesize WafStack for the given stage and return an assertions Template."""
    app = cdk.App()
    WafStack(app, f"cms-{stage}-ui-waf", stage=stage)
    return Template.from_stack(WafStack(cdk.App(), f"cms-{stage}-ui-waf", stage=stage))


@pytest.fixture(scope="module")
def staging_template() -> Template:
    return _synth("staging")


@pytest.fixture(scope="module")
def prod_template() -> Template:
    return _synth("prod")


# ---------------------------------------------------------------------------
# (5) Scope must be CLOUDFRONT
# ---------------------------------------------------------------------------

class TestScope:
    def test_staging_scope_is_cloudfront(self, staging_template: Template) -> None:
        """WebACL scope must be CLOUDFRONT — required for CloudFront distributions."""
        staging_template.has_resource_properties(
            "AWS::WAFv2::WebACL",
            {"Scope": "CLOUDFRONT"},
        )

    def test_prod_scope_is_cloudfront(self, prod_template: Template) -> None:
        prod_template.has_resource_properties(
            "AWS::WAFv2::WebACL",
            {"Scope": "CLOUDFRONT"},
        )


# ---------------------------------------------------------------------------
# (1) All four rules present in correct priority order
# ---------------------------------------------------------------------------

class TestRuleOrder:
    """Verify each rule exists at the correct priority in both stage templates."""

    @pytest.mark.parametrize(
        "rule_name, expected_priority",
        [
            ("AmazonIPReputation", 0),
            ("KnownBadInputs", 1),
            ("CommonRuleSet", 2),
            ("SignupRateLimit", 10),
        ],
    )
    def test_staging_rule_priority(
        self, staging_template: Template, rule_name: str, expected_priority: int
    ) -> None:
        staging_template.has_resource_properties(
            "AWS::WAFv2::WebACL",
            {
                "Rules": Match.array_with(
                    [
                        Match.object_like(
                            {"Name": rule_name, "Priority": expected_priority}
                        )
                    ]
                )
            },
        )

    @pytest.mark.parametrize(
        "rule_name, expected_priority",
        [
            ("AmazonIPReputation", 0),
            ("KnownBadInputs", 1),
            ("CommonRuleSet", 2),
            ("SignupRateLimit", 10),
        ],
    )
    def test_prod_rule_priority(
        self, prod_template: Template, rule_name: str, expected_priority: int
    ) -> None:
        prod_template.has_resource_properties(
            "AWS::WAFv2::WebACL",
            {
                "Rules": Match.array_with(
                    [
                        Match.object_like(
                            {"Name": rule_name, "Priority": expected_priority}
                        )
                    ]
                )
            },
        )

    def test_exactly_four_rules_staging(self, staging_template: Template) -> None:
        """No extra rules shipped — Bot Control must not appear."""
        acls = staging_template.find_resources("AWS::WAFv2::WebACL")
        assert len(acls) == 1
        acl = list(acls.values())[0]
        rules = acl["Properties"]["Rules"]
        assert len(rules) == 4, f"Expected 4 rules, got {len(rules)}: {[r['Name'] for r in rules]}"

    def test_bot_control_absent_staging(self, staging_template: Template) -> None:
        """AWSManagedRulesBotControlRuleSet is deferred to v1.1 (paid group)."""
        acls = staging_template.find_resources("AWS::WAFv2::WebACL")
        acl = list(acls.values())[0]
        rule_names = [r["Name"] for r in acl["Properties"]["Rules"]]
        assert "BotControl" not in " ".join(rule_names)
        # Also verify managed group names directly
        statements = [r.get("Statement", {}) for r in acl["Properties"]["Rules"]]
        managed_names = [
            s.get("ManagedRuleGroupStatement", {}).get("Name", "")
            for s in statements
        ]
        assert "AWSManagedRulesBotControlRuleSet" not in managed_names

    def test_managed_rule_groups_use_aws_vendor(self, staging_template: Template) -> None:
        """All managed rule groups must use VendorName=AWS."""
        acls = staging_template.find_resources("AWS::WAFv2::WebACL")
        acl = list(acls.values())[0]
        for rule in acl["Properties"]["Rules"]:
            stmt = rule.get("Statement", {})
            mrg = stmt.get("ManagedRuleGroupStatement")
            if mrg:
                assert mrg.get("VendorName") == "AWS", (
                    f"Rule {rule['Name']} has VendorName={mrg.get('VendorName')!r}"
                )


# ---------------------------------------------------------------------------
# (2) staging = COUNT, prod = BLOCK for the three managed groups
# ---------------------------------------------------------------------------

class TestActionMode:
    """Managed rule groups: staging=COUNT, prod=BLOCK."""

    _MANAGED_RULE_NAMES = ["AmazonIPReputation", "KnownBadInputs", "CommonRuleSet"]

    def _get_override_action_for_rule(self, template: Template, rule_name: str) -> dict:
        acls = template.find_resources("AWS::WAFv2::WebACL")
        acl = list(acls.values())[0]
        for rule in acl["Properties"]["Rules"]:
            if rule["Name"] == rule_name:
                return rule.get("OverrideAction", {})
        raise AssertionError(f"Rule {rule_name!r} not found in WebACL")

    @pytest.mark.parametrize("rule_name", _MANAGED_RULE_NAMES)
    def test_staging_managed_rules_are_count(
        self, staging_template: Template, rule_name: str
    ) -> None:
        """Staging managed rule groups must use COUNT override (OverrideAction.Count)."""
        override = self._get_override_action_for_rule(staging_template, rule_name)
        assert "Count" in override, (
            f"Rule {rule_name} on staging must use COUNT override; got {override}"
        )
        assert "None" not in override, (
            f"Rule {rule_name} on staging must not use None (BLOCK) override; got {override}"
        )

    @pytest.mark.parametrize("rule_name", _MANAGED_RULE_NAMES)
    def test_prod_managed_rules_are_block(
        self, prod_template: Template, rule_name: str
    ) -> None:
        """Prod managed rule groups must use None override (rule group's BLOCK action)."""
        override = self._get_override_action_for_rule(prod_template, rule_name)
        # OverrideActionProperty(none={}) serialises as {"None": {}} in the template
        assert "None" in override, (
            f"Rule {rule_name} on prod must use None (BLOCK) override; got {override}"
        )
        assert "Count" not in override, (
            f"Rule {rule_name} on prod must not use COUNT override; got {override}"
        )

    def test_rate_rule_blocks_on_staging(self, staging_template: Template) -> None:
        """Rate-based rule must BLOCK on staging (not COUNT) — low false-positive risk."""
        acls = staging_template.find_resources("AWS::WAFv2::WebACL")
        acl = list(acls.values())[0]
        rate_rule = next(
            r for r in acl["Properties"]["Rules"] if r["Name"] == "SignupRateLimit"
        )
        # Rate-based rule uses `Action`, not `OverrideAction`
        action = rate_rule.get("Action", {})
        assert "Block" in action, (
            f"SignupRateLimit on staging must use Action.Block; got {action}"
        )

    def test_rate_rule_blocks_on_prod(self, prod_template: Template) -> None:
        """Rate-based rule must BLOCK on prod."""
        acls = prod_template.find_resources("AWS::WAFv2::WebACL")
        acl = list(acls.values())[0]
        rate_rule = next(
            r for r in acl["Properties"]["Rules"] if r["Name"] == "SignupRateLimit"
        )
        action = rate_rule.get("Action", {})
        assert "Block" in action, (
            f"SignupRateLimit on prod must use Action.Block; got {action}"
        )


# ---------------------------------------------------------------------------
# (3) Rate-based rule's limit + URI-path scope-down
# ---------------------------------------------------------------------------

class TestRateBasedRule:
    """Verify the rate-based rule parameters and URI-path scope-down."""

    def _get_rate_statement(self, template: Template) -> dict:
        acls = template.find_resources("AWS::WAFv2::WebACL")
        acl = list(acls.values())[0]
        rate_rule = next(
            r for r in acl["Properties"]["Rules"] if r["Name"] == "SignupRateLimit"
        )
        return rate_rule["Statement"]["RateBasedStatement"]

    def test_rate_limit_is_100(self, staging_template: Template) -> None:
        stmt = self._get_rate_statement(staging_template)
        assert stmt["Limit"] == 100, f"Expected Limit=100, got {stmt['Limit']}"

    def test_evaluation_window_is_300s(self, staging_template: Template) -> None:
        stmt = self._get_rate_statement(staging_template)
        assert stmt["EvaluationWindowSec"] == 300, (
            f"Expected EvaluationWindowSec=300 (5 min), got {stmt['EvaluationWindowSec']}"
        )

    def test_aggregate_key_type_is_ip(self, staging_template: Template) -> None:
        stmt = self._get_rate_statement(staging_template)
        assert stmt["AggregateKeyType"] == "IP"

    def _extract_byte_match_strings(self, scope_down: dict) -> list[str]:
        """Recursively collect all SearchString values from byte-match statements."""
        results: list[str] = []
        if "ByteMatchStatement" in scope_down:
            results.append(scope_down["ByteMatchStatement"]["SearchString"])
        if "OrStatement" in scope_down:
            for sub in scope_down["OrStatement"]["Statements"]:
                results.extend(self._extract_byte_match_strings(sub))
        return results

    def test_scope_down_covers_signup_prefix(self, staging_template: Template) -> None:
        """Scope-down statement must match /signup path prefix."""
        stmt = self._get_rate_statement(staging_template)
        scope_down = stmt.get("ScopeDownStatement", {})
        search_strings = self._extract_byte_match_strings(scope_down)
        assert "/signup" in search_strings, (
            f"Expected /signup in ScopeDownStatement search strings; got {search_strings}"
        )

    def test_scope_down_covers_confirm_signup_prefix(
        self, staging_template: Template
    ) -> None:
        """Scope-down statement must match /confirm-signup path prefix."""
        stmt = self._get_rate_statement(staging_template)
        scope_down = stmt.get("ScopeDownStatement", {})
        search_strings = self._extract_byte_match_strings(scope_down)
        assert "/confirm-signup" in search_strings, (
            f"Expected /confirm-signup in search strings; got {search_strings}"
        )

    def test_scope_down_uses_starts_with(self, staging_template: Template) -> None:
        """URI-path matching must use STARTS_WITH positional constraint."""
        stmt = self._get_rate_statement(staging_template)

        def _collect_constraints(node: dict) -> list[str]:
            results = []
            if "ByteMatchStatement" in node:
                results.append(node["ByteMatchStatement"]["PositionalConstraint"])
            if "OrStatement" in node:
                for sub in node["OrStatement"]["Statements"]:
                    results.extend(_collect_constraints(sub))
            return results

        scope_down = stmt.get("ScopeDownStatement", {})
        constraints = _collect_constraints(scope_down)
        assert all(c == "STARTS_WITH" for c in constraints), (
            f"All byte-match constraints must be STARTS_WITH; got {constraints}"
        )

    def test_scope_down_uses_uri_path(self, staging_template: Template) -> None:
        """Byte-match statements must target UriPath."""
        stmt = self._get_rate_statement(staging_template)

        def _collect_fields(node: dict) -> list[dict]:
            results = []
            if "ByteMatchStatement" in node:
                results.append(node["ByteMatchStatement"]["FieldToMatch"])
            if "OrStatement" in node:
                for sub in node["OrStatement"]["Statements"]:
                    results.extend(_collect_fields(sub))
            return results

        scope_down = stmt.get("ScopeDownStatement", {})
        fields = _collect_fields(scope_down)
        assert all("UriPath" in f for f in fields), (
            f"All FieldToMatch entries must contain UriPath; got {fields}"
        )

    def test_scope_down_uses_lowercase_transform(self, staging_template: Template) -> None:
        """Text transformations must include LOWERCASE for case-insensitive matching."""
        stmt = self._get_rate_statement(staging_template)

        def _collect_transforms(node: dict) -> list[str]:
            results = []
            if "ByteMatchStatement" in node:
                transforms = node["ByteMatchStatement"].get("TextTransformations", [])
                results.extend(t["Type"] for t in transforms)
            if "OrStatement" in node:
                for sub in node["OrStatement"]["Statements"]:
                    results.extend(_collect_transforms(sub))
            return results

        scope_down = stmt.get("ScopeDownStatement", {})
        transforms = _collect_transforms(scope_down)
        assert "LOWERCASE" in transforms, (
            f"Expected LOWERCASE transform in scope-down; got {transforms}"
        )


# ---------------------------------------------------------------------------
# (4) SSM parameter at /cms/{stage}/ui-waf/web-acl-arn
# ---------------------------------------------------------------------------

class TestSsmExport:
    def test_staging_ssm_parameter_exists(self, staging_template: Template) -> None:
        staging_template.has_resource_properties(
            "AWS::SSM::Parameter",
            {"Name": "/cms/staging/ui-waf/web-acl-arn"},
        )

    def test_prod_ssm_parameter_exists(self, prod_template: Template) -> None:
        prod_template.has_resource_properties(
            "AWS::SSM::Parameter",
            {"Name": "/cms/prod/ui-waf/web-acl-arn"},
        )

    def test_staging_cfn_output_exists(self, staging_template: Template) -> None:
        """CfnOutput WebAclArn must also be present for cross-stack wiring."""
        outputs = staging_template.find_outputs("WebAclArn")
        assert outputs, "CfnOutput 'WebAclArn' not found in staging template"

    def test_prod_cfn_output_exists(self, prod_template: Template) -> None:
        outputs = prod_template.find_outputs("WebAclArn")
        assert outputs, "CfnOutput 'WebAclArn' not found in prod template"


# ---------------------------------------------------------------------------
# Visibility / CloudWatch — COUNT rules must have metrics enabled
# ---------------------------------------------------------------------------

class TestVisibility:
    """Every rule and the WebACL itself must have CloudWatch metrics enabled.

    A COUNT rule with metrics disabled is invisible — it defeats the purpose
    of COUNT mode (observation before enforcement).
    """

    def _get_all_visibility_configs(self, template: Template) -> list[dict]:
        """Return the VisibilityConfig for the WebACL and every rule."""
        acls = template.find_resources("AWS::WAFv2::WebACL")
        acl = list(acls.values())[0]
        configs = [acl["Properties"]["VisibilityConfig"]]
        for rule in acl["Properties"]["Rules"]:
            configs.append(rule["VisibilityConfig"])
        return configs

    def test_staging_all_metrics_enabled(self, staging_template: Template) -> None:
        configs = self._get_all_visibility_configs(staging_template)
        for cfg in configs:
            assert cfg["CloudWatchMetricsEnabled"] is True, (
                f"CloudWatchMetricsEnabled must be True; got {cfg}"
            )
            assert cfg["SampledRequestsEnabled"] is True, (
                f"SampledRequestsEnabled must be True; got {cfg}"
            )

    def test_prod_all_metrics_enabled(self, prod_template: Template) -> None:
        configs = self._get_all_visibility_configs(prod_template)
        for cfg in configs:
            assert cfg["CloudWatchMetricsEnabled"] is True
            assert cfg["SampledRequestsEnabled"] is True


class TestEnvHandling:
    """The stack must construct the way app.py actually calls it.

    app.py passes `env=Environment(account=..., region=...)` — a cdk.Environment
    object, not a dict. An earlier revision did `dict(kwargs.pop("env", None) or {})`,
    which raises `TypeError: 'Environment' object is not iterable` for that caller. All
    35 other tests passed against it because every one of them passed a dict or omitted
    env entirely — the stack was unusable by its only real consumer.

    Same shape as several defects found on 2026-08-10: the test exercised a calling
    convention the production caller does not use.
    """

    def test_accepts_a_real_cdk_environment(self) -> None:
        app = cdk.App()
        stack = WafStack(
            app, "cms-staging-ui-waf", stage="staging",
            env=cdk.Environment(account="123456789012", region="us-west-2"),
        )
        assert stack.region == "us-east-1", (
            "CLOUDFRONT-scoped WAF must be pinned to us-east-1 even when the app's "
            "primary region is elsewhere"
        )
        assert stack.account == "123456789012", "account must be preserved from env"

    def test_accepts_a_dict_env(self) -> None:
        app = cdk.App()
        stack = WafStack(
            app, "cms-staging-ui-waf", stage="staging",
            env={"account": "123456789012", "region": "us-west-2"},
        )
        assert stack.region == "us-east-1"
        assert stack.account == "123456789012"

    def test_accepts_no_env(self) -> None:
        app = cdk.App()
        stack = WafStack(app, "cms-staging-ui-waf", stage="staging")
        assert stack.region == "us-east-1"

    def test_region_is_pinned_even_if_env_says_us_east_1(self) -> None:
        """Idempotent: passing us-east-1 explicitly must not break anything."""
        app = cdk.App()
        stack = WafStack(
            app, "cms-prod-ui-waf", stage="prod",
            env=cdk.Environment(account="123456789012", region="us-east-1"),
        )
        assert stack.region == "us-east-1"
