// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useCallback, useEffect, useRef, useState } from 'react';
import Alert from '@cloudscape-design/components/alert';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Checkbox from '@cloudscape-design/components/checkbox';
import FormField from '@cloudscape-design/components/form-field';
import Input from '@cloudscape-design/components/input';
import Modal from '@cloudscape-design/components/modal';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';

import { oem1BulkUnenroll, BulkUnenrollError } from '@/api/oem1BulkUnenroll';
import { oem1EnrollQuota } from '@/api/oem1EnrollQuota';
import { useUserRole } from '@/auth/useUserRole';
import { VehicleItem } from '@/types/fleet-types';

/**
 * Hard-coded threshold per spec § 5.3 — NOT configurable in v1.
 */
const TYPED_CONFIRMATION_THRESHOLD = 10;

export interface BulkUnenrollModalProps {
  visible: boolean;
  vehicles: VehicleItem[];
  fleetId: string;
  fleetName: string;
  onDismiss: () => void;
  /** Called on successful submission. Receives the OEM1 requestId. */
  onSuccess?: (requestId: string) => void;
}

/**
 * Derive the unique SKU from the selected vehicles.
 * Returns the SKU string if all vehicles share the same oem1_active_sku,
 * or null if the selection is heterogeneous (decision 2026-06-05-005).
 */
function deriveSku(vehicles: VehicleItem[]): string | null {
  const skus = new Set(vehicles.map((v) => v.oem1_active_sku).filter(Boolean));
  if (skus.size === 1) return [...skus][0] as string;
  return null; // heterogeneous or empty
}

export function BulkUnenrollModal({
  visible,
  vehicles,
  fleetId,
  fleetName,
  onDismiss,
  onSuccess,
}: BulkUnenrollModalProps) {
  const { isAdmin, isOperator, fleetIds: userFleetIds } = useUserRole();

  // Rendering guard: only admin or operator may use this modal.
  if (!isAdmin && !isOperator) return null;

  // For non-admins, pre-filter vehicles to those in the user's fleet scope.
  const scopedVehicles = isAdmin
    ? vehicles
    : vehicles.filter((v) => !v.fleetId || userFleetIds.includes(v.fleetId));

  // Defense-in-depth: detect vehicles that are outside the user's fleet scope.
  // We always show an error rather than silently dropping them (per spec constraint).
  const outOfScopeVehicles = isAdmin
    ? []
    : vehicles.filter((v) => v.fleetId && !userFleetIds.includes(v.fleetId));

  const sku = deriveSku(scopedVehicles);
  const heterogeneous = scopedVehicles.length > 0 && sku === null;

  const requiresConfirmation = scopedVehicles.length >= TYPED_CONFIRMATION_THRESHOLD;
  const [confirmationText, setConfirmationText] = useState('');
  const [hardDelete, setHardDelete] = useState(false); // C9: default off
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // Quota banner
  const [quotaRemaining, setQuotaRemaining] = useState<number | null>(null);

  // Stable clientRequestId for the lifetime of this modal session (per spec).
  const clientRequestIdRef = useRef<string>(crypto.randomUUID());

  // Reset state whenever modal opens.
  useEffect(() => {
    if (visible) {
      setConfirmationText('');
      setHardDelete(false);
      setSubmitError(null);
      setSubmitting(false);
      clientRequestIdRef.current = crypto.randomUUID();
      void oem1EnrollQuota()
        .then((r) => setQuotaRemaining(r.remaining))
        .catch(() => setQuotaRemaining(null));
    }
  }, [visible]);

  const confirmationMatches =
    !requiresConfirmation || confirmationText.trim() === fleetName;

  // Defense-in-depth: block submit if any out-of-scope vehicle is present.
  const hasOutOfScopeVehicles = outOfScopeVehicles.length > 0;

  const canSubmit =
    !heterogeneous &&
    !hasOutOfScopeVehicles &&
    scopedVehicles.length > 0 &&
    sku !== null &&
    confirmationMatches &&
    !submitting;

  const handleSubmit = useCallback(async () => {
    if (!canSubmit || sku === null) return;

    setSubmitting(true);
    setSubmitError(null);

    try {
      const vins = scopedVehicles
        .map((v) => v.vehicleId ?? v.vin)
        .filter((id): id is string => Boolean(id));

      const result = await oem1BulkUnenroll({
        fleetId,
        vehicleIds: vins,
        sku,
        clientRequestId: clientRequestIdRef.current,
        hardDelete,
      });

      onSuccess?.(result.requestId);
    } catch (err) {
      const message =
        err instanceof BulkUnenrollError
          ? err.message
          : 'An unexpected error occurred.';
      setSubmitError(message);
    } finally {
      setSubmitting(false);
    }
  }, [canSubmit, sku, scopedVehicles, fleetId, hardDelete, onSuccess]);

  return (
    <Modal
      visible={visible}
      onDismiss={onDismiss}
      header={`Unenroll ${scopedVehicles.length} vehicle${scopedVehicles.length !== 1 ? 's' : ''}`}
      closeAriaLabel="Close unenroll dialog"
      footer={
        <Box float="right">
          <SpaceBetween direction="horizontal" size="xs">
            <Button variant="link" onClick={onDismiss} disabled={submitting}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => void handleSubmit()}
              disabled={!canSubmit}
              loading={submitting}
              data-testid="submit-unenroll"
            >
              Unenroll
            </Button>
          </SpaceBetween>
        </Box>
      }
    >
      <SpaceBetween size="m">
        {/* Selection summary */}
        <Box>
          <strong>
            {scopedVehicles.length} vehicle{scopedVehicles.length !== 1 ? 's' : ''}
          </strong>{' '}
          selected from fleet <strong>{fleetName}</strong>
          {sku && (
            <>
              {' '}
              · SKU: <strong>{sku}</strong>
            </>
          )}
        </Box>

        {/* Defense-in-depth: out-of-scope vehicles must not be silently dropped */}
        {hasOutOfScopeVehicles && (
          <Alert
            type="error"
            data-testid="out-of-scope-error"
            header="Vehicles outside your fleet scope"
          >
            You can't un-enroll {outOfScopeVehicles.length} vehicle
            {outOfScopeVehicles.length !== 1 ? 's' : ''} outside your fleet scope.
            Remove them from your selection to proceed.
          </Alert>
        )}

        {/* Heterogeneous-SKU error (decision 2026-06-05-005) */}
        {heterogeneous && (
          <Alert
            type="error"
            data-testid="heterogeneous-sku-error"
            header="Multiple SKUs selected"
          >
            Selected vehicles have different active SKUs. Unenroll one SKU at a
            time.
          </Alert>
        )}

        {/* Mandatory billing / timing copy (spec § 5.3 / C2) */}
        {!heterogeneous && !hasOutOfScopeVehicles && (
          <Alert type="warning" data-testid="billing-warning">
            Subscription billing stops on submission. Unenrollment may take up
            to 7 days to complete.
          </Alert>
        )}

        {/* Quota banner */}
        {quotaRemaining !== null && (
          <StatusIndicator
            type={quotaRemaining > 0 ? 'info' : 'warning'}
            data-testid="quota-banner"
          >
            {quotaRemaining} OEM1 unenroll request
            {quotaRemaining !== 1 ? 's' : ''} remaining this hour
          </StatusIndicator>
        )}

        {/* Hard-delete checkbox — default OFF (C9) */}
        <Checkbox
          checked={hardDelete}
          onChange={({ detail }) => setHardDelete(detail.checked)}
          data-testid="hard-delete-checkbox"
        >
          Permanently delete vehicle records (irreversible)
        </Checkbox>

        {/* Typed-confirmation gate for ≥10 vehicles (spec § 5.3) */}
        {requiresConfirmation && (
          <FormField
            label={
              <>
                Type the fleet name <strong>{fleetName}</strong> to confirm
              </>
            }
            data-testid="confirmation-field"
          >
            <Input
              value={confirmationText}
              onChange={({ detail }) => setConfirmationText(detail.value)}
              placeholder={fleetName}
              ariaLabel="Type fleet name to confirm unenroll"
              data-testid="confirmation-input"
            />
          </FormField>
        )}

        {/* Submission error */}
        {submitError && (
          <Alert type="error" data-testid="submit-error">
            {submitError}
          </Alert>
        )}
      </SpaceBetween>
    </Modal>
  );
}
