"""Unit tests for the UI custom-domain alias synth-time guard.

Pure-function tests (no CDK/jsii) covering the decision branches for both the
staging and prod home regions.
"""

import pytest

from aspects.domain_alias_guard import enforce_ui_domain_alias

STAGING_DOMAIN = "staging.fleet.example.com"
STAGING_HOME = "us-west-2"
PROD_DOMAIN = "fleet.example.com"
PROD_HOME = "us-east-1"


def _call(region, attached, expected_alias, home_region):
    enforce_ui_domain_alias(
        region=region,
        ui_custom_domain_attached=attached,
        expected_alias=expected_alias,
        home_region=home_region,
    )


def test_staging_home_attached_passes():
    _call(STAGING_HOME, True, STAGING_DOMAIN, STAGING_HOME)


def test_staging_home_absent_raises():
    with pytest.raises(RuntimeError) as exc:
        _call(STAGING_HOME, False, STAGING_DOMAIN, STAGING_HOME)
    msg = str(exc.value)
    assert STAGING_DOMAIN in msg
    assert "uiCustomDomain" in msg


def test_prod_home_attached_passes():
    _call(PROD_HOME, True, PROD_DOMAIN, PROD_HOME)


def test_prod_home_absent_raises():
    with pytest.raises(RuntimeError) as exc:
        _call(PROD_HOME, False, PROD_DOMAIN, PROD_HOME)
    assert PROD_DOMAIN in str(exc.value)


def test_cross_region_absent_no_raise():
    # Region != home (e.g. a cross-region clean-deploy) intentionally skips the
    # alias -> guard must NOT false-fail, for either stage's expectation.
    _call("ap-northeast-1", False, STAGING_DOMAIN, STAGING_HOME)
    _call("us-west-2", False, PROD_DOMAIN, PROD_HOME)
