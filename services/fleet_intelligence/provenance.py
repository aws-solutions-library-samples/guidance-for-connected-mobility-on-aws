"""
Provenance contract for Fleet Intelligence.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § D1

Every record whose values did not come from a real vehicle, a real transaction or a real
external system carries a `provenance` field drawn from this vocabulary:

    "simulated"  — produced by a seed script or the simulator
    "measured"   — came off a vehicle or an external system of record
    "derived"    — computed from other records; inherits the *weakest* provenance of its inputs
    "reference"  — catalog or standards data (DTC definitions, NHTSA recalls)

Weakest-input inheritance rule (§ D1):
    A CPM figure derived from simulated cost rows is "simulated", not "derived".
    Weakest-input inheritance is the rule that makes the label honest rather than laundering.

Weakness order (weakest first — lower rank number is weaker):
    simulated (0) > reference (1) > derived (2) > measured (3)

There is **no default**.  A record without provenance raises; it does not become "measured".
Defaulting is how an unlabelled value reaches a screen.
"""

from __future__ import annotations

from typing import Sequence

# The four valid provenance values.  This set is the single source of truth;
# _WEAKNESS_RANK is derived from it.
VALID_PROVENANCE_VALUES: frozenset[str] = frozenset(
    {"simulated", "measured", "derived", "reference"}
)

# Weakness rank: smaller number = weaker (dominates in weakest_provenance).
# Ordering from § D1: simulated > reference > derived > measured
# (weakest first means simulated poisons everything)
_WEAKNESS_RANK: dict[str, int] = {
    "simulated": 0,
    "reference": 1,
    "derived": 2,
    "measured": 3,
}

assert set(_WEAKNESS_RANK.keys()) == set(VALID_PROVENANCE_VALUES), (
    "_WEAKNESS_RANK must cover exactly VALID_PROVENANCE_VALUES"
)


def validate_provenance(value: object) -> str:
    """Validate that *value* is one of the four allowed provenance strings.

    Returns the value unchanged if valid.
    Raises ``ValueError`` for any invalid input (including ``None`` and empty string).

    There is no default — callers must always supply an explicit provenance.
    """
    if not isinstance(value, str):
        raise ValueError(
            f"Provenance must be a string, got {type(value).__name__!r}: {value!r}"
        )
    if value not in VALID_PROVENANCE_VALUES:
        raise ValueError(
            f"Unknown provenance value {value!r}. "
            f"Valid values: {sorted(VALID_PROVENANCE_VALUES)}"
        )
    return value


def weakest_provenance(provenances: Sequence[object]) -> str:
    """Return the weakest provenance value among *provenances*.

    "Weakest" is defined by § D1 weakness order (weakest first):
        simulated (0) > reference (1) > derived (2) > measured (3)

    Rules:
    - Every element must be a valid provenance string; any unknown or ``None`` raises.
    - An empty sequence raises ``ValueError`` (no inputs → no defined result).
    - The return value is the element with the lowest weakness rank (most simulated wins).

    There is no default: if any element is missing or unknown the call fails loudly.
    Defaulting to "measured" is exactly the bug this function is designed to prevent.
    """
    if len(provenances) == 0:  # type: ignore[arg-type]
        raise ValueError(
            "weakest_provenance requires at least one input; got an empty sequence."
        )

    weakest: str | None = None
    weakest_rank: int = len(_WEAKNESS_RANK)  # higher than any valid rank

    for prov in provenances:
        # validate_provenance raises for None, unknown values, non-strings — no default
        valid = validate_provenance(prov)
        rank = _WEAKNESS_RANK[valid]
        if rank < weakest_rank:
            weakest_rank = rank
            weakest = valid

    # weakest is always set here because we validated len(provenances) >= 1
    assert weakest is not None
    return weakest
