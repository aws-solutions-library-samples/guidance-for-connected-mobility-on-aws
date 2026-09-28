// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ClearDTCModal — confirmation modal for the remote SOVD Clear DTC operation.
 *
 * HARD GATE H: The Clear button is only enabled when connectionStatus === 'connected'
 * (allow-list gate, fail-closed on undefined/null/any other value).
 *
 * HARD GATE I: The operator MUST check "I have verified the underlying repair is
 * complete" before the Clear button becomes enabled. The checkbox state is included
 * in the attestation payload sent to the API and the checkbox is reset to unchecked
 * on every new modal open.
 *
 * user_email for the attestation is sourced exclusively from the Cognito id-token
 * claim via useAuth().user.email — it is NEVER read from a user-editable field.
 */

import React, { useState, useEffect } from 'react';
import {
  Alert,
  Box,
  Button,
  Checkbox,
  KeyValuePairs,
  Modal,
  SpaceBetween,
} from '@cloudscape-design/components';
import { getApiEndpoint } from '../../../config/api';
import { useAuth } from '../../../auth/useAuth';
import { authFetch } from '../../../utils/authFetch';

// The attestation text is a hard-coded constant because it is also logged
// verbatim in the backend audit trail and must be byte-identical on both sides.
const ATTESTATION_TEXT = 'I have verified the underlying repair is complete';

interface DtcInfo {
  code: string;
  description?: string;
  firstSeenAt?: number;
  occurrenceCount?: number;
  ecu?: string;
}

interface ClearDTCModalProps {
  visible: boolean;
  onDismiss: () => void;
  onSuccess: () => void;
  dtc: DtcInfo;
  vehicleId: string;
  connectionStatus?: string | null;
}

/** Format epoch-ms (or epoch-s) as a locale string, returning 'N/A' on bad input. */
function formatEpochMs(ms?: number): string {
  if (typeof ms !== 'number' || ms <= 0) return 'N/A';
  const normalized = ms > 9_999_999_999 ? ms : ms * 1000;
  const d = new Date(normalized);
  return isNaN(d.getTime()) ? 'N/A' : d.toLocaleString();
}

const ClearDTCModal: React.FC<ClearDTCModalProps> = ({
  visible,
  onDismiss,
  onSuccess,
  dtc,
  vehicleId,
  connectionStatus,
}) => {
  // Checkbox state — MUST reset to false on every new modal open (HARD GATE I).
  const [attested, setAttested] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const { user } = useAuth();
  const apiEndpoint = getApiEndpoint();

  // Reset attestation checkbox whenever modal becomes visible (false → true).
  // This satisfies the "do NOT persist checkbox state across modal opens" constraint.
  useEffect(() => {
    if (visible) {
      setAttested(false);
      setError(null);
    }
  }, [visible]);

  // HARD GATE H: allow-list — only === 'connected' enables the button.
  // Fail-closed on undefined, null, 'disconnected', or any other value.
  const isConnected = connectionStatus === 'connected';

  // Clear button is enabled only when BOTH conditions are met:
  //   1. connectionStatus === 'connected'   (HARD GATE H)
  //   2. attestation checkbox is checked    (HARD GATE I)
  const canClear = isConnected && attested;

  const handleClear = async () => {
    if (!canClear || !vehicleId) return;
    setSubmitting(true);
    setError(null);

    // user_email sourced from Cognito claim — NEVER from a user-editable field.
    const userEmail = user?.email ?? '';

    const body = {
      command_type: 'clear_dtcs',
      components: [dtc.ecu ?? 'UNKNOWN'],
      attestation: {
        text: ATTESTATION_TEXT,
        user_email: userEmail,
        timestamp_ms: Date.now(),
      },
    };

    const url = `${apiEndpoint}api/commands/${vehicleId}`;

    /**
     * Inner attempt: fire the request, return the Response or throw on network error.
     */
    const attempt = async (): Promise<Response> => {
      return authFetch(url, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
    };

    try {
      let res: Response;
      try {
        res = await attempt();
      } catch (_networkErr) {
        // Network error (no response) — retry once with 500 ms backoff.
        await new Promise(resolve => setTimeout(resolve, 500));
        try {
          res = await attempt();
        } catch (retryErr: unknown) {
          const msg = retryErr instanceof Error ? retryErr.message : 'Network error';
          setError(`Network error after retry: ${msg}`);
          return;
        }
      }

      if (res.ok) {
        // Success — notify parent and close.
        onSuccess();
        onDismiss();
        return;
      }

      // 4xx / 5xx — display response body verbatim.
      let errorBody: string;
      try {
        const json = await res.json();
        errorBody = typeof json === 'object' ? JSON.stringify(json) : String(json);
      } catch {
        errorBody = await res.text().catch(() => `HTTP ${res.status}`);
      }
      setError(errorBody);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header="Clear Diagnostic Trouble Code"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss} disabled={submitting}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={handleClear}
              disabled={!canClear || submitting}
              loading={submitting}
              data-testid="clear-dtc-confirm-btn"
            >
              Clear DTC
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {/* DTC summary card — all 5 required fields */}
        <KeyValuePairs
          columns={2}
          items={[
            { label: 'DTC Code',          value: dtc.code ?? 'N/A' },
            { label: 'ECU',               value: dtc.ecu ?? 'N/A' },
            { label: 'Description',       value: dtc.description ?? 'N/A' },
            { label: 'First Seen',        value: formatEpochMs(dtc.firstSeenAt) },
            { label: 'Occurrence Count',  value: String(dtc.occurrenceCount ?? 'N/A') },
          ]}
        />

        {/* Attestation checkbox — HARD GATE I */}
        <Checkbox
          checked={attested}
          onChange={({ detail }) => setAttested(detail.checked)}
          data-testid="attestation-checkbox"
        >
          {ATTESTATION_TEXT}
        </Checkbox>

        {/* Error alert — shown verbatim on 4xx or network-retry failure */}
        {error && (
          <Alert
            type="error"
            dismissible
            onDismiss={() => setError(null)}
          >
            {error}
          </Alert>
        )}
      </SpaceBetween>
    </Modal>
  );
};

export default ClearDTCModal;
