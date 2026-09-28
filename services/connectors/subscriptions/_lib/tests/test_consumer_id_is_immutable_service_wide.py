# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""`consumer_id` must never be written by an update path, anywhere in the service.

Spec `2026-09-10-cms-connected-services-subscriptions`, Option A.
Issue `issues/2026-09-12-subscription-ownership-claim-has-no-writer/`.

Option A makes the Subscription row's `consumer_id` the authorization authority.
That is only sound while the field is **server-authored at create and immutable
thereafter** — which is true today, verified by hand across every handler.

Nothing *enforced* it. A future overbroad `UpdateExpression` in any sibling
handler could silently break the invariant that the read paths now depend on, and
no test would notice: the sibling's own tests would pass, and the authorization
tests would keep passing too, because they construct rows directly rather than
mutating them through a handler.

This file is that enforcement. It is a cross-file lint, deliberately executable
rather than documentary — per `~/.kiro/steering/agentic-tiers.md`: "a boundary
that exists only in a prompt is a boundary that will be crossed."

Independently recommended as a High suggestion by the sharded security review of
2026-09-12 (`security-review-option-a-crud.md` S2,
`security-review-option-a-scope.md` S1), which flagged that each per-directory
review could verify the invariant only within its own scope.
"""
from __future__ import annotations

import os
import re

_SERVICE_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

#: Field whose immutability the authorization model depends on.
_PROTECTED = "consumer_id"

#: The one legitimate writer: create, via PutItem in the crud handler.
_CREATE_HANDLER = os.path.join("subscription_crud", "handler.py")


def _handler_sources() -> list[str]:
    """Every handler.py under the service, excluding tests and caches."""
    out = []
    for root, dirs, files in os.walk(_SERVICE_ROOT):
        dirs[:] = [
            d for d in dirs
            if d not in {"tests", "__pycache__", ".pytest_cache", ".build"}
        ]
        for f in files:
            if f.endswith(".py"):
                out.append(os.path.join(root, f))
    assert out, "found no service sources to lint — the walk root is wrong"
    return out


def _update_expressions(source: str) -> list[str]:
    """Extract UpdateExpression string literals from a source file.

    Matches `UpdateExpression=` / `UpdateExpression:` followed by a quoted
    string, including the parenthesised multi-line concatenation style used by
    these handlers.
    """
    found = []
    for m in re.finditer(r"UpdateExpression\s*[=:]\s*\(?((?:\s*[\"'][^\"']*[\"'])+)", source):
        found.append(" ".join(re.findall(r"[\"']([^\"']*)[\"']", m.group(1))))
    return found


def test_no_update_expression_writes_consumer_id():
    """The authorization anchor must not be mutable through any update path."""
    offenders = []
    for path in _handler_sources():
        source = open(path, encoding="utf-8").read()
        for expr in _update_expressions(source):
            # SET/ADD/REMOVE/DELETE of the protected attribute. A bare mention
            # inside a ConditionExpression is fine and is not matched here,
            # because only UpdateExpression literals are extracted.
            if re.search(rf"\b{_PROTECTED}\b", expr):
                offenders.append((os.path.relpath(path, _SERVICE_ROOT), expr.strip()))

    assert not offenders, (
        "an UpdateExpression writes `consumer_id`, which Option A's authorization "
        "model requires to be immutable after create. If this change is "
        "intentional, the read-path ownership checks in subscription_crud and "
        "subscription_records must be revisited FIRST — they trust this field.\n"
        + "\n".join(f"  {p}: {e}" for p, e in offenders)
    )


def test_consumer_id_is_written_only_by_the_create_path():
    """Exactly one file may author the field, and it must do so via PutItem."""
    writers = []
    for path in _handler_sources():
        rel = os.path.relpath(path, _SERVICE_ROOT)
        source = open(path, encoding="utf-8").read()
        # An item-construction assignment: `"consumer_id": <something>`
        if re.search(rf"[\"']{_PROTECTED}[\"']\s*:", source) and "put_item" in source:
            writers.append(rel)

    assert writers == [_CREATE_HANDLER], (
        f"expected only {_CREATE_HANDLER} to author `consumer_id` via put_item, "
        f"got {writers}. A second writer means two places can decide who owns a "
        f"subscription."
    )


def test_the_lint_is_not_vacuous():
    """Guard the guard: prove the detector fires on the pattern it claims to catch.

    Without this, a regex that silently matched nothing would let the two tests
    above pass forever while enforcing nothing — the exact failure mode this
    spec has hit repeatedly (an assertion passing for a reason unrelated to its
    name).
    """
    bad = 'UpdateExpression="SET consumer_id = :x, updated_at = :t"'
    exprs = _update_expressions(bad)
    assert exprs, "the UpdateExpression extractor matched nothing at all"
    assert any(re.search(rf"\b{_PROTECTED}\b", e) for e in exprs), (
        "the detector failed to flag a deliberate consumer_id write"
    )

    # And it must NOT flag a legitimate scope write, or it would block real work.
    ok = 'UpdateExpression="ADD vehicle_scope :v SET updated_at = :t"'
    assert not any(
        re.search(rf"\b{_PROTECTED}\b", e) for e in _update_expressions(ok)
    ), "the detector false-positives on an ordinary scope update"


def test_multiline_concatenated_update_expressions_are_parsed():
    """The handlers use a parenthesised multi-line style — it must be covered."""
    src = (
        'resp = table.update_item(\n'
        '    UpdateExpression=(\n'
        '        "SET consumer_id = :c"\n'
        '        " , updated_at = :t"\n'
        '    ),\n'
        ')\n'
    )
    exprs = _update_expressions(src)
    assert exprs, "multi-line concatenated UpdateExpression was not extracted"
    assert any(re.search(rf"\b{_PROTECTED}\b", e) for e in exprs), (
        "multi-line form parsed but the protected field was not detected — a "
        "real write in this style would slip through"
    )
