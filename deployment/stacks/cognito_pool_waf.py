"""REGIONAL WAF web ACL for the Cognito user pool — the surface signup actually uses.

Why this exists separately from `waf_stack.py`
----------------------------------------------
`waf_stack.py` ships a **CLOUDFRONT**-scoped web ACL attached to the UI distribution, and
its rate-based rule is scoped to `/signup*` on that distribution. That rule can never
match. Self-registration does not traverse CloudFront:

  * there is no SPA signup route (the frontend has none);
  * the auth model is redirect-to-Cognito, so signup lives on the pool's own
    managed-login domain (`/signup`, `/confirm`), not on the distribution;
  * the prod distribution has one S3 origin and zero extra cache behaviours, so nothing
    proxies the Cognito API through it.

So "a WAF is attached to prod" was true while "the registration endpoint is rate-limited"
was false. See `decisions.md` 2026-08-10 § "WAF is deployed and attached, but the signup
rate-limit rule is on the wrong surface". A WAF rule is only as real as the association it
hangs off.

AWS WAF **can** be associated with a Cognito user pool, which protects "your classic
hosted UI, managed login, and Amazon Cognito API service endpoints"
(https://docs.aws.amazon.com/cognito/latest/developerguide/user-pool-waf.html,
verified 2026-08-11). That association requires a **REGIONAL** web ACL in the pool's own
region, so it is a second, different web ACL — not a re-scope of the CloudFront one.
Because it must live in the pool's region, this is a Construct instantiated inside
`ui_stack` (which owns the pool and is already in that region) rather than a separate
stack: no cross-region context plumbing, no two-phase deploy, and the association is a
one-way `Association -> Pool` edge so it cannot recreate the trigger cycle that
`00eff297` fixed.

Rule set and stage posture
--------------------------
  p0  AWSManagedRulesAmazonIpReputationList   staging COUNT / prod BLOCK
  p1  AWSManagedRulesKnownBadInputsRuleSet    staging COUNT / prod BLOCK
  p2  AWSManagedRulesCommonRuleSet            COUNT on **all** stages (see below)
  p10 SignupRateLimit    (rate-based, scoped)  BLOCK on all stages
  p11 AuthRateLimit      (rate-based, global)  BLOCK on all stages

`CommonRuleSet` stays in COUNT even on prod, deliberately and against the CloudFront
stack's posture, because the two surfaces differ in one way that matters: Cognito
**forwards the request body** for user-pool API calls (it does not for managed login).
OWASP body rules — `SizeRestrictions_BODY`, `CrossSiteScripting_BODY`, `GenericRFI_BODY` —
would therefore inspect JSON containing passwords and JWTs, where a false positive is not
a blocked scan, it is a user who cannot sign in. There is no traffic baseline for this
surface yet. The two low-false-positive groups go straight to BLOCK on prod on the
evidence that the same three groups have been in BLOCK on the prod distribution since
2026-08-10 with prod still serving 200s, so the same client IPs already clear them.
Graduating `CommonRuleSet` to BLOCK is a follow-on gated on a week of COUNT metrics.

`AWSManagedRulesATPRuleSet` (Fraud Control account-takeover prevention) is **not usable
here** — the docs state a web ACL using it cannot be associated with a Cognito user pool
at all. A test asserts its absence, because that is a hard service constraint rather than
a preference.

No rule uses the **CAPTCHA** action. Per the same docs, a CAPTCHA in a pool-associated web
ACL causes an unrecoverable error in managed-login TOTP registration. Also test-asserted.

Rate-rule targeting
-------------------
`SignupRateLimit`'s scope-down covers both routes a registration can arrive by, because
either one alone leaves a hole:

  * managed-login paths `/signup*` and `/confirm*` — the interactive surface. `/confirm*`
    intentionally also covers `/confirmUser` and `/confirmforgotPassword`, which are the
    same abuse shape. Endpoint paths verified at
    https://docs.aws.amazon.com/cognito/latest/developerguide/managed-login-endpoints.html
    (2026-08-11).
  * the user-pool API, matched on `x-amz-target` (what the AWS SDKs send, e.g.
    `AWSCognitoIdentityProviderService.SignUp`) and on `x-amzn-cognito-operation-name`
    (which the docs name as the header identifying the operation in WAF logs, so it is
    forwarded to WAF). Both are matched so the rule holds whichever is present.

`AuthRateLimit` is unscoped and deliberately generous (2000 req / 5 min per IP ≈ 6.7 rps
sustained from one source). It exists because the blocker this work closes names
credential stuffing, which happens on `/login` and `InitiateAuth` — surfaces a
signup-scoped rule does not touch. The limit is set well above interactive use since
corporate NAT egress puts many legitimate users behind one address; distributed stuffing
is out of reach of any IP-based limit and is covered instead by the reputation list.

WCU budget: CommonRuleSet 700 + KnownBadInputs 200 + IpReputation 25 + two rate rules
(~10) ≈ 935, inside the 1500 default for a REGIONAL web ACL.
"""
from __future__ import annotations

from aws_cdk import aws_wafv2 as wafv2
from constructs import Construct

# Managed-login / classic-hosted-UI path prefixes that carry registration traffic.
# Verified against the AWS "User-interactive managed login and classic hosted UI
# endpoints" reference, 2026-08-11.
_SIGNUP_PATH_PREFIXES = ("/signup", "/confirm")

# Headers that identify a user-pool API operation. Matched with CONTAINS + LOWERCASE so
# `AWSCognitoIdentityProviderService.SignUp`, `SignUp` and `ConfirmSignUp` all hit.
_OPERATION_HEADERS = ("x-amz-target", "x-amzn-cognito-operation-name")
_OPERATION_NEEDLE = "signup"

# Requests per 5-minute window, per source IP.
_SIGNUP_RATE_LIMIT = 100
_AUTH_RATE_LIMIT = 2000
_RATE_WINDOW_SEC = 300


class CognitoPoolWaf(Construct):
    """A REGIONAL web ACL associated with one Cognito user pool.

    Parameters
    ----------
    scope, id:
        Standard construct arguments. Instantiate inside the stack that owns the pool —
        a REGIONAL web ACL and its association must be in the pool's region.
    stage:
        Deployment stage (``"staging"``, ``"prod"``, ...). Selects the managed-rule-group
        action mode; anything other than ``"prod"`` is treated as non-production and gets
        COUNT, which fails safe for an unrecognised stage.
    user_pool_arn:
        ARN of the pool to associate. One web ACL per pool is the service limit.

    Attributes
    ----------
    web_acl_arn:
        ARN of the created web ACL, for outputs or operator lookup.
    """

    def __init__(
        self,
        scope: Construct,
        id: str,
        *,
        stage: str,
        user_pool_arn: str,
    ) -> None:
        super().__init__(scope, id)

        self._stage = stage
        is_prod = stage == "prod"

        def _vis(metric_name: str) -> wafv2.CfnWebACL.VisibilityConfigProperty:
            # Metrics and sampling are on for every rule, including COUNT rules. A COUNT
            # rule without metrics is invisible, which defeats the only reason to count.
            return wafv2.CfnWebACL.VisibilityConfigProperty(
                cloud_watch_metrics_enabled=True,
                metric_name=metric_name,
                sampled_requests_enabled=True,
            )

        def _managed_rule(
            *, name: str, rule_name: str, priority: int, block_on_prod: bool
        ) -> wafv2.CfnWebACL.RuleProperty:
            # OverrideActionProperty.none={} means "use the rule group's own actions"
            # (i.e. BLOCK); count={} forces every rule in the group to COUNT.
            override = (
                wafv2.CfnWebACL.OverrideActionProperty(none={})
                if (is_prod and block_on_prod)
                else wafv2.CfnWebACL.OverrideActionProperty(count={})
            )
            return wafv2.CfnWebACL.RuleProperty(
                name=rule_name,
                priority=priority,
                statement=wafv2.CfnWebACL.StatementProperty(
                    managed_rule_group_statement=(
                        wafv2.CfnWebACL.ManagedRuleGroupStatementProperty(
                            vendor_name="AWS", name=name
                        )
                    )
                ),
                override_action=override,
                visibility_config=_vis(rule_name),
            )

        def _lower(priority: int = 0) -> list[wafv2.CfnWebACL.TextTransformationProperty]:
            return [
                wafv2.CfnWebACL.TextTransformationProperty(
                    priority=priority, type="LOWERCASE"
                )
            ]

        # ── Signup scope-down: managed-login paths OR user-pool API operation ──────
        signup_matchers: list[wafv2.CfnWebACL.StatementProperty] = [
            wafv2.CfnWebACL.StatementProperty(
                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(uri_path={}),
                    positional_constraint="STARTS_WITH",
                    search_string=prefix,
                    text_transformations=_lower(),
                )
            )
            for prefix in _SIGNUP_PATH_PREFIXES
        ]
        signup_matchers += [
            wafv2.CfnWebACL.StatementProperty(
                byte_match_statement=wafv2.CfnWebACL.ByteMatchStatementProperty(
                    field_to_match=wafv2.CfnWebACL.FieldToMatchProperty(
                        # Raw dict, NOT wafv2.CfnWebACL.SingleHeaderProperty(name=...).
                        #
                        # The typed property renders `{"SingleHeader": {"name": ...}}`
                        # with a lowercase key, and CloudFormation requires `Name`.
                        # cfn-lint rejects the typed form with two errors — E3003
                        # "'Name' is a required property" and E3002 "Additional
                        # properties are not allowed ('name' was unexpected)" — so the
                        # typed form does not deploy. Verified 2026-08-11 by linting both
                        # renderings of this exact rule (aws-cdk-lib 2.257.0).
                        #
                        # Nothing in a shape-only template test would catch this, which
                        # is why test_cognito_pool_waf.py asserts the PascalCase key
                        # explicitly. The header name itself is case-insensitive to WAF;
                        # the CFN property key is not.
                        single_header={"Name": header}
                    ),
                    positional_constraint="CONTAINS",
                    search_string=_OPERATION_NEEDLE,
                    text_transformations=_lower(),
                )
            )
            for header in _OPERATION_HEADERS
        ]

        signup_rate_rule = wafv2.CfnWebACL.RuleProperty(
            name="SignupRateLimit",
            priority=10,
            statement=wafv2.CfnWebACL.StatementProperty(
                rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                    aggregate_key_type="IP",
                    limit=_SIGNUP_RATE_LIMIT,
                    evaluation_window_sec=_RATE_WINDOW_SEC,
                    scope_down_statement=wafv2.CfnWebACL.StatementProperty(
                        or_statement=wafv2.CfnWebACL.OrStatementProperty(
                            statements=signup_matchers
                        )
                    ),
                )
            ),
            # BLOCK on every stage. This is the control the P1 exists for; counting it on
            # staging would leave the observation window unprotected and teach us nothing
            # we do not already know about legitimate signup volume (nobody submits 100
            # registrations from one IP in five minutes).
            action=wafv2.CfnWebACL.RuleActionProperty(block={}),
            visibility_config=_vis("SignupRateLimit"),
        )

        auth_rate_rule = wafv2.CfnWebACL.RuleProperty(
            name="AuthRateLimit",
            priority=11,
            statement=wafv2.CfnWebACL.StatementProperty(
                rate_based_statement=wafv2.CfnWebACL.RateBasedStatementProperty(
                    aggregate_key_type="IP",
                    limit=_AUTH_RATE_LIMIT,
                    evaluation_window_sec=_RATE_WINDOW_SEC,
                    # No scope-down: this covers the whole pool surface, which is where
                    # credential stuffing lands (/login, InitiateAuth).
                )
            ),
            action=wafv2.CfnWebACL.RuleActionProperty(block={}),
            visibility_config=_vis("AuthRateLimit"),
        )

        web_acl = wafv2.CfnWebACL(
            self,
            "WebACL",
            # WAF scope string — REGIONAL is required to associate with a Cognito pool;
            # a CLOUDFRONT-scoped ACL cannot be associated with a regional resource.
            scope="REGIONAL",
            default_action=wafv2.CfnWebACL.DefaultActionProperty(allow={}),
            visibility_config=_vis(f"cms-{stage}-pool-waf"),
            name=f"cms-{stage}-pool-waf",
            # ASCII only, and no parentheses. WAF validates `description` against
            #   ^[\w+=:#@/\-,\.][\w+=:#@/\-,\.\s]+[\w+=:#@/\-,\.]$
            # which permits word characters, spaces and `+=:#@/-,.` and nothing else.
            # An em dash here failed the deploy with
            #   "1 validation error detected: Value '...' at 'description' failed to
            #    satisfy constraint"
            # after `cdk synth` AND `aws wafv2 check-capacity` had both accepted it —
            # synth does not validate service-side string constraints, and check-capacity
            # inspects only the rules, never the ACL's own fields. Verified live
            # 2026-08-11; the stack rolled back cleanly. `test_waf_string_constraints.py`
            # now asserts this regex at synth time.
            description=(
                f"REGIONAL WAF for the CMS {stage} Cognito user pool: managed login, "
                "hosted UI, and user-pool API endpoints"
            ),
            rules=[
                _managed_rule(
                    name="AWSManagedRulesAmazonIpReputationList",
                    rule_name="AmazonIPReputation",
                    priority=0,
                    block_on_prod=True,
                ),
                _managed_rule(
                    name="AWSManagedRulesKnownBadInputsRuleSet",
                    rule_name="KnownBadInputs",
                    priority=1,
                    block_on_prod=True,
                ),
                # COUNT on every stage — see module docstring (body inspection of
                # Cognito API JSON; no baseline yet).
                _managed_rule(
                    name="AWSManagedRulesCommonRuleSet",
                    rule_name="CommonRuleSet",
                    priority=2,
                    block_on_prod=False,
                ),
                signup_rate_rule,
                auth_rate_rule,
            ],
        )

        # Association is a one-way edge (Association -> Pool). Nothing depends on the
        # association, so this cannot reintroduce the Pool -> Function -> Role -> Pool
        # cycle that `00eff297` fixed.
        wafv2.CfnWebACLAssociation(
            self,
            "PoolAssociation",
            resource_arn=user_pool_arn,
            web_acl_arn=web_acl.attr_arn,
        )

        self.web_acl_arn = web_acl.attr_arn
