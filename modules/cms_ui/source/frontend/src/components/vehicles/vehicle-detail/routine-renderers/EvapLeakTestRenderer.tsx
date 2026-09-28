// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * EvapLeakTestRenderer — renders the `evap_leak_test` routine result.
 *
 * Layout:
 *   [VerdictBanner]
 *   ColumnLayout (2 col):
 *     System pressure   |  Leak rate
 *     {kpa} kPa         |  {ccm} cc/min
 *
 * Falls back to <RawDrawer> when response.result is undefined.
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ D4 / Group 3 T3.4
 */

import React from 'react';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import type { RoutineRendererProps } from './types';
import { VerdictBanner } from './VerdictBanner';
import { RawDrawer } from './RawDrawer';

export const EvapLeakTestRenderer: React.FC<RoutineRendererProps> = ({
  response,
  routineId,
  commandId,
}) => {
  if (!response.result) {
    return <RawDrawer response={response} routineId={routineId} commandId={commandId} />;
  }

  const pressureKpa = response.result.system_pressure_kpa as number | undefined;
  const leakRateCcm = response.result.leak_rate_ccm as number | undefined;

  return (
    <SpaceBetween size="s" data-testid="evap-leak-test-renderer">
      <VerdictBanner verdict={response.verdict} />
      <ColumnLayout columns={2} variant="text-grid">
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">System pressure</Box>
          <Box data-testid="evap-pressure-kpa">
            {pressureKpa !== undefined ? `${pressureKpa} kPa` : '—'}
          </Box>
        </SpaceBetween>
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Leak rate</Box>
          <Box data-testid="evap-leak-rate-ccm">
            {leakRateCcm !== undefined ? `${leakRateCcm} cc/min` : '—'}
          </Box>
        </SpaceBetween>
      </ColumnLayout>
    </SpaceBetween>
  );
};

export default EvapLeakTestRenderer;
