// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataSourceBadge — red-phase test skeleton.
 *
 * Spec: 2026-09-11-cms-cs-meridian-ingestion T1.7
 *
 * All 4 cases will be RED against the stub (which always renders
 * "Unknown").  G2.T2.4 implements the real mapping and makes them green.
 *
 * Cases:
 *  (1) dataSourceRoute="vehicle-telemetry" → renders "Onboard FWE"
 *  (2) dataSourceRoute="cs-meridian"       → renders "Connected Services (Meridian)"
 *  (3) dataSourceRoute undefined           → renders "Unknown"
 *  (4) dataSourceRoute unrecognised value  → renders "Unknown" (no throw)
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { DataSourceBadge } from './DataSourceBadge';

describe('DataSourceBadge', () => {
  // ── (1) Onboard FWE ──────────────────────────────────────────────────────
  // RED against stub: stub returns "Unknown", not "Onboard FWE"
  it('renders "Onboard FWE" when dataSourceRoute is "vehicle-telemetry"', () => {
    render(<DataSourceBadge dataSourceRoute="vehicle-telemetry" />);
    expect(screen.getByText('Onboard FWE')).toBeInTheDocument();
  });

  // ── (2) Connected Services (Meridian) ────────────────────────────────────
  // RED against stub: stub returns "Unknown", not the Meridian label
  it('renders "Connected Services (Meridian)" when dataSourceRoute is "cs-meridian"', () => {
    render(<DataSourceBadge dataSourceRoute="cs-meridian" />);
    expect(screen.getByText('Connected Services (Meridian)')).toBeInTheDocument();
  });

  // ── (3) Missing / undefined prop → Unknown ───────────────────────────────
  // GREEN against stub (stub always returns "Unknown")
  it('renders "Unknown" when dataSourceRoute is undefined', () => {
    render(<DataSourceBadge />);
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  // ── (4) Unrecognised value → Unknown, no throw ───────────────────────────
  // GREEN against stub (stub always returns "Unknown")
  it('renders "Unknown" for an unrecognised dataSourceRoute value and does not throw', () => {
    expect(() =>
      render(<DataSourceBadge dataSourceRoute="some-future-source" />),
    ).not.toThrow();
    expect(screen.getByText('Unknown')).toBeInTheDocument();
  });

  // ── (5) cloud-telemetry → OEM (Cloud) — vehicle-level dataSource fallback ─
  it('renders "OEM (Cloud)" when dataSourceRoute is "cloud-telemetry"', () => {
    render(<DataSourceBadge dataSourceRoute="cloud-telemetry" />);
    expect(screen.getByText('OEM (Cloud)')).toBeInTheDocument();
  });

  // ── (6) Legacy enum values still resolve correctly ───────────────────────
  it('renders "Onboard FWE" for legacy value "onboard-fwe"', () => {
    render(<DataSourceBadge dataSourceRoute="onboard-fwe" />);
    expect(screen.getByText('Onboard FWE')).toBeInTheDocument();
  });

  it('renders "OEM (Cloud)" for legacy value "cloud-oem1"', () => {
    render(<DataSourceBadge dataSourceRoute="cloud-oem1" />);
    expect(screen.getByText('OEM (Cloud)')).toBeInTheDocument();
  });
});
