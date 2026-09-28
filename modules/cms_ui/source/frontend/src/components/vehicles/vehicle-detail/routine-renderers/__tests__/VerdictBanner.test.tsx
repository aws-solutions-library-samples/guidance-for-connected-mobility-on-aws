// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VerdictBanner — 4 test cases
 *
 * (1) in_spec     → success StatusIndicator  "Check passed"
 * (2) marginal    → warning StatusIndicator  "Reading within tolerance — monitor"
 * (3) out_of_spec → error   StatusIndicator  "Reading outside tolerance — repair action required"
 * (4) missing     → info    StatusIndicator  "Verdict unavailable"
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { VerdictBanner } from '../VerdictBanner';

describe('VerdictBanner', () => {
  it('renders success StatusIndicator for in_spec', () => {
    render(<VerdictBanner verdict="in_spec" />);
    // StatusIndicator renders the text; verify the label text is present
    expect(screen.getByText('Check passed')).toBeInTheDocument();
  });

  it('renders warning StatusIndicator for marginal', () => {
    render(<VerdictBanner verdict="marginal" />);
    expect(screen.getByText('Reading within tolerance — monitor')).toBeInTheDocument();
  });

  it('renders error StatusIndicator for out_of_spec', () => {
    render(<VerdictBanner verdict="out_of_spec" />);
    expect(
      screen.getByText('Reading outside tolerance — repair action required'),
    ).toBeInTheDocument();
  });

  it('renders info "Verdict unavailable" when verdict is undefined', () => {
    render(<VerdictBanner />);
    expect(screen.getByText('Verdict unavailable')).toBeInTheDocument();
  });
});
