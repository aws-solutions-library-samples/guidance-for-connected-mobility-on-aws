# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Regression tests pinning DynamoDB pagination in seed_charging_sessions.py.

Spec: `.kiro/specs/2026-09-10-cms-connected-services-subscriptions/` — closes the
"no regression test pins the pagination" half of the T4.2 review gap recorded in
that spec's `review.md` § "Close-out addendum (2026-09-13)".

WHY THIS FILE EXISTS
--------------------
T4.2's review Cycle 1 raised a Warning: `_existing_sks()` and `_count_rows()`
issued a single `table.query()` and consumed only the first page, so past a 1 MB
page the idempotency skip under-reports and a re-run rewrites rows it had already
written — the guarantee `_existing_sks` exists to provide would stop holding
exactly when it starts to matter. Fix group `f6f85e2d` added the
`LastEvaluatedKey` loops. Nothing then pinned them: at close-out, deleting either
loop failed no test in the repo.

WHAT THESE TESTS ASSERT — properties, not presence
--------------------------------------------------
This spec accumulated six recorded instances of tests that assert a call happened
rather than that it was correct, so each test here asserts a value:

  * union/sum across pages by **exact equality**, not `>=` or a length check, so a
    first-page-only or last-page-only read fails rather than coincidentally passing;
  * the **cursor value** — call N+1's `ExclusiveStartKey` must equal call N's
    returned `LastEvaluatedKey` — rather than merely that a second call occurred,
    so paging with a wrong or constant cursor fails;
  * a single-page **negative control**, so "always loop twice" is not a passing
    implementation either.

MUTATION-VERIFIED. Deleting the `while` loop from `_existing_sks` (returning after
the first page) fails `test_existing_sks_unions_every_page` and
`test_existing_sks_threads_the_returned_cursor`. Deleting it from `_count_rows`
fails `test_count_rows_sums_every_page` and
`test_count_rows_threads_the_returned_cursor`. Recorded here because a pagination
guard that has never been seen to fail is indistinguishable from one that cannot.

No AWS calls: the fake table below is a pure page-sequence replayer.
"""

from __future__ import annotations

import os
import sys
from typing import Any

import pytest

# ── add deployment/scripts to sys.path ────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS_DIR = os.path.abspath(os.path.join(_HERE, ".."))
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

import seed_charging_sessions as seeder  # noqa: E402


class _PagedTable:
    """Replay a fixed sequence of Query responses, recording each call's kwargs.

    Raises rather than returning a default if queried more often than there are
    pages — an implementation that loops forever fails loudly instead of hanging.
    """

    def __init__(self, pages: list[dict[str, Any]]) -> None:
        self._pages = list(pages)
        self.calls: list[dict[str, Any]] = []

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.calls.append(dict(kwargs))
        if not self._pages:
            raise AssertionError(
                f"query() called {len(self.calls)} times but only "
                f"{len(self.calls) - 1} pages were provided — the pagination loop "
                "is not terminating on absent LastEvaluatedKey"
            )
        return self._pages.pop(0)


# ── _existing_sks ─────────────────────────────────────────────────────────────


class TestExistingSksPagination:
    """`_existing_sks` must return the union of every page, not the first."""

    def test_existing_sks_unions_every_page(self) -> None:
        table = _PagedTable(
            [
                {
                    "Items": [{"sessionStartTime": "2026-08-14T01:00:00Z"},
                              {"sessionStartTime": "2026-08-15T01:00:00Z"}],
                    "LastEvaluatedKey": {"vehicleId": "VEH-TEST-1",
                                         "sessionStartTime": "2026-08-15T01:00:00Z"},
                },
                {
                    "Items": [{"sessionStartTime": "2026-08-16T01:00:00Z"}],
                    "LastEvaluatedKey": {"vehicleId": "VEH-TEST-1",
                                         "sessionStartTime": "2026-08-16T01:00:00Z"},
                },
                {"Items": [{"sessionStartTime": "2026-08-17T01:00:00Z"}]},
            ]
        )

        got = seeder._existing_sks(table, "VEH-TEST-1")

        # Exact equality: a first-page-only read returns a 2-element subset and
        # fails here; a last-page-only read returns a 1-element subset and fails.
        assert got == {
            "2026-08-14T01:00:00Z",
            "2026-08-15T01:00:00Z",
            "2026-08-16T01:00:00Z",
            "2026-08-17T01:00:00Z",
        }, (
            "expected the union of all 3 pages; got "
            f"{sorted(got)} after {len(table.calls)} query call(s) — an unpaginated "
            "read returns only the first page, which is the T4.2 Warning"
        )
        assert len(table.calls) == 3

    def test_existing_sks_threads_the_returned_cursor(self) -> None:
        cursor_1 = {"vehicleId": "VEH-TEST-2", "sessionStartTime": "2026-08-15T01:00:00Z"}
        cursor_2 = {"vehicleId": "VEH-TEST-2", "sessionStartTime": "2026-08-16T01:00:00Z"}
        table = _PagedTable(
            [
                {"Items": [{"sessionStartTime": "2026-08-15T01:00:00Z"}],
                 "LastEvaluatedKey": cursor_1},
                {"Items": [{"sessionStartTime": "2026-08-16T01:00:00Z"}],
                 "LastEvaluatedKey": cursor_2},
                {"Items": [{"sessionStartTime": "2026-08-17T01:00:00Z"}]},
            ]
        )

        seeder._existing_sks(table, "VEH-TEST-2")

        # The cursor VALUE, not just the presence of a second call: paging with a
        # stale or constant cursor would re-read one page forever.
        assert "ExclusiveStartKey" not in table.calls[0], (
            "the first Query must not carry a cursor"
        )
        assert table.calls[1]["ExclusiveStartKey"] == cursor_1
        assert table.calls[2]["ExclusiveStartKey"] == cursor_2

    def test_existing_sks_single_page_issues_exactly_one_query(self) -> None:
        """Negative control: 'always loop twice' must not pass either."""
        table = _PagedTable([{"Items": [{"sessionStartTime": "2026-08-14T01:00:00Z"}]}])

        got = seeder._existing_sks(table, "VEH-TEST-3")

        assert got == {"2026-08-14T01:00:00Z"}
        assert len(table.calls) == 1, (
            "a response with no LastEvaluatedKey is the last page — querying again "
            "would either hang or raise"
        )

    def test_existing_sks_empty_table_is_empty_set(self) -> None:
        table = _PagedTable([{"Items": []}])
        assert seeder._existing_sks(table, "VEH-TEST-4") == set()
        assert len(table.calls) == 1


# ── _count_rows ───────────────────────────────────────────────────────────────


class TestCountRowsPagination:
    """`_count_rows` must sum Count across every page.

    An under-count makes the post-apply VERIFY step report success against a
    partial write, which is the failure mode with the worse blast radius of the
    two: `_existing_sks` under-reporting rewrites rows that already exist, but a
    short `_count_rows` says the seed succeeded when it did not.
    """

    def test_count_rows_sums_every_page(self) -> None:
        table = _PagedTable(
            [
                {"Count": 40, "LastEvaluatedKey": {"vehicleId": "VEH-TEST-5",
                                                   "sessionStartTime": "p1"}},
                {"Count": 40, "LastEvaluatedKey": {"vehicleId": "VEH-TEST-5",
                                                   "sessionStartTime": "p2"}},
                {"Count": 12},
            ]
        )

        got = seeder._count_rows(table, "VEH-TEST-5")

        # Exact: an unpaginated count returns 40, not 92. Distinct per-page values
        # so that summing the wrong subset cannot coincidentally reach the total.
        assert got == 92, (
            f"expected 40+40+12=92 across 3 pages, got {got} after "
            f"{len(table.calls)} query call(s) — an unpaginated Select=COUNT "
            "counts only rows scanned within the first 1 MB page"
        )
        assert len(table.calls) == 3

    def test_count_rows_threads_the_returned_cursor(self) -> None:
        cursor_1 = {"vehicleId": "VEH-TEST-6", "sessionStartTime": "p1"}
        table = _PagedTable(
            [{"Count": 7, "LastEvaluatedKey": cursor_1}, {"Count": 3}]
        )

        seeder._count_rows(table, "VEH-TEST-6")

        assert "ExclusiveStartKey" not in table.calls[0]
        assert table.calls[1]["ExclusiveStartKey"] == cursor_1

    def test_count_rows_single_page_issues_exactly_one_query(self) -> None:
        """Negative control, as above."""
        table = _PagedTable([{"Count": 10}])

        assert seeder._count_rows(table, "VEH-TEST-7") == 10
        assert len(table.calls) == 1

    def test_count_rows_absent_count_key_is_zero_not_crash(self) -> None:
        """`.get("Count", 0)` — a malformed page must not raise KeyError."""
        table = _PagedTable([{}])
        assert seeder._count_rows(table, "VEH-TEST-8") == 0


# ── the Query shape both helpers depend on ────────────────────────────────────


class TestQueryShape:
    """Both helpers must keep the projections that make them cheap.

    Not pagination, but adjacent: dropping `ProjectionExpression` or
    `Select="COUNT"` silently multiplies read cost and, for COUNT, changes what
    the response even contains.
    """

    def test_existing_sks_projects_only_the_sort_key(self) -> None:
        table = _PagedTable([{"Items": []}])
        seeder._existing_sks(table, "VEH-TEST-9")
        assert table.calls[0]["ProjectionExpression"] == "sessionStartTime"
        assert "Select" not in table.calls[0]

    def test_count_rows_uses_select_count(self) -> None:
        table = _PagedTable([{"Count": 0}])
        seeder._count_rows(table, "VEH-TEST-10")
        assert table.calls[0]["Select"] == "COUNT"
        assert "ProjectionExpression" not in table.calls[0]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
