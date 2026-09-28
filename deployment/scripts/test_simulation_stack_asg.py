#!/usr/bin/env python3
"""Regression guard — FweASG in cms-staging-simulation MUST NOT set DesiredCapacity.

Bug context: spec ``2026-06-18-cms-fwe-decoder-manifest-bucket-resolution`` WS3.
An explicit ``desired_capacity=1`` in the CDK ``AutoScalingGroup`` construct
causes CloudFormation to reset the live ASG to 1 on every ``cdk deploy
cms-staging-simulation``. This shrinks the ASG and bounces any running FWE
agent tasks, silently killing telemetry.

Fix (Group 2): remove ``desired_capacity=1`` from the ``FweASG`` construct in
``deployment/stacks/simulation_stack.py`` (~L245). Managed scaling (via
``AsgCapacityProvider``) owns the live desired count; ``MinSize=1`` keeps at
least one warm instance without resetting it.

Synth prerequisite: this test reads the pre-synthesized template from
``cdk.out/cms-staging-simulation.template.json``. If absent, the test is skipped.
To produce the output::

    cd deployment && source .venv/bin/activate && cdk synth cms-staging-simulation

This test is RED until the Group 2 fix lands.
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

TEMPLATE_PATH = (
    Path(__file__).parent.parent / "cdk.out" / "cms-staging-simulation.template.json"
)


class FweAsgDesiredCapacityTest(unittest.TestCase):
    """Assert FweASG does not hard-reset DesiredCapacity on every deploy."""

    @classmethod
    def setUpClass(cls) -> None:
        if not TEMPLATE_PATH.exists():
            cls._template = None
            return
        resources = json.loads(TEMPLATE_PATH.read_text())["Resources"]
        cls._asgs = {
            k: v
            for k, v in resources.items()
            if v["Type"] == "AWS::AutoScaling::AutoScalingGroup"
            and k.startswith("FweASG")
        }

    def _skip_if_no_template(self) -> None:
        if not TEMPLATE_PATH.exists():
            self.skipTest(
                f"Synth output not found at {TEMPLATE_PATH}. "
                "Run `cdk synth cms-staging-simulation` from deployment/ first."
            )

    def test_fwe_asg_exists(self) -> None:
        """Sanity: at least one FweASG resource must be present."""
        self._skip_if_no_template()
        self.assertTrue(
            self._asgs,
            "No AWS::AutoScaling::AutoScalingGroup with logical ID starting 'FweASG' found — "
            "check that the stack still contains the FweASG construct.",
        )

    def test_fwe_asg_no_desired_capacity(self) -> None:
        """FweASG MUST NOT have DesiredCapacity in its synthesized template.

        An explicit DesiredCapacity causes CloudFormation to reset the live
        count on every deploy, shrinking the ASG and bouncing FWE tasks.
        Remove ``desired_capacity=1`` from the CDK construct; let managed
        scaling own the live count.

        RED until WS3 (Group 2) removes desired_capacity from simulation_stack.py.
        """
        self._skip_if_no_template()
        for logical_id, resource in self._asgs.items():
            props = resource.get("Properties", {})
            self.assertNotIn(
                "DesiredCapacity",
                props,
                f"{logical_id}: DesiredCapacity={props.get('DesiredCapacity')!r} is set — "
                "this resets the ASG on every deploy and will bounce FWE agents. "
                "Fix: remove desired_capacity=1 from FweASG in simulation_stack.py (~L245).",
            )

    def test_fwe_asg_min_size_at_least_one(self) -> None:
        """FweASG MinSize MUST be >= 1 to keep a warm instance available.

        Removing desired_capacity must not accidentally drop MinSize to 0.
        """
        self._skip_if_no_template()
        for logical_id, resource in self._asgs.items():
            props = resource.get("Properties", {})
            min_size = int(props.get("MinSize", 0))
            self.assertGreaterEqual(
                min_size,
                1,
                f"{logical_id}: MinSize={min_size!r} — must be >= 1 after removing "
                "desired_capacity so managed scaling always has a floor instance.",
            )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(
        unittest.TestLoader().loadTestsFromTestCase(FweAsgDesiredCapacityTest)
    )
    sys.exit(0 if result.wasSuccessful() else 1)
