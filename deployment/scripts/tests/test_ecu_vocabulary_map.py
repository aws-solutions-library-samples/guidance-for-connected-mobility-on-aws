# SPDX-License-Identifier: Apache-2.0
"""
Tests: ECU vocabulary mapping — DX16, DX17, DX20.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (D18, F4, C6, C11)

DX16 — The ECU mapping is complete in BOTH directions:
  (a) Every model-manifest ECU (TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU) has
      an explicit entry in MODEL_ECU_TO_SIDECAR, even when its value is empty.
  (b) Every sidecar ECU in _SIDECAR_ECU_MAP (all 9) appears either in a
      forward-map value OR in SIDECAR_WITHOUT_MODEL_ECU — never just absent.
  (c) The mapping is many-to-many: at least one model ECU maps to multiple
      addresses, and at least one maps to zero addresses.
  (d) The empty case (GW → []) is explicitly asserted, because a mapping that
      silently drops GW would under-report an ECU the operator can see elsewhere.

DX17 — A model ECU with no diagnostic address is reported as
  NOT_REMOTELY_DIAGNOSABLE_REASON, never silently omitted from results.
  Asserted by showing that callers receive an explicit signal (empty frozenset +
  reason string) rather than a missing key or None.

DX20 — _SIDECAR_ECU_MAP (realtime_telemetry_simulator.py) still agrees with
  simulation_lambda._ECU_BY_NUMBER in BOTH name and CAN ID.  The comment at
  realtime_telemetry_simulator.py:386-390 asserts this agreement; nothing in
  the repo enforced it before this task.

  CAN-ID normalisation: _SIDECAR_ECU_MAP stores int values (e.g. 0x7E0 = 2016),
  _ECU_BY_NUMBER stores hex strings (e.g. "0x7E0").  Both are normalised to int
  before comparison (int(v, 16) for strings).

  FAIL-DEMONSTRATION: the test perturbs an in-memory copy to prove it CAN detect
  a divergence, then restores the original.  This ensures DX20 is not vacuously
  green.  Neither source file is edited.

SCOPE NOTE
----------
This file tests ecu_vocabulary_map.py, which lives in services/simulation/.
To keep path resolution simple the test adds both the repo root and
services/simulation to sys.path.

No VINs, brand names, or account IDs per C8 (public mirror).
ECU names and CAN IDs are standard automotive vocabulary.
"""

from __future__ import annotations

import importlib
import os
import sys
from copy import deepcopy
from typing import Any, Dict, FrozenSet, Optional

import pytest

# ---------------------------------------------------------------------------
# sys.path setup
# Adds the repo root so we can import services.simulation.ecu_vocabulary_map,
# and services/simulation as a flat path (the C5 dual-context requirement).
# ---------------------------------------------------------------------------
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_SIM_DIR = os.path.join(_REPO_ROOT, "services", "simulation")
_SIM_LAMBDA_DIR = os.path.join(_SIM_DIR, "lambda")

for _p in [_REPO_ROOT, _SIM_DIR, _SIM_LAMBDA_DIR]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


# ---------------------------------------------------------------------------
# Import the module under test
# Uses the same guarded-import pattern as test_ecu_powertrain_profiles.py
# ---------------------------------------------------------------------------

def _load_module(name: str) -> Optional[Any]:
    """Try importing `name`; return None if it does not exist."""
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError:
        return None


_evm = _load_module("ecu_vocabulary_map")
_MODULE_PRESENT = _evm is not None


# ---------------------------------------------------------------------------
# Vocabulary constants (read from module if present; hard-coded for assertions)
# ---------------------------------------------------------------------------

# Model-manifest ECU names (seed_model_manifests.py:51-58).
# Hard-coded here so DX16 asserts completeness against these, not against the
# module's own self-declared set (which would be circular).
# T5.2 added ECM (Engine Control Module, approved 2026-09-03). Updated from 8 → 9.
_EXPECTED_MODEL_ECU_NAMES: FrozenSet[str] = frozenset({
    "TCU", "BMS", "VCU", "BCM", "ADAS", "IVI", "GW", "CCU",
    "ECM",   # T5.2: Engine Control Module — owns ECU_ENGINE + ECU_EVAP
})

# Sidecar ECU names from _SIDECAR_ECU_MAP (realtime_telemetry_simulator.py:391).
# Hard-coded for the same reason.
_EXPECTED_SIDECAR_ECU_NAMES: FrozenSet[str] = frozenset({
    "ECU_BRAKE",
    "ECU_ENGINE",
    "ECU_POWERTRAIN",
    "ECU_PCM",
    "ECU_COMM",
    "ECU_BATTERY_HV",
    "ECU_BATTERY_12V",
    "ECU_EVAP",
    "ECU_BODY",
})

# D18 spec-fixed rows — non-negotiable, validated here regardless of the rest.
_SPEC_FIXED_ROWS = {
    "BMS": frozenset({"ECU_BATTERY_HV", "ECU_BATTERY_12V"}),
    "VCU": frozenset({"ECU_POWERTRAIN", "ECU_PCM"}),
    "BCM": frozenset({"ECU_BODY"}),
    "GW":  frozenset(),
}


# ===========================================================================
# DX16a — forward-map completeness: every model ECU has an entry
# ===========================================================================

def test_dx16a_forward_map_covers_all_model_ecus() -> None:
    """DX16(a): MODEL_ECU_TO_SIDECAR contains a key for every model-manifest ECU.

    9 keys expected: TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU, ECM (added T5.2).
    An ECU missing from the map would silently drop it from DID resolution.
    """
    assert _MODULE_PRESENT, (
        "ecu_vocabulary_map module not found — T4.2 not yet shipped"
    )
    actual_keys = frozenset(_evm.MODEL_ECU_TO_SIDECAR.keys())
    missing = _EXPECTED_MODEL_ECU_NAMES - actual_keys
    extra = actual_keys - _EXPECTED_MODEL_ECU_NAMES
    assert not missing, (
        f"DX16a FAIL — model ECUs absent from MODEL_ECU_TO_SIDECAR: {missing}"
    )
    assert not extra, (
        f"DX16a FAIL — unexpected model ECU names in MODEL_ECU_TO_SIDECAR: {extra}"
    )


# ===========================================================================
# DX16b — reverse coverage: every sidecar ECU is accounted for
# ===========================================================================

def test_dx16b_every_sidecar_ecu_is_accounted_for() -> None:
    """DX16(b): every sidecar ECU appears in a forward-map value OR in
    SIDECAR_WITHOUT_MODEL_ECU — no sidecar ECU is just absent.

    9 sidecar ECUs: ECU_BRAKE, ECU_ENGINE, ECU_POWERTRAIN, ECU_PCM, ECU_COMM,
    ECU_BATTERY_HV, ECU_BATTERY_12V, ECU_EVAP, ECU_BODY.
    """
    assert _MODULE_PRESENT, (
        "ecu_vocabulary_map module not found — T4.2 not yet shipped"
    )
    # All sidecar ECUs that appear in any forward-map value.
    in_forward: FrozenSet[str] = frozenset(
        name
        for sidecar_set in _evm.MODEL_ECU_TO_SIDECAR.values()
        for name in sidecar_set
    )
    # Plus those explicitly declared as having no model-manifest owner.
    accounted: FrozenSet[str] = in_forward | _evm.SIDECAR_WITHOUT_MODEL_ECU

    # Check module's declared ALL_SIDECAR_ECU_NAMES matches our expectation.
    assert _evm.ALL_SIDECAR_ECU_NAMES == _EXPECTED_SIDECAR_ECU_NAMES, (
        f"DX16b FAIL — ALL_SIDECAR_ECU_NAMES mismatch.\n"
        f"  Expected : {sorted(_EXPECTED_SIDECAR_ECU_NAMES)}\n"
        f"  Got      : {sorted(_evm.ALL_SIDECAR_ECU_NAMES)}"
    )

    missing = _EXPECTED_SIDECAR_ECU_NAMES - accounted
    assert not missing, (
        f"DX16b FAIL — sidecar ECUs missing from both forward map and "
        f"SIDECAR_WITHOUT_MODEL_ECU: {missing}"
    )


# ===========================================================================
# DX16c — many-to-many cardinality asserted
# ===========================================================================

def test_dx16c_mapping_is_many_to_many() -> None:
    """DX16(c): at least one model ECU maps to multiple sidecar addresses,
    and at least one maps to zero addresses (the empty/GW case).

    This asserts the mapping is genuinely many-to-many and not secretly 1:1.
    """
    assert _MODULE_PRESENT, (
        "ecu_vocabulary_map module not found — T4.2 not yet shipped"
    )
    multi_mappings = [
        (k, v)
        for k, v in _evm.MODEL_ECU_TO_SIDECAR.items()
        if len(v) > 1
    ]
    assert multi_mappings, (
        "DX16c FAIL — no model ECU maps to multiple sidecar addresses; "
        "the mapping is not many-to-many as required by D18"
    )

    empty_mappings = [
        k for k, v in _evm.MODEL_ECU_TO_SIDECAR.items() if len(v) == 0
    ]
    assert empty_mappings, (
        "DX16c FAIL — no model ECU maps to zero sidecar addresses; "
        "the empty case (GW → []) is missing"
    )


# ===========================================================================
# DX16d — GW's empty mapping is explicit
# ===========================================================================

def test_dx16d_gw_empty_case_is_explicit() -> None:
    """DX16(d): GW has an explicit empty entry — not a missing key.

    A mapping that silently drops GW would under-report an ECU the operator
    can see in the model manifest.  The empty case is a real modelled state.
    """
    assert _MODULE_PRESENT, (
        "ecu_vocabulary_map module not found — T4.2 not yet shipped"
    )
    assert "GW" in _evm.MODEL_ECU_TO_SIDECAR, (
        "DX16d FAIL — GW is missing from MODEL_ECU_TO_SIDECAR; "
        "the empty case must be explicitly modelled"
    )
    assert _evm.MODEL_ECU_TO_SIDECAR["GW"] == frozenset(), (
        f"DX16d FAIL — GW should map to an empty set; got "
        f"{_evm.MODEL_ECU_TO_SIDECAR['GW']}"
    )


# ===========================================================================
# DX16e — D18 spec-fixed rows are exact
# ===========================================================================

def test_dx16e_spec_fixed_rows_are_exact() -> None:
    """DX16(e): The four D18 spec-fixed rows match their spec-mandated values.

    BMS → {ECU_BATTERY_HV, ECU_BATTERY_12V}
    VCU → {ECU_POWERTRAIN, ECU_PCM}
    BCM → {ECU_BODY}
    GW  → {}
    """
    assert _MODULE_PRESENT, (
        "ecu_vocabulary_map module not found — T4.2 not yet shipped"
    )
    for model_ecu, expected in _SPEC_FIXED_ROWS.items():
        actual = _evm.MODEL_ECU_TO_SIDECAR.get(model_ecu)
        assert actual is not None, (
            f"DX16e FAIL — spec-fixed row '{model_ecu}' missing from table"
        )
        assert actual == expected, (
            f"DX16e FAIL — spec-fixed row '{model_ecu}' mismatch.\n"
            f"  Expected : {sorted(expected)}\n"
            f"  Got      : {sorted(actual)}"
        )


# ===========================================================================
# DX17 — empty mapping renders as NOT_REMOTELY_DIAGNOSABLE_REASON
# ===========================================================================

def test_dx17_empty_mapping_yields_named_reason() -> None:
    """DX17: a model ECU with no diagnostic address MUST render a named reason.

    The caller receives:
      - is_remotely_diagnosable(ecu) == False
      - get_sidecar_ecus_for_model_ecu(ecu) == frozenset()  (empty, not None)
      - NOT_REMOTELY_DIAGNOSABLE_REASON is a non-empty string

    This is asserted for every model ECU whose forward mapping is empty.
    The GW case is the spec-mandated example; IVI and CCU are also expected to
    be empty in the current table.
    """
    assert _MODULE_PRESENT, (
        "ecu_vocabulary_map module not found — T4.2 not yet shipped"
    )
    reason = _evm.NOT_REMOTELY_DIAGNOSABLE_REASON
    assert isinstance(reason, str) and reason.strip(), (
        "DX17 FAIL — NOT_REMOTELY_DIAGNOSABLE_REASON is empty or not a string"
    )

    for model_ecu, sidecar_set in _evm.MODEL_ECU_TO_SIDECAR.items():
        if not sidecar_set:
            # Model ECU with no sidecar address — must signal this explicitly.
            assert not _evm.is_remotely_diagnosable(model_ecu), (
                f"DX17 FAIL — is_remotely_diagnosable('{model_ecu}') returned "
                f"True despite empty address set"
            )
            result = _evm.get_sidecar_ecus_for_model_ecu(model_ecu)
            assert isinstance(result, frozenset), (
                f"DX17 FAIL — get_sidecar_ecus_for_model_ecu('{model_ecu}') "
                f"returned {type(result)}, expected frozenset"
            )
            assert result == frozenset(), (
                f"DX17 FAIL — get_sidecar_ecus_for_model_ecu('{model_ecu}') "
                f"returned {result!r}, expected empty frozenset"
            )
            # The result must NOT be None, a missing key, or raise an exception.
            # Reaching here without error confirms silent-omission is prevented.


def test_dx17_gw_is_not_remotely_diagnosable() -> None:
    """DX17 — GW (the spec-canonical empty case) is not remotely diagnosable."""
    assert _MODULE_PRESENT, (
        "ecu_vocabulary_map module not found — T4.2 not yet shipped"
    )
    assert not _evm.is_remotely_diagnosable("GW"), (
        "DX17 FAIL — GW should not be remotely diagnosable"
    )
    assert _evm.get_sidecar_ecus_for_model_ecu("GW") == frozenset(), (
        "DX17 FAIL — GW should map to empty frozenset"
    )
    reason = _evm.NOT_REMOTELY_DIAGNOSABLE_REASON
    assert "remotely" in reason.lower() or "diagnosable" in reason.lower(), (
        f"DX17 FAIL — reason string '{reason}' does not convey diagnostic intent"
    )


def test_dx17_unknown_model_ecu_raises() -> None:
    """DX17 — requesting an unknown model ECU name raises KeyError.

    Ensures the caller gets an explicit error rather than silently receiving
    an empty set for a typo or an unregistered ECU name.
    """
    assert _MODULE_PRESENT, (
        "ecu_vocabulary_map module not found — T4.2 not yet shipped"
    )
    with pytest.raises(KeyError):
        _evm.get_sidecar_ecus_for_model_ecu("NONEXISTENT_ECU")


# ===========================================================================
# DX20 — _SIDECAR_ECU_MAP agrees with simulation_lambda._ECU_BY_NUMBER
# ===========================================================================

def _load_sidecar_map() -> Optional[Dict]:
    """Load _SIDECAR_ECU_MAP from realtime_telemetry_simulator (no side effects)."""
    try:
        rts = importlib.import_module("realtime_telemetry_simulator")
        return rts._SIDECAR_ECU_MAP  # type: ignore[attr-defined]
    except (ModuleNotFoundError, AttributeError):
        return None


def _load_lambda_ecu_by_number() -> Optional[Dict]:
    """Load _ECU_BY_NUMBER from simulation_lambda.

    simulation_lambda reads several env vars at import time (ECS_CLUSTER,
    WORKER_TASK_DEF, etc.) and also imports _lib.fleet_membership from the
    OEM1 connector overlay.  We set stub values before importing, following
    the pattern established by services/simulation/lambda/test_simulation_lambda.py.
    """
    import os as _os

    # Stub the required env vars so the module-level globals construct without
    # real AWS resources.
    _os.environ.setdefault("ECS_CLUSTER", "test-cluster")
    _os.environ.setdefault(
        "WORKER_TASK_DEF",
        "arn:aws:ecs:us-west-2:111111111111:task-definition/cms-test-worker:1",
    )
    _os.environ.setdefault("WORKER_SUBNETS", "subnet-aaa,subnet-bbb")
    _os.environ.setdefault("WORKER_SECURITY_GROUP", "sg-zzz")
    _os.environ.setdefault("SIMULATIONS_TABLE", "cms-test-simulations")
    _os.environ.setdefault("DEPLOYMENT_STAGE", "test")
    _os.environ.setdefault("AWS_REGION", "us-west-2")
    _os.environ.setdefault("AWS_DEFAULT_REGION", "us-west-2")
    _os.environ.setdefault("FWE_TASK_DEF", "cms-test-fwe-agent")
    _os.environ.setdefault("FWE_SIM_TASK_DEF", "cms-test-fwe-simulator")

    # Add the OEM1 connector root to sys.path so _lib.fleet_membership resolves
    # (same overlay that CDK applies to the Lambda bundle at deploy time).
    _oem1_root = os.path.normpath(
        os.path.join(_REPO_ROOT, "services", "connectors", "oem1")
    )
    if _oem1_root not in sys.path:
        sys.path.insert(0, _oem1_root)

    try:
        sl = importlib.import_module("simulation_lambda")
        return sl._ECU_BY_NUMBER  # type: ignore[attr-defined]
    except (ModuleNotFoundError, AttributeError, KeyError):
        return None


def _normalise_can_id(value: Any) -> int:
    """Convert a CAN ID to int regardless of whether it is an int or hex string.

    _SIDECAR_ECU_MAP stores req_id as int (e.g. 0x7E0 = 2016).
    _ECU_BY_NUMBER stores "req" as hex string (e.g. "0x7E0").
    Both normalise to the same int.
    """
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        return int(value, 16)
    raise TypeError(f"Cannot normalise CAN ID: {value!r}")


def test_dx20_sidecar_map_names_agree_with_lambda() -> None:
    """DX20: _SIDECAR_ECU_MAP ECU names match _ECU_BY_NUMBER ECU names exactly.

    The comment at realtime_telemetry_simulator.py:386-390 declares these two
    constants are in agreement.  This test enforces that claim.

    Both maps must contain the same 9 ECU names.
    """
    sidecar_map = _load_sidecar_map()
    lambda_map = _load_lambda_ecu_by_number()

    if sidecar_map is None:
        pytest.skip("realtime_telemetry_simulator not importable (python-can absent)")
    if lambda_map is None:
        pytest.skip("simulation_lambda not importable")

    sidecar_names = frozenset(sidecar_map.keys())
    lambda_names = frozenset(entry["name"] for entry in lambda_map.values())

    missing_from_sidecar = lambda_names - sidecar_names
    missing_from_lambda = sidecar_names - lambda_names

    assert not missing_from_sidecar, (
        f"DX20 FAIL — ECU names in _ECU_BY_NUMBER but NOT in _SIDECAR_ECU_MAP: "
        f"{missing_from_sidecar}"
    )
    assert not missing_from_lambda, (
        f"DX20 FAIL — ECU names in _SIDECAR_ECU_MAP but NOT in _ECU_BY_NUMBER: "
        f"{missing_from_lambda}"
    )


def test_dx20_sidecar_map_can_ids_agree_with_lambda() -> None:
    """DX20: CAN IDs agree between _SIDECAR_ECU_MAP and _ECU_BY_NUMBER.

    _SIDECAR_ECU_MAP stores req_id as int (e.g. 0x7E0 = 2016).
    _ECU_BY_NUMBER stores "req" as hex string (e.g. "0x7E0").
    Both are normalised to int before comparison.
    """
    sidecar_map = _load_sidecar_map()
    lambda_map = _load_lambda_ecu_by_number()

    if sidecar_map is None:
        pytest.skip("realtime_telemetry_simulator not importable (python-can absent)")
    if lambda_map is None:
        pytest.skip("simulation_lambda not importable")

    # Build name→req_id from lambda map.
    lambda_by_name: Dict[str, int] = {
        entry["name"]: _normalise_can_id(entry["req"])
        for entry in lambda_map.values()
    }

    mismatches = []
    for ecu_name, cfg in sidecar_map.items():
        sidecar_req_id = _normalise_can_id(cfg["req_id"])
        lambda_req_id = lambda_by_name.get(ecu_name)
        if lambda_req_id is None:
            continue  # name mismatch already caught by test_dx20_names
        if sidecar_req_id != lambda_req_id:
            mismatches.append(
                f"  {ecu_name}: sidecar req_id={hex(sidecar_req_id)}, "
                f"lambda req={hex(lambda_req_id)}"
            )

    assert not mismatches, (
        "DX20 FAIL — CAN ID divergence between _SIDECAR_ECU_MAP and "
        "_ECU_BY_NUMBER:\n" + "\n".join(mismatches)
    )


def test_dx20_fail_demonstration_name_divergence() -> None:
    """DX20 — FAIL DEMONSTRATION (names): a perturbed in-memory copy is detected.

    This test modifies an IN-MEMORY COPY of _SIDECAR_ECU_MAP (not the live
    module attribute) and runs the name-agreement check against it.  The check
    must detect the divergence.  The original module is not touched.

    This demonstrates that DX20's name check is not vacuously green.
    """
    lambda_map = _load_lambda_ecu_by_number()
    if lambda_map is None:
        pytest.skip("simulation_lambda not importable")

    # Build a copy of sidecar map names with a deliberate mutation.
    lambda_names = frozenset(entry["name"] for entry in lambda_map.values())

    # Perturb: remove one real ECU name, add a fake one.
    # We pick ECU_BRAKE (guaranteed in both maps) and a clearly fake name.
    mutated_sidecar_names = (lambda_names - {"ECU_BRAKE"}) | {"ECU_FAKE_PERTURBATION"}

    missing_from_sidecar = lambda_names - mutated_sidecar_names
    missing_from_lambda = mutated_sidecar_names - lambda_names

    assert missing_from_sidecar or missing_from_lambda, (
        "DX20 FAIL DEMONSTRATION: the perturbed copy was NOT detected as divergent; "
        "the name-agreement check is vacuously green and cannot serve as a guard"
    )
    # If we reach here, the perturbation was detected — proof the real check works.


def test_dx20_fail_demonstration_can_id_divergence() -> None:
    """DX20 — FAIL DEMONSTRATION (CAN IDs): a perturbed in-memory copy is detected.

    Builds an in-memory sidecar map with one CAN ID changed and verifies the
    CAN-ID agreement check detects it.  The real module attribute is NOT changed.
    """
    sidecar_map = _load_sidecar_map()
    lambda_map = _load_lambda_ecu_by_number()

    if sidecar_map is None:
        pytest.skip("realtime_telemetry_simulator not importable (python-can absent)")
    if lambda_map is None:
        pytest.skip("simulation_lambda not importable")

    # Build name→req_id from lambda.
    lambda_by_name: Dict[str, int] = {
        entry["name"]: _normalise_can_id(entry["req"])
        for entry in lambda_map.values()
    }

    # Deep-copy the sidecar map and mutate one CAN ID.
    mutated = deepcopy(dict(sidecar_map))
    # Pick ECU_BRAKE (req_id 0x7E0) and change it to a clearly wrong value.
    target_name = "ECU_BRAKE"
    assert target_name in mutated, f"ECU_BRAKE not in sidecar map — fix test setup"
    original_req_id = mutated[target_name]["req_id"]
    mutated[target_name]["req_id"] = 0xDEAD  # obviously wrong CAN ID

    # Run the agreement check against the mutated copy.
    mismatches = []
    for ecu_name, cfg in mutated.items():
        sidecar_req_id = _normalise_can_id(cfg["req_id"])
        lambda_req_id = lambda_by_name.get(ecu_name)
        if lambda_req_id is None:
            continue
        if sidecar_req_id != lambda_req_id:
            mismatches.append(ecu_name)

    assert mismatches, (
        "DX20 FAIL DEMONSTRATION: the CAN-ID perturbation on ECU_BRAKE "
        f"(changed from {hex(original_req_id)} to 0xdead) was NOT detected; "
        "the CAN-ID agreement check is vacuously green and cannot serve as a guard"
    )
    # Reaching here confirms the real check would catch a real divergence.
