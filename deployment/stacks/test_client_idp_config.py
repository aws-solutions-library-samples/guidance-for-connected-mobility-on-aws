"""Guard: the app client must ship a usable federated sign-in configuration.

Regression suite for the 2026-08-11 prod outage,
`issues/2026-08-11-prod-federate-client-config-reset/`. A `cms-prod-ui` deploy reset
`SupportedIdentityProviders` to `["COGNITO"]` and `CallbackURLs` to aws-cdk-lib's
`["https://example.com"]` placeholder, and all 9 prod platform-admins lost Federate
sign-in. Nothing failed at synth, nothing failed at deploy, the stack reported
`UPDATE_COMPLETE`, and the only signal in the entire system was a human clicking the button
and seeing a generic Hosted UI error page.

Both halves are asserted against the synthesised template, because both were properties of
the template rather than of the deploy:

* the client permits the federated provider the stage declares;
* the callback URL is the one the SPA actually sends — `<origin>/auth/callback`,
  `SimpleAuthProvider.tsx:386` — and never the CDK placeholder.

There is also a test for a bug this fix introduced and the template caught: the
`cognito-only` sentinel leaking into the provider list, which would have made staging
permit a provider that does not exist.

Run with:
  cd deployment && .venv/bin/python -m pytest stacks/test_client_idp_config.py -v
"""
from __future__ import annotations

import json
import os
import subprocess
import tempfile

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_DEPLOYMENT = os.path.abspath(os.path.join(_HERE, os.pardir))

# The placeholder aws-cdk-lib supplies when `o_auth` is left unset. Its presence in a
# domain-bearing stage's template is the defect, not a cosmetic issue.
_CDK_PLACEHOLDER_CALLBACK = "https://example.com"


def _stage_env(stage: str) -> dict:
    env = dict(os.environ)
    cfg = os.path.join(_DEPLOYMENT, "config", f"{stage}.env")
    if os.path.isfile(cfg):
        with open(cfg) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.replace("_", "").isalnum():
                    env[k] = v
    return env


def _synth_client(stage: str, *extra_context: str, env_overrides: dict | None = None) -> dict:
    """Synthesise the stage's UI stack and return its UserPoolClient properties."""
    env = _stage_env(stage)
    env.update(env_overrides or {})
    with tempfile.TemporaryDirectory(prefix=f"cdkout-idp-{stage}-") as out_dir:
        args = [
            "npx", "cdk", "synth", f"cms-{stage}-ui", "--json", "--output", out_dir,
            "-c", f"uiCustomDomain={env.get('UI_CUSTOM_DOMAIN','')}",
            "-c", f"uiCustomDomainCertArn={env.get('UI_CUSTOM_DOMAIN_CERT_ARN','')}",
            "-c", f"uiCustomDomainRegion={env.get('UI_CUSTOM_DOMAIN_REGION','')}",
            "-c", "uiCustomDomainManageDns=false",
            "-c", "cms.allow_unauth_map_auth=true",
            *extra_context,
        ]
        proc = subprocess.run(
            args, cwd=_DEPLOYMENT, env=env, capture_output=True, text=True, timeout=900
        )
    if proc.returncode != 0:
        raise AssertionError(f"cdk synth failed for {stage}:\n{proc.stderr[-1500:]}")
    template = json.loads(proc.stdout[proc.stdout.find("{"):])
    clients = [
        r["Properties"]
        for r in template["Resources"].values()
        if r["Type"] == "AWS::Cognito::UserPoolClient"
    ]
    assert len(clients) == 1, f"expected one app client, found {len(clients)}"
    return clients[0]


@pytest.fixture(scope="module")
def prod_client() -> dict:
    return _synth_client(
        "prod",
        "-c", "cms.enable_internal_auto_provisioning=true",
        "-c", "wafWebAclArn=arn:aws:wafv2:us-east-1:111111111111:global/webacl/x/y",
    )


@pytest.fixture(scope="module")
def staging_client() -> dict:
    return _synth_client("staging", "-c", "cms.allow_self_signup=true")


class TestProdPermitsFederate:
    def test_federate_is_permitted(self, prod_client) -> None:
        """The exact field the outage reset. `["COGNITO"]` alone is the broken state."""
        idps = prod_client["SupportedIdentityProviders"]
        assert "AmazonFederate" in idps, (
            f"prod client would permit only {idps} — deploying that removes Federate "
            f"sign-in for all platform-admins, which is the 2026-08-11 outage."
        )
        assert "COGNITO" in idps, "password sign-in must remain available"

    def test_callback_matches_what_the_spa_sends(self, prod_client) -> None:
        """`SimpleAuthProvider.tsx` sends `${window.location.origin}/auth/callback`.

        The expected host is read from the publish-excluded stage config rather than
        written here: an earlier revision hardcoded it and tripped the publish scanner
        (`internal_awsi`, critical) in a file that ships. Deriving it also means the test
        cannot disagree with the value the stack is built from.
        """
        domain = _stage_env("prod")["UI_CUSTOM_DOMAIN"]
        assert prod_client["CallbackURLs"] == [
            f"https://{domain}/auth/callback"
        ], prod_client["CallbackURLs"]

    def test_no_placeholder_callback(self, prod_client) -> None:
        assert _CDK_PLACEHOLDER_CALLBACK not in prod_client["CallbackURLs"]


class TestStagingCognitoOnlySentinel:
    def test_sentinel_does_not_become_a_provider_name(self, staging_client) -> None:
        """The bug this fix introduced, caught by the template.

        `cognito-only` means "no federated provider here". An earlier revision skipped it
        in the guard but not in the list construction, so staging synthesised
        `['COGNITO', 'cognito-only']` — a client permitting a provider that does not
        exist, i.e. the same broken sign-in this change exists to prevent.
        """
        idps = staging_client["SupportedIdentityProviders"]
        assert idps == ["COGNITO"], (
            f"the cognito-only sentinel leaked into the provider list: {idps}"
        )

    def test_staging_still_gets_a_real_callback(self, staging_client) -> None:
        """The sentinel opts out of federation, not out of a correct callback URL."""
        domain = _stage_env("staging")["UI_CUSTOM_DOMAIN"]
        assert staging_client["CallbackURLs"] == [
            f"https://{domain}/auth/callback"
        ], staging_client["CallbackURLs"]
        assert _CDK_PLACEHOLDER_CALLBACK not in staging_client["CallbackURLs"]


class TestGuardRefusesTheBrokenConfiguration:
    def test_domain_without_any_federated_idp_fails_synth(self) -> None:
        """The deploy that caused the outage must now be impossible.

        With `CLIENT_EXTRA_IDPS` empty and no Federate credentials — the exact state of
        every normal prod deploy before this fix — synth must abort rather than produce a
        client with `["COGNITO"]`.
        """
        with pytest.raises(AssertionError) as exc:
            _synth_client(
                "prod",
                "-c", "wafWebAclArn=arn:aws:wafv2:us-east-1:111111111111:global/webacl/x/y",
                env_overrides={
                    "CLIENT_EXTRA_IDPS": "",
                    "FEDERATE_CLIENT_ID": "",
                    "FEDERATE_CLIENT_SECRET": "",
                },
            )
        assert "no federated identity provider" in str(exc.value), str(exc.value)[-600:]

    def test_federate_in_extra_without_creds_fails_synth(self) -> None:
        """The 2026-08-30 outage class: CLIENT_EXTRA_IDPS trusts AmazonFederate,
        but the creds that make CDK actually manage the IdP resource are absent.

        Reproduces the pattern that happened live 2026-08-30:
        `make staging-deploy` chained through `phase1` (creds loaded → IdP synthesised)
        then into `data-processing` which re-synths `cms-staging-ui` as a CDK
        dependency stack WITHOUT re-loading Federate creds. `_federate_creds_present`
        went False for the second synth, the AmazonFederateIdP resource fell out
        of the template, and CloudFormation deleted it.

        The guard's prior "CLIENT_EXTRA_IDPS satisfies it" path let this pass —
        the client's SupportedIdentityProviders kept AmazonFederate, but the pool
        no longer had an AmazonFederate IdP to satisfy the client's trust.
        """
        with pytest.raises(AssertionError) as exc:
            _synth_client(
                "prod",
                "-c", "wafWebAclArn=arn:aws:wafv2:us-east-1:111111111111:global/webacl/x/y",
                env_overrides={
                    "CLIENT_EXTRA_IDPS": "AmazonFederate",
                    "FEDERATE_CLIENT_ID": "",
                    "FEDERATE_CLIENT_SECRET": "",
                },
            )
        msg = str(exc.value)
        assert "'AmazonFederate'" in msg, msg[-600:]
        assert "FEDERATE_CLIENT_ID" in msg, msg[-600:]
        assert "2026-08-11" in msg or "2026-08-30" in msg, msg[-600:]

    def test_federate_in_extra_with_creds_still_passes(self) -> None:
        """The intended path: CLIENT_EXTRA_IDPS=AmazonFederate + creds present.

        This is what every properly-configured deploy looks like. The new guard
        rule must not block this case — only the credless variant.
        """
        client = _synth_client(
            "staging",
            "-c", "cms.allow_self_signup=true",
            env_overrides={
                "CLIENT_EXTRA_IDPS": "AmazonFederate",
                "FEDERATE_CLIENT_ID": "test-client-id",
                "FEDERATE_CLIENT_SECRET": "test-client-secret",
                "FEDERATE_OIDC_ISSUER": "https://idp-integ.federate.amazon.com",
                "COGNITO_DOMAIN_PREFIX": "cms-test-prefix",
            },
        )
        assert "AmazonFederate" in client["SupportedIdentityProviders"]
