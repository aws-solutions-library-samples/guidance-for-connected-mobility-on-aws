// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// DispatchModal — T3.2: "Dispatch to service" flow.
//
// Spec: `.kiro/specs/2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5` T3.2
//
// Opens from the Diagnostics tab (fleet-operator or platform-admin only — gated
// in VehicleDiagnosticsPanel before this modal is shown; the modal itself is
// defensive-only).
//
// Calls CMS POST /api/dispatch proxy — NEVER the DMS API directly (CORS + JWT
// leak risk, spec T3.3 constraint).  On success: Flashbar success toast in the
// modal, with a link to the new RO on DMS's fleet repair-order page.
//
// DMS RO URL shape: `<dms origin>/fleet/repair-orders/<roId>` (DMS spec
// 2026-09-25-dms-fleet-ro-page), built by utils/dmsLinks.ts. The earlier
// `/service?ro_id=` link was wrong twice over: DMS's /service ignores ro_id, and
// it is gated to dealer groups, so a fleet operator is denied there.
//
// Caller info: read from Cognito id-token via useAuth().user — same pattern as
// ClearDTCModal and RunRoutineModal. user_email is NEVER sourced from a
// user-editable field.

import React, { useState, useEffect } from 'react';
import {
  Alert,
  Box,
  Button,
  ExpandableSection,
  Flashbar,
  FormField,
  Modal,
  RadioGroup,
  Select,
  SpaceBetween,
  Textarea,
} from '@cloudscape-design/components';
import type { FlashbarProps, SelectProps } from '@cloudscape-design/components';
import { getApiEndpoint } from '../../../config/api';
import { useAuth } from '../../../auth/useAuth';
import { authFetch } from '../../../utils/authFetch';
import { useDealerOptions, dealerPlaceholder } from '../../../hooks/useDealerOptions';
import { CLOUD_THRESHOLD_SOURCE } from '../../../utils/dispatchDtcs';
import { dmsFleetRepairOrderUrl } from '../../../utils/dmsLinks';

// ── Types ──────────────────────────────────────────────────────────────────────

/** One DTC code seen in the current session. */
export interface DispatchDtcEntry {
  code: string;
  description?: string;
  ecu?: string;
  /**
   * True when the code comes from the vehicle's ACTIVE DTC record and not from
   * the latest diagnostic scan (e.g. a cloud threshold detection, which no ECU
   * stores). Display only; the dispatch body still sends codes.
   */
  recorded?: boolean;
  /**
   * For a `recorded` code: the `source` of every ACTIVE record row for it, in
   * record order. A code can have both a cloud threshold row and an ECU row.
   * Display only.
   */
  sources?: string[];
}

/** One routine run during the current session. */
export interface DispatchRoutineEntry {
  routineId: string;
  status?: string;
  submittedAt?: string;
}

/** A user-authored note on the session. */
export interface DispatchNoteEntry {
  text: string;
  createdAt?: string;
}

/** Evidence summary passed from the Diagnostics tab. */
export interface DispatchEvidence {
  sessionId: string;
  dtcs: DispatchDtcEntry[];
  routinesRun: DispatchRoutineEntry[];
  notes: DispatchNoteEntry[];
}

export interface DispatchModalProps {
  visible: boolean;
  onDismiss: () => void;
  /** Called after a successful dispatch so the parent can update its session log. */
  onSuccess: (roId: string) => void;
  vehicleId: string;
  /** VIN sourced from the vehicle record; required for the dispatch payload. */
  vin: string;
  /** Evidence from the current session — DTCs, routines run, notes. */
  evidence: DispatchEvidence;
}

// ── Priority options (ordered low→high for the radio group) ───────────────────

const PRIORITY_OPTIONS = [
  { value: 'Normal', label: 'Normal', description: 'Routine service window' },
  { value: 'Urgent', label: 'Urgent', description: 'Address within 48 hours' },
  { value: 'Critical', label: 'Critical', description: 'Address immediately' },
];

// ── DMS repair-order link ─────────────────────────────────────────────────────
//
// `dmsFleetRepairOrderUrl` (utils/dmsLinks.ts) reads runtimeConfig.dmsUiOrigin,
// which `make regenerate-runtime-config` writes from the stage's
// DMS_UI_CALLBACK_ORIGIN, rather than a hardcoded host: the internal hostname
// otherwise baked into the bundle, which the pre-sync secret scanner rejects.
// When the origin is absent it returns null and the success flashbar renders
// without the link.

// ── Component ──────────────────────────────────────────────────────────────────

const DispatchModal: React.FC<DispatchModalProps> = ({
  visible,
  onDismiss,
  onSuccess,
  vehicleId,
  vin,
  evidence,
}) => {
  const { user, getAuthHeaders } = useAuth();
  const apiEndpoint = getApiEndpoint();

  // Fetch dealer options only when the modal is open (avoids a needless call on
  // every Diagnostics tab mount).
  const { options: dealerOptions, status: dealerStatus, errorMessage: dealerError } =
    useDealerOptions(visible);

  // ── Form state ────────────────────────────────────────────────────────────────

  const [selectedDealer, setSelectedDealer] = useState<SelectProps.Option | null>(null);
  const [priority, setPriority] = useState<string>('Normal');
  const [complaint, setComplaint] = useState<string>('');

  // ── Submission state ──────────────────────────────────────────────────────────

  const [submitting, setSubmitting] = useState(false);
  const [notifications, setNotifications] = useState<FlashbarProps.MessageDefinition[]>([]);
  const [fieldError, setFieldError] = useState<string | null>(null);

  // ── Reset on open ─────────────────────────────────────────────────────────────
  //
  // Re-populate the complaint textarea with the prefill each time the modal
  // opens.  The prefill uses the first active DTC and the caller's email —
  // both are authoritative at modal-open time and should not drift if the
  // parent's props change while the modal is closed.

  useEffect(() => {
    if (!visible) return;

    // Reset form state
    setSelectedDealer(null);
    setPriority('Normal');
    setFieldError(null);
    setNotifications([]);
    setSubmitting(false);

    // Prefill complaint: "Fleet-detected <dtc> — dispatched by <email>"
    // If multiple DTCs, use the first.  If none, omit the DTC portion.
    const callerEmail = user?.email ?? '';
    const primaryDtc = evidence.dtcs.length > 0 ? evidence.dtcs[0].code : null;
    const prefill = primaryDtc
      ? `Fleet-detected ${primaryDtc} — dispatched by ${callerEmail}`
      : `Fleet diagnostic dispatch — by ${callerEmail}`;
    setComplaint(prefill);
  }, [visible]); // eslint-disable-line react-hooks/exhaustive-deps

  // ── Dispatch handler ──────────────────────────────────────────────────────────

  const handleDispatch = async () => {
    // Validation: dealer is required.
    if (!selectedDealer) {
      setFieldError('Please select a service centre before dispatching.');
      return;
    }
    setFieldError(null);
    setSubmitting(true);
    setNotifications([]);

    const body = {
      vehicleId,
      vin,
      dealer_id: selectedDealer.value,
      complaint,
      priority,
      // Structured evidence — persisted by T2.7 on the DMS RO.
      evidence: {
        sessionId: evidence.sessionId,
        dtcs: evidence.dtcs.map((d) => d.code),
        routinesRun: evidence.routinesRun.map((r) => ({
          routineId: r.routineId,
          status: r.status,
          submittedAt: r.submittedAt,
        })),
        notes: evidence.notes.map((n) => n.text),
      },
    };

    try {
      let res: Response;
      try {
        res = await authFetch(`${apiEndpoint}api/dispatch`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
      } catch (_networkErr) {
        // Network error — surface once, do not retry silently.
        setNotifications([{
          type: 'error',
          content: 'Network error. Check your connection and try again.',
          dismissible: true,
          onDismiss: () => setNotifications([]),
        }]);
        setSubmitting(false);
        return;
      }

      if (res.ok) {
        let roId: string | undefined;
        try {
          const data = await res.json();
          roId = data?.ro_id ?? data?.roId ?? undefined;
        } catch {
          // JSON parse failed — treat as success with no RO id.
        }

        const roLink = roId ? dmsFleetRepairOrderUrl(roId) : null;

        setNotifications([{
          type: 'success',
          content: 'Repair order created.',
          dismissible: true,
          onDismiss: () => setNotifications([]),
          // Include a link action only when we have the RO id.
          ...(roLink ? {
            action: (
              <Button
                variant="inline-link"
                iconName="external"
                iconAlign="right"
                href={roLink}
                target="_blank"
                data-testid="dispatch-ro-link"
              >
                Open RO in DMS
              </Button>
            ),
          } : {}),
        }]);

        if (roId) {
          onSuccess(roId);
        } else {
          onSuccess('');
        }
        return;
      }

      // 4xx / 5xx — render the response body verbatim.
      let errorBody: string;
      try {
        const json = await res.json();
        errorBody = typeof json === 'object' ? JSON.stringify(json) : String(json);
      } catch {
        errorBody = await res.text().catch(() => `HTTP ${res.status}`);
      }
      setNotifications([{
        type: 'error',
        content: errorBody,
        dismissible: true,
        onDismiss: () => setNotifications([]),
      }]);
    } finally {
      setSubmitting(false);
    }
  };

  // ── Evidence summary section ──────────────────────────────────────────────────
  //
  // Shows counts (e.g. "3 DTCs") as the expandable header; expands to show each
  // item verbatim. Neither invented copy nor placeholder values.

  const dtcCount = evidence.dtcs.length;
  const recordDtcCount = evidence.dtcs.filter((d) => d.recorded).length;
  const scanDtcCount = dtcCount - recordDtcCount;
  const routineCount = evidence.routinesRun.length;
  const noteCount = evidence.notes.length;

  // FG3: codes from the vehicle's fault record are not session activity, so
  // the summary counts them separately from the scan's.
  const plural = (n: number) => (n !== 1 ? 's' : '');
  const dtcSummary =
    recordDtcCount === 0
      ? dtcCount > 0 && `${dtcCount} DTC${plural(dtcCount)}`
      : scanDtcCount === 0
        ? `${recordDtcCount} DTC${plural(recordDtcCount)} from the fault record`
        : `${scanDtcCount} DTC${plural(scanDtcCount)} from scan, ${recordDtcCount} from the fault record`;

  const evidenceSummary = [
    dtcSummary,
    routineCount > 0 && `${routineCount} routine${routineCount !== 1 ? 's' : ''} run`,
    noteCount > 0 && `${noteCount} note${noteCount !== 1 ? 's' : ''}`,
  ].filter(Boolean).join(', ') || 'No session activity';

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header="Dispatch to service"
      data-testid="dispatch-modal"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button
              variant="link"
              onClick={onDismiss}
              disabled={submitting}
              data-testid="dispatch-cancel-button"
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={handleDispatch}
              loading={submitting}
              disabled={submitting || !selectedDealer}
              data-testid="dispatch-submit-button"
            >
              Dispatch
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">

        {/* Success / error flash */}
        {notifications.length > 0 && (
          <Flashbar items={notifications} />
        )}

        {/* (a) Dealership dropdown */}
        <FormField
          label="Service centre"
          errorText={
            fieldError ??
            (dealerStatus === 'error' ? dealerError : undefined)
          }
          data-testid="dispatch-dealer-field"
        >
          <Select
            options={dealerOptions}
            selectedOption={selectedDealer}
            onChange={({ detail }) => {
              setSelectedDealer(detail.selectedOption);
              if (fieldError) setFieldError(null);
            }}
            placeholder={dealerPlaceholder(dealerStatus)}
            statusType={
              dealerStatus === 'loading' ? 'loading' :
              dealerStatus === 'error' ? 'error' :
              'finished'
            }
            loadingText="Loading service centres…"
            errorText={dealerStatus === 'error' ? dealerError : undefined}
            disabled={dealerStatus === 'loading' || dealerStatus === 'error'}
            data-testid="dispatch-dealer-select"
          />
        </FormField>

        {/* (b) Priority radio group */}
        <FormField
          label="Priority"
          data-testid="dispatch-priority-field"
        >
          <RadioGroup
            value={priority}
            onChange={({ detail }) => setPriority(detail.value)}
            items={PRIORITY_OPTIONS}
            data-testid="dispatch-priority-radio"
          />
        </FormField>

        {/* (c) Editable complaint textarea — prefilled on open */}
        <FormField
          label="Complaint"
          description="Describe the issue to be addressed. Pre-filled from the active fault code."
          data-testid="dispatch-complaint-field"
        >
          <Textarea
            value={complaint}
            onChange={({ detail }) => setComplaint(detail.value)}
            rows={3}
            placeholder="Describe the fault or concern…"
            data-testid="dispatch-complaint-textarea"
          />
        </FormField>

        {/* (d) Evidence summary — counts expandable to item lists */}
        <FormField
          label="Session evidence"
          description={
            recordDtcCount > 0
              ? "The latest scan's fault codes, the vehicle's recorded fault codes, and this session's routines and notes will be attached to the repair order."
              : "The latest scan's fault codes and this session's routines and notes will be attached to the repair order."
          }
          data-testid="dispatch-evidence-field"
        >
          <Box data-testid="dispatch-evidence-summary">
            <Box variant="p" color={dtcCount + routineCount + noteCount > 0 ? undefined : 'text-status-inactive'}>
              {evidenceSummary}
            </Box>

            {dtcCount > 0 && (
              <ExpandableSection
                headerText={`${dtcCount} fault code${dtcCount !== 1 ? 's' : ''}`}
                data-testid="dispatch-evidence-dtcs"
              >
                {evidence.dtcs.map((d, i) => (
                  <Box key={d.code ?? i} variant="p">
                    {d.code}{d.description ? ` — ${d.description}` : ''}
                    {d.ecu ? ` (${d.ecu})` : ''}
                    {d.recorded ? (
                      <Box variant="small" color="text-body-secondary" data-testid={`dispatch-dtc-recorded-${d.code}`}>
                        From the vehicle&apos;s fault record, not from a diagnostic scan.
                        {d.sources?.includes(CLOUD_THRESHOLD_SOURCE)
                          ? ' Raised in the cloud from telemetry.'
                          : ''}
                      </Box>
                    ) : null}
                  </Box>
                ))}
              </ExpandableSection>
            )}

            {routineCount > 0 && (
              <ExpandableSection
                headerText={`${routineCount} routine${routineCount !== 1 ? 's' : ''} run`}
                data-testid="dispatch-evidence-routines"
              >
                {evidence.routinesRun.map((r, i) => (
                  <Box key={r.routineId ?? i} variant="p">
                    {r.routineId}
                    {r.status ? ` → ${r.status}` : ''}
                    {r.submittedAt ? ` (${r.submittedAt})` : ''}
                  </Box>
                ))}
              </ExpandableSection>
            )}

            {noteCount > 0 && (
              <ExpandableSection
                headerText={`${noteCount} note${noteCount !== 1 ? 's' : ''}`}
                data-testid="dispatch-evidence-notes"
              >
                {evidence.notes.map((n, i) => (
                  <Box key={i} variant="p">
                    {n.text}
                  </Box>
                ))}
              </ExpandableSection>
            )}
          </Box>
        </FormField>

        {/* Non-dismissible tip about the proxy path */}
        <Alert type="info" data-testid="dispatch-proxy-note">
          The repair order will be created in the DMS Service Lane. Diagnostics can
          continue after dispatch — the technician will see this session's evidence
          when they open the repair order.
        </Alert>

      </SpaceBetween>
    </Modal>
  );
};

export default DispatchModal;
