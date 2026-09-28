// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useRef } from 'react';
import {
  SpaceBetween,
  Alert,
  Box,
  StatusIndicator,
} from '@cloudscape-design/components';
import { oem1EnrollQuota } from '@/api/oem1EnrollQuota';
import type { EnrollWizardState, WizardAction } from '../state/reducer';

const POLL_INTERVAL_MS = 30_000;

interface StepQuotaCheckProps {
  state: EnrollWizardState;
  dispatch: React.Dispatch<WizardAction>;
}

const StepQuotaCheck: React.FC<StepQuotaCheckProps> = ({ state, dispatch }) => {
  const { quotaRemaining } = state;
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  function fetchQuota() {
    oem1EnrollQuota()
      .then((resp) => dispatch({ type: 'SET_QUOTA', remaining: resp.remaining }))
      .catch(() => {
        // advisory — do not block UI on error (R17)
      });
  }

  useEffect(() => {
    fetchQuota();
    timerRef.current = setInterval(fetchQuota, POLL_INTERVAL_MS);
    return () => {
      if (timerRef.current !== null) clearInterval(timerRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const loading = quotaRemaining === null;

  return (
    <SpaceBetween size="m">
      {loading ? (
        <StatusIndicator type="loading">Checking enroll quota…</StatusIndicator>
      ) : quotaRemaining === 0 ? (
        <Alert type="error">
          <strong>0 of 4 enroll requests remaining this hour.</strong> Submit is disabled.
          The quota resets on the hour. This check is advisory — the server enforces the limit (R17).
        </Alert>
      ) : (
        <Alert type="success">
          <strong>{quotaRemaining} of 4 enroll request{quotaRemaining !== 1 ? 's' : ''} remaining</strong> this hour.
        </Alert>
      )}

      <Box color="text-body-secondary" fontSize="body-s">
        Quota is checked every 30 s. The server enforces the hard 4-req/hour limit (C1 / R17).
        If two users submit simultaneously both may see quota available — the last request will receive a 429.
      </Box>
    </SpaceBetween>
  );
};

export default StepQuotaCheck;
