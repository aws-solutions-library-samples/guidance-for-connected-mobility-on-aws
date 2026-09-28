// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect } from 'react';
import {
  SpaceBetween,
  Table,
  Badge,
  Alert,
  StatusIndicator,
  Box,
} from '@cloudscape-design/components';
import { oem1Preflight } from '@/api/oem1Preflight';
import type { EnrollWizardState, WizardAction } from '../state/reducer';

interface StepPreflightProps {
  state: EnrollWizardState;
  dispatch: React.Dispatch<WizardAction>;
}

const StepPreflight: React.FC<StepPreflightProps> = ({ state, dispatch }) => {
  const { rows, sku, preflightStatus } = state;
  const vins = rows.map((r) => r.vin);

  useEffect(() => {
    if (preflightStatus !== 'idle' || vins.length === 0 || !sku) return;
    dispatch({ type: 'SET_PREFLIGHT_STATUS', status: 'loading' });
    oem1Preflight({ vins, sku })
      .then((resp) => dispatch({ type: 'SET_PREFLIGHT_RESULTS', results: resp.results }))
      .catch((err: unknown) => {
        const msg = err instanceof Error ? err.message : 'Preflight failed';
        dispatch({ type: 'SET_ERROR', message: msg });
        dispatch({ type: 'SET_PREFLIGHT_STATUS', status: 'error' });
      });
  // Only run when step mounts or when vins/sku change while status is idle
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (preflightStatus === 'loading') {
    return <StatusIndicator type="loading">Running capability check…</StatusIndicator>;
  }

  if (preflightStatus === 'error') {
    return (
      <Alert type="error">
        {state.errorMessage ?? 'Pre-flight check failed. You may proceed to the next step and the server will re-run checks.'}
      </Alert>
    );
  }

  const capable = rows.filter((r) => r.preflightResult?.isCapable !== false);
  const incapable = rows.filter((r) => r.preflightResult?.isCapable === false);

  return (
    <SpaceBetween size="m">
      {incapable.length > 0 && (
        <Alert type="warning">
          {incapable.length} VIN(s) failed capability check. They will be excluded from enrollment.
        </Alert>
      )}

      <Table
        items={rows}
        columnDefinitions={[
          { id: 'vin', header: 'VIN', cell: (r) => r.vin },
          {
            id: 'capability',
            header: 'Capability',
            cell: (r) => {
              if (!r.preflightResult) return <Badge color="grey">Pending</Badge>;
              return r.preflightResult.isCapable
                ? <Badge color="green">Capable</Badge>
                : <Badge color="red">Not capable</Badge>;
            },
          },
          {
            id: 'reason',
            header: 'Reason',
            cell: (r) => r.preflightResult?.reason ?? <Box color="text-body-secondary">—</Box>,
          },
          {
            id: 'make',
            header: 'Make / Model',
            cell: (r) => {
              const mi = r.preflightResult?.modelInfo;
              if (!mi) return <Box color="text-body-secondary">—</Box>;
              return `${mi.make ?? ''} ${mi.model ?? ''} ${mi.year ?? ''}`.trim() || '—';
            },
          },
        ]}
        empty={<Box textAlign="center">No VINs loaded</Box>}
      />

      {preflightStatus === 'done' && (
        <Alert type="info">
          {capable.length} of {rows.length} VINs passed pre-flight. The server will re-run this check on Submit (C3).
        </Alert>
      )}
    </SpaceBetween>
  );
};

export default StepPreflight;
