// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * EnrollWizard — 7-step OEM1 fleet enroll wizard.
 *
 * Spec § 5.2 | C4 (driver gate) | C12 (no inline 'oem1' literals) |
 * C16 (empty-states) | R17 (quota advisory) | Decision 014 (clientRequestId).
 * T3.2: rendering guard widened to (isOperator || isAdmin); operator fleet scoped
 * to user.fleetIds[0] (defense-in-depth only — server gate is authoritative).
 */

import React, { useReducer, useCallback, useState } from 'react';
import { Wizard } from '@cloudscape-design/components';
import {
  wizardReducer,
  initState,
  clearSession,
  WizardAction,
} from './state/reducer';
import { oem1BulkEnroll } from '@/api/oem1BulkEnroll';
import { useUserRole } from '@/auth/useUserRole';
import StepSource from './steps/Source';
import StepPreflight from './steps/Preflight';
import StepSkuPick from './steps/SkuPick';
import StepDriverAssign from './steps/DriverAssign';
import StepQuotaCheck from './steps/QuotaCheck';
import StepConfirm from './steps/Confirm';
import StepResult from './steps/Result';

// Step indices
const STEP_SOURCE = 0;
const STEP_PREFLIGHT = 1;
const STEP_SKU = 2;
const STEP_DRIVER = 3;
const STEP_QUOTA = 4;
const STEP_CONFIRM = 5;
const STEP_RESULT = 6;

interface DriverOption {
  value: string;
  label: string;
}

interface EnrollWizardProps {
  fleetId: string;
  /** Available drivers for the fleet. Caller is responsible for loading them. */
  drivers?: DriverOption[];
  onClose: () => void;
}

const EnrollWizard: React.FC<EnrollWizardProps> = ({
  fleetId,
  drivers = [],
  onClose,
}) => {
  const { isAdmin, isOperator, fleetIds: userFleetIds } = useUserRole();

  // Defense-in-depth gate: fleet-viewer (and unauthenticated) cannot access the
  // wizard. Server T2.1/T2.4 is the authoritative gate; this is UX only.
  if (!isAdmin && !isOperator) return null;

  // For fleet-operators (non-admin): fleet is locked to their first JWT-derived
  // fleetId. For platform-admin: the caller-supplied fleetId is used as-is
  // (cross-fleet authority — server enforces).
  const effectiveFleetId = (!isAdmin && isOperator)
    ? (userFleetIds[0] ?? fleetId)
    : fleetId;

  const [state, dispatch] = useReducer(wizardReducer, undefined, initState);
  // Ensure fleetId from props is tracked — sync on first render
  const [initialised, setInitialised] = useState(false);
  if (!initialised) {
    if (effectiveFleetId && state.fleetId !== effectiveFleetId) {
      dispatch({ type: 'SET_FLEET', fleetId: effectiveFleetId });
    }
    setInitialised(true);
  }

  const vins = state.rows.map((r) => r.vin);
  const allDriversAssigned =
    state.rows.length > 0 && state.rows.every((r) => r.driverId.trim().length > 0);

  // Submit disabled if: quota is 0 (advisory), or not all drivers assigned (C4)
  const submitDisabled =
    state.quotaRemaining === 0 || !allDriversAssigned || state.rows.length === 0;

  function isStepValid(stepIndex: number): boolean {
    switch (stepIndex) {
      case STEP_SOURCE:
        return vins.length > 0;
      case STEP_PREFLIGHT:
        return vins.length > 0;
      case STEP_SKU:
        return state.sku.length > 0;
      case STEP_DRIVER:
        return allDriversAssigned;
      case STEP_QUOTA:
        return (state.quotaRemaining ?? 1) > 0;
      case STEP_CONFIRM:
        return !submitDisabled;
      default:
        return true;
    }
  }

  const handleNavigate = useCallback(
    ({ detail }: { detail: { requestedStepIndex: number; reason: string } }) => {
      // Allow navigating backward freely; forward requires current step valid
      if (
        detail.reason === 'next' &&
        !isStepValid(state.step)
      ) {
        return;
      }
      dispatch({ type: 'SET_STEP', step: detail.requestedStepIndex });
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [state.step, vins.length, state.sku, allDriversAssigned, state.quotaRemaining],
  );

  async function handleSubmit() {
    if (submitDisabled) return;
    dispatch({ type: 'SET_STEP', step: STEP_RESULT });
    dispatch({ type: 'SET_SUBMIT_STATUS', submitStatus: 'loading' });
    try {
      const result = await oem1BulkEnroll({
        fleetId: state.fleetId,
        vehicleIds: state.rows.map((r) => r.vin),
        sku: state.sku,
        clientRequestId: state.clientRequestId, // stable within session (Decision 014)
      });
      dispatch({ type: 'SET_SUBMIT_RESULT', result });
      clearSession();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Enrollment failed';
      dispatch({ type: 'SET_ERROR', message: msg });
      dispatch({ type: 'SET_SUBMIT_STATUS', submitStatus: 'error' });
    }
  }

  const steps = [
    {
      title: 'Add VINs',
      description: 'Paste VINs or upload a CSV to start enrollment.',
      content: (
        <StepSource currentVins={vins} dispatch={dispatch} />
      ),
    },
    {
      title: 'Pre-flight check',
      description: 'Capability badges per VIN. Server re-runs this on Submit.',
      content: <StepPreflight state={state} dispatch={dispatch} />,
    },
    {
      title: 'Select SKU',
      description: 'Choose the OEM1 product to enroll.',
      content: <StepSkuPick sku={state.sku} dispatch={dispatch} />,
      isOptional: false,
    },
    {
      title: 'Assign drivers',
      description: 'Each VIN requires a driver (C4).',
      content: (
        <StepDriverAssign state={state} dispatch={dispatch} drivers={drivers} />
      ),
    },
    {
      title: 'Check quota',
      description: 'Up to 4 enroll requests per hour (advisory — server enforces, R17).',
      content: <StepQuotaCheck state={state} dispatch={dispatch} />,
    },
    {
      title: 'Confirm',
      description: 'Review before submitting.',
      content: <StepConfirm state={state} />,
    },
    {
      title: 'Result',
      description: 'Enrollment submission status.',
      content: (
        <StepResult state={state} dispatch={dispatch} onClose={onClose} />
      ),
    },
  ];

  return (
    <div
      data-testid="enroll-wizard"
      data-fleet-scope={!isAdmin && isOperator ? 'locked' : 'open'}
      data-fleet-id={effectiveFleetId || undefined}
    >
    <Wizard
      steps={steps}
      activeStepIndex={state.step}
      onNavigate={handleNavigate}
      onSubmit={() => void handleSubmit()}
      onCancel={() => {
        clearSession();
        onClose();
      }}
      isLoadingNextStep={state.preflightStatus === 'loading'}
      submitButtonText="Submit enrollment"
      i18nStrings={{
        stepNumberLabel: (stepNumber) => `Step ${stepNumber}`,
        collapsedStepsLabel: (stepNumber, stepsCount) => `Step ${stepNumber} of ${stepsCount}`,
        skipToButtonLabel: (step) => `Skip to ${step.title}`,
        navigationAriaLabel: 'Enrollment wizard',
        cancelButton: 'Cancel',
        previousButton: 'Previous',
        nextButton: 'Next',
        submitButton: 'Submit enrollment',
        optional: 'optional',
      }}
    />
    </div>
  );
};

export default EnrollWizard;
