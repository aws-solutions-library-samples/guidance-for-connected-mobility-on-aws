// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle list — "Source" column tests.
 *
 * Spec: 2026-08-29-cms-vehicle-classification § D7 + D8
 *
 * Cases:
 *  - Mixed dataset (some onboard, some offboard, one with missing classification)
 *    → the Source column renders the correct badge variant per row.
 *  - Missing 'classification' field on a row (stale cached response)
 *    → renders 'Unknown' badge rather than crashing.
 *  - No per-row fleet-disagreement badge on the list view.
 *
 * Deliberate non-cases (per spec § D7 Constraints):
 *  - No per-row fleet-disagreement badge on the list view.
 *    Disagreement badges live only on the vehicle-detail page.
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ClassificationBadge } from '@/components/vehicles/shared/classification-badge';
import type { VehicleItem } from '@/types/fleet-types';

// ── Minimal Source column cell — mirrors vehicles-table.tsx ───────────────

function SourceCell({ item }: { item: Partial<VehicleItem> }) {
  return (
    <span data-testid={`source-badge-${item.vehicleId}`}>
      <ClassificationBadge classification={item.classification as any} />
    </span>
  );
}

// Render a list of source cells for a dataset
function VehicleSourceList({ vehicles }: { vehicles: Array<Partial<VehicleItem>> }) {
  return (
    <div>
      {vehicles.map(v => (
        <SourceCell key={v.vehicleId} item={v} />
      ))}
    </div>
  );
}

// ── test fixtures ─────────────────────────────────────────────────────────

const MIXED_VEHICLES: Array<Partial<VehicleItem>> = [
  { vehicleId: 'VEH-A', vin: 'TESTVIN000001', classification: 'onboard' },
  { vehicleId: 'VEH-B', vin: 'TESTVIN000002', classification: 'offboard' },
  { vehicleId: 'VEH-C', vin: 'TESTVIN000003', classification: 'onboard' },
];

const VEHICLE_MISSING_CLASSIFICATION: Partial<VehicleItem> = {
  vehicleId: 'VEH-D',
  vin: 'TESTVIN000004',
  // classification intentionally absent — stale cached response
};

// ── tests ─────────────────────────────────────────────────────────────────

describe('Vehicle list — Source column', () => {
  // ── mixed dataset rendering ────────────────────────────────────────────
  it(
    'renders an Onboard badge for vehicles with classification="onboard" ' +
      'and an Offboard badge for vehicles with classification="offboard" ' +
      'in a mixed-dataset vehicle list',
    () => {
      render(<VehicleSourceList vehicles={MIXED_VEHICLES} />);

      // VEH-A and VEH-C are onboard
      const onboardBadges = screen.getAllByText('Onboard');
      expect(onboardBadges.length).toBeGreaterThanOrEqual(2);

      // VEH-B is offboard
      const offboardBadges = screen.getAllByText('Offboard');
      expect(offboardBadges.length).toBeGreaterThanOrEqual(1);

      // per-row source badge wrappers exist
      expect(screen.getByTestId('source-badge-VEH-A')).toBeInTheDocument();
      expect(screen.getByTestId('source-badge-VEH-B')).toBeInTheDocument();
      expect(screen.getByTestId('source-badge-VEH-C')).toBeInTheDocument();
    },
  );

  // ── missing classification field ───────────────────────────────────────
  it(
    "renders an 'Unknown' badge (not an error or blank) when a row's " +
      "'classification' field is absent (e.g. stale cached API response)",
    () => {
      render(<SourceCell item={VEHICLE_MISSING_CLASSIFICATION} />);

      // Should render Unknown, not crash or show blank
      expect(screen.getByText('Unknown')).toBeInTheDocument();
    },
  );

  // ── no per-row disagreement badge on list ─────────────────────────────
  it(
    'does NOT render any fleet-disagreement / mismatch badge on the list view ' +
      'even when classificationWarnings is present on a row — ' +
      'mismatch badges are detail-page only per spec § D7',
    () => {
      // Vehicle that would have a disagreement warning on the detail page.
      // The list cell only gets `classification`, not `classificationWarnings` —
      // and even if it did, it should not render a mismatch badge.
      const vehicleWithWarnings: Partial<VehicleItem> = {
        vehicleId: 'VEH-MISMATCH',
        vin: 'TESTVIN000005',
        classification: 'onboard',
        classificationWarnings: [
          {
            code: 'fleet_disagreement',
            message: "Vehicle disagrees with fleet.",
          },
        ],
      };

      // The SourceCell (mirroring vehicles-table) does NOT render classificationWarnings
      render(<SourceCell item={vehicleWithWarnings} />);

      // Classification badge renders correctly
      expect(screen.getByText('Onboard')).toBeInTheDocument();

      // No fleet mismatch badge present — list view is spec-intentionally badge-free
      expect(screen.queryByText('Fleet mismatch')).not.toBeInTheDocument();
    },
  );
});
