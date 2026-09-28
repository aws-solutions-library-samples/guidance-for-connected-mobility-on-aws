#!/usr/bin/env python3
"""Guard: ``backfill_campaign_owner._TELEMETRY_TEMPLATE`` stays in sync with
``simulation_lambda._TELEMETRY_TEMPLATE``.

Background
----------
``deployment/scripts/backfill_campaign_owner.py`` contains a local copy of the
auto-ensure template name so it can run standalone without importing the Lambda's
dependencies.  A prose comment directs the reader to keep the two in sync, but
prose is not a control: if the Lambda's constant changes before the backfill runs
on a stage that still needs it, every per-vehicle auto-ensure row is misclassified
as ``"oem"`` instead of ``"platform"`` — silently, on a RETAIN table.

This test makes the sync rule executable.

Approach
--------
Both source files are parsed symmetrically (neither is imported) so the test has
no dependency on either module's runtime requirements.  The extraction regex is
the same for both; the test asserts the two extracted values are equal, never
restating the literal itself as the expected value.

Run from ``deployment/scripts/``::

    python3 -m pytest test_backfill_campaign_owner_template_sync.py -v
"""
from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parents[2]
_BACKFILL_PATH = _REPO_ROOT / "deployment" / "scripts" / "backfill_campaign_owner.py"
_LAMBDA_PATH = _REPO_ROOT / "services" / "simulation" / "lambda" / "simulation_lambda.py"

# Pattern that matches:  _TELEMETRY_TEMPLATE = "some-value"
# Anchored at the start of a logical assignment to avoid matching comments or
# interpolation sites.
_CONSTANT_RE = re.compile(
    r'^_TELEMETRY_TEMPLATE\s*=\s*["\']([^"\']+)["\']',
    re.MULTILINE,
)


def _extract_telemetry_template(path: Path) -> str:
    """Parse *exactly one* ``_TELEMETRY_TEMPLATE = ...`` assignment from *path*.

    Raises ``AssertionError`` when the constant cannot be found so the caller
    gets a clear failure message rather than an AttributeError.  Also raises
    when *more than one* match is found, so a stage override or second
    definition cannot silently shadow the authoritative value.
    """
    source = path.read_text(encoding="utf-8")
    matches = _CONSTANT_RE.findall(source)
    assert len(matches) == 1, (
        f"Expected exactly 1 '_TELEMETRY_TEMPLATE = ...' in "
        f"{path.relative_to(_REPO_ROOT)}, found {len(matches)}: {matches!r}"
    )
    return matches[0]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_telemetry_template_constants_are_equal() -> None:
    """The two ``_TELEMETRY_TEMPLATE`` constants must be identical.

    If the Lambda's constant changes, the backfill misclassifies every
    auto-ensure row as ``"oem"`` instead of ``"platform"`` on any stage
    that has not yet been migrated — silently, on a RETAIN table.
    """
    backfill_value = _extract_telemetry_template(_BACKFILL_PATH)
    lambda_value = _extract_telemetry_template(_LAMBDA_PATH)

    assert backfill_value == lambda_value, (
        f"_TELEMETRY_TEMPLATE mismatch: "
        f"backfill_campaign_owner has {backfill_value!r}, "
        f"simulation_lambda has {lambda_value!r}. "
        f"Update one to match the other."
    )


def test_extraction_helper_finds_constant_in_both_files() -> None:
    """Positive control: both source files must contain the constant.

    Without this, a future rename that removes the constant from one file would
    leave ``test_telemetry_template_constants_are_equal`` failing loudly on the
    one-match assertion, but the guard is here to make the failure mode explicit
    and the assertion message actionable.
    """
    backfill_value = _extract_telemetry_template(_BACKFILL_PATH)
    lambda_value = _extract_telemetry_template(_LAMBDA_PATH)

    assert isinstance(backfill_value, str) and backfill_value, (
        f"backfill constant is empty or not a string: {backfill_value!r}"
    )
    assert isinstance(lambda_value, str) and lambda_value, (
        f"lambda constant is empty or not a string: {lambda_value!r}"
    )
