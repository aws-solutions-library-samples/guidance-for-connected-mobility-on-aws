"""Guard: no route may address a stage-specific DynamoDB table by hardcoded name.

Found 2026-08-10. `GET /api/v1/fleet-actions` and
`POST /api/v1/fleet-actions/{id}/{approve|reject}` both addressed
`cms-prod-vfo-action-queue` as a bare literal with **no env-var fallback**, in a Lambda
deployed to every stage. Consequences:

  * On staging, the GET returned PRODUCTION rows — including the 32 rows carrying a
    customer brand name in `tenantId` that the 2026-08-10 exposure audit found — while
    staging's own 385-row table was invisible.
  * The approve/reject route calls `update_item`, so acting on a fleet action from
    STAGING MUTATED PRODUCTION DATA.

Line ~7410 of the same file already used the correct `f'cms-{stage}-vfo-action-queue'`
form, so the correct pattern was present in the file the whole time. That is what makes
this worth a guard rather than just a fix: the inconsistency was invisible to review
because both forms coexisted.

The test allows `os.environ.get('X', 'cms-prod-...')` — an env-var-overridable default is
the established idiom here (see SAFETY_EVENTS_TABLE_NAME, SERVICE_HISTORY_TABLE_NAME).
What it forbids is a bare literal with no override path.
"""
from __future__ import annotations

import os
import re

_HERE = os.path.dirname(os.path.abspath(__file__))
_INDEX = os.path.join(_HERE, "index.py")


def _lines() -> list[tuple[int, str]]:
    with open(_INDEX) as fh:
        return list(enumerate(fh.read().splitlines(), start=1))


# A stage-specific table literal: cms-<stage>-... for a CONCRETE stage.
_HARDCODED = re.compile(r"""['"]cms-(prod|staging)-[a-z0-9-]+['"]""")


def _is_env_defaulted(line: str) -> bool:
    """True if the literal appears as an os.environ.get(...) default.

    That form is acceptable: the deployed stack sets the variable, so the literal is a
    local-dev fallback rather than the effective table name.
    """
    return "os.environ.get" in line or "os.getenv" in line


def test_no_bare_hardcoded_stage_table_literals() -> None:
    offenders = []
    for lineno, line in _lines():
        stripped = line.strip()
        if stripped.startswith("#"):
            continue  # comments may legitimately name the old defect
        for m in _HARDCODED.finditer(line):
            if _is_env_defaulted(line):
                continue
            offenders.append(f"index.py:{lineno}: {m.group(0)}")

    assert not offenders, (
        "Route(s) address a stage-specific table by bare literal, with no env-var "
        "override. On any stage other than the hardcoded one this reads — and possibly "
        "WRITES — another stage's data. Use "
        "os.environ.get('<VAR>', f'cms-{stage}-<table>').\n  " + "\n  ".join(offenders)
    )


def test_vfo_action_queue_routes_are_stage_scoped() -> None:
    """Targeted assertion for the two routes that carried the defect."""
    with open(_INDEX) as fh:
        src = fh.read()
    # Strip comments so the explanatory notes naming the old literal do not match.
    code_only = "\n".join(l.split("#", 1)[0] for l in src.splitlines())
    assert "'cms-prod-vfo-action-queue'" not in code_only, (
        "the hardcoded prod VFO action-queue table is back — staging would read and "
        "write production rows"
    )
    # The paired positive assertion — that VFO_ACTION_QUEUE_TABLE_NAME appears in
    # index.py — was removed 2026-09-09. The VFO teardown
    # (2026-09-05-cms-vfo-teardown) deleted every route that read that table, so the
    # env var is legitimately absent from this handler and the assertion had been
    # failing since. See
    # issues/2026-09-09-vfo-teardown-left-15-tests-for-deleted-routes/.
    #
    # The negative assertion above is deliberately KEPT. The DDB tables themselves were
    # retained (the Flink processors still write to them, per backlog row
    # "VFO tables cleanup"), so a hardcoded prod table name reappearing in this handler
    # would still be a real cross-stage defect. Removing the guard because its partner
    # went stale would drop live coverage.


def test_guard_would_catch_a_reintroduction() -> None:
    """Prove the regex actually matches the shape it is meant to forbid.

    A guard whose pattern silently stops matching is indistinguishable from no guard —
    the lesson this repo has now relearned several times.
    """
    bad = "                table = dynamodb.Table('cms-prod-vfo-action-queue')"
    assert _HARDCODED.search(bad) and not _is_env_defaulted(bad)

    ok = "  t = dynamodb.Table(os.environ.get('X_TABLE', 'cms-prod-storage-safety-events'))"
    assert _HARDCODED.search(ok) and _is_env_defaulted(ok)
