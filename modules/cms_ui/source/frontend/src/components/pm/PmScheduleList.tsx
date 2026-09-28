// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PmScheduleList — shows all PM schedules grouped by status:
 *   - Overdue (needs immediate attention)
 *   - Due soon (within 7 days)
 *   - Upcoming
 *
 * Reads GET /api/v1/fleet-intelligence/pm/schedules.
 * Every figure carries a <ProvenanceBadge> (all simulated today — spec § D6).
 */

import React, { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Button,
  Header,
  SpaceBetween,
  StatusIndicator,
  Table,
  TextFilter,
} from "@cloudscape-design/components";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import { ProvenanceBadge } from "../provenance";
import type { PmSchedule } from "./types";
import { PM_BASIS_LABELS, getPmStatus } from "./types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PmScheduleListProps {
  fleetId?: string;
  /** Called when the user clicks "Create Schedule" */
  onCreateSchedule?: () => void;
  /** Called when the user clicks "Edit" on a row */
  onEditSchedule?: (schedule: PmSchedule) => void;
  /** Called when the user clicks "Complete" on a row */
  onCompleteSchedule?: (schedule: PmSchedule) => void;
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function formatDue(schedule: PmSchedule): string {
  if (schedule.basis === "calendar") {
    return schedule.nextDueDate
      ? new Date(schedule.nextDueDate).toLocaleDateString()
      : "—";
  }
  if (schedule.basis === "mileage") {
    return schedule.nextDueValue != null
      ? `${schedule.nextDueValue.toLocaleString()} mi`
      : "—";
  }
  if (schedule.basis === "engine_hours") {
    return schedule.nextDueValue != null
      ? `${schedule.nextDueValue.toLocaleString()} hr`
      : "—";
  }
  return "—";
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const PmScheduleList: React.FC<PmScheduleListProps> = ({
  fleetId,
  onCreateSchedule,
  onEditSchedule,
  onCompleteSchedule,
}) => {
  const [schedules, setSchedules] = useState<PmSchedule[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState("");

  const loadSchedules = () => {
    setLoading(true);
    setError(null);

    const base = getApiEndpoint().replace(/\/$/, "");
    const params = new URLSearchParams();
    if (fleetId) params.set("fleetId", fleetId);

    const url = `${base}/api/v1/fleet-intelligence/pm/schedules${
      params.toString() ? `?${params.toString()}` : ""
    }`;

    authFetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      // The handler returns {data, provenance} — not {schedules}. Contract source
      // of truth: services/fleet_intelligence/index.py::_handle_pm_schedules_get.
      // See issues/2026-09-03-fleet-intelligence-ui-api-contract-mismatch/.
      .then((d: { data: PmSchedule[] }) =>
        setSchedules(d.data ?? [])
      )
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e))
      )
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    loadSchedules();
  }, [fleetId]); // eslint-disable-line react-hooks/exhaustive-deps

  // Filter
  const filtered = filter
    ? schedules.filter(
        (s) =>
          s.vehicleId.toLowerCase().includes(filter.toLowerCase()) ||
          s.taskCode.toLowerCase().includes(filter.toLowerCase()) ||
          (s.taskDescription ?? "")
            .toLowerCase()
            .includes(filter.toLowerCase())
      )
    : schedules;

  const overdue = filtered.filter((s) => getPmStatus(s) === "overdue");
  const dueSoon = filtered.filter((s) => getPmStatus(s) === "due-soon");
  const upcoming = filtered.filter((s) => getPmStatus(s) === "upcoming");

  const allItems = [...overdue, ...dueSoon, ...upcoming];

  // ---------------------------------------------------------------------------
  // Column definitions
  // ---------------------------------------------------------------------------

  const columns = [
    {
      id: "vehicle",
      header: "Vehicle",
      cell: (item: PmSchedule) => item.vehicleId,
    },
    {
      id: "task",
      header: "Task",
      cell: (item: PmSchedule) =>
        item.taskDescription ?? item.taskCode,
    },
    {
      id: "basis",
      header: "Basis",
      cell: (item: PmSchedule) => (
        <Badge color="blue">{PM_BASIS_LABELS[item.basis]}</Badge>
      ),
    },
    {
      id: "interval",
      header: "Interval",
      cell: (item: PmSchedule) => {
        const unit =
          item.basis === "mileage"
            ? " mi"
            : item.basis === "engine_hours"
            ? " hr"
            : " days";
        return `${item.intervalValue.toLocaleString()}${unit}`;
      },
    },
    {
      id: "nextDue",
      header: "Next Due",
      cell: (item: PmSchedule) => (
        <SpaceBetween size="xs" direction="horizontal">
          <span>{formatDue(item)}</span>
          <ProvenanceBadge provenance={item.provenance} variant="row" />
        </SpaceBetween>
      ),
    },
    {
      id: "status",
      header: "Status",
      cell: (item: PmSchedule) => {
        const status = getPmStatus(item);
        if (status === "overdue")
          return (
            <StatusIndicator type="error">
              Overdue
              {item.daysUntilDue !== undefined &&
                ` (${Math.abs(item.daysUntilDue)}d ago)`}
            </StatusIndicator>
          );
        if (status === "due-soon")
          return (
            <StatusIndicator type="warning">
              Due soon
              {item.daysUntilDue !== undefined &&
                ` (${item.daysUntilDue}d)`}
            </StatusIndicator>
          );
        return (
          <StatusIndicator type="success">
            Upcoming
          </StatusIndicator>
        );
      },
    },
    {
      id: "actions",
      header: "Actions",
      cell: (item: PmSchedule) => (
        <SpaceBetween size="xs" direction="horizontal">
          {onEditSchedule && (
            <Button
              variant="link"
              onClick={() => onEditSchedule(item)}
              data-testid={`edit-schedule-${item.scheduleId}`}
            >
              Edit
            </Button>
          )}
          {onCompleteSchedule && (
            <Button
              variant="link"
              onClick={() => onCompleteSchedule(item)}
              data-testid={`complete-schedule-${item.scheduleId}`}
            >
              Complete
            </Button>
          )}
        </SpaceBetween>
      ),
    },
  ];

  return (
    <SpaceBetween size="l">
      {error && (
        <Alert type="error" header="Failed to load PM schedules">
          {error}
        </Alert>
      )}

      <Table<PmSchedule>
        header={
          <Header
            variant="h2"
            counter={`(${allItems.length})`}
            actions={
              <SpaceBetween size="xs" direction="horizontal">
                <Button
                  variant="normal"
                  iconName="refresh"
                  onClick={loadSchedules}
                  data-testid="refresh-schedules"
                >
                  Refresh
                </Button>
                {onCreateSchedule && (
                  <Button
                    variant="primary"
                    iconName="add-plus"
                    onClick={onCreateSchedule}
                    data-testid="create-schedule-btn"
                  >
                    Create Schedule
                  </Button>
                )}
              </SpaceBetween>
            }
          >
            PM Schedules
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filter}
            filteringPlaceholder="Filter by vehicle, task code…"
            onChange={({ detail }) => setFilter(detail.filteringText)}
          />
        }
        loading={loading}
        loadingText="Loading PM schedules…"
        items={allItems}
        columnDefinitions={columns}
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            {filter
              ? "No PM schedules match the filter"
              : "No PM schedules found"}
          </Box>
        }
        variant="full-page"
        stickyHeader
      />
    </SpaceBetween>
  );
};

export default PmScheduleList;
