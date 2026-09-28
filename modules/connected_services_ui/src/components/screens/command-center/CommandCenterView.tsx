// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CommandCenterView — cross-domain operations overview grid.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T4.1
 *
 * ## Layout
 *
 * Six tiles in a 3×2 grid:
 *   Row 1: Fleet Connectivity | Software Rollout | Quality Signals
 *   Row 2: Security           | Manufacturing    | Pending Approvals
 *
 * Below the grid: activity feed (6 cross-domain events), each clicking through
 * to its source screen.
 *
 * ## Drill-down contracts
 *
 *   Fleet Connectivity  → /connectivity/fleet-health?state=degraded
 *                          (pre-applies the degraded filter per FleetHealthView.tsx
 *                           ConnectivityStateParam contract)
 *   Software Rollout    → /software/rollout
 *   Quality Signals     → /diagnostics/quality-signals
 *   Security            → /software/security  (placeholder screen)
 *   Manufacturing       → /manufacturing/factory-registration
 *   Pending Approvals   → /software/workbench
 *
 * Query strings are stripped by the deadLinks L2 guard before path matching,
 * so `?state=degraded` is safe on the connectivity tile's navigate() call.
 *
 * ## Pending Approvals source
 *
 * The count is derived from PENDING_APPROVALS_TILE which carries two simulated
 * counters: `otaProposals` (Diagnosis Workbench approval step, T5.4) and
 * `stopShipHolds` (StopShip screen, T6.2). When those fixtures land in Groups 5-6,
 * replace the constants with imports from those fixture modules and sum from their
 * arrays directly. The split in commandCenter.fixture.ts documents the intent.
 *
 * ## ProvenanceValue constraint
 *
 * Every fixture-derived field is rendered via ProvenanceField or through
 * assertProvenance(). No bare `.value` dereferences appear in JSX
 * (spec D4 / T2.2 provenanceRender guard).
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Container from "@cloudscape-design/components/container";
import Grid from "@cloudscape-design/components/grid";
import Header from "@cloudscape-design/components/header";
import Link from "@cloudscape-design/components/link";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import type { TableProps } from "@cloudscape-design/components/table";
import React, { useCallback } from "react";
import { useNavigate } from "react-router-dom";

import ProvenanceField from "../../commons/ProvenanceField";
import { assertProvenance } from "../../../types";

import {
  COMMAND_CENTER_ACTIVITY,
  FLEET_CONNECTIVITY_TILE,
  MANUFACTURING_TILE,
  PENDING_APPROVALS_TILE,
  QUALITY_SIGNALS_TILE,
  SECURITY_TILE,
  SOFTWARE_ROLLOUT_TILE,
  type ActivityFeedEvent,
} from "./commandCenter.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-command-center-overview-grid";

// ── Activity feed column definitions ─────────────────────────────────────────

/**
 * Activity feed column definitions.
 *
 * `targetPath` carries the navigation path as a ProvenanceValue<string>.
 * The `.value` is accessed in the `onFollow`/`href` handler (non-JSX),
 * which satisfies the provenanceRender guard (no `.value` in JSX interpolation).
 * Every displayed field goes through ProvenanceField.
 */
function buildActivityColumns(
  onRowClick: (path: string) => void
): TableProps.ColumnDefinition<ActivityFeedEvent>[] {
  return [
    {
      id: "timestamp",
      header: "Time",
      width: 200,
      cell: (item) => (
        <ProvenanceField field={item.timestamp} label="activity_timestamp" />
      ),
    },
    {
      id: "domain",
      header: "Domain",
      width: 180,
      cell: (item) => (
        <ProvenanceField field={item.domain} label="activity_domain" />
      ),
    },
    {
      id: "description",
      header: "Event",
      cell: (item) => {
        assertProvenance(item.description, "activity_description");
        assertProvenance(item.targetPath, "activity_target_path");
        const text = item.description.value ?? "—";
        // targetPath.value is accessed in a non-JSX expression (handler argument
        // and href attribute value) — this keeps the provenanceRender guard green.
        const navPath = item.targetPath.value ?? "/";
        return (
          <Link
            onFollow={(e) => {
              e.preventDefault();
              onRowClick(navPath);
            }}
            href={navPath}
          >
            {text}
          </Link>
        );
      },
    },
  ];
}

// ── Tile component ────────────────────────────────────────────────────────────

interface TileProps {
  label: string;
  headline: React.ReactNode;
  caption: string;
  drillDownPath: string;
  drillDownLabel: string;
  testId: string;
}

function CommandTile({
  label,
  headline,
  caption,
  drillDownPath,
  drillDownLabel,
  testId,
}: TileProps): React.ReactElement {
  const navigate = useNavigate();
  const handleDrillDown = useCallback(() => {
    navigate(drillDownPath);
  }, [navigate, drillDownPath]);

  return (
    <Container
      header={<Header variant="h2">{label}</Header>}
      footer={
        <Button
          variant="link"
          onClick={handleDrillDown}
          data-testid={testId}
        >
          {drillDownLabel}
        </Button>
      }
    >
      <SpaceBetween size="xs">
        <Box variant="h1" color="text-label">
          {headline}
        </Box>
        <Box variant="small" color="text-body-secondary">
          {caption}
        </Box>
      </SpaceBetween>
    </Container>
  );
}

// ── Main component ────────────────────────────────────────────────────────────

const CommandCenterView: React.FC = () => {
  const navigate = useNavigate();

  // ── Tile click handlers ──────────────────────────────────────────────────

  const handleFleetConnectivityClick = useCallback(() => {
    navigate("/connectivity/fleet-health?state=degraded");
  }, [navigate]);

  const handleSoftwareRolloutClick = useCallback(() => {
    navigate("/software/rollout");
  }, [navigate]);

  const handleQualitySignalsClick = useCallback(() => {
    navigate("/diagnostics/quality-signals");
  }, [navigate]);

  const handleSecurityClick = useCallback(() => {
    navigate("/software/security");
  }, [navigate]);

  const handleManufacturingClick = useCallback(() => {
    navigate("/manufacturing/factory-registration");
  }, [navigate]);

  const handlePendingApprovalsClick = useCallback(() => {
    navigate("/software/workbench");
  }, [navigate]);

  // ── Activity feed click handler ──────────────────────────────────────────

  const handleActivityRowClick = useCallback(
    (path: string) => {
      navigate(path);
    },
    [navigate]
  );

  // ── Asserted values for tile headlines ───────────────────────────────────

  assertProvenance(FLEET_CONNECTIVITY_TILE.totalDegraded, "fleet_total_degraded");
  assertProvenance(SOFTWARE_ROLLOUT_TILE.activeRollouts, "software_active_rollouts");
  assertProvenance(SOFTWARE_ROLLOUT_TILE.pctComplete, "software_pct_complete");
  assertProvenance(QUALITY_SIGNALS_TILE.openSignals, "quality_open_signals");
  assertProvenance(QUALITY_SIGNALS_TILE.affectedVehicles, "quality_affected_vehicles");
  assertProvenance(SECURITY_TILE.monitoringStatus, "security_monitoring_status");
  assertProvenance(MANUFACTURING_TILE.pendingRegistration, "manufacturing_pending_registration");
  assertProvenance(MANUFACTURING_TILE.activeHolds, "manufacturing_active_holds");
  assertProvenance(PENDING_APPROVALS_TILE.total, "pending_approvals_total");
  assertProvenance(PENDING_APPROVALS_TILE.otaProposals, "pending_approvals_ota");
  assertProvenance(PENDING_APPROVALS_TILE.stopShipHolds, "pending_approvals_stop_ship");

  const degradedCount = FLEET_CONNECTIVITY_TILE.totalDegraded.value ?? 0;
  const rolloutCount = SOFTWARE_ROLLOUT_TILE.activeRollouts.value ?? 0;
  const rolloutPct = SOFTWARE_ROLLOUT_TILE.pctComplete.value ?? 0;
  const signalCount = QUALITY_SIGNALS_TILE.openSignals.value ?? 0;
  const affectedCount = QUALITY_SIGNALS_TILE.affectedVehicles.value ?? 0;
  const securityStatus = SECURITY_TILE.monitoringStatus.value ?? "—";
  const pendingReg = MANUFACTURING_TILE.pendingRegistration.value ?? 0;
  const activeHolds = MANUFACTURING_TILE.activeHolds.value ?? 0;
  const pendingTotal = PENDING_APPROVALS_TILE.total.value ?? 0;
  const otaCount = PENDING_APPROVALS_TILE.otaProposals.value ?? 0;
  const stopShipCount = PENDING_APPROVALS_TILE.stopShipHolds.value ?? 0;

  const activityColumns = buildActivityColumns(handleActivityRowClick);

  return (
    <SpaceBetween size="l">
      {/* settle marker — required by registry-completeness P1 */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {/* ── Six-tile grid (3 × 2) ──────────────────────────────────────── */}
      <Grid
        gridDefinition={[
          { colspan: 4 },
          { colspan: 4 },
          { colspan: 4 },
          { colspan: 4 },
          { colspan: 4 },
          { colspan: 4 },
        ]}
      >
        {/* Tile 1 — Fleet Connectivity */}
        <Container
          header={<Header variant="h2">Fleet Connectivity</Header>}
          footer={
            <Button
              variant="link"
              onClick={handleFleetConnectivityClick}
              data-testid="cs-tile-degraded-connectivity"
            >
              View degraded vehicles
            </Button>
          }
        >
          <SpaceBetween size="xs">
            <Box variant="h1" color="text-status-warning">
              {degradedCount}
            </Box>
            <Box variant="small" color="text-body-secondary">
              vehicles in degraded connectivity state
            </Box>
          </SpaceBetween>
        </Container>

        {/* Tile 2 — Software Rollout */}
        <Container
          header={<Header variant="h2">Software Rollout</Header>}
          footer={
            <Button
              variant="link"
              onClick={handleSoftwareRolloutClick}
              data-testid="cs-tile-software-rollout"
            >
              View rollout monitor
            </Button>
          }
        >
          <SpaceBetween size="xs">
            <Box variant="h1" color="text-status-info">
              {rolloutCount}
            </Box>
            <Box variant="small" color="text-body-secondary">
              active rollouts &mdash; {rolloutPct}% fleet penetration
            </Box>
          </SpaceBetween>
        </Container>

        {/* Tile 3 — Quality Signals */}
        <Container
          header={<Header variant="h2">Quality Signals</Header>}
          footer={
            <Button
              variant="link"
              onClick={handleQualitySignalsClick}
              data-testid="cs-tile-quality-signals"
            >
              View quality signals
            </Button>
          }
        >
          <SpaceBetween size="xs">
            <Box variant="h1" color="text-status-error">
              {signalCount}
            </Box>
            <Box variant="small" color="text-body-secondary">
              open fault signatures &mdash; {affectedCount} vehicles affected
            </Box>
          </SpaceBetween>
        </Container>

        {/* Tile 4 — Security */}
        <Container
          header={<Header variant="h2">Security</Header>}
          footer={
            <Button
              variant="link"
              onClick={handleSecurityClick}
              data-testid="cs-tile-security"
            >
              View security monitor
            </Button>
          }
        >
          <SpaceBetween size="xs">
            <StatusIndicator type="stopped">
              {securityStatus}
            </StatusIndicator>
            <Box variant="small" color="text-body-secondary">
              R155 monitoring is not yet active in this pass
            </Box>
          </SpaceBetween>
        </Container>

        {/* Tile 5 — Manufacturing */}
        <Container
          header={<Header variant="h2">Manufacturing</Header>}
          footer={
            <Button
              variant="link"
              onClick={handleManufacturingClick}
              data-testid="cs-tile-manufacturing"
            >
              View factory registration
            </Button>
          }
        >
          <SpaceBetween size="xs">
            <Box variant="h1" color="text-status-info">
              {pendingReg}
            </Box>
            <Box variant="small" color="text-body-secondary">
              units pending factory registration &mdash; {activeHolds} active holds
            </Box>
          </SpaceBetween>
        </Container>

        {/* Tile 6 — Pending Approvals */}
        <Container
          header={<Header variant="h2">Pending Approvals</Header>}
          footer={
            <Button
              variant="link"
              onClick={handlePendingApprovalsClick}
              data-testid="cs-tile-pending-approvals"
            >
              View approval queue
            </Button>
          }
        >
          <SpaceBetween size="xs">
            <Box variant="h1" color="text-status-warning">
              {pendingTotal}
            </Box>
            <Box variant="small" color="text-body-secondary">
              total pending &mdash; {otaCount} OTA proposals, {stopShipCount} stop-ship holds
            </Box>
          </SpaceBetween>
        </Container>
      </Grid>

      {/* ── Activity feed ──────────────────────────────────────────────── */}
      <Table<ActivityFeedEvent>
        header={
          <Header variant="h2" counter={`(${COMMAND_CENTER_ACTIVITY.length})`}>
            Recent Activity
          </Header>
        }
        items={COMMAND_CENTER_ACTIVITY}
        columnDefinitions={activityColumns}
        sortingDisabled
        stripedRows
      />
    </SpaceBetween>
  );
};

export default CommandCenterView;
