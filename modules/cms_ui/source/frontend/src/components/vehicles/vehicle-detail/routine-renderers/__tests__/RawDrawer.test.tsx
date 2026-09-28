// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RawDrawer — tests
 *
 * (1) Small payload (<4096 bytes) renders as <pre> with JSON content; no truncation notice
 * (2) Large payload (>4096 bytes) renders truncated <pre> + truncation notice + download link
 * (3) Non-SUCCEEDED status renders nothing (null)
 */

import React from 'react';
import { render, screen } from '@testing-library/react';
import { describe, it, expect } from 'vitest';
import { RawDrawer } from '../RawDrawer';

describe('RawDrawer', () => {
  it('renders a small payload as <pre> without truncation notice', () => {
    const response = { status: 'SUCCEEDED', verdict: 'in_spec' as const, result: { foo: 'bar' } };
    render(<RawDrawer response={response} routineId="test_routine" commandId="cmd-001" />);

    const pre = screen.getByTestId('session-log-response-pre-cmd-001');
    expect(pre).toBeInTheDocument();
    // JSON payload is present in the pre
    expect(pre.textContent).toContain('"foo"');
    expect(pre.textContent).toContain('"bar"');

    // No truncation notice
    expect(screen.queryByTestId('session-log-response-truncated-cmd-001')).toBeNull();
    expect(screen.queryByTestId('session-log-response-download-cmd-001')).toBeNull();
  });

  it('renders a large payload (>4096 bytes) with truncation notice and download link', () => {
    // Build a payload whose JSON serialisation exceeds 4096 bytes
    const bigPayload = { status: 'SUCCEEDED', data: 'x'.repeat(5000) };
    render(<RawDrawer response={bigPayload} routineId="test_routine" commandId="cmd-002" />);

    const pre = screen.getByTestId('session-log-response-pre-cmd-002');
    expect(pre).toBeInTheDocument();

    // Truncation notice is rendered
    expect(screen.getByTestId('session-log-response-truncated-cmd-002')).toBeInTheDocument();
    expect(
      screen.getByText(/Payload exceeds 4 KB/),
    ).toBeInTheDocument();

    // Download link is rendered
    const downloadLink = screen.getByTestId('session-log-response-download-cmd-002');
    expect(downloadLink).toBeInTheDocument();
    expect(downloadLink.getAttribute('download')).toBe('response-cmd-002.json');
    expect(downloadLink.getAttribute('href')).toMatch(/^data:application\/json/);
  });

  it('renders nothing when status is not SUCCEEDED', () => {
    const response = { status: 'FAILED', reason: 'timeout' };
    const { container } = render(
      <RawDrawer response={response} routineId="test_routine" commandId="cmd-003" />,
    );
    expect(container.firstChild).toBeNull();
  });
});
