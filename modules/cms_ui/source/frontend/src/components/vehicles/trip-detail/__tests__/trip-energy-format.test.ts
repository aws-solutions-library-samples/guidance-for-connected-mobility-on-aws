// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Tests for the trip-detail Energy Used panel formatter.
 *
 * The property that matters: **absent energy renders as "Not reported", never as
 * a number.** The backend returns `energy: null` when no telemetry signal
 * measured consumption, and on the Meridian fleet that is the common case — the
 * EV signals are present in the schema but flat zero (`is_ev` is 0), so a `0`
 * here would assert the trip consumed no energy rather than that nothing
 * measured it.
 */
import { describe, it, expect } from 'vitest';
import { formatEnergyUsed } from '../tripEnergyFormat';

describe('formatEnergyUsed', () => {
  it('renders "Not reported" and says why, for null energy', () => {
    // Mutation: returning '0 kWh' or '0%' for null makes this fail — and would
    // put a fabricated measurement on the page.
    const got = formatEnergyUsed(null);
    expect(got.headline).toBe('Not reported');
    expect(got.detail).toMatch(/No telemetry signal/i);
  });

  it('treats undefined and a malformed payload the same as null', () => {
    expect(formatEnergyUsed(undefined).headline).toBe('Not reported');
    expect(formatEnergyUsed({ basis: 'fuel_level' } as any).headline).toBe('Not reported');
  });

  it('shows kWh alongside the percentage for a state-of-charge basis', () => {
    const got = formatEnergyUsed({
      basis: 'ev_state_of_charge', signal: 'ev_soc',
      startPercent: 80, endPercent: 60, usedPercent: 20,
      samples: 12, usedKwh: 18.8, batteryCapacityKwh: 94,
    });
    expect(got.headline).toBe('18.8 kWh (20.0% of battery)');
    expect(got.detail).toContain('80.0% → 60.0%');
    expect(got.detail).toContain('12 readings');
    expect(got.detail).toContain('state of charge');
  });

  it('shows percent of tank only for a fuel basis — no invented kWh', () => {
    // The real staging shape: fuelLevel 89.5 -> 75.5 with no usedKwh, because a
    // fuel percentage is not convertible to kWh without tank capacity.
    const got = formatEnergyUsed({
      basis: 'fuel_level', signal: 'fuelLevel',
      startPercent: 89.5, endPercent: 75.5, usedPercent: 14,
      samples: 9,
    });
    expect(got.headline).toBe('14.0% of tank');
    expect(got.headline).not.toMatch(/kWh/);
    expect(got.detail).toContain('fuel level');
  });

  it('reports a negative delta as regained rather than hiding it', () => {
    // Regen downhill, or a mid-trip refuel. Mutation: an abs() or max(0,...)
    // clamp on usedPercent makes this fail and silently misreports the trip.
    const got = formatEnergyUsed({
      basis: 'ev_state_of_charge', signal: 'ev_soc',
      startPercent: 60, endPercent: 65, usedPercent: -5,
      samples: 4, usedKwh: -5, batteryCapacityKwh: 100,
    });
    expect(got.headline).toBe('+5.0 kWh regained (5.0% of battery)');
  });

  it('singularises a single reading', () => {
    const got = formatEnergyUsed({
      basis: 'fuel_level', signal: 'fuelLevel',
      startPercent: 50, endPercent: 40, usedPercent: 10, samples: 1,
    });
    expect(got.detail).toContain('1 reading (');
  });

  it('renders a genuine zero-consumption measurement as 0, not "Not reported"', () => {
    // A signal that varied and netted to zero IS a measurement. The backend only
    // returns null when nothing reported at all, and the two must stay distinct
    // on the page.
    const got = formatEnergyUsed({
      basis: 'ev_state_of_charge', signal: 'ev_soc',
      startPercent: 80, endPercent: 80, usedPercent: 0,
      samples: 3, usedKwh: 0, batteryCapacityKwh: 94,
    });
    expect(got.headline).toBe('0.0 kWh (0.0% of battery)');
    expect(got.headline).not.toBe('Not reported');
  });
});
