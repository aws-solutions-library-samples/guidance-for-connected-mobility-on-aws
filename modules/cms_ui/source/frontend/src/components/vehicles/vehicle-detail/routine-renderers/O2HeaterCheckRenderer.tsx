// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * O2HeaterCheckRenderer — renders the `o2_heater_check` routine result.
 *
 * Layout:
 *   [VerdictBanner]
 *   ColumnLayout (2 col):
 *     Bank 1 upstream response  |  Manufacturer threshold
 *     {response_ms} ms           |  {threshold_ms} ms
 *
 * Falls back to <RawDrawer> when response.result is undefined.
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ D4 / Group 3 T3.3
 */

import React from 'react';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import type { RoutineRendererProps } from './types';
import { VerdictBanner } from './VerdictBanner';
import { RawDrawer } from './RawDrawer';

export const O2HeaterCheckRenderer: React.FC<RoutineRendererProps> = ({
  response,
  routineId,
  commandId,
}) => {
  if (!response.result) {
    return <RawDrawer response={response} routineId={routineId} commandId={commandId} />;
  }

  const responseMs = response.result.bank1_upstream_response_ms as number | undefined;
  const thresholdMs = response.result.threshold_ms as number | undefined;

  return (
    <SpaceBetween size="s" data-testid="o2-heater-check-renderer">
      <VerdictBanner verdict={response.verdict} />
      <ColumnLayout columns={2} variant="text-grid">
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Bank 1 upstream response</Box>
          <Box data-testid="o2-response-ms">
            {responseMs !== undefined ? `${responseMs} ms` : '—'}
          </Box>
        </SpaceBetween>
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Manufacturer threshold</Box>
          <Box data-testid="o2-threshold-ms">
            {thresholdMs !== undefined ? `${thresholdMs} ms` : '—'}
          </Box>
        </SpaceBetween>
      </ColumnLayout>
    </SpaceBetween>
  );
};

export default O2HeaterCheckRenderer;
