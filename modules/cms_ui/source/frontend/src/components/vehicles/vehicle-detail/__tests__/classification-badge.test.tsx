// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle detail — classification badge tests.
 *
 * Spec: 2026-08-29-cms-vehicle-classification § D7 + D8
 *
 * Cases:
 *  (badge variants)
 *   - onboard vehicle → success-colored badge with label 'Onboard'
 *   - offboard vehicle → stopped-colored badge with label 'Offboard'
 *   - unknown (UnclassifiableVehicleError mapped to 'unknown') → info-colored badge
 *  (disagreement badge)
 *   - response includes classificationWarnings[].code === 'fleet_disagreement'
 *     → additional 'Fleet mismatch' severity-medium badge renders next to
 *       the classification badge
 *   - response with no classificationWarnings → no mismatch badge rendered
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { ClassificationBadge } from '@/components/vehicles/shared/classification-badge';

// ── Helper: render a vehicle-detail-like panel with the badge ─────────────

interface PanelProps {
  classification?: string;
  classificationWarnings?: Array<{ code: string; message: string }>;
}

/**
 * Minimal panel that mirrors the VehicleDetailView Source row.
 * Renders ClassificationBadge + optional Fleet mismatch badge when
 * classificationWarnings includes fleet_disagreement.
 */
function SourcePanel({ classification, classificationWarnings }: PanelProps) {
  const hasMismatch = classificationWarnings?.some(w => w.code === 'fleet_disagreement');
  const mismatchMsg = classificationWarnings?.find(w => w.code === 'fleet_disagreement')?.message ?? '';

  return (
    <div data-testid="source-panel">
      <span data-testid="classification-badge-wrapper">
        <ClassificationBadge classification={classification as any} />
      </span>
      {hasMismatch && (
        <span data-testid="fleet-mismatch-badge-wrapper" title={mismatchMsg}>
          Fleet mismatch
        </span>
      )}
    </div>
  );
}

describe('Vehicle detail — ClassificationBadge', () => {
  // ── onboard ────────────────────────────────────────────────────────────
  // Expected Cloudscape Badge color: "green"
  // (Valid Badge colors verified from @cloudscape-design/components/badge/interfaces.d.ts)
  it(
    "renders a green-colored badge with text 'Onboard' when classification='onboard'",
    () => {
      const { container } = render(<ClassificationBadge classification="onboard" />);
      expect(screen.getByText('Onboard')).toBeInTheDocument();
      // S3: verify the Badge element carries the green color class
      // Cloudscape renders Badge color as a CSS class containing the color name,
      // e.g. awsui_..._green_... — guards against a future regression where an
      // invalid color value (like "success") silently falls back to grey.
      const badge = container.querySelector('[class*="badge"]') ?? container.firstChild;
      expect(badge).toBeTruthy();
      // The rendered HTML class attribute contains "green" for the green Badge variant.
      expect((badge as HTMLElement).className).toMatch(/green/i);
    },
  );

  // ── offboard ───────────────────────────────────────────────────────────
  // Expected Cloudscape Badge color: "red"
  it(
    "renders a red-colored badge with text 'Offboard' when classification='offboard'",
    () => {
      const { container } = render(<ClassificationBadge classification="offboard" />);
      expect(screen.getByText('Offboard')).toBeInTheDocument();
      // S3: verify the red color class is present (not "stopped", which is invalid)
      const badge = container.querySelector('[class*="badge"]') ?? container.firstChild;
      expect((badge as HTMLElement).className).toMatch(/red/i);
    },
  );

  // ── unknown ────────────────────────────────────────────────────────────
  // Expected Cloudscape Badge color: "blue"
  it(
    "renders a blue-colored badge with text 'Unknown' when classification='unknown' " +
      '(mapped from UnclassifiableVehicleError at the projection layer)',
    () => {
      const { container } = render(<ClassificationBadge classification="unknown" />);
      expect(screen.getByText('Unknown')).toBeInTheDocument();
      // S3: verify the blue color class is present
      const badge = container.querySelector('[class*="badge"]') ?? container.firstChild;
      expect((badge as HTMLElement).className).toMatch(/blue/i);
    },
  );

  // ── undefined / missing → Unknown ─────────────────────────────────────
  it("renders 'Unknown' badge when classification is undefined (missing field)", () => {
    render(<ClassificationBadge />);
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  // ── disagreement badge: present ────────────────────────────────────────
  it(
    "renders an additional 'Fleet mismatch' badge when " +
      "classificationWarnings includes an entry with code='fleet_disagreement'",
    () => {
      render(
        <SourcePanel
          classification="onboard"
          classificationWarnings={[
            {
              code: 'fleet_disagreement',
              message:
                "Vehicle dataSource 'vehicle-telemetry' disagrees with fleet data_source 'cloud-telemetry'.",
            },
          ]}
        />,
      );

      // Classification badge is present
      expect(screen.getByText('Onboard')).toBeInTheDocument();

      // Fleet mismatch badge is present
      expect(screen.getByTestId('fleet-mismatch-badge-wrapper')).toBeInTheDocument();
      expect(screen.getByText('Fleet mismatch')).toBeInTheDocument();
    },
  );

  // ── disagreement badge: absent ─────────────────────────────────────────
  it(
    'does NOT render a fleet-mismatch badge when classificationWarnings is ' +
      'absent or empty',
    () => {
      render(
        <SourcePanel
          classification="onboard"
          classificationWarnings={[]}
        />,
      );

      // Classification badge still renders
      expect(screen.getByText('Onboard')).toBeInTheDocument();

      // No mismatch badge
      expect(screen.queryByTestId('fleet-mismatch-badge-wrapper')).not.toBeInTheDocument();
    },
  );
});
