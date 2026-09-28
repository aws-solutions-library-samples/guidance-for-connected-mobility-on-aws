// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CellBalanceCheckRenderer — 4 test cases
 *
 * (1) 8 cells, in_spec — all within 2σ, no outlier indicators
 * (2) 8 cells, marginal — one outlier highlighted with warning StatusIndicator
 * (3) 8 cells, out_of_spec — one outlier highlighted with error StatusIndicator
 * (4) Variable lengths: 4-cell and 96-cell arrays render the correct number of cells
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { CellBalanceCheckRenderer } from '../CellBalanceCheckRenderer';

/** Build an 8-cell array with all cells at baseV except index `outlierIdx` which
 *  is bumped by `delta` V — large enough to exceed 2σ. */
function makeVoltages(count: number, baseV = 3.7, outlierIdx?: number, delta = 0.5): number[] {
  return Array.from({ length: count }, (_, i) =>
    i === outlierIdx ? baseV + delta : baseV,
  );
}

describe('CellBalanceCheckRenderer', () => {
  // ── (1) in_spec — all equal cells, no outlier ────────────────────────────
  it('renders all cells in_spec without outlier indicators', () => {
    const voltages = makeVoltages(8);
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: { cell_voltages: voltages, max_delta_mv: 5 },
    };
    render(<CellBalanceCheckRenderer response={response} routineId="cell_balance_check" />);

    expect(screen.getByText('Check passed')).toBeInTheDocument();
    expect(screen.getByTestId('cell-max-delta')).toHaveTextContent('5 mV');

    // 8 cells rendered
    for (let i = 0; i < 8; i++) {
      expect(screen.getByTestId(`cell-${i}`)).toBeInTheDocument();
    }
  });

  // ── (2) marginal — one outlier cell ──────────────────────────────────────
  it('renders marginal verdict with one outlier cell highlighted', () => {
    // All cells at 3.7 V except cell 3 at 4.2 V — far outside 2σ
    const voltages = makeVoltages(8, 3.7, 3, 0.5);
    const response = {
      status: 'SUCCEEDED',
      verdict: 'marginal' as const,
      result: { cell_voltages: voltages, max_delta_mv: 500 },
    };
    render(<CellBalanceCheckRenderer response={response} routineId="cell_balance_check" />);

    expect(screen.getByText('Reading within tolerance — monitor')).toBeInTheDocument();

    // Cell 3 should be an outlier and show a StatusIndicator
    const cell3 = screen.getByTestId('cell-3');
    // The StatusIndicator text is the formatted voltage (4.200 V)
    expect(cell3).toHaveTextContent('4.200 V');
  });

  // ── (3) out_of_spec — outlier highlighted with error indicator ───────────
  it('renders out_of_spec verdict with error StatusIndicator for outlier', () => {
    const voltages = makeVoltages(8, 3.7, 5, 0.8);
    const response = {
      status: 'SUCCEEDED',
      verdict: 'out_of_spec' as const,
      result: { cell_voltages: voltages, max_delta_mv: 800 },
    };
    render(<CellBalanceCheckRenderer response={response} routineId="cell_balance_check" />);

    expect(
      screen.getByText('Reading outside tolerance — repair action required'),
    ).toBeInTheDocument();

    // Cell 5 (index 5) is the outlier
    expect(screen.getByTestId('cell-5')).toBeInTheDocument();
  });

  // ── (4a) 4 cells — small pack ────────────────────────────────────────────
  it('renders exactly 4 cells for a 4-cell pack', () => {
    const voltages = makeVoltages(4);
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: { cell_voltages: voltages, max_delta_mv: 2 },
    };
    render(<CellBalanceCheckRenderer response={response} routineId="cell_balance_check" />);
    for (let i = 0; i < 4; i++) {
      expect(screen.getByTestId(`cell-${i}`)).toBeInTheDocument();
    }
    expect(screen.queryByTestId('cell-4')).toBeNull();
  });

  // ── (4b) 96 cells — EV pack ──────────────────────────────────────────────
  it('renders all 96 cells for a 96-cell pack without throwing', () => {
    const voltages = makeVoltages(96);
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: { cell_voltages: voltages, max_delta_mv: 3 },
    };
    expect(() =>
      render(<CellBalanceCheckRenderer response={response} routineId="cell_balance_check" />),
    ).not.toThrow();

    // Spot check a few cells at known indices
    expect(screen.getByTestId('cell-0')).toBeInTheDocument();
    expect(screen.getByTestId('cell-47')).toBeInTheDocument();
    expect(screen.getByTestId('cell-95')).toBeInTheDocument();
    expect(screen.queryByTestId('cell-96')).toBeNull();
  });

  // ── (5) Missing result → falls back to RawDrawer ─────────────────────────
  it('falls back to RawDrawer when result is undefined', () => {
    const response = { status: 'SUCCEEDED', verdict: 'in_spec' as const, result: undefined };
    render(
      <CellBalanceCheckRenderer
        response={response}
        routineId="cell_balance_check"
        commandId="cmd-cell"
      />,
    );
    expect(screen.queryByTestId('cell-balance-check-renderer')).toBeNull();
    expect(screen.getByTestId('session-log-response-drawer-cmd-cell')).toBeInTheDocument();
  });
});
