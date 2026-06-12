#!/usr/bin/env python3
"""Regression assertion — globally-named CMS S3 buckets MUST stay
within S3's 63-char DNS-compliant limit across every current AWS
region.

Bug context: the morning fix
``2026-06-03-cms-storage-bucket-region-suffix`` (commits
``e9e4653``, ``ce1040e``) suffixed ``ServiceInvoiceBucket`` with
``-{region}-{account}`` to fix a cross-region collision, but
landed on the pattern
``cms-{stage}-storage-service-invoices-{region}-{account}``,
which expands to **64 chars** in 14-char regions
(``ap-northeast-1`` etc.) — overshooting S3's 63-char limit by 1.
CDK rejects the stack at app-instantiation time before any AWS
round-trip; synth never runs.

The fix in ``2026-06-03-storage-bucket-name-too-long-ap-northeast-1``
dropped the ``-service-`` infix, yielding
``cms-{stage}-storage-invoices-{region}-{account}`` (56 chars worst
case). This test asserts the property: for every current AWS
region, the bucket-name f-string used in
``deployment/stacks/storage_stack.py`` evaluates to ≤63 chars.

Why a hardcoded region list (not boto3.Session().get_available_regions("s3")):
- Determinism — test result must not depend on the boto3 endpoints
  package version pinned in the CI image.
- Future-proof — if AWS adds a new region with a longer name,
  someone must update this list and re-run the math, which is
  exactly the reviewer touch-point we want.

This is a stdlib-only test. Returns 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import sys
import unittest


# Hardcoded list of CURRENT AWS regions as of 2026-06-03. Update
# only when AWS adds a new region. Source: AWS Knowledge MCP
# `list_regions` (matched against the public AWS regional services
# list). 14 chars is the longest current region name (ap-northeast-1
# etc.; eusc-de-east-1 in the European Sovereign Cloud).
ALL_REGIONS: list[str] = [
    "af-south-1",
    "ap-east-1",
    "ap-east-2",
    "ap-northeast-1",
    "ap-northeast-2",
    "ap-northeast-3",
    "ap-south-1",
    "ap-south-2",
    "ap-southeast-1",
    "ap-southeast-2",
    "ap-southeast-3",
    "ap-southeast-4",
    "ap-southeast-5",
    "ap-southeast-6",
    "ap-southeast-7",
    "ca-central-1",
    "ca-west-1",
    "eu-central-1",
    "eu-central-2",
    "eu-north-1",
    "eu-south-1",
    "eu-south-2",
    "eu-west-1",
    "eu-west-2",
    "eu-west-3",
    "eusc-de-east-1",
    "il-central-1",
    "me-central-1",
    "me-south-1",
    "mx-central-1",
    "sa-east-1",
    "us-east-1",
    "us-east-2",
    "us-gov-east-1",
    "us-gov-west-1",
    "us-west-1",
    "us-west-2",
]

# CMS deployment stages. ``staging`` (7 chars) is the longest in
# active use, so it dominates the worst-case math; ``dev`` and
# ``prod`` are shorter and trivially fit. We test all three to
# guard against accidental name shifts in any environment.
STAGES: list[str] = ["dev", "staging", "prod"]

# Account ID is always 12 chars (12-digit AWS account). Use the
# CMS sandbox/staging account ID as the canonical test value; any
# 12-digit value yields the same length.
ACCOUNT_LEN_PLACEHOLDER = "X" * 12

S3_BUCKET_NAME_MAX = 63


class TestStorageStackBucketNameLengths(unittest.TestCase):
    """Regression — `storage_stack.py` ServiceInvoiceBucket name
    must be ≤63 chars in every current AWS region for every CMS
    deployment stage."""

    def _service_invoice_bucket_name(self, stage: str, region: str, account: str) -> str:
        """Mirror the f-string at storage_stack.py:124 exactly.

        Pattern: ``f"{construct_id}-invoices-{self.region}-{self.account}"``
        where ``construct_id = f"cms-{stage}-storage"`` (set by
        ``app.py:60``).

        We don't import StorageStack to evaluate the real construct —
        instantiating a CDK stack requires a fully-initialized App
        and pulls in the full aws-cdk-lib transitive graph for a
        single-string check. Mirroring the f-string is the
        narrowest assertion that locks in the resulting name.
        """
        construct_id = f"cms-{stage}-storage"
        return f"{construct_id}-invoices-{region}-{account}"

    def test_service_invoice_bucket_fits_in_every_region(self) -> None:
        violations: list[tuple[str, str, int, str]] = []
        for stage in STAGES:
            for region in ALL_REGIONS:
                name = self._service_invoice_bucket_name(
                    stage, region, ACCOUNT_LEN_PLACEHOLDER
                )
                if len(name) > S3_BUCKET_NAME_MAX:
                    violations.append((stage, region, len(name), name))

        if violations:
            lines = ["Bucket-name length regression — ServiceInvoiceBucket overshoots 63 chars:"]
            for stage, region, length, name in violations:
                lines.append(
                    f"  stage={stage:8s} region={region:18s} len={length:3d} > {S3_BUCKET_NAME_MAX}  ({name})"
                )
            self.fail("\n".join(lines))

    def test_worst_case_bound_documented(self) -> None:
        """Document the worst-case explicitly so a reviewer reading
        a regression failure has a fixed target. Worst case across
        ``STAGES`` and ``ALL_REGIONS`` is staging+14-char-region:
        56 chars; if this assertion fails in either direction the
        f-string in storage_stack.py has changed and the comment
        block above the bucket declaration needs to be re-checked."""
        max_observed = 0
        max_case: tuple[str, str, str] = ("", "", "")
        for stage in STAGES:
            for region in ALL_REGIONS:
                name = self._service_invoice_bucket_name(
                    stage, region, ACCOUNT_LEN_PLACEHOLDER
                )
                if len(name) > max_observed:
                    max_observed = len(name)
                    max_case = (stage, region, name)

        # The pattern ``cms-{stage}-storage-invoices-{region}-{account}``
        # has 35 fixed chars + len(stage) + len(region). Worst case
        # is staging (7) + any 14-char region = 35 + 7 + 14 = 56.
        EXPECTED_MAX = 56
        self.assertEqual(
            max_observed,
            EXPECTED_MAX,
            f"Worst-case bucket-name length changed: expected {EXPECTED_MAX}, "
            f"got {max_observed} for stage={max_case[0]} region={max_case[1]}: "
            f"{max_case[2]!r}. Update the comment block in storage_stack.py "
            f"and re-run the math table in "
            f"issues/2026-06-03-storage-bucket-name-too-long-ap-northeast-1/report.md.",
        )

    def test_morning_broken_pattern_would_have_failed(self) -> None:
        """Sanity-check the inverse: the morning's pre-fix pattern
        ``cms-{stage}-storage-service-invoices-{region}-{account}``
        DID overshoot in 14-char regions (64 chars). This guards
        against a future "let's add the data-shape qualifier back"
        regression by codifying that 14-char-region+staging+accounts
        cannot tolerate the extra ``-service-`` (8 char) infix."""
        broken_pattern_max = 0
        for stage in STAGES:
            for region in ALL_REGIONS:
                construct_id = f"cms-{stage}-storage"
                broken_name = f"{construct_id}-service-invoices-{region}-{ACCOUNT_LEN_PLACEHOLDER}"
                if len(broken_name) > broken_pattern_max:
                    broken_pattern_max = len(broken_name)

        self.assertGreater(
            broken_pattern_max,
            S3_BUCKET_NAME_MAX,
            f"Sanity-check inverted: the morning's pre-fix pattern was "
            f"supposed to overshoot 63 chars in at least one stage/region "
            f"combination. Got max={broken_pattern_max}. If this passes, "
            f"either the regression target moved or this test's STAGES/"
            f"ALL_REGIONS list is incomplete.",
        )


class TestBedrockAgentsStackVfoKbBucketNameLengths(unittest.TestCase):
    """Regression — `bedrock_agents_stack.py` VfoKnowledgeBaseBucket name
    must be ≤63 chars in every current AWS region for every CMS deployment
    stage. Pattern: ``f"cms-{deployment_stage}-vfo-knowledge-base-{self.region}-{self.account}"``.

    Bug context: spec ``2026-06-04-cms-vfo-kb-bucket-region-suffix``
    brought the VFO knowledge-base bucket under CDK lifecycle. The
    deterministic name pattern adds a 33-char fixed prefix (``cms-...-vfo-knowledge-base-``)
    + len(stage) + len(region) + 13 chars (``-`` + 12-digit account).
    Worst case staging+ap-northeast-1+12-char-account = 58 chars. Fits
    within the 63-char S3 limit with 5 chars headroom."""

    def _vfo_kb_bucket_name(self, stage: str, region: str, account: str) -> str:
        """Mirror the f-string in bedrock_agents_stack.py exactly.

        Pattern: ``f"cms-{deployment_stage}-vfo-knowledge-base-{self.region}-{self.account}"``.
        """
        return f"cms-{stage}-vfo-knowledge-base-{region}-{account}"

    def test_vfo_kb_bucket_fits_in_every_region(self) -> None:
        violations: list[tuple[str, str, int, str]] = []
        for stage in STAGES:
            for region in ALL_REGIONS:
                name = self._vfo_kb_bucket_name(
                    stage, region, ACCOUNT_LEN_PLACEHOLDER
                )
                if len(name) > S3_BUCKET_NAME_MAX:
                    violations.append((stage, region, len(name), name))

        if violations:
            lines = ["Bucket-name length regression — VfoKnowledgeBaseBucket overshoots 63 chars:"]
            for stage, region, length, name in violations:
                lines.append(
                    f"  stage={stage:8s} region={region:18s} len={length:3d} > {S3_BUCKET_NAME_MAX}  ({name})"
                )
            self.fail("\n".join(lines))

    def test_worst_case_bound_documented(self) -> None:
        """Worst case across STAGES and ALL_REGIONS is staging+14-char-region:
        58 chars; if this assertion fails the f-string in bedrock_agents_stack.py
        has changed and the comment block above the bucket declaration needs
        to be re-checked."""
        max_observed = 0
        max_case: tuple[str, str, str] = ("", "", "")
        for stage in STAGES:
            for region in ALL_REGIONS:
                name = self._vfo_kb_bucket_name(
                    stage, region, ACCOUNT_LEN_PLACEHOLDER
                )
                if len(name) > max_observed:
                    max_observed = len(name)
                    max_case = (stage, region, name)

        # Pattern ``cms-{stage}-vfo-knowledge-base-{region}-{account}``
        # has 33 fixed chars + len(stage) + len(region) + 12 (account).
        # Worst-case real region is 14 chars; eusc-de-east-1 is 14 chars too.
        # = 33 + 7 + 14 + 12 = 66? Let me recount.
        # ``cms-`` (4) + ``{stage}-`` (8 for staging) + ``vfo-knowledge-base-`` (19) +
        # ``{region}-`` (15 for 14-char region) + 12 (account) = 4+8+19+15+12 = 58.
        EXPECTED_MAX = 58
        self.assertEqual(
            max_observed,
            EXPECTED_MAX,
            f"Worst-case bucket-name length changed: expected {EXPECTED_MAX}, "
            f"got {max_observed} for stage={max_case[0]} region={max_case[1]}: "
            f"{max_case[2]!r}. Update the comment block in "
            f"bedrock_agents_stack.py and the documented worst-case "
            f"in spec 2026-06-04-cms-vfo-kb-bucket-region-suffix.",
        )


if __name__ == "__main__":
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestSuite()
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(
        TestStorageStackBucketNameLengths
    ))
    suite.addTests(unittest.TestLoader().loadTestsFromTestCase(
        TestBedrockAgentsStackVfoKbBucketNameLengths
    ))
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
