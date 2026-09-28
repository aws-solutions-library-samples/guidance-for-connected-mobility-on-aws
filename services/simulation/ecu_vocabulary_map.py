# SPDX-License-Identifier: Apache-2.0
"""
ECU vocabulary mapping — single source of truth for the many-to-many join
between the two disjoint ECU name sets in the platform.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (D18, F4, C11)

WHY THIS EXISTS
---------------
The platform has two ECU vocabularies that are disjoint — not renamed, different:

  1. Model-manifest ECU names (seed_model_manifests.py:51-58) — domain/functional:
       TCU, BMS, VCU, BCM, ADAS, IVI, GW, CCU  (8 names)

  2. Sidecar diagnostic addresses (_SIDECAR_ECU_MAP, realtime_telemetry_simulator.py:391)
     — OBD-II / UDS addressing:
       ECU_BRAKE(0x7E0), ECU_ENGINE(0x7E1), ECU_POWERTRAIN(0x7E2), ECU_PCM(0x7E3),
       ECU_COMM(0x7E4), ECU_BATTERY_HV(0x7E5), ECU_BATTERY_12V(0x7E6),
       ECU_EVAP(0x7E7), ECU_BODY(0x18DA09F1)  (9 names)

Nothing in the platform previously joined these sets. A per-model DID table keyed
on manifest ECU names cannot be dispatched to a CAN address without this mapping.

CARDINALITY CONTRACT
--------------------
The mapping is many-to-many and MUST allow empty:

  - A model ECU can map to zero addresses ("not remotely diagnosable") — e.g. GW.
  - A model ECU can map to multiple addresses — e.g. BMS → two battery addresses.
  - A sidecar address can appear in at most one model-ECU's forward list.
    (No address is shared by two domain ECUs — that would mean two ECU owners
     for the same bus slot, which is a protocol error, not a platform gap.)

Rejecting either direction silently is prohibited. DX17: a model ECU with no
diagnostic address renders "not remotely diagnosable" — never drops. DX16:
completeness asserted in both directions.

DO NOT RENAME EITHER VOCABULARY (C11)
---------------------------------------
The sidecar's names describe what answers at a specific CAN address.
The manifest's names are what the rest of the platform, the UI, and
baselineVersion already use.  Renaming either side makes one of them lie.

SOURCE-OF-TRUTH NOTE (C6)
--------------------------
_SIDECAR_ECU_MAP (realtime_telemetry_simulator.py:391) is already declared a
READ-ONLY copy of simulation_lambda._ECU_BY_NUMBER.  This module introduces a
THIRD set of names that could diverge — it deliberately does NOT re-declare
those constants.  DX20 enforces the agreement between the two existing copies
by importing each source directly.  This module imports neither; it only maps
names to names.  Any code that needs CAN IDs must look them up in _SIDECAR_ECU_MAP.

JUDGEMENT-CALL ROWS (READ THIS BEFORE ACCEPTING THE TABLE)
-----------------------------------------------------------
Four rows in MODEL_ECU_TO_SIDECAR are marked JUDGEMENT CALL.  They are NOT
addressing facts — they are architect-review candidates, and this spec has been
bitten four times by unvalidated key assumptions (F9, F12, F13, duplicate dtc_code).
These rows require explicit sign-off before Stage 2 ships.  See HANDBACK section
below and the inline rationale comments.

JUDGEMENT-CALL ROWS — RESOLVED 2026-09-03 by the platform owner
---------------------------------------------------------------
All four rows that were open at authoring time are now decided:

  1. TCU  → [ECU_COMM]     — APPROVED as-is (comms-domain alignment).
  2. ADAS → []             — CHANGED from [ECU_BRAKE] to empty. 0x7E0 is the ABS/brake
                             controller (C-prefix chassis codes route there; C0035 is a
                             wheel-speed sensor), not an ADAS domain controller. Empty
                             is the safe failure; a wrong owner mis-dispatches reads.
  3. IVI  → []             — APPROVED as-is (no standard UDS address).
  4. CCU  → []             — APPROVED as-is (no OBD-II slot; BMS owns the battery pair).

  ECU_ENGINE + ECU_EVAP    — owner APPROVED as a NEW `ECM` model ECU, added by T5.2.
  ECU_BRAKE                — remains unowned pending a confirmed chassis/ABS model ECU.

See SIDECAR_WITHOUT_MODEL_ECU below for the T5.2 requirement and the per-powertrain
expectation this implies.
"""

from __future__ import annotations

from typing import Dict, FrozenSet, List

# ---------------------------------------------------------------------------
# MODEL_ECU_TO_SIDECAR
#
# Key   — model-manifest ECU name (from seed_model_manifests.py:51-58)
# Value — frozenset of sidecar ECU names this model ECU maps to
#         (from _SIDECAR_ECU_MAP in realtime_telemetry_simulator.py:391)
#
# Each entry is tagged with the rationale and whether it is:
#   SPEC-FIXED     — explicitly decided in spec.md D18; not a judgement call
#   JUDGEMENT CALL — needs architect sign-off before Stage 2 ships
# ---------------------------------------------------------------------------

MODEL_ECU_TO_SIDECAR: Dict[str, FrozenSet[str]] = {
    # -----------------------------------------------------------------------
    # SPEC-FIXED rows (D18 in spec.md)
    # -----------------------------------------------------------------------

    "BMS": frozenset({"ECU_BATTERY_HV", "ECU_BATTERY_12V"}),
    # SPEC-FIXED (D18): Battery Management System answers on both the
    # high-voltage pack address (0x7E5) and the 12V auxiliary address (0x7E6).
    # The join is not 1:1 — one domain ECU, two diagnostic addresses.

    "VCU": frozenset({"ECU_POWERTRAIN", "ECU_PCM"}),
    # SPEC-FIXED (D18): Vehicle Control Unit supervises the powertrain (0x7E2)
    # and the Powertrain Control Module (0x7E3).

    "BCM": frozenset({"ECU_BODY"}),
    # SPEC-FIXED (D18): Body Control Module answers on the extended-addressing
    # body ECU slot (0x18DA09F1).

    "GW": frozenset(),
    # SPEC-FIXED (D18): Central Gateway is a routing node, not a UDS target;
    # it plausibly has no diagnostic address.  EMPTY IS A REAL MODELLED STATE —
    # never omit this row; DX16 asserts its explicit presence.

    # -----------------------------------------------------------------------
    # JUDGEMENT-CALL rows — require architect sign-off before Stage 2 ships
    # -----------------------------------------------------------------------

    "TCU": frozenset({"ECU_COMM"}),
    # JUDGEMENT CALL: Telematics Control Unit handles vehicle connectivity.
    # ECU_COMM (0x7E4) is the comms-domain address in the sidecar map, making
    # the domain alignment plausible.  However, no OBD-II or UDS specification
    # mandates a TCU behind 0x7E4 — this is a demo-platform convention, not a
    # protocol fact.  A TCU on a real vehicle may answer at a different slot or
    # may have no UDS diagnostic address at all (e.g. if it is a proprietary
    # telematics module).  Needs architect sign-off.

    "ADAS": frozenset(),
    # ARCHITECT-DECIDED 2026-09-03 (platform owner approved): **empty**, not ECU_BRAKE.
    # The first pass assigned ECU_BRAKE (0x7E0) on "ADAS integrates with braking"
    # domain reasoning. That was rejected on evidence: `_ECU_PREFIX_DEFAULT` in
    # simulation_lambda.py routes C-prefix *chassis* codes to ECU_BRAKE, and the
    # catalog's C0035 at that address is a wheel-speed sensor — so 0x7E0 is the
    # ABS/brake controller, not an ADAS domain controller.
    #
    # Empty is the SAFE failure here: it renders "not remotely diagnosable", which is
    # visible and fixable. A wrong owner would silently dispatch per-model DID reads to
    # the wrong CAN address — a correctness bug rather than a declared gap. Per D18 an
    # unmapped model ECU is modelled explicitly, never dropped.
    #
    # If the platform later confirms an ADAS diagnostic address, add it here.

    "IVI": frozenset(),
    # JUDGEMENT CALL: Infotainment & Cluster typically has no OBD-II diagnostic
    # address.  Most vehicle diagnostic interfaces do not expose IVI as a UDS
    # target via the standard 0x7Exx range.  Treating it as empty by analogy
    # with GW (routing node, no address) is the conservative interpretation.
    # If the platform intends to expose IVI diagnostics via a manufacturer
    # extension address, this row must be updated and the sidecar map extended.
    # Needs architect sign-off.

    "CCU": frozenset(),
    # JUDGEMENT CALL: Charger Control Unit is an EV/hybrid-specific ECU with no
    # standard OBD-II diagnostic address slot.  BMS already owns the two battery
    # sidecar addresses (ECU_BATTERY_HV / ECU_BATTERY_12V).  Assigning CCU a
    # separate address would require extending _SIDECAR_ECU_MAP, which is
    # explicitly READ-ONLY (C6).  Treated as "not remotely diagnosable" in v1.
    # Needs architect sign-off.

    "ECM": frozenset({"ECU_ENGINE", "ECU_EVAP"}),
    # T5.2 — APPROVED 2026-09-03 by platform owner.
    # Engine Control Module owns BOTH ECU_ENGINE (0x7E1) and ECU_EVAP (0x7E7).
    # One ECU rather than two: EVAP purge is engine-managed on gasoline vehicles.
    #
    # ⚠️ PER-POWERTRAIN NOTE: The vocabulary maps ECM to {ECU_ENGINE, ECU_EVAP}.
    # The DIESEL profile deliberately excludes ECU_EVAP at the PROFILE layer
    # (powertrain_profiles.py) because diesel vehicles have no EVAP system.
    # The vocabulary map records what ECM *can* own; the profile records what
    # a specific powertrain *should* resolve. These are different gates.
    #
    # ECU_BRAKE (0x7E0) remains unowned — see SIDECAR_WITHOUT_MODEL_ECU.
}

# ---------------------------------------------------------------------------
# SIDECAR_TO_MODEL_ECUS (reverse index — derived, not maintained separately)
#
# Sidecar ECU name → frozenset of model ECU names that map to it.
# Built once at import time from MODEL_ECU_TO_SIDECAR so there is exactly
# one source of truth.  DX16 asserts this covers every sidecar ECU name.
# ---------------------------------------------------------------------------

def _build_reverse_index(
    forward: Dict[str, FrozenSet[str]],
) -> Dict[str, FrozenSet[str]]:
    """Invert MODEL_ECU_TO_SIDECAR into a sidecar-keyed reverse index.

    Every sidecar ECU name that appears in any forward-map value gets a key.
    Sidecar ECUs that appear in NO forward-map value end up in
    SIDECAR_WITHOUT_MODEL_ECU (computed separately).
    """
    reverse: Dict[str, List[str]] = {}
    for model_ecu, sidecar_ecus in forward.items():
        for sidecar_name in sidecar_ecus:
            reverse.setdefault(sidecar_name, []).append(model_ecu)
    return {k: frozenset(v) for k, v in reverse.items()}


SIDECAR_TO_MODEL_ECUS: Dict[str, FrozenSet[str]] = _build_reverse_index(
    MODEL_ECU_TO_SIDECAR
)

# ---------------------------------------------------------------------------
# SIDECAR_WITHOUT_MODEL_ECU
#
# Sidecar ECU names that do not appear in any forward-map value.
# These are CAN bus targets that exist in the sidecar but have no model-manifest
# counterpart.  They are EXPLICITLY MODELLED here, not silently absent.
#
# Currently: {"ECU_BRAKE"}  — computed, do not hand-maintain.
#
#   ECU_BRAKE  (0x7E0) — ABS / chassis brake controller.  No model ECU claims it;
#                        see the ADAS row above for why ADAS was rejected as its owner.
#   ECU_ENGINE (0x7E1) — was unowned pre-T5.2; now owned by ECM (added T5.2).
#   ECU_EVAP   (0x7E7) — was unowned pre-T5.2; now owned by ECM (added T5.2).
#
# ECU_BRAKE remains deliberately unowned pending a confirmed chassis/ABS model ECU.
# ---------------------------------------------------------------------------

_ALL_SIDECAR_NAMES_IN_FORWARD: FrozenSet[str] = frozenset(
    name
    for sidecar_set in MODEL_ECU_TO_SIDECAR.values()
    for name in sidecar_set
)

# Full set of sidecar ECU names — must match _SIDECAR_ECU_MAP exactly (DX20).
ALL_SIDECAR_ECU_NAMES: FrozenSet[str] = frozenset({
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

# Full set of model-manifest ECU names (seed_model_manifests.py:51-58).
# ECM was added by T5.2 (approved 2026-09-03) — Engine Control Module owning
# both ECU_ENGINE and ECU_EVAP. See MODEL_ECU_TO_SIDECAR["ECM"] below.
ALL_MODEL_ECU_NAMES: FrozenSet[str] = frozenset({
    "TCU",
    "BMS",
    "VCU",
    "BCM",
    "ADAS",
    "IVI",
    "GW",
    "CCU",
    "ECM",   # T5.2: Engine Control Module (ECU_ENGINE + ECU_EVAP owner)
})

# ---------------------------------------------------------------------------
# DBC_NODE_NAMES — the THIRD ECU vocabulary. Recorded, not mapped.
#
# Source: the `BU_:` line of `deployment/ecr/cms-sim-service/can/cms-fleet.dbc`
# (an identical copy ships in cms-fwe-agent/can/). This is the node set the FWE
# agent actually decodes CAN with, so it is the vocabulary the *telemetry* path
# already speaks — which makes it load-bearing even though nothing dispatches
# diagnostics through it.
#
# D18/F4 frame the reconciliation problem as TWO vocabularies. There are three.
# This set is largely DISJOINT from the manifest's: the manifest's TCU, VCU, GW,
# CCU and IVI have no DBC node, and the DBC's ECM, TCM, TPMS, ICM, HVAC, LIGHT,
# CONN, MAINT and GPS have no manifest ECU. Only BCM, BMS and ADAS are shared.
#
# ⚠️ WHY THIS IS HERE RATHER THAN IN THE MAP — read before T5.2.
#
# T5.2 adds `ECM` to the manifest vocabulary (approved 2026-09-03). **The DBC
# already declares `ECM`, and `TCM`.** Pick manifest names WITHOUT checking this
# set and the manifest ends up disagreeing with the file the FWE agent decodes
# with — a fourth fork, in a spec that has already been bitten five times by
# unvalidated key assumptions. Check here first; reuse the DBC's spelling.
#
# Deliberately NOT joined into MODEL_ECU_TO_SIDECAR: nothing dispatches through
# DBC nodes today, and inventing a third mapping edge would add exactly the kind
# of unsourced structure this module exists to avoid. Recorded so it cannot be
# forgotten; mapped only if and when something needs it.
# ---------------------------------------------------------------------------

DBC_NODE_NAMES: FrozenSet[str] = frozenset({
    "ECM",    # Engine Control Module — the name T5.2 should adopt
    "TCM",    # Transmission Control Module
    "BCM",    # shared with the model manifest
    "TPMS",   # gives tire-pressure events a real owning node
    "ADAS",   # shared with the model manifest
    "BMS",    # shared with the model manifest
    "ICM",
    "HVAC",
    "LIGHT",
    "CONN",
    "MAINT",
    "GPS",
})

SIDECAR_WITHOUT_MODEL_ECU: FrozenSet[str] = (
    ALL_SIDECAR_ECU_NAMES - _ALL_SIDECAR_NAMES_IN_FORWARD
)

# ---------------------------------------------------------------------------
# DBC_NODE_TO_MODEL_ECU — the join DBC_NODE_NAMES deliberately deferred.
#
# The DBC_NODE_NAMES block above says the third vocabulary is "mapped only if and
# when something needs it." T4.3 (signal→ECU attribution) is that need: DX28 asserts
# every signal in a model's subset maps to an ECU **that model actually has**, and
# model manifests are written in the manifest vocabulary. So a DBC transmitting node
# has to resolve to a manifest ECU name or the attribution cannot be checked.
#
# ⚠️ MOST OF THIS TABLE IS JUDGEMENT, NOT FACT. Read before trusting a count.
#
# Only BCM, BMS and ADAS are shared between the two vocabularies — those three rows
# are identity and safe. The other nine are domain reasoning, and they are tagged
# individually. They follow the precedent T4.2 set: ship a value with the tag and the
# rationale, surface it for sign-off, let the owner adjudicate (which is how the four
# T4.2 judgement rows were resolved on 2026-09-03).
#
# WHY NOT LEAVE THE WEAK ROWS EMPTY, given the ADAS→[] precedent?
# Because the two failure modes are not symmetric here. An unmapped *sidecar address*
# renders "not remotely diagnosable" — a visible, declared gap. An unmapped *DBC node*
# strands its signals in no model's subset, so they silently vanish from every derived
# count — which is the F9 drift this task exists to kill. Provenance is the mitigation
# instead: every attribution records which tier produced it, so a reviewed guess is
# distinguishable from an authoritative read. See signal_attribution.py.
# ---------------------------------------------------------------------------

DBC_NODE_TO_MODEL_ECU: Dict[str, str] = {
    # --- identity rows: the name is shared by both vocabularies. Safe. ---
    "BCM": "BCM",
    "BMS": "BMS",
    "ADAS": "ADAS",

    # --- approved row ---
    "ECM": "ECM",
    # T5.2 added ECM to ALL_MODEL_ECU_NAMES and MODEL_ECU_TO_SIDECAR. The DBC
    # already declares ECM, and the manifest vocabulary now matches it.
    # ECM is in ALL_MODEL_ECU_NAMES as of T5.2, so this row is no longer pending.

    # --- JUDGEMENT CALL rows: need owner sign-off ---
    "TCM": "VCU",
    # JUDGEMENT: Transmission Control Module. VCU already owns ECU_POWERTRAIN and
    # ECU_PCM, so transmission sits inside its domain. A real vehicle may expose the
    # TCM as its own UDS target instead.

    "TPMS": "BCM",
    # JUDGEMENT: follows R11's own seed hint (`tpms` → `BCM`). Tyre-pressure receivers
    # are body-controlled on most platforms. The DBC having a TPMS node is what gives
    # tyre-pressure events a real owning node at all (see DBC_NODE_NAMES).

    "ICM": "IVI",
    # JUDGEMENT: Instrument Cluster Module. The manifest's IVI is explicitly
    # "Infotainment & Cluster", so the cluster half is already in IVI's remit.

    "HVAC": "BCM",
    # JUDGEMENT: climate control. Body-controlled on most platforms; the manifest has
    # no climate ECU. Weakish — a dedicated HVAC controller is common on real vehicles.

    "LIGHT": "BCM",
    # JUDGEMENT: exterior/interior lighting is body-domain. Reasonably strong.

    "CONN": "TCU",
    # JUDGEMENT: connectivity → Telematics Control Unit. Strong; TCU is the comms ECU
    # and maps to ECU_COMM.

    "GPS": "TCU",
    # JUDGEMENT: position/GNSS is telematics-provided. Strong, same reasoning as CONN.

    "MAINT": "BCM",
    # ⚠️ WEAKEST ROW — flag for sign-off first. "MAINT" is a *functional* grouping
    # (service intervals, oil life, pad wear), not a physical ECU, so no mapping is
    # really defensible. BCM is chosen because those counters are body/cluster
    # aggregated on this platform. The alternative — leaving it unmapped — strands 8
    # DBC signals in no model subset, which is the silent-drift failure this task
    # exists to prevent, so a tagged guess is the lesser evil. Revisit with the owner.
}

# ---------------------------------------------------------------------------
# NOT_REMOTELY_DIAGNOSABLE_REASON
#
# The string a caller MUST render when a model ECU maps to zero sidecar
# addresses (DX17).  Centralised here so every consumer says the same thing.
# ---------------------------------------------------------------------------

NOT_REMOTELY_DIAGNOSABLE_REASON: str = "not remotely diagnosable"

# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def get_sidecar_ecus_for_model_ecu(model_ecu_name: str) -> FrozenSet[str]:
    """Return the sidecar ECU names for a given model-manifest ECU name.

    Raises KeyError if model_ecu_name is not in MODEL_ECU_TO_SIDECAR — every
    known model ECU must be in the table; unknown names are a caller bug.

    Returns an empty frozenset for ECUs that have no diagnostic address (GW,
    IVI, CCU in the current table).  An empty result means "not remotely
    diagnosable" — callers MUST surface this state, never silently omit.
    """
    return MODEL_ECU_TO_SIDECAR[model_ecu_name]


def get_model_ecus_for_sidecar_ecu(sidecar_ecu_name: str) -> FrozenSet[str]:
    """Return the model-manifest ECU names that map to a given sidecar ECU.

    Returns an empty frozenset for sidecar ECUs that appear in no forward
    mapping (currently ECU_ENGINE and ECU_EVAP).  Those ECUs are reachable via
    the sidecar directly but have no model-manifest driver, so they cannot be
    resolved per-model — see SIDECAR_WITHOUT_MODEL_ECU for why that blocks
    DX22/DX30 until T5.2 extends the manifest vocabulary.
    """
    return SIDECAR_TO_MODEL_ECUS.get(sidecar_ecu_name, frozenset())


def is_remotely_diagnosable(model_ecu_name: str) -> bool:
    """Return True iff the model ECU has at least one diagnostic address."""
    return bool(get_sidecar_ecus_for_model_ecu(model_ecu_name))
