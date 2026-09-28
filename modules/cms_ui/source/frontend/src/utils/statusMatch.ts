// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Case-insensitive status helpers.
 *
 * Backend records in this codebase store `status` with inconsistent casing:
 *   - fleets, vehicles, subscriptions, drivers (seed) → lowercase (`"active"`)
 *   - staging drivers (mixed history)                 → uppercase (`"ACTIVE"`)
 *   - trips (Flink-owned)                             → uppercase (`"ACTIVE"` / `"COMPLETED"`)
 *   - DTCs, certificates, IoT FleetWise resources     → uppercase (`"ACTIVE"`)
 *
 * Comparing with a hardcoded literal has historically rendered the wrong
 * StatusIndicator icon (warning triangle, red ✗, grey stopped) on healthy
 * records. See `issues/2026-09-25-status-casing-icons/`.
 *
 * Prefer these helpers over ad-hoc `status === 'ACTIVE'` compares at any site
 * that reads a `status` field whose backend casing is not guaranteed.
 */

/**
 * Case-insensitive equality on two possibly-undefined status strings.
 *
 * Returns false if either side is non-string (null, undefined, number, …) so
 * callers do not need to null-check first.
 */
export function statusEquals(
  status: string | null | undefined,
  target: string | null | undefined,
): boolean {
  if (typeof status !== 'string') return false;
  if (typeof target !== 'string') return false;
  return status.toLowerCase() === target.toLowerCase();
}

/**
 * Returns true iff `status` matches `"active"` case-insensitively.
 *
 * Does NOT accept `"in_progress"` or other synonyms — call sites that need a
 * broader definition of "in flight" should compose those separately (see
 * `TripsTable.tsx`, which accepts `in_progress` as a distinct trip state).
 */
export function isStatusActive(status: string | null | undefined): boolean {
  return statusEquals(status, 'active');
}
