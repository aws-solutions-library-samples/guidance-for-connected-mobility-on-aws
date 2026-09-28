// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AbsPumpCycleRenderer — 3 verdict cases + 1 missing-result fallback
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { AbsPumpCycleRenderer } from '../AbsPumpCycleRenderer';

describe('AbsPumpCycleRenderer', () => {
  it('renders in_spec verdict with cycles fraction', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: { cycles_observed: 10, cycles_expected: 10 },
    };
    render(<AbsPumpCycleRenderer response={response} routineId="abs_pump_cycle" />);
    expect(screen.getByText('Check passed')).toBeInTheDocument();
    expect(screen.getByTestId('abs-cycles')).toHaveTextContent('10 / 10');
  });

  it('renders marginal verdict', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'marginal' as const,
      result: { cycles_observed: 8, cycles_expected: 10 },
    };
    render(<AbsPumpCycleRenderer response={response} routineId="abs_pump_cycle" />);
    expect(screen.getByText('Reading within tolerance — monitor')).toBeInTheDocument();
    expect(screen.getByTestId('abs-cycles')).toHaveTextContent('8 / 10');
  });

  it('renders out_of_spec verdict', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'out_of_spec' as const,
      result: { cycles_observed: 3, cycles_expected: 10 },
    };
    render(<AbsPumpCycleRenderer response={response} routineId="abs_pump_cycle" />);
    expect(
      screen.getByText('Reading outside tolerance — repair action required'),
    ).toBeInTheDocument();
    expect(screen.getByTestId('abs-cycles')).toHaveTextContent('3 / 10');
  });

  it('falls back to RawDrawer when result is undefined', () => {
    const response = { status: 'SUCCEEDED', verdict: 'in_spec' as const, result: undefined };
    render(
      <AbsPumpCycleRenderer response={response} routineId="abs_pump_cycle" commandId="cmd-abs" />,
    );
    expect(screen.queryByTestId('abs-pump-cycle-renderer')).toBeNull();
    expect(screen.getByTestId('session-log-response-drawer-cmd-abs')).toBeInTheDocument();
  });
});
