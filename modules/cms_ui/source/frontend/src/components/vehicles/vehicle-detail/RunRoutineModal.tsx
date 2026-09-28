// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RunRoutineModal — confirmation modal for the remote SOVD Run Routine operation.
 *
 * HARD GATE H: The Run button is only enabled when connectionStatus === 'connected'
 * (allow-list gate, fail-closed on undefined/null/any other value).
 *
 * HARD GATE I: The operator MUST check the attestation checkbox before the Run
 * button becomes enabled. The checkbox state resets to unchecked on every new
 * modal open. It is NEVER persisted across opens.
 *
 * user_email for the attestation is sourced exclusively from the Cognito id-token
 * claim via useAuth().user.email — it is NEVER read from a user-editable field.
 *
 * The POST is the caller's responsibility. This component collects the operator's
 * attestation and calls onConfirm with the attestation payload; it does not issue
 * any network request itself and does not import sovdScanClient.
 */

import React, { useState, useEffect } from 'react';
import {
  Box,
  Button,
  Checkbox,
  Modal,
  SpaceBetween,
  TextContent,
} from '@cloudscape-design/components';
import { useAuth } from '../../../auth/useAuth';

// The attestation text is a hard-coded constant because it is logged and
// audited verbatim in the backend trail. It describes actuation — the
// operator is commanding this routine to run on this vehicle now — and
// notes that the vehicle's own precondition check may still refuse it.
const ATTESTATION_TEXT =
  'I authorise this routine to run on this vehicle now. ' +
  'I understand the vehicle will perform its own precondition check and may still refuse execution.';

export interface RunRoutineAttestation {
  text: string;
  user_email: string;
  timestamp_ms: number;
}

export interface RunRoutineModalProps {
  visible: boolean;
  onDismiss: () => void;
  /** Called with the attestation payload when the operator confirms. The caller issues the POST. */
  onConfirm: (attestation: RunRoutineAttestation) => void;
  routineId: string;
  routineName?: string;
  connectionStatus?: string | null;
}

const RunRoutineModal: React.FC<RunRoutineModalProps> = ({
  visible,
  onDismiss,
  onConfirm,
  routineId,
  routineName,
  connectionStatus,
}) => {
  // Checkbox state — MUST reset to false on every new modal open (HARD GATE I).
  const [attested, setAttested] = useState(false);

  const { user } = useAuth();

  // Reset attestation checkbox whenever modal becomes visible (false → true).
  // This satisfies the "do NOT persist checkbox state across modal opens" constraint.
  useEffect(() => {
    if (visible) {
      setAttested(false);
    }
  }, [visible]);

  // HARD GATE H: allow-list — only === 'connected' enables the button.
  // Fail-closed on undefined, null, 'disconnected', or any other value.
  const isConnected = connectionStatus === 'connected';

  // Run button is enabled only when BOTH conditions are met:
  //   1. connectionStatus === 'connected'   (HARD GATE H)
  //   2. attestation checkbox is checked    (HARD GATE I)
  const canRun = isConnected && attested;

  const handleConfirm = () => {
    if (!canRun) return;

    // user_email sourced from Cognito claim — NEVER from a user-editable field.
    const userEmail = user?.email ?? '';

    onConfirm({
      text: ATTESTATION_TEXT,
      user_email: userEmail,
      timestamp_ms: Date.now(),
    });
  };

  // Conditionally unmount when not visible so queryByRole('dialog') returns null
  // in test environments that do not compute CSS (jsdom).
  if (!visible) return null;

  const displayName = routineName ?? routineId;

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={`Run Routine: ${displayName}`}
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={handleConfirm}
              disabled={!canRun}
              data-testid="run-routine-confirm-btn"
            >
              Execute routine
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        <TextContent>
          <p>
            Routine ID: <strong>{routineId}</strong>
          </p>
        </TextContent>

        {/* Attestation checkbox — HARD GATE I */}
        <Checkbox
          checked={attested}
          onChange={({ detail }) => setAttested(detail.checked)}
          data-testid="attestation-checkbox"
        >
          {ATTESTATION_TEXT}
        </Checkbox>
      </SpaceBetween>
    </Modal>
  );
};

export default RunRoutineModal;
