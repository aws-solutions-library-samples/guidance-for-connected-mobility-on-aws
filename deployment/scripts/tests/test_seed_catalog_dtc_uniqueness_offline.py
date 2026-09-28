"""Offline guards for DTC-code uniqueness across every event-catalog producer.

Companion to tests/test_dtc_catalog_uniqueness.py, which asserts the same
invariant but is @pytest.mark.integration — it reads the live DDB table, so it
cannot fail until someone has already seeded a colliding row into an
environment. These tests read the seeders' static data instead, so a colliding
constant fails at commit time with no AWS credentials.

Why this file exists (2026-09-09): live cms-staging-event-catalog acquired a
P0118 collision between maintenance.high_engine_temp and diagnostics.dtc.P0118.
The root cause was not a bad value in isolation but an asymmetry between
producers:

  seed_dtc_catalog_gap_fill.py  checks `if dtc_code in existing_dtc_codes` and
                                skips (line ~409, "any entry, any event_id").
  seed_event_catalog.py         writes its static collections unconditionally.

Whichever producer runs last wins, and only one of them is guarded. The
2026-09-02 P1 remediation (dedup_dtc_catalog.py) fixed the *rows* and never the
*producers*, which is why the class recurred against a different code.

A non-unique dtc_code means the verdict served for that code depends on DDB scan
order, which is how a P0 stop-driving instruction becomes a routine notice.

Design note — producers are DISCOVERED, not enumerated. The first version of
this file hard-coded seed_event_catalog.EVENTS and passed while C0035 was
duplicated one variable away in OEM1_EVENTS. A guard scoped narrower than its
invariant is the defect it is meant to catch, so collection discovery is part of
the guard and test_producer_discovery_is_not_silently_empty defends it.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import seed_event_catalog as seed_events  # noqa: E402
import seed_dtc_catalog_gap_fill as gap_fill  # noqa: E402

# Collections that complete a field on rows already owned elsewhere rather than
# claiming ownership of a code. Excluded by name, deliberately and narrowly.
_NOT_OWNERSHIP_CLAIMS = {"SEVERITY_HINT_COMPLETIONS"}

_MODULES = ((seed_events, "seed_event_catalog"), (gap_fill, "seed_dtc_catalog_gap_fill"))


def _discover_producers() -> dict:
    """Every module-level list-of-dict collection carrying dtc_code values.

    Discovery rather than enumeration: a new collection added to either seeder
    is covered automatically instead of silently escaping the invariant.
    """
    producers: dict = {}
    for mod, modname in _MODULES:
        for name, val in vars(mod).items():
            if name in _NOT_OWNERSHIP_CLAIMS:
                continue
            if not (isinstance(val, list) and val and isinstance(val[0], dict)):
                continue
            if any(e.get("dtc_code") for e in val):
                producers[f"{modname}.{name}"] = val
    return producers


def _claims(producers: dict) -> dict:
    """dtc_code -> [(producer, event_id, severity_hint), ...]"""
    claims: dict = {}
    for pname, entries in producers.items():
        for e in entries:
            code = e.get("dtc_code")
            if code:
                claims.setdefault(code, []).append(
                    (pname, e.get("event_id"), e.get("severity_hint"))
                )
    return claims


# --------------------------------------------------------------------------
# The invariant: across every producer, each dtc_code has exactly one owner.
# --------------------------------------------------------------------------
def test_every_dtc_code_has_exactly_one_owner_across_all_producers():
    dups = {c: v for c, v in _claims(_discover_producers()).items() if len(v) > 1}
    if dups:
        lines = [
            "The same dtc_code is claimed by more than one catalog producer.",
            "Seeding these produces a non-unique key, so the verdict served for the",
            "code depends on DDB scan order — a P0 stop-driving instruction can be",
            "silently replaced by a routine notice.",
            "",
        ]
        for code, owners in sorted(dups.items()):
            lines.append(f"  {code}:")
            for pname, event_id, hint in owners:
                lines.append(f"    {hint or '??':4} {event_id:42} [{pname}]")
            hints = {h for _, _, h in owners}
            if len(hints) > 1:
                lines.append(f"    ^ conflicting severity_hint across owners: {sorted(hints)}")
        pytest.fail("\n".join(lines))


# --------------------------------------------------------------------------
# Premise guards — the invariant test above is vacuous if discovery returns
# nothing, or if dtc_code is renamed. Both have to fail loudly instead.
# --------------------------------------------------------------------------
def test_producer_discovery_is_not_silently_empty():
    producers = _discover_producers()
    assert len(producers) >= 3, (
        f"expected at least 3 code-carrying collections, found {sorted(producers)}. "
        "A collection was renamed or removed — widen discovery before trusting the "
        "uniqueness result above."
    )
    # Named explicitly so a rename is a failure rather than a silent narrowing.
    for expected in (
        "seed_event_catalog.EVENTS",
        "seed_event_catalog.OEM1_EVENTS",
        "seed_dtc_catalog_gap_fill.NEW_DTC_ENTRIES",
    ):
        assert expected in producers, f"{expected} no longer discovered"


def test_producers_actually_carry_codes():
    total = sum(
        1 for entries in _discover_producers().values() for e in entries if e.get("dtc_code")
    )
    assert total > 30, (
        f"only {total} dtc_code values found across all producers — did the field "
        "get renamed? The uniqueness test cannot fail on data it cannot see."
    )


# --------------------------------------------------------------------------
# Negative control — prove the invariant test can actually fail.
# --------------------------------------------------------------------------
def test_guard_detects_an_injected_cross_producer_collision():
    producers = _discover_producers()
    names = sorted(producers)
    poisoned = dict(producers)
    poisoned[names[0]] = list(producers[names[0]]) + [
        {"event_id": "test.injected_a", "dtc_code": "ZZ999", "severity_hint": "P0"}
    ]
    poisoned[names[1]] = list(producers[names[1]]) + [
        {"event_id": "test.injected_b", "dtc_code": "ZZ999", "severity_hint": "P3"}
    ]
    dups = {c: v for c, v in _claims(poisoned).items() if len(v) > 1}
    assert "ZZ999" in dups, "guard cannot see an injected cross-producer collision"
    assert len(dups["ZZ999"]) == 2
