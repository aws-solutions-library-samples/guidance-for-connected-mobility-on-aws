// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Shared types for the PM (Preventive Maintenance) feature.
 * These match the fleet-intelligence handler's API contracts (spec § D2, § D6).
 */

import type { ProvenanceValue } from "../provenance";

// ---------------------------------------------------------------------------
// Schedule basis — the three trigger types from spec § D6
// ---------------------------------------------------------------------------

export type PmBasis = "mileage" | "engine_hours" | "calendar";

export const PM_BASIS_LABELS: Record<PmBasis, string> = {
  mileage: "Mileage",
  engine_hours: "Engine Hours",
  calendar: "Calendar",
};

// ---------------------------------------------------------------------------
// PM Schedule — single schedule record returned by GET /pm/schedules
// ---------------------------------------------------------------------------

export interface PmSchedule {
  scheduleId: string;
  vehicleId: string;
  fleetId?: string;
  /** Task description / VMRS-shaped code in synthetic namespace */
  taskCode: string;
  taskDescription?: string;
  basis: PmBasis;
  /** Interval value in the basis unit (miles, hours, or days) */
  intervalValue: number;
  /** Projected value at which the next PM is due (basis unit) */
  nextDueValue: number;
  /** Projected calendar date for the next PM (ISO date string) */
  nextDueDate: string;
  /** Most-recent completion reading — odometer, engine-hours, or calendar date */
  lastPerformedAt?: string;
  lastPerformedMileage?: number;
  lastPerformedEngineHours?: number;
  /** Current vehicle reading — used to compute overdue status */
  currentMileage?: number;
  currentEngineHours?: number;
  /** True if the current reading has passed nextDueValue (or date is in the past) */
  overdue?: boolean;
  /** Days until due (negative = overdue days ago) */
  daysUntilDue?: number;
  provenance: ProvenanceValue;
}

// ---------------------------------------------------------------------------
// Compliance rollup — returned by GET /pm/compliance
// ---------------------------------------------------------------------------

export interface PmComplianceRollup {
  fleetId?: string;
  windowStart: string;
  windowEnd: string;
  totalScheduled: number;
  completedOnTime: number;
  /** Percentage of scheduled PMs completed before due point (0–100) */
  complianceRate: number;
  overdueCount: number;
  provenance: ProvenanceValue;
}

/**
 * The compliance API response, exactly as
 * `services/fleet_intelligence/pm.py::compute_compliance` returns it — pinned by
 * `services/fleet_intelligence/tests/test_pm.py` (§ D6). Note `rate` is a 0–1
 * fraction, while the view model above uses 0–100 percent.
 * See issues/2026-09-03-fleet-intelligence-ui-api-contract-mismatch/.
 */
export interface PmComplianceApiResponse {
  fleetId?: string;
  window: { start: string; end: string };
  total: number;
  compliant: number;
  non_compliant: number;
  /** Fraction in [0, 1] */
  rate: number;
  provenance: ProvenanceValue;
}

/** Map the API response onto the view model. The single conversion point. */
export function toPmComplianceRollup(
  r: PmComplianceApiResponse
): PmComplianceRollup {
  return {
    fleetId: r.fleetId,
    windowStart: r.window?.start ?? "",
    windowEnd: r.window?.end ?? "",
    totalScheduled: r.total ?? 0,
    completedOnTime: r.compliant ?? 0,
    complianceRate: (r.rate ?? 0) * 100,
    overdueCount: r.non_compliant ?? 0,
    provenance: r.provenance,
  };
}

// ---------------------------------------------------------------------------
// Create/update payload — POST /pm/schedules
// ---------------------------------------------------------------------------

export interface PmSchedulePayload {
  vehicleId: string;
  taskCode: string;
  taskDescription?: string;
  basis: PmBasis;
  intervalValue: number;
  lastPerformedAt?: string;
  lastPerformedMileage?: number;
  lastPerformedEngineHours?: number;
}

// ---------------------------------------------------------------------------
// Schedule status derived from API fields
// ---------------------------------------------------------------------------

export type PmScheduleStatus = "overdue" | "due-soon" | "upcoming";

/** Classify schedule status for display */
export function getPmStatus(schedule: PmSchedule): PmScheduleStatus {
  if (schedule.overdue) return "overdue";
  // Due-soon = within 7 days or within 10% of interval
  if (schedule.daysUntilDue !== undefined && schedule.daysUntilDue <= 7)
    return "due-soon";
  return "upcoming";
}
