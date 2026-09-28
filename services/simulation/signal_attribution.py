# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
# SPDX-License-Identifier: Apache-2.0
"""
Signal → ECU attribution, and the derived signal counts that replace hand-written ones.

Spec: .kiro/specs/2026-09-02-cms-diagnostics-platform (T4.3, D21, F9, F13, F15)

WHAT THIS FIXES
---------------
Per F9 the model manifest asserted `signalCount: 280` and eight per-ECU counts summing
to 230, against a catalog that holds 319 rows and carries **no `ecu` field at all**. The
counts were decorative: nothing joined them to the catalog, so nothing could contradict
them. D21's rule is that a count which cannot be re-derived is a claim, and claims drift
silently — the same class as the README cost table that sat 22 months stale.

So attribution is **derived at read time, never stored.** No `ecu` attribute is
backfilled onto the 319 catalog rows. That is deliberate and consistent with the
`classification` decision of 2026-09-03: a stored copy is a fourth thing to drift.

THREE TIERS, AND WHY THE PROPORTIONS MATTER (F15)
--------------------------------------------------
The DBC is the approved authoritative source (2026-09-03). Measured against the **live**
319-row catalog, not against the DBC's own 262 signals:

    tier 1  DBC `BU_` transmitting node   74  (23.2%)  authoritative
    tier 2  DBC message-name prefix       12  ( 3.8%)  reviewed hint
    tier 3  normalised `signal_group`     233 (73.0%)  last resort

The DBC anchors 86 of 319. It is genuinely free and portable for those — swap in a
customer's real DBC and tier 1 re-derives itself — but it does **not** carry most of the
catalog, because the two sets are largely disjoint (exact `signal_name` overlap: 81).
Anyone planning follow-on work off the "28.6%" figure in T4.3's RESULT block should read
F15 first: that number is DBC-internal, and the tier-3 hand review is the real cost.

Every attribution therefore records its **provenance**, so a reader can tell an
authoritative read from a reviewed guess. A count whose provenance is 73% `signal_group`
should not be presented as though the bus told us.

JOIN KEYS, IN ORDER
-------------------
1. `signal_name` → DBC signal name. Exact, 81 hits.
2. `can_id` → DBC message id. Exact, **65 of 65 hit, no near-misses.** Preferred where
   both exist: signal names get renamed, CAN ids are the wire contract.
3. normalised `signal_group` → SIGNAL_GROUP_TO_MODEL_ECU.

F13 IS LIVE IN TIER 3 — READ BEFORE ADDING A GROUP
---------------------------------------------------
`signal_group` carries casing and synonym collisions: `chassis`/`Chassis`,
`emissions`/`Emissions`, `Engine` vs `powertrain`, `EV` vs `ev_charging`,
`HVAC` vs `cabin_climate`. Tier 3 consumes the **normalised** group, or attribution
reintroduces exactly the defect F13 identified — and DX27 keys on the same normalisation.

`normalise_signal_group` here is intended as the SINGLE source of that normalisation.
T5.1b must import it rather than write a second one; two normalisers is the drift
pattern this module exists to end.
"""

from __future__ import annotations

import os
import re
from typing import Dict, FrozenSet, Iterable, List, NamedTuple, Optional, Tuple

try:  # flat import (tests add services/simulation to sys.path)
    from ecu_vocabulary_map import ALL_MODEL_ECU_NAMES, DBC_NODE_TO_MODEL_ECU
except ModuleNotFoundError:  # package-qualified import
    from services.simulation.ecu_vocabulary_map import (  # type: ignore
        ALL_MODEL_ECU_NAMES,
        DBC_NODE_TO_MODEL_ECU,
    )

# ---------------------------------------------------------------------------
# The DBC. An identical copy ships in cms-fwe-agent/can/ — the sim-service copy is
# canonical here only because it is the one the simulator loads. Verified identical
# (byte-for-byte) 2026-09-03; if they ever diverge that is a defect, not a choice.
# ---------------------------------------------------------------------------

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.abspath(os.path.join(_HERE, "..", ".."))
DBC_PATH = os.path.join(
    _REPO_ROOT, "deployment", "ecr", "cms-sim-service", "can", "cms-fleet.dbc"
)

# Marks a DBC message with no declared transmitting node. Not an ECU name.
_NO_TRANSMITTER = "Vector__XXX"

# ECM has been added to ALL_MODEL_ECU_NAMES by T5.2 (ecu_vocabulary_map.py).
# _PENDING_MODEL_ECUS is now empty — it existed to bridge the gap between T4.3
# (which attributed signals to ECM before the manifest vocabulary included it)
# and T5.2 (which adds ECM to the vocabulary). With ECM in ALL_MODEL_ECU_NAMES,
# the two forks are collapsed and this set is redundant.
_PENDING_MODEL_ECUS: FrozenSet[str] = frozenset()

#: Every name attribution is allowed to produce.
VALID_ATTRIBUTION_TARGETS: FrozenSet[str] = ALL_MODEL_ECU_NAMES | _PENDING_MODEL_ECUS

# Provenance tiers, most to least authoritative.
PROV_DBC_NODE = "dbc_node"
PROV_DBC_PREFIX = "dbc_prefix"
PROV_SIGNAL_GROUP = "signal_group"
PROV_UNATTRIBUTED = "unattributed"

PROVENANCE_RANK: Dict[str, int] = {
    PROV_DBC_NODE: 1,
    PROV_DBC_PREFIX: 2,
    PROV_SIGNAL_GROUP: 3,
    PROV_UNATTRIBUTED: 4,
}


# ---------------------------------------------------------------------------
# F13 normalisation — the single source. T5.1b must import this.
# ---------------------------------------------------------------------------

#: Synonym → canonical group. Casing is handled by lowercasing first; this table is
#: only for genuine synonyms, where two *different words* mean the same domain.
_GROUP_SYNONYMS: Dict[str, str] = {
    "engine": "powertrain",      # F13: 'Engine' rows are combustion-relevant
    "ev": "ev_charging",         # F13: bare 'EV' is the EV-exclusive group
    "hvac": "cabin_climate",     # F13: HVAC and cabin_climate are one domain
}


def normalise_signal_group(raw: Optional[str]) -> str:
    """Canonicalise a `signal_group` value (F13).

    Lowercases, trims, collapses internal whitespace/hyphens to underscores, then
    applies the synonym table. Returns "" for None/blank — callers must treat an
    empty group as unattributable rather than guessing.

    This is the single source of signal-group normalisation. T5.1b consumes it;
    DX27 keys on the same result.
    """
    if not raw:
        return ""
    slug = re.sub(r"[\s\-]+", "_", str(raw).strip().lower())
    return _GROUP_SYNONYMS.get(slug, slug)


# ---------------------------------------------------------------------------
# Tier 3: normalised signal_group → model ECU.
#
# ⚠️ ENTIRELY JUDGEMENT. This table attributes 73% of the catalog and NONE of it is
# sourced from the bus. It is the "review once by hand, then freeze behind the tests"
# step R11 called for, and it needs owner sign-off the same way T4.2's four rows did.
#
# Two rows are load-bearing for powertrain correctness and should be reviewed first:
#
#   powertrain -> ECM   NOT VCU. DX27 requires a full-EV model to resolve ZERO
#                       powertrain-exclusive signals. EV manifests carry VCU, so
#                       attributing powertrain to VCU would make DX27 unsatisfiable;
#                       ECM is ICE/hybrid-only, so the exclusion falls out of the
#                       vocabulary instead of being asserted separately.
#   ev_charging -> CCU  Charger Control Unit, chosen over R11's BMS/CCU pair because
#                       attribution must be 1:1 or per-ECU counts double-count. CCU is
#                       EV/hybrid-only, which makes DX27's mirror half work the same way.
# ---------------------------------------------------------------------------

SIGNAL_GROUP_TO_MODEL_ECU: Dict[str, str] = {
    # powertrain / energy — the DX27-relevant rows
    "powertrain": "ECM",
    "emissions": "ECM",          # EVAP is engine-managed; ECM owns ECU_EVAP
    "ev_charging": "CCU",
    "ev_specific": "CCU",        # EV-exclusive, so it must map to an EV-only ECU for
                                 # DX27's mirror half to hold. CCU rather than BMS
                                 # because ICE lines legitimately carry a 12V BMS.
    # vehicle dynamics
    "vehicle_control": "VCU",
    "driving": "VCU",            # harsh-event accelerometry (lateral/longitudinal)
    "chassis": "VCU",            # JUDGEMENT: yaw/traction are ABS/ESC domain, and no
                                 # model ECU owns ECU_BRAKE. VCU is the closest owner.
    # driver assistance & safety
    "adas": "ADAS",
    "safety": "ADAS",            # JUDGEMENT: impact/airbag status. Could be BCM.
    # telematics
    "connectivity": "TCU",
    "geofence": "TCU",
    "gps": "TCU",                # consistent with the GPS DBC node → TCU
    "diagnostics": "GW",         # JUDGEMENT: MIL/DTC aggregates. GW rather than ECM so
                                 # EV models still resolve diagnostics signals.
    "core_telemetry": "GW",      # JUDGEMENT: odometer/speed aggregates, gateway-routed.
    # body & comfort — BCM owns the convenience domain
    "doors": "BCM",
    "windows": "BCM",
    "mirrors": "BCM",
    "wipers": "BCM",
    "lighting": "BCM",
    "security": "BCM",
    "body": "BCM",
    "cabin_climate": "BCM",
    "environment": "BCM",
    "tpms": "BCM",
    "maintenance": "BCM",        # weakest, same reasoning as the MAINT DBC node
}


# ---------------------------------------------------------------------------
# DBC parsing
# ---------------------------------------------------------------------------

class DbcSignal(NamedTuple):
    """One signal as declared in the DBC."""
    signal_name: str
    message_name: str
    message_id: int
    transmitter: str

    @property
    def has_named_transmitter(self) -> bool:
        return bool(self.transmitter) and self.transmitter != _NO_TRANSMITTER

    @property
    def message_prefix(self) -> str:
        return self.message_name.split("_")[0]


class DbcIndex(NamedTuple):
    """Parsed DBC, indexed for both join keys."""
    by_signal_name: Dict[str, DbcSignal]
    by_message_id: Dict[int, DbcSignal]
    declared_nodes: Tuple[str, ...]


_RE_MESSAGE = re.compile(r"^BO_\s+(\d+)\s+(\w+)\s*:\s*(\d+)\s+(\S+)")
_RE_SIGNAL = re.compile(r"^\s+SG_\s+(\w+)\s*:")
_RE_NODES = re.compile(r"^BU_\s*:\s*(.*)$")


def parse_dbc(path: Optional[str] = None) -> DbcIndex:
    """Parse the DBC into a signal index keyed by both name and message id.

    Only `BU_`, `BO_` and `SG_` records are read — enough for attribution. Raises
    FileNotFoundError if the DBC is absent, deliberately: silently degrading to
    tier 3 for the whole catalog would be a 73%→100% guess with no signal to the
    caller that the authoritative source went missing.
    """
    dbc_path = path or DBC_PATH
    with open(dbc_path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()

    by_name: Dict[str, DbcSignal] = {}
    by_id: Dict[int, DbcSignal] = {}
    nodes: Tuple[str, ...] = ()
    cur_name: Optional[str] = None
    cur_id: Optional[int] = None
    cur_tx: str = _NO_TRANSMITTER

    for line in lines:
        node_match = _RE_NODES.match(line)
        if node_match:
            nodes = tuple(node_match.group(1).split())
            continue
        msg = _RE_MESSAGE.match(line)
        if msg:
            cur_id, cur_name, cur_tx = int(msg.group(1)), msg.group(2), msg.group(4)
            continue
        sig = _RE_SIGNAL.match(line)
        if sig and cur_name is not None and cur_id is not None:
            record = DbcSignal(sig.group(1), cur_name, cur_id, cur_tx)
            by_name[record.signal_name] = record
            # First signal of a message establishes the message-level entry; every
            # signal in a message shares its transmitter, so later ones add nothing.
            by_id.setdefault(record.message_id, record)

    return DbcIndex(by_name, by_id, nodes)


def _coerce_can_id(raw: object) -> Optional[int]:
    """Parse a catalog `can_id` ("0x120" or "288") into an int, or None."""
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    try:
        return int(text, 16) if text.lower().startswith("0x") else int(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

class Attribution(NamedTuple):
    """The attribution of one catalog signal to one model ECU."""
    signal_name: str
    model_ecu: Optional[str]     # None == unattributed; never guessed
    provenance: str
    dbc_node: Optional[str]      # the DBC node, when a DBC tier produced this
    normalised_group: str

    @property
    def is_authoritative(self) -> bool:
        return self.provenance == PROV_DBC_NODE


def attribute_signal(record: Dict[str, object], dbc: DbcIndex) -> Attribution:
    """Attribute a single catalog record to a model ECU, recording provenance.

    Tier order is fixed: DBC transmitting node, then DBC message-name prefix, then
    normalised `signal_group`. Returns provenance `unattributed` with `model_ecu=None`
    when no tier resolves — the caller decides what to do, and nothing is invented.
    """
    signal_name = str(record.get("signal_name") or "")
    group = normalise_signal_group(record.get("signal_group"))  # type: ignore[arg-type]

    hit = dbc.by_signal_name.get(signal_name)
    if hit is None:
        can_id = _coerce_can_id(record.get("can_id"))
        if can_id is not None:
            hit = dbc.by_message_id.get(can_id)

    if hit is not None and hit.has_named_transmitter:
        mapped = DBC_NODE_TO_MODEL_ECU.get(hit.transmitter)
        if mapped:
            return Attribution(
                signal_name, mapped, PROV_DBC_NODE, hit.transmitter, group
            )

    if hit is not None:
        # Tier 2: no transmitting node, so fall back to the message-name prefix. The
        # prefix set mixes real ECU names with functional domains, which is why this is
        # a hint tier — resolve it through the same two tables rather than inventing a
        # third, and only accept a hit.
        prefix = hit.message_prefix
        mapped = DBC_NODE_TO_MODEL_ECU.get(prefix) or SIGNAL_GROUP_TO_MODEL_ECU.get(
            normalise_signal_group(prefix)
        )
        if mapped:
            return Attribution(signal_name, mapped, PROV_DBC_PREFIX, prefix, group)

    mapped = SIGNAL_GROUP_TO_MODEL_ECU.get(group)
    if mapped:
        return Attribution(signal_name, mapped, PROV_SIGNAL_GROUP, None, group)

    return Attribution(signal_name, None, PROV_UNATTRIBUTED, None, group)


def attribute_catalog(
    records: Iterable[Dict[str, object]], dbc: Optional[DbcIndex] = None
) -> List[Attribution]:
    """Attribute every catalog record. Order is preserved."""
    index = dbc or parse_dbc()
    return [attribute_signal(r, index) for r in records]


# ---------------------------------------------------------------------------
# Derived counts — the point of the exercise (DX26)
# ---------------------------------------------------------------------------

class DerivedCounts(NamedTuple):
    """Signal counts derived from a catalog query. Never hand-written."""
    total_catalog_signals: int
    per_ecu: Dict[str, int]          # model ECU → count, for the model's own ECUs
    model_signal_count: int          # sum of per_ecu — the model's attributable total
    unattributed: int                # in no model subset; surfaced, never hidden
    attributed_to_absent_ecu: Dict[str, int]  # ECU not on this model → count

    @property
    def coverage_pct(self) -> float:
        if not self.total_catalog_signals:
            return 0.0
        return self.model_signal_count / self.total_catalog_signals * 100


def derive_counts(
    attributions: Iterable[Attribution], model_ecus: Iterable[str]
) -> DerivedCounts:
    """Derive a model's signal counts from attributions and the model's ECU list.

    `per_ecu` covers exactly the model's declared ECUs — an ECU the model carries but
    which owns no signals is present with 0, because a missing key and a zero count mean
    different things and DX28 needs to tell them apart.

    `attributed_to_absent_ecu` is what DX28 asserts empty *for the model's subset*:
    signals whose ECU the model does not carry are not that model's signals. On a
    universal baseline manifest that dict is the interesting one; on a per-powertrain
    manifest it is expected to be non-empty and is the caller's business, not an error.
    """
    ecus = list(model_ecus)
    per_ecu: Dict[str, int] = {name: 0 for name in ecus}
    absent: Dict[str, int] = {}
    unattributed = 0
    total = 0

    for attribution in attributions:
        total += 1
        target = attribution.model_ecu
        if target is None:
            unattributed += 1
        elif target in per_ecu:
            per_ecu[target] += 1
        else:
            absent[target] = absent.get(target, 0) + 1

    return DerivedCounts(
        total_catalog_signals=total,
        per_ecu=per_ecu,
        model_signal_count=sum(per_ecu.values()),
        unattributed=unattributed,
        attributed_to_absent_ecu=absent,
    )


def provenance_breakdown(attributions: Iterable[Attribution]) -> Dict[str, int]:
    """Count attributions by provenance tier. Use this before quoting a count."""
    out: Dict[str, int] = {
        PROV_DBC_NODE: 0,
        PROV_DBC_PREFIX: 0,
        PROV_SIGNAL_GROUP: 0,
        PROV_UNATTRIBUTED: 0,
    }
    for attribution in attributions:
        out[attribution.provenance] = out.get(attribution.provenance, 0) + 1
    return out
