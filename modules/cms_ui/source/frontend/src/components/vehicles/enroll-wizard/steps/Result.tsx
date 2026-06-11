// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  SpaceBetween,
  Alert,
  StatusIndicator,
  KeyValuePairs,
  Button,
  Box,
} from '@cloudscape-design/components';
import type { EnrollWizardState, WizardAction } from '../state/reducer';

interface SubmitResult {
  requestId?: string | number;
  acceptedCount?: number;
  preFlightFailureCount?: number;
  enrollmentStatus?: string;
}

interface StepResultProps {
  state: EnrollWizardState;
  dispatch: React.Dispatch<WizardAction>;
  onClose: () => void;
}

const StepResult: React.FC<StepResultProps> = ({ state, dispatch, onClose }) => {
  const { submitStatus, submitResult, errorMessage } = state;
  const result = submitResult as SubmitResult | null;

  if (submitStatus === 'loading') {
    return <StatusIndicator type="loading">Submitting enrollment request…</StatusIndicator>;
  }

  if (submitStatus === 'error') {
    return (
      <SpaceBetween size="m">
        <Alert type="error">
          {errorMessage ?? 'Enrollment submission failed. You may retry — the clientRequestId ensures no duplicate OEM1 call.'}
        </Alert>
        <Button onClick={() => dispatch({ type: 'SET_STEP', step: 5 })}>← Back to Confirm</Button>
      </SpaceBetween>
    );
  }

  if (submitStatus === 'done' && result) {
    return (
      <SpaceBetween size="m">
        <Alert type="success">
          Enrollment request submitted. OEM1 processes requests asynchronously — completion takes up to 7 days (C2).
        </Alert>

        <KeyValuePairs
          columns={2}
          items={[
            { label: 'OEM1 request ID', value: String(result.requestId ?? '—') },
            { label: 'Accepted VINs', value: String(result.acceptedCount ?? '—') },
            { label: 'Pre-flight failures', value: String(result.preFlightFailureCount ?? 0) },
            { label: 'Status', value: result.enrollmentStatus ?? 'IN_PROGRESS' },
          ]}
        />

        <Box>
          <Button onClick={onClose} variant="primary">
            Close
          </Button>
          <Button onClick={() => dispatch({ type: 'RESET' })} variant="inline-link">
            Enroll another batch
          </Button>
        </Box>
      </SpaceBetween>
    );
  }

  return <Box color="text-body-secondary">No result yet.</Box>;
};

export default StepResult;
