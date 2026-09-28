// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Per-record status-render helpers.
 *
 * Colocated here (rather than inline in each component's `.tsx`) so the
 * unit tests do not need to load the full component tree — several of the
 * components that call these helpers transitively import maplibre-gl, which
 * does not initialise cleanly under jsdom.
 *
 * See `issues/2026-09-25-status-casing-icons/` and the per-component render
 * tests under `__tests__/`. All comparisons flow through the case-insensitive
 * primitives in `./statusMatch` so that backend records stored with either
 * `active` or `ACTIVE` casing render as the same green success indicator.
 */

import { statusEquals, isStatusActive } from './statusMatch';

// ── Fleet detail page ─────────────────────────────────────────────────────────

export function fleetHeaderStatusIndicator(
  status: string | null | undefined,
): { type: 'success' | 'warning'; label: string } {
  return {
    type: isStatusActive(status) ? 'success' : 'warning',
    label: status || 'ACTIVE',
  };
}

export function fleetPickerVehicleStatusIndicator(
  status: string | null | undefined,
): { type: 'success' | 'stopped'; label: string } {
  return {
    type: isStatusActive(status) ? 'success' : 'stopped',
    label: status || 'Unknown',
  };
}

// ── Drivers list + form ───────────────────────────────────────────────────────

export type DriverIndicatorType = 'success' | 'in-progress' | 'error' | 'stopped';

export function driverStatusRender(
  status: string | null | undefined,
): { type: DriverIndicatorType; label: string } {
  if (typeof status !== 'string' || status.length === 0) {
    return { type: 'stopped', label: '—' };
  }
  const type: DriverIndicatorType =
    statusEquals(status, 'active') ? 'success'
    : statusEquals(status, 'on_leave') ? 'in-progress'
    : statusEquals(status, 'terminated') ? 'error'
    : 'stopped';
  const label =
    statusEquals(status, 'active') ? 'Active'
    : statusEquals(status, 'on_leave') ? 'On leave'
    : statusEquals(status, 'terminated') ? 'Terminated'
    : status.charAt(0).toUpperCase() + status.slice(1);
  return { type, label };
}

export function driverFormStatusLabel(status: string | null | undefined): string {
  if (typeof status !== 'string') return '';
  if (statusEquals(status, 'active')) return 'Active';
  if (statusEquals(status, 'on_leave')) return 'On leave';
  if (statusEquals(status, 'terminated')) return 'Terminated';
  return status;
}

// ── Driver detail page ────────────────────────────────────────────────────────

export function driverDetailStatusIndicatorType(
  status: string | null | undefined,
): 'success' | 'error' {
  return isStatusActive(status) ? 'success' : 'error';
}

// ── Simulation vehicle picker ─────────────────────────────────────────────────

export function simVehicleStatusIndicatorType(
  status: string | null | undefined,
): 'success' | 'info' {
  return isStatusActive(status) ? 'success' : 'info';
}

// ── Trip detail badge ─────────────────────────────────────────────────────────

export function tripStatusBadge(
  status: string | null | undefined,
): { color: 'green' | 'blue'; label: string } {
  if (statusEquals(status, 'completed')) return { color: 'green', label: 'Completed' };
  if (isStatusActive(status)) return { color: 'blue', label: 'Active' };
  return { color: 'blue', label: typeof status === 'string' ? status : '' };
}
