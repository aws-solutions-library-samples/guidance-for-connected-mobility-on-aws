// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * FleetPreviewCount — Seam 2 component.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T5.4
 *
 * ## Seam 2 contract
 *
 * The "N vehicles match" preview is computed by a pure function over the fleet
 * rows and rendered as a plain count. This component must NOT import any
 * artifact-shaped fixture (Tier2Artifact / DiagnosisArtifact / WorkbenchWorkflow).
 * The seam guard (src/components/screens/software/__tests__/seam2PreviewNoArtifact.test.ts)
 * enforces this by inspecting this file's imports at test time.
 *
 * The distinction is visible in the UI: this is a plain integer count element,
 * positioned below the AI-backed diagnosis card and labelled differently.
 *
 * ## Inputs
 *
 * Accepts a subset of fleet rows directly as a prop. The caller (DiagnosisWorkbenchView)
 * passes the fleet rows from the fixture; this component performs the filter.
 * No fixture module is imported here — only the types needed to describe the filter.
 *
 * ## No location fields
 *
 * No trip/GPS/location/odometer field appears anywhere in this file.
 */

import Box from "@cloudscape-design/components/box";
import React from "react";
import type { FleetHealthRow } from "../connectivity/fleetHealth.fixture";

// ---------------------------------------------------------------------------
// Pure filter function (exported for unit testing)
// ---------------------------------------------------------------------------

export interface FleetPreviewCriteria {
  /** Market filter — empty means all markets. */
  markets: string[];
  /** TCU tier filter — empty means all tiers. */
  tcuTiers: string[];
  /** Connectivity state filter — empty means all states. */
  connectivityStates: string[];
}

/**
 * computeFleetMatchCount — pure function.
 *
 * Counts how many rows from `rows` satisfy all three filter dimensions.
 * Empty arrays mean "no filter applied for this dimension" (match everything).
 * This function has no side effects and no imports from any fixture module.
 */
export function computeFleetMatchCount(
  rows: FleetHealthRow[],
  criteria: FleetPreviewCriteria
): number {
  return rows.filter((row) => {
    const marketOk =
      criteria.markets.length === 0 || criteria.markets.includes(row.market.value ?? "");
    const tcuOk =
      criteria.tcuTiers.length === 0 || criteria.tcuTiers.includes(row.tcuTier.value ?? "");
    const stateOk =
      criteria.connectivityStates.length === 0 ||
      criteria.connectivityStates.includes(row.connectivityState.value ?? "");
    return marketOk && tcuOk && stateOk;
  }).length;
}

// ---------------------------------------------------------------------------
// FleetPreviewCount component
// ---------------------------------------------------------------------------

interface FleetPreviewCountProps {
  /** Fleet rows to filter. Passed in from the caller — not imported here. */
  rows: FleetHealthRow[];
  /** Filter criteria. */
  criteria: FleetPreviewCriteria;
}

/**
 * FleetPreviewCount
 *
 * Renders a plain integer count of vehicles matching the given criteria.
 * Visually distinct from the AI diagnosis card — no badge colour, no confidence
 * score, no evidence chips. Just a number and a label.
 *
 * This component is intentionally minimal: the count is computed deterministically,
 * and the test that verifies it is a property test, not a snapshot.
 */
const FleetPreviewCount: React.FC<FleetPreviewCountProps> = ({ rows, criteria }) => {
  const count = computeFleetMatchCount(rows, criteria);
  return (
    <Box
      data-testid="cs-workbench-fleet-preview-count"
      data-count={String(count)}
    >
      <Box variant="awsui-key-label">Target vehicles matching criteria</Box>
      <Box
        variant="h1"
        data-testid="cs-workbench-fleet-preview-number"
        fontWeight="bold"
      >
        {count}
      </Box>
      <Box variant="small" color="text-body-secondary">
        Derived from fleet health fixture — simulated data
      </Box>
    </Box>
  );
};

export default FleetPreviewCount;
