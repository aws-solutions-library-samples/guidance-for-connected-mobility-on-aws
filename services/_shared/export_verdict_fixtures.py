# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Export verdict fixtures from ROUTINE_RESULT_SCHEMAS into a JSON bridge for TS tests.

Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ Group 6 T6.1
  D5 — verdict is computed server-side and is authoritative; the UI must never
       recompute it. Duplicating threshold logic between Python and TypeScript
       is the exact bug D5 forbids ("same class of bug as computing 'latest'
       client-side").

WHY THIS FILE EXISTS
-------------------
The TS renderer tests need, per routine per verdict, a payload that genuinely
produces that verdict. If a TS test hardcodes

    { leak_rate_ccm: 2.0 }  ->  expect 'out_of_spec'

then the *pairing* of payload to verdict is asserted in TypeScript. Move the
Python threshold from 1.5 to 3.0 and that test still passes while asserting a
pairing that is now false. The TS suite would be green and wrong.

So this generator runs each probe payload through the schema's own
`verdict_from_result` and records **the verdict Python actually returned**.
TypeScript asserts the renderer displays that verdict. TypeScript never decides
which verdict a payload deserves.

THE DELIBERATE SECOND COPY (read before "deduplicating" it)
-----------------------------------------------------------
`VERDICT_PRESENTATION` below restates the verdict -> (label, indicator type)
mapping that also lives in the frontend's `routine-renderers/types.ts`
(`VERDICT_LABEL` / `verdictType`). That duplication is intentional and is the
point of the guard.

If the TS test imported the expected label from `types.ts`, it would assert
only that the component uses the same constant the test uses — a mutation to
`types.ts` would change both sides and the test would still pass. Mutation-blind.

Instead this mapping is the spec-D5-derived reference, exported through the
bridge, and the TS test asserts rendered output against it. Editing
`types.ts` alone now breaks the test. Two independent copies compared by a
test is a golden-file assertion, not a duplication bug. Do not collapse them.

UNREACHABLE VERDICT BANDS
-------------------------
Not every routine can produce all three verdicts. `abs_pump_cycle`'s verdict
function is a binary count comparison with no marginal band (see
`_abs_pump_verdict`'s docstring). `EXPECTED_UNREACHABLE` declares that, and
generation FAILS if reality stops matching the declaration in either
direction — a band becoming reachable is as much a change as one disappearing.

For a declared-unreachable verdict we still emit a fixture, tagged
`verdict_source: "presentation_only"`, so the renderer's banner presentation is
covered for a verdict the wire could legitimately carry (nothing stops a future
sidecar from sending it). Those fixtures assert presentation only — never that
the payload produces the verdict, because it does not.

USAGE
-----
    python3 -m _shared.export_verdict_fixtures --write   # regenerate artifacts
    python3 -m _shared.export_verdict_fixtures --check   # CI: fail on drift

Run from `services/`.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Iterable

from _shared.routine_result_schemas import (
    ROUTINE_RESULT_SCHEMAS,
    validate_result,
)

# ---------------------------------------------------------------------------
# Spec D5 verdict presentation contract
#
# Source of truth: spec.md D5 + docs/tech.md § SOVD (c).
# The frontend's types.ts carries its own copy; the TS test asserts against
# THIS one so the two are compared rather than assumed equal. See module
# docstring, "THE DELIBERATE SECOND COPY".
# ---------------------------------------------------------------------------

VERDICT_PRESENTATION: dict[str, dict[str, str]] = {
    "in_spec": {
        "label": "Check passed",
        "indicator_type": "success",
    },
    "marginal": {
        "label": "Reading within tolerance — monitor",
        "indicator_type": "warning",
    },
    "out_of_spec": {
        "label": "Reading outside tolerance — repair action required",
        "indicator_type": "error",
    },
}

ALL_VERDICTS: tuple[str, ...] = ("in_spec", "marginal", "out_of_spec")

# ---------------------------------------------------------------------------
# Probe payloads
#
# These are INPUTS, not truth. Their job is to straddle every threshold band
# densely enough that each reachable verdict is hit. The verdict attached to
# each is whatever Python computes — never what this table's author expected.
#
# Ordering is significant: the first probe yielding a given verdict is the one
# exported, so the artifact is deterministic. Do not reorder casually; it will
# churn the generated file (harmlessly, but noisily).
# ---------------------------------------------------------------------------

PROBES: dict[str, list[dict[str, Any]]] = {
    "lamp_self_check": [
        {"lamps": ["ok"] * 8, "ambient_lux": 340},
        {"lamps": ["ok", "ok", "ok", "ok", "ok", "flicker", "ok", "ok"], "ambient_lux": 12},
        {"lamps": ["ok", "dim", "ok", "ok", "ok", "ok", "ok", "ok"], "ambient_lux": 88},
        {"lamps": ["ok", "ok", "open_circuit", "ok", "ok", "ok", "ok", "ok"], "ambient_lux": 412},
        {"lamps": ["short", "ok", "ok", "ok", "ok", "ok", "ok", "ok"], "ambient_lux": 210},
    ],
    "o2_heater_check": [
        {"bank1_upstream_response_ms": 62, "threshold_ms": 100},
        {"bank1_upstream_response_ms": 100, "threshold_ms": 100},
        {"bank1_upstream_response_ms": 110, "threshold_ms": 100},
        {"bank1_upstream_response_ms": 120, "threshold_ms": 100},
        {"bank1_upstream_response_ms": 145, "threshold_ms": 100},
    ],
    # system_pressure_kpa is NEGATIVE here on purpose: an EVAP test is a
    # vacuum-decay test, and `routine_sims._produce_evap_leak_test` generates
    # roughly -15.0..-10.1 kPa. Earlier positive probes validated fine (the
    # verdict function reads only leak_rate_ccm) but left the renderer's
    # negative-number formatting path uncovered, and made the fixtures
    # unrepresentative of the payload production actually sends.
    "evap_leak_test": [
        {"system_pressure_kpa": -14.0, "leak_rate_ccm": 0.12},
        {"system_pressure_kpa": -13.6, "leak_rate_ccm": 0.5},
        {"system_pressure_kpa": -13.1, "leak_rate_ccm": 0.9},
        {"system_pressure_kpa": -12.4, "leak_rate_ccm": 1.5},
        {"system_pressure_kpa": -10.9, "leak_rate_ccm": 2.4},
    ],
    "abs_pump_cycle": [
        {"cycles_observed": 6, "cycles_expected": 6},
        {"cycles_observed": 4, "cycles_expected": 6},
        {"cycles_observed": 0, "cycles_expected": 6},
        {"cycles_observed": 7, "cycles_expected": 6},
    ],
    "pack_isolation_test": [
        {"isolation_resistance_mohm": 240.0, "threshold_mohm": 100.0},
        {"isolation_resistance_mohm": 120.0, "threshold_mohm": 100.0},
        {"isolation_resistance_mohm": 108.0, "threshold_mohm": 100.0},
        {"isolation_resistance_mohm": 100.0, "threshold_mohm": 100.0},
        {"isolation_resistance_mohm": 61.0, "threshold_mohm": 100.0},
    ],
    "cell_balance_check": [
        {"cell_voltages": [3.702, 3.701, 3.703, 3.700], "max_delta_mv": 3.0},
        {"cell_voltages": [3.712, 3.698, 3.703, 3.700], "max_delta_mv": 20.0},
        {"cell_voltages": [3.724, 3.690, 3.705, 3.699], "max_delta_mv": 34.0},
        {"cell_voltages": [3.740, 3.688, 3.702, 3.699], "max_delta_mv": 50.0},
        {"cell_voltages": [3.780, 3.661, 3.704, 3.698], "max_delta_mv": 119.0},
    ],
}

# Verdicts that no payload can produce, per routine, and why.
# Generation fails if reality diverges from this in EITHER direction.
EXPECTED_UNREACHABLE: dict[str, set[str]] = {
    "abs_pump_cycle": {"marginal"},
}

# Payload used for a presentation-only fixture. Must be schema-valid; its
# verdict is NOT asserted to come from Python.
PRESENTATION_ONLY_PAYLOAD: dict[str, dict[str, Any]] = {
    "abs_pump_cycle": {"cycles_observed": 6, "cycles_expected": 6},
}

ARTIFACT_NAME = "verdict-fixtures.generated.json"

# Artifact destinations, relative to each repo root.
CMS_ARTIFACT_REL = Path(
    "modules/cms_ui/source/frontend/src/components/vehicles/vehicle-detail"
    "/routine-renderers/__tests__"
) / ARTIFACT_NAME

DMS_ARTIFACT_REL = Path(
    "frontend/src/components/service/routine-renderers/__tests__"
) / ARTIFACT_NAME

# services/_shared/export_verdict_fixtures.py -> repo root is 2 parents up.
CMS_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DMS_ROOT = CMS_ROOT.parent / "guidance-for-dealer-management-system-on-aws"


class FixtureGenerationError(RuntimeError):
    """Raised when the schemas no longer support the declared fixture matrix."""


def _reachable(routine_id: str) -> dict[str, dict[str, Any]]:
    """Map each verdict Python can produce for *routine_id* to its first probe.

    The verdict is taken from the schema's own `verdict_from_result`. Probe
    order decides which payload wins, keeping output deterministic.
    """
    schema = ROUTINE_RESULT_SCHEMAS[routine_id]
    verdict_fn = schema["verdict_from_result"]
    found: dict[str, dict[str, Any]] = {}
    for payload in PROBES[routine_id]:
        errors = validate_result(routine_id, payload)
        if errors:
            raise FixtureGenerationError(
                f"{routine_id}: probe payload is not schema-valid: {errors}. "
                f"Probe: {payload!r}"
            )
        verdict = verdict_fn(payload)
        if verdict not in VERDICT_PRESENTATION:
            raise FixtureGenerationError(
                f"{routine_id}: verdict_from_result returned {verdict!r}, which has no "
                f"presentation mapping. Known: {sorted(VERDICT_PRESENTATION)}"
            )
        found.setdefault(verdict, payload)
    return found


def build_fixtures() -> dict[str, Any]:
    """Build the full artifact payload.

    Raises:
        FixtureGenerationError: if a verdict band changed reachability, if a
            probe payload stopped being schema-valid, or if a routine lost a
            declared presentation-only payload. Fail loudly — a silently
            shrinking fixture matrix is a coverage regression no test catches.
    """
    if set(PROBES) != set(ROUTINE_RESULT_SCHEMAS):
        missing = sorted(set(ROUTINE_RESULT_SCHEMAS) - set(PROBES))
        extra = sorted(set(PROBES) - set(ROUTINE_RESULT_SCHEMAS))
        raise FixtureGenerationError(
            "PROBES is out of step with ROUTINE_RESULT_SCHEMAS. "
            f"Schemas with no probes: {missing}. Probes with no schema: {extra}. "
            "Every pilot routine needs probes, or its renderer ships untested."
        )

    fixtures: list[dict[str, Any]] = []

    for routine_id in sorted(ROUTINE_RESULT_SCHEMAS):
        schema = ROUTINE_RESULT_SCHEMAS[routine_id]
        found = _reachable(routine_id)
        declared_unreachable = EXPECTED_UNREACHABLE.get(routine_id, set())
        actually_unreachable = set(ALL_VERDICTS) - set(found)

        if actually_unreachable != declared_unreachable:
            newly_unreachable = sorted(actually_unreachable - declared_unreachable)
            newly_reachable = sorted(declared_unreachable - actually_unreachable)
            raise FixtureGenerationError(
                f"{routine_id}: verdict reachability changed.\n"
                f"  declared unreachable: {sorted(declared_unreachable)}\n"
                f"  actually unreachable: {sorted(actually_unreachable)}\n"
                + (
                    f"  NEWLY UNREACHABLE {newly_unreachable}: a threshold moved and this "
                    f"band is now dead, or the probes no longer straddle it. Add a probe "
                    f"that hits it, or declare it in EXPECTED_UNREACHABLE with a reason.\n"
                    if newly_unreachable
                    else ""
                )
                + (
                    f"  NEWLY REACHABLE {newly_reachable}: verdict_from_result gained a band "
                    f"it did not have. Remove it from EXPECTED_UNREACHABLE so the fixture "
                    f"asserts the real Python verdict instead of presentation only.\n"
                    if newly_reachable
                    else ""
                )
            )

        for verdict in ALL_VERDICTS:
            if verdict in found:
                fixtures.append(
                    {
                        "routine_id": routine_id,
                        "verdict": verdict,
                        "verdict_source": "python",
                        "result": found[verdict],
                        "renderer_hint": schema["renderer_hint"],
                        "schema_version": schema["version"],
                    }
                )
                continue

            payload = PRESENTATION_ONLY_PAYLOAD.get(routine_id)
            if payload is None:
                raise FixtureGenerationError(
                    f"{routine_id}: verdict {verdict!r} is unreachable and no "
                    f"PRESENTATION_ONLY_PAYLOAD is declared, so the renderer would ship "
                    f"with no coverage for it. Add one."
                )
            errors = validate_result(routine_id, payload)
            if errors:
                raise FixtureGenerationError(
                    f"{routine_id}: PRESENTATION_ONLY_PAYLOAD is not schema-valid: {errors}"
                )
            fixtures.append(
                {
                    "routine_id": routine_id,
                    "verdict": verdict,
                    "verdict_source": "presentation_only",
                    "result": payload,
                    "renderer_hint": schema["renderer_hint"],
                    "schema_version": schema["version"],
                }
            )

    return {
        "_generated_by": "services/_shared/export_verdict_fixtures.py",
        "_do_not_edit": (
            "Generated artifact. Regenerate with "
            "`cd services && python3 -m _shared.export_verdict_fixtures --write`. "
            "Hand edits are reverted by the staleness guard in "
            "services/_shared/tests/test_export_verdict_fixtures.py."
        ),
        "_verdict_source_meaning": {
            "python": (
                "result was run through the schema's verdict_from_result and this is the "
                "verdict it returned. Safe to assert payload -> verdict."
            ),
            "presentation_only": (
                "no payload can produce this verdict for this routine (see "
                "EXPECTED_UNREACHABLE). Assert banner presentation only, never that this "
                "result yields this verdict."
            ),
        },
        "artifact_version": 1,
        "verdict_presentation": VERDICT_PRESENTATION,
        "fixtures": fixtures,
    }


def render_json(payload: dict[str, Any]) -> str:
    """Serialise deterministically so byte-comparison is a valid drift check."""
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def _targets(dms_root: Path | None) -> list[tuple[str, Path]]:
    targets = [("CMS", CMS_ROOT / CMS_ARTIFACT_REL)]
    root = dms_root if dms_root is not None else DEFAULT_DMS_ROOT
    # The DMS checkout is absent on the public mirror and in CMS-only CI. Skip
    # rather than fail — same SKIP posture as verify_renderer_parity.py.
    if (root / DMS_ARTIFACT_REL).parent.is_dir():
        targets.append(("DMS", root / DMS_ARTIFACT_REL))
    return targets


def write(dms_root: Path | None = None) -> list[Path]:
    """Write the artifact to every present target. Returns paths written."""
    content = render_json(build_fixtures())
    written: list[Path] = []
    for label, path in _targets(dms_root):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        print(f"[WRITE] {label}: {path}")
        written.append(path)
    return written


def check(dms_root: Path | None = None) -> list[str]:
    """Compare committed artifacts against freshly generated content.

    Returns a list of drift messages; empty means clean.
    """
    content = render_json(build_fixtures())
    problems: list[str] = []
    for label, path in _targets(dms_root):
        if not path.is_file():
            problems.append(
                f"{label}: artifact missing at {path}. Run "
                f"`python3 -m _shared.export_verdict_fixtures --write`."
            )
            continue
        on_disk = path.read_text(encoding="utf-8")
        if on_disk != content:
            problems.append(
                f"{label}: {path} is stale. The Python schemas and this artifact "
                f"disagree. Regenerate with "
                f"`cd services && python3 -m _shared.export_verdict_fixtures --write`."
            )
    return problems


def _describe(fixtures: Iterable[dict[str, Any]]) -> str:
    by_source: dict[str, int] = {}
    for f in fixtures:
        by_source[f["verdict_source"]] = by_source.get(f["verdict_source"], 0) + 1
    parts = ", ".join(f"{k}={v}" for k, v in sorted(by_source.items()))
    return parts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--write", action="store_true", help="regenerate artifacts")
    group.add_argument("--check", action="store_true", help="fail if artifacts are stale")
    parser.add_argument(
        "--dms-root",
        type=Path,
        default=None,
        help="override the DMS checkout root (default: sibling directory)",
    )
    args = parser.parse_args(argv)

    try:
        payload = build_fixtures()
    except FixtureGenerationError as exc:
        print(f"[FAIL] fixture generation: {exc}", file=sys.stderr)
        return 2

    if args.write:
        write(args.dms_root)
        print(
            f"[OK] {len(payload['fixtures'])} fixtures "
            f"({_describe(payload['fixtures'])})"
        )
        return 0

    problems = check(args.dms_root)
    if problems:
        for p in problems:
            print(f"[DRIFT] {p}", file=sys.stderr)
        return 1
    print(
        f"[OK] artifacts match the schemas — {len(payload['fixtures'])} fixtures "
        f"({_describe(payload['fixtures'])})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
