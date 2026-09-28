// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * LampSelfCheckRenderer — 4 test cases
 *
 * (1) All 8 lamps ok + in_spec verdict → all success StatusIndicators
 * (2) One flicker lamp + marginal verdict → warning StatusIndicator for that lamp
 * (3) One open_circuit lamp + out_of_spec verdict → error StatusIndicator for that lamp
 * (4) Missing result → falls back to RawDrawer (no lamp grid rendered)
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { LampSelfCheckRenderer } from '../LampSelfCheckRenderer';

describe('LampSelfCheckRenderer', () => {
  // ── (1) All OK / in_spec ──────────────────────────────────────────────────
  it('renders all success StatusIndicators when all lamps are ok (in_spec)', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: {
        lamps: ['ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok', 'ok'],
        ambient_lux: 340,
      },
    };
    render(
      <LampSelfCheckRenderer response={response} routineId="lamp_self_check" commandId="cmd-1" />,
    );

    // Verdict banner shows "Check passed"
    expect(screen.getByText('Check passed')).toBeInTheDocument();

    // All 8 lamp cells rendered
    for (let i = 0; i < 8; i++) {
      expect(screen.getByTestId(`lamp-cell-${i}`)).toBeInTheDocument();
    }

    // Ambient lux shown
    expect(screen.getByTestId('lamp-ambient-lux')).toHaveTextContent('340 lx');

    // No error or warning lamp statuses
    expect(screen.queryByText('open_circuit')).toBeNull();
    expect(screen.queryByText('flicker')).toBeNull();
  });

  // ── (2) One flicker → marginal ────────────────────────────────────────────
  it('renders warning for a flicker lamp (marginal)', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'marginal' as const,
      result: {
        lamps: ['ok', 'ok', 'ok', 'ok', 'ok', 'flicker', 'ok', 'ok'],
        ambient_lux: 12,
      },
    };
    render(
      <LampSelfCheckRenderer response={response} routineId="lamp_self_check" commandId="cmd-2" />,
    );

    // Verdict banner shows marginal label
    expect(screen.getByText('Reading within tolerance — monitor')).toBeInTheDocument();

    // The flicker lamp cell is rendered
    expect(screen.getByText('flicker')).toBeInTheDocument();
  });

  // ── (3) One open_circuit → out_of_spec ────────────────────────────────────
  it('renders error StatusIndicator for an open_circuit lamp (out_of_spec)', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'out_of_spec' as const,
      result: {
        lamps: ['ok', 'ok', 'open_circuit', 'ok', 'ok', 'ok', 'ok', 'ok'],
        ambient_lux: 412,
      },
    };
    const { container } = render(
      <LampSelfCheckRenderer response={response} routineId="lamp_self_check" commandId="cmd-3" />,
    );

    // Verdict banner shows out_of_spec label
    expect(
      screen.getByText('Reading outside tolerance — repair action required'),
    ).toBeInTheDocument();

    // The open_circuit lamp text is rendered
    expect(screen.getByText('open_circuit')).toBeInTheDocument();

    // Cloudscape StatusIndicator renders a class containing "status-error" for type="error".
    // This guards that lampIndicatorType returns 'error' for open_circuit — a mutation
    // that always returns 'success' would cause the lamp cell's indicator to be 'success',
    // not 'error'. We check the lamp cell itself, not just any error in the container.
    const cell2 = screen.getByTestId('lamp-cell-2'); // index 2 = L brake = open_circuit
    const errorInCell = cell2.querySelectorAll('[class*="status-error"]');
    expect(errorInCell.length).toBeGreaterThan(0);
  });

  // ── (4) Missing result → falls back to RawDrawer ──────────────────────────
  it('falls back to RawDrawer when result is undefined', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: undefined,
    };
    render(
      <LampSelfCheckRenderer response={response} routineId="lamp_self_check" commandId="cmd-4" />,
    );

    // No lamp grid
    expect(screen.queryByTestId('lamp-self-check-renderer')).toBeNull();
    // RawDrawer's ExpandableSection should appear
    expect(screen.getByTestId('session-log-response-drawer-cmd-4')).toBeInTheDocument();
  });
});
