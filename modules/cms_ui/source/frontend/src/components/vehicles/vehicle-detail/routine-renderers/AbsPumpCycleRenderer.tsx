// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AbsPumpCycleRenderer — renders the `abs_pump_cycle` routine result.
 *
 * Layout:
 *   [VerdictBanner]
 *   ColumnLayout (1 col):
 *     Cycles
 *     {cycles_observed} / {cycles_expected}
 *
 * Falls back to <RawDrawer> when response.result is undefined.
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ D4 / Group 3 T3.5
 */

import React from 'react';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import type { RoutineRendererProps } from './types';
import { VerdictBanner } from './VerdictBanner';
import { RawDrawer } from './RawDrawer';

export const AbsPumpCycleRenderer: React.FC<RoutineRendererProps> = ({
  response,
  routineId,
  commandId,
}) => {
  if (!response.result) {
    return <RawDrawer response={response} routineId={routineId} commandId={commandId} />;
  }

  const observed = response.result.cycles_observed as number | undefined;
  const expected = response.result.cycles_expected as number | undefined;

  return (
    <SpaceBetween size="s" data-testid="abs-pump-cycle-renderer">
      <VerdictBanner verdict={response.verdict} />
      <ColumnLayout columns={1} variant="text-grid">
        <SpaceBetween size="xs">
          <Box variant="awsui-key-label">Cycles</Box>
          <Box data-testid="abs-cycles">
            {observed !== undefined && expected !== undefined
              ? `${observed} / ${expected}`
              : '—'}
          </Box>
        </SpaceBetween>
      </ColumnLayout>
    </SpaceBetween>
  );
};

export default AbsPumpCycleRenderer;
