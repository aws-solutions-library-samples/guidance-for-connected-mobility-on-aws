// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * PmComplianceRollup — summary panel for PM compliance over a fleet and window.
 *
 * Reads GET /api/v1/fleet-intelligence/pm/compliance.
 * Compliance = completed_on_time / total_scheduled (spec § D6).
 * Badge is visible at panel level (all simulated today).
 */

import React, { useEffect, useState } from "react";
import {
  Alert,
  Box,
  ColumnLayout,
  Container,
  Header,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
} from "@cloudscape-design/components";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import { ProvenanceBadge } from "../provenance";
import type {
  PmComplianceRollup as PmComplianceRollupData,
  PmComplianceApiResponse,
} from "./types";
import { toPmComplianceRollup } from "./types";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

interface PmComplianceRollupProps {
  fleetId?: string;
  /** ISO date strings for the compliance window — defaults to trailing 30 days */
  windowStart?: string;
  windowEnd?: string;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const PmComplianceRollup: React.FC<PmComplianceRollupProps> = ({
  fleetId,
  windowStart,
  windowEnd,
}) => {
  const [rollup, setRollup] = useState<PmComplianceRollupData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);

    const base = getApiEndpoint().replace(/\/$/, "");
    const params = new URLSearchParams();
    if (fleetId) params.set("fleetId", fleetId);
    if (windowStart) params.set("windowStart", windowStart);
    if (windowEnd) params.set("windowEnd", windowEnd);

    const url = `${base}/api/v1/fleet-intelligence/pm/compliance${
      params.toString() ? `?${params.toString()}` : ""
    }`;

    authFetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      // Map the handler's shape (rate 0-1, window:{start,end}) onto the view
      // model (complianceRate 0-100, windowStart/windowEnd). Reading the raw
      // response directly used to crash on rollup.complianceRate.toFixed().
      .then((d: PmComplianceApiResponse) => setRollup(toPmComplianceRollup(d)))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e))
      )
      .finally(() => setLoading(false));
  }, [fleetId, windowStart, windowEnd]);

  // Compliance rating
  const rate = rollup?.complianceRate ?? 0;
  const complianceStatus =
    rate >= 90 ? "success" : rate >= 70 ? "warning" : "error";

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={
            <SpaceBetween size="xs" direction="horizontal">
              <span>PM completion vs schedule within the window</span>
              {rollup && (
                <ProvenanceBadge
                  provenance={rollup.provenance}
                  variant="panel"
                />
              )}
            </SpaceBetween>
          }
        >
          PM Compliance
        </Header>
      }
    >
      {error ? (
        <Alert type="error" header="Failed to load compliance data">
          {error}
        </Alert>
      ) : loading ? (
        <Box color="text-body-secondary">Loading compliance data…</Box>
      ) : !rollup ? (
        <Box color="text-body-secondary">No compliance data available</Box>
      ) : (
        <SpaceBetween size="l">
          {/* Overall compliance rate */}
          <ProgressBar
            value={rollup.complianceRate}
            label="Overall PM compliance rate"
            description={`${rollup.complianceRate.toFixed(1)}% of scheduled PMs completed on time`}
            additionalInfo={`Window: ${rollup.windowStart} – ${rollup.windowEnd}`}
            status={
              complianceStatus === "error"
                ? "error"
                : complianceStatus === "warning"
                ? "in-progress"
                : "success"
            }
            data-testid="compliance-progress"
          />

          {/* KPI tiles */}
          <ColumnLayout columns={3}>
            <Box>
              <Box variant="awsui-key-label">Scheduled</Box>
              <Box
                variant="h2"
                data-testid="compliance-total-scheduled"
              >
                {rollup.totalScheduled}
              </Box>
            </Box>

            <Box>
              <Box variant="awsui-key-label">Completed on time</Box>
              <Box variant="h2">
                <StatusIndicator type="success">
                  {rollup.completedOnTime}
                </StatusIndicator>
              </Box>
            </Box>

            <Box>
              <Box variant="awsui-key-label">Overdue</Box>
              <Box variant="h2">
                <StatusIndicator
                  type={rollup.overdueCount > 0 ? "error" : "success"}
                  data-testid="compliance-overdue-count"
                >
                  {rollup.overdueCount}
                </StatusIndicator>
              </Box>
            </Box>
          </ColumnLayout>
        </SpaceBetween>
      )}
    </Container>
  );
};

export default PmComplianceRollup;
