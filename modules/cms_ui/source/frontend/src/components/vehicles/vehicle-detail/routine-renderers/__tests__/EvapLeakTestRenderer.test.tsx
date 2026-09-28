// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * EvapLeakTestRenderer — 3 verdict cases + 1 missing-result fallback
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { EvapLeakTestRenderer } from '../EvapLeakTestRenderer';

describe('EvapLeakTestRenderer', () => {
  it('renders in_spec verdict with pressure and leak rate', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: { system_pressure_kpa: 95, leak_rate_ccm: 0.8 },
    };
    render(<EvapLeakTestRenderer response={response} routineId="evap_leak_test" />);
    expect(screen.getByText('Check passed')).toBeInTheDocument();
    expect(screen.getByTestId('evap-pressure-kpa')).toHaveTextContent('95 kPa');
    expect(screen.getByTestId('evap-leak-rate-ccm')).toHaveTextContent('0.8 cc/min');
  });

  it('renders marginal verdict', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'marginal' as const,
      result: { system_pressure_kpa: 92, leak_rate_ccm: 1.8 },
    };
    render(<EvapLeakTestRenderer response={response} routineId="evap_leak_test" />);
    expect(screen.getByText('Reading within tolerance — monitor')).toBeInTheDocument();
  });

  it('renders out_of_spec verdict', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'out_of_spec' as const,
      result: { system_pressure_kpa: 80, leak_rate_ccm: 5.0 },
    };
    render(<EvapLeakTestRenderer response={response} routineId="evap_leak_test" />);
    expect(
      screen.getByText('Reading outside tolerance — repair action required'),
    ).toBeInTheDocument();
  });

  it('falls back to RawDrawer when result is undefined', () => {
    const response = { status: 'SUCCEEDED', verdict: 'in_spec' as const, result: undefined };
    render(
      <EvapLeakTestRenderer response={response} routineId="evap_leak_test" commandId="cmd-evap" />,
    );
    expect(screen.queryByTestId('evap-leak-test-renderer')).toBeNull();
    expect(screen.getByTestId('session-log-response-drawer-cmd-evap')).toBeInTheDocument();
  });
});
