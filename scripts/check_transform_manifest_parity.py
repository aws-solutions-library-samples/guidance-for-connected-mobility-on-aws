#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Verify each transform manifest maps only fields its producer actually emits.

Spec: `.kiro/specs/2026-09-20-transform-manifest-contract-guards/spec.md` T1.1 (D1).

## Why this exists

A transform manifest in `services/data_processing/manifests/` names, per signal, a
``source_path`` it expects to find in the incoming payload. Nothing checks that the
producer still emits that path.

**The failure is silent, not loud.** In `meridian-ev-transform.json`, 12 of 38 mappings
carry a ``default_value`` and ``validation.required_fields`` is an empty list. So a
renamed or dropped source field does not raise, does not DLQ, and does not log — it
resolves to its default. Rename ``tire_pressure_fl`` in the simulator and the maintenance
table fills with plausible ``0.0``s that look like a healthy vehicle.

That is the same shape as
`issues/2026-09-20-trip-intent-reuse-path-ignores-city-route-length-scenarios/`: a value
accepted, reported successful, and discarded. The manifest and the producer are two
separately-maintained artifacts in two languages with no compiler between them.

## Direction of the assertion, and why it is a subset

``manifest.source_paths ⊆ producer.emitted_keys``.

Subset, deliberately, not equality. The simulator emits 269 keys; the manifest projects
38. Equality would fail permanently and therefore guard nothing. A producer key with no
mapping is the intended projection, not an error — only the reverse (a mapping with no
producer key) is a defect.

## Scope: why oem1 is excluded

Only manifests whose producer lives in this repo can be checked statically:

  * ``meridian-ev-transform.json`` — produced by
    ``realtime_telemetry_simulator.py::generate_telemetry_data``. **In scope.**
  * ``oem1-transform.json`` — produced by the external OEM1 connector feed, whose payload
    shape is defined by upstream protobuf, not by any function here. There is no in-repo
    key set to compare against, so a parity check would be fabricated. **Out of scope,
    recorded in `_OUT_OF_SCOPE` with that reason rather than silently skipped** — an
    unexplained exclusion is how an allowlist rots (this repo has already had one go stale
    by 16 entries).

## The extraction hazard this guard had to solve first

``generate_telemetry_data`` returns a **variable**, not a dict literal. A naive AST walk
for returned dict literals finds ~11 keys instead of 269, and then reports ~30 manifest
paths as missing — a fabricated failure that looks exactly like a real one. That happened
twice during this spec's investigation.

So the extractor follows the returned name and unions three construction forms (literal
assignment, subscript write, ``.update({...})``), and the anti-vacuity floors below exist
to make a broken extractor fail as *"the extractor is broken"* rather than as a list of
innocent field names.

Exit codes: 0 = every in-scope manifest is covered, 1 = a gap or a broken extraction.
"""

from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent

MANIFEST_DIR = _REPO_ROOT / "services" / "data_processing" / "manifests"
SIMULATOR_PATH = _REPO_ROOT / "services" / "simulation" / "realtime_telemetry_simulator.py"
MSK_TOPICS_SCRIPT = _REPO_ROOT / "deployment" / "scripts" / "create_msk_topics.py"
SIMULATION_LAMBDA = _REPO_ROOT / "services" / "simulation" / "lambda" / "simulation_lambda.py"
TELEMETRY_INTEGRATION_STACK = _REPO_ROOT / "deployment" / "stacks" / "telemetry_integration_stack.py"

#: The prefix `deriveSourceKeyFromTopic` strips to get the manifest's filename stem.
CS_PRODUCT_PREFIX = "cs-product-"

#: Manifest filename → the producer whose emitted key set it is checked against.
#: ``(source_path, function_name)`` relative to the repo root.
_IN_SCOPE: dict[str, tuple[Path, str]] = {
    "meridian-ev-transform.json": (SIMULATOR_PATH, "generate_telemetry_data"),
}

#: Manifest filename → why no parity check is possible. Never leave an exclusion
#: unexplained; a bare skip is indistinguishable from an oversight.
#: D3's three sources (MSK provisioning, rule allowlist, CDK topic rules) are all
#: cross-checked; a manifest appearing in any source must appear in _IN_SCOPE or here.
_OUT_OF_SCOPE: dict[str, str] = {
    "oem1-transform.json": (
        "producer is the external OEM1 connector feed, whose payload shape is defined by "
        "upstream protobuf rather than by any function in this repo — there is no in-repo "
        "key set to compare against"
    ),
}

#: Anti-vacuity floor on the producer side. The real count is 269; a floor of 100 is well
#: clear of ordinary churn while still catching the ~11-key naive-extraction failure that
#: motivated it.
_MIN_EMITTED_KEYS = 100

#: Anti-vacuity floor on the manifest side. Without it, emptying ``signal_mappings``
#: yields an empty set, and the empty set is a subset of everything — so the guard would
#: pass while the manifest mapped nothing at all.
#: Applied to ``len(mappings)`` (the total mapping count), NOT to ``len(paths)`` (the
#: non-null source_path count) — so a manifest with all source_paths set to null still
#: trips the floor (38 mappings ≥ 10) and a non-string source_path drop cannot mask it.
_MIN_MAPPINGS = 10


class ExtractionError(RuntimeError):
    """Raised when a key set cannot be extracted at all, as opposed to being empty."""


def _parse(path: Path) -> ast.Module:
    try:
        return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except FileNotFoundError as exc:
        raise ExtractionError(f"file not found: {path}") from exc
    except SyntaxError as exc:  # pragma: no cover - would break the build anyway
        raise ExtractionError(f"could not parse {path}: {exc}") from exc


def manifest_source_paths(path: Path) -> tuple[set[str], list[str], frozenset[str]]:
    """Return ``(paths, dropped_cms_fields, required_emitted)`` from a transform manifest.

    *paths* — the set of ``source_path`` strings from every mapping whose
    ``source_path`` is a ``str``.

    *dropped_cms_fields* — the list of ``cms_field`` values (or ``"<unknown>"`` when that
    key is also absent) for every mapping whose ``source_path`` is not a ``str``.
    ``OEMTelemetryProcessor:331`` NPEs on a null ``sourcePath``; the drop is a schema
    violation, not a defensive posture.

    *required_emitted* — the pair of names the producer must emit regardless of signal
    mappings: ``vehicle_id_extraction.path`` (default ``"vehicleId"``) and
    ``timestamp_field`` (default ``"timestamp"``, mirroring
    ``OEMTelemetryProcessor.java:772``). These are derived from the manifest under test,
    not a module constant, so renaming either field in the manifest produces a finding
    that names the new field rather than a stale literal.

    The ``_MIN_MAPPINGS`` floor is applied to ``len(mappings)`` — the total mapping count
    — not to ``len(paths)``. A manifest with all ``source_path``s nulled has 38 mappings
    and still trips the floor; the null-drop cannot hide behind it.
    """
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ExtractionError(f"manifest not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise ExtractionError(f"manifest is not valid JSON: {path}: {exc}") from exc

    mappings = doc.get("signal_mappings")
    if not isinstance(mappings, list):
        raise ExtractionError(
            f"{path.name}: 'signal_mappings' is missing or not a list — the manifest "
            "schema changed; update this guard deliberately"
        )

    # F1.1 — derive required names from the manifest, not a module constant.
    vid_extraction = doc.get("vehicle_id_extraction")
    if not isinstance(vid_extraction, dict) or "path" not in vid_extraction:
        raise ExtractionError(
            f"{path.name}: 'vehicle_id_extraction.path' is missing or not a dict — "
            "cannot derive the vehicle-id field name; the manifest schema changed"
        )
    vid_field = vid_extraction["path"]
    if not isinstance(vid_field, str):
        raise ExtractionError(
            f"{path.name}: 'vehicle_id_extraction.path' is not a string — "
            "cannot derive the vehicle-id field name"
        )
    ts_field = doc.get("timestamp_field", "timestamp")  # mirror OEMTelemetryProcessor.java:772
    if not isinstance(ts_field, str):
        raise ExtractionError(
            f"{path.name}: 'timestamp_field' is not a string — "
            "cannot derive the timestamp field name"
        )
    required_emitted = frozenset({vid_field, ts_field})

    # F1.8 — separate valid paths from non-string source_paths; apply floor to total.
    paths: set[str] = set()
    dropped_cms_fields: list[str] = []
    for m in mappings:
        if not isinstance(m, dict):
            continue
        sp = m.get("source_path")
        if isinstance(sp, str):
            paths.add(sp)
        else:
            # Non-string source_path is a schema violation (null, dict, list, …).
            # OEMTelemetryProcessor:331 NPEs on null sourcePath at runtime; do not drop silently.
            dropped_cms_fields.append(m.get("cms_field", "<unknown>") if isinstance(m.get("cms_field"), str) else "<unknown>")

    return paths, dropped_cms_fields, required_emitted


def emitted_keys(path: Path, func_name: str) -> tuple[set[str], int]:
    """Return ``(emitted_keys, unresolvable_update_count)`` for a producer function.

    ``generate_telemetry_data`` builds a dict and returns it **by name**, so this follows
    the returned identifier and unions three construction forms:

      1. ``<var> = {...}``            — dict literal assignment
      2. ``<var>['key'] = ...``       — subscript write
      3. ``<var>.update({...})``      — literal-dict update

    A ``.update(<non-literal>)`` call cannot be resolved statically. Rather than silently
    under-reporting, those are counted and surfaced, so a "missing path" finding can be
    read with the knowledge that some keys were unresolvable.
    """
    tree = _parse(path)
    func = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == func_name
        ),
        None,
    )
    if func is None:
        raise ExtractionError(
            f"producer function {func_name!r} not found in {path.name} — it was probably "
            "renamed; update _IN_SCOPE"
        )

    returns = [n for n in ast.walk(func) if isinstance(n, ast.Return) and n.value is not None]
    if not returns:
        raise ExtractionError(f"{func_name} has no return statement with a value")

    # The payload variable: the name returned. If the function returns a dict literal
    # directly, collect from it and we are done.
    direct: set[str] = set()
    var: str | None = None
    for ret in returns:
        if isinstance(ret.value, ast.Dict):
            direct |= _dict_str_keys(ret.value)
        elif isinstance(ret.value, ast.Name):
            var = ret.value.id
    if var is None:
        if direct:
            return direct, 0
        raise ExtractionError(
            f"{func_name} returns neither a bare name nor a dict literal — this guard "
            "cannot determine the payload shape; extend it deliberately"
        )

    keys: set[str] = set(direct)
    unresolvable = 0
    for node in ast.walk(func):
        # 1. <var> = {...}
        if isinstance(node, ast.Assign) and isinstance(node.value, ast.Dict):
            if any(isinstance(t, ast.Name) and t.id == var for t in node.targets):
                keys |= _dict_str_keys(node.value)
        # 2. <var>['key'] = ...
        if isinstance(node, ast.Assign):
            for t in node.targets:
                if (
                    isinstance(t, ast.Subscript)
                    and isinstance(t.value, ast.Name)
                    and t.value.id == var
                    and isinstance(t.slice, ast.Constant)
                    and isinstance(t.slice.value, str)
                ):
                    keys.add(t.slice.value)
        # 3. <var>.update({...})
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "update"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == var
        ):
            for arg in node.args:
                if isinstance(arg, ast.Dict):
                    keys |= _dict_str_keys(arg)
                else:
                    unresolvable += 1

    return keys, unresolvable


def _dict_str_keys(node: ast.Dict) -> set[str]:
    return {
        k.value
        for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def check() -> tuple[int, list[str]]:
    """Run the parity check across every manifest in ``MANIFEST_DIR``."""
    lines: list[str] = []
    failed = False

    if not MANIFEST_DIR.is_dir():
        return 1, [f"❌ manifest directory not found: {MANIFEST_DIR}"]

    found = sorted(p.name for p in MANIFEST_DIR.glob("*-transform.json"))
    if not found:
        return 1, [
            f"❌ no '*-transform.json' files in {MANIFEST_DIR} — extraction is broken, "
            "not the contract"
        ]

    # Every manifest present must be classified. An unclassified one is neither checked
    # nor knowingly excluded, which is the state this guard exists to prevent.
    unclassified = [n for n in found if n not in _IN_SCOPE and n not in _OUT_OF_SCOPE]
    if unclassified:
        lines.append(
            "❌ manifest(s) present but neither in scope nor excluded: "
            + ", ".join(unclassified)
        )
        lines.append(
            "   Add each to _IN_SCOPE with its producer, or to _OUT_OF_SCOPE with the "
            "reason no in-repo producer exists. Leaving one unclassified means it is "
            "silently unguarded."
        )
        failed = True

    for name in found:
        if name in _OUT_OF_SCOPE:
            lines.append(f"⊘ {name}: not checked — {_OUT_OF_SCOPE[name]}")
            continue
        if name not in _IN_SCOPE:
            continue

        producer_path, func_name = _IN_SCOPE[name]
        try:
            paths, dropped_cms_fields, required_emitted = manifest_source_paths(MANIFEST_DIR / name)
            emitted, unresolvable = emitted_keys(producer_path, func_name)
        except ExtractionError as exc:
            lines.append(f"❌ {name}: extraction failed — {exc}")
            failed = True
            continue

        # F1.8 — report dropped (non-string source_path) mappings before the floor check.
        # These are schema violations; even if the remaining paths pass, a dropped field
        # means the manifest is malformed and the processor would NPE at runtime.
        if dropped_cms_fields:
            lines.append(
                f"❌ {name}: {len(dropped_cms_fields)} mapping(s) have a non-string "
                f"source_path (OEMTelemetryProcessor:331 NPEs on null sourcePath): "
                + ", ".join(dropped_cms_fields)
            )
            failed = True
            continue

        # F1.8 — Anti-vacuity: apply _MIN_MAPPINGS to len(mappings) via the doc total.
        # We re-derive the count from paths + dropped to avoid re-reading the file.
        total_mappings = len(paths) + len(dropped_cms_fields)
        if total_mappings < _MIN_MAPPINGS:
            lines.append(
                f"❌ {name}: only {total_mappings} signal mapping(s), expected at least "
                f"{_MIN_MAPPINGS} — the manifest or the extractor is broken. An empty "
                "mapping set is a subset of everything and would pass vacuously."
            )
            failed = True
            continue
        if len(emitted) < _MIN_EMITTED_KEYS:
            lines.append(
                f"❌ {name}: extracted only {len(emitted)} key(s) from "
                f"{producer_path.name}::{func_name}, expected at least "
                f"{_MIN_EMITTED_KEYS} — the extractor is broken, not the contract. "
                f"({func_name} returns a variable; check that the returned-name follow "
                "and the update()/subscript passes still work.)"
            )
            failed = True
            continue

        # F1.1 — required_emitted is now derived from this manifest's own fields.
        missing_required = required_emitted - emitted
        if missing_required:
            lines.append(
                f"❌ {name}: producer appears not to emit "
                f"{', '.join(sorted(missing_required))} — the transform needs these for "
                "vehicle-id extraction and timestamp parsing, so their absence means the "
                "extractor is broken rather than one signal being unmapped."
            )
            failed = True
            continue

        # F1.7 — split paths on the first unbracketed dot; paths with nested tails whose
        # head IS emitted are out of this guard's static reach (reported informatively, not
        # as gaps). Bracket-notation (arrays/filters) is explicitly out of scope — see the
        # note below if a future extension handles it.
        nested_tails: list[str] = []  # informational only
        flat_paths: set[str] = set()
        nested_missing_heads: list[str] = []
        for sp in paths:
            if "." in sp:
                # NOTE: bracket-notation paths like `[0]` or `[?filter]` are NOT handled
                # here; this guard is static and the schema calls out arrays as runtime
                # concerns. Add handling here deliberately if array paths are introduced.
                head, _ = sp.split(".", 1)
                if head in emitted:
                    nested_tails.append(sp)
                else:
                    nested_missing_heads.append(sp)
            else:
                flat_paths.add(sp)

        gaps = flat_paths - emitted
        # Also include nested paths whose HEAD is not emitted as gaps
        if nested_missing_heads:
            for sp in nested_missing_heads:
                gaps.add(sp)  # head is absent from emitted; the path cannot resolve

        if gaps:
            lines.append(
                f"❌ {name}: {len(gaps)} mapped source_path(s) the producer never emits: "
                + ", ".join(sorted(gaps))
            )
            lines.append(
                f"   {producer_path.name}::{func_name} does not emit these, so the "
                "transform resolves each to its `default_value` (or omits it). No error, "
                "no DLQ, no log — the downstream table fills with defaults that look like "
                "real readings."
            )
            lines.append(
                "   Either the producer renamed/dropped the field (fix the producer, or "
                "update the manifest deliberately), or the manifest names a field that "
                "never existed."
            )
            if unresolvable:
                lines.append(
                    f"   NOTE: {unresolvable} unresolvable `.update(<non-literal>)` "
                    "call(s) in the producer — some emitted keys may be invisible to this "
                    "guard, so verify before editing the manifest."
                )
            failed = True
            continue

        nested_note = ""
        if nested_tails:
            nested_note = f" ({len(nested_tails)} nested source_path(s) with tails — not statically checked)"
        note = f", {unresolvable} unresolvable update() call(s)" if unresolvable else ""
        lines.append(
            f"✅ {name}: all {len(paths)} mapped source_path(s) emitted by "
            f"{producer_path.name}::{func_name} "
            f"({len(emitted)} keys emitted{note}) (0 dropped){nested_note}"
        )

    if failed:
        return 1, lines

    lines.append(
        "   NOTE: this proves the mapped fields EXIST in the producer's payload, not that "
        "their values are correct or that the projection is sufficient downstream."
    )
    return 0, lines


# ── D5: fixture-regeneration guard ────────────────────────────────────────────────────

#: Path to the committed golden fixture (raw JSON) for the Meridian EV end-to-end test.
#: Checked by T3.2 to detect simulator key-set drift without re-running the Java test.
_MERIDIAN_FIXTURE_RAW = (
    _REPO_ROOT
    / "modules"
    / "flink"
    / "src"
    / "test"
    / "resources"
    / "fixtures"
    / "meridian-ev-telemetry.raw.json"
)

#: Command printed when the fixture is stale, so a finding is self-explaining.
_FIXTURE_REGEN_CMD = "python3 scripts/gen_meridian_telemetry_fixture.py"


def check_fixture_freshness() -> tuple[int, list[str]]:
    """T3.2 — assert the committed fixture's key set matches the simulator's emitted keys.

    Reads ``modules/flink/src/test/resources/fixtures/meridian-ev-telemetry.raw.json``
    (committed artifact from T3.1) and compares its top-level key set against
    ``emitted_keys(SIMULATOR_PATH, 'generate_telemetry_data')``.

    - If the simulator gains keys, the fixture is stale (the Java test may miss them).
    - If the fixture has keys the simulator no longer emits, the fixture is stale.
    - Either direction is reported, naming the added/removed keys and the regeneration
      command, so a finding is self-explaining.

    An anti-vacuity floor on both sides prevents a broken fixture from silently
    passing (e.g. an empty JSON object ``{}``) and a broken extractor from silently
    passing as a no-op.
    """
    lines: list[str] = []
    failed = False

    if not _MERIDIAN_FIXTURE_RAW.exists():
        try:
            rel = _MERIDIAN_FIXTURE_RAW.relative_to(_REPO_ROOT)
        except ValueError:
            rel = _MERIDIAN_FIXTURE_RAW
        return 1, [
            f"❌ fixture not found: {rel} — "
            f"regenerate with: {_FIXTURE_REGEN_CMD}"
        ]

    try:
        fixture_doc = json.loads(_MERIDIAN_FIXTURE_RAW.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return 1, [
            f"❌ fixture is not valid JSON: {_MERIDIAN_FIXTURE_RAW.relative_to(_REPO_ROOT)}: {exc} — "
            f"regenerate with: {_FIXTURE_REGEN_CMD}"
        ]

    fixture_keys: set[str] = set(fixture_doc.keys())

    if len(fixture_keys) < _MIN_EMITTED_KEYS:
        return 1, [
            f"❌ fixture has only {len(fixture_keys)} top-level key(s), expected at least "
            f"{_MIN_EMITTED_KEYS} — the fixture is broken or truncated. "
            f"Regenerate with: {_FIXTURE_REGEN_CMD}"
        ]

    try:
        emitted, unresolvable = emitted_keys(SIMULATOR_PATH, "generate_telemetry_data")
    except ExtractionError as exc:
        return 1, [f"❌ fixture-freshness check: extraction failed — {exc}"]

    if len(emitted) < _MIN_EMITTED_KEYS:
        return 1, [
            f"❌ fixture-freshness check: extracted only {len(emitted)} key(s) from the "
            f"simulator, expected at least {_MIN_EMITTED_KEYS} — the extractor is broken."
        ]

    added_to_simulator = emitted - fixture_keys      # simulator has new keys not in fixture
    removed_from_simulator = fixture_keys - emitted  # fixture has keys simulator no longer emits

    if added_to_simulator or removed_from_simulator:
        failed = True
        if added_to_simulator:
            lines.append(
                f"❌ simulator now emits {len(added_to_simulator)} key(s) not in the "
                f"fixture: {', '.join(sorted(added_to_simulator))}"
            )
        if removed_from_simulator:
            lines.append(
                f"❌ fixture has {len(removed_from_simulator)} key(s) the simulator no "
                f"longer emits: {', '.join(sorted(removed_from_simulator))}"
            )
        lines.append(
            f"   The Java end-to-end test reads the committed fixture; stale keys cause "
            "incorrect assertions. Regenerate the fixture and commit both files:"
        )
        lines.append(f"   {_FIXTURE_REGEN_CMD}")
        if unresolvable:
            lines.append(
                f"   NOTE: {unresolvable} unresolvable `.update(<non-literal>)` call(s) "
                "in the simulator — some emitted keys may be invisible to this guard."
            )
        return 1, lines

    note = f" ({unresolvable} unresolvable update() call(s) in simulator)" if unresolvable else ""
    lines.append(
        f"✅ fixture key set matches simulator emitted keys "
        f"({len(fixture_keys)} keys, {len(emitted)} emitted{note})"
    )
    return 0, lines


def _topic_ids_from_msk_script() -> set[str]:
    """Product ids from ``create_msk_topics.py``'s ``CANONICAL_TOPICS`` table."""
    tree = _parse(MSK_TOPICS_SCRIPT)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(isinstance(t, ast.Name) and t.id == "CANONICAL_TOPICS" for t in targets):
                continue
            if not isinstance(node.value, ast.List):
                raise ExtractionError("CANONICAL_TOPICS is not a list literal")
            out = set()
            for elt in node.value.elts:
                if isinstance(elt, ast.Tuple) and elt.elts:
                    first = elt.elts[0]
                    if isinstance(first, ast.Constant) and isinstance(first.value, str):
                        if first.value.startswith(CS_PRODUCT_PREFIX):
                            out.add(first.value[len(CS_PRODUCT_PREFIX):])
            return out
    raise ExtractionError(
        f"CANONICAL_TOPICS not found in {MSK_TOPICS_SCRIPT.name} — it was renamed; "
        "update this guard"
    )


def _topic_ids_from_rule_allowlist() -> set[str]:
    """Product ids implied by ``_ALLOWED_RULE_SUFFIXES`` in the simulation Lambda.

    A suffix ``cs_product_<id>_rule`` corresponds to IoT rule
    ``cms_{stage}_cs_product_<id>_rule``, whose Kafka action publishes to topic
    ``cs-product-<id>``. Underscores become hyphens at that boundary, which is the
    translation this function inverts.
    """
    tree = _parse(SIMULATION_LAMBDA)
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if not any(
                isinstance(t, ast.Name) and t.id == "_ALLOWED_RULE_SUFFIXES" for t in targets
            ):
                continue
            if not isinstance(node.value, (ast.Tuple, ast.List, ast.Set)):
                raise ExtractionError("_ALLOWED_RULE_SUFFIXES is not a literal sequence")
            out = set()
            for elt in node.value.elts:
                if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                    v = elt.value
                    if v.startswith("cs_product_") and v.endswith("_rule"):
                        core = v[len("cs_product_"): -len("_rule")]
                        out.add(core.replace("_", "-"))
            return out
    raise ExtractionError(
        f"_ALLOWED_RULE_SUFFIXES not found in {SIMULATION_LAMBDA.name} — it was renamed; "
        "update this guard"
    )


def _topic_ids_from_cdk_topic_rules() -> set[str]:
    """Product ids from ``telemetry_integration_stack.py``'s ``iot.CfnTopicRule`` calls.

    ``OEMTelemetryProcessor`` reads ``topic=`` verbatim; the CDK stack is the authoritative
    binding between a topic name and a Kafka action. A ``CfnTopicRule`` added here with no
    ``CANONICAL_TOPICS`` row and no rule-suffix entry stays invisible to the other two
    extractors while still demanding ``manifests/<id>-transform.json``.

    Only string literals in ``topic=`` keyword arguments starting ``cs-product-`` are in
    scope. If a ``topic=`` value is an f-string or a variable, it is skipped and a
    diagnostic line is emitted so the omission is visible — do NOT attempt to resolve
    variables or f-strings statically.
    """
    tree = _parse(TELEMETRY_INTEGRATION_STACK)
    out: set[str] = set()
    skipped_dynamic: list[str] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        # Match KafkaActionProperty(topic=...) — that is where the Kafka topic is set.
        func = node.func
        is_kafka_action = (
            isinstance(func, ast.Attribute) and func.attr == "KafkaActionProperty"
        )
        if not is_kafka_action:
            continue
        for kw in node.keywords:
            if kw.arg != "topic":
                continue
            if isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                val = kw.value.value
                if val.startswith(CS_PRODUCT_PREFIX):
                    out.add(val[len(CS_PRODUCT_PREFIX):])
            else:
                # f-string, variable, or other non-literal — skip and mark for diagnostics.
                skipped_dynamic.append(ast.dump(kw.value)[:80])

    return out


def check_topic_coupling() -> tuple[int, list[str]]:
    """D3 — every ``cs-product-*`` topic must have a matching tracked manifest.

    ``deriveSourceKeyFromTopic`` (``OEMTelemetryProcessor.java``) returns the
    ``cs-product-`` suffix **exactly**, and ``loadManifestFromS3`` interpolates it into
    ``manifests/<id>-transform.json``. So the Kafka topic name *is* the manifest filename,
    and renaming the topic silently changes which file the processor demands. The failure
    is loud at runtime (``UnmatchedTopicException`` + the D5 metric and alarm) but only at
    runtime, and then on every record.

    Two independent sources are read and cross-checked rather than unioned: a product with
    a provisioned topic but no allowlisted rule cannot be produced, and an allowlisted rule
    with no provisioned topic publishes into the void. Either asymmetry is a finding.
    """
    lines: list[str] = []
    try:
        from_msk = _topic_ids_from_msk_script()
        from_rules = _topic_ids_from_rule_allowlist()
        from_cdk = _topic_ids_from_cdk_topic_rules()
    except ExtractionError as exc:
        return 1, [f"❌ topic-coupling: extraction failed — {exc}"]

    if not from_msk:
        return 1, [
            f"❌ no 'cs-product-*' entries found in {MSK_TOPICS_SCRIPT.name}'s "
            "CANONICAL_TOPICS — the extractor is broken, not the contract "
            "(zero topics checked would otherwise pass vacuously)"
        ]
    if not from_rules:
        return 1, [
            f"❌ no 'cs_product_*_rule' entries found in {SIMULATION_LAMBDA.name}'s "
            "_ALLOWED_RULE_SUFFIXES — the extractor is broken, not the contract"
        ]
    if not from_cdk:
        return 1, [
            f"❌ no 'cs-product-*' KafkaActionProperty topic= literals found in "
            f"{TELEMETRY_INTEGRATION_STACK.name} — the CDK extractor is broken, not the "
            "contract (zero topics found would otherwise pass vacuously)"
        ]

    failed = False

    # Cross-check all three sources; asymmetry on any pair is a finding.
    # Pairs: MSK vs rules, MSK vs CDK, rules vs CDK.
    all_ids = from_msk | from_rules | from_cdk

    only_msk = from_msk - from_rules - from_cdk
    only_rules = from_rules - from_msk - from_cdk
    only_cdk = from_cdk - from_msk - from_rules

    # Pairwise asymmetries between the three sources
    msk_not_rules = from_msk - from_rules
    rules_not_msk = from_rules - from_msk
    msk_not_cdk = from_msk - from_cdk
    cdk_not_msk = from_cdk - from_msk
    rules_not_cdk = from_rules - from_cdk
    cdk_not_rules = from_cdk - from_rules

    if msk_not_rules:
        lines.append(
            "❌ product topic(s) provisioned in CANONICAL_TOPICS but with no allowlisted rule: "
            + ", ".join(sorted(msk_not_rules))
        )
        lines.append(
            "   Nothing can publish to these: _resolve_rule_name falls back to the "
            "CMS-native default for any rule_name not on the allowlist."
        )
        failed = True
    if rules_not_msk:
        lines.append(
            "❌ rule(s) allowlisted but with no provisioned topic in CANONICAL_TOPICS: "
            + ", ".join(sorted(rules_not_msk))
        )
        lines.append(
            "   The IoT rule would publish into a topic that create_msk_topics.py does "
            "not provision."
        )
        failed = True
    if msk_not_cdk:
        lines.append(
            f"❌ product topic(s) in CANONICAL_TOPICS but absent from {TELEMETRY_INTEGRATION_STACK.name} "
            "KafkaActionProperty: " + ", ".join(sorted(msk_not_cdk))
        )
        lines.append(
            "   The topic is provisioned but no CDK IoT rule routes traffic into it."
        )
        failed = True
    if cdk_not_msk:
        lines.append(
            f"❌ product topic(s) in {TELEMETRY_INTEGRATION_STACK.name} CDK rules but absent from "
            "CANONICAL_TOPICS: " + ", ".join(sorted(cdk_not_msk))
        )
        lines.append(
            "   A CDK rule routes to an unprovisioned topic; the Kafka action would fail "
            "at message delivery."
        )
        failed = True
    if rules_not_cdk:
        lines.append(
            f"❌ rule(s) allowlisted but absent from {TELEMETRY_INTEGRATION_STACK.name} CDK rules: "
            + ", ".join(sorted(rules_not_cdk))
        )
        failed = True
    if cdk_not_rules:
        lines.append(
            f"❌ product topic(s) in {TELEMETRY_INTEGRATION_STACK.name} CDK rules but absent from "
            "_ALLOWED_RULE_SUFFIXES: " + ", ".join(sorted(cdk_not_rules))
        )
        failed = True

    for pid in sorted(from_msk | from_rules | from_cdk):
        expected = MANIFEST_DIR / f"{pid}-transform.json"
        derivation = f"{CS_PRODUCT_PREFIX}{pid} → {pid} → {expected.name}"
        if expected.is_file():
            lines.append(f"✅ {derivation}")
        else:
            lines.append(f"❌ {derivation} — MISSING")
            lines.append(
                f"   OEMTelemetryProcessor would load 'manifests/{expected.name}' for "
                f"topic '{CS_PRODUCT_PREFIX}{pid}' and throw UnmatchedTopicException on "
                "every record. Add the manifest, or rename it to match the topic."
            )
            failed = True

    return (1, lines) if failed else (0, lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--only",
        choices=("parity", "coupling", "fixture"),
        help="run only one of the three checks (default: all three)",
    )
    args = parser.parse_args(argv)

    code = 0
    if args.only in (None, "parity"):
        print("── D1: manifest ↔ producer field parity ──")
        c, lines = check()
        for line in lines:
            print(line)
        code |= c
    if args.only in (None, "coupling"):
        if args.only is None:
            print()
        print("── D3: topic ↔ manifest-filename coupling ──")
        c, lines = check_topic_coupling()
        for line in lines:
            print(line)
        code |= c
    if args.only in (None, "fixture"):
        if args.only is None:
            print()
        print("── D5: fixture ↔ simulator key-set freshness ──")
        c, lines = check_fixture_freshness()
        for line in lines:
            print(line)
        code |= c
    return 1 if code else 0


if __name__ == "__main__":
    sys.exit(main())
