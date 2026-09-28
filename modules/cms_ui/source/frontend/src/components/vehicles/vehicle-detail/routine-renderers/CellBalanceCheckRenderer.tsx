// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CellBalanceCheckRenderer — renders the `cell_balance_check` routine result.
 *
 * Layout:
 *   [VerdictBanner]
 *   "Max delta: {max_delta_mv} mV"
 *   ColumnLayout (columns=4) of per-cell boxes
 *     — cells >2σ from mean are highlighted with a StatusIndicator in the verdict colour
 *     — all other cells are plain Box with the voltage value
 *
 * Handles arrays of length 4, 8, and 96 without breaking layout.
 * Uses ColumnLayout columns={4} which is verified present in the codebase.
 * (BarChart was not verified at docs/tech.md write time; using ColumnLayout instead.)
 *
 * Falls back to <RawDrawer> when response.result is undefined.
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ D4 / Group 3 T3.7
 */

import React from 'react';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import Box from '@cloudscape-design/components/box';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import SpaceBetween from '@cloudscape-design/components/space-between';
import type { RoutineRendererProps } from './types';
import { VerdictBanner } from './VerdictBanner';
import { RawDrawer } from './RawDrawer';
import { verdictType } from './types';

/**
 * Compute mean and standard deviation of a numeric array.
 * Returns { mean, stdDev }. stdDev is 0 when array has < 2 elements.
 */
function stats(values: number[]): { mean: number; stdDev: number } {
  if (values.length === 0) return { mean: 0, stdDev: 0 };
  const mean = values.reduce((acc, v) => acc + v, 0) / values.length;
  if (values.length < 2) return { mean, stdDev: 0 };
  const variance =
    values.reduce((acc, v) => acc + (v - mean) ** 2, 0) / values.length;
  return { mean, stdDev: Math.sqrt(variance) };
}

export const CellBalanceCheckRenderer: React.FC<RoutineRendererProps> = ({
  response,
  routineId,
  commandId,
}) => {
  if (!response.result) {
    return <RawDrawer response={response} routineId={routineId} commandId={commandId} />;
  }

  const cellVoltages = (response.result.cell_voltages as number[] | undefined) ?? [];
  const maxDeltaMv = response.result.max_delta_mv as number | undefined;

  const { mean, stdDev } = stats(cellVoltages);
  const outlierThreshold = 2 * stdDev;

  // Verdict colour for outlier cells — falls back to 'warning' when undefined
  const outlierIndicatorType =
    response.verdict && response.verdict in verdictType
      ? verdictType[response.verdict]
      : 'warning';

  return (
    <SpaceBetween size="s" data-testid="cell-balance-check-renderer">
      <VerdictBanner verdict={response.verdict} />

      {maxDeltaMv !== undefined && (
        <Box data-testid="cell-max-delta">Max delta: {maxDeltaMv} mV</Box>
      )}

      <ColumnLayout columns={4} data-testid="cell-voltage-grid">
        {cellVoltages.map((voltage, i) => {
          const isOutlier = stdDev > 0 && Math.abs(voltage - mean) > outlierThreshold;
          return (
            <Box key={i} data-testid={`cell-${i}`}>
              {isOutlier ? (
                <StatusIndicator type={outlierIndicatorType}>
                  {voltage.toFixed(3)} V
                </StatusIndicator>
              ) : (
                <Box>{voltage.toFixed(3)} V</Box>
              )}
            </Box>
          );
        })}
      </ColumnLayout>
    </SpaceBetween>
  );
};

export default CellBalanceCheckRenderer;
