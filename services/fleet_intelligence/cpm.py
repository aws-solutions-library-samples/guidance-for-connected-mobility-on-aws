"""
CPM (Cost Per Mile) — deterministic arithmetic for Fleet Intelligence.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § D2, § D1
Tier: deterministic arithmetic only — no LLM in any path (§ Tier classification).

CPM is the PRD's payoff metric: cost per mile, groupable by OEM, fleet or vehicle.
Every response carries a `provenance` computed by weakest-input inheritance (§ D1):
a CPM figure derived from simulated cost rows is "simulated", not "derived".

This module operates on plain cost rows passed in by the handler (T3.5). It performs
no I/O and imports no AWS SDK — the DynamoDB read (via the fleetId-yearMonth-index GSI,
not a scan) lives in index.py so that this arithmetic stays unit-testable and pure.

A cost row is a dict with at least:
    vehicleId, yearMonth, maintenanceCost, fuelCost, totalMiles, provenance
and, for OEM grouping, `make`; for fleet grouping, `fleetId`.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

try:  # repo-root import (tests); flat import inside the Lambda asset
    from services.fleet_intelligence.provenance import weakest_provenance
except ModuleNotFoundError:  # pragma: no cover - Lambda runtime path
    from provenance import weakest_provenance


class InvalidGroupByError(ValueError):
    """Raised when the caller-supplied ``groupBy`` value is not in the supported set.

    Distinct from the generic ``ValueError`` this subclass extends: this signals
    a *client input* error (400 territory) as opposed to an *internal shape*
    error (500 territory).  The handler catches this narrowly so ADP-side
    env / bind-count / result-ceiling ValueErrors do NOT leak through the
    ``400 {"error": str(exc)}`` path — see security-review-g2-cycle1 W2.
    """


# groupBy value → the row key that identifies the group.
_GROUP_KEY: dict[str, str] = {
    "oem": "make",
    "fleet": "fleetId",
    "vehicle": "vehicleId",
}


def compute_cpm(total_cost: float, total_miles: float) -> float:
    """Cost per mile = total_cost / total_miles.

    Returns 0.0 when total_miles is zero rather than dividing by zero (§ D2:
    "CPM over an empty month returns zero"). A month with no miles has no
    cost-per-mile to report; zero is the honest, non-erroring answer.
    """
    if total_miles == 0:
        return 0.0
    return total_cost / total_miles


def _row_cost(row: Mapping[str, Any]) -> float:
    return float(row.get("maintenanceCost", 0.0)) + float(row.get("fuelCost", 0.0))


def group_cpm_by(
    rows: Sequence[Mapping[str, Any]],
    group_by: str,
) -> dict[str, Any]:
    """Aggregate CPM across *rows*, grouped by "oem" | "fleet" | "vehicle".

    Returns::

        {
            "groupBy": <group_by>,
            "data": [{"group": <name>, "cpm": <float>,
                      "totalCost": <float>, "totalMiles": <float>}, ...],
            "provenance": <weakest provenance across all rows>,
        }

    CPM per group = sum(maintenanceCost + fuelCost) / sum(totalMiles), with the
    zero-miles guard from ``compute_cpm``. Provenance is the weakest of every input
    row's provenance (§ D1) — one simulated row makes the whole figure simulated.

    Raises ``ValueError`` for an unsupported ``group_by`` and for a row missing its
    grouping key; a silently-dropped row is a wrong CPM.
    """
    if group_by not in _GROUP_KEY:
        raise InvalidGroupByError(
            f"Unsupported group_by {group_by!r}; valid: {sorted(_GROUP_KEY)}"
        )
    key = _GROUP_KEY[group_by]

    buckets: dict[str, dict[str, float]] = {}
    vehicles_per_group: dict[str, set[str]] = {}
    provenances: list[Any] = []
    for row in rows:
        if key not in row:
            # Name the MISSING KEY and the row's identifier only — never the row.
            #
            # This used to interpolate `row.get('vehicleId', row)`, whose fallback
            # is the ENTIRE ADP row: every cost, mileage and identifier field for
            # one vehicle, embedded in an exception message that the 500 handler
            # logs to CloudWatch. Log read permission is much broader than this
            # API's Cognito-group authz, so that is a cross-boundary leak of
            # customer data. Latent while ADP_DATA_PROVENANCE=simulated; live as
            # soon as a stage reads measured data.
            #
            # A vehicleId alone is sufficient to locate the offending row, and
            # `sorted(row)` gives the shape without any values.
            # Security review W-SEC-1, Group 5 cycle 1.
            raise ValueError(
                f"Row for vehicleId={row.get('vehicleId', '<absent>')!r} is missing "
                f"grouping key {key!r} required for group_by={group_by!r}; "
                f"row carried fields {sorted(row)!r}"
            )
        group = row[key]
        bucket = buckets.setdefault(group, {"totalCost": 0.0, "totalMiles": 0.0})
        bucket["totalCost"] += _row_cost(row)
        bucket["totalMiles"] += float(row.get("totalMiles", 0.0))
        # Distinct contributing vehicles, so the caller can show "N vehicles"
        # without inventing the number. For group_by="vehicle" this is always 1.
        vehicles_per_group.setdefault(group, set()).add(row.get("vehicleId", ""))
        # provenance is mandatory on every row — weakest_provenance raises if missing/unknown
        provenances.append(row.get("provenance"))

    data = [
        {
            "group": group,
            "cpm": compute_cpm(b["totalCost"], b["totalMiles"]),
            "totalCost": b["totalCost"],
            "totalMiles": b["totalMiles"],
            "vehicleCount": len(vehicles_per_group[group]),
        }
        for group, b in buckets.items()
    ]

    return {
        "groupBy": group_by,
        "data": data,
        # Empty-input guard: weakest_provenance raises ValueError on an empty
        # sequence (§ provenance.py:78 — "no inputs → no defined result"). That
        # is correct behaviour for the general helper, but at this callsite the
        # empty case is a real production shape: a fleet with no cost rows in
        # the queried window (fresh stage, empty vehicle-costs table). Bubbling
        # the ValueError to API GW produces the "HTTP 400: weakest_provenance
        # requires at least one input" the operator saw during UAT. The honest
        # response is data=[] + provenance="unknown", so the client can render
        # "no data" without inventing a provenance level.
        # See issues/2026-09-10-cpm-empty-cost-table-returns-400/.
        "provenance": weakest_provenance(provenances) if provenances else "unknown",
    }
