# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Tests for clear_false_coolant_dtcs.py.

Spec: .kiro/specs/2026-09-25-cms-coolant-threshold-units/ (Group 3a)

The DynamoDB table is a stub. The live run (Group 3b, dry run first) is the
live verification for this script.
"""
import io
import os
import sys

import pytest

_SCRIPTS = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, _SCRIPTS)
import clear_false_coolant_dtcs as mod  # noqa: E402
import seed_event_catalog  # noqa: E402

OVERHEAT = "maintenance.coolant_critical_overheat"
RUNAWAY = "maintenance.thermal_runaway"


def _row(code="P0217", actual=185.1, trigger=OVERHEAT, status="ACTIVE",
         cleared="", vid="VEH-A", ts=1, source="flink-maintenance-processor"):
    desc = "Engine coolant critically overheated — stop driving"
    if actual is not None:
        desc += f" — coolant_temp > 125.0 (actual: {actual:.1f})"
    row = {"vehicleId": vid, "timestamp": ts, "code": code, "status": status,
           "clearedDate": cleared, "description": desc, "source": source}
    if trigger is not None:
        row["triggerEventId"] = trigger
    return row


class _ConditionFailed(Exception):
    response = {"Error": {"Code": "ConditionalCheckFailedException"}}


class StubTable:
    def __init__(self, pages, fail_keys=()):
        self.pages = list(pages)
        self.scan_calls = []
        self.updates = []
        self.fail_keys = set(fail_keys)

    def scan(self, **kwargs):
        self.scan_calls.append(dict(kwargs))
        i = len(self.scan_calls) - 1
        resp = {"Items": self.pages[i]}
        if i + 1 < len(self.pages):
            resp["LastEvaluatedKey"] = {"vehicleId": f"page{i}", "timestamp": i}
        return resp

    def update_item(self, **kwargs):
        key = (kwargs["Key"]["vehicleId"], kwargs["Key"]["timestamp"])
        if key in self.fail_keys:
            raise _ConditionFailed()
        self.updates.append(kwargs)


# ── thresholds match the seeds ───────────────────────────────────────────────

def test_rule_table_matches_seed_event_catalog():
    seeded = {}
    for val in vars(seed_event_catalog).values():
        if isinstance(val, list) and val and isinstance(val[0], dict):
            for e in val:
                if e.get("event_id") in (OVERHEAT, RUNAWAY):
                    seeded[e["dtc_code"]] = (e["event_id"], float(e["threshold_value"]))
    assert seeded == mod.COOLANT_RULES


# ── predicate ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize("code,trigger,threshold", [
    ("P0217", OVERHEAT, 257.0),
    ("B0001_FIRE", RUNAWAY, 280.0),
])
def test_reading_at_threshold_is_false(code, trigger, threshold):
    assert mod.classify(_row(code=code, trigger=trigger, actual=threshold)) == "false"


@pytest.mark.parametrize("code,trigger,threshold", [
    ("P0217", OVERHEAT, 257.0),
    ("B0001_FIRE", RUNAWAY, 280.0),
])
def test_reading_below_threshold_is_false(code, trigger, threshold):
    assert mod.classify(_row(code=code, trigger=trigger, actual=threshold - 70)) == "false"


@pytest.mark.parametrize("code,trigger,threshold", [
    ("P0217", OVERHEAT, 257.0),
    ("B0001_FIRE", RUNAWAY, 280.0),
])
def test_reading_above_threshold_is_kept(code, trigger, threshold):
    # A genuine overheat must never be cleared.
    assert mod.classify(_row(code=code, trigger=trigger, actual=threshold + 0.1)) is None


def test_other_rule_mapping_to_p0217_is_kept():
    # maintenance.high_engine_temp (EngineTemp > 230 °F) also emits P0217 and is correct.
    assert mod.classify(_row(trigger="maintenance.high_engine_temp", actual=231.8)) is None


def test_row_without_reading_is_residue_not_false():
    assert mod.classify(_row(actual=None, trigger=None, source=None)) == "residue"
    assert mod.classify(_row(actual=None, trigger=OVERHEAT, source="fwe-uds-dtc")) == "residue"


def test_fire_row_without_reading_is_kept():
    assert mod.classify(_row(code="B0001_FIRE", trigger=RUNAWAY, actual=None)) is None


@pytest.mark.parametrize("status,cleared", [("CLEARED", "2026-09-25"), ("ACTIVE", "2026-09-25"),
                                            ("RESOLVED", "")])
def test_already_inactive_rows_are_kept(status, cleared):
    assert mod.classify(_row(status=status, cleared=cleared)) is None


def test_unrelated_code_is_kept():
    assert mod.classify(_row(code="P0562", trigger=OVERHEAT)) is None


def test_parse_actual_reads_only_the_trailing_suffix():
    assert mod.parse_actual("x (actual: 185.1)") == 185.1
    assert mod.parse_actual("x (actual: 185.1) trailing") is None
    assert mod.parse_actual(None) is None


# ── scan and writes ─────────────────────────────────────────────────────────

def test_scan_follows_last_evaluated_key_to_the_end():
    t = StubTable([[_row(vid="A")], [_row(vid="B")], [_row(vid="C")]])
    rows = list(mod.iter_candidate_rows(t))
    assert [r["vehicleId"] for r in rows] == ["A", "B", "C"]
    assert "ExclusiveStartKey" not in t.scan_calls[0]
    assert t.scan_calls[1]["ExclusiveStartKey"] == {"vehicleId": "page0", "timestamp": 0}


def test_dry_run_writes_nothing():
    t = StubTable([[_row(vid="A"), _row(vid="B", actual=None, trigger=None)]])
    counts = mod.run(t, apply=False, include_residue=True, out=io.StringIO())
    assert t.updates == []
    assert counts["false"] == 1 and counts["residue"] == 1


def test_main_defaults_to_dry_run():
    t = StubTable([[_row()]])
    assert mod.main(["--stage", "staging", "--region", "us-west-2"],
                    table_factory=lambda s, r: t) == 0
    assert t.updates == []


def test_apply_writes_mark_cleared_semantics_conditionally():
    t = StubTable([[_row(vid="A", ts=7)]])
    mod.run(t, apply=True, include_residue=False, out=io.StringIO(), now_iso="2026-09-25T22:00:00Z")
    assert len(t.updates) == 1
    u = t.updates[0]
    assert u["Key"] == {"vehicleId": "A", "timestamp": 7}
    assert u["ConditionExpression"] == "#s = :active"
    assert u["ExpressionAttributeValues"][":active"] == "ACTIVE"
    assert u["ExpressionAttributeValues"][":cleared"] == "CLEARED"
    assert u["ExpressionAttributeValues"][":d"] == "2026-09-25T22:00:00Z"
    assert u["ExpressionAttributeValues"][":r"] == mod.REASON_FALSE
    assert "REMOVE activeCode" in u["UpdateExpression"]


def test_residue_needs_the_flag():
    t = StubTable([[_row(vid="A", actual=None, trigger=None)]])
    mod.run(t, apply=True, include_residue=False, out=io.StringIO())
    assert t.updates == []


def test_residue_is_cleared_with_its_own_reason():
    t = StubTable([[_row(vid="A", actual=None, trigger=None)]])
    mod.run(t, apply=True, include_residue=True, out=io.StringIO())
    assert [u["ExpressionAttributeValues"][":r"] for u in t.updates] == [mod.REASON_RESIDUE]


def test_row_cleared_concurrently_is_skipped_not_fatal():
    t = StubTable([[_row(vid="A", ts=1), _row(vid="B", ts=2)]], fail_keys={("A", 1)})
    counts = mod.run(t, apply=True, include_residue=False, out=io.StringIO())
    assert counts["skipped"] == 1 and counts["cleared"] == 1


def test_genuine_overheat_is_never_written_even_with_apply():
    t = StubTable([[_row(actual=300.0), _row(code="B0001_FIRE", trigger=RUNAWAY, actual=290.0)]])
    mod.run(t, apply=True, include_residue=True, out=io.StringIO())
    assert t.updates == []


def test_output_lines_carry_no_description_text():
    out = io.StringIO()
    mod.run(StubTable([[_row()]]), apply=False, include_residue=False, out=out)
    assert "stop driving" not in out.getvalue()
    assert "would-clear\tVEH-A\tP0217" in out.getvalue()
