// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * LampSelfCheckRenderer — renders the `lamp_self_check` routine result.
 *
 * Layout:
 *   ┌─────────────────────────────────────────────────────────┐
 *   │  [VerdictBanner]                                         │
 *   │  ── Row 1 (4 lamps) ────────────────────────────────── │
 *   │  L headlight   R headlight   L brake      R brake        │
 *   │  ── Row 2 (4 lamps) ────────────────────────────────── │
 *   │  L turn        R turn        reverse       license plate │
 *   │  Ambient light: NNN lx                                   │
 *   └─────────────────────────────────────────────────────────┘
 *
 * Per-lamp StatusIndicator type:
 *   ok           → success
 *   dim / flicker → warning
 *   open_circuit / short → error
 *
 * Falls back to <RawDrawer> when response.result is undefined.
 *
 * Spec: .kiro/specs/2026-09-10-cms-sovd-routine-result-contracts/ D4 / Group 3 T3.2
 * Patterns: docs/tech.md § SOVD (b), (c)
 */

import React from 'react';
import Grid from '@cloudscape-design/components/grid';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Box from '@cloudscape-design/components/box';
import SpaceBetween from '@cloudscape-design/components/space-between';
import type { RoutineRendererProps } from './types';
import { VerdictBanner } from './VerdictBanner';
import { RawDrawer } from './RawDrawer';

// Lamp names in index order (per spec D4 field description)
const LAMP_NAMES = [
  'L headlight',
  'R headlight',
  'L brake',
  'R brake',
  'L turn',
  'R turn',
  'reverse',
  'license plate',
];

type LampStatus = 'ok' | 'dim' | 'flicker' | 'open_circuit' | 'short';

function lampIndicatorType(status: LampStatus | string): 'success' | 'warning' | 'error' {
  if (status === 'ok') return 'success';
  if (status === 'dim' || status === 'flicker') return 'warning';
  return 'error'; // open_circuit | short | unknown
}

// 4-across grid definition (12-col Cloudscape grid)
const FOUR_COL: Array<{ colspan: number }> = [
  { colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 },
];

/**
 * LampSelfCheckRenderer — renders 8 lamp verdicts in a 4×2 grid with a
 * per-lamp StatusIndicator and the ambient lux reading below.
 */
export const LampSelfCheckRenderer: React.FC<RoutineRendererProps> = ({
  response,
  routineId,
  commandId,
}) => {
  // Fall back to RawDrawer when result is absent
  if (!response.result) {
    return <RawDrawer response={response} routineId={routineId} commandId={commandId} />;
  }

  const lamps = (response.result.lamps as string[] | undefined) ?? [];
  const ambientLux = response.result.ambient_lux as number | undefined;

  const row1 = lamps.slice(0, 4);
  const row2 = lamps.slice(4, 8);

  return (
    <SpaceBetween size="s" data-testid="lamp-self-check-renderer">
      <VerdictBanner verdict={response.verdict} />

      {/* Row 1: L headlight, R headlight, L brake, R brake */}
      <Grid gridDefinition={FOUR_COL}>
        {row1.map((lampStatus, i) => (
          <Box key={i} data-testid={`lamp-cell-${i}`}>
            <Box variant="small" color="text-body-secondary">{LAMP_NAMES[i]}</Box>
            <StatusIndicator type={lampIndicatorType(lampStatus)}>
              {lampStatus}
            </StatusIndicator>
          </Box>
        ))}
      </Grid>

      {/* Row 2: L turn, R turn, reverse, license plate */}
      <Grid gridDefinition={FOUR_COL}>
        {row2.map((lampStatus, i) => (
          <Box key={i + 4} data-testid={`lamp-cell-${i + 4}`}>
            <Box variant="small" color="text-body-secondary">{LAMP_NAMES[i + 4]}</Box>
            <StatusIndicator type={lampIndicatorType(lampStatus)}>
              {lampStatus}
            </StatusIndicator>
          </Box>
        ))}
      </Grid>

      {ambientLux !== undefined && (
        <Box variant="small" color="text-body-secondary" data-testid="lamp-ambient-lux">
          Ambient light: {ambientLux} lx
        </Box>
      )}
    </SpaceBetween>
  );
};

export default LampSelfCheckRenderer;
