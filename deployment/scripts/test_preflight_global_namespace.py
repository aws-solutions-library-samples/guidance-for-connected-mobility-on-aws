#!/usr/bin/env python3
"""Regression test for preflight S3 global-namespace warning fix.

Verifies that ``preflight_region_clean.check_s3_buckets``:

1. Counts ``cms-{stage}-*`` buckets physically resident in the
   target region as REGIONAL BLOCKERS (returns count → contributes
   to overall preflight blocker count, can be auto-cleaned).
2. Surfaces ``cms-{stage}-*`` buckets resident in OTHER regions as
   GLOBAL-NAMESPACE WARNINGS (does NOT contribute to the returned
   blocker count, does NOT auto-clean — but DOES print the warning
   so the operator sees the cross-region collision risk).

Bug context: clean-deploy run 5 (2026-06-03T15-17-33Z) against
ap-northeast-1 PASSed preflight_account but FAILed deploy_all on
``cms-staging-storage-service-invoices already exists``. The bucket
was alive in us-west-2 (live staging deployment); preflight's
``if loc == region`` filter correctly skipped it for "regional
orphan" purposes but failed to surface that the storage stack's
globally-named bucket would collide on deploy.

The storage-stack defect was fixed in spec
``2026-06-03-cms-storage-bucket-region-suffix`` (storage_stack.py
ServiceInvoiceBucket initially suffixed ``-{region}-{account}``;
subsequently shortened to ``cms-{stage}-storage-invoices-{region}-{account}``
in fix ``2026-06-03-storage-bucket-name-too-long-ap-northeast-1``
to fit the 63-char DNS limit in 14-char regions). The UI
FrontendBucket and VFO knowledge-base buckets were similarly
suffixed in
``2026-06-03-cms-ui-frontend-bucket-region-suffix`` and
``2026-06-04-cms-vfo-kb-bucket-region-suffix``. This regression
test is retained as the abstract pattern still applies to any
future cms-{stage}-* bucket regression. Test fixtures use
synthetic defect-shaped names (``cms-staging-legacy-fixture-bucket``)
to keep the regression check meaningful without referencing any
live defect.

See: ``issues/2026-06-03-clean-deploy-coverage-step-back/``,
``.kiro/specs/2026-06-03-cms-storage-bucket-region-suffix/spec.md``
(Risk #8 documents the remaining defect),
and ``deployment/scripts/preflight_region_clean.py``.

This is a stdlib-only test (uses ``unittest.mock``); no pytest, no
moto. Returns exit code 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import io
import os
import sys
import unittest
from contextlib import redirect_stdout
from unittest.mock import MagicMock, patch

# Make `deployment/scripts/` importable when running from anywhere.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

import preflight_region_clean  # noqa: E402


class TestPreflightS3GlobalNamespaceWarning(unittest.TestCase):
    """Regression: preflight must surface cross-region buckets that
    would cause a global-namespace deploy collision."""

    def _make_s3_mock(self, buckets_with_locations: list[tuple[str, str]]) -> MagicMock:
        """Build a mock S3 client that returns the given (name, region)
        pairs from list_buckets and reports locations via get_bucket_location."""
        s3 = MagicMock()
        s3.list_buckets.return_value = {
            "Buckets": [{"Name": name} for name, _ in buckets_with_locations],
        }

        def get_bucket_location_side_effect(Bucket: str):  # noqa: N803
            for name, loc in buckets_with_locations:
                if name == Bucket:
                    # Mimic real API: us-east-1 returns None for
                    # LocationConstraint; other regions return the region.
                    return {
                        "LocationConstraint": loc if loc != "us-east-1" else None,
                    }
            raise AssertionError(f"unexpected bucket lookup: {Bucket}")

        s3.get_bucket_location.side_effect = get_bucket_location_side_effect
        return s3

    def test_cross_region_bucket_is_warning_not_blocker(self) -> None:
        """A cms-{stage}-* bucket alive in us-west-2 must surface as a
        global-namespace warning (NOT a regional blocker) when preflight
        runs against ap-northeast-1. Fixture uses a SYNTHETIC defect-shaped
        name (``cms-staging-legacy-fixture-bucket``) since all known
        live buckets are now CDK-owned and region-suffixed (per specs
        2026-06-03-cms-storage-bucket-region-suffix,
        2026-06-03-cms-ui-frontend-bucket-region-suffix,
        2026-06-04-cms-vfo-kb-bucket-region-suffix)."""
        s3 = self._make_s3_mock([
            ("cms-staging-legacy-fixture-bucket", "us-west-2"),
            ("cms-staging-storage-datalakebucket-x", "us-west-2"),
            ("unrelated-bucket", "ap-northeast-1"),  # different prefix
        ])

        out = io.StringIO()
        with patch("preflight_region_clean.boto3.client", return_value=s3), \
             redirect_stdout(out):
            blockers = preflight_region_clean.check_s3_buckets(
                region="ap-northeast-1", stage="staging", auto_clean=False
            )

        text = out.getvalue()

        # Assertion 1: returned blocker count is 0 — these are
        # cross-region warnings, not regional blockers.
        self.assertEqual(
            blockers, 0,
            f"REGRESSION: cross-region buckets must NOT contribute to "
            f"the regional-blocker count. Got {blockers}; expected 0. "
            f"\nOutput:\n{text}",
        )

        # Assertion 2: BOTH cms-staging-* cross-region buckets appear
        # in the global-namespace warning section.
        self.assertIn("cms-staging-legacy-fixture-bucket", text,
                      "REGRESSION: missed cross-region bucket in warning")
        self.assertIn("cms-staging-storage-datalakebucket-x", text,
                      "REGRESSION: missed cross-region bucket in warning")
        self.assertIn("us-west-2", text,
                      "REGRESSION: warning must report the bucket's region")

        # Assertion 3: warning explanation mentions global-namespace
        # collision risk so the operator understands why this is shown.
        self.assertIn("GLOBAL", text.upper(),
                      "REGRESSION: warning must explain S3 names are global")
        self.assertIn("collide", text.lower(),
                      "REGRESSION: warning must explain collision risk")

        # Assertion 4: the unrelated-bucket (different prefix) was
        # NOT surfaced.
        self.assertNotIn("unrelated-bucket", text)

    def test_regional_orphan_still_counts_as_blocker(self) -> None:
        """A bucket resident in the target region (true regional
        orphan) must still be counted as a blocker — the new fix must
        not accidentally weaken the existing regional check."""
        s3 = self._make_s3_mock([
            ("cms-staging-leftover-orphan", "ap-northeast-1"),
        ])

        out = io.StringIO()
        with patch("preflight_region_clean.boto3.client", return_value=s3), \
             redirect_stdout(out):
            blockers = preflight_region_clean.check_s3_buckets(
                region="ap-northeast-1", stage="staging", auto_clean=False
            )

        self.assertEqual(
            blockers, 1,
            f"REGRESSION: regional orphan must still count as blocker. "
            f"\nOutput:\n{out.getvalue()}",
        )
        self.assertIn("cms-staging-leftover-orphan", out.getvalue())

    def test_clean_region_returns_zero_no_warnings(self) -> None:
        """Empty case: no cms-{stage}-* buckets anywhere → 0 blockers,
        no warning section emitted."""
        s3 = self._make_s3_mock([
            ("unrelated-bucket-1", "us-east-1"),
            ("unrelated-bucket-2", "ap-northeast-1"),
        ])

        out = io.StringIO()
        with patch("preflight_region_clean.boto3.client", return_value=s3), \
             redirect_stdout(out):
            blockers = preflight_region_clean.check_s3_buckets(
                region="ap-northeast-1", stage="staging", auto_clean=False
            )

        self.assertEqual(blockers, 0)
        text = out.getvalue()
        # Clean line is printed (the existing ok() branch).
        self.assertIn("S3 buckets in ap-northeast-1", text)
        # No global-namespace warning section.
        self.assertNotIn("OTHER regions", text)

    def test_mixed_regional_and_cross_region(self) -> None:
        """Both kinds at once: regional blocker counted, cross-region
        surfaced as warning, totals correct."""
        s3 = self._make_s3_mock([
            ("cms-staging-leftover-in-target", "ap-northeast-1"),  # blocker
            ("cms-staging-legacy-fixture-bucket", "us-west-2"),  # warning
        ])

        out = io.StringIO()
        with patch("preflight_region_clean.boto3.client", return_value=s3), \
             redirect_stdout(out):
            blockers = preflight_region_clean.check_s3_buckets(
                region="ap-northeast-1", stage="staging", auto_clean=False
            )

        self.assertEqual(blockers, 1, "exactly one regional blocker")
        text = out.getvalue()
        self.assertIn("cms-staging-leftover-in-target", text)
        self.assertIn("cms-staging-legacy-fixture-bucket", text)
        self.assertIn("us-west-2", text)


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(
        TestPreflightS3GlobalNamespaceWarning
    )
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
