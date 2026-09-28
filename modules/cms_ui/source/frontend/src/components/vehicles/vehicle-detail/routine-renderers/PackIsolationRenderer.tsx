// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PackIsolationRenderer — renders the `pack_isolation_test` routine result.
 *
 * Layout:
 *   [VerdictBanner]
 *   ColumnLayout (2 col):
 *     Measured resistance   |  Minimum spec
 *     {mohm} MΩ             |  {threshold_mohm} MΩ
 *   [Note: Higher is safer — readings below threshold indicate insulation degradation]
 *
 * Falls back to <RawDrawer> when response.result is undefined.
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ D4 / Group 3 T3.6
 */

import React from 'react';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import type { RoutineRendererProps } from './types';
import { VerdictBanner } from './VerdictBanner';
import { RawDrawer } from './RawDrawer';

export const PackIsolationRenderer: React.FC<RoutineRendererProps> = ({
  response,
  routineId,
  commandId,
}) => {
  if (!response.result) {
    return <RawDrawer response={response} routineId={routineId} commandId={commandId} />;
  }

  const measuredMohm = response.result.isolation_resistance_mohm as number | undefined;
  const thresholdMohm = response.result.threshold_mohm as number | undefined;

  return (
    <SpaceBetween size="s" data-testid="pack-isolation-renderer">
      <VerdictBanner verdict={response.verdict} />
      <ColumnLayout columns={2} variant="text-grid">
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Measured resistance</Box>
          <Box data-testid="pack-measured-mohm">
            {measuredMohm !== undefined ? `${measuredMohm} MΩ` : '—'}
          </Box>
        </SpaceBetween>
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Minimum spec</Box>
          <Box data-testid="pack-threshold-mohm">
            {thresholdMohm !== undefined ? `${thresholdMohm} MΩ` : '—'}
          </Box>
        </SpaceBetween>
      </ColumnLayout>
      <Box variant="small" color="text-body-secondary" data-testid="pack-isolation-note">
        Higher is safer — readings below threshold indicate insulation degradation.
      </Box>
    </SpaceBetween>
  );
};

export default PackIsolationRenderer;
