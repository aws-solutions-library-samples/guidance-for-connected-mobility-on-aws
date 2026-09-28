"""Red-phase tests for EXTERNAL_UI_CALLBACK_CONTEXT_KEYS callback-URL registry.

Spec: .kiro/specs/2026-09-03-cms-auth-api-core-extraction/ (Group 1 Task 1.1)

These tests verify the behavior of the EXTERNAL_UI_CALLBACK_CONTEXT_KEYS registry in
UIStack.  All three tests must FAIL (NameError/ImportError on the constant) before T1.2
implements the registry — that is the expected red-phase outcome.

Three invariants tested against the synthesized AWS::Cognito::UserPoolClient:

  (a) No external context keys set → CallbackURLs / LogoutURLs are byte-identical to
      today's baseline (single CMS UI origin only).  This proves the registry is
      behavior-preserving for the default-unset case, which is what keeps
      test_client_idp_config.py::test_staging_still_gets_a_real_callback passing.

  (b) dmsUiCallbackOrigin context key set to https://example.cloudfront.net →
      the DMS callback appears in CallbackURLs and LogoutURLs, in order after the
      CMS UI origin.  This proves the registry folds the existing DMS block.

  (c) A registry entry carrying a non-https:// value → synth raises ValueError naming
      the offending context key.  This proves fail-closed behaviour is uniform across
      all registry entries, not special-cased for DMS.

Run with (from deployment/ directory):
    .venv/bin/python -m pytest stacks/tests/test_callback_url_registry.py -v

Decisions reference: decisions.md § "REVERSAL: extraction plan superseded"
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# sys.path setup — mirrors the pattern in test_connected_services_ui_stack.py
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent   # deployment/stacks/tests/
_STACKS = _HERE.parent                    # deployment/stacks/
_DEPLOYMENT = _STACKS.parent              # deployment/

for _dir in (str(_DEPLOYMENT), str(_STACKS)):
    if _dir not in sys.path:
        sys.path.insert(0, _dir)

# ---------------------------------------------------------------------------
# Synthetic constants — never real account IDs or internal hostnames
# ---------------------------------------------------------------------------

_STAGE = "test"          # in _GUARD_OPTIONAL_STAGES — guard returns early
_ACCOUNT = "123456789012"
_REGION = "us-west-2"
_UI_DOMAIN = "cms-test.example.invalid"
_DMS_ORIGIN = "https://d1234567890abc.cloudfront.net.example.invalid"


# ---------------------------------------------------------------------------
# Stack synthesis helpers
# ---------------------------------------------------------------------------

def _synth_ui_stack(context: dict[str, str]) -> dict:
    """Synthesise UIStack with the given CDK context and return the template dict."""
    from aws_cdk import App, Environment
    from stacks.ui_stack import UIStack, EXTERNAL_UI_CALLBACK_CONTEXT_KEYS  # noqa: F401 (red phase: import triggers failure)

    # DEPLOYMENT_STAGE="" is in _GUARD_OPTIONAL_STAGES; the IdP guard returns early.
    os.environ["DEPLOYMENT_STAGE"] = ""
    os.environ.setdefault("CLIENT_EXTRA_IDPS", "")
    os.environ.setdefault("FEDERATE_CLIENT_ID", "")
    os.environ.setdefault("FEDERATE_CLIENT_SECRET", "")
    os.environ.setdefault("FEDERATE_OIDC_ISSUER", "")
    os.environ.setdefault("COGNITO_DOMAIN_PREFIX", "")

    app = App(context=context)
    stack_name = f"cms-{_STAGE}-ui"
    UIStack(
        app,
        stack_name,
        env=Environment(account=_ACCOUNT, region=_REGION),
    )
    synth_result = app.synth()
    raw = synth_result.get_stack_by_name(stack_name).template
    return json.loads(json.dumps(raw))


def _pool_client(template: dict) -> dict:
    """Return the single AWS::Cognito::UserPoolClient properties from the template."""
    clients = [
        r["Properties"]
        for r in template["Resources"].values()
        if r["Type"] == "AWS::Cognito::UserPoolClient"
    ]
    assert len(clients) == 1, f"expected one UserPoolClient, found {len(clients)}"
    return clients[0]


# ---------------------------------------------------------------------------
# (a) No external context keys set → byte-identical to today's baseline
# ---------------------------------------------------------------------------

class TestNoExternalOrigins:
    """Registry with no context values set → CallbackURLs / LogoutURLs unchanged.

    This is the byte-identical guarantee for the default-unset case. The existing
    test_client_idp_config.py::test_staging_still_gets_a_real_callback pins
    CallbackURLs as a single-item exact-equality assertion; this test proves the
    same invariant at the unit level using a synthetic stage.
    """

    def test_callback_urls_contain_only_cms_ui_origin(self) -> None:
        """When no registry entry is set, only the CMS UI origin appears."""
        context = {
            "uiCustomDomain": _UI_DOMAIN,
            "uiCustomDomainCertArn": "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000",
            "uiCustomDomainRegion": "us-east-1",
            "uiCustomDomainManageDns": "false",
            "cms.allow_unauth_map_auth": "true",
        }
        template = _synth_ui_stack(context)
        client = _pool_client(template)
        assert client["CallbackURLs"] == [f"https://{_UI_DOMAIN}/auth/callback"], (
            f"expected single CMS callback URL, got: {client['CallbackURLs']}"
        )

    def test_logout_urls_contain_only_cms_ui_origin(self) -> None:
        """When no registry entry is set, only the CMS UI origin appears in logout."""
        context = {
            "uiCustomDomain": _UI_DOMAIN,
            "uiCustomDomainCertArn": "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000",
            "uiCustomDomainRegion": "us-east-1",
            "uiCustomDomainManageDns": "false",
            "cms.allow_unauth_map_auth": "true",
        }
        template = _synth_ui_stack(context)
        client = _pool_client(template)
        assert client["LogoutURLs"] == [f"https://{_UI_DOMAIN}/"], (
            f"expected single CMS logout URL, got: {client['LogoutURLs']}"
        )


# ---------------------------------------------------------------------------
# (b) dmsUiCallbackOrigin set → DMS callback appears in CallbackURLs / LogoutURLs
# ---------------------------------------------------------------------------

class TestDmsOriginInRegistry:
    """Registry fold: dmsUiCallbackOrigin context key set → DMS URLs appended."""

    def test_dms_callback_url_appended_after_cms(self) -> None:
        """DMS callback URL appears in CallbackURLs after the CMS UI origin."""
        context = {
            "uiCustomDomain": _UI_DOMAIN,
            "uiCustomDomainCertArn": "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000",
            "uiCustomDomainRegion": "us-east-1",
            "uiCustomDomainManageDns": "false",
            "cms.allow_unauth_map_auth": "true",
            "dmsUiCallbackOrigin": _DMS_ORIGIN,
        }
        template = _synth_ui_stack(context)
        client = _pool_client(template)
        assert f"{_DMS_ORIGIN}/auth/callback" in client["CallbackURLs"], (
            f"DMS callback URL missing: {client['CallbackURLs']}"
        )
        # Order: CMS first, DMS second (registry order)
        assert client["CallbackURLs"][0] == f"https://{_UI_DOMAIN}/auth/callback", (
            f"CMS origin must be first in CallbackURLs: {client['CallbackURLs']}"
        )
        assert client["CallbackURLs"][1] == f"{_DMS_ORIGIN}/auth/callback", (
            f"DMS origin must be second in CallbackURLs: {client['CallbackURLs']}"
        )

    def test_dms_logout_url_appended_after_cms(self) -> None:
        """DMS logout URL appears in LogoutURLs after the CMS UI origin."""
        context = {
            "uiCustomDomain": _UI_DOMAIN,
            "uiCustomDomainCertArn": "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000",
            "uiCustomDomainRegion": "us-east-1",
            "uiCustomDomainManageDns": "false",
            "cms.allow_unauth_map_auth": "true",
            "dmsUiCallbackOrigin": _DMS_ORIGIN,
        }
        template = _synth_ui_stack(context)
        client = _pool_client(template)
        assert f"{_DMS_ORIGIN}/" in client["LogoutURLs"], (
            f"DMS logout URL missing: {client['LogoutURLs']}"
        )
        assert client["LogoutURLs"][0] == f"https://{_UI_DOMAIN}/", (
            f"CMS origin must be first in LogoutURLs: {client['LogoutURLs']}"
        )
        assert client["LogoutURLs"][1] == f"{_DMS_ORIGIN}/", (
            f"DMS origin must be second in LogoutURLs: {client['LogoutURLs']}"
        )


# ---------------------------------------------------------------------------
# (c) Non-https:// registry entry → ValueError at synth
# ---------------------------------------------------------------------------

class TestRegistryFailsClosedOnNonHttps:
    """Fail-closed behaviour is uniform across all registry entries.

    The https:// check that existed on the old DMS-specific block must apply to
    every entry in the registry, not just DMS. This ensures a future frontend
    onboarding cannot accidentally register an http:// origin.
    """

    def test_http_origin_raises_value_error(self) -> None:
        """A non-https:// dmsUiCallbackOrigin value raises ValueError at synth."""
        context = {
            "uiCustomDomain": _UI_DOMAIN,
            "uiCustomDomainCertArn": "arn:aws:acm:us-east-1:123456789012:certificate/00000000-0000-0000-0000-000000000000",
            "uiCustomDomainRegion": "us-east-1",
            "uiCustomDomainManageDns": "false",
            "cms.allow_unauth_map_auth": "true",
            "dmsUiCallbackOrigin": "http://insecure.example.invalid",
        }
        with pytest.raises(ValueError) as exc:
            _synth_ui_stack(context)
        assert "dmsUiCallbackOrigin" in str(exc.value), (
            f"ValueError must name the offending context key; got: {exc.value}"
        )
