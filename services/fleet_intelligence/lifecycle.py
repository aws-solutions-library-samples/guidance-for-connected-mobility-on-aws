"""
Lifecycle sell-timing analysis — deterministic arithmetic for Fleet Intelligence.

Spec: .kiro/specs/2026-09-02-cms-fleet-intelligence-v1/spec.md § D3, § D1
Tier: deterministic arithmetic only — no LLM SDK of any kind in any path (§ D3).

`analyze_sell_timing` fits a straight line to a vehicle's monthly maintenance-cost
history and projects the month at which projected maintenance crosses the vehicle's
monthly straight-line depreciation. It returns the crossover, the fitted parameters,
the fit's R² (confidence is the fit quality, never a self-reported number), the
depreciation inputs, and the raw input series — so the answer is auditable.

It does NOT phrase a recommendation ("sell now", "hold"). It returns the crossover and
lets the caller decide (§ D3): the judgment is the agent's; the arithmetic is here.
"""
from __future__ import annotations

from typing import Any, Mapping, Sequence

try:  # repo-root import (tests); flat import inside the Lambda asset
    from services.fleet_intelligence.provenance import weakest_provenance
except ModuleNotFoundError:  # pragma: no cover - Lambda runtime path
    from provenance import weakest_provenance

# Straight-line depreciation life. A 10-year / 120-month default; the vehicle's
# purchase price divided by this gives monthly depreciation. Deterministic and
# stated in the result so the caller can see the assumption.
STRAIGHT_LINE_LIFE_MONTHS = 120

# Minimum series length at which an r_squared is worth reporting as a fit
# quality. A straight line fits any 2 points exactly, so at n <= 2 r_squared is
# 1.0 by construction regardless of the data — "perfect fit" at the point of
# least evidence. 3 is the smallest n at which the value can distinguish a real
# linear trend from an artefact of the geometry.
_MIN_SERIES_FOR_FIT = 3


def _linear_fit(ys: Sequence[float]) -> tuple[float, float, float]:
    """Ordinary least-squares fit of ys against x = 0, 1, ..., n-1.

    Returns (slope, intercept, r_squared). r_squared is clamped to [0, 1].
    For a constant series (zero variance in y) the fit is a flat line and
    r_squared is defined as 1.0 (the line explains all of the zero variance).
    """
    n = len(ys)
    if n == 0:
        raise ValueError("Cannot fit an empty maintenance series.")
    if n == 1:
        return 0.0, float(ys[0]), 1.0

    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n

    ss_xx = sum((x - mean_x) ** 2 for x in xs)
    ss_xy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    ss_yy = sum((y - mean_y) ** 2 for y in ys)

    slope = ss_xy / ss_xx if ss_xx != 0 else 0.0
    intercept = mean_y - slope * mean_x

    if ss_yy == 0:
        r_squared = 1.0
    else:
        ss_res = sum((y - (intercept + slope * x)) ** 2 for x, y in zip(xs, ys))
        r_squared = 1.0 - ss_res / ss_yy

    r_squared = max(0.0, min(1.0, r_squared))
    return slope, intercept, r_squared


def _add_months(year_month: str, months: int) -> str:
    """Add *months* to a 'YYYY-MM' string, returning 'YYYY-MM'."""
    year, month = (int(p) for p in year_month.split("-")[:2])
    zero_based = (year * 12 + (month - 1)) + months
    return f"{zero_based // 12:04d}-{zero_based % 12 + 1:02d}"


def crossover_offset(
    intercept: float,
    slope: float,
    n: int,
    monthly_depreciation: float,
    horizon_months: int,
) -> int | None:
    """Months after the series end at which the fitted line reaches depreciation.

    The smallest k in 1..horizon_months with
    ``intercept + slope * ((n - 1) + k) >= monthly_depreciation``, or None.
    The ADP rollup SQL (``adp_rollup.PER_VIN_K_CASE``) computes the same k in
    closed form; its live parity test calls this function as the reference.
    """
    for k in range(1, horizon_months + 1):
        projected_index = (n - 1) + k
        projected_cost = intercept + slope * projected_index
        if projected_cost >= monthly_depreciation:
            return k
    return None


def analyze_sell_timing(
    vehicle_params: Mapping[str, Any],
    maintenance_series: Sequence[Mapping[str, Any]],
    horizon_months: int,
) -> dict[str, Any]:
    """Project the sell-timing crossover for one vehicle.

    Fits maintenance cost over the observed months, then walks forward month by
    month (1..horizon_months from the series end) until projected maintenance
    reaches monthly depreciation. Returns the crossover month or ``None`` when no
    crossover falls inside the horizon — with the horizon always stated (§ D3).

    Provenance is the weakest of the vehicle params and every series point (§ D1):
    with simulated inputs the answer is "simulated", never "derived".
    """
    if len(maintenance_series) == 0:
        raise ValueError("maintenance_series must contain at least one month.")

    ys = [float(p["maintenanceCost"]) for p in maintenance_series]
    slope, intercept, r_squared = _linear_fit(ys)

    purchase_price = float(vehicle_params["purchasePrice"])
    monthly_depreciation = purchase_price / STRAIGHT_LINE_LIFE_MONTHS

    n = len(maintenance_series)
    series_end_month = maintenance_series[-1]["yearMonth"]

    k = crossover_offset(intercept, slope, n, monthly_depreciation, horizon_months)
    crossover_month = _add_months(series_end_month, k) if k is not None else None

    provenances = [vehicle_params.get("provenance")] + [
        p.get("provenance") for p in maintenance_series
    ]

    return {
        "crossover_month": crossover_month,
        "horizon_months": horizon_months,
        "maintenance_fit": {"slope": slope, "intercept": intercept},
        "r_squared": r_squared,
        # Whether r_squared means anything at this series length.
        #
        # A straight line fits any 2 points exactly, so n <= 2 yields
        # r_squared == 1.0 unconditionally — it reports "perfect fit" precisely
        # when there is least evidence. Observed live 2026-09-13 on
        # VEH-DEMO-PUB-002: slope 0.0, r_squared 1.0, input_series of 2. Reading
        # that as high confidence inverts its meaning.
        #
        # r_squared is left as-is rather than nulled: callers that plot the fit
        # still need it, and silently changing a numeric field's type is worse
        # than qualifying it. This flag is the qualifier, and the UI should
        # suppress or caveat any confidence presentation when it is False.
        "r_squared_meaningful": n >= _MIN_SERIES_FOR_FIT,
        "series_length": n,
        "depreciation_params": {
            "purchase_price": purchase_price,
            "monthly_depreciation": monthly_depreciation,
            "life_months": STRAIGHT_LINE_LIFE_MONTHS,
        },
        "input_series": list(maintenance_series),
        "provenance": weakest_provenance(provenances),
    }



# ---------------------------------------------------------------------------
# Fleet-level lifecycle aggregation (D1 / D3 / D9)
# ---------------------------------------------------------------------------

# Threshold constants for binning vehicles by months-until-crossover.
# Mutation-testable: tests import and assert these exact values.
_SELL_RECOMMENDED_MAX_MONTHS: int = 6   # crossover ≤ 6 months → sell recommended
_SELL_SOON_MAX_MONTHS: int = 12         # crossover 7–12 months → sell soon


def _derive_tire_status(tire_snapshot: dict | None) -> tuple[str, list | None]:
    """Compute tireStatus (4-value) and tirePositions from a tire snapshot.

    Returns (tireStatus, tirePositions).  When no snapshot is supplied
    returns ('unknown', None).
    """
    if not tire_snapshot:
        return "unknown", None

    positions = tire_snapshot.get("positions") or []
    if not positions:
        return "unknown", None

    # Build the canonical tirePositions list (T1.1 shape)
    tire_positions = [
        {
            "position": p.get("position"),
            "treadDepthMm": p.get("treadDepthMm"),
            "wearCategory": p.get("wearCategory"),
            "needsReplacement": p.get("needsReplacement", False),
        }
        for p in positions
    ]

    # Worst-state derivation per spec D3
    # Priority: needs_replacement/replace > monitor > ok
    for p in positions:
        if p.get("needsReplacement", False) or p.get("wearCategory") == "replace":
            return "needs_replacement", tire_positions

    for p in positions:
        if p.get("wearCategory") == "monitor":
            return "monitor", tire_positions

    return "healthy", tire_positions


def analyze_fleet_lifecycle(
    cost_rows: list[dict],
    vehicles_by_id: dict[str, dict],
    tire_health_by_vehicle_id: dict[str, dict],
    horizon_months: int = 36,
) -> dict:
    """Aggregate per-vehicle sell-timing analysis into a fleet-level summary.

    Composition function: groups *cost_rows* by vehicleId, sorts each group
    by yearMonth, calls ``analyze_sell_timing()`` once per vehicle, and
    constructs the D3-shape response (summary, fleetMonthlyTrend, rows,
    provenance).

    Parameters
    ----------
    cost_rows:
        List of cost records in the shape ``fetch_cost_rows()`` returns.
        Each record has at least ``vehicleId``, ``yearMonth``,
        ``maintenanceCost``, and ``provenance``.
    vehicles_by_id:
        Dict keyed by vehicleId containing DDB vehicle records.  Used as the
        authoritative source for make/model/year/purchasePrice/vin.
    tire_health_by_vehicle_id:
        Dict keyed by vehicleId containing the per-vehicle latest tire
        snapshot from ``fetch_tire_health()``.  Missing key → tireStatus
        'unknown', not an error.
    horizon_months:
        Maximum months ahead to project the crossover.

    Returns a dict matching spec D3 / D9.
    """
    # --- Group cost rows by vehicleId -----------------------------------
    series_by_vehicle: dict[str, list[dict]] = {}
    for row in cost_rows:
        vid = row["vehicleId"]
        series_by_vehicle.setdefault(vid, []).append(row)

    # Sort each vehicle's series by yearMonth (ascending)
    for vid in series_by_vehicle:
        series_by_vehicle[vid].sort(key=lambda r: r["yearMonth"])

    # --- Per-vehicle analysis -------------------------------------------
    output_rows: list[dict] = []
    all_provenances: list[str] = []

    for vid, series in series_by_vehicle.items():
        vehicle_params = vehicles_by_id.get(vid) or {}

        # analyze_sell_timing requires purchasePrice; if DDB record is
        # missing, fall back to cost-row shape.  In practice the handler
        # will always pass the DDB dict.
        effective_params = {
            "purchasePrice": vehicle_params.get("purchasePrice", 60000.0),
            "provenance": vehicle_params.get("provenance", "simulated"),
        }

        timing = analyze_sell_timing(effective_params, series, horizon_months)

        # monthsUntilCrossover: integer offset from series end to crossover
        months_until: int | None = None
        if timing["crossover_month"] is not None:
            n = len(series)
            series_end = series[-1]["yearMonth"]
            # Walk forward month-by-month from series end until we hit the crossover
            k = 1
            while k <= horizon_months:
                if _add_months(series_end, k) == timing["crossover_month"]:
                    months_until = k
                    break
                k += 1

        # currentMonthlyMaintenance: last observed value in the series
        current_maint = float(series[-1]["maintenanceCost"]) if series else None

        # Tire health
        tire_snapshot = tire_health_by_vehicle_id.get(vid)
        tire_status, tire_positions = _derive_tire_status(tire_snapshot)

        # Provenance for this row (weakest across all inputs)
        row_provenances = [effective_params["provenance"]] + [
            r.get("provenance", "simulated") for r in series
        ]
        if tire_snapshot and tire_snapshot.get("provenance"):
            row_provenances.append(tire_snapshot["provenance"])

        row_provenance = weakest_provenance(row_provenances)
        all_provenances.append(row_provenance)

        output_rows.append({
            "vehicleId": vid,
            "vin": vehicle_params.get("vin"),
            "fleetId": vehicle_params.get("fleetId") or (series[0].get("fleetId") if series else None),
            "make": vehicle_params.get("make"),
            "model": vehicle_params.get("model"),
            "year": vehicle_params.get("year"),
            "purchasePrice": vehicle_params.get("purchasePrice"),
            "seriesLengthMonths": timing["series_length"],
            "crossoverMonth": timing["crossover_month"],
            "monthsUntilCrossover": months_until,
            "rSquared": timing["r_squared"],
            "rSquaredMeaningful": timing["r_squared_meaningful"],
            "currentMonthlyMaintenance": current_maint,
            "monthlyDepreciation": timing["depreciation_params"]["monthly_depreciation"],
            "tireStatus": tire_status,
            "tirePositions": tire_positions,
            "provenance": row_provenance,
        })

    # --- Summary counts -------------------------------------------------
    total_vehicles = len(output_rows)
    sell_recommended = 0
    sell_soon = 0
    healthy = 0
    insufficient_data = 0
    tires_need_replacement = 0
    crossover_months_list: list[int] = []

    for row in output_rows:
        # Tire KPI count
        if row["tireStatus"] == "needs_replacement":
            tires_need_replacement += 1

        # Sell-timing bucket — insufficientData excluded from sell counts
        if not row["rSquaredMeaningful"]:
            insufficient_data += 1
        elif row["monthsUntilCrossover"] is not None:
            m = row["monthsUntilCrossover"]
            if m <= _SELL_RECOMMENDED_MAX_MONTHS:
                sell_recommended += 1
            elif m <= _SELL_SOON_MAX_MONTHS:
                sell_soon += 1
            else:
                healthy += 1
            crossover_months_list.append(m)
        else:
            # crossoverMonth is None (no crossover in horizon) → healthy
            healthy += 1

    avg_months_to_crossover: float | None = (
        sum(crossover_months_list) / len(crossover_months_list)
        if crossover_months_list
        else None
    )

    # avgMonthlyDepreciation: fleet mean across ALL rows (D9 invariant #4)
    avg_monthly_depreciation: float | None = None
    if output_rows:
        depreciation_values = [
            r["monthlyDepreciation"]
            for r in output_rows
            if r["monthlyDepreciation"] is not None
        ]
        if depreciation_values:
            avg_monthly_depreciation = sum(depreciation_values) / len(depreciation_values)

    # --- Fleet monthly trend (D9) ---------------------------------------
    # Group cost_rows by yearMonth; average maintenanceCost and count distinct vehicleIds.
    trend_buckets: dict[str, dict] = {}
    for row in cost_rows:
        ym = row["yearMonth"]
        if ym not in trend_buckets:
            trend_buckets[ym] = {"total": 0.0, "vehicle_ids": set()}
        trend_buckets[ym]["total"] += float(row["maintenanceCost"])
        trend_buckets[ym]["vehicle_ids"].add(row["vehicleId"])

    # Only emit months with at least one reporter (D9 invariant #3)
    fleet_monthly_trend: list[dict] = []
    for ym in sorted(trend_buckets):
        bucket = trend_buckets[ym]
        count = len(bucket["vehicle_ids"])
        if count >= 1:
            fleet_monthly_trend.append({
                "yearMonth": ym,
                "avgMaintenance": bucket["total"] / count,
                "vehicleCount": count,
            })

    # --- Top-level provenance -------------------------------------------
    # Weakest across all row provenances; if no rows, default to 'simulated'
    # (no real data was processed — the label is honest)
    if all_provenances:
        top_provenance = weakest_provenance(all_provenances)
    else:
        top_provenance = "simulated"

    return {
        "summary": {
            "totalVehicles": total_vehicles,
            "sellRecommendedCount": sell_recommended,
            "sellSoonCount": sell_soon,
            "healthyCount": healthy,
            "insufficientDataCount": insufficient_data,
            "tiresNeedReplacementCount": tires_need_replacement,
            "avgMonthsToCrossover": avg_months_to_crossover,
            "avgMonthlyDepreciation": avg_monthly_depreciation,
            "horizonMonths": horizon_months,
        },
        "fleetMonthlyTrend": fleet_monthly_trend,
        "rows": output_rows,
        "provenance": top_provenance,
    }
