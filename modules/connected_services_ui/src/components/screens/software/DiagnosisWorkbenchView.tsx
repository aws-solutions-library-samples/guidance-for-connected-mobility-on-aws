// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DiagnosisWorkbenchView — full six-step OTA campaign approval stepper (T5.4).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.4
 *
 * ## Six screen states (steps)
 *
 *   1. Signal     — the detected fault signal; entry point.
 *   2. Diagnosis  — ranked hypotheses backed by a Tier2Artifact.
 *   3. Proposal   — target-criteria form + fleet-preview count (Seam 2).
 *   4. Approval   — its own screen state with its own settleMarker (Seam 1).
 *   5. Rollout    — hands off to CampaignsView (T5.5).
 *   6. Record     — campaign ID, RXSWIN touched, rationale, outcome.
 *
 * ## Seam 1 — Approval as a real screen state
 *
 * UNECE R156 requires a documented human approval step, and a later phase is
 * expected to replace the human with automation AT THIS SEAM. A modal confirm
 * cannot represent that seam because automation cannot be wired to it.
 * The Approval step is therefore a distinct stepper position with its own
 * settleMarker ("cs-settle-workbench-approval-step") and required rationale
 * text field. No window.confirm or modal is used for the Approve action.
 *
 * ## Seam 2 — Fleet preview as a pure function
 *
 * The "N vehicles match" count is computed by FleetPreviewCount, a pure-function
 * component that imports no artifact-shaped fixture. It is rendered adjacent to
 * (not inside) the AI diagnosis card to make the distinction visible.
 *
 * ## Seam 3 — RXSWIN deferred
 *
 * RXSWIN fields are plain ProvenanceValue<string>, never inside evidence[].
 * See decisions.md 2026-09-04.
 *
 * ## Backward compatibility
 *
 * The T4.4 click-path (hops 3 and 4) still works:
 *   - The settle marker "cs-settle-diagnosis-workbench-stepper" is rendered in
 *     the root container for BOTH the VIN-scoped stepper AND the no-VIN landing.
 *   - The back button (cs-workbench-back-to-subscriber) is still rendered in
 *     the VIN-scoped state.
 *
 * ## Provenance
 *
 * Every field from fixture data is passed through ProvenanceField or has its
 * .value extracted to a local variable before JSX to satisfy the provenanceRender
 * guard.
 *
 * ## No location fields
 *
 * No trip/GPS/location/odometer field anywhere in this file.
 */

import Alert from "@cloudscape-design/components/alert";
import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Checkbox from "@cloudscape-design/components/checkbox";
import Container from "@cloudscape-design/components/container";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import Input from "@cloudscape-design/components/input";
import ProgressBar from "@cloudscape-design/components/progress-bar";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import Table from "@cloudscape-design/components/table";
import Textarea from "@cloudscape-design/components/textarea";
import Wizard from "@cloudscape-design/components/wizard";
import React, { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";
import ProvenanceField from "../../commons/ProvenanceField";
import FleetPreviewCount from "./FleetPreviewCount";
import type { FleetPreviewCriteria } from "./FleetPreviewCount";
import {
  VIN_SOFTWARE_STATES,
  WORKBENCH_WORKFLOW,
} from "./diagnosisWorkbench.fixture";
import type { SoftwarePackage } from "./diagnosisWorkbench.fixture";

// ---------------------------------------------------------------------------
// settle markers — two distinct markers for the two seam-guarded states
// ---------------------------------------------------------------------------

/** Root marker — appears in both VIN-scoped and no-VIN landing state. */
const SETTLE_MARKER = "cs-settle-diagnosis-workbench-stepper";

/**
 * Approval-step marker — appears ONLY when the stepper is on the Approval step.
 * This is Seam 1: the seam guard asserts this marker is only reachable by
 * reaching this step, never via window.confirm or a modal.
 */
export const APPROVAL_STEP_SETTLE_MARKER = "cs-settle-workbench-approval-step";

// ---------------------------------------------------------------------------
// Step indices
// ---------------------------------------------------------------------------

const STEP_SIGNAL = 0;
const STEP_DIAGNOSIS = 1;
const STEP_PROPOSAL = 2;
const STEP_APPROVAL = 3;
const STEP_ROLLOUT = 4;
const STEP_RECORD = 5;

// ---------------------------------------------------------------------------
// Package table columns
// ---------------------------------------------------------------------------

const PACKAGE_COLUMNS = [
  {
    id: "name",
    header: "Package",
    cell: (row: SoftwarePackage) => (
      <ProvenanceField field={row.name} label="pkg-name" testId={`cs-workbench-pkg-name-${row.id}`} />
    ),
  },
  {
    id: "installedVersion",
    header: "Installed",
    cell: (row: SoftwarePackage) => (
      <ProvenanceField
        field={row.installedVersion}
        label="pkg-installed"
        testId={`cs-workbench-pkg-installed-${row.id}`}
      />
    ),
  },
  {
    id: "baselineVersion",
    header: "Baseline",
    cell: (row: SoftwarePackage) => (
      <ProvenanceField
        field={row.baselineVersion}
        label="pkg-baseline"
        testId={`cs-workbench-pkg-baseline-${row.id}`}
      />
    ),
  },
  {
    id: "drift",
    header: "Drift",
    cell: (row: SoftwarePackage) => {
      const driftValue = row.hasDrift.value === true;
      return driftValue ? (
        <Badge color="red" data-testid={`cs-workbench-pkg-drift-${row.id}`}>
          Drift
        </Badge>
      ) : (
        <StatusIndicator type="success" data-testid={`cs-workbench-pkg-ok-${row.id}`}>
          Current
        </StatusIndicator>
      );
    },
  },
  {
    id: "lastUpdatedAt",
    header: "Last updated",
    cell: (row: SoftwarePackage) => (
      <ProvenanceField
        field={row.lastUpdatedAt}
        label="pkg-lastUpdated"
        testId={`cs-workbench-pkg-updated-${row.id}`}
      />
    ),
  },
];

// ---------------------------------------------------------------------------
// Step components
// ---------------------------------------------------------------------------

interface SignalStepProps {
  vin: string;
}

const SignalStep: React.FC<SignalStepProps> = ({ vin }) => {
  const vinState = VIN_SOFTWARE_STATES[vin] ?? null;
  const signal = WORKBENCH_WORKFLOW.signal;

  // Extract .value before JSX
  const signalTypeValue = signal.signalType.value;
  const severityValue = signal.severity.value;
  const affectedEstimateValue = signal.affectedPopulationEstimate.value;
  const hasDrift = vinState != null ? vinState.overallDrift.value === true : false;

  return (
    <SpaceBetween size="m">
      <Container
        header={<Header variant="h3">Detected Signal</Header>}
        data-testid="cs-workbench-signal-container"
      >
        <SpaceBetween size="s">
          <Box>
            <Box variant="awsui-key-label">Signal type</Box>
            <Box data-testid="cs-workbench-signal-type">{signalTypeValue}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Severity</Box>
            <Badge
              color={severityValue === "high" ? "red" : severityValue === "medium" ? "severity-medium" : "grey"}
              data-testid="cs-workbench-signal-severity"
            >
              {severityValue}
            </Badge>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Estimated affected vehicles</Box>
            <Box data-testid="cs-workbench-signal-affected">{String(affectedEstimateValue)}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">RXSWIN reference (simulated)</Box>
            {/* RXSWIN — plain simulated field, Seam 3 deferred */}
            <ProvenanceField
              field={signal.rxswinReference}
              label="signal-rxswin"
              testId="cs-workbench-signal-rxswin"
            />
          </Box>
          <Box>
            <Box variant="awsui-key-label">Detected at</Box>
            <ProvenanceField
              field={signal.detectedAt}
              label="signal-detected-at"
              testId="cs-workbench-signal-detected-at"
            />
          </Box>
        </SpaceBetween>
      </Container>

      {vinState != null && (
        <Container
          header={
            <Header
              variant="h3"
              description={
                <>
                  {"Market: "}
                  <ProvenanceField field={vinState.market} label="workbench-market" testId="cs-workbench-market" />
                </>
              }
              actions={
                hasDrift ? (
                  <StatusIndicator
                    type="warning"
                    data-testid="cs-workbench-drift-indicator"
                  >
                    Version drift detected
                  </StatusIndicator>
                ) : (
                  <StatusIndicator
                    type="success"
                    data-testid="cs-workbench-no-drift-indicator"
                  >
                    All packages current
                  </StatusIndicator>
                )
              }
            >
              {"Software State for "}
              <ProvenanceField field={vinState.vin} label="workbench-vin" testId="cs-workbench-vin" />
            </Header>
          }
          data-testid="cs-workbench-summary-card"
        >
          <SpaceBetween size="s">
            {hasDrift && (
              <Alert type="warning" statusIconAriaLabel="Warning" data-testid="cs-workbench-drift-alert">
                One or more software packages are not at their assigned baseline version.
              </Alert>
            )}
          </SpaceBetween>
        </Container>
      )}

      {vinState != null && (
        <Container
          header={<Header variant="h3">Installed Packages</Header>}
          data-testid="cs-workbench-packages-container"
        >
          <Table
            columnDefinitions={PACKAGE_COLUMNS}
            items={vinState.packages}
            empty="No package records."
            data-testid="cs-workbench-packages-table"
          />
        </Container>
      )}
    </SpaceBetween>
  );
};

// ---------------------------------------------------------------------------

const DiagnosisStep: React.FC = () => {
  const artifact = WORKBENCH_WORKFLOW.diagnosisArtifact;
  // Artifact envelope fields are exempt from ProvenanceField
  const computedAt = artifact.computed_at;
  const confidencePct = Math.round(artifact.confidence * 100);

  return (
    <SpaceBetween size="m">
      {/* Labelled AI card — this is the artifact-backed panel */}
      <Container
        header={
          <Header
            variant="h3"
            description={`Agent: ${artifact.agent_version} · Computed at ${computedAt} · Confidence ${String(confidencePct)}%`}
          >
            AI Diagnosis (Tier 2 artifact)
          </Header>
        }
        data-testid="cs-workbench-diagnosis-ai-card"
      >
        <SpaceBetween size="s">
          <ExpandableSection headerText="Evidence chips" data-testid="cs-workbench-evidence-chips">
            <SpaceBetween size="xs">
              {artifact.evidence.map((chip, idx) => (
                <Box key={idx} data-testid={`cs-workbench-evidence-chip-${String(idx)}`}>
                  <Badge color="blue">{chip.label}</Badge>
                  {chip.detail != null && (
                    <Box variant="small" color="text-body-secondary">
                      {chip.detail}
                    </Box>
                  )}
                </Box>
              ))}
            </SpaceBetween>
          </ExpandableSection>

          <Box variant="awsui-key-label">Ranked hypotheses</Box>
          {artifact.payload.map((hyp, idx) => {
            const rankValue = hyp.rank.value;
            const descValue = hyp.description.value;
            const confidenceValueRaw = hyp.confidence.value;
            const confidenceValue = confidenceValueRaw != null ? confidenceValueRaw : 0;
            const componentValue = hyp.affectedComponent.value;
            const actionValue = hyp.recommendedAction.value;
            return (
              <Container
                key={idx}
                data-testid={`cs-workbench-hypothesis-${String(idx)}`}
              >
                <SpaceBetween size="xs">
                  <Box>
                    <Box variant="awsui-key-label">Rank {String(rankValue)} — {componentValue}</Box>
                    <Box>{descValue}</Box>
                  </Box>
                  <Box>
                    <ProgressBar
                      value={Math.round(confidenceValue * 100)}
                      label={`Confidence: ${String(Math.round(confidenceValue * 100))}%`}
                      data-testid={`cs-workbench-hypothesis-confidence-${String(idx)}`}
                    />
                  </Box>
                  <Box variant="small" color="text-body-secondary">
                    Recommended: {actionValue}
                  </Box>
                </SpaceBetween>
              </Container>
            );
          })}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};

// ---------------------------------------------------------------------------

interface ProposalStepProps {
  criteria: FleetPreviewCriteria;
  onCriteriaChange: (next: FleetPreviewCriteria) => void;
}

const AVAILABLE_MARKETS = ["US", "Germany", "India"];
const AVAILABLE_TCU_TIERS = ["TCU-1", "TCU-2", "TCU-3"];
const AVAILABLE_CONN_STATES = ["connected", "degraded", "ntn_fallback", "unreachable"];

const ProposalStep: React.FC<ProposalStepProps> = ({ criteria, onCriteriaChange }) => {
  // Helpers to toggle values in an array
  const toggleIn = (arr: string[], val: string): string[] =>
    arr.includes(val) ? arr.filter((x) => x !== val) : [...arr, val];

  return (
    <SpaceBetween size="m" data-testid="cs-workbench-proposal-container">
      <Container header={<Header variant="h3">Target Criteria</Header>}>
        <SpaceBetween size="m">
          {/* Markets */}
          <Box>
            <Box variant="awsui-key-label">Markets (empty = all)</Box>
            <SpaceBetween size="xs" direction="horizontal">
              {AVAILABLE_MARKETS.map((m) => (
                <Checkbox
                  key={m}
                  checked={criteria.markets.includes(m)}
                  onChange={({ detail }) => {
                    void detail;
                    onCriteriaChange({ ...criteria, markets: toggleIn(criteria.markets, m) });
                  }}
                  data-testid={`cs-workbench-proposal-market-${m}`}
                >
                  {m}
                </Checkbox>
              ))}
            </SpaceBetween>
          </Box>

          {/* TCU Tiers */}
          <Box>
            <Box variant="awsui-key-label">TCU tiers (empty = all)</Box>
            <SpaceBetween size="xs" direction="horizontal">
              {AVAILABLE_TCU_TIERS.map((t) => (
                <Checkbox
                  key={t}
                  checked={criteria.tcuTiers.includes(t)}
                  onChange={({ detail }) => {
                    void detail;
                    onCriteriaChange({ ...criteria, tcuTiers: toggleIn(criteria.tcuTiers, t) });
                  }}
                  data-testid={`cs-workbench-proposal-tcu-${t}`}
                >
                  {t}
                </Checkbox>
              ))}
            </SpaceBetween>
          </Box>

          {/* Connectivity States */}
          <Box>
            <Box variant="awsui-key-label">Connectivity states (empty = all)</Box>
            <SpaceBetween size="xs" direction="horizontal">
              {AVAILABLE_CONN_STATES.map((s) => (
                <Checkbox
                  key={s}
                  checked={criteria.connectivityStates.includes(s)}
                  onChange={({ detail }) => {
                    void detail;
                    onCriteriaChange({ ...criteria, connectivityStates: toggleIn(criteria.connectivityStates, s) });
                  }}
                  data-testid={`cs-workbench-proposal-state-${s}`}
                >
                  {s}
                </Checkbox>
              ))}
            </SpaceBetween>
          </Box>
        </SpaceBetween>
      </Container>

      {/*
        Seam 2: fleet-preview count — plain count component, visually distinct
        from the labelled-AI card in DiagnosisStep. FleetPreviewCount is a pure
        function component that imports no artifact-shaped fixture.
      */}
      <Container
        header={
          <Header
            variant="h3"
            description="Derived from fleet health fixture — no AI inference applied"
          >
            Target Set Preview
          </Header>
        }
        data-testid="cs-workbench-proposal-preview"
      >
        {/* Pass fleet rows from the fleet health fixture — not from diagnosisWorkbench.fixture */}
        <FleetPreviewCount rows={FLEET_ROWS} criteria={criteria} />
      </Container>
    </SpaceBetween>
  );
};

// ---------------------------------------------------------------------------

interface ApprovalStepProps {
  rationale: string;
  onRationaleChange: (val: string) => void;
  onApprove: () => void;
  onReject: () => void;
}

/**
 * ApprovalStep — Seam 1.
 *
 * This is a REAL screen state (a Wizard step), not a modal confirm.
 * The Approve button calls onApprove — a prop — never window.confirm.
 * The settleMarker APPROVAL_STEP_SETTLE_MARKER is rendered here and nowhere else.
 *
 * The seam guard (seam1ApprovalNotModal.test.ts) asserts:
 *   1. APPROVAL_STEP_SETTLE_MARKER appears in document when the stepper is on
 *      the Approval step.
 *   2. No call to window.confirm exists in this file's source.
 *   3. No element with role="dialog" exists when the Approve button is clicked.
 */
const ApprovalStep: React.FC<ApprovalStepProps> = ({
  rationale,
  onRationaleChange,
  onApprove,
  onReject,
}) => {
  const data = WORKBENCH_WORKFLOW.approvalData;

  // Extract .value before JSX
  const targetCountValue = data.targetVehicleCount.value;
  const bandwidthValue = data.bandwidthEstimateGb.value;
  const blastValue = data.blastRadiusEstimate.value;

  const rationaleValid = rationale.trim().length > 0;

  return (
    <SpaceBetween size="m" data-testid="cs-workbench-approval-container">
      {/*
        Seam 1 marker — unique to this step. The seam guard asserts it appears
        when the Approval step is active and is NOT reachable via any modal path.
      */}
      <span
        data-settle-marker={APPROVAL_STEP_SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
        data-testid="cs-workbench-approval-settle-marker"
      >
        {APPROVAL_STEP_SETTLE_MARKER}
      </span>

      <Container header={<Header variant="h3">Approval Summary</Header>}>
        <SpaceBetween size="s">
          <Box>
            <Box variant="awsui-key-label">Target vehicles</Box>
            <Box data-testid="cs-workbench-approval-target-count">{String(targetCountValue)}</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Estimated bandwidth</Box>
            <Box data-testid="cs-workbench-approval-bandwidth">{String(bandwidthValue)} GB</Box>
          </Box>
          <Box>
            <Box variant="awsui-key-label">Terrestrial cost</Box>
            <ProvenanceField
              field={data.terrestrialCostEstimate}
              label="approval-terrestrial-cost"
              testId="cs-workbench-approval-terrestrial-cost"
            />
          </Box>
          <Box>
            <Box variant="awsui-key-label">NTN cost</Box>
            <ProvenanceField
              field={data.ntnCostEstimate}
              label="approval-ntn-cost"
              testId="cs-workbench-approval-ntn-cost"
            />
          </Box>
          <Box>
            <Box variant="awsui-key-label">Blast radius</Box>
            <Box data-testid="cs-workbench-approval-blast-radius">{blastValue}</Box>
          </Box>
        </SpaceBetween>
      </Container>

      <Container header={<Header variant="h3">Approval Decision</Header>}>
        <SpaceBetween size="m">
          <Box>
            <Box variant="awsui-key-label">
              Rationale (required){" "}
              {!rationaleValid && (
                <Badge color="red" data-testid="cs-workbench-approval-rationale-required">
                  Required
                </Badge>
              )}
            </Box>
            <Textarea
              value={rationale}
              onChange={({ detail }) => { onRationaleChange(detail.value); }}
              placeholder="State the reason for approving or rejecting this campaign."
              rows={4}
              data-testid="cs-workbench-approval-rationale-input"
            />
          </Box>

          {/*
            Approve and Reject are plain Buttons, not window.confirm and not a Modal trigger.
            The Approve button is disabled until the rationale field is non-empty.
          */}
          <SpaceBetween size="s" direction="horizontal">
            <Button
              variant="primary"
              disabled={!rationaleValid}
              onClick={onApprove}
              data-testid="cs-workbench-approval-approve-button"
            >
              Approve
            </Button>
            <Button
              variant="normal"
              onClick={onReject}
              data-testid="cs-workbench-approval-reject-button"
            >
              Reject
            </Button>
          </SpaceBetween>

          {!rationaleValid && (
            <Alert type="warning" statusIconAriaLabel="Warning" data-testid="cs-workbench-approval-rationale-warning">
              A rationale is required before approving or rejecting this campaign.
            </Alert>
          )}
        </SpaceBetween>
      </Container>
    </SpaceBetween>
  );
};

// ---------------------------------------------------------------------------

const RolloutStep: React.FC = () => (
  <Container
    header={<Header variant="h3">Rollout</Header>}
    data-testid="cs-workbench-rollout-container"
  >
    <SpaceBetween size="s">
      <Alert type="info" statusIconAriaLabel="Info" data-testid="cs-workbench-rollout-handoff-alert">
        Rollout monitoring continues in the <strong>Software Campaigns</strong> screen (T5.5).
        Once approved, the campaign is visible there under its assigned ID.
      </Alert>
      <Box variant="p" color="text-body-secondary">
        The staged-progress monitor (canary → 10% → 50% → 100%), fault-rate chart, and
        widen/halt controls are in the Campaigns screen. Navigate there after approving.
      </Box>
    </SpaceBetween>
  </Container>
);

// ---------------------------------------------------------------------------

const RecordStep: React.FC = () => {
  const record = WORKBENCH_WORKFLOW.record;
  const rxswinTouchedValue = record.rxswinTouched.value ?? [];

  return (
    <Container
      header={<Header variant="h3">Campaign Record</Header>}
      data-testid="cs-workbench-record-container"
    >
      <SpaceBetween size="s">
        <Box>
          <Box variant="awsui-key-label">Software campaign ID</Box>
          <ProvenanceField
            field={record.softwareCampaignId}
            label="record-campaign-id"
            testId="cs-workbench-record-campaign-id"
          />
        </Box>
        <Box>
          <Box variant="awsui-key-label">
            RXSWIN identifiers touched (simulated)
          </Box>
          {/* RXSWIN — plain simulated field, Seam 3 deferred */}
          <Box data-testid="cs-workbench-record-rxswin">
            {rxswinTouchedValue.join(", ")}
          </Box>
        </Box>
        <Box>
          <Box variant="awsui-key-label">Approval rationale</Box>
          <ProvenanceField
            field={record.approvalRationale}
            label="record-rationale"
            testId="cs-workbench-record-rationale"
          />
        </Box>
        <Box>
          <Box variant="awsui-key-label">Rollout outcome</Box>
          <ProvenanceField
            field={record.rolloutOutcomeSummary}
            label="record-outcome"
            testId="cs-workbench-record-outcome"
          />
        </Box>
        <Box>
          <Box variant="awsui-key-label">Completed at</Box>
          <ProvenanceField
            field={record.completedAt}
            label="record-completed-at"
            testId="cs-workbench-record-completed-at"
          />
        </Box>
      </SpaceBetween>
    </Container>
  );
};

// ---------------------------------------------------------------------------
// DiagnosisWorkbenchView — root component
// ---------------------------------------------------------------------------

/**
 * DiagnosisWorkbenchView
 *
 * Route shapes:
 *   /software/workbench              → landing (no VIN: shows instruction panel)
 *   /software/workbench/:signalId    → VIN-scoped stepper
 *
 * The root settle marker (SETTLE_MARKER) appears in both states; the approval
 * step settle marker (APPROVAL_STEP_SETTLE_MARKER) appears only while the
 * stepper is on the Approval step.
 */
const DiagnosisWorkbenchView: React.FC = () => {
  const { signalId } = useParams<{ signalId?: string }>();
  const navigate = useNavigate();

  const vin = signalId != null ? decodeURIComponent(signalId) : null;
  const vinState = vin != null ? (VIN_SOFTWARE_STATES[vin] ?? null) : null;

  // Stepper state
  const [activeStep, setActiveStep] = useState(STEP_SIGNAL);
  const [approved, setApproved] = useState(false);
  const [rejected, setRejected] = useState(false);

  // Proposal criteria state
  const [criteria, setCriteria] = useState<FleetPreviewCriteria>({
    markets: [],
    tcuTiers: [],
    connectivityStates: [],
  });

  // Approval rationale state
  const [rationale, setRationale] = useState("");

  return (
    <SpaceBetween size="l">
      {/*
        Root settle marker — present in both the VIN-scoped stepper and the
        no-VIN landing state. The registry-completeness guard P1 strips <nav>
        and looks for this string in the content area.
      */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      {/* ------------------------------------------------------------------ */}
      {/* VIN-scoped header with back affordance                              */}
      {/* ------------------------------------------------------------------ */}
      {vin != null && (
        <Box>
          <Button
            variant="link"
            onClick={() => navigate(`/connectivity/subscriber-lookup/${encodeURIComponent(vin)}`)}
            data-testid="cs-workbench-back-to-subscriber"
          >
            ← Back to Subscriber Lookup for {vin}
          </Button>
        </Box>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* No-VIN landing state                                                */}
      {/* ------------------------------------------------------------------ */}
      {vin == null && (
        <Container
          header={<Header variant="h2">Diagnosis Workbench</Header>}
          data-testid="cs-workbench-landing"
        >
          <SpaceBetween size="s">
            <Box variant="p">
              To diagnose a vehicle's software state, navigate to{" "}
              <strong>Subscriber Lookup</strong>, open a VIN's detail view, and
              click <strong>View software history for this VIN</strong>.
            </Box>
          </SpaceBetween>
        </Container>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* VIN-scoped: not found in fixture catalogue                          */}
      {/* ------------------------------------------------------------------ */}
      {vin != null && vinState == null && (
        <Alert
          type="warning"
          statusIconAriaLabel="Warning"
          data-testid="cs-workbench-vin-not-found"
        >
          No software state found for VIN <strong>{vin}</strong> in the
          simulated data set. Try navigating here via the Subscriber Lookup
          detail for a degraded or unreachable VIN.
        </Alert>
      )}

      {/* ------------------------------------------------------------------ */}
      {/* VIN-scoped: six-step stepper                                        */}
      {/* ------------------------------------------------------------------ */}
      {vin != null && vinState != null && (
        <SpaceBetween size="m">
          {approved && (
            <Alert type="success" statusIconAriaLabel="Success" data-testid="cs-workbench-approved-banner">
              Campaign approved. View progress in <strong>Software Campaigns</strong>.
            </Alert>
          )}
          {rejected && (
            <Alert type="warning" statusIconAriaLabel="Warning" data-testid="cs-workbench-rejected-banner">
              Campaign rejected.
            </Alert>
          )}

          <Container
            data-testid="cs-workbench-stepper-container"
            header={
              <Header variant="h2">
                {"OTA Campaign Workflow for "}{vin}
              </Header>
            }
          >
            <Wizard
              data-testid="cs-workbench-stepper"
              activeStepIndex={activeStep}
              onNavigate={({ detail }) => { setActiveStep(detail.requestedStepIndex); }}
              onSubmit={() => { /* record step is the final step — no submit action */ }}
              onCancel={() => { navigate(`/connectivity/subscriber-lookup/${encodeURIComponent(vin)}`); }}
              steps={[
                {
                  title: "Signal",
                  content: <SignalStep vin={vin} />,
                },
                {
                  title: "Diagnosis",
                  content: <DiagnosisStep />,
                },
                {
                  title: "Proposal",
                  content: (
                    <ProposalStep
                      criteria={criteria}
                      onCriteriaChange={setCriteria}
                    />
                  ),
                },
                {
                  title: "Approval",
                  content: (
                    <ApprovalStep
                      rationale={rationale}
                      onRationaleChange={setRationale}
                      onApprove={() => {
                        setApproved(true);
                        setRejected(false);
                        setActiveStep(STEP_ROLLOUT);
                      }}
                      onReject={() => {
                        setRejected(true);
                        setApproved(false);
                        setActiveStep(STEP_ROLLOUT);
                      }}
                    />
                  ),
                },
                {
                  title: "Rollout",
                  content: <RolloutStep />,
                },
                {
                  title: "Record",
                  content: <RecordStep />,
                  isOptional: false,
                },
              ]}
            />
          </Container>
        </SpaceBetween>
      )}
    </SpaceBetween>
  );
};

export default DiagnosisWorkbenchView;
