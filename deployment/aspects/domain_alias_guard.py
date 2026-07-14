"""Synth-time guard for the UI custom-domain alias (staging + prod).

The CMS UI `FrontendDistribution` custom-domain alias is resolved from ambient
CDK context (`uiCustomDomain` / `uiCustomDomainCertArn`) per the domain map
committed in `app.py` (`UI_CUSTOM_DOMAIN_BY_STAGE`).
A `cdk deploy` synthesized *without* that context silently drops the alias — and
with it the Cognito unauthenticated path / SpaRewriteFunction (and, on staging
deployments that sit behind an internal edge-auth gate, that gate). See issue
`2026-06-18-cms-ui-domain-alias-context-conditional-deploy-risk`.

This guard aborts synth the moment a domain-bearing UI stack would be
synthesized without its alias in that stack's home region. The caller (app.py)
supplies the per-stage expected alias + home region.

Design note (decisions.md): an earlier `IAspect` that inspected
`CfnDistribution.distribution_config.aliases` aborted synth via a jsii
serialization error when no alias is set, before the guard ran. We instead
consume `ui_stack`'s own `ui_custom_domain_attached` signal via this pure
function — robust, testable, no jsii round-trip.
"""

from __future__ import annotations


def enforce_ui_domain_alias(
    *,
    region: str,
    ui_custom_domain_attached: bool,
    expected_alias: str,
    home_region: str,
) -> None:
    """Raise ``RuntimeError`` if a domain-bearing UI stack would synth alias-less.

    Fires only in the home region: when ``region == home_region`` but the UI
    stack did NOT attach the custom-domain alias. Cross-region deploys
    (``region != home_region``) legitimately skip the alias (partition-global
    CloudFront-CNAME collision avoidance — see
    ``~/.kiro/steering/cross-region-namespace.md`` Check 3) and are exempt.

    The caller gates *which* stages are enforced (only stages with a committed
    expected domain), so this function is stage-agnostic.
    """
    if region != home_region:
        return
    if ui_custom_domain_attached:
        return
    raise RuntimeError(
        f"The CMS UI stack would synthesize WITHOUT its required custom-domain "
        f"alias '{expected_alias}' in its home region {home_region}. Deploying "
        f"from this context would silently drop the custom domain (and the "
        f"Cognito unauthenticated path / SpaRewriteFunction, and on deployments "
        f"behind an edge-auth gate, that gate). Set the domain context "
        f"(-c uiCustomDomain={expected_alias} -c uiCustomDomainCertArn=<acm-arn>) "
        f"or use the canonical UI deploy flow (config/<stage>.env supplies it). "
        f"See docs/DEPLOYMENT.md and issue "
        f"2026-06-18-cms-ui-domain-alias-context-conditional-deploy-risk."
    )
