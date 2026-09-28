// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Presentation helper for the trip-detail Energy Used panel.
 *
 * Mirrors the backend's `compute_trip_energy` contract: `energy` is `null` when
 * NO telemetry signal reported consumption, and that must render as "Not
 * reported" rather than as `0`. On the Meridian fleet the EV signals exist in
 * the schema but are flat zeros (`is_ev` is 0), so "0 kWh" would state that a
 * trip consumed no energy when the truth is that nothing measured it.
 */

export interface TripEnergy {
  basis: 'ev_state_of_charge' | 'fuel_level' | string;
  signal: string;
  startPercent: number;
  endPercent: number;
  usedPercent: number;
  samples: number;
  usedKwh?: number;
  batteryCapacityKwh?: number;
}

/** What the panel shows: a headline figure plus the basis it was derived from. */
export function formatEnergyUsed(
  energy: TripEnergy | null | undefined,
): { headline: string; detail: string | null } {
  if (!energy || typeof energy.usedPercent !== 'number') {
    return {
      headline: 'Not reported',
      // Says WHY there is no number, so it does not read as a UI fault.
      detail: 'No telemetry signal on this trip reported energy or fuel level.',
    };
  }

  const pct = energy.usedPercent;
  const isCharge = energy.basis === 'ev_state_of_charge';
  const scale = isCharge ? 'battery' : 'tank';

  // Regen, or refuelling mid-trip, legitimately produces a negative delta.
  // Reported as a gain rather than hidden behind an absolute value.
  const gained = pct < 0;
  const magnitude = Math.abs(pct).toFixed(1);

  let headline: string;
  if (typeof energy.usedKwh === 'number') {
    const kwh = Math.abs(energy.usedKwh).toFixed(1);
    headline = gained
      ? `+${kwh} kWh regained (${magnitude}% of ${scale})`
      : `${kwh} kWh (${magnitude}% of ${scale})`;
  } else {
    headline = gained
      ? `+${magnitude}% of ${scale} regained`
      : `${magnitude}% of ${scale}`;
  }

  const detail =
    `${energy.startPercent.toFixed(1)}% → ${energy.endPercent.toFixed(1)}% ` +
    `over ${energy.samples} reading${energy.samples === 1 ? '' : 's'} ` +
    `(${isCharge ? 'state of charge' : 'fuel level'})`;

  return { headline, detail };
}
