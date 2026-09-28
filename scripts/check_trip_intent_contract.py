#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify the ``tripIntent`` contract: every key written has a reader.

Spec: `.kiro/specs/2026-09-20-trip-intent-param-contract/spec.md` T1.1.
Issue: `issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/`.

## Why this exists

``tripIntent`` is a DynamoDB map written by the simulation Lambda and read by the
simulator's presence loop running inside the vehicle-ecu sidecar. The two sides live in
different files, ship in different artifacts (a Lambda zip and a container image), and
have no shared schema.

On 2026-09-20 that gap produced a silent, user-visible defect: the Lambda wrote `city`,
`routeLength`, `safetyScenarios`, and `maintenanceScenarios`; the simulator read none of
them. A request for Munich with route length 60 returned `200 {"success": true}` and ran
in Seattle with route length 20.

**Every test on both sides passed, and always had.** The writer's tests assert the map
contains the keys. The simulator's tests pass parameters directly to ``run_trips``.
Neither side's tests can observe the other — the contract had no owner. This script is
that owner.

Same defect shape as this repo's context-key threading bugs
(`issues/2026-09-13-cs-consumer-context-keys-never-threaded/`,
`issues/2026-09-12-cs-portal-subscriptions-endpoint-never-threaded/`): a declared input
with no compiler and no single owner.

## Why a ratchet rather than a plain equality assert

The two sides diverge *today* by four keys. A plain `written == consumed` assertion would
be red on arrival, which in practice means it gets skipped, marked xfail, or wired into CI
"later" — and guards nothing in the interim.

Instead this asserts `written - consumed == _KNOWN_UNCONSUMED` **exactly**. That is green
today and fails in both directions:

  * a NEW key written with no reader  -> fails (the defect class, caught immediately)
  * a key in `_KNOWN_UNCONSUMED` that HAS gained a reader -> fails (the closure ratchet,
    so landing the fix forces the exemption to shrink instead of leaving a stale
    allowlist entry that quietly re-permits the bug later)

The second direction is the one that matters over time. An allowlist nobody is forced to
shrink becomes a permanent exemption. This repo has already been bitten by a guard whose
`_KNOWN_GAPS` went stale — see the repo-wide CDK context-key threading lint, whose own
`test_known_gaps_are_still_real` enumerated 16 stale entries at once.

## Why AST and not regex

Both files parse cleanly, and AST cannot be fooled by a key name appearing in a comment,
a docstring, or a log format string. The writer's four parameter keys are in fact
*mentioned* in a nearby comment block, so a naive substring scan of the writer would
report them as written even if the write were deleted.

## Known limitations

Three gaps, all deliberate and all tested so they stay known rather than becoming
surprises:

**1. Reads through an undeclared binding are detected, not silently missed — but they are
not automatically accepted either.** ``consumed_keys`` counts reads through the names in
``READER_VARS``. A read through any other receiver is found by ``keys_by_receiver`` and
reported as a **binding fault**: the guard says which name was used, tells you to add it to
``READER_VARS`` if it is legitimate, and explicitly forbids allowlisting the key.

This distinction was added in review Cycle 2, which showed the earlier behaviour was worse
than a false negative. With ``city``/``routeLength`` read correctly inside a helper
(``_apply_trip_overrides(self, intent_map)`` — the shape spec § "Apply/restore shape"
describes), the guard reported them as having no reader and then suggested adding them to
``_DEFERRED_READERS``. Because the ratchet is one-way, following that suggestion would have
permanently excused working code and re-permitted the original defect alongside it.

What remains genuinely invisible: a **computed** key name (``intent.get(name)``), and a read
through an attribute or subscript receiver rather than a plain name. Pinned by
``test_consumed_keys_ignores_computed_key_names``. The guard also cannot prove that a
non-``READER_VARS`` receiver really is the intent map — an unrelated ``config.get("city")``
looks the same. Its messages therefore state only the observation (the key *appears in a*
``.get()`` *call on* that receiver) and offer both resolutions: declare the binding if it is
the intent map, or add a real reader if it is not.

**2. This guard proves contract SHAPE, not EFFECT.** A reader that binds a key and throws
the value away — ``_unused = intent.get("city", "")`` — satisfies this guard completely.
The guard proves a key name appears in a ``.get()`` call; it cannot prove the value
reaches behaviour.

This asymmetry matters because **the ratchet is one-way**: once ``city`` and
``routeLength`` leave ``_DEFERRED_READERS``, no structural signal remains that could ever
report them ineffective again. Green here must NOT be read as "the parameters take
effect".

What does cover effect, and is where a reviewer should look instead:

  * spec T2.2 — unit tests asserting the applied values are actually used, and restored
    afterwards.
  * spec T3.2 — live verification against a warm ECS container, which is the only thing
    that can fail the way the real reuse path failed. The defect this guard exists to
    prevent was itself found by a live gate after a fully green suite.

**3. A computed key name on the writer side cannot be verified, so it is rejected rather
than skipped.** ``written_keys`` raises ``ExtractionError`` if the intent map contains any
non-string-literal key. A contract whose key names are computed at runtime is not a
contract this guard can own, and silently ignoring such a key would evade the guard's
primary assertion (a new written key with no reader). Pinned by
``test_computed_writer_key_is_rejected_not_skipped``.

Exit codes: 0 = contract holds, 1 = contract violated or extraction looks broken.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

WRITER_PATH = _REPO_ROOT / "services" / "simulation" / "lambda" / "simulation_lambda.py"
READER_PATH = _REPO_ROOT / "services" / "simulation" / "realtime_telemetry_simulator.py"

WRITER_FUNC = "_write_trip_intent"

#: The DynamoDB ``ExpressionAttributeValues`` placeholder whose dict value *is* the
#: intent map. Pinned as a constant because the writer's outer dict also contains
#: ``Key={"vehicleId": ...}``, whose keys are not part of this contract.
INTENT_PLACEHOLDER = ":i"

#: The names the reader may bind the consumed intent map to.
#:
#: A SET, not a single name, because the apply/restore logic spec § "Apply/restore shape"
#: describes is naturally extracted into a helper — and a helper's parameter is a new
#: binding. Review Cycle 2 demonstrated the consequence of assuming one name: with the
#: reads live and working inside `_apply_trip_overrides(self, intent_map)`, the guard
#: reported `written with no reader: city, routeLength` and then invited the implementer to
#: add them to `_DEFERRED_READERS` — permanently re-allowlisting correct code, since the
#: ratchet is one-way.
#:
#: If you introduce a new binding for the intent map, add its name here. The loose
#: cross-receiver scan below will tell you to.
READER_VARS: frozenset[str] = frozenset({"intent", "intent_map"})

#: Keys written deliberately for observability or as placeholders, which no reader is
#: ever expected to consume. Each is documented as such in the writer's own docstring:
#:
#:   * ``requestedAt``  — ISO-8601 UTC, for observability and TTL reasoning.
#:   * ``agentTaskArn`` — written for observability and *future* anti-misrouting logic;
#:     the docstring states it is "not currently validated by the presence loop", and
#:     per-vehicle DDB scoping already bounds the impact of a misrouted intent.
#:   * ``uds_dtc_map``  — always written as ``""``; the UDS path goes via env on
#:     fresh-agent launch, and the reuse path carries it separately.
#:
#: This set exists because the first run of this guard surfaced these three alongside the
#: four real defects, and the issue report
#: (`issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/`)
#: undercounted the divergence at 4 keys when it is in fact 7. Keeping them in the same
#: bucket as the deferred-reader keys would hide the genuine remaining debt the moment
#: Tier A lands — the residual set would still be non-empty and would read as "work
#: outstanding" when it is not.
_WRITE_ONLY_BY_DESIGN: frozenset[str] = frozenset(
    {
        "requestedAt",
        "agentTaskArn",
        "uds_dtc_map",
    }
)

#: Keys the writer writes that a reader is *supposed* to consume and does not. These are
#: the defect, not a design choice.
#:
#: SHRINK THIS as readers land. `test_known_unconsumed_entries_are_still_real` fails if
#: an entry here has gained a reader, so it cannot silently go stale.
#:
#: All four were the subject of
#: `issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/`.
#:
#: **Tier A (`city`, `routeLength`) landed 2026-09-20** — spec Group 2, T2.1, read in
#: `PresenceLoop._resolve_trip_overrides`. Removed from this set, which is the one-way
#: step: no structural signal can report those two ineffective ever again. Their
#: effectiveness is carried instead by `services/simulation/tests/
#: test_trip_intent_overrides.py` (by-value assertions, 5 mutations verified incl. the
#: `routeLength: 0` sentinel) and by T3.2's warm-container live check. See § "Known
#: limitations" 2 — this guard would be equally green if the reads discarded the values.
#:
#: Tier B (`safetyScenarios`, `maintenanceScenarios`) — **CLOSED 2026-09-21 by T4.2**
#: (commit `009b8b46`). Both keys now have readers in `PresenceLoop`, via
#: `_resolve_catalog_scenarios` → `_apply_catalog_overrides`, which drives
#: `EventCatalogDriver.set_active_events` + `compute_degradation_targets` with
#: snapshot/restore around each trip. Group 4 landed outcome (a) — implement, no
#: descope; see `.kiro/specs/2026-09-20-trip-intent-param-contract/decisions.md`
#: § "2026-09-21 — T4.1" for the ownership trace and § "T4.2 targets the wrong
#: mechanism" for why the reader binds catalog `event_id`s rather than `SCENARIOS`
#: keys.
#:
#: This set is now empty and should stay that way. A new entry here is a claim that
#: a key is written with no reader *on purpose and temporarily* — it needs a cited
#: decision and an owning task, or it is just the original defect with paperwork.
_DEFERRED_READERS: frozenset[str] = frozenset()

#: Everything the guard tolerates as currently unconsumed.
_KNOWN_UNCONSUMED: frozenset[str] = _WRITE_ONLY_BY_DESIGN | _DEFERRED_READERS

#: Keys without which the presence loop cannot function at all. If the extractor fails to
#: find these, it is broken — regardless of how many other keys it found.
#:
#: This replaces an earlier `_MIN_CONSUMED_KEYS = 2` magic number, which had zero headroom
#: (2 vs a real count of 2) and, worse, was not coupled to the ratchet: once Tier A lands
#: and `consumed` grows to 4, a break in the *new* read path would drop consumed 4 -> 2,
#: clear a count floor of 2, and produce the misleading "contract violated — city,
#: routeLength" message that the floor exists to prevent. A required-key property cannot
#: go stale that way and needs no raising as the contract grows.
_REQUIRED_CONSUMED: frozenset[str] = frozenset({"simulationId", "tripsCount"})

#: Same property on the writer side — these two are structurally required.
_REQUIRED_WRITTEN: frozenset[str] = frozenset({"simulationId", "tripsCount"})

#: Secondary sanity floor on the writer side only. The required-key check above is the
#: primary anti-vacuity control; this catches a partial extraction that happens to retain
#: both required keys. Deliberately below the real count of 9.
_MIN_WRITTEN_KEYS = 5


class ExtractionError(RuntimeError):
    """Raised when a key set cannot be extracted at all (as opposed to being empty)."""


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except FileNotFoundError as exc:  # pragma: no cover - environment problem
        raise ExtractionError(f"file not found: {path}") from exc
    except SyntaxError as exc:  # pragma: no cover - would break the build anyway
        raise ExtractionError(f"could not parse {path}: {exc}") from exc


def _dict_string_keys(node: ast.Dict) -> set[str]:
    """Return the string-literal keys of a dict literal.

    Raises ``ExtractionError`` on any key that is not a string literal — a computed key
    name, or a ``**spread``. Such a key is **rejected, not skipped**: silently ignoring it
    would evade this guard's primary assertion, since a new written key with no reader
    could be introduced as ``('brandNew' + 'Knob')`` and the guard would report clean. A
    contract whose key names are computed at runtime is not one this guard can own, and
    saying so loudly is the only honest outcome.
    """
    keys: set[str] = set()
    for key in node.keys:
        if key is None:
            raise ExtractionError(
                "the intent map uses dict unpacking (`**`), so its key set cannot be "
                "determined statically — this guard cannot verify a computed contract"
            )
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
            raise ExtractionError(
                f"the intent map has a non-literal key at line {key.lineno} — "
                "this guard cannot verify a computed contract. Use a string literal, or "
                "the guard's primary assertion (a written key with no reader) can be "
                "evaded"
            )
        keys.add(key.value)
    return keys


def written_keys(tree: ast.Module) -> set[str]:
    """Extract the intent-map keys written by the writer function.

    Locates ``WRITER_FUNC``, then the dict literal bound to the ``INTENT_PLACEHOLDER``
    key inside it. Scoping to that placeholder (rather than collecting every dict literal
    in the function) is what keeps ``vehicleId`` and the placeholder itself out of the
    result.
    """
    target: ast.FunctionDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == WRITER_FUNC:
            target = node
            break
    if target is None:
        raise ExtractionError(
            f"writer function {WRITER_FUNC!r} not found in {WRITER_PATH.name} — "
            "it was probably renamed; update WRITER_FUNC"
        )

    for node in ast.walk(target):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant)
                and key.value == INTENT_PLACEHOLDER
                and isinstance(value, ast.Dict)
            ):
                return _dict_string_keys(value)

    raise ExtractionError(
        f"no dict literal bound to {INTENT_PLACEHOLDER!r} inside {WRITER_FUNC} — "
        "the ExpressionAttributeValues shape changed; update INTENT_PLACEHOLDER"
    )


def consumed_keys(tree: ast.Module) -> set[str]:
    """Extract the intent-map keys read via ``<binding>.get("literal")``.

    ``<binding>`` must be one of ``READER_VARS``. Reads through any other receiver are
    reported separately by :func:`keys_by_receiver` rather than silently ignored — see
    that function for why.
    """
    keys: set[str] = set()
    for key, receivers in keys_by_receiver(tree).items():
        if receivers & READER_VARS:
            keys.add(key)
    return keys


def keys_by_receiver(tree: ast.Module) -> dict[str, set[str]]:
    """Map every ``X.get("literal")`` key to the set of receiver names ``X`` used.

    This loose scan exists to distinguish two findings the guard previously conflated:

      * a key genuinely has no reader (the defect), versus
      * a key IS read, but through a binding this guard does not recognise (a guard
        configuration problem, or a refactor that introduced a helper).

    Reporting the second as the first is actively harmful: it names working keys as unread
    and, because the remedy the guard suggested was "add it to the allowlist", it would
    push an implementer into permanently excusing correct code.

    The scan cannot prove a non-``READER_VARS`` receiver really is the intent map — it
    could be an unrelated ``config.get("city")``. The messages built from it therefore state
    only the observation — that the key appears in a ``.get()`` call on that receiver — and
    offer both resolutions: declare the binding if it is the intent map, or add a real reader
    if it is not. That is honest about the ambiguity, and it never invites an allowlist entry.

    Review Cycle 3 caught an earlier version whose message asserted ``"is read via X"`` as
    fact while this docstring claimed it only reported non-confirmation — the third instance
    in this spec of a doc promising more than the code delivered.
    """
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "get":
            continue
        if not isinstance(func.value, ast.Name):
            continue
        if not node.args:
            continue
        first = node.args[0]
        if isinstance(first, ast.Constant) and isinstance(first.value, str):
            out.setdefault(first.value, set()).add(func.value.id)
    return out


def check() -> tuple[int, list[str]]:
    """Run the contract check. Returns ``(exit_code, lines_to_print)``."""
    lines: list[str] = []

    try:
        written = written_keys(_parse(WRITER_PATH))
        reader_tree = _parse(READER_PATH)
        consumed = consumed_keys(reader_tree)
        by_receiver = keys_by_receiver(reader_tree)
    except ExtractionError as exc:
        return 1, [f"❌ trip-intent contract: extraction failed — {exc}"]

    def _other_receivers(key: str) -> set[str]:
        """Receiver names that read ``key`` but are not recognised bindings."""
        return by_receiver.get(key, set()) - READER_VARS

    def _binding_fault_lines(keys: set[str], header: str) -> list[str]:
        out = [f"❌ {header}: " + ", ".join(sorted(keys))]
        for key in sorted(keys):
            names = ", ".join(sorted(_other_receivers(key)))
            # Phrased as an observation, not a conclusion. The guard sees a `.get()` call
            # with this key on that receiver; it cannot know whether the receiver is the
            # intent map. Review Cycle 3 flagged the earlier wording ("is read via ...")
            # for asserting as fact what the docstring said was only unconfirmable.
            out.append(
                f"   {key!r} appears in a .get() call on {names}, which is not a "
                "recognised binding for the intent map."
            )
        out.append(
            f"   This guard only counts reads through {sorted(READER_VARS)}. Resolve it one "
            "of two ways, depending on what that receiver actually is:"
        )
        out.append(
            "     (a) it IS the intent map under another name — a helper parameter, say: "
            "add that name to READER_VARS."
        )
        out.append(
            "     (b) it is UNRELATED (an `os.environ`, a `config`, a different dict that "
            "happens to use the same key name) — then this key genuinely has no reader, and "
            "the fix is to add one. Do not extend READER_VARS to silence the message."
        )
        out.append(
            "   DO NOT add these keys to _DEFERRED_READERS or _WRITE_ONLY_BY_DESIGN. The "
            "ratchet is one-way; allowlisting a key that is actually read would permanently "
            "excuse working code and re-permit the original defect alongside it."
        )
        return out

    # ── Anti-vacuity first: an empty-vs-empty comparison must never read as success ──
    #
    # Required-key checks come before any count floor because they are properties rather
    # than magic numbers, and because a misleading "contract violated" message naming
    # innocent keys is worse than no message — it sends the next reader to audit the
    # contract instead of the extractor.
    missing_written = _REQUIRED_WRITTEN - written
    if missing_written:
        lines.append(
            "❌ extraction looks broken, not the contract — "
            f"{WRITER_PATH.name}::{WRITER_FUNC} appears not to write "
            f"{', '.join(sorted(missing_written))}, which the presence loop cannot "
            "function without. Check WRITER_FUNC / INTENT_PLACEHOLDER."
        )
        return 1, lines

    missing_consumed = _REQUIRED_CONSUMED - consumed
    if missing_consumed:
        rebound = {k for k in missing_consumed if _other_receivers(k)}
        if rebound:
            return 1, lines + _binding_fault_lines(
                rebound,
                "a structurally-required key is read through an unrecognised binding",
            )
        lines.append(
            "❌ extraction looks broken, not the contract — "
            f"{READER_PATH.name} appears not to read "
            f"{', '.join(sorted(missing_consumed))}, which the presence loop cannot "
            f"function without. Check READER_VARS (currently {sorted(READER_VARS)})."
        )
        return 1, lines

    if len(written) < _MIN_WRITTEN_KEYS:
        lines.append(
            f"❌ extracted only {len(written)} written key(s) from "
            f"{WRITER_PATH.name}::{WRITER_FUNC}, expected at least "
            f"{_MIN_WRITTEN_KEYS} — the extractor is broken, not the contract"
        )
        return 1, lines

    unconsumed = written - consumed
    unexpected = unconsumed - _KNOWN_UNCONSUMED

    # Reclassify before reporting: a key read through an unrecognised binding is a binding
    # problem, NOT an absent reader. Conflating them told Cycle 2's probe that city and
    # routeLength had no reader while a helper was reading both, and then suggested the one
    # remedy that would have made it permanent.
    rebound = {k for k in unexpected if _other_receivers(k)}
    unexpected -= rebound

    # A key read but never written: always resolves to its default at runtime, so it is a
    # silent no-op. The live hazard is a spelling slip — the writer translates snake_case
    # to camelCase at the boundary (`"routeLength": config.get("route_length")`), so both
    # spellings are correct in different places in that one file, and `route_length` on
    # the reader side would otherwise register as progress.
    orphan_reads = consumed - written

    # An allowlist entry that is no longer in `unconsumed` has EITHER gained a reader OR
    # lost its write. Intersecting with `written` first keeps those two apart: without it,
    # deleting the `city` write reports "city now HAS readers" — the exact opposite of the
    # truth. Not hypothetical: T5.2 makes removing the scenario writes a plausible Tier B
    # descope outcome.
    closed_deferred = (_DEFERRED_READERS & written) - unconsumed
    closed_by_design = (_WRITE_ONLY_BY_DESIGN & written) - unconsumed
    no_longer_written = _KNOWN_UNCONSUMED - written

    if unexpected:
        lines.append(
            "❌ trip-intent contract violated — written with no reader: "
            + ", ".join(sorted(unexpected))
        )
        lines.append(
            f"   {WRITER_PATH.name}::{WRITER_FUNC} writes these into the tripIntent map, "
            f"but nothing in {READER_PATH.name} reads them."
        )
        lines.append(
            "   A key written with no reader is silently discarded at runtime while the "
            "API still reports success. Either add the reader, or add the key to "
            "_WRITE_ONLY_BY_DESIGN (observability/placeholder) or _DEFERRED_READERS "
            "(deferred defect, with a filed issue)."
        )

    if rebound:
        lines.extend(
            _binding_fault_lines(
                rebound, "written and read, but through an unrecognised binding"
            )
        )

    if orphan_reads:
        lines.append(
            "❌ trip-intent contract violated — read but never written: "
            + ", ".join(sorted(orphan_reads))
        )
        lines.append(
            f"   {READER_PATH.name} reads these from the intent map, but "
            f"{WRITER_PATH.name}::{WRITER_FUNC} never writes them, so they always "
            "resolve to their default — a silent no-op."
        )
        lines.append(
            "   Most likely a spelling slip: the writer translates snake_case to "
            "camelCase at the boundary, so both spellings are correct in different "
            "places in that file. Compare against the written set above."
        )

    if closed_deferred:
        lines.append(
            "❌ _DEFERRED_READERS is stale — these keys now HAVE readers: "
            + ", ".join(sorted(closed_deferred))
        )
        lines.append(
            "   Good news, but remove them from _DEFERRED_READERS. A stale exemption "
            "re-permits the original defect: the next key added alongside them would be "
            "excused too."
        )

    if closed_by_design:
        lines.append(
            "❌ _WRITE_ONLY_BY_DESIGN is stale — these keys now HAVE readers: "
            + ", ".join(sorted(closed_by_design))
        )
        lines.append(
            "   These were classified as write-only (observability or placeholder). If a "
            "reader was added deliberately, move them out of _WRITE_ONLY_BY_DESIGN and "
            "update the writer's docstring, which still documents them as unread."
        )

    if no_longer_written:
        lines.append(
            "❌ the allowlist names keys that are no longer written at all: "
            + ", ".join(sorted(no_longer_written))
        )
        lines.append(
            "   This is NOT the same as gaining a reader. If the write was removed "
            "deliberately (for example a descope that drops a control rather than ship "
            "one that is silently ignored), remove the key from the allowlist too and "
            "record the decision."
        )

    if unexpected or rebound or orphan_reads or closed_deferred or closed_by_design or no_longer_written:
        return 1, lines

    lines.append(
        f"✅ trip-intent contract holds — {len(written)} key(s) written, "
        f"{len(consumed)} consumed, "
        f"{len(_DEFERRED_READERS & unconsumed)} awaiting a reader "
        f"({', '.join(sorted(_DEFERRED_READERS & unconsumed)) or 'none'}), "
        f"{len(_WRITE_ONLY_BY_DESIGN & unconsumed)} write-only by design"
    )
    lines.append(
        "   NOTE: this proves contract shape, not effect. A reader that binds a key and "
        "discards it satisfies this guard — see § Known limitations 2."
    )
    return 0, lines


def main() -> int:
    code, lines = check()
    for line in lines:
        print(line)
    return code


if __name__ == "__main__":
    sys.exit(main())
