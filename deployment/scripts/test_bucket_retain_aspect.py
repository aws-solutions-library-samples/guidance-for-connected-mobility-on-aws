#!/usr/bin/env python3
"""Unit test for ``aspects.bucket_retain_aspect.BucketRetainAspect``.

Verifies the three documented behaviors:

1. Aspect FAILS synth on a stack containing an explicit-named bucket
   without ``RemovalPolicy.RETAIN`` (e.g. ``DESTROY``).
2. Aspect PASSES synth on a stack with an explicit-named bucket that has
   ``RemovalPolicy.RETAIN``.
3. Aspect IGNORES buckets without an explicit ``bucket_name`` (auto-generated
   names are not globally-namespaced for the purposes of this discipline).

Background: discipline codified in ``~/.kiro/steering/cross-region-namespace.md``
("Bucket RETAIN aspect" P3 backlog row, surfaced 2026-06-03 by FrontendBucket
spec architect handback). Aspect locks the invariant that L2 ``aws_s3.Bucket``
defaults to ``DeletionPolicy: Retain`` so a CDK-major upgrade flipping the
default cannot silently regress the portfolio.

Stdlib-only test (``unittest`` + plain CDK App synth). No pytest, no moto.
Returns exit code 0 on PASS, 1 on FAIL.

Run from ``deployment/``::

    source .venv/bin/activate
    python3 scripts/test_bucket_retain_aspect.py
"""
from __future__ import annotations

import os
import sys
import unittest

# Make ``deployment/`` importable so ``aspects.bucket_retain_aspect`` resolves
# regardless of cwd. Mirrors the pattern in ``test_preflight_global_namespace.py``.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEPLOYMENT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, DEPLOYMENT_DIR)

import aws_cdk as cdk  # noqa: E402
from aws_cdk import aws_s3 as s3  # noqa: E402

from aspects.bucket_retain_aspect import BucketRetainAspect  # noqa: E402


class BucketRetainAspectTest(unittest.TestCase):
    """Three-case truth table for ``BucketRetainAspect``."""

    def test_fails_synth_on_explicit_named_bucket_without_retain(self) -> None:
        """A bucket with explicit ``bucket_name=`` and ``DESTROY`` must
        cause synth to raise ``RuntimeError`` mentioning the construct path."""
        app = cdk.App()
        stack = cdk.Stack(app, "TestStackBad")
        s3.Bucket(
            stack,
            "BadBucket",
            bucket_name="test-explicit-name-destroy",
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        cdk.Aspects.of(app).add(BucketRetainAspect())

        with self.assertRaises(RuntimeError) as cm:
            app.synth()

        msg = str(cm.exception)
        # Surface the specific bucket so the operator can find it.
        self.assertIn("BadBucket", msg, f"error must name the offending bucket; got: {msg!r}")
        # Surface the discipline ref so the operator knows where to read.
        self.assertIn("RETAIN", msg.upper())

    def test_passes_synth_on_explicit_named_bucket_with_retain(self) -> None:
        """A bucket with explicit ``bucket_name=`` and ``RETAIN`` must synth
        without error."""
        app = cdk.App()
        stack = cdk.Stack(app, "TestStackGood")
        s3.Bucket(
            stack,
            "GoodBucket",
            bucket_name="test-explicit-name-retain",
            removal_policy=cdk.RemovalPolicy.RETAIN,
        )
        cdk.Aspects.of(app).add(BucketRetainAspect())

        # No exception expected.
        try:
            app.synth()
        except Exception as exc:  # pragma: no cover — failure path
            self.fail(f"Aspect must not raise on RETAIN-named bucket; got: {exc!r}")

    def test_ignores_buckets_without_explicit_name(self) -> None:
        """A bucket with auto-generated name (no ``bucket_name=``) must NOT
        be checked, regardless of removal_policy. Validates the
        'globally-namespaced' proxy used by the aspect."""
        app = cdk.App()
        stack = cdk.Stack(app, "TestStackAutogen")
        # Auto-generated name, but with DESTROY — aspect must skip.
        s3.Bucket(
            stack,
            "AutogenDestroyBucket",
            removal_policy=cdk.RemovalPolicy.DESTROY,
        )
        cdk.Aspects.of(app).add(BucketRetainAspect())

        try:
            app.synth()
        except Exception as exc:  # pragma: no cover — failure path
            self.fail(
                "Aspect must ignore auto-generated-name buckets even when "
                f"removal_policy=DESTROY; got: {exc!r}"
            )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(BucketRetainAspectTest)
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
