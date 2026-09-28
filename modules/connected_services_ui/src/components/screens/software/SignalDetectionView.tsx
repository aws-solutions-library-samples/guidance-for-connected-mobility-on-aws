// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * SignalDetectionView — pre-computed fault-signal card feed (T5.3).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.3
 *
 * ## Viewing this screen triggers no computation.
 *
 * This is the core of the Tier 2 split: the fault-detection agent runs on a
 * schedule (Tier 2 — autonomous, nobody waiting), writes a Tier2Artifact for
 * each detected signal, and stores it. This component only READS those
 * pre-computed artifacts and renders them as cards. There is no inference,
 * no fetch, no API call, and no client-side computation at display time.
 *
 * See agentic-tiers.md: "precompute the expensive part, read it fast, narrate
 * it live."
 *
 * ## Card feed
 *
 * Cards are rendered newest-first (descending computed_at). Each card shows:
 *   - Fault signature
 *   - Affected-vehicle-count estimate (with provenance)
 *   - Confidence (from artifact envelope — exempt from ProvenanceField)
 *   - Computed at (from artifact envelope — exempt from ProvenanceField)
 *   - Evidence chips
 *   - "Diagnose" button → Diagnosis Workbench pre-loaded with the signal's VIN
 *
 * ## Click-through
 *
 * Clicking "Diagnose" navigates to /software/workbench/:signalId where
 * signalId is the first triggering VIN from the signal's payload. The
 * Diagnosis Workbench (DiagnosisWorkbenchView) accepts this shape via
 * useParams<{ signalId?: string }>.
 *
 * ## Provenance
 *
 * Every displayed payload field is a ProvenanceValue<T> rendered via
 * ProvenanceField. The five Tier2Artifact envelope fields (computed_at,
 * confidence, evidence, agent_version, inputs_hash) are exempt from wrapping
 * per spec D4 and are extracted to local variables before JSX.
 *
 * ## No location fields
 *
 * No trip / GPS / location / odometer field anywhere in this file.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Button from "@cloudscape-design/components/button";
import Cards from "@cloudscape-design/components/cards";
import Container from "@cloudscape-design/components/container";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import React from "react";
import { useNavigate } from "react-router-dom";
import ProvenanceField from "../../commons/ProvenanceField";
import { DETECTED_SIGNALS } from "./signalDetection.fixture";
import type { DetectedSignal } from "./signalDetection.fixture";

// ---------------------------------------------------------------------------
// Settle marker
// ---------------------------------------------------------------------------

const SETTLE_MARKER = "cs-settle-signal-detection-card-feed";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Map confidence value (0..1) to a Cloudscape StatusIndicator type. */
function confidenceType(
  confidence: number
): "success" | "warning" | "error" | "in-progress" {
  if (confidence >= 0.8) return "success";
  if (confidence >= 0.6) return "warning";
  return "error";
}

/** Format ISO timestamp to a readable string without extracting location. */
function formatTimestamp(iso: string): string {
  // Formats as YYYY-MM-DD HH:MM UTC without using location-sensitive locale APIs.
  const d = new Date(iso);
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${String(d.getUTCFullYear())}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

// ---------------------------------------------------------------------------
// SignalCard — single signal rendered as a card body
// ---------------------------------------------------------------------------

interface SignalCardProps {
  signal: DetectedSignal;
  onDiagnose: (signalId: string) => void;
}

const SignalCard: React.FC<SignalCardProps> = ({ signal, onDiagnose }) => {
  // Artifact envelope fields — exempt from ProvenanceField (spec D4)
  const computedAt = signal.computed_at;
  const confidencePct = Math.round(signal.confidence * 100);
  const evidenceChips = signal.evidence;

  // Payload — all displayed values must go through ProvenanceField
  const payload = signal.payload;

  // Extract the first triggering VIN to use as the signalId for click-through.
  // Using .value here is intentional: we need the raw string for the navigate call.
  const triggeringVinsValue = payload.triggeringVins.value ?? [];
  const firstVin = triggeringVinsValue[0] ?? "";

  return (
    <SpaceBetween size="s">
      {/* Fault signature + market badge */}
      <SpaceBetween size="xs" direction="horizontal">
        <Box fontWeight="bold" data-testid="cs-signal-fault-signature-label">
          <ProvenanceField
            field={payload.faultSignature}
            label="signal-fault-signature"
            testId="cs-signal-fault-signature"
          />
        </Box>
        <ProvenanceField
          field={payload.market}
          label="signal-market"
          testId="cs-signal-market"
        />
      </SpaceBetween>

      {/* Description */}
      <ProvenanceField
        field={payload.description}
        label="signal-description"
        testId="cs-signal-description"
      />

      {/* Connectivity state */}
      <Box>
        <Box variant="awsui-key-label">Connectivity state</Box>
        <ProvenanceField
          field={payload.connectivityState}
          label="signal-conn-state"
          testId="cs-signal-conn-state"
        />
      </Box>

      {/* Affected vehicle count estimate */}
      <Box>
        <Box variant="awsui-key-label">Affected vehicles (estimate)</Box>
        <ProvenanceField
          field={payload.affectedVehicleCountEstimate}
          label="signal-affected-count"
          testId="cs-signal-affected-count"
          render={(v) => String(v)}
        />
      </Box>

      {/* Confidence — artifact envelope field, not ProvenanceValue-wrapped */}
      <Box>
        <Box variant="awsui-key-label">Agent confidence</Box>
        <StatusIndicator
          type={confidenceType(signal.confidence)}
          data-testid="cs-signal-confidence-indicator"
        >
          {String(confidencePct)}%
        </StatusIndicator>
      </Box>

      {/* Computed at — artifact envelope field */}
      <Box>
        <Box variant="awsui-key-label">Computed at</Box>
        <Box data-testid="cs-signal-computed-at">{formatTimestamp(computedAt)}</Box>
      </Box>

      {/* Evidence chips */}
      {evidenceChips.length > 0 && (
        <ExpandableSection
          headerText={`Evidence (${String(evidenceChips.length)})`}
          data-testid="cs-signal-evidence-section"
        >
          <SpaceBetween size="xs">
            {evidenceChips.map((chip, idx) => (
              <Box key={idx} data-testid={`cs-signal-evidence-chip-${String(idx)}`}>
                <Badge color="blue">{chip.label}</Badge>
                {chip.detail != null && chip.detail.length > 0 && (
                  <Box variant="small" color="text-body-secondary">
                    {chip.detail}
                  </Box>
                )}
              </Box>
            ))}
          </SpaceBetween>
        </ExpandableSection>
      )}

      {/* Click-through to Diagnosis Workbench */}
      <Box>
        <Button
          variant="primary"
          disabled={firstVin.length === 0}
          onClick={() => { onDiagnose(firstVin); }}
          data-testid="cs-signal-diagnose-button"
        >
          Diagnose
        </Button>
      </Box>
    </SpaceBetween>
  );
};

// ---------------------------------------------------------------------------
// SignalDetectionView — root component
// ---------------------------------------------------------------------------

/**
 * SignalDetectionView
 *
 * Route: /software/signals
 *
 * Renders DETECTED_SIGNALS as a card feed, newest first. Each card displays
 * the pre-computed Tier2Artifact for a detected fault signal and provides a
 * "Diagnose" button that navigates to the Diagnosis Workbench pre-loaded with
 * the signal's triggering VIN.
 *
 * ## Viewing this screen triggers no computation.
 *
 * This component reads and displays pre-computed artifacts only. No agent
 * inference, no fetch, and no API call happens at display time. The fault-
 * detection agent runs autonomously on a schedule (Tier 2) and writes the
 * artifacts that this component reads. See agentic-tiers.md.
 */
const SignalDetectionView: React.FC = () => {
  const navigate = useNavigate();

  const handleDiagnose = (vin: string): void => {
    navigate(`/software/workbench/${encodeURIComponent(vin)}`);
  };

  return (
    <SpaceBetween size="l">
      {/*
        Settle marker — the registry-completeness guard P1 strips <nav> and
        asserts this string appears in the content area.
      */}
      <span
        data-settle-marker={SETTLE_MARKER}
        style={{ display: "none" }}
        aria-hidden="true"
      >
        {SETTLE_MARKER}
      </span>

      <Container
        header={
          <Header
            variant="h1"
            description="Pre-computed fault signals from the autonomous detection agent. Viewing this feed triggers no computation — the reasoning already happened."
          >
            Signal Detection
          </Header>
        }
        data-testid="cs-signal-detection-container"
      >
        <Cards
          cardDefinition={{
            header: (signal: DetectedSignal) => {
              // Extract .value before JSX
              const faultSigValue =
                signal.payload.faultSignature.value ?? "—";
              const marketValue = signal.payload.market.value ?? "—";
              return (
                <Header
                  variant="h3"
                  data-testid="cs-signal-card-header"
                >
                  {faultSigValue}
                  {" "}
                  <Badge color="grey">{marketValue}</Badge>
                </Header>
              );
            },
            sections: [
              {
                id: "body",
                content: (signal: DetectedSignal) => (
                  <SignalCard signal={signal} onDiagnose={handleDiagnose} />
                ),
              },
            ],
          }}
          items={DETECTED_SIGNALS}
          empty={
            <Box textAlign="center" color="inherit">
              <Box variant="strong" textAlign="center" color="inherit">
                No signals detected
              </Box>
              <Box variant="p" color="inherit">
                No fault signals are in the pre-computed artifact catalogue.
              </Box>
            </Box>
          }
          data-testid="cs-signal-detection-cards"
        />
      </Container>
    </SpaceBetween>
  );
};

export default SignalDetectionView;
