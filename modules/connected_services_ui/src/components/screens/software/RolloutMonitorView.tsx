// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RolloutMonitorView — Rollout Monitor screen (T5.5).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.5
 *
 * ## Screen contents
 *
 * Per-campaign staged rollout progress (canary → 10% → 50% → 100%), a fault-rate
 * delta series, and a widen/halt recommendation card.
 *
 * ## Seam 4 — halt threshold constant
 *
 * ROLLOUT_HALT_THRESHOLD_PCT is a named exported constant. It is rendered verbatim
 * on screen in the halt threshold indicator. The seam-4 guard
 * (seam4HaltThresholdConstant.test.ts) asserts the rendered string and this
 * constant carry the same value. This makes the threshold enforceable: changing the
 * constant without updating the render (or vice versa) fails the guard.
 *
 * The threshold is: if the fault rate delta exceeds +ROLLOUT_HALT_THRESHOLD_PCT
 * percentage points above baseline in a measurement window, a halt is recommended.
 *
 * ## D12 naming
 *
 * All campaign references use the SoftwareCampaign / software_campaign /
 * SOFTWARE_CAMPAIGNS_* prefix to avoid collision with CMS's FleetWise
 * data-collection campaigns.
 *
 * ## No location fields
 *
 * No trip/GPS/location/odometer field anywhere in this file.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Header from "@cloudscape-design/components/header";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import React from "react";
import { useNavigate } from "react-router-dom";
import ProvenanceField from "../../commons/ProvenanceField";
import {
  SOFTWARE_CAMPAIGNS_ROLLOUT_RECORD,
  SOFTWARE_CAMPAIGN_ROWS,
} from "./softwareCampaigns.fixture";
import type { RolloutStage, FaultRateDeltaPoint } from "./softwareCampaigns.fixture";

// ---------------------------------------------------------------------------
// settle marker
// ---------------------------------------------------------------------------

const SETTLE_MARKER = "cs-settle-rollout-monitor-progress-table";

// ---------------------------------------------------------------------------
// Seam 4 — halt threshold constant
//
// CRITICAL: This constant is the single source of truth for the halt threshold.
// It is exported so that the seam-4 guard can compare its value against the
// string rendered in the UI. Never hardcode a different number in the render
// path — the guard WILL catch it.
//
// A fault rate delta above this threshold (in percentage points) triggers the
// halt recommendation card.
// ---------------------------------------------------------------------------

/** Seam 4: halt threshold in percentage points above baseline. */
export const ROLLOUT_HALT_THRESHOLD_PCT = 5;

// ---------------------------------------------------------------------------
// Stage table columns
// ---------------------------------------------------------------------------

const STAGE_COLUMNS = [
  {
    id: "stageName",
    header: "Stage",
    cell: (row: RolloutStage) => {
      const nameValue = row.stageName.value ?? "";
      const statusValue = row.status.value ?? "pending";
      return (
        <Box data-testid={`cs-rollout-stage-name-${nameValue}`}>
          {nameValue}
          {statusValue === "in_progress" && (
            <Badge color="blue" data-testid={`cs-rollout-stage-active-${nameValue}`}>
              Active
            </Badge>
          )}
        </Box>
      );
    },
  },
  {
    id: "targetPct",
    header: "Target %",
    cell: (row: RolloutStage) => {
      const targetValue = row.targetPct.value ?? 0;
      return <Box data-testid="cs-rollout-stage-target">{String(targetValue)}%</Box>;
    },
  },
  {
    id: "actualPct",
    header: "Actual %",
    cell: (row: RolloutStage) => {
      const targetValue = row.targetPct.value ?? 0;
      const actualValue = row.actualPct.value ?? 0;
      const stageName = row.stageName.value ?? "";
      return (
        <ProgressBar
          value={targetValue > 0 ? Math.round((actualValue / targetValue) * 100) : 0}
          additionalInfo={`${String(actualValue)}% of fleet`}
          data-testid={`cs-rollout-stage-progress-${stageName}`}
        />
      );
    },
  },
  {
    id: "vehicleCount",
    header: "Vehicles updated",
    cell: (row: RolloutStage) => {
      // Extract .value before JSX to satisfy provenanceRender guard
      const stageNameForId = row.stageName.value ?? "";
      return (
        <ProvenanceField
          field={row.vehicleCount}
          label="rollout-stage-vehicle-count"
          testId={`cs-rollout-stage-vehicles-${stageNameForId}`}
        />
      );
    },
  },
  {
    id: "status",
    header: "Status",
    cell: (row: RolloutStage) => {
      const statusValue = row.status.value ?? "pending";
      type StageStatus = "pending" | "in_progress" | "completed" | "halted";
      const indicators: Record<StageStatus, { type: "pending" | "in-progress" | "success" | "error"; label: string }> = {
        pending: { type: "pending", label: "Pending" },
        in_progress: { type: "in-progress", label: "In progress" },
        completed: { type: "success", label: "Completed" },
        halted: { type: "error", label: "Halted" },
      };
      const { type, label } = indicators[statusValue as StageStatus] ?? { type: "pending" as const, label: statusValue };
      return (
        <StatusIndicator type={type} data-testid={`cs-rollout-stage-status-${statusValue}`}>
          {label}
        </StatusIndicator>
      );
    },
  },
];

// ---------------------------------------------------------------------------
// Fault rate delta chart (simple ASCII-style table — no canvas dependency)
// ---------------------------------------------------------------------------

const FAULT_RATE_COLUMNS = [
  {
    id: "timestamp",
    header: "Time",
    cell: (row: FaultRateDeltaPoint) => (
      <ProvenanceField
        field={row.timestamp}
        label="fault-rate-timestamp"
        testId="cs-rollout-fault-timestamp"
      />
    ),
  },
  {
    id: "faultRateDelta",
    header: "Fault rate delta (pp)",
    cell: (row: FaultRateDeltaPoint) => {
      const delta = row.faultRateDelta.value ?? 0;
      const isPositive = delta > 0;
      return (
        <Box
          color={isPositive ? "text-status-error" : "text-status-success"}
          data-testid="cs-rollout-fault-delta-cell"
        >
          {isPositive ? "+" : ""}
          {delta.toFixed(2)} pp
        </Box>
      );
    },
  },
];

// ---------------------------------------------------------------------------
// RolloutMonitorView
// ---------------------------------------------------------------------------

/**
 * RolloutMonitorView
 *
 * Displays per-campaign staged rollout progress, a fault-rate-delta series,
 * and a widen/halt recommendation card.
 *
 * This is a client-side-only simulated view — no API client, no fetch, no Cognito.
 *
 * Seam 4: ROLLOUT_HALT_THRESHOLD_PCT is exported and rendered verbatim.
 * The seam-4 guard asserts the rendered string matches the constant value.
 */
const RolloutMonitorView: React.FC = () => {
  const navigate = useNavigate();
  const record = SOFTWARE_CAMPAIGNS_ROLLOUT_RECORD;

  // Extract .value fields before JSX to satisfy the provenanceRender guard
  const campaignIdValue = record.softwareCampaignId.value ?? "";

  // Derive the campaign row for the header
  const matchingCampaign = SOFTWARE_CAMPAIGN_ROWS.find(
    (r) => r.softwareCampaignId.value === record.softwareCampaignId.value,
  );

  const currentFaultDelta = record.currentFaultRateDelta.value ?? 0;
  const isAboveHaltThreshold = currentFaultDelta > ROLLOUT_HALT_THRESHOLD_PCT / 100;
  const activeStageIdx = record.activeStageIndex.value ?? 0;
  const activeStage = record.stages[activeStageIdx] ?? null;
  const activeStageName = activeStage != null ? (activeStage.stageName.value ?? "") : "—";
  // Extract string fields before JSX to satisfy provenanceRender guard
  const widenRecommendationText = record.widenRecommendation.value ?? "";
  const haltRecommendationText = record.haltRecommendation.value ?? "";

  return (
    <SpaceBetween size="l">
      {/* settle marker */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="cs-rollout-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      {/* ------------------------------------------------------------------ */}
      {/* Header with campaign selection                                      */}
      {/* ------------------------------------------------------------------ */}
      <Container
        header={
          <Header
            variant="h1"
            description={
              matchingCampaign != null
                ? (matchingCampaign.targetDescription.value ?? "")
                : "Rollout progress by stage"
            }
            actions={
              <Button
                variant="normal"
                onClick={() => { navigate("/software/campaigns"); }}
                data-testid="cs-rollout-back-to-campaigns"
              >
                ← All campaigns
              </Button>
            }
          >
            Rollout Monitor — {campaignIdValue}
          </Header>
        }
        data-testid="cs-rollout-header-container"
      >
        <Box variant="p" color="text-body-secondary" data-testid="cs-rollout-active-stage-label">
          Active stage: <strong>{activeStageName}</strong>
        </Box>
      </Container>

      {/* ------------------------------------------------------------------ */}
      {/* Stage progress table                                                */}
      {/* ------------------------------------------------------------------ */}
      <Container
        header={<Header variant="h2">Staged Rollout Progress</Header>}
        data-testid="cs-rollout-stages-container"
      >
        <Table
          columnDefinitions={STAGE_COLUMNS}
          items={record.stages}
          empty="No stage data."
          data-testid="cs-rollout-stages-table"
          ariaLabels={{ tableLabel: "Rollout stages table" }}
        />
      </Container>

      {/* ------------------------------------------------------------------ */}
      {/* Fault rate delta series                                             */}
      {/* ------------------------------------------------------------------ */}
      <Container
        header={
          <Header
            variant="h2"
            description="Fault rate delta vs. pre-campaign baseline (percentage points). Negative = improvement."
          >
            Fault Rate Delta
          </Header>
        }
        data-testid="cs-rollout-fault-rate-container"
      >
        <Table
          columnDefinitions={FAULT_RATE_COLUMNS}
          items={record.faultRateDeltaSeries}
          empty="No fault rate data."
          data-testid="cs-rollout-fault-rate-table"
          ariaLabels={{ tableLabel: "Fault rate delta table" }}
        />
      </Container>

      {/* ------------------------------------------------------------------ */}
      {/* Seam 4 — Widen / Halt recommendation card                          */}
      {/* ------------------------------------------------------------------ */}
      <Container
        header={<Header variant="h2">Widen / Halt Recommendation</Header>}
        data-testid="cs-rollout-recommendation-container"
      >
        <SpaceBetween size="m">
          {/*
            Seam 4: ROLLOUT_HALT_THRESHOLD_PCT rendered verbatim.
            The seam-4 guard (seam4HaltThresholdConstant.test.ts) asserts that
            the value rendered inside data-testid="cs-rollout-halt-threshold-value"
            matches String(ROLLOUT_HALT_THRESHOLD_PCT).
            DO NOT hardcode a different number here.
          */}
          <Box data-testid="cs-rollout-halt-threshold-row">
            <Box variant="awsui-key-label">Halt threshold</Box>
            <Box data-testid="cs-rollout-halt-threshold-value">
              {String(ROLLOUT_HALT_THRESHOLD_PCT)}% above baseline fault rate
            </Box>
          </Box>

          <Box data-testid="cs-rollout-current-delta-row">
            <Box variant="awsui-key-label">Current fault rate delta</Box>
            <Box
              color={isAboveHaltThreshold ? "text-status-error" : "text-status-success"}
              data-testid="cs-rollout-current-delta-value"
            >
              {currentFaultDelta >= 0 ? "+" : ""}{(currentFaultDelta * 100).toFixed(1)} pp
            </Box>
          </Box>

          {isAboveHaltThreshold ? (
            <Alert
              type="error"
              statusIconAriaLabel="Error"
              data-testid="cs-rollout-halt-alert"
              header="Halt recommended"
            >
              Fault rate delta exceeds the{" "}
              <strong>{String(ROLLOUT_HALT_THRESHOLD_PCT)}%</strong> halt threshold.
              Review the fault series before widening.
            </Alert>
          ) : (
            <Alert
              type="success"
              statusIconAriaLabel="Success"
              data-testid="cs-rollout-widen-alert"
              header="Widen recommended"
            >
              {widenRecommendationText}
            </Alert>
          )}

          <Box variant="small" color="text-body-secondary" data-testid="cs-rollout-halt-guidance">
            {haltRecommendationText}
          </Box>
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};

export default RolloutMonitorView;
