"""Synth-based red-phase tests for ConnectedServicesUiStack.

Spec: .kiro/specs/2026-09-03-cms-connected-services-portal/ (Group 1 Task 1.5)

These tests synthesise ConnectedServicesUiStack in isolation using the CDK Python
API, then assert structural invariants on the CloudFormation template.  The stack
does not exist yet (it is built in T2.1); all tests must show as FAILED — NOT
as collection errors or skips.

The six items tested here correspond to the five DMS-extraction defects that escaped
HTTP-200 gates and were found by user UAT (spec R5), plus the callback-origin
assertion that proves the flag actually reaches the synthesized template:

  (a) SPA 403 → index.html fallback is configured in CloudFront
  (b) runtime-config.js is actually injected (BucketDeployment present)
  (c) index.html carries no-cache headers; hashed assets are long-cached
  (d) the nav shell renders (distribution default_root_object = index.html)
  (e) / resolves (default_root_object) and an unknown path hits the SPA catchall
  (f) connectedServicesUiCallbackOrigin appears in the synthesized template

**No test in this file may use an HTTP-200 assertion.**  On a SPA every route
returns the same index.html whether or not the app initialises, so HTTP 200
measures CloudFront rather than the application.  That is precisely why these
four defects escaped the DMS extraction's gates and were found by user UAT.

Run with (from deployment/ directory):
    .venv/bin/python -m pytest stacks/tests/test_connected_services_ui_stack.py -v

Decisions reference: decisions.md § "Test file locations corrected"
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — mirrors the two existing stack test files exactly
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent          # deployment/stacks/tests/
_STACKS = _HERE.parent                           # deployment/stacks/
_DEPLOYMENT = _STACKS.parent                     # deployment/

for _dir in (str(_DEPLOYMENT), str(_STACKS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_STAGE = "staging"
_ACCOUNT = "123456789012"   # synthetic placeholder — never a real AWS account
_REGION = "us-west-2"

# Stack name follows the repo-wide cms-{stage}-{name} convention confirmed in
# tasks.md global-constraints block (deployment/Makefile:593,753,781).
_STACK_NAME = f"cms-{_STAGE}-connected-services-ui"

# Expected context key for the callback origin (spec D3).
# W7 fix: use RFC 2606 .invalid placeholder — never an awsi.aws.dev hostname.
# The real staging domain (<connected-services-domain>) is only in staging.env,
# not in test fixtures that ship to the public mirror.
_CALLBACK_ORIGIN_KEY = "connectedServicesUiCallbackOrigin"
_CALLBACK_ORIGIN_VALUE = "https://example.invalid"

# Minimal required context keys so the stack can synth without raising ValueError
# for missing required values.  All values are synthetic placeholders — no real
# AWS account IDs, no internal hostnames, no real Cognito pool IDs.
#
# W7 fix (security-review Cycle 1):
#   - Pool ID: "us-west-2_EXAMPL" — 6 chars after "_", below the {8,} floor in
#     the cognito_user_pool_id scanner rule.  Any pool-ID-shaped string with 8+
#     chars after the "_" will trip the rule; this one does not.
#   - Cognito domain: "placeholder.example.invalid" — RFC 2606 .invalid TLD,
#     does not match the [a-z0-9-]+.auth.us-*.amazoncognito.com pattern.
#     The real staging hosted-UI domain is only in staging.env (publish-excluded).
#   - API endpoint: "https://api.example.invalid" — RFC 2606 .invalid TLD,
#     never matches internal_awsi pattern.
#   - Callback origin: see _CALLBACK_ORIGIN_VALUE above.
_SYNTH_CONTEXT: dict[str, str] = {
    "connectedServicesUiCognitoUserPoolId": "us-west-2_EXAMPL",
    "connectedServicesUiCognitoClientId": "testclientid00000001",
    "connectedServicesUiCognitoDomain": "placeholder.example.invalid",
    "connectedServicesUiApiEndpoint": "https://api.example.invalid",
    _CALLBACK_ORIGIN_KEY: _CALLBACK_ORIGIN_VALUE,
}


# ---------------------------------------------------------------------------
# Template helper — synthesise once and cache
# ---------------------------------------------------------------------------

_template_cache: dict | None = None


def _get_template() -> dict:
    """Synthesise ConnectedServicesUiStack and return the CloudFormation template dict.

    Raises ModuleNotFoundError when the stack module does not yet exist (T2.1
    pending).  Callers catch this and call pytest.fail() so tests show as FAILED,
    not ERROR.
    """
    global _template_cache
    if _template_cache is not None:
        return _template_cache

    from aws_cdk import App, Environment
    # This import raises ModuleNotFoundError until T2.1 creates the file.
    from stacks.connected_services_ui_stack import ConnectedServicesUiStack  # noqa: PLC0415

    os.environ["DEPLOYMENT_STAGE"] = _STAGE
    app = App(context=_SYNTH_CONTEXT)
    ConnectedServicesUiStack(
        app,
        _STACK_NAME,
        stage=_STAGE,
        env=Environment(account=_ACCOUNT, region=_REGION),
    )
    synth_result = app.synth()
    raw = synth_result.get_stack_by_name(_STACK_NAME).template
    _template_cache = json.loads(json.dumps(raw))
    return _template_cache


def _template() -> dict:
    """Return the synthesized template or fail the test with a clear message."""
    try:
        return _get_template()
    except ModuleNotFoundError:
        pytest.fail(
            "ConnectedServicesUiStack not found — "
            "deployment/stacks/connected_services_ui_stack.py does not exist yet. "
            "This is the expected red-phase failure: implement the stack in T2.1."
        )


# ---------------------------------------------------------------------------
# Helpers — mirror the pattern from the two existing stack test files
# ---------------------------------------------------------------------------

def _of_type(t: dict, resource_type: str) -> dict[str, dict]:
    return {k: v for k, v in t["Resources"].items() if v["Type"] == resource_type}


def _cloudfront_distributions(t: dict) -> dict[str, dict]:
    return _of_type(t, "AWS::CloudFront::Distribution")


def _s3_bucket_deployments(t: dict) -> dict[str, dict]:
    """Return Custom::CDKBucketDeployment resources (CDK BucketDeployment construct)."""
    return {
        k: v
        for k, v in t["Resources"].items()
        if v["Type"] == "Custom::CDKBucketDeployment"
    }


def _distributions_config(dist: dict) -> dict:
    return dist.get("Properties", {}).get("DistributionConfig", {})


def _custom_error_responses(dist: dict) -> list[dict]:
    return _distributions_config(dist).get("CustomErrorResponses", [])


# ---------------------------------------------------------------------------
# (a) SPA 403 → index.html fallback configured in CloudFront
# ---------------------------------------------------------------------------

class TestSpa403Fallback:
    """Item (a): OAC + private S3 bucket returns 403 for a missing key, not 404.

    Without a 403 → /index.html error response, every deep-link and browser
    refresh on a non-root SPA route returns a raw 403 XML response from S3
    rather than the application shell.  This is the first of the four DMS
    defects found by user UAT.

    Asserted against: CloudFront distribution CustomErrorResponses in the
    synthesized CloudFormation template.  NOT an HTTP-200 check.
    """

    def test_cloudfront_distribution_has_403_to_index_html_error_response(self) -> None:
        """CloudFront CustomErrorResponses must rewrite 403 → 200 /index.html."""
        t = _template()
        distributions = _cloudfront_distributions(t)
        assert distributions, "No AWS::CloudFront::Distribution resources found"

        found = False
        for logical_id, dist in distributions.items():
            for err in _custom_error_responses(dist):
                if (
                    err.get("ErrorCode") == 403
                    and err.get("ResponseCode") == 200
                    and err.get("ResponsePagePath") == "/index.html"
                ):
                    found = True
                    break

        assert found, (
            "No CloudFront CustomErrorResponse found with "
            "ErrorCode=403, ResponseCode=200, ResponsePagePath='/index.html'. "
            "OAC on a private S3 bucket returns 403 (not 404) for a missing key, "
            "so every SPA deep link will return a 403 XML error from S3 without "
            "this fallback.  This is defect (a) from the DMS extraction. "
            "See dms_ui_stack.py for the reference implementation."
        )


# ---------------------------------------------------------------------------
# (b) runtime-config.js is actually injected
# ---------------------------------------------------------------------------

class TestRuntimeConfigInjection:
    """Item (b): runtime-config.js must be deployed as a BucketDeployment.

    The DMS extraction's second escaped defect: the stack read the context
    values but never emitted the file.  Symptom: sign-in failed with
    "Value '' at 'clientId'" on a deployed site that looked correct at synth.

    Asserted against: Custom::CDKBucketDeployment resources in the synthesized
    CloudFormation template.  NOT an HTTP-200 check.
    """

    def test_runtime_config_js_bucket_deployment_is_present(self) -> None:
        """At least one BucketDeployment resource must exist for runtime-config.js."""
        t = _template()
        deployments = _s3_bucket_deployments(t)
        assert deployments, (
            "No Custom::CDKBucketDeployment resources found in the template. "
            "The stack must deploy runtime-config.js via BucketDeployment so the "
            "Cognito credentials and API endpoint are available at runtime. "
            "Without this, sign-in fails with empty clientId/userPoolId. "
            "This is defect (b) from the DMS extraction. "
            "See dms_ui_stack.py DmsRuntimeConfigDeployment for the reference."
        )

    def test_runtime_config_deployment_has_no_cache_header(self) -> None:
        """The runtime-config.js BucketDeployment must set a no-cache Cache-Control header."""
        t = _template()
        deployments = _s3_bucket_deployments(t)
        assert deployments, (
            "No Custom::CDKBucketDeployment resources found. "
            "Cannot verify no-cache header without a deployment resource."
        )

        found_no_cache_deployment = False
        for logical_id, deployment in deployments.items():
            props_str = json.dumps(deployment.get("Properties", {}))
            if "no-cache" in props_str or "no-store" in props_str:
                found_no_cache_deployment = True
                break

        assert found_no_cache_deployment, (
            "No BucketDeployment resource found with 'no-cache' or 'no-store' "
            "in its properties.  runtime-config.js must be deployed with "
            "cache_control=[CacheControl.from_string('no-cache, no-store, must-revalidate')] "
            "so the browser always revalidates it after a redeploy. "
            "See dms_ui_stack.py DmsRuntimeConfigDeployment for the reference."
        )


# ---------------------------------------------------------------------------
# (c) index.html no-cache; hashed assets long-cached
# ---------------------------------------------------------------------------

class TestCacheHeaderSplit:
    """Item (c): cache policy split between index.html and hashed assets.

    The DMS extraction's third escaped defect: index.html was long-cached,
    so after a redeploy users were stuck with the previous index.html for up
    to 24 hours.  Fix: separate BucketDeployments for index.html (no-cache)
    and hashed assets (immutable).

    Asserted against: multiple Custom::CDKBucketDeployment resources in the
    synthesized template.  NOT an HTTP-200 check.
    """

    def test_separate_deployments_exist_for_index_and_assets(self) -> None:
        """At least two BucketDeployment resources must exist: one no-cache, one long-cache."""
        t = _template()
        deployments = _s3_bucket_deployments(t)
        assert len(deployments) >= 2, (
            f"Expected at least 2 BucketDeployment resources (one no-cache for "
            f"index.html/runtime-config.js, one long-cache for hashed assets), "
            f"but found {len(deployments)}. "
            "CDK BucketDeployment applies the same cache-control to all sources, "
            "so separate deployments are required for different cache policies. "
            "This is defect (c) from the DMS extraction."
        )

    def test_long_cache_deployment_exists_for_hashed_assets(self) -> None:
        """At least one BucketDeployment must use a long-cache (immutable) policy for assets."""
        t = _template()
        deployments = _s3_bucket_deployments(t)
        assert deployments, "No BucketDeployment resources found."

        found_long_cache = False
        for logical_id, deployment in deployments.items():
            props_str = json.dumps(deployment.get("Properties", {}))
            if "immutable" in props_str or "max-age=31536000" in props_str or "max-age=2592000" in props_str:
                found_long_cache = True
                break

        assert found_long_cache, (
            "No BucketDeployment found with a long-cache policy (immutable or "
            "max-age >= 2592000). "
            "Hashed assets should be cached with 'public, max-age=31536000, immutable' "
            "or similar, so the browser does not re-fetch unchanged bundles. "
            "See dms_ui_stack.py DmsFrontendAssetsDeployment for the reference."
        )


# ---------------------------------------------------------------------------
# (d) Nav shell renders — default_root_object = index.html
# ---------------------------------------------------------------------------

class TestNavShellRendering:
    """Item (d): the nav shell renders via the distribution's default root object.

    The DMS extraction's fourth escaped defect: without default_root_object='index.html'
    a GET to / returns a 403 XML error (S3's response to a GET on the bucket root)
    rather than the application.  HTTP 200 could not detect this because CloudFront
    returned 200 for /index.html but the SPA routing shell was never reached.

    Asserted against: CloudFront DistributionConfig.DefaultRootObject in the
    synthesized CloudFormation template.  NOT an HTTP-200 check.
    """

    def test_distribution_default_root_object_is_index_html(self) -> None:
        """CloudFront distribution must have DefaultRootObject='index.html'."""
        t = _template()
        distributions = _cloudfront_distributions(t)
        assert distributions, "No AWS::CloudFront::Distribution resources found."

        found = False
        for logical_id, dist in distributions.items():
            config = _distributions_config(dist)
            if config.get("DefaultRootObject") == "index.html":
                found = True
                break

        assert found, (
            "No CloudFront distribution found with DefaultRootObject='index.html'. "
            "Without this, a GET to / returns a 403 XML error from S3 (the bucket root "
            "is not an object) rather than the application shell. "
            "The nav shell cannot render without this setting. "
            "This is defect (d) from the DMS extraction. "
            "Add default_root_object='index.html' to the Distribution constructor."
        )


# ---------------------------------------------------------------------------
# (e) / resolves and unknown path hits SPA catchall
# ---------------------------------------------------------------------------

class TestRouteResolution:
    """Item (e): / resolves via DefaultRootObject; unknown paths hit the SPA catchall.

    The DMS extraction's fifth escaped defect: HTTP 200 confirmed / was reachable,
    but the SPA router never loaded because the distribution had no 404 → index.html
    error response.  Any deep link or browser refresh on a non-root route returned
    a raw 404 XML response.

    Asserted against: CloudFront DistributionConfig in the synthesized template.
    NOT an HTTP-200 check.
    """

    def test_root_resolves_via_default_root_object(self) -> None:
        """/ resolves via DefaultRootObject='index.html'."""
        t = _template()
        distributions = _cloudfront_distributions(t)
        assert distributions, "No AWS::CloudFront::Distribution resources found."

        root_resolves = any(
            _distributions_config(dist).get("DefaultRootObject") == "index.html"
            for dist in distributions.values()
        )
        assert root_resolves, (
            "CloudFront distribution lacks DefaultRootObject='index.html'. "
            "The / route must resolve to the application shell without a redirect. "
            "See defect (e) from the DMS extraction."
        )

    def test_unknown_path_hits_spa_catchall_via_404_error_response(self) -> None:
        """Unknown paths must hit the SPA catchall via a 404 → index.html error response."""
        t = _template()
        distributions = _cloudfront_distributions(t)
        assert distributions, "No AWS::CloudFront::Distribution resources found."

        found_404_fallback = False
        for logical_id, dist in distributions.items():
            for err in _custom_error_responses(dist):
                if (
                    err.get("ErrorCode") == 404
                    and err.get("ResponseCode") == 200
                    and err.get("ResponsePagePath") == "/index.html"
                ):
                    found_404_fallback = True
                    break

        assert found_404_fallback, (
            "No CloudFront CustomErrorResponse with ErrorCode=404, ResponseCode=200, "
            "ResponsePagePath='/index.html'. "
            "Without this, any SPA route other than / returns a raw 404 XML error. "
            "This is the catchall portion of defect (e) from the DMS extraction."
        )


# ---------------------------------------------------------------------------
# (f) connectedServicesUiCallbackOrigin appears in the synthesized template
# ---------------------------------------------------------------------------

class TestCallbackOriginInTemplate:
    """Item (f): connectedServicesUiCallbackOrigin must appear in the synthesized template.

    Spec D3 and the tasks.md T1.5 constraint both require this to be asserted
    against the synthesized template, not merely against the stack's Python code.

    The historical lesson (2026-08-11 prod Federate outage; DMS residual ii):
    CDK accepts a context key that nothing passes, the stack synthesizes
    successfully with an empty value, and the Cognito callback URL is set to the
    CloudFront default or an empty string.  An assertion that the stack reads the
    context value is not sufficient — the value must appear in the CloudFormation
    template that gets deployed to the Cognito user pool.

    Test fixtures use an RFC 2606 .invalid placeholder; the real staging domain
    (<connected-services-domain>) is only in staging.env (publish-excluded).

    Asserted against: the full serialized CloudFormation template.
    NOT an HTTP-200 check.
    """

    def test_callback_origin_value_appears_in_synthesized_template(self) -> None:
        """The connectedServicesUiCallbackOrigin value must be present in the CFN template.

        The observable: whatever value is passed as _CALLBACK_ORIGIN_VALUE (an RFC 2606
        .invalid placeholder in test fixtures; the real staging domain only in staging.env)
        must appear somewhere in the serialized CloudFormation template.  This proves the
        value flows from the context key through the stack to a real resource property,
        not just to a Python local variable.
        """
        t = _template()
        template_str = json.dumps(t)
        assert _CALLBACK_ORIGIN_VALUE in template_str, (
            f"The connectedServicesUiCallbackOrigin value "
            f"({_CALLBACK_ORIGIN_VALUE!r}) does not appear anywhere in the "
            "synthesized CloudFormation template. "
            "The stack must consume the context value and embed it in at least one "
            "CFN resource (e.g., the Cognito callback URL list in ui_stack.py). "
            "An assertion that the stack reads the key is insufficient — the value "
            "must reach the template. "
            "This is defect (f), reproducing the 2026-08-11 prod Federate outage "
            "and DMS residual (ii). "
            "See spec D3 and decisions.md § 2026-09-03."
        )
