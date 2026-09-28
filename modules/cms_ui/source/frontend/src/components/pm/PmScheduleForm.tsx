// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PmScheduleForm — create or edit a PM schedule.
 *
 * Covers all three bases (mileage, engine_hours, calendar) as required by
 * spec T4.2. Calls POST /api/v1/fleet-intelligence/pm/schedules for both
 * create and update (the handler treats a matching vehicleId+taskCode as upsert).
 */

import React, { useState } from "react";
import {
  Alert,
  Button,
  Container,
  Form,
  FormField,
  Header,
  Input,
  Select,
  SpaceBetween,
} from "@cloudscape-design/components";
import type { SelectProps } from "@cloudscape-design/components";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import type { PmBasis, PmSchedule, PmSchedulePayload } from "./types";
import { PM_BASIS_LABELS } from "./types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PmScheduleFormProps {
  /** When provided, the form is editing an existing schedule */
  initialValues?: Partial<PmSchedule>;
  /** Called after a successful save */
  onSave?: () => void;
  /** Called when the user cancels */
  onCancel?: () => void;
}

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const BASIS_OPTIONS: SelectProps.Option[] = [
  { value: "mileage", label: PM_BASIS_LABELS.mileage },
  { value: "engine_hours", label: PM_BASIS_LABELS.engine_hours },
  { value: "calendar", label: PM_BASIS_LABELS.calendar },
];

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const PmScheduleForm: React.FC<PmScheduleFormProps> = ({
  initialValues,
  onSave,
  onCancel,
}) => {
  const isEdit = !!initialValues?.scheduleId;

  const [vehicleId, setVehicleId] = useState(initialValues?.vehicleId ?? "");
  const [taskCode, setTaskCode] = useState(initialValues?.taskCode ?? "");
  const [taskDescription, setTaskDescription] = useState(
    initialValues?.taskDescription ?? ""
  );
  const [basis, setBasis] = useState<PmBasis>(
    initialValues?.basis ?? "mileage"
  );
  const [intervalValue, setIntervalValue] = useState(
    String(initialValues?.intervalValue ?? "")
  );
  const [lastPerformedAt, setLastPerformedAt] = useState(
    initialValues?.lastPerformedAt ?? ""
  );
  const [lastMileage, setLastMileage] = useState(
    initialValues?.lastPerformedMileage != null
      ? String(initialValues.lastPerformedMileage)
      : ""
  );
  const [lastHours, setLastHours] = useState(
    initialValues?.lastPerformedEngineHours != null
      ? String(initialValues.lastPerformedEngineHours)
      : ""
  );

  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // ---------------------------------------------------------------------------
  // Validation
  // ---------------------------------------------------------------------------

  const [vehicleIdError, setVehicleIdError] = useState("");
  const [taskCodeError, setTaskCodeError] = useState("");
  const [intervalError, setIntervalError] = useState("");

  const validate = (): boolean => {
    let valid = true;

    if (!vehicleId.trim()) {
      setVehicleIdError("Vehicle ID is required");
      valid = false;
    } else {
      setVehicleIdError("");
    }

    if (!taskCode.trim()) {
      setTaskCodeError("Task code is required");
      valid = false;
    } else {
      setTaskCodeError("");
    }

    const parsed = Number(intervalValue);
    if (!intervalValue || isNaN(parsed) || parsed <= 0) {
      setIntervalError("Interval must be a positive number");
      valid = false;
    } else {
      setIntervalError("");
    }

    return valid;
  };

  // ---------------------------------------------------------------------------
  // Submit
  // ---------------------------------------------------------------------------

  const handleSubmit = () => {
    if (!validate()) return;

    setSaving(true);
    setError(null);

    const payload: PmSchedulePayload = {
      vehicleId: vehicleId.trim(),
      taskCode: taskCode.trim(),
      taskDescription: taskDescription.trim() || undefined,
      basis,
      intervalValue: Number(intervalValue),
    };

    // Basis-specific last-performed fields
    if (basis === "calendar" && lastPerformedAt) {
      payload.lastPerformedAt = lastPerformedAt;
    }
    if (basis === "mileage" && lastMileage) {
      payload.lastPerformedMileage = Number(lastMileage);
    }
    if (basis === "engine_hours" && lastHours) {
      payload.lastPerformedEngineHours = Number(lastHours);
    }

    const base = getApiEndpoint().replace(/\/$/, "");
    authFetch(`${base}/api/v1/fleet-intelligence/pm/schedules`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    })
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then(() => {
        setSaving(false);
        onSave?.();
      })
      .catch((e: unknown) => {
        setSaving(false);
        setError(e instanceof Error ? e.message : String(e));
      });
  };

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------

  const basisOption =
    BASIS_OPTIONS.find((o) => o.value === basis) ?? BASIS_OPTIONS[0];

  return (
    <Form
      header={
        <Header variant="h2">
          {isEdit ? "Edit PM Schedule" : "Create PM Schedule"}
        </Header>
      }
      actions={
        <SpaceBetween size="xs" direction="horizontal">
          {onCancel && (
            <Button
              variant="link"
              onClick={onCancel}
              disabled={saving}
              data-testid="pm-form-cancel"
            >
              Cancel
            </Button>
          )}
          <Button
            variant="primary"
            onClick={handleSubmit}
            loading={saving}
            data-testid="pm-form-save"
          >
            {isEdit ? "Save changes" : "Create schedule"}
          </Button>
        </SpaceBetween>
      }
      errorText={error ?? undefined}
    >
      {error && (
        <Alert type="error" header="Failed to save schedule">
          {error}
        </Alert>
      )}

      <Container
        header={<Header variant="h3">Schedule details</Header>}
      >
        <SpaceBetween size="m">
          <FormField
            label="Vehicle ID"
            errorText={vehicleIdError}
            description="The vehicle this schedule applies to"
          >
            <Input
              value={vehicleId}
              onChange={({ detail }) => setVehicleId(detail.value)}
              placeholder="VEH-0001"
              data-testid="pm-form-vehicle-id"
              disabled={isEdit}
            />
          </FormField>

          <FormField
            label="Task Code"
            errorText={taskCodeError}
            description="VMRS-shaped task code (synthetic namespace)"
          >
            <Input
              value={taskCode}
              onChange={({ detail }) => setTaskCode(detail.value)}
              placeholder="PM-OIL-0001"
              data-testid="pm-form-task-code"
            />
          </FormField>

          <FormField
            label="Task description (optional)"
          >
            <Input
              value={taskDescription}
              onChange={({ detail }) => setTaskDescription(detail.value)}
              placeholder="Oil change and filter replacement"
              data-testid="pm-form-task-description"
            />
          </FormField>

          <FormField
            label="Basis"
            description="What triggers the next PM: distance, engine run time, or elapsed calendar time"
          >
            <Select
              selectedOption={basisOption}
              onChange={({ detail }) =>
                setBasis(detail.selectedOption.value as PmBasis)
              }
              options={BASIS_OPTIONS}
              data-testid="pm-form-basis"
            />
          </FormField>

          <FormField
            label={`Interval (${
              basis === "mileage"
                ? "miles"
                : basis === "engine_hours"
                ? "hours"
                : "days"
            })`}
            errorText={intervalError}
            description="How often the PM recurs"
          >
            <Input
              value={intervalValue}
              onChange={({ detail }) => setIntervalValue(detail.value)}
              type="number"
              inputMode="numeric"
              placeholder={
                basis === "mileage"
                  ? "5000"
                  : basis === "engine_hours"
                  ? "250"
                  : "90"
              }
              data-testid="pm-form-interval"
            />
          </FormField>
        </SpaceBetween>
      </Container>

      <Container
        header={<Header variant="h3">Last completion (optional)</Header>}
      >
        <SpaceBetween size="m">
          {basis === "calendar" && (
            <FormField
              label="Last performed date"
              description="ISO date (YYYY-MM-DD) of most recent completion"
            >
              <Input
                value={lastPerformedAt}
                onChange={({ detail }) => setLastPerformedAt(detail.value)}
                placeholder="2025-07-01"
                data-testid="pm-form-last-date"
              />
            </FormField>
          )}

          {basis === "mileage" && (
            <FormField
              label="Odometer at last PM (miles)"
            >
              <Input
                value={lastMileage}
                onChange={({ detail }) => setLastMileage(detail.value)}
                type="number"
                inputMode="numeric"
                placeholder="45000"
                data-testid="pm-form-last-mileage"
              />
            </FormField>
          )}

          {basis === "engine_hours" && (
            <FormField
              label="Engine hours at last PM"
            >
              <Input
                value={lastHours}
                onChange={({ detail }) => setLastHours(detail.value)}
                type="number"
                inputMode="numeric"
                placeholder="1200"
                data-testid="pm-form-last-hours"
              />
            </FormField>
          )}
        </SpaceBetween>
      </Container>
    </Form>
  );
};

export default PmScheduleForm;
