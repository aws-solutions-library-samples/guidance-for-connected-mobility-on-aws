"""Guard: every raw-passthrough IoT topic rule must pin awsIotSqlVersion 2016-03-23.

Issue: issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/

Omitting `aws_iot_sql_version` on a `CfnTopicRule` defaults it to the original
2015-10-08 SQL version, which does not support nested JSON objects or arrays.
With a `SELECT *` passthrough it silently:

  - empties top-level arrays to []
  - DROPS arrays nested inside an object
  - FLATTENS nested objects into their parent

Scalars survive, so the resulting row looks healthy. That shipped on the SOVD
response rule and destroyed every array in every SOVD response for the life of
the feature — `lamp_self_check` lost `lamps`, `cell_balance_check` lost
`cell_voltages`, and `sovd_read_dtcs` lost its per-ECU DTC arrays.

AWS docs: "2016-03-23 ... (recommended)" and "Nested object queries ...
Supported by SQL version 2016-03-23 and later."
  https://docs.aws.amazon.com/iot/latest/developerguide/iot-rule-sql-version.html
  https://docs.aws.amazon.com/iot/latest/developerguide/iot-sql-nested-queries.html

This is a SOURCE-structural guard (AST over the stack modules) rather than a
synth test, so it runs without a CDK-enabled interpreter.

A rule is exempt only if its SQL wraps the payload in `encode(...)` — a
base64-encoded payload is opaque to the SQL engine and cannot be mangled. That
is why the FWE protobuf rule was never affected.

Run:
  python3 -m pytest deployment/stacks/tests/test_iot_rule_sql_version.py -v
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_HERE = Path(__file__).resolve().parent
_STACKS = _HERE.parent

_REQUIRED_VERSION = "2016-03-23"

# Rules deliberately NOT yet migrated, each with a reason. Every entry is
# asserted to still be a real candidate by test_known_gaps_are_still_real, so a
# fixed rule cannot linger here and quietly weaken the guard.
# Rules deliberately NOT yet migrated, each with a reason. Every entry is
# asserted to still be a real candidate by test_known_gaps_are_still_real, so a
# fixed rule cannot linger here and quietly weaken the guard.
#
# EMPTY as of 2026-09-23 — every raw-passthrough rule in the repo now pins
# 2016-03-23. Keep the dict (and its guard) rather than deleting it: a future
# rule that genuinely cannot migrate needs a documented home, and an empty
# allowlist is the strongest possible state for this guard to be in.
_KNOWN_GAPS: dict[str, str] = {}


def _iter_topic_rules():
    """Yield (module_name, construct_id, sql, sql_version) per CfnTopicRule."""
    for path in sorted(_STACKS.glob("*.py")):
        try:
            tree = ast.parse(path.read_text(), filename=str(path))
        except SyntaxError:  # pragma: no cover - a broken stack fails elsewhere
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
            if name != "CfnTopicRule":
                continue

            construct_id = None
            for arg in node.args:
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    construct_id = arg.value
                    break

            sql = None
            version = None
            for kw in node.keywords:
                if kw.arg != "topic_rule_payload" or not isinstance(kw.value, ast.Call):
                    continue
                for pkw in kw.value.keywords:
                    if pkw.arg == "sql" and isinstance(pkw.value, ast.Constant):
                        sql = pkw.value.value
                    elif pkw.arg == "aws_iot_sql_version" and isinstance(pkw.value, ast.Constant):
                        version = pkw.value.value
            yield path.name, construct_id, sql, version


def _is_raw_passthrough(sql: str | None) -> bool:
    """True when the rule hands the SQL engine a structure it can mangle.

    `encode(*, 'base64')` yields an opaque string, so such rules are immune
    regardless of SQL version.
    """
    if not sql:
        return False
    if "encode(" in sql:
        return False
    return "SELECT *" in sql or "select *" in sql


def _candidates():
    return [
        (mod, cid, sql, ver)
        for mod, cid, sql, ver in _iter_topic_rules()
        if _is_raw_passthrough(sql)
    ]


# ---------------------------------------------------------------------------
# Anti-vacuity: the scanner must actually see the things it claims to check.
# Without these, deleting the AST walk would make every assertion below pass.
# ---------------------------------------------------------------------------

def test_scanner_finds_topic_rules_at_all():
    rules = list(_iter_topic_rules())
    assert len(rules) >= 3, (
        f"AST scan found only {len(rules)} CfnTopicRule construct(s) — the walk is "
        "broken, so every other assertion in this module is vacuous."
    )


def test_scanner_finds_the_sovd_rule_specifically():
    ids = {cid for _, cid, _, _ in _iter_topic_rules()}
    assert "SovdResponseRule" in ids, (
        f"SovdResponseRule not found by the scan (saw: {sorted(ids)}). Either it was "
        "renamed or the AST walk no longer matches it."
    )


def test_scanner_classifies_the_sovd_rule_as_a_raw_passthrough():
    """The SOVD rule must be IN scope for the version requirement.

    If a future edit wrapped its SQL in encode(), this would stop being a
    candidate and the version assertion below would pass without checking
    anything.
    """
    ids = {cid for _, cid, _, _ in _candidates()}
    assert "SovdResponseRule" in ids, (
        "SovdResponseRule is no longer classified as a raw passthrough, so the "
        "version requirement no longer applies to it. If that is intentional "
        "(e.g. it now base64-encodes), delete this test deliberately."
    )


# ---------------------------------------------------------------------------
# The actual requirement.
# ---------------------------------------------------------------------------

def test_raw_passthrough_rules_pin_sql_version_2016_03_23():
    offenders = []
    for mod, cid, sql, ver in _candidates():
        if cid in _KNOWN_GAPS:
            continue
        if ver != _REQUIRED_VERSION:
            offenders.append(
                f"{mod}::{cid} has aws_iot_sql_version={ver!r} "
                f"(need {_REQUIRED_VERSION!r}) for sql={sql[:70]!r}"
            )
    assert not offenders, (
        "IoT topic rule(s) pass a raw payload through the SQL engine without pinning "
        f"awsIotSqlVersion={_REQUIRED_VERSION}. On 2015-10-08 (the default when the "
        "property is omitted) nested arrays are dropped and nested objects flattened, "
        "silently corrupting the payload:\n  " + "\n  ".join(offenders)
    )


def test_sovd_rule_pins_the_version_explicitly():
    """Named assertion for the rule the issue was filed about.

    The sweep above would also catch this, but a named test means a future
    change to _KNOWN_GAPS cannot quietly exempt the SOVD rule.
    """
    assert "SovdResponseRule" not in _KNOWN_GAPS, (
        "SovdResponseRule must never be added to _KNOWN_GAPS — it is the rule the "
        "array-destruction defect was filed against."
    )
    found = [
        (mod, ver) for mod, cid, _, ver in _iter_topic_rules() if cid == "SovdResponseRule"
    ]
    assert found, "SovdResponseRule not found"
    for mod, ver in found:
        assert ver == _REQUIRED_VERSION, (
            f"{mod}::SovdResponseRule has aws_iot_sql_version={ver!r}, need "
            f"{_REQUIRED_VERSION!r}. Omitting or downgrading it re-introduces "
            "issues/2026-09-23-iot-rule-destroys-arrays-in-sovd-responses/ — arrays "
            "in every SOVD response are silently destroyed while rows still look healthy."
        )


def test_known_gaps_are_still_real():
    """Every _KNOWN_GAPS entry must still be an unfixed candidate.

    Stops the allowlist outliving the gaps it documents — an entry for a rule
    that has since been fixed (or deleted) would silently shrink the guard's
    reach.
    """
    by_id = {cid: ver for _, cid, _, ver in _candidates()}
    stale = []
    for cid in _KNOWN_GAPS:
        if cid not in by_id:
            stale.append(f"{cid} is no longer a raw-passthrough candidate")
        elif by_id[cid] == _REQUIRED_VERSION:
            stale.append(f"{cid} already pins {_REQUIRED_VERSION}")
    assert not stale, (
        "_KNOWN_GAPS is stale — remove these entries:\n  " + "\n  ".join(stale)
    )
