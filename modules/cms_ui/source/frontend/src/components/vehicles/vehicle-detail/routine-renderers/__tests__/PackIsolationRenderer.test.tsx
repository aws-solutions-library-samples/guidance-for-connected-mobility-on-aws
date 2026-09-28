// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PackIsolationRenderer — 3 verdict cases + 1 missing-result fallback
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { PackIsolationRenderer } from '../PackIsolationRenderer';

describe('PackIsolationRenderer', () => {
  it('renders in_spec verdict with resistance and threshold', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'in_spec' as const,
      result: { isolation_resistance_mohm: 500, threshold_mohm: 100 },
    };
    render(<PackIsolationRenderer response={response} routineId="pack_isolation_test" />);
    expect(screen.getByText('Check passed')).toBeInTheDocument();
    expect(screen.getByTestId('pack-measured-mohm')).toHaveTextContent('500 MΩ');
    expect(screen.getByTestId('pack-threshold-mohm')).toHaveTextContent('100 MΩ');
    // Safety note is rendered
    expect(screen.getByTestId('pack-isolation-note')).toHaveTextContent(
      'Higher is safer',
    );
  });

  it('renders marginal verdict', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'marginal' as const,
      result: { isolation_resistance_mohm: 120, threshold_mohm: 100 },
    };
    render(<PackIsolationRenderer response={response} routineId="pack_isolation_test" />);
    expect(screen.getByText('Reading within tolerance — monitor')).toBeInTheDocument();
  });

  it('renders out_of_spec verdict', () => {
    const response = {
      status: 'SUCCEEDED',
      verdict: 'out_of_spec' as const,
      result: { isolation_resistance_mohm: 40, threshold_mohm: 100 },
    };
    render(<PackIsolationRenderer response={response} routineId="pack_isolation_test" />);
    expect(
      screen.getByText('Reading outside tolerance — repair action required'),
    ).toBeInTheDocument();
  });

  it('falls back to RawDrawer when result is undefined', () => {
    const response = { status: 'SUCCEEDED', verdict: 'in_spec' as const, result: undefined };
    render(
      <PackIsolationRenderer
        response={response}
        routineId="pack_isolation_test"
        commandId="cmd-pack"
      />,
    );
    expect(screen.queryByTestId('pack-isolation-renderer')).toBeNull();
    expect(screen.getByTestId('session-log-response-drawer-cmd-pack')).toBeInTheDocument();
  });
});
