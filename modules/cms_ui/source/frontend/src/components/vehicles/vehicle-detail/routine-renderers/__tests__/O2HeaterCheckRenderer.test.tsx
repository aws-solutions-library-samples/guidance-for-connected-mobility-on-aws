// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * O2HeaterCheckRenderer — 3 verdict cases + 1 missing-result fallback
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { O2HeaterCheckRenderer } from '../O2HeaterCheckRenderer';

describe('O2HeaterCheckRenderer', () => {
  it('renders in_spec verdict with response and threshold values', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: { bank1_upstream_response_ms: 800, threshold_ms: 1500 },
    };
    render(<O2HeaterCheckRenderer response={response} routineId="o2_heater_check" />);
    expect(screen.getByText('Check passed')).toBeInTheDocument();
    expect(screen.getByTestId('o2-response-ms')).toHaveTextContent('800 ms');
    expect(screen.getByTestId('o2-threshold-ms')).toHaveTextContent('1500 ms');
  });

  it('renders marginal verdict', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'marginal' as const,
      result: { bank1_upstream_response_ms: 1550, threshold_ms: 1500 },
    };
    render(<O2HeaterCheckRenderer response={response} routineId="o2_heater_check" />);
    expect(screen.getByText('Reading within tolerance — monitor')).toBeInTheDocument();
    expect(screen.getByTestId('o2-response-ms')).toHaveTextContent('1550 ms');
  });

  it('renders out_of_spec verdict', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'out_of_spec' as const,
      result: { bank1_upstream_response_ms: 2000, threshold_ms: 1500 },
    };
    render(<O2HeaterCheckRenderer response={response} routineId="o2_heater_check" />);
    expect(
      screen.getByText('Reading outside tolerance — repair action required'),
    ).toBeInTheDocument();
  });

  it('falls back to RawDrawer when result is undefined', () => {
    const response = { status: 'SUCCEEDED', verdict: 'in_spec' as const, result: undefined };
    render(
      <O2HeaterCheckRenderer response={response} routineId="o2_heater_check" commandId="cmd-o2" />,
    );
    expect(screen.queryByTestId('o2-heater-check-renderer')).toBeNull();
    expect(screen.getByTestId('session-log-response-drawer-cmd-o2')).toBeInTheDocument();
  });
});
