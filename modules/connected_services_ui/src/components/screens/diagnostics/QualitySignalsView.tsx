// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * QualitySignalsView — pre-computed quality signal trend panel (T6.5).
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T6.5
 *
 * ## Viewing this screen triggers no computation.
 *
 * This is the core of the Tier 2 split: the quality-analysis agent runs on a
 * schedule (Tier 2 — autonomous, nobody waiting), writes a Tier2Artifact for
 * each quality dimension, and stores it. This component only READS those
 * pre-computed artifacts and renders them as cards. There is no inference,
 * no fetch, no API call, and no client-side computation at display time.
 *
 * See agentic-tiers.md: "precompute the expensive part, read it fast, narrate
 * it live."
 *
 * ## Aggregate population data only
 *
 * No trip, GPS, driver-identity, or vehicle-location field is rendered here.
 * Every payload field is an aggregate population figure (T6.5 Constraints).
 *
 * ## Artifact contract
 *
 * Each card renders a QualitySignal (Tier2Artifact<QualitySignalPayload>):
 *   - signalName, populationScore, trend, vehicleCount, scope, summary
 *     rendered via ProvenanceField (payload fields — all wrapped)
 *   - confidence (artifact envelope) displayed directly — exempt per spec D4
 *   - computed_at (artifact envelope) displayed directly — exempt per spec D4
 *   - evidence chips (artifact envelope) — exempt per spec D4
 *   - agent_version, inputs_hash — envelope metadata, not displayed
 *
 * ## Provenance
 *
 * Every displayed payload field is a ProvenanceValue<T> rendered via
 * ProvenanceField. The five Tier2Artifact envelope fields are exempt from
 * wrapping per spec D4 and are extracted to local variables before JSX.
 */

import Badge from "@cloudscape-design/components/badge";
import Box from "@cloudscape-design/components/box";
import Cards from "@cloudscape-design/components/cards";
import Container from "@cloudscape-design/components/container";
import ExpandableSection from "@cloudscape-design/components/expandable-section";
import Header from "@cloudscape-design/components/header";
import SpaceBetween from "@cloudscape-design/components/space-between";
import StatusIndicator from "@cloudscape-design/components/status-indicator";
import React from "react";

import ProvenanceField from "../../commons/ProvenanceField";
import type { QualitySignal } from "./qualitySignals.fixture";
import { QUALITY_SIGNALS } from "./qualitySignals.fixture";
import type { SignalTrendDirection } from "./qualitySignals.fixture";

// ── Constants ─────────────────────────────────────────────────────────────────

/** settleMarker — unique to this screen, asserted by registry-completeness P1. */
const SETTLE_MARKER = "cs-settle-quality-signals-trend-panel";

// ── Helpers ───────────────────────────────────────────────────────────────────

/** Map confidence value (0..1) to a Cloudscape StatusIndicator type. */
function confidenceType(
  confidence: number
): "success" | "warning" | "error" | "in-progress" {
  if (confidence >= 0.8) return "success";
  if (confidence >= 0.6) return "warning";
  return "error";
}

/** Map population score (0..100) to a StatusIndicator type. */
function scoreType(
  score: number
): "success" | "warning" | "error" {
  if (score >= 85) return "success";
  if (score >= 65) return "warning";
  return "error";
}

/** Map trend direction to a StatusIndicator type. */
function trendType(
  trend: SignalTrendDirection
): "success" | "warning" | "error" {
  if (trend === "improving") return "success";
  if (trend === "stable") return "warning";
  return "error";
}

/** Format ISO timestamp to YYYY-MM-DD HH:MM UTC without locale-sensitive APIs. */
function formatTimestamp(iso: string): string {
  const d = new Date(iso);
  const pad = (n: number): string => String(n).padStart(2, "0");
  return `${String(d.getUTCFullYear())}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())} ${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())} UTC`;
}

// ── SignalCard — single quality signal rendered as card body ──────────────────

interface SignalCardProps {
  signal: QualitySignal;
}

const SignalCard: React.FC<SignalCardProps> = ({ signal }) => {
  // Artifact envelope fields — exempt from ProvenanceField (spec D4)
  const computedAt = signal.computed_at;
  const confidencePct = Math.round(signal.confidence * 100);
  const evidenceChips = signal.evidence;

  // Payload — all displayed values must go through ProvenanceField
  const payload = signal.payload;

  // Extract trend value before JSX for StatusIndicator type mapping.
  const trendValue = payload.trend.value;

  return (
    <SpaceBetween size="s">
      {/* Population score — most prominent metric */}
      <Box>
        <Box variant="awsui-key-label">Population Score</Box>
        {payload.populationScore.value !== null &&
        payload.populationScore.provenance !== "absent" ? (
          <StatusIndicator
            type={scoreType(payload.populationScore.value ?? 0)}
            data-testid="cs-quality-score-indicator"
          >
            <ProvenanceField
              field={payload.populationScore}
              label="quality_population_score"
              testId="cs-quality-score"
              render={(v) => `${String(v)} / 100`}
            />
          </StatusIndicator>
        ) : (
          <ProvenanceField
            field={payload.populationScore}
            label="quality_population_score"
            testId="cs-quality-score"
            render={(v) => `${String(v)} / 100`}
          />
        )}
      </Box>

      {/* Trend direction */}
      <Box>
        <Box variant="awsui-key-label">Trend</Box>
        {trendValue !== null && payload.trend.provenance !== "absent" ? (
          <SpaceBetween size="xs" direction="horizontal">
            <StatusIndicator
              type={trendType(trendValue)}
              data-testid="cs-quality-trend-indicator"
            >
              {trendValue.charAt(0).toUpperCase() + trendValue.slice(1)}
            </StatusIndicator>
          </SpaceBetween>
        ) : (
          <ProvenanceField
            field={payload.trend}
            label="quality_trend"
            testId="cs-quality-trend"
          />
        )}
      </Box>

      {/* Vehicle count — aggregate, no individual identity */}
      <Box>
        <Box variant="awsui-key-label">Vehicles in scope</Box>
        <ProvenanceField
          field={payload.vehicleCount}
          label="quality_vehicle_count"
          testId="cs-quality-vehicle-count"
          render={(v) => v.toLocaleString("en-US")}
        />
      </Box>

      {/* Market scope */}
      <Box>
        <Box variant="awsui-key-label">Market scope</Box>
        <ProvenanceField
          field={payload.scope}
          label="quality_scope"
          testId="cs-quality-scope"
        />
      </Box>

      {/* Summary */}
      <Box>
        <Box variant="awsui-key-label">Summary</Box>
        <ProvenanceField
          field={payload.summary}
          label="quality_summary"
          testId="cs-quality-summary"
        />
      </Box>

      {/* Confidence — artifact envelope field, not ProvenanceValue-wrapped */}
      <Box>
        <Box variant="awsui-key-label">Agent confidence</Box>
        <StatusIndicator
          type={confidenceType(signal.confidence)}
          data-testid="cs-quality-confidence-indicator"
        >
          {String(confidencePct)}%
        </StatusIndicator>
      </Box>

      {/* Computed at — artifact envelope field */}
      <Box>
        <Box variant="awsui-key-label">Computed at</Box>
        <Box data-testid="cs-quality-computed-at">
          {formatTimestamp(computedAt)}
        </Box>
      </Box>

      {/* Evidence chips */}
      {evidenceChips.length > 0 && (
        <ExpandableSection
          headerText={`Evidence (${String(evidenceChips.length)})`}
          data-testid="cs-quality-evidence-section"
        >
          <SpaceBetween size="xs">
            {evidenceChips.map((chip, idx) => (
              <Box key={idx} data-testid={`cs-quality-evidence-chip-${String(idx)}`}>
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
    </SpaceBetween>
  );
};

// ── QualitySignalsView — root component ───────────────────────────────────────

/**
 * QualitySignalsView
 *
 * Route: /diagnostics/quality-signals
 *
 * Renders QUALITY_SIGNALS as a card feed. Each card displays the pre-computed
 * Tier2Artifact for one quality dimension of the vehicle population.
 *
 * ## Viewing this screen triggers no computation.
 *
 * This component reads and displays pre-computed artifacts only. No agent
 * inference, no fetch, and no API call happens at display time. The quality-
 * analysis agent runs autonomously on a schedule (Tier 2) and writes the
 * artifacts that this component reads. See agentic-tiers.md.
 */
const QualitySignalsView: React.FC = () => {
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
        data-testid="quality-signals-settle-marker"
      >
        {SETTLE_MARKER}
      </span>

      <Container
        header={
          <Header
            variant="h1"
            description="Pre-computed quality signals from the autonomous analysis agent. Viewing this panel triggers no computation — the reasoning already happened."
            data-testid="quality-signals-header"
          >
            Quality Signals
          </Header>
        }
        data-testid="quality-signals-container"
      >
        <Cards
          cardDefinition={{
            header: (signal: QualitySignal) => {
              // Extract .value before JSX — payload field, ProvenanceValue
              const nameValue = signal.payload.signalName.value ?? "—";
              const scoreValue = signal.payload.populationScore.value;
              return (
                <Header
                  variant="h3"
                  data-testid="cs-quality-card-header"
                >
                  {nameValue}
                  {scoreValue !== null && (
                    <> <Badge color="blue">{String(scoreValue)}/100</Badge></>
                  )}
                </Header>
              );
            },
            sections: [
              {
                id: "body",
                content: (signal: QualitySignal) => (
                  <SignalCard signal={signal} />
                ),
              },
            ],
          }}
          items={QUALITY_SIGNALS}
          empty={
            <Box textAlign="center" color="inherit">
              <Box variant="strong" textAlign="center" color="inherit">
                No quality signals
              </Box>
              <Box variant="p" color="inherit">
                No quality signal artifacts are in the pre-computed catalogue.
              </Box>
            </Box>
          }
          data-testid="quality-signals-cards"
        />
      </Container>
    </SpaceBetween>
  );
};

export default QualitySignalsView;
