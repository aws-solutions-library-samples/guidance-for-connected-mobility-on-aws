// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Per-component status-render tests.
 *
 * See `issues/2026-09-25-status-casing-icons/`. Each `describe` block below
 * covers one of the five components fixed by that issue. They are colocated
 * here rather than under each component's own `__tests__/` directory because
 * two of the components (DriverDetailView, TripDetailView) transitively load
 * maplibre-gl at module init, which crashes under jsdom — so the tests have
 * to import from `../statusRender.ts` directly.
 *
 * MUTATION CHECK — reverting `statusMatch.statusEquals` to a case-sensitive
 * `s === t` (i.e. removing the `.toLowerCase()` calls in `../statusMatch.ts`)
 * MUST cause every test named `... "ACTIVE" ...` or `... "Active" ...` or
 * `... "COMPLETED" ...` below to fail. If the tests still pass, the helper is
 * no longer guarding the property this issue was filed for.
 */

import { describe, it, expect } from 'vitest';
import {
  fleetHeaderStatusIndicator,
  fleetPickerVehicleStatusIndicator,
  driverStatusRender,
  driverFormStatusLabel,
  driverDetailStatusIndicatorType,
  simVehicleStatusIndicatorType,
  tripStatusBadge,
} from '../statusRender';

describe('FleetDetailsPage — fleetHeaderStatusIndicator', () => {
  it('returns success for lowercase "active" (the stored casing)', () => {
    expect(fleetHeaderStatusIndicator('active')).toEqual({
      type: 'success',
      label: 'active',
    });
  });

  it('returns success for uppercase "ACTIVE"', () => {
    expect(fleetHeaderStatusIndicator('ACTIVE')).toEqual({
      type: 'success',
      label: 'ACTIVE',
    });
  });

  it('returns success for mixed-case "Active"', () => {
    expect(fleetHeaderStatusIndicator('Active')).toEqual({
      type: 'success',
      label: 'Active',
    });
  });

  it('returns warning for any non-active status', () => {
    expect(fleetHeaderStatusIndicator('inactive').type).toBe('warning');
    expect(fleetHeaderStatusIndicator('archived').type).toBe('warning');
    expect(fleetHeaderStatusIndicator('').type).toBe('warning');
  });

  it('falls back to "ACTIVE" label when status is missing', () => {
    expect(fleetHeaderStatusIndicator(undefined).label).toBe('ACTIVE');
    expect(fleetHeaderStatusIndicator(null).label).toBe('ACTIVE');
  });
});

describe('FleetDetailsPage — fleetPickerVehicleStatusIndicator', () => {
  it('returns success for lowercase "active" (the stored casing)', () => {
    expect(fleetPickerVehicleStatusIndicator('active').type).toBe('success');
  });

  it('returns success for uppercase "ACTIVE" (the fallback casing)', () => {
    expect(fleetPickerVehicleStatusIndicator('ACTIVE').type).toBe('success');
  });

  it('returns stopped for any non-active status', () => {
    expect(fleetPickerVehicleStatusIndicator('offline').type).toBe('stopped');
    expect(fleetPickerVehicleStatusIndicator('decommissioned').type).toBe('stopped');
  });

  it('falls back to "Unknown" label when status is missing', () => {
    expect(fleetPickerVehicleStatusIndicator(undefined).label).toBe('Unknown');
    expect(fleetPickerVehicleStatusIndicator('').label).toBe('Unknown');
  });
});

describe('DriversView — driverStatusRender', () => {
  it('renders the green success indicator for "active" (lowercase seed)', () => {
    expect(driverStatusRender('active')).toEqual({ type: 'success', label: 'Active' });
  });

  it('renders the green success indicator for "ACTIVE" (uppercase, staging)', () => {
    // Reported bug: this used to fall through to the grey stopped indicator.
    expect(driverStatusRender('ACTIVE')).toEqual({ type: 'success', label: 'Active' });
  });

  it('renders the green success indicator for "Active" (mixed case)', () => {
    expect(driverStatusRender('Active')).toEqual({ type: 'success', label: 'Active' });
  });

  it('renders the in-progress indicator for "on_leave" and "ON_LEAVE"', () => {
    expect(driverStatusRender('on_leave')).toEqual({ type: 'in-progress', label: 'On leave' });
    expect(driverStatusRender('ON_LEAVE')).toEqual({ type: 'in-progress', label: 'On leave' });
  });

  it('renders the error indicator for "terminated" and "TERMINATED"', () => {
    expect(driverStatusRender('terminated')).toEqual({ type: 'error', label: 'Terminated' });
    expect(driverStatusRender('TERMINATED')).toEqual({ type: 'error', label: 'Terminated' });
  });

  it('renders an em-dash for missing status', () => {
    expect(driverStatusRender(undefined)).toEqual({ type: 'stopped', label: '—' });
    expect(driverStatusRender(null)).toEqual({ type: 'stopped', label: '—' });
    expect(driverStatusRender('')).toEqual({ type: 'stopped', label: '—' });
  });

  it('renders unknown enum values with the stopped indicator and title-cased label', () => {
    expect(driverStatusRender('suspended')).toEqual({ type: 'stopped', label: 'Suspended' });
  });
});

describe('DriversView — driverFormStatusLabel', () => {
  it('labels every casing of every enum value consistently', () => {
    expect(driverFormStatusLabel('active')).toBe('Active');
    expect(driverFormStatusLabel('ACTIVE')).toBe('Active');
    expect(driverFormStatusLabel('on_leave')).toBe('On leave');
    expect(driverFormStatusLabel('ON_LEAVE')).toBe('On leave');
    expect(driverFormStatusLabel('terminated')).toBe('Terminated');
    expect(driverFormStatusLabel('TERMINATED')).toBe('Terminated');
  });

  it('returns the raw value for an unknown enum entry', () => {
    expect(driverFormStatusLabel('suspended')).toBe('suspended');
  });

  it('returns empty string for non-string input', () => {
    expect(driverFormStatusLabel(undefined)).toBe('');
    expect(driverFormStatusLabel(null)).toBe('');
  });
});

describe('DriverDetailView — driverDetailStatusIndicatorType', () => {
  it('returns success for every casing of "active"', () => {
    expect(driverDetailStatusIndicatorType('active')).toBe('success');
    expect(driverDetailStatusIndicatorType('ACTIVE')).toBe('success');
    expect(driverDetailStatusIndicatorType('Active')).toBe('success');
  });

  it('returns error for terminated', () => {
    expect(driverDetailStatusIndicatorType('terminated')).toBe('error');
    expect(driverDetailStatusIndicatorType('TERMINATED')).toBe('error');
  });

  it('returns error for missing / unknown status', () => {
    expect(driverDetailStatusIndicatorType(undefined)).toBe('error');
    expect(driverDetailStatusIndicatorType(null)).toBe('error');
    expect(driverDetailStatusIndicatorType('')).toBe('error');
    expect(driverDetailStatusIndicatorType('on_leave')).toBe('error');
    // NOTE: `on_leave` currently renders as error on the detail header — the
    // scope of this fix is casing only, not adding the missing `in-progress`
    // branch that the list view has.
  });
});

describe('VehicleSelectionModal — simVehicleStatusIndicatorType', () => {
  it('returns success for lowercase "active" (the stored casing)', () => {
    expect(simVehicleStatusIndicatorType('active')).toBe('success');
  });

  it('returns success for uppercase "ACTIVE" (fallback casing)', () => {
    expect(simVehicleStatusIndicatorType('ACTIVE')).toBe('success');
  });

  it('returns info for idle / offline / unknown', () => {
    expect(simVehicleStatusIndicatorType('IDLE')).toBe('info');
    expect(simVehicleStatusIndicatorType('offline')).toBe('info');
    expect(simVehicleStatusIndicatorType(undefined)).toBe('info');
    expect(simVehicleStatusIndicatorType('')).toBe('info');
  });
});

describe('TripDetailView — tripStatusBadge', () => {
  it('paints a green Completed badge for uppercase "COMPLETED"', () => {
    // The reported bug: this used to render blue with the raw string.
    expect(tripStatusBadge('COMPLETED')).toEqual({ color: 'green', label: 'Completed' });
  });

  it('paints a green Completed badge for lowercase "completed"', () => {
    expect(tripStatusBadge('completed')).toEqual({ color: 'green', label: 'Completed' });
  });

  it('paints a blue Active badge for uppercase "ACTIVE" (Flink casing)', () => {
    expect(tripStatusBadge('ACTIVE')).toEqual({ color: 'blue', label: 'Active' });
  });

  it('paints a blue Active badge for lowercase "active"', () => {
    expect(tripStatusBadge('active')).toEqual({ color: 'blue', label: 'Active' });
  });

  it('leaves the raw string on an unknown status, coloured blue as before', () => {
    expect(tripStatusBadge('cancelled')).toEqual({ color: 'blue', label: 'cancelled' });
  });

  it('returns an empty label for non-string status', () => {
    expect(tripStatusBadge(undefined)).toEqual({ color: 'blue', label: '' });
    expect(tripStatusBadge(null)).toEqual({ color: 'blue', label: '' });
  });
});
