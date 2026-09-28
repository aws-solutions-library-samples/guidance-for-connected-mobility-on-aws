// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * VerdictBanner — small reusable banner that shows the SOVD routine verdict
 * as a Cloudscape StatusIndicator with the canonical label text.
 *
 * When `verdict` is undefined or unrecognised, renders a defensive
 * "Verdict unavailable" info indicator — no crash, no empty render.
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ Group 3
 * Patterns: docs/tech.md § SOVD (c)
 */

import React from 'react';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Box from '@cloudscape-design/components/box';
import type { Verdict } from './types';
import { VERDICT_LABEL, verdictType } from './types';

export interface VerdictBannerProps {
  verdict?: Verdict;
}

/**
 * VerdictBanner — renders the verdict as a StatusIndicator.
 *
 * in_spec     → success (green)   "Check passed"
 * marginal    → warning (yellow)  "Reading within tolerance — monitor"
 * out_of_spec → error   (red)     "Reading outside tolerance — repair action required"
 * missing     → info    (blue)    "Verdict unavailable"
 */
export const VerdictBanner: React.FC<VerdictBannerProps> = ({ verdict }) => {
  if (verdict === undefined || !(verdict in verdictType)) {
    return (
      <Box margin={{ bottom: 's' }}>
        <StatusIndicator type="info">Verdict unavailable</StatusIndicator>
      </Box>
    );
  }

  return (
    <Box margin={{ bottom: 's' }}>
      <StatusIndicator type={verdictType[verdict]}>
        {VERDICT_LABEL[verdict]}
      </StatusIndicator>
    </Box>
  );
};

export default VerdictBanner;
