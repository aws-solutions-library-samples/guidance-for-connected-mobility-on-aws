"""Guard: WAF string fields must satisfy the service's own regexes at synth time.

Filed after a live failure on 2026-08-11. An em dash in the pool WebACL's `description`
was accepted by `cdk synth` and by `aws wafv2 check-capacity`, then rejected by the service
during `cdk deploy`:

    1 validation error detected: Value 'REGIONAL WAF for the CMS staging Cognito user
    pool ? managed login, ...' at 'description' failed to satisfy constraint:
    Member must satisfy regular expression pattern:
    ^[\\w+=:#@/\\-,\\.][\\w+=:#@/\\-,\\.\\s]+[\\w+=:#@/\\-,\\.]$

The stack rolled back cleanly, so the cost was time rather than damage — but it is the
fourth pre-deploy check this week that passed something the service refuses, and the
reason is always the same: each check validates a different slice.
`cdk synth` checks CloudFormation property *shape*; `check-capacity` checks *rule
grammar* and never looks at the ACL's own fields; neither enforces service-side string
constraints. This suite closes that specific gap for the fields WAF constrains, and it is
cheap: pure regex over the synthesised template, no AWS call.

Covers both WAF stacks — the CloudFront ACL in `waf_stack.py` and the pool ACL in
`cognito_pool_waf.py` — because the constraint is the service's, not either stack's.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/test_waf_string_constraints.py -v
"""
from __future__ import annotations

import re

import aws_cdk as cdk
import pytest
from aws_cdk.assertions import Template

from stacks.cognito_pool_waf import CognitoPoolWaf
from stacks.waf_stack import WafStack

_POOL_ARN = "arn:aws:cognito-idp:us-west-2:111111111111:userpool/<user-pool-id>"

# Verbatim from the service's validation message, 2026-08-11.
_DESCRIPTION_RE = re.compile(r"^[\w+=:#@/\-,\.][\w+=:#@/\-,\.\s]+[\w+=:#@/\-,\.]$")

# Names and CloudWatch metric names: word characters and hyphens only.
# https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-wafv2-webacl.html
_NAME_RE = re.compile(r"^[\w\-]{1,128}$")


def _templates() -> list[tuple[str, Template]]:
    out = []
    for stage in ("staging", "prod"):
        app = cdk.App()
        stack = cdk.Stack(app, f"cms-{stage}-ui")
        CognitoPoolWaf(stack, "PoolWaf", stage=stage, user_pool_arn=_POOL_ARN)
        out.append((f"pool/{stage}", Template.from_stack(stack)))

        out.append(
            (
                f"cloudfront/{stage}",
                Template.from_stack(WafStack(cdk.App(), f"cms-{stage}-ui-waf", stage=stage)),
            )
        )
    return out


_TEMPLATES = _templates()


@pytest.mark.parametrize("label,template", _TEMPLATES, ids=[t[0] for t in _TEMPLATES])
class TestWafStringConstraints:
    def _acls(self, template: Template) -> list[dict]:
        return [
            r["Properties"]
            for r in template.find_resources("AWS::WAFv2::WebACL").values()
        ]

    def test_description_satisfies_the_service_regex(self, label, template) -> None:
        """The field that actually failed. Non-ASCII punctuation is the usual culprit."""
        for props in self._acls(template):
            description = props.get("Description")
            if description is None:
                continue
            assert _DESCRIPTION_RE.match(description), (
                f"{label}: WebACL Description is rejected by WAF. Allowed: word "
                f"characters, spaces and + = : # @ / - , . — note that an em dash, "
                f"parentheses and quotes are NOT allowed. Got: {description!r}"
            )

    def test_acl_name_satisfies_the_service_regex(self, label, template) -> None:
        for props in self._acls(template):
            name = props["Name"]
            assert _NAME_RE.match(name), f"{label}: invalid WebACL Name {name!r}"

    def test_rule_and_metric_names_satisfy_the_service_regex(
        self, label, template
    ) -> None:
        """A rejected metric name fails the same way, one rule deeper in the payload."""
        for props in self._acls(template):
            assert _NAME_RE.match(props["VisibilityConfig"]["MetricName"]), (
                f"{label}: invalid WebACL MetricName "
                f"{props['VisibilityConfig']['MetricName']!r}"
            )
            for rule in props["Rules"]:
                assert _NAME_RE.match(rule["Name"]), (
                    f"{label}: invalid rule Name {rule['Name']!r}"
                )
                metric_name = rule["VisibilityConfig"]["MetricName"]
                assert _NAME_RE.match(metric_name), (
                    f"{label}: invalid rule MetricName {metric_name!r}"
                )

    def test_all_waf_strings_are_ascii(self, label, template) -> None:
        """Broad net for the general shape of the failure.

        The specific character was an em dash, but the lesson is that a non-ASCII
        character anywhere in a WAF string field is a deploy-time failure waiting for the
        next person who types a nicer-looking dash.
        """
        for props in self._acls(template):
            for field in ("Name", "Description"):
                value = props.get(field)
                if value is None:
                    continue
                assert value.isascii(), (
                    f"{label}: WebACL {field} contains a non-ASCII character: {value!r}"
                )
