// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PmComplianceView — the top-level page rendered at the PM Compliance route.
 *
 * Combines:
 *   - PmComplianceRollup (compliance summary)
 *   - PmScheduleList (due / overdue / upcoming)
 *   - PmScheduleForm (modal-style inline panel for create/edit)
 *
 * Route constant is set in T5.4 when the Compliance nav section is created.
 * This component is self-contained; T5.4 adds the route pointing here.
 */

import React, { useState } from "react";
import { Alert, SpaceBetween } from "@cloudscape-design/components";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import { PmComplianceRollup } from "./PmComplianceRollup";
import { PmScheduleList } from "./PmScheduleList";
import { PmScheduleForm } from "./PmScheduleForm";
import type { PmSchedule } from "./types";

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const PmComplianceView: React.FC = () => {
  const [formOpen, setFormOpen] = useState(false);
  const [editingSchedule, setEditingSchedule] = useState<
    Partial<PmSchedule> | undefined
  >(undefined);
  // Force PmScheduleList to reload by bumping a key
  const [listKey, setListKey] = useState(0);
  const [completionError, setCompletionError] = useState<string | null>(null);

  const handleCreate = () => {
    setEditingSchedule(undefined);
    setFormOpen(true);
  };

  const handleEdit = (schedule: PmSchedule) => {
    setEditingSchedule(schedule);
    setFormOpen(true);
  };

  const handleComplete = (schedule: PmSchedule) => {
    // POST to the complete endpoint — the form handles general create/edit;
    // completion is a separate action that records the completion date.
    //
    // The handler (services/fleet_intelligence/index.py::_handle_pm_complete)
    // requires BOTH vehicleId (it is the table's partition key) and an
    // ISO-8601 `completedDate`, and 400s without them — so both are sent
    // explicitly here rather than relying on a server-side default.
    setCompletionError(null);
    const base = getApiEndpoint().replace(/\/$/, "");
    authFetch(
      `${base}/api/v1/fleet-intelligence/pm/schedules/${encodeURIComponent(
        schedule.scheduleId
      )}/complete`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          vehicleId: schedule.vehicleId,
          completedDate: new Date().toISOString().slice(0, 10),
        }),
      }
    )
      .then((res) => {
        if (!res.ok) {
          throw new Error(`Completion failed (${res.status})`);
        }
        setListKey((k) => k + 1);
      })
      .catch((err) => {
        // Surface the failure — a silently-swallowed error leaves the operator
        // believing the PM was recorded when it was not.
        setCompletionError(
          err instanceof Error ? err.message : "Could not record the completion."
        );
      });
  };

  const handleSave = () => {
    setFormOpen(false);
    setEditingSchedule(undefined);
    setListKey((k) => k + 1);
  };

  const handleCancel = () => {
    setFormOpen(false);
    setEditingSchedule(undefined);
  };

  return (
    <SpaceBetween size="l">
      {/* Completion failures must be visible — see handleComplete */}
      {completionError && (
        <Alert
          type="error"
          dismissible
          onDismiss={() => setCompletionError(null)}
          header="Could not record the PM completion"
        >
          {completionError}
        </Alert>
      )}

      {/* Compliance rollup panel */}
      <PmComplianceRollup />

      {/* Create / Edit form — rendered inline above the list when open */}
      {formOpen && (
        <PmScheduleForm
          initialValues={editingSchedule}
          onSave={handleSave}
          onCancel={handleCancel}
        />
      )}

      {/* Schedule list */}
      <PmScheduleList
        key={listKey}
        onCreateSchedule={handleCreate}
        onEditSchedule={handleEdit}
        onCompleteSchedule={handleComplete}
      />
    </SpaceBetween>
  );
};

export default PmComplianceView;
