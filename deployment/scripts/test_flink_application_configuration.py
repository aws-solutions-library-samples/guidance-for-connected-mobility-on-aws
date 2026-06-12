#!/usr/bin/env python3
"""Regression assertion — every CfnApplication in cms-staging-flink MUST
synthesize with a non-empty ApplicationConfiguration containing the three
required sub-keys (ApplicationCodeConfiguration, FlinkApplicationConfiguration,
EnvironmentProperties).

Bug context: spec ``2026-06-08-cms-flink-cfn-config-keys-fix``.
Pre-fix synth produced ``ApplicationConfiguration: {}`` for every Flink app
because ``create_flink_app_config()`` returned a dict with PascalCase CFN-spec
keys (e.g. ``ApplicationCodeConfiguration``). CDK's jsii silently drops keys
that don't match typed-property snake_case attributes, producing an empty
``ApplicationConfiguration`` in the synthesized template.

Canonical post-fix construction pattern: ``docs/tech.md § (c.1) AWS Kinesis
Analytics V2`` — always use typed ``ka.CfnApplication.ApplicationConfigurationProperty``
objects, never raw dicts with PascalCase keys.

Synth prerequisite: this test reads a pre-synthesized template from
``cdk.out/cms-staging-flink.template.json``. If absent, the test skips with
a clear message. To produce the output, from the ``deployment/`` directory:

    source .venv/bin/activate
    cdk synth cms-staging-flink -o ./cdk.out

Note: ``cdk synth`` requires valid AWS credentials and CDK context. In CI,
run via ``make synth-and-test`` (or equivalent) so synthesis happens before
this test.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REQUIRED_SUBKEYS = {
    "ApplicationCodeConfiguration",
    "FlinkApplicationConfiguration",
    "EnvironmentProperties",
}

TEMPLATE_PATH = (
    Path(__file__).parent.parent / "cdk.out" / "cms-staging-flink.template.json"
)


class FlinkApplicationConfigurationTest(unittest.TestCase):
    """Assert all Flink apps in cms-staging-flink have a non-empty
    ApplicationConfiguration with the three mandatory sub-keys."""

    @classmethod
    def setUpClass(cls) -> None:
        if not TEMPLATE_PATH.exists():
            return
        cls.flink_apps: dict = {
            name: r
            for name, r in json.loads(TEMPLATE_PATH.read_text())["Resources"].items()
            if r["Type"] == "AWS::KinesisAnalyticsV2::Application"
        }

    def _skip_if_no_template(self) -> None:
        if not TEMPLATE_PATH.exists():
            self.skipTest(
                f"Synth output not found at {TEMPLATE_PATH}. "
                "Run `cdk synth cms-staging-flink -o ./cdk.out` from deployment/ first."
            )

    def test_flink_app_count(self) -> None:
        """Stack must contain >= 9 Flink applications."""
        self._skip_if_no_template()
        self.assertGreaterEqual(
            len(self.flink_apps),
            9,
            f"Expected >= 9 AWS::KinesisAnalyticsV2::Application resources; "
            f"found {len(self.flink_apps)}: {list(self.flink_apps)}",
        )

    def test_no_empty_application_configuration(self) -> None:
        """Every Flink app must have a non-empty ApplicationConfiguration."""
        self._skip_if_no_template()
        failures: list[str] = []
        for name, resource in self.flink_apps.items():
            ac = resource["Properties"].get("ApplicationConfiguration", {})
            if not ac:
                failures.append(f"{name}: ApplicationConfiguration is empty (got {ac!r})")
        if failures:
            self.fail(
                "Pre-fix regression detected — ApplicationConfiguration is empty for:\n"
                + "\n".join(f"  {f}" for f in failures)
            )

    def test_required_subkeys_present(self) -> None:
        """Every Flink app's ApplicationConfiguration must have all three
        required sub-keys: ApplicationCodeConfiguration, FlinkApplicationConfiguration,
        EnvironmentProperties."""
        self._skip_if_no_template()
        failures: list[str] = []
        for name, resource in self.flink_apps.items():
            ac = resource["Properties"].get("ApplicationConfiguration", {})
            missing = REQUIRED_SUBKEYS - set(ac.keys())
            if missing:
                failures.append(f"{name}: missing sub-keys {sorted(missing)}")
        if failures:
            self.fail(
                "ApplicationConfiguration missing required sub-keys for:\n"
                + "\n".join(f"  {f}" for f in failures)
            )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.TestLoader().loadTestsFromTestCase(FlinkApplicationConfigurationTest)
    )
    sys.exit(0 if result.wasSuccessful() else 1)
