"""WAF stack for CloudFront — account-provisioning posture.

Deploys a CloudFront-scoped WebACL in us-east-1 (required by AWS for any
WebACL attached to a CloudFront distribution).  The stack is instantiated
once per stage and is independent of the primary deployment region for
other CMS stacks.

Rule evaluation order (lower priority number = evaluated first):

  0. AWSManagedRulesAmazonIpReputationList  — IP threat intelligence
  1. AWSManagedRulesKnownBadInputsRuleSet   — known exploit patterns
  2. AWSManagedRulesCommonRuleSet           — OWASP top-10
  3. SignupRateLimit (rate-based)           — 100 req / 5 min per IP,
                                             scoped to /signup* and /confirm-signup*

NOTE: AWSManagedRulesBotControlRuleSet is intentionally omitted from v1.
It is a paid managed rule group (additional $10/month subscription + per-request
fee); deferred to v1.1 after baseline traffic observations confirm the need.

Action by stage:

  Managed rule groups (rules 0–2):
    - staging → COUNT (observe traffic, record what would have been blocked)
    - prod    → BLOCK (enforced from day one of Phase B enablement)

  Rate-based rule (rule 3) — BLOCK on ALL stages:
    Rate-limiting a signup endpoint is low false-positive risk because
    legitimate users do not submit more than 100 signup requests in 5
    minutes from the same IP.  Starting in COUNT mode on staging would
    offer no protection during the observation window; we gain nothing by
    counting first for this rule specifically.

CloudWatch visibility:

  ALL rules ship with CloudWatch metrics + sampled requests enabled,
  including staging rules in COUNT mode.  A COUNT rule with metrics
  disabled is invisible and defeats the entire purpose of COUNT mode,
  which is to *learn* what legitimate traffic looks like before enabling
  BLOCK.  Metrics are required; not optional for staging.

Cross-stack consumption:

  The WebACL ARN is exported two ways:
    1. SSM parameter at /cms/{stage}/ui-waf/web-acl-arn
       (for operator discoverability and manual inspection)
    2. CfnOutput named WebAclArn
       (the architect will wire the ARN into ui_stack via CDK context,
       following the same cross-region idiom used for uiCustomDomainCertArn
       — a us-east-1 ACM ARN read by the CloudFront distribution)
"""
from __future__ import annotations

import aws_cdk as cdk
from aws_cdk import (
    CfnOutput,
    Stack,
    aws_ssm as ssm,
    aws_wafv2 as wafv2,
)
from constructs import Construct


class WafStack(Stack):
    """CloudFront WAF stack (us-east-1).

    Parameters
    ----------
    scope:   CDK app construct.
    id:      Construct ID, conventionally cms-{stage}-ui-waf.
    stage:   Deployment stage string (e.g. "staging", "prod").
             Determines rule action mode for managed rule groups.
    **kwargs: Passed to Stack.__init__.  Do NOT include env.region here;
              this stack hard-codes us-east-1 because CloudFront-scoped
              WAF resources MUST live in us-east-1.
    """

    def __init__(self, scope: Construct, id: str, *, stage: str, **kwargs) -> None:
        # Force region to us-east-1 regardless of the primary region: a
        # CLOUDFRONT-scoped WAFv2 WebACL MUST live in us-east-1 (verified in
        # docs/tech.md § "CloudFront WAF for account provisioning").
        #
        # `env` may arrive as a cdk.Environment (which is how app.py passes it —
        # `env=Environment(account=..., region=...)`) or as a plain dict, or be
        # absent. An earlier revision did `dict(kwargs.pop("env", None) or {})`,
        # which raised `TypeError: 'Environment' object is not iterable` for the
        # real caller and was invisible to 35 passing tests because every test
        # passed a dict. Handle all three shapes.
        _env = kwargs.pop("env", None)
        if _env is None:
            account = None
        elif isinstance(_env, dict):
            account = _env.get("account")
        else:  # cdk.Environment
            account = getattr(_env, "account", None)
        super().__init__(
            scope, id, env=cdk.Environment(account=account, region="us-east-1"), **kwargs
        )

        self._stage = stage
        is_prod = stage == "prod"

        # ── Visibility helper ─────────────────────────────────────────────────
        # Used on every rule and on the WebACL itself.  CloudWatch metrics +
        # sampled requests are always enabled — even for COUNT rules on staging.
        def _vis(metric_name: str) -> wafv2.CfnWebACL.VisibilityConfigProperty:
            return wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=metric_name,
                sampled_requests_enabled=True,
            )

        # ── Managed rule group action ─────────────────────────────────────────
        # Staging uses COUNT (observe without blocking); prod uses BLOCK (enforce).
        # OverrideActionProperty.none={} means "use the rule group's own actions"
        # (i.e., BLOCK); OverrideActionProperty.count={} overrides all rules within
        # the group to COUNT.
        def _managed_override() -> wafv2.CfnWebACL.OverrideActionProperty:
            if is_prod:
                return wafv2.CfnWebACL.OverrideActionProperty(none={})
            return wafv2.CfnWebACL.OverrideActionProperty(count={})

        # ── Rule helpers ──────────────────────────────────────────────────────

        def _managed_rule(
            name: str,
            rule_name: str,
            priority: int,
        ) -> wafv2.CfnWebACL.RuleProperty:
            """Build a managed rule group rule."""
            return wafv2.CfnWebACL.RuleProperty(
                name=rule_name,
                priority=priority,
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                        vendor_name="AWS",
                        name=name,
                    )
                ),
                override_action=_managed_override(),
                visibility_config=_vis(rule_name),
            )

        # ── Rate-based scope-down statement ───────────────────────────────────
        # Restrict the rate count to /signup* and /confirm-signup* URI paths.
        # LOWERCASE text transformation is applied so /Signup and /SIGNUP
        # are treated identically.
        signup_match = wafv2.CfnWebACL.ByteMatchStatementProperty(
            field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
            positional_constraint="STARTS_WITH",
            search_string="/signup",
            text_transformations=[
                wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
            ],
        )

        confirm_signup_match = wafv2.CfnWebACL.ByteMatchStatementProperty(
            field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
            positional_constraint="STARTS_WITH",
            search_string="/confirm-signup",
            text_transformations=[
                wafv2.CfnWebACL.TextTransformationProperty(priority=0, type="LOWERCASE")
            ],
        )

        signup_paths_scope_down = wafv2.CfnWebACL.StatementProperty(
            or_statement=wafv2.CfnWebACL.OrStatementProperty(
                statements=[
                    wafv2.CfnWebACL.StatementProperty(byte_match_statement=signup_match),
                    wafv2.CfnWebACL.StatementProperty(
                        byte_match_statement=confirm_signup_match
                    ),
                ]
            )
        )

        # Rate-based rule — BLOCK on all stages (see module docstring for rationale).
        # 100 requests per 5-minute window (evaluation_window_sec=300) per source IP.
        rate_limit_rule = wafv2.CfnWebACL.RuleProperty(
            name="SignupRateLimit",
            priority=10,  # evaluated after managed-rule groups (priorities 0–2)
            statement=wafv2.CfnWebACL.StatementProperty(
                rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                    aggregate_key_type="IP",
                    limit=100,
                    evaluation_window_sec=300,  # 5-minute window
                    scope_down_statement=signup_paths_scope_down,
                )
            ),
            # Rate-based rule uses action (not override_action) because it is not
            # a managed rule group.  BLOCK on all stages — rate-limiting a signup
            # endpoint has negligible false-positive risk and should be enforced
            # even during the staging observation window.
            action=wafv2.CfnWebACL.RuleActionProperty(block={}),
            visibility_config=_vis("SignupRateLimit"),
        )

        # ── WebACL ────────────────────────────────────────────────────────────
        # scope_ is the CDK construct parent (positional); scope (kw) is the WAF
        # scope string.  These two parameters have different names to avoid
        # collision in the CDK Python bindings — verified via inspect.signature.
        web_acl = wafv2.CfnWebACL(
            self,
            "WebACL",
            scope="CLOUDFRONT",  # WAF scope — CloudFront requires us-east-1
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=_vis(f"cms-{stage}-waf"),
            name=f"cms-{stage}-ui-waf",
            description=f"CloudFront WAF for CMS {stage} signup posture",
            rules=[
                # Priority 0 — IP reputation (cheapest, eliminate known-bad IPs first)
                _managed_rule(
                    name="AWSManagedRulesAmazonIpReputationList",
                    rule_name="AmazonIPReputation",
                    priority=0,
                ),
                # Priority 1 — Known-bad inputs / exploit probes
                _managed_rule(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    rule_name="KnownBadInputs",
                    priority=1,
                ),
                # Priority 2 — OWASP common web application protection
                _managed_rule(
                    name="AWSManagedRulesCommonRuleSet",
                    rule_name="CommonRuleSet",
                    priority=2,
                ),
                # NOTE: AWSManagedRulesBotControlRuleSet intentionally omitted —
                # deferred to v1.1 (paid managed rule group; not in v1 scope).
                #
                # Priority 10 — Rate-based rule on /signup* and /confirm-signup*
                rate_limit_rule,
            ],
        )

        # ── SSM export ────────────────────────────────────────────────────────
        # The architect will read this parameter from ui_stack.py to attach the
        # WebACL to the CloudFront distribution.  Uses the same cross-region
        # idiom as uiCustomDomainCertArn: the SSM parameter lives in us-east-1
        # (same region as this stack); ui_stack resolves it at deploy time via
        # ssm.StringParameter.value_for_string_parameter().
        ssm.StringParameter(
            self,
            "WebAclArnParameter",
            parameter_name=f"/cms/{stage}/ui-waf/web-acl-arn",
            string_value=web_acl.attr_arn,
            description=f"CloudFront WebACL ARN for CMS {stage} — consumed by ui_stack",
        )

        # ── CfnOutput ─────────────────────────────────────────────────────────
        CfnOutput(
            self,
            "WebAclArn",
            value=web_acl.attr_arn,
            export_name=f"cms-{stage}-ui-waf-web-acl-arn",
            description="CloudFront WebACL ARN (us-east-1) for CMS UI stack attachment",
        )
