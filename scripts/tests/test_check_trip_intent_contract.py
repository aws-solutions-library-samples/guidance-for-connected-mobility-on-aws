#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``check_trip_intent_contract.py`` — spec T1.1.

Spec: ``.kiro/specs/2026-09-20-trip-intent-param-contract/tasks.md`` T1.1.

Every test that exercises a violation does so by pointing the guard at **synthetic
source files** in ``tmp_path``, never by editing the real ones. That keeps the mutation
tests hermetic and parallel-safe.

The four mutations the spec requires (M1–M4) each have a named test below, plus a fifth
case the first real run of the guard surfaced (a write-only-by-design key gaining a
reader, which must produce a *different* message from a deferred key gaining one).

Precedent for the import shim: ``scripts/tests/test_check_trip_simulator_modal_sync.py``.

Run from repo root::

    python3 -m pytest scripts/tests/test_check_trip_intent_contract.py -v
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

# ── Load the script as a module ───────────────────────────────────────────────
_SCRIPT_PATH = Path(__file__).parent.parent / "check_trip_intent_contract.py"
_spec = importlib.util.spec_from_file_location("check_trip_intent_contract", _SCRIPT_PATH)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_trip_intent_contract"] = guard
_spec.loader.exec_module(guard)


# ── Synthetic source builders ─────────────────────────────────────────────────
#
# Deliberately minimal, but structurally identical to the real files in the ways the
# guard depends on: the writer wraps the intent map in an `ExpressionAttributeValues`
# dict keyed by the `":i"` placeholder and also carries an unrelated `Key={...}` dict
# (so a test can catch a regression where the guard starts collecting `vehicleId`); the
# reader reads via `intent.get("literal")`.


def _writer_src(keys: list[str], *, func_name: str = "_write_trip_intent",
                placeholder: str = ":i") -> str:
    entries = "\n".join(f'                {k!r}: config.get({k!r}),' for k in keys)
    return f'''
def {func_name}(vehicle_id, sim_id, config):
    """Docstring mentioning city and routeLength to prove AST beats substring scanning."""
    vehicles_table.update_item(
        Key={{"vehicleId": vehicle_id}},
        UpdateExpression="SET tripIntent = :i",
        ExpressionAttributeValues={{
            {placeholder!r}: {{
{entries}
            }},
        }},
    )
'''


def _reader_src(keys: list[str], *, var: str = "intent") -> str:
    body = "\n".join(f'    x = {var}.get({k!r}, None)' for k in keys)
    return f'''
def presence_loop({var}):
{body or "    pass"}
'''


@pytest.fixture
def sources(tmp_path, monkeypatch):
    """Point the guard at synthetic files; return a writer/reader installer."""

    writer = tmp_path / "simulation_lambda.py"
    reader = tmp_path / "realtime_telemetry_simulator.py"

    def install(written: list[str], consumed: list[str], **kw) -> None:
        writer.write_text(
            _writer_src(
                written,
                func_name=kw.get("func_name", "_write_trip_intent"),
                placeholder=kw.get("placeholder", ":i"),
            ),
            encoding="utf-8",
        )
        reader.write_text(
            _reader_src(consumed, var=kw.get("reader_var", "intent")),
            encoding="utf-8",
        )

    monkeypatch.setattr(guard, "WRITER_PATH", writer)
    monkeypatch.setattr(guard, "READER_PATH", reader)
    return install


#: The real allowlist, as a list, for building synthetic sources that mirror reality.
#: **Empty since 2026-09-21** — T4.3 closed Tier B. Kept as a derived name rather than
#: inlined so the tests below keep mirroring the guard rather than a snapshot of it.
_DEFERRED = sorted(guard._DEFERRED_READERS)
_BY_DESIGN = sorted(guard._WRITE_ONLY_BY_DESIGN)
#: Today's real consumed set. Tier A landed 2026-09-20 (spec Group 2, T2.1) giving `city`
#: and `routeLength` readers; Tier B landed 2026-09-21 (Group 4, T4.2, commit `009b8b46`)
#: giving `safetyScenarios` and `maintenanceScenarios` readers via
#: `PresenceLoop._apply_catalog_overrides`. Both closures were the ratchet firing in its
#: closure direction on a real change. Tests that need to model either tier's reads being
#: ABSENT use the `_CONSUMED_PRE_*` shapes below rather than hand-trimming this list, so
#: the scenarios stay distinguishable.
_CONSUMED = [
    "simulationId",
    "tripsCount",
    "city",
    "routeLength",
    "safetyScenarios",
    "maintenanceScenarios",
]
#: The pre-Tier-A shape, kept only for the "someone deleted the new reads" regression.
_CONSUMED_PRE_TIER_A = ["simulationId", "tripsCount"]
#: The pre-Tier-B shape — Tier A's reads present, Tier B's not.
_CONSUMED_PRE_TIER_B = ["simulationId", "tripsCount", "city", "routeLength"]
#: A written set that exactly reproduces today's real-world state.
_WRITTEN_TODAY = _CONSUMED + _DEFERRED + _BY_DESIGN

#: A synthetic deferred key, for exercising the closure ratchet now that the real
#: allowlist is empty. The ratchet's whole value is its closure direction, and with
#: nothing allowlisted there is no real key left that can make the allowlist stale — so
#: the tests that guard that direction inject one. Using a synthetic name rather than
#: re-aiming at a real key keeps those tests honest as tiers close: a test pointed at a
#: key that has since gained a reader passes without exercising its branch, which is how
#: this file's own M2 test went vacuous twice (once at Tier A, once at Tier B).
_SYNTHETIC_DEFERRED = "someFutureKnob"
_SYNTHETIC_DEFERRED_2 = "someOtherFutureKnob"


# ── The real repo ─────────────────────────────────────────────────────────────


def test_contract_holds_against_the_real_sources():
    """The guard is green on the repo as committed, with the key sets pinned exactly.

    Pins the sets rather than just asserting exit 0 plus a substring. A count- or
    substring-only assertion passes while the contract silently reshapes underneath it —
    which is the presence-vs-property distinction this repo keeps getting bitten by. A
    legitimate contract change must update this test deliberately.
    """
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)

    written = guard.written_keys(guard._parse(guard.WRITER_PATH))
    consumed = guard.consumed_keys(guard._parse(guard.READER_PATH))

    assert written == {
        "simulationId",
        "tripsCount",
        "requestedAt",
        "agentTaskArn",
        "uds_dtc_map",
        "city",
        "routeLength",
        "safetyScenarios",
        "maintenanceScenarios",
    }
    assert consumed == {
        "simulationId",
        "tripsCount",
        "city",
        "routeLength",
        "safetyScenarios",
        "maintenanceScenarios",
    }
    # `vehicleId` lives in the sibling `Key={...}` dict and is NOT part of this contract.
    assert "vehicleId" not in written
    # The allowlist must describe reality exactly, not merely cover it.
    assert written - consumed == set(guard._KNOWN_UNCONSUMED)
    assert consumed - written == set(), "a read with no write is a silent no-op"


def test_real_sources_satisfy_the_required_key_properties():
    """Guards the guard: the structurally-required keys must be found on both sides.

    If this fails, the extractor is broken rather than the contract — the presence loop
    cannot function without either key.
    """
    written = guard.written_keys(guard._parse(guard.WRITER_PATH))
    consumed = guard.consumed_keys(guard._parse(guard.READER_PATH))
    assert guard._REQUIRED_WRITTEN <= written
    assert guard._REQUIRED_CONSUMED <= consumed
    assert len(written) >= guard._MIN_WRITTEN_KEYS


# ── M1: a new written key with no reader ──────────────────────────────────────


def test_m1_new_written_key_without_reader_fails(sources):
    """M1 — the defect class itself. A 10th key with no reader must fail, by name."""
    sources(_WRITTEN_TODAY + ["brandNewKnob"], _CONSUMED)
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "brandNewKnob" in blob
    assert "written with no reader" in blob


def test_m1_control_same_key_with_a_reader_passes(sources):
    """Positive control for M1 — the same key, now consumed, is clean."""
    sources(_WRITTEN_TODAY + ["brandNewKnob"], _CONSUMED + ["brandNewKnob"])
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)


# ── M2: the deferred ratchet ──────────────────────────────────────────────────


def test_known_unconsumed_entries_are_still_real(sources, monkeypatch):
    """M2 — a deferred key that gained a reader must force the allowlist to shrink.

    This is the closure ratchet, and it is the direction that matters over time: an
    allowlist nobody is forced to shrink becomes a permanent exemption.

    Uses a SYNTHETIC deferred key. The real `_DEFERRED_READERS` is empty as of
    2026-09-21 (T4.3), so no real key can make the allowlist stale, and a test aimed at
    one would pass without reaching this branch. That has now happened twice in this
    file's history — the test was aimed at `city` until Tier A closed, then at
    `safetyScenarios` until Tier B closed. A synthetic key cannot close underneath it.
    """
    monkeypatch.setattr(
        guard, "_DEFERRED_READERS", frozenset({_SYNTHETIC_DEFERRED})
    )
    monkeypatch.setattr(
        guard,
        "_KNOWN_UNCONSUMED",
        frozenset(guard._WRITE_ONLY_BY_DESIGN) | {_SYNTHETIC_DEFERRED},
    )
    # The synthetic key is written, allowlisted as deferred, AND has a reader.
    sources(
        _WRITTEN_TODAY + [_SYNTHETIC_DEFERRED],
        _CONSUMED + [_SYNTHETIC_DEFERRED],
    )
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "_DEFERRED_READERS is stale" in blob
    assert _SYNTHETIC_DEFERRED in blob


def test_m2_partial_closure_requires_emptying_every_closed_key(sources, monkeypatch):
    """Landing a tier fails until EVERY key of that tier leaves the allowlist.

    Closing one of two deferred keys is not enough — the ratchet must still fail, naming
    both, so a half-finished closure cannot land quietly. This was the shape Tier B
    actually had (`safetyScenarios` + `maintenanceScenarios` closing together in T4.2);
    now expressed with synthetic keys for the reason in the test above.
    """
    deferred = {_SYNTHETIC_DEFERRED, _SYNTHETIC_DEFERRED_2}
    monkeypatch.setattr(guard, "_DEFERRED_READERS", frozenset(deferred))
    monkeypatch.setattr(
        guard,
        "_KNOWN_UNCONSUMED",
        frozenset(guard._WRITE_ONLY_BY_DESIGN) | deferred,
    )
    sources(_WRITTEN_TODAY + sorted(deferred), _CONSUMED + sorted(deferred))
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert _SYNTHETIC_DEFERRED in blob and _SYNTHETIC_DEFERRED_2 in blob


def test_m2_control_shrinking_the_allowlist_makes_it_green(sources, monkeypatch):
    """Positive control — with the allowlist correctly shrunk, the tier is clean.

    Asserts the ratchet is satisfiable and not merely strict. It played this role for
    Group 2 and Group 4; both of those closures are now pinned by
    `test_contract_holds_against_the_real_sources` against the real sources, so this
    keeps using synthetic keys to stay independent of the current real state.
    """
    deferred = {_SYNTHETIC_DEFERRED, _SYNTHETIC_DEFERRED_2}
    # Allowlist NOT patched to contain them — this is the post-closure state.
    monkeypatch.setattr(guard, "_DEFERRED_READERS", frozenset())
    monkeypatch.setattr(
        guard, "_KNOWN_UNCONSUMED", frozenset(guard._WRITE_ONLY_BY_DESIGN)
    )
    sources(_WRITTEN_TODAY + sorted(deferred), _CONSUMED + sorted(deferred))
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)


# ── The case the first real run surfaced ──────────────────────────────────────


def test_write_only_by_design_gaining_a_reader_reports_distinctly(sources):
    """A write-only key gaining a reader is a design change, not a closed defect.

    The two must not share a message: `agentTaskArn` gaining a reader means the
    "future anti-misrouting logic" its docstring anticipates has landed, and the
    docstring needs updating — a different instruction from "remove the exemption".
    """
    sources(_WRITTEN_TODAY, _CONSUMED + ["agentTaskArn"])
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "_WRITE_ONLY_BY_DESIGN is stale" in blob
    assert "_DEFERRED_READERS is stale" not in blob
    assert "docstring" in blob


# ── M3 / M4: anti-vacuity floors ──────────────────────────────────────────────


def test_m3_writer_extraction_breaking_trips_the_floor(sources):
    """M3 — a writer that yields too few keys must fail loudly, not report clean."""
    sources(["simulationId", "tripsCount"], _CONSUMED)
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "extractor is broken" in blob
    assert "written key" in blob


def test_m3_writer_function_renamed_is_loud(sources):
    """A renamed writer function is an extraction failure, never a silent pass."""
    sources(_WRITTEN_TODAY, _CONSUMED, func_name="_write_intent_v2")
    code, lines = guard.check()
    assert code == 1
    assert "extraction failed" in "\n".join(lines)


def test_m3_placeholder_changed_is_loud(sources):
    """A changed ExpressionAttributeValues placeholder is an extraction failure."""
    sources(_WRITTEN_TODAY, _CONSUMED, placeholder=":intent")
    code, lines = guard.check()
    assert code == 1
    assert "extraction failed" in "\n".join(lines)


def test_m4_reads_rebound_to_an_unknown_name_is_a_binding_fault(sources):
    """M4a — reads that exist under an unrecognised binding must NOT read as "no reader".

    This is review Cycle 2's W4. With the reads live under a different binding, the guard
    previously reported `written with no reader: city, routeLength` and then suggested
    adding them to `_DEFERRED_READERS` — the one remedy that, given a one-way ratchet,
    would permanently excuse working code.

    The message must name the binding, and must explicitly forbid allowlisting.
    """
    sources(_WRITTEN_TODAY, _CONSUMED, reader_var="payload")
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "unrecognised binding" in blob
    assert "payload" in blob
    assert "DO NOT add these keys" in blob
    assert "written with no reader" not in blob


def test_m4_reads_genuinely_absent_is_an_extractor_fault(sources):
    """M4b — when the required keys are read nowhere at all, blame the extractor.

    Distinct from M4a: there is no other receiver to point at, so the honest finding is
    that extraction (or configuration) is broken, naming the knob to check. A count floor
    could not make this distinction at all, which is why it was replaced by a required-key
    property in Fix Group 1.
    """
    sources(_WRITTEN_TODAY, [])
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "extraction looks broken" in blob
    assert "READER_VARS" in blob
    assert "contract violated" not in blob


def test_post_tier_a_reads_genuinely_deleted_is_a_contract_fault(sources, monkeypatch):
    """Post-Tier-A, if the new reads are DELETED outright, that is a real contract fault.

    Renamed from `..._is_still_an_extractor_fault`, which was a misnomer: this scenario
    omits the reads rather than rebinding them, so the guard correctly reports a contract
    fault, and the old name promised an assertion the body never made. Review Cycle 2
    flagged it as presence-vs-property inside the verification of a fix.

    The rebinding scenario the old name described is covered by
    `test_post_tier_a_reads_rebound_is_a_binding_fault`.

    Since Tier A landed this is no longer a hypothetical future state — it is the
    regression guard for someone ripping T2.1's reads back out. The monkeypatch it used
    to need is gone: `_DEFERRED_READERS` really is Tier-B-only now, so nothing excuses
    `city`/`routeLength` any more, which is exactly what makes this a contract fault.
    """
    sources(_WRITTEN_TODAY, _CONSUMED_PRE_TIER_A)
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "written with no reader" in blob
    assert "city" in blob and "routeLength" in blob


def test_post_tier_a_reads_rebound_is_a_binding_fault(sources, monkeypatch):
    """Post-Tier-A with the reads moved to an unrecognised binding — W4's exact shape.

    Reproduces the helper-extraction that spec § "Apply/restore shape" describes, with a
    binding name NOT in `READER_VARS`. Must be a binding fault, never a contract fault,
    because telling the implementer these keys are unread is false and the suggested
    remedy would be irreversible.

    Post-Tier-A this is the guard against T2.1's helper being renamed to an undeclared
    receiver — the live shape of the hazard, not a hypothetical one.
    """
    # Install both files first, then overwrite only the reader.
    sources(_WRITTEN_TODAY, _CONSUMED_PRE_TIER_A)
    # Tier B's two reads go through the DECLARED binding, so the only fault in this
    # reader is `tp`. Added 2026-09-21 (T4.3): before Tier B landed, omitting them left
    # them unwritten-with-no-reader and this test failed for the wrong reason.
    guard.READER_PATH.write_text(
        "def loop(intent):\n"
        "    a = intent.get('simulationId', '')\n"
        "    b = intent.get('tripsCount', 1)\n"
        "    e = intent.get('safetyScenarios', [])\n"
        "    f = intent.get('maintenanceScenarios', [])\n"
        "    tp = intent\n"
        "    c = tp.get('city', '')\n"
        "    d = tp.get('routeLength', 0)\n",
        encoding="utf-8",
    )
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "unrecognised binding" in blob
    assert "tp" in blob
    assert "written with no reader" not in blob
    assert "DO NOT add these keys" in blob


def test_helper_binding_in_reader_vars_is_accepted(sources, monkeypatch):
    """Positive control — the same helper shape passes once its binding is declared.

    `intent_map` ships in `READER_VARS` precisely so the T2.1 implementer can extract the
    apply/restore helper the spec describes without tripping the guard. A guard that only
    ever rejects has not been shown to be satisfiable.

    T2.1 took exactly this shape (`_resolve_trip_overrides(self, intent_map)`), so the
    real repo is now the strongest instance of this control; this synthetic one is kept
    because it isolates the binding question from everything else in that 6,800-line file.
    """
    sources(_WRITTEN_TODAY, _CONSUMED_PRE_TIER_A)
    guard.READER_PATH.write_text(
        "def _apply_trip_overrides(self, intent_map):\n"
        "    c = intent_map.get('city', '')\n"
        "    d = intent_map.get('routeLength', 0)\n"
        "def _apply_catalog_overrides(self, intent_map):\n"
        "    e = intent_map.get('safetyScenarios', [])\n"
        "    f = intent_map.get('maintenanceScenarios', [])\n"
        "def loop(intent):\n"
        "    a = intent.get('simulationId', '')\n"
        "    b = intent.get('tripsCount', 1)\n",
        encoding="utf-8",
    )
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)


# ── Orphan reads (a read with no write) ───────────────────────────────────────


def test_orphan_read_is_reported(sources):
    """A key read but never written always resolves to its default — a silent no-op.

    The live hazard is a spelling slip: the writer translates snake_case to camelCase at
    the boundary (`"routeLength": config.get("route_length")`), so both spellings are
    correct in different places in that one file. Without this check, a reader using
    `route_length` would register as "3 consumed" — a progress signal on a no-op — while
    `routeLength` stayed listed as awaiting a reader.
    """
    sources(_WRITTEN_TODAY, _CONSUMED + ["route_length"])
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "read but never written" in blob
    assert "route_length" in blob
    assert "snake_case" in blob


def test_orphan_read_control_correct_spelling_is_not_an_orphan(sources):
    """Positive control — the correctly-spelled key is clean, not an orphan.

    Before Tier A landed this asserted that `routeLength` tripped the *ratchet* (it was
    written, read, and still allowlisted). Now that it has legitimately left
    `_DEFERRED_READERS`, consuming it is simply correct, so the honest assertion is
    exit 0 with no orphan message. The ratchet direction is covered against a key that
    is still allowlisted by `test_known_unconsumed_entries_are_still_real`.
    """
    sources(_WRITTEN_TODAY, _CONSUMED + ["routeLength"])
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)
    assert "read but never written" not in "\n".join(lines)


# ── A deleted write must not be reported as a gained reader ───────────────────


def test_deleted_write_is_not_reported_as_a_gained_reader(sources):
    """Removing an allowlisted key's write must not claim it "now HAS readers".

    `_DEFERRED_READERS - unconsumed` treated "absent from unconsumed" as "gained a
    reader", but a key that is no longer *written* is also absent from unconsumed — so
    deleting the `city` write reported the exact opposite of the truth.

    Re-aimed twice as tiers closed: from `city` (Tier A), to `safetyScenarios`
    (Tier B), and now to `agentTaskArn`. The branch under test intersects the allowlist
    with the written set, so it can only fire for a key that is still in
    `_KNOWN_UNCONSUMED`; with `_DEFERRED_READERS` empty as of T4.3, the only remaining
    real members are `_WRITE_ONLY_BY_DESIGN`. Aimed at a real still-allowlisted key
    rather than a synthetic one on purpose — this asserts the message the guard emits
    about the actual repo, which is what a future reader deleting a write would see.
    """
    written_without_by_design = [k for k in _WRITTEN_TODAY if k != "agentTaskArn"]
    sources(written_without_by_design, _CONSUMED)
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "no longer written at all" in blob
    assert "agentTaskArn" in blob
    assert "now HAVE readers" not in blob


# ── Computed writer keys are rejected, not skipped ────────────────────────────


def test_computed_writer_key_is_rejected_not_skipped(tmp_path, monkeypatch):
    """A computed key on the writer side would evade the guard's primary assertion.

    `('brandNew' + 'Knob')` as a key previously yielded `exit 0` and `9 keys written` —
    the 10th key invisible. Since M1 (a new written key with no reader) is this guard's
    reason to exist, an evasion of it must fail loudly rather than be silently ignored.
    """
    writer = tmp_path / "w.py"
    writer.write_text(
        _writer_src(_WRITTEN_TODAY).replace(
            "'uds_dtc_map': config.get('uds_dtc_map'),",
            "'uds_dtc_map': config.get('uds_dtc_map'),\n"
            "                ('brandNew' + 'Knob'): config.get('x'),",
        ),
        encoding="utf-8",
    )
    reader = tmp_path / "r.py"
    reader.write_text(_reader_src(_CONSUMED), encoding="utf-8")
    monkeypatch.setattr(guard, "WRITER_PATH", writer)
    monkeypatch.setattr(guard, "READER_PATH", reader)

    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "non-literal key" in blob
    assert "computed contract" in blob


def test_dict_unpacking_in_the_intent_map_is_rejected(tmp_path):
    """`**spread` also makes the key set statically undeterminable."""
    src = tmp_path / "w.py"
    src.write_text(
        '''
def _write_trip_intent(vehicle_id, sim_id, config):
    vehicles_table.update_item(
        Key={"vehicleId": vehicle_id},
        ExpressionAttributeValues={
            ":i": {
                "simulationId": sim_id,
                **extra_keys,
            },
        },
    )
''',
        encoding="utf-8",
    )
    with pytest.raises(guard.ExtractionError, match="dict unpacking"):
        guard.written_keys(guard._parse(src))


# ── Extractor unit behaviour ──────────────────────────────────────────────────


def test_written_keys_ignores_the_sibling_key_dict(tmp_path):
    """`Key={"vehicleId": ...}` must not leak into the written set."""
    src = tmp_path / "w.py"
    src.write_text(_writer_src(["simulationId", "city"]), encoding="utf-8")
    assert guard.written_keys(guard._parse(src)) == {"simulationId", "city"}


def test_consumed_keys_ignores_gets_on_other_objects(tmp_path):
    """Only `intent.get(...)` counts — `config.get(...)` is a different object."""
    src = tmp_path / "r.py"
    src.write_text(
        '''
def loop(intent, config):
    a = intent.get("simulationId")
    b = config.get("city")
    c = os.environ.get("tripsCount")
''',
        encoding="utf-8",
    )
    assert guard.consumed_keys(guard._parse(src)) == {"simulationId"}


def test_consumed_keys_ignores_computed_key_names(tmp_path):
    """A computed key name is invisible to the guard — the documented limitation.

    Asserted rather than left implicit so the gap is a known, tested property instead of
    a surprise the next reader has to rediscover.
    """
    src = tmp_path / "r.py"
    src.write_text(
        '''
def loop(intent, name):
    a = intent.get("simulationId")
    b = intent.get(name)
    c = intent.get("city" if flag else "routeLength")
''',
        encoding="utf-8",
    )
    assert guard.consumed_keys(guard._parse(src)) == {"simulationId"}


def test_missing_file_is_an_extraction_error(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "WRITER_PATH", tmp_path / "nope.py")
    code, lines = guard.check()
    assert code == 1
    assert "extraction failed" in "\n".join(lines)


def test_main_returns_the_check_exit_code(capsys):
    assert guard.main() == 0
    assert "contract holds" in capsys.readouterr().out
