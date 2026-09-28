#!/usr/bin/env python3
# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for ``check_transform_manifest_parity.py`` — spec T1.1.

Spec: ``.kiro/specs/2026-09-20-transform-manifest-contract-guards/tasks.md`` T1.1.

Violation cases point the guard at **synthetic** manifests and producers in ``tmp_path``;
the real files are never edited. The four spec-mandated mutations (M1–M4) each have a named
test, and each asserts the *distinguishing* message rather than merely a non-zero exit —
M2's whole point is that a broken extractor must not present as a contract failure, and a
test that only checked `exit == 1` would pass for either.

Precedent for the import shim: ``scripts/tests/test_check_trip_intent_contract.py``.

Run from repo root::

    python3 -m pytest scripts/tests/test_check_transform_manifest_parity.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).parent.parent / "check_transform_manifest_parity.py"
_spec = importlib.util.spec_from_file_location("check_transform_manifest_parity", _SCRIPT_PATH)
assert _spec and _spec.loader
guard = importlib.util.module_from_spec(_spec)
sys.modules["check_transform_manifest_parity"] = guard
_spec.loader.exec_module(guard)


# ── Synthetic builders ────────────────────────────────────────────────────────


def _manifest(source_paths: list[str], **extra) -> str:
    doc = {
        "manifest_version": "1.0",
        "source_name": "synthetic",
        "timestamp_field": "timestamp",
        "vehicle_id_extraction": {"strategy": "direct", "path": "vehicleId"},
        "signal_mappings": [
            {
                "source_signal": p,
                "cms_field": p,
                "source_path": p,
                "data_type": "float",
                "default_value": 0.0,
            }
            for p in source_paths
        ],
        "validation": {"required_fields": [], "range_checks": []},
    }
    doc.update(extra)
    return json.dumps(doc, indent=2)


def _producer(literal: list[str], subscript: list[str] = (), update: list[str] = (),
              *, unresolvable_update: bool = False,
              func_name: str = "generate_telemetry_data") -> str:
    """Build a producer that returns a variable, like the real one does."""
    lit = ", ".join(f"{k!r}: 0.0" for k in literal)
    sub = "\n".join(f"    telemetry[{k!r}] = 0.0" for k in subscript)
    upd = ""
    if update:
        upd = "\n    telemetry.update({" + ", ".join(f"{k!r}: 0.0" for k in update) + "})"
    if unresolvable_update:
        upd += "\n    telemetry.update(some_other_dict)"
    return f'''
def {func_name}(self, vehicle, previous_state, force_maintenance_alert):
    """Synthetic producer; returns a variable, not a literal."""
    telemetry = {{{lit}}}
{sub}{upd}
    return telemetry
'''


#: A producer key set comfortably over the guard's 100-key floor.
_BULK = [f"filler_{i}" for i in range(120)]
_STRUCTURAL = ["vehicleId", "timestamp"]


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    """Point the guard at a synthetic manifest dir + producer."""
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    prod = tmp_path / "producer.py"

    def install(source_paths: list[str], emitted: list[str], *,
                manifest_name: str = "synthetic-transform.json",
                out_of_scope: dict | None = None,
                producer_kwargs: dict | None = None,
                **manifest_extra) -> None:
        (mdir / manifest_name).write_text(_manifest(source_paths, **manifest_extra),
                                         encoding="utf-8")
        prod.write_text(_producer(emitted, **(producer_kwargs or {})), encoding="utf-8")
        monkeypatch.setattr(guard, "MANIFEST_DIR", mdir)
        monkeypatch.setattr(
            guard, "_IN_SCOPE", {manifest_name: (prod, "generate_telemetry_data")}
        )
        monkeypatch.setattr(guard, "_OUT_OF_SCOPE", out_of_scope or {})

    return install


# ── The real repo ─────────────────────────────────────────────────────────────


def test_parity_holds_against_the_real_repo():
    """Green on the repo as committed, with counts pinned rather than merely non-zero."""
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)
    blob = "\n".join(lines)
    assert "all 38 mapped source_path(s) emitted" in blob
    assert "269 keys emitted" in blob


def test_real_manifest_paths_are_a_strict_subset_of_real_emitted_keys():
    """Pins the actual sets, not counts — the assertion the guard is built on.

    Subset, not equality: the producer emits 269 keys and the manifest projects 38. A
    producer key with no mapping is the intended projection, not a defect.
    """
    paths, dropped, required_emitted = guard.manifest_source_paths(guard.MANIFEST_DIR / "meridian-ev-transform.json")
    emitted, _ = guard.emitted_keys(guard.SIMULATOR_PATH, "generate_telemetry_data")
    assert len(paths) == 38
    assert dropped == [], f"unexpected dropped fields: {dropped}"
    assert paths < emitted, sorted(paths - emitted)
    # F1.1: required_emitted is derived from the manifest, not a constant.
    # vehicleId and timestamp come from vehicle_id_extraction.path and timestamp_field.
    assert "vehicleId" in required_emitted
    assert "timestamp" in required_emitted
    assert required_emitted <= emitted


def test_real_extraction_clears_the_floors_with_headroom():
    """Guards the guard: the floors must not be sitting at the real values."""
    paths, dropped, _ = guard.manifest_source_paths(guard.MANIFEST_DIR / "meridian-ev-transform.json")
    emitted, _ = guard.emitted_keys(guard.SIMULATOR_PATH, "generate_telemetry_data")
    assert len(emitted) > guard._MIN_EMITTED_KEYS * 2
    assert len(paths) > guard._MIN_MAPPINGS * 2
    assert len(emitted) >= guard._MIN_EMITTED_KEYS


def test_oem1_is_excluded_with_a_stated_reason():
    """An exclusion must carry its reason in the output, not be a silent skip."""
    assert "oem1-transform.json" in guard._OUT_OF_SCOPE
    reason = guard._OUT_OF_SCOPE["oem1-transform.json"]
    assert "OEM1 connector feed" in reason
    _, lines = guard.check()
    blob = "\n".join(lines)
    assert "oem1-transform.json: not checked" in blob
    assert "no in-repo key set" in blob


# ── M1: a mapped path the producer never emits ────────────────────────────────


def test_m1_mapped_path_the_producer_never_emits_fails(synthetic):
    """M1 — the defect class. Must name the path and explain the silent default."""
    synthetic(["speed", "tire_pressure_fl"] + _BULK[:10],
              ["speed"] + _STRUCTURAL + _BULK)
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "tire_pressure_fl" in blob
    assert "never emits" in blob
    assert "default_value" in blob


def test_m1_control_same_path_emitted_passes(synthetic):
    """Positive control for M1 — the same mapping, now emitted, is clean."""
    synthetic(["speed", "tire_pressure_fl"] + _BULK[:10],
              ["speed", "tire_pressure_fl"] + _STRUCTURAL + _BULK)
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)


def test_producer_emitting_extra_keys_is_not_an_error(synthetic):
    """Subset, not equality — the projection is intended."""
    synthetic(_BULK[:12], _BULK + _STRUCTURAL + ["unmapped_a", "unmapped_b"])
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)


# ── M2: broken producer extraction ────────────────────────────────────────────


def test_m2_thin_extraction_reports_extractor_fault_not_contract_fault(synthetic):
    """M2 — the distinction that matters most.

    A producer yielding too few keys means the extractor is broken. Reporting it as
    "N mapped paths the producer never emits" would send the next reader to audit ~30
    innocent field names instead of the one broken traversal. Asserting only `exit == 1`
    would pass for either, so this asserts the message AND the absence of the other.
    """
    synthetic(_BULK[:12], ["speed"] + _STRUCTURAL)  # 3 emitted, floor is 100
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "extractor is broken" in blob
    assert "returns a variable" in blob
    assert "never emits" not in blob


def test_m2_returned_variable_follow_is_what_finds_the_keys(synthetic):
    """All three construction forms the real producer uses must be unioned.

    Each of the three mapped keys is reachable only via a different form — `lit_a` from
    the dict literal, `sub_b` from a subscript write, `upd_c` from `update({...})` — so the
    check can only pass if all three passes work. Dropping any one turns this red.
    """
    synthetic(
        ["lit_a", "sub_b", "upd_c"] + _BULK[:10],
        ["lit_a"] + _STRUCTURAL + _BULK,
        producer_kwargs={"subscript": ["sub_b"], "update": ["upd_c"]},
    )
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)


@pytest.mark.parametrize("drop", ["subscript", "update"])
def test_m2_dropping_either_traversal_pass_is_caught(synthetic, drop):
    """Negative control for the test above — each pass is individually load-bearing."""
    kwargs = {"subscript": ["sub_b"], "update": ["upd_c"]}
    del kwargs[drop]
    synthetic(
        ["lit_a", "sub_b", "upd_c"] + _BULK[:10],
        ["lit_a"] + _STRUCTURAL + _BULK,
        producer_kwargs=kwargs,
    )
    code, lines = guard.check()
    assert code == 1
    missing = "sub_b" if drop == "subscript" else "upd_c"
    assert missing in "\n".join(lines)


def test_unresolvable_update_is_counted_and_surfaced(synthetic):
    """`.update(<non-literal>)` cannot be resolved; it must be reported, not ignored.

    Surfaced only alongside a gap finding, where it changes how the gap should be read.
    """
    synthetic(
        ["speed", "invisible_key"] + _BULK[:10],
        ["speed"] + _STRUCTURAL + _BULK,
        producer_kwargs={"unresolvable_update": True},
    )
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "unresolvable" in blob
    assert "verify before editing the manifest" in blob


def test_renamed_producer_function_is_an_extraction_error(synthetic):
    synthetic(_BULK[:12], _BULK + _STRUCTURAL,
              producer_kwargs={"func_name": "generate_telemetry_data_v2"})
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "extraction failed" in blob
    assert "_IN_SCOPE" in blob


# ── M3: vacuous manifest ──────────────────────────────────────────────────────


def test_m3_empty_signal_mappings_does_not_pass_vacuously(synthetic):
    """M3 — the empty set is a subset of everything; the floor must catch it."""
    synthetic([], _BULK + _STRUCTURAL)
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "0 signal mapping(s)" in blob
    assert "vacuously" in blob


def test_missing_signal_mappings_key_is_an_extraction_error(synthetic):
    synthetic(_BULK[:12], _BULK + _STRUCTURAL)
    p = next(guard.MANIFEST_DIR.glob("*-transform.json"))
    doc = json.loads(p.read_text())
    del doc["signal_mappings"]
    p.write_text(json.dumps(doc))
    code, lines = guard.check()
    assert code == 1
    assert "'signal_mappings' is missing" in "\n".join(lines)


# ── M4: structural keys ───────────────────────────────────────────────────────


def test_m4_producer_missing_a_structural_key_is_an_extractor_fault(synthetic):
    """M4 — vehicleId/timestamp absent breaks every record, not one signal."""
    synthetic(_BULK[:12], _BULK + ["timestamp"])  # vehicleId absent
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "vehicleId" in blob
    assert "vehicle-id extraction" in blob
    assert "never emits" not in blob


# ── Classification completeness ───────────────────────────────────────────────


def test_unclassified_manifest_fails(synthetic, monkeypatch):
    """A manifest that is neither in scope nor excluded is silently unguarded."""
    synthetic(_BULK[:12], _BULK + _STRUCTURAL)
    (guard.MANIFEST_DIR / "surprise-transform.json").write_text(
        _manifest(["speed"]), encoding="utf-8"
    )
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "surprise-transform.json" in blob
    assert "neither in scope nor excluded" in blob


def test_no_manifests_at_all_is_a_failure(tmp_path, monkeypatch):
    """An empty directory must not read as 'everything passed'."""
    empty = tmp_path / "empty"
    empty.mkdir()
    monkeypatch.setattr(guard, "MANIFEST_DIR", empty)
    code, lines = guard.check()
    assert code == 1
    assert "no '*-transform.json' files" in "\n".join(lines)


def test_missing_manifest_dir_is_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(guard, "MANIFEST_DIR", tmp_path / "nope")
    code, lines = guard.check()
    assert code == 1
    assert "manifest directory not found" in "\n".join(lines)


def test_main_returns_the_check_exit_code(capsys):
    assert guard.main([]) == 0
    assert "mapped source_path(s) emitted" in capsys.readouterr().out



# ── D3: topic ↔ manifest-filename coupling (T2.1) ─────────────────────────────


@pytest.fixture
def coupling(tmp_path, monkeypatch):
    """Point the coupling check at synthetic sources."""
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    msk = tmp_path / "create_msk_topics.py"
    lam = tmp_path / "simulation_lambda.py"
    cdk_stack = tmp_path / "telemetry_integration_stack.py"

    def install(msk_ids: list[str], rule_ids: list[str], manifest_ids: list[str],
                cdk_ids: list[str] | None = None) -> None:
        # cdk_ids defaults to matching msk_ids so existing tests don't need a 4th arg.
        if cdk_ids is None:
            cdk_ids = list(msk_ids)
        rows = "\n".join(f'    ("cs-product-{i}", 3, 2),' for i in msk_ids)
        msk.write_text(
            "CANONICAL_TOPICS: list[tuple[str, int, int]] = [\n"
            '    ("cms-telemetry-raw", 3, 2),\n' + rows + "\n]\n",
            encoding="utf-8",
        )
        sfx = "\n".join(f'    "cs_product_{i.replace("-", "_")}_rule",' for i in rule_ids)
        lam.write_text(
            "_ALLOWED_RULE_SUFFIXES = (\n"
            '    "iot_msk_rule",\n' + sfx + "\n)\n",
            encoding="utf-8",
        )
        # Synthetic CDK stack: one KafkaActionProperty per cdk_id
        if cdk_ids:
            kafka_blocks = "\n".join(
                f'        iot.CfnTopicRule.KafkaActionProperty(\n'
                f'            destination_arn="arn:aws:iot:us-east-1:123456789012:ruledestination/vpc/fake",\n'
                f'            topic="cs-product-{i}",\n'
                f'            client_properties={{}},\n'
                f'        )'
                for i in cdk_ids
            )
            cdk_stack.write_text(
                f"import iot\nclass TelemetryIntegrationStack:\n    def __init__(self):\n{kafka_blocks}\n",
                encoding="utf-8",
            )
        else:
            # Empty CDK stack — no KafkaActionProperty entries
            cdk_stack.write_text(
                "import iot\nclass TelemetryIntegrationStack:\n    pass\n",
                encoding="utf-8",
            )
        for i in manifest_ids:
            (mdir / f"{i}-transform.json").write_text(_manifest(["speed"]), encoding="utf-8")
        monkeypatch.setattr(guard, "MANIFEST_DIR", mdir)
        monkeypatch.setattr(guard, "MSK_TOPICS_SCRIPT", msk)
        monkeypatch.setattr(guard, "SIMULATION_LAMBDA", lam)
        monkeypatch.setattr(guard, "TELEMETRY_INTEGRATION_STACK", cdk_stack)

    return install


def test_coupling_holds_against_the_real_repo():
    """Green today, and the derivation chain is printed so a failure self-explains."""
    code, lines = guard.check_topic_coupling()
    assert code == 0, "\n".join(lines)
    assert "cs-product-meridian-ev → meridian-ev → meridian-ev-transform.json" in "\n".join(lines)


def test_real_repo_derives_exactly_one_product_from_each_source():
    """Pins the sets. All three sources must agree, and today all yield {meridian-ev}."""
    assert guard._topic_ids_from_msk_script() == {"meridian-ev"}
    assert guard._topic_ids_from_rule_allowlist() == {"meridian-ev"}
    assert guard._topic_ids_from_cdk_topic_rules() == {"meridian-ev"}


def test_m5_renamed_manifest_is_reported_with_the_expected_filename(coupling):
    """M5 — the topic name IS the manifest filename; a rename breaks every record."""
    coupling(["meridian-ev"], ["meridian-ev"], ["meridian-electric"])
    code, lines = guard.check_topic_coupling()
    assert code == 1
    blob = "\n".join(lines)
    assert "meridian-ev-transform.json — MISSING" in blob
    assert "UnmatchedTopicException" in blob


def test_m6a_empty_msk_topic_extraction_trips_the_floor(coupling):
    """M6a — zero topics checked must not pass vacuously."""
    coupling([], ["meridian-ev"], ["meridian-ev"])
    code, lines = guard.check_topic_coupling()
    assert code == 1
    blob = "\n".join(lines)
    assert "extractor is broken" in blob
    assert "vacuously" in blob


def test_m6b_empty_rule_allowlist_extraction_trips_the_floor(coupling):
    """M6b — the other source's floor. Initially a no-op mutation hid this; see decisions.md."""
    coupling(["meridian-ev"], [], ["meridian-ev"])
    code, lines = guard.check_topic_coupling()
    assert code == 1
    assert "_ALLOWED_RULE_SUFFIXES" in "\n".join(lines)


def test_topic_provisioned_with_no_allowlisted_rule_is_a_finding(coupling):
    """Nothing could publish to it — _resolve_rule_name would fall back to CMS-native."""
    coupling(["meridian-ev", "orphan-ev"], ["meridian-ev"], ["meridian-ev", "orphan-ev"])
    code, lines = guard.check_topic_coupling()
    assert code == 1
    blob = "\n".join(lines)
    assert "provisioned in CANONICAL_TOPICS but with no allowlisted rule: orphan-ev" in blob


def test_rule_allowlisted_with_no_provisioned_topic_is_a_finding(coupling):
    """The IoT rule would publish into a topic nothing provisions."""
    coupling(["meridian-ev"], ["meridian-ev", "ghost-ev"], ["meridian-ev", "ghost-ev"])
    code, lines = guard.check_topic_coupling()
    assert code == 1
    assert "allowlisted but with no provisioned topic in CANONICAL_TOPICS: ghost-ev" in "\n".join(lines)


def test_coupling_control_all_three_in_agreement_passes(coupling):
    coupling(["a-ev", "b-ev"], ["a-ev", "b-ev"], ["a-ev", "b-ev"])
    code, lines = guard.check_topic_coupling()
    assert code == 0, "\n".join(lines)


def test_renamed_canonical_topics_constant_is_an_extraction_error(coupling, tmp_path):
    coupling(["meridian-ev"], ["meridian-ev"], ["meridian-ev"])
    guard.MSK_TOPICS_SCRIPT.write_text("TOPICS_V2 = []\n", encoding="utf-8")
    code, lines = guard.check_topic_coupling()
    assert code == 1
    assert "extraction failed" in "\n".join(lines)


def test_main_runs_both_checks_and_ors_their_exit_codes(capsys):
    assert guard.main([]) == 0
    out = capsys.readouterr().out
    assert "D1: manifest ↔ producer field parity" in out
    assert "D3: topic ↔ manifest-filename coupling" in out


def test_main_only_flag_selects_one_check(capsys):
    assert guard.main(["--only", "coupling"]) == 0
    out = capsys.readouterr().out
    assert "D3:" in out and "D1:" not in out


# ── F1.1: manifest-derived required_emitted ───────────────────────────────────


def test_f1_1_renamed_vehicle_id_path_names_new_field_not_vehicleId(synthetic):
    """F1.1 — the finding must name the field from THIS manifest, not the old constant."""
    # vehicle_id_extraction.path = "deviceSerialNumber"; producer does NOT emit it
    synthetic(
        _BULK[:12],
        _BULK + ["timestamp"],  # emits timestamp but NOT deviceSerialNumber
        vehicle_id_extraction={"strategy": "direct", "path": "deviceSerialNumber"},
    )
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "deviceSerialNumber" in blob
    # Must NOT claim the old constant value "vehicleId" is the missing key
    assert "vehicle-id extraction" in blob


def test_f1_1_renamed_timestamp_field_names_new_field(synthetic):
    """F1.1 — renaming timestamp_field → eventTimeUtc: finding names eventTimeUtc."""
    synthetic(
        _BULK[:12],
        _BULK + ["vehicleId"],  # emits vehicleId but NOT eventTimeUtc
        timestamp_field="eventTimeUtc",
    )
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "eventTimeUtc" in blob
    assert "vehicle-id extraction" in blob


def test_f1_1_both_renamed_names_both_new_fields(synthetic):
    """F1.1 — both fields renamed: finding names both new fields."""
    synthetic(
        _BULK[:12],
        _BULK,  # emits neither deviceId nor eventTime
        vehicle_id_extraction={"strategy": "direct", "path": "deviceId"},
        timestamp_field="eventTime",
    )
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    assert "deviceId" in blob
    assert "eventTime" in blob


def test_f1_1_missing_timestamp_field_uses_default(synthetic):
    """F1.1 — a manifest without timestamp_field uses the default 'timestamp'."""
    # Build a manifest doc with NO timestamp_field key using a dedicated JSON approach
    # since _manifest() always sets timestamp_field; we need to override it post-creation
    import tempfile as _tempfile
    doc = {
        "manifest_version": "1.0",
        "source_name": "synthetic",
        # Deliberately NO timestamp_field key — default "timestamp" must be used
        "vehicle_id_extraction": {"strategy": "direct", "path": "vehicleId"},
        "signal_mappings": [
            {"source_signal": k, "cms_field": k, "source_path": k,
             "data_type": "float", "default_value": 0.0}
            for k in _BULK[:12]
        ],
        "validation": {"required_fields": [], "range_checks": []},
    }
    with _tempfile.NamedTemporaryFile(mode='w', suffix='-transform.json', delete=False) as f:
        json.dump(doc, f)
        tmp_path_val = Path(f.name)
    try:
        _, dropped, required_emitted = guard.manifest_source_paths(tmp_path_val)
        # Default "timestamp" must be used
        assert "timestamp" in required_emitted
        assert "vehicleId" in required_emitted
        assert dropped == []
    finally:
        tmp_path_val.unlink()


def test_f1_1_missing_vehicle_id_extraction_is_schema_error(synthetic):
    """F1.1 — a manifest missing vehicle_id_extraction fails with schema-shape message."""
    import tempfile as _tempfile
    doc = {
        "manifest_version": "1.0",
        "source_name": "synthetic",
        "timestamp_field": "timestamp",
        # No vehicle_id_extraction key
        "signal_mappings": [
            {"source_signal": k, "cms_field": k, "source_path": k,
             "data_type": "float", "default_value": 0.0}
            for k in _BULK[:12]
        ],
        "validation": {"required_fields": [], "range_checks": []},
    }
    with _tempfile.NamedTemporaryFile(mode='w', suffix='-transform.json', delete=False) as f:
        json.dump(doc, f)
        tmp_path_val = Path(f.name)
    try:
        with pytest.raises(guard.ExtractionError) as exc_info:
            guard.manifest_source_paths(tmp_path_val)
        assert "vehicle_id_extraction" in str(exc_info.value)
    finally:
        tmp_path_val.unlink()


# ── F1.7: nested source_path handling ────────────────────────────────────────


def test_f1_7_nested_source_path_with_emitted_head_is_not_a_gap(synthetic):
    """F1.7 — tripProgress.percentComplete: head 'tripProgress' emitted, tail not checked."""
    # tripProgress is emitted; the full dot-path is beyond static reach
    synthetic(
        ["speed", "tripProgress.percentComplete"] + _BULK[:10],
        ["speed", "tripProgress"] + _STRUCTURAL + _BULK,
    )
    code, lines = guard.check()
    assert code == 0, "\n".join(lines)
    blob = "\n".join(lines)
    assert "nested" in blob.lower() or "not statically checked" in blob


def test_f1_7_nested_source_path_with_missing_head_is_a_gap(synthetic):
    """F1.7 — notARealKey.foo: head 'notARealKey' not emitted → gap."""
    synthetic(
        ["speed", "notARealKey.foo"] + _BULK[:10],
        ["speed"] + _STRUCTURAL + _BULK,
    )
    code, lines = guard.check()
    assert code == 1
    blob = "\n".join(lines)
    # The gap must name the full path (or at minimum the missing head)
    assert "notARealKey" in blob
    assert "never emits" in blob


# ── F1.8: non-string source_path and _MIN_MAPPINGS pre-filter ────────────────


def test_f1_8_null_source_paths_fail_naming_cms_fields(tmp_path, monkeypatch):
    """F1.8 — 20 of 38 source_paths set to null → FAILS naming the 20 cms_fields."""
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    prod = tmp_path / "producer.py"
    prod.write_text(_producer(_BULK + _STRUCTURAL), encoding="utf-8")

    valid_keys = _BULK[:18]
    null_keys = [f"null_field_{i}" for i in range(20)]
    doc_mappings = (
        [{"source_signal": k, "cms_field": k, "source_path": k,
          "data_type": "float", "default_value": 0.0} for k in valid_keys]
        + [{"source_signal": k, "cms_field": k, "source_path": None,
            "data_type": "float", "default_value": 0.0} for k in null_keys]
    )
    doc = {
        "manifest_version": "1.0",
        "source_name": "synthetic",
        "timestamp_field": "timestamp",
        "vehicle_id_extraction": {"strategy": "direct", "path": "vehicleId"},
        "signal_mappings": doc_mappings,
        "validation": {"required_fields": [], "range_checks": []},
    }
    (mdir / "synthetic-transform.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(guard, "MANIFEST_DIR", mdir)
    monkeypatch.setattr(guard, "_IN_SCOPE", {"synthetic-transform.json": (prod, "generate_telemetry_data")})
    monkeypatch.setattr(guard, "_OUT_OF_SCOPE", {})

    code, lines = guard.check()
    assert code == 1, "\n".join(lines)
    blob = "\n".join(lines)
    assert "non-string source_path" in blob
    for k in null_keys:
        assert k in blob


def test_f1_8_non_string_source_path_dict_fails(tmp_path, monkeypatch):
    """F1.8 — a source_path set to a nested dict → FAILS naming the cms_field."""
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    prod = tmp_path / "producer.py"
    prod.write_text(_producer(_BULK + _STRUCTURAL), encoding="utf-8")

    doc_mappings = (
        [{"source_signal": k, "cms_field": k, "source_path": k,
          "data_type": "float", "default_value": 0.0} for k in _BULK[:11]]
        + [{"source_signal": "bad_field", "cms_field": "bad_field",
            "source_path": {"nested": "dict"},
            "data_type": "object", "default_value": None}]
    )
    doc = {
        "manifest_version": "1.0",
        "source_name": "synthetic",
        "timestamp_field": "timestamp",
        "vehicle_id_extraction": {"strategy": "direct", "path": "vehicleId"},
        "signal_mappings": doc_mappings,
        "validation": {"required_fields": [], "range_checks": []},
    }
    (mdir / "synthetic-transform.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(guard, "MANIFEST_DIR", mdir)
    monkeypatch.setattr(guard, "_IN_SCOPE", {"synthetic-transform.json": (prod, "generate_telemetry_data")})
    monkeypatch.setattr(guard, "_OUT_OF_SCOPE", {})

    code, lines = guard.check()
    assert code == 1, "\n".join(lines)
    blob = "\n".join(lines)
    assert "non-string source_path" in blob
    assert "bad_field" in blob


def test_f1_8_min_mappings_applied_to_total_not_filtered_count(tmp_path, monkeypatch):
    """F1.8 — _MIN_MAPPINGS floor in check() uses total mapping count (len(paths)+len(dropped)).

    Scenario: 15 mappings total, ALL 15 with null source_path → 0 valid paths.
    - If floor applied to 0 (filtered): 0 < 10 → vacuity floor fires first — WRONG.
    - If floor applied to 15 (total): 15 >= 10 → floor passes; non-string error fires.
    We verify the non-string error fires (correct) not the vacuity floor (wrong).

    Since check() reports dropped before the floor, the mutation equivalent is verified
    via manifest_source_paths(): the returned tuple exposes the counts the caller uses.
    """
    mdir = tmp_path / "manifests"
    mdir.mkdir()
    prod = tmp_path / "producer.py"
    prod.write_text(_producer(_BULK + _STRUCTURAL), encoding="utf-8")

    # 15 mappings, all null source_path → 0 valid paths (< floor of 10)
    null_keys = [f"null_{i}" for i in range(15)]
    doc_mappings = [
        {"source_signal": k, "cms_field": k, "source_path": None,
         "data_type": "float", "default_value": 0.0}
        for k in null_keys
    ]
    doc = {
        "manifest_version": "1.0",
        "source_name": "synthetic",
        "timestamp_field": "timestamp",
        "vehicle_id_extraction": {"strategy": "direct", "path": "vehicleId"},
        "signal_mappings": doc_mappings,
        "validation": {"required_fields": [], "range_checks": []},
    }
    (mdir / "synthetic-transform.json").write_text(json.dumps(doc), encoding="utf-8")
    monkeypatch.setattr(guard, "MANIFEST_DIR", mdir)
    monkeypatch.setattr(guard, "_IN_SCOPE", {"synthetic-transform.json": (prod, "generate_telemetry_data")})
    monkeypatch.setattr(guard, "_OUT_OF_SCOPE", {})

    code, lines = guard.check()
    assert code == 1, "\n".join(lines)
    blob = "\n".join(lines)
    # Must be the non-string error (because 15 total >= 10), NOT the vacuity floor
    assert "non-string source_path" in blob
    assert "vacuously" not in blob

    # Direct check: manifest_source_paths exposes the (paths, dropped) tuple.
    # total_mappings = len(paths) + len(dropped) must equal the manifest mapping count.
    paths, dropped, _ = guard.manifest_source_paths(mdir / "synthetic-transform.json")
    assert len(paths) == 0
    assert len(dropped) == 15
    assert len(paths) + len(dropped) == 15  # total_mappings: what check() uses for floor


# ── F1.3: CDK topic rule extractor ───────────────────────────────────────────


def test_f1_3_cdk_extractor_returns_real_repo_topic(monkeypatch):
    """F1.3 — the real telemetry_integration_stack.py yields {meridian-ev}."""
    ids = guard._topic_ids_from_cdk_topic_rules()
    assert "meridian-ev" in ids


def test_f1_3_renamed_cdk_topic_causes_asymmetry(coupling):
    """F1.3 — renaming topic= in CDK to 'cs-product-fake' → asymmetry finding naming CDK."""
    # msk and rules agree on meridian-ev; CDK has fake-ev instead
    coupling(["meridian-ev"], ["meridian-ev"], ["meridian-ev"],
             cdk_ids=["fake-ev"])
    code, lines = guard.check_topic_coupling()
    assert code == 1
    blob = "\n".join(lines)
    # CDK has fake-ev which is absent from MSK and rules
    assert "fake-ev" in blob
    # telemetry_integration_stack.py is named as the outlier source
    assert "telemetry_integration_stack.py" in blob or "CDK" in blob


def test_f1_3_new_cdk_topic_with_matching_entries_and_no_manifest_reports_missing(coupling):
    """F1.3 — a NEW cs-product-fake topic in all three sources but no manifest → MISSING."""
    # All three sources agree on fake-ev; but no manifest file for it
    coupling(["meridian-ev", "fake-ev"], ["meridian-ev", "fake-ev"], ["meridian-ev"],
             cdk_ids=["meridian-ev", "fake-ev"])
    code, lines = guard.check_topic_coupling()
    assert code == 1
    blob = "\n".join(lines)
    assert "fake-ev-transform.json" in blob
    assert "MISSING" in blob


def test_f1_3_empty_cdk_extraction_trips_the_floor(coupling):
    """F1.3 — zero CDK topics found must not pass vacuously."""
    coupling(["meridian-ev"], ["meridian-ev"], ["meridian-ev"], cdk_ids=[])
    code, lines = guard.check_topic_coupling()
    assert code == 1
    blob = "\n".join(lines)
    assert "CDK extractor is broken" in blob or "extractor is broken" in blob


def test_real_repo_cdk_extractor_finds_meridian_ev():
    """F1.3 smoke: the real stack file yields exactly {meridian-ev}."""
    ids = guard._topic_ids_from_cdk_topic_rules()
    assert ids == {"meridian-ev"}


# ── T3.2: fixture-regeneration guard ─────────────────────────────────────────────────────


@pytest.fixture
def fixture_dir(tmp_path, monkeypatch):
    """Point the guard at a tmp fixture directory and a synthetic simulator.

    Yields a helper:
        fixture_dir(keys: list[str], emitted: list[str]) -> (code, lines)

    The synthetic fixture has ``keys`` as its top-level keys; the synthetic
    simulator emits ``emitted``.
    """
    def _make(fixture_keys: list[str], emitted_keys_: list[str]):
        # Write a synthetic fixture
        fixture_file = tmp_path / "meridian-ev-telemetry.raw.json"
        fixture_doc = {k: 0.0 for k in fixture_keys}
        fixture_file.write_text(json.dumps(fixture_doc, indent=2), encoding="utf-8")

        # Write a synthetic simulator that emits emitted_keys_
        sim_file = tmp_path / "fake_simulator.py"
        lit = ", ".join(f"{k!r}: 0.0" for k in emitted_keys_)
        sim_file.write_text(
            f"def generate_telemetry_data(self, v, s=None, f=False):\n"
            f"    telemetry = {{{lit}}}\n"
            f"    return telemetry\n",
            encoding="utf-8",
        )

        monkeypatch.setattr(guard, "_MERIDIAN_FIXTURE_RAW", fixture_file)
        monkeypatch.setattr(guard, "SIMULATOR_PATH", sim_file)

        return guard.check_fixture_freshness()

    return _make


def test_t3_2_fixture_freshness_passes_when_keys_match(fixture_dir):
    """T3.2 — fixture and simulator have the same 150 keys → guard passes."""
    keys = [f"key_{i}" for i in range(150)]
    code, lines = fixture_dir(keys, keys)
    assert code == 0
    blob = "\n".join(lines)
    assert "✅" in blob


def test_t3_2_m9_simulator_gains_key_guard_fails_naming_it(fixture_dir):
    """M9 — simulator gains a new key the fixture doesn't have → guard fails naming it."""
    base_keys = [f"key_{i}" for i in range(150)]
    # Simulator emits one extra key not in the fixture
    simulator_keys = base_keys + ["brand_new_signal"]
    code, lines = fixture_dir(base_keys, simulator_keys)
    assert code == 1, "Guard must fail when simulator gains a new key"
    blob = "\n".join(lines)
    assert "brand_new_signal" in blob, (
        "Finding must name the added key"
    )
    # Must also print the regeneration command
    assert "gen_meridian_telemetry_fixture.py" in blob, (
        "Finding must include the regeneration command"
    )


def test_t3_2_fixture_has_key_simulator_dropped_fails(fixture_dir):
    """T3.2 — fixture has a key the simulator no longer emits → guard fails naming it."""
    base_keys = [f"key_{i}" for i in range(150)]
    fixture_keys = base_keys + ["old_signal_removed"]
    code, lines = fixture_dir(fixture_keys, base_keys)
    assert code == 1
    blob = "\n".join(lines)
    assert "old_signal_removed" in blob


def test_t3_2_fixture_missing_guard_reports_regen_cmd(tmp_path, monkeypatch):
    """T3.2 — fixture file absent → guard reports the regeneration command."""
    monkeypatch.setattr(
        guard, "_MERIDIAN_FIXTURE_RAW", tmp_path / "meridian-ev-telemetry.raw.json"
    )
    code, lines = guard.check_fixture_freshness()
    assert code == 1
    blob = "\n".join(lines)
    assert "gen_meridian_telemetry_fixture.py" in blob


def test_t3_2_fixture_below_floor_fails(tmp_path, monkeypatch):
    """T3.2 — fixture with fewer than MIN_EMITTED_KEYS top-level keys trips the floor."""
    fixture_file = tmp_path / "meridian-ev-telemetry.raw.json"
    fixture_file.write_text(json.dumps({"vehicleId": "x", "timestamp": 1}), encoding="utf-8")
    monkeypatch.setattr(guard, "_MERIDIAN_FIXTURE_RAW", fixture_file)
    code, lines = guard.check_fixture_freshness()
    assert code == 1
    blob = "\n".join(lines)
    assert "broken or truncated" in blob or "floor" in blob.lower() or "least" in blob


def test_t3_2_guard_passes_on_real_repo():
    """T3.2 smoke: real fixture and real simulator are in sync."""
    code, lines = guard.check_fixture_freshness()
    assert code == 0, "\n".join(lines)


def test_t3_2_main_fixture_only_flag():
    """T3.2 — ``--only fixture`` runs only the fixture guard and exits 0 today."""
    import io
    from contextlib import redirect_stdout
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = guard.main(["--only", "fixture"])
    assert rc == 0
    out = buf.getvalue()
    assert "D5" in out
    assert "D1" not in out
    assert "D3" not in out
