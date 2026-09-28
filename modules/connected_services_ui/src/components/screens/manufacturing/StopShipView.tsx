// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * StopShipView — Stop-Ship / Stop-Sale screen (T6.2).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.2
 *
 * ## Screen layout
 *
 * Quality-criterion input with a live-updating match count against two pools:
 *   - Units in production
 *   - Units in in-transit inventory
 * NOT dealer lot inventory.
 *
 * A "Place Hold" button reveals StopShipHoldConfirmation, which requires a
 * named authorizer (person AND role), both non-empty. A holds log below shows
 * active and lifted holds with authorizer and timestamp.
 *
 * ## Seam 7 — higher-stakes action class
 *
 * Stop-Ship halts units before a recall is formally issued. This is a more
 * severe action than approving a software campaign (T5.4 ApprovalStep).
 *
 * The confirmation is StopShipHoldConfirmation — a form requiring two named
 * fields (person + role), both non-empty, rendered as a bordered alert-level
 * panel with a distinctive "HALT PRODUCTION" heading. It is NOT ApprovalStep
 * from DiagnosisWorkbenchView, which is a Wizard step that requires only a
 * rationale textarea and has testId "cs-workbench-approval-container".
 *
 * The seam-7 guard (StopShipView.test.tsx) asserts:
 *   1. Hold cannot be placed with either authorizer field empty.
 *   2. The confirmation component has testId "cs-stop-ship-hold-confirmation"
 *      (NOT "cs-workbench-approval-container").
 *
 * ## Pool scope
 *
 * The two match pools are production and in-transit inventory only.
 * Dealer lot inventory is outside the scope of this portal.
 *
 * ## ProvenanceField constraint
 *
 * Every fixture-derived value is rendered through ProvenanceField or
 * assertProvenance (spec D4 / T2.2 provenanceRender guard).
 *
 * ## No location fields
 *
 * No trip/GPS/location/odometer field anywhere in this file.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import ColumnLayout from "@cloudscape-design/components/column-layout";
import Container from "@cloudscape-design/components/container";
import FormField from "@cloudscape-design/components/form-field";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import React, { useState } from "react";

import ProvenanceField from "../../commons/ProvenanceField";
import { assertProvenance } from "../../../types";

import {
  STOP_SHIP_FIXTURE,
  type HoldLogEntry,
  type HoldStatus,
} from "./stopShip.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-stop-ship-hold-authorization-panel";

// ── Seam 7: StopShipHoldConfirmation ─────────────────────────────────────────
//
// This component is INTENTIONALLY different from ApprovalStep in
// DiagnosisWorkbenchView. ApprovalStep is a Wizard step with a single
// rationale Textarea (testId: "cs-workbench-approval-container"). This
// component is a standalone two-field named-authorizer form styled as a
// critical action panel (testId: "cs-stop-ship-hold-confirmation").
//
// Seam 7 invariant: both authorizerPerson and authorizerRole must be non-empty
// before the hold can be placed. The button is disabled until both are filled.
// This is enforced in both the component and the seam-7 guard test.

interface StopShipHoldConfirmationProps {
  criterion: string;
  productionMatchCount: number;
  inTransitMatchCount: number;
  authorizerPerson: string;
  authorizerRole: string;
  onPersonChange: (v: string) => void;
  onRoleChange: (v: string) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * StopShipHoldConfirmation — the seam-7 named-authorizer confirmation form.
 *
 * This is NOT ApprovalStep (DiagnosisWorkbenchView). It is a distinct
 * component requiring both a person name and a role, styled as a critical
 * action panel. The "Place Hold" button is disabled unless both fields are
 * non-empty — this is the seam-7 guard's primary assertion.
 *
 * testId: "cs-stop-ship-hold-confirmation"
 * (compare: ApprovalStep's testId is "cs-workbench-approval-container")
 */
export const StopShipHoldConfirmation: React.FC<StopShipHoldConfirmationProps> =
  ({
    criterion,
    productionMatchCount,
    inTransitMatchCount,
    authorizerPerson,
    authorizerRole,
    onPersonChange,
    onRoleChange,
    onConfirm,
    onCancel,
  }) => {
    // Seam 7 invariant: both fields must be non-empty.
    const bothFilled =
      authorizerPerson.trim().length > 0 && authorizerRole.trim().length > 0;

    return (
      <Container
        header={
          <Header
            variant="h3"
            data-testid="cs-stop-ship-hold-confirmation-heading"
          >
            Halt Production — Authorizer Required
          </Header>
        }
        data-testid="cs-stop-ship-hold-confirmation"
      >
        <SpaceBetween size="m">
          {/*
           * Critical-action alert. This panel is NOT reachable by the same
           * code path as the campaign-approval step — it is shown only when
           * the user clicks "Place Hold" on this screen.
           */}
          <Alert
            type="warning"
            statusIconAriaLabel="Warning"
            header="This action halts units before a recall is formally issued."
            data-testid="cs-stop-ship-hold-confirmation-alert"
          >
            Hold criterion: <strong>{criterion}</strong>
            <br />
            Units in production that match: <strong>{String(productionMatchCount)}</strong>
            <br />
            Units in transit that match: <strong>{String(inTransitMatchCount)}</strong>
            <br />
            This hold applies to production and in-transit inventory only.
          </Alert>

          {/* Named authorizer — BOTH fields required (Seam 7) */}
          <FormField
            label={
              <>
                Authorizer name{" "}
                {!authorizerPerson.trim() && (
                  <Badge
                    color="red"
                    data-testid="cs-stop-ship-hold-confirmation-person-required"
                  >
                    Required
                  </Badge>
                )}
              </>
            }
            description="Full name of the person authorizing this hold."
            data-testid="cs-stop-ship-hold-confirmation-person-field"
          >
            <Input
              value={authorizerPerson}
              onChange={({ detail }) => { onPersonChange(detail.value); }}
              placeholder="e.g. J. Rivera"
              data-testid="cs-stop-ship-hold-confirmation-person-input"
            />
          </FormField>

          <FormField
            label={
              <>
                Authorizer role{" "}
                {!authorizerRole.trim() && (
                  <Badge
                    color="red"
                    data-testid="cs-stop-ship-hold-confirmation-role-required"
                  >
                    Required
                  </Badge>
                )}
              </>
            }
            description="Official role of the authorizing person (e.g. Quality Director)."
            data-testid="cs-stop-ship-hold-confirmation-role-field"
          >
            <Input
              value={authorizerRole}
              onChange={({ detail }) => { onRoleChange(detail.value); }}
              placeholder="e.g. Quality Director"
              data-testid="cs-stop-ship-hold-confirmation-role-input"
            />
          </FormField>

          {!bothFilled && (
            <Alert
              type="error"
              statusIconAriaLabel="Error"
              data-testid="cs-stop-ship-hold-confirmation-fields-incomplete"
            >
              Both authorizer name and role are required to place a hold.
            </Alert>
          )}

          <SpaceBetween size="s" direction="horizontal">
            {/*
             * Seam 7 guard: this button is DISABLED unless both
             * authorizerPerson and authorizerRole are non-empty.
             */}
            <Button
              variant="primary"
              disabled={!bothFilled}
              onClick={onConfirm}
              data-testid="cs-stop-ship-hold-confirmation-confirm-button"
            >
              Place Hold
            </Button>
            <Button
              variant="normal"
              onClick={onCancel}
              data-testid="cs-stop-ship-hold-confirmation-cancel-button"
            >
              Cancel
            </Button>
          </SpaceBetween>
        </SpaceBetween>
      </Container>
    );
  };

// ── Hold status helpers ───────────────────────────────────────────────────────

function HoldStatusIndicator({
  status,
}: {
  status: HoldStatus;
}): React.ReactElement {
  if (status === "active") {
    return (
      <StatusIndicator type="warning" data-testid="cs-hold-status-active">
        Active
      </StatusIndicator>
    );
  }
  return (
    <StatusIndicator type="success" data-testid="cs-hold-status-lifted">
      Lifted
    </StatusIndicator>
  );
}

function PoolScopeBadge({
  scope,
}: {
  scope: "production" | "in_transit" | "both";
}): React.ReactElement {
  const labels: Record<string, string> = {
    production: "Production",
    in_transit: "In Transit",
    both: "Both pools",
  };
  return (
    <Badge
      color={scope === "both" ? "red" : "blue"}
      data-testid={`cs-hold-pool-${scope}`}
    >
      {labels[scope]}
    </Badge>
  );
}

// ── Holds log column definitions ──────────────────────────────────────────────

const HOLDS_LOG_COLUMNS = [
  {
    id: "criterion",
    header: "Hold criterion",
    cell: (row: HoldLogEntry) => (
      <ProvenanceField
        field={row.criterion}
        label="hold-criterion"
        testId={`cs-hold-criterion-${row.id}`}
      />
    ),
  },
  {
    id: "poolScope",
    header: "Pool scope",
    cell: (row: HoldLogEntry) => {
      assertProvenance(row.poolScope, "poolScope");
      return <PoolScopeBadge scope={row.poolScope.value!} />;
    },
  },
  {
    id: "matchCount",
    header: "Units held",
    cell: (row: HoldLogEntry) => (
      <ProvenanceField
        field={row.matchCount}
        label="hold-match-count"
        testId={`cs-hold-match-count-${row.id}`}
        render={(v) => String(v)}
      />
    ),
  },
  {
    id: "authorizerPerson",
    header: "Authorizer",
    cell: (row: HoldLogEntry) => (
      <ProvenanceField
        field={row.authorizerPerson}
        label="hold-authorizer-person"
        testId={`cs-hold-authorizer-person-${row.id}`}
      />
    ),
  },
  {
    id: "authorizerRole",
    header: "Role",
    cell: (row: HoldLogEntry) => (
      <ProvenanceField
        field={row.authorizerRole}
        label="hold-authorizer-role"
        testId={`cs-hold-authorizer-role-${row.id}`}
      />
    ),
  },
  {
    id: "placedAt",
    header: "Placed",
    cell: (row: HoldLogEntry) => (
      <ProvenanceField
        field={row.placedAt}
        label="hold-placed-at"
        testId={`cs-hold-placed-at-${row.id}`}
      />
    ),
  },
  {
    id: "status",
    header: "Status",
    cell: (row: HoldLogEntry) => {
      assertProvenance(row.status, "status");
      return <HoldStatusIndicator status={row.status.value!} />;
    },
    sortingField: "status",
  },
];

// ── Root component ────────────────────────────────────────────────────────────

const StopShipView: React.FC = () => {
  const [criterion, setCriterion] = useState("");
  const [showConfirmation, setShowConfirmation] = useState(false);
  const [authorizerPerson, setAuthorizerPerson] = useState("");
  const [authorizerRole, setAuthorizerRole] = useState("");
  const [holdPlaced, setHoldPlaced] = useState(false);

  // Derive live-updating pool counts from the fixture example.
  // In a real implementation these would update on criterion change.
  const poolCounts = STOP_SHIP_FIXTURE.examplePoolCounts;
  assertProvenance(poolCounts.productionCount, "productionCount");
  assertProvenance(poolCounts.inTransitCount, "inTransitCount");
  const productionCount = poolCounts.productionCount.value!;
  const inTransitCount = poolCounts.inTransitCount.value!;

  const handlePlaceHoldClick = () => {
    setShowConfirmation(true);
    setAuthorizerPerson("");
    setAuthorizerRole("");
  };

  const handleConfirm = () => {
    // Client-side state only — no API call.
    setHoldPlaced(true);
    setShowConfirmation(false);
    setCriterion("");
    setTimeout(() => { setHoldPlaced(false); }, 2500);
  };

  const handleCancel = () => {
    setShowConfirmation(false);
    setAuthorizerPerson("");
    setAuthorizerRole("");
  };

  return (
    <SpaceBetween size="l" data-testid="cs-stop-ship-root">
      {/* settle marker — unique to this screen */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="cs-stop-ship-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      {holdPlaced && (
        <Alert
          type="success"
          statusIconAriaLabel="Success"
          data-testid="cs-stop-ship-hold-placed-banner"
        >
          Hold placed. Affected units are flagged in production and in-transit
          inventory.
        </Alert>
      )}

      {/* ── Criterion input and pool match counts ── */}
      <Container
        header={
          <Header variant="h2" data-testid="cs-stop-ship-criterion-header">
            Quality Hold Criterion
          </Header>
        }
        data-testid="cs-stop-ship-criterion-container"
      >
        <SpaceBetween size="m">
          <FormField
            label="Criterion"
            description="Enter a quality criterion to match units in production and in-transit inventory."
            data-testid="cs-stop-ship-criterion-field"
          >
            <Input
              value={criterion}
              onChange={({ detail }) => { setCriterion(detail.value); }}
              placeholder="e.g. TCU firmware rev < 3.1.4"
              data-testid="cs-stop-ship-criterion-input"
            />
          </FormField>

          {/* Live-updating match counts against the two pools */}
          <ColumnLayout columns={2} data-testid="cs-stop-ship-pool-counts">
            <Box data-testid="cs-stop-ship-production-count">
              <Box variant="awsui-key-label">Units in production matching</Box>
              <Box
                variant="h3"
                data-testid="cs-stop-ship-production-count-value"
              >
                {criterion.trim() ? String(productionCount) : "—"}
              </Box>
            </Box>
            <Box data-testid="cs-stop-ship-in-transit-count">
              <Box variant="awsui-key-label">
                Units in in-transit inventory matching
              </Box>
              <Box
                variant="h3"
                data-testid="cs-stop-ship-in-transit-count-value"
              >
                {criterion.trim() ? String(inTransitCount) : "—"}
              </Box>
            </Box>
          </ColumnLayout>

          <Box
            variant="small"
            color="text-body-secondary"
            data-testid="cs-stop-ship-pool-scope-note"
          >
            Scope: production and in-transit inventory only. Lot inventory held
            by distribution channels is not included.
          </Box>

          <Button
            variant="primary"
            disabled={!criterion.trim() || showConfirmation}
            onClick={handlePlaceHoldClick}
            data-testid="cs-stop-ship-place-hold-button"
          >
            Place Hold
          </Button>
        </SpaceBetween>
      </Container>

      {/*
       * Seam 7: StopShipHoldConfirmation is shown here — NOT ApprovalStep.
       * It requires both authorizerPerson and authorizerRole to be non-empty.
       * testId: "cs-stop-ship-hold-confirmation"
       * ApprovalStep testId: "cs-workbench-approval-container" (not present here).
       */}
      {showConfirmation && (
        <StopShipHoldConfirmation
          criterion={criterion}
          productionMatchCount={productionCount}
          inTransitMatchCount={inTransitCount}
          authorizerPerson={authorizerPerson}
          authorizerRole={authorizerRole}
          onPersonChange={setAuthorizerPerson}
          onRoleChange={setAuthorizerRole}
          onConfirm={handleConfirm}
          onCancel={handleCancel}
        />
      )}

      {/* ── Holds log ── */}
      <Table
        columnDefinitions={HOLDS_LOG_COLUMNS}
        items={STOP_SHIP_FIXTURE.holdsLog}
        header={
          <Header data-testid="cs-stop-ship-holds-log-header">
            Active and Lifted Holds ({STOP_SHIP_FIXTURE.holdsLog.length})
          </Header>
        }
        data-testid="cs-stop-ship-holds-log-table"
      />
    </SpaceBetween>
  );
};

export default StopShipView;
