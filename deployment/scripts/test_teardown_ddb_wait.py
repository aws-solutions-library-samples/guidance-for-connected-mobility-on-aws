#!/usr/bin/env python3
"""Regression test for teardown DDB delete sync-completion fix.

Verifies that ``teardown_region_force.delete_orphaned_ddb_tables``:

1. Issues ``delete_table`` for each matched ``cms-{stage}-*`` table.
2. Polls ``describe_table`` after the deletes are issued, and does
   NOT return until every initiated delete has completed (i.e. the
   table is gone, surfaced as ``ResourceNotFoundException``) or its
   per-table timeout elapses.

Bug context: clean-deploy run 5 (2026-06-03T15-17-33Z) against
ap-northeast-1 saw teardown PASS while audit FAIL with 6 DDB tables
listed as orphans. Investigation showed teardown's deletes had
succeeded but the function returned the moment the API calls were
issued; ``dynamodb.delete_table`` is asynchronous, so the audit (run
~17s later by the orchestrator) saw the still-DELETING tables in
its enumeration.

See: ``issues/2026-06-03-clean-deploy-coverage-step-back/`` and
``deployment/scripts/teardown_region_force.py.delete_orphaned_ddb_tables``.

This is a stdlib-only test (uses ``unittest.mock``); no pytest, no
moto. Returns exit code 0 on PASS, 1 on FAIL.
"""
from __future__ import annotations

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

# Make `deployment/scripts/` importable when running from anywhere.
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, SCRIPT_DIR)

# Stub `time.sleep` to make the test fast — the real function uses
# 5s polling intervals which would make this test slow without
# undermining the assertion (we still verify it polls).
import teardown_region_force  # noqa: E402

from botocore.exceptions import ClientError


def _make_resource_not_found_error() -> ClientError:
    return ClientError(
        error_response={
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "Requested resource not found",
            },
            "ResponseMetadata": {"HTTPStatusCode": 400},
        },
        operation_name="DescribeTable",
    )


class TestDeleteOrphanedDDBTablesWaitsForCompletion(unittest.TestCase):
    """Regression: function must NOT return until tables are gone."""

    def setUp(self) -> None:
        self.tables_in_account = [
            "cms-staging-campaigns",
            "cms-staging-data-source-configs",
            "cms-staging-decoder-manifest",
            "unrelated-table",  # ← must NOT be touched
        ]

    def _build_mock_ddb(
        self,
        describe_outcomes: dict[str, list],
    ) -> MagicMock:
        """Build a mock DDB client whose ``describe_table`` returns
        successive outcomes per call (so the test can simulate
        eventual-consistency: first call says EXISTS, second says GONE)."""
        ddb = MagicMock()

        # list_tables — paginated mock
        page = {"TableNames": list(self.tables_in_account)}
        paginator = MagicMock()
        paginator.paginate.return_value = iter([page])
        ddb.get_paginator.return_value = paginator

        # describe_table — pop next outcome per call
        outcomes_iter = {k: iter(v) for k, v in describe_outcomes.items()}

        def describe_side_effect(TableName: str):  # noqa: N803
            it = outcomes_iter.get(TableName)
            if it is None:
                raise _make_resource_not_found_error()
            outcome = next(it)
            if isinstance(outcome, Exception):
                raise outcome
            return outcome

        ddb.describe_table.side_effect = describe_side_effect

        # delete_table — succeed silently
        ddb.delete_table.return_value = {}
        # update_table — for deletion-protection toggle path
        ddb.update_table.return_value = {}

        return ddb

    def test_function_polls_describe_table_until_gone(self) -> None:
        """Each matched cms-staging-* table is delete'd, then the
        function polls describe_table until each raises
        ResourceNotFoundException."""
        # describe_outcomes per table:
        #   - First call (pre-delete): returns ACTIVE table (proceed to delete).
        #   - After delete, polling phase: 1st call EXISTS (DELETING), 2nd GONE.
        active_table_resp = {"Table": {"TableStatus": "ACTIVE"}}
        deleting_table_resp = {"Table": {"TableStatus": "DELETING"}}

        describe_outcomes = {
            "cms-staging-campaigns": [
                active_table_resp,           # pre-delete describe
                deleting_table_resp,         # 1st wait-poll: still here
                _make_resource_not_found_error(),  # 2nd wait-poll: gone
            ],
            "cms-staging-data-source-configs": [
                active_table_resp,
                _make_resource_not_found_error(),  # gone after 1 poll
            ],
            "cms-staging-decoder-manifest": [
                active_table_resp,
                deleting_table_resp,
                deleting_table_resp,
                _make_resource_not_found_error(),  # gone after 3 polls
            ],
        }

        ddb = self._build_mock_ddb(describe_outcomes)

        with patch("teardown_region_force.boto3.client", return_value=ddb), \
             patch("teardown_region_force.time.sleep") as mock_sleep:
            teardown_region_force.delete_orphaned_ddb_tables(
                region="ap-northeast-1", stage="staging", dry_run=False
            )

        # Assertion 1: delete_table was called once per matched table,
        # NOT for the unrelated table.
        deleted = [c.kwargs.get("TableName") or c.args[0]
                   for c in ddb.delete_table.call_args_list]
        # signature is delete_table(TableName=t) — kwargs path
        deleted_kw = [c.kwargs["TableName"]
                      for c in ddb.delete_table.call_args_list
                      if "TableName" in c.kwargs]
        self.assertEqual(sorted(deleted_kw), [
            "cms-staging-campaigns",
            "cms-staging-data-source-configs",
            "cms-staging-decoder-manifest",
        ], f"delete_table call set wrong: {deleted_kw}")
        self.assertNotIn("unrelated-table", deleted_kw)

        # Assertion 2: describe_table was called MORE than once per
        # matched table — first call is the pre-delete check, second+
        # are the wait-poll. If teardown had reverted to fire-and-
        # forget, each table would be described exactly ONCE (pre-
        # delete only) and never polled.
        from collections import Counter
        describe_counts = Counter(
            c.kwargs.get("TableName") for c in ddb.describe_table.call_args_list
            if "TableName" in c.kwargs
        )
        for table in [
            "cms-staging-campaigns",
            "cms-staging-data-source-configs",
            "cms-staging-decoder-manifest",
        ]:
            self.assertGreater(
                describe_counts[table], 1,
                f"REGRESSION: {table} was described only "
                f"{describe_counts[table]} time(s); the wait-loop is "
                f"missing. Function must poll describe_table after "
                f"issuing delete_table to ensure the table is "
                f"actually gone before returning.",
            )

        # Assertion 3: time.sleep was called (the wait-loop sleeps
        # between polls). If the function returned without sleeping,
        # there is no wait.
        self.assertGreater(
            mock_sleep.call_count, 0,
            "REGRESSION: time.sleep was never called, indicating "
            "the wait-loop is missing.",
        )

    def test_dry_run_does_not_wait_or_call_aws(self) -> None:
        """dry_run=True must NOT issue deletes nor wait."""
        # describe_table is called only for live-mode pre-delete checks;
        # dry_run skips the inner block entirely. We provide a mock
        # that would FAIL if invoked in the wait-loop, to prove no wait.
        active = {"Table": {"TableStatus": "ACTIVE"}}
        ddb = self._build_mock_ddb({
            "cms-staging-campaigns": [active] * 100,
            "cms-staging-data-source-configs": [active] * 100,
            "cms-staging-decoder-manifest": [active] * 100,
        })

        with patch("teardown_region_force.boto3.client", return_value=ddb), \
             patch("teardown_region_force.time.sleep") as mock_sleep:
            teardown_region_force.delete_orphaned_ddb_tables(
                region="ap-northeast-1", stage="staging", dry_run=True
            )

        # No deletes issued in dry-run.
        self.assertEqual(ddb.delete_table.call_count, 0,
                         "dry_run=True must not call delete_table")
        # No waiting in dry-run (the inner-loop sleep at top of
        # phase-2 also doesn't execute because pending stays empty).
        self.assertEqual(mock_sleep.call_count, 0,
                         "dry_run=True must not sleep")


if __name__ == "__main__":
    # Behave like a CLI tool: exit 0 on success, 1 on failure.
    runner = unittest.TextTestRunner(verbosity=2)
    suite = unittest.TestLoader().loadTestsFromTestCase(
        TestDeleteOrphanedDDBTablesWaitsForCompletion
    )
    result = runner.run(suite)
    sys.exit(0 if result.wasSuccessful() else 1)
