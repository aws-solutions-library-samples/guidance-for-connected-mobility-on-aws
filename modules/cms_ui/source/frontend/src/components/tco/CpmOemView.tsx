// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * CpmOemView — CPM (cost-per-mile) breakdown grouped by OEM / vehicle make.
 *
 * Reads `GET /api/v1/fleet-intelligence/cpm?groupBy=oem` which is served by the
 * new `services/fleet_intelligence/` handler (spec § D2).
 *
 * Design constraints:
 *   - Every figure carries a <ProvenanceBadge> — cost rows are simulated today
 *     (see spec § F2 + § D1). The badge must be visible, not hover-only.
 *   - Existing /api/v1/tco/* panels in FleetCostDashboard remain untouched.
 *     This view is additive, not a replacement.
 *   - Cloudscape design tokens only — no literal hex.
 */

import React, { useEffect, useState } from "react";
import {
  Box,
  Container,
  Header,
  SpaceBetween,
  StatusIndicator,
  Table,
  Alert,
} from "@cloudscape-design/components";
import BarChart from "@cloudscape-design/components/bar-chart";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import { ProvenanceBadge } from "../provenance";
import type { ProvenanceValue } from "../provenance";

// ---------------------------------------------------------------------------
// Types matching the fleet-intelligence CPM API response (spec § D2)
// ---------------------------------------------------------------------------

export interface CpmOemItem {
  /** Group key — for groupBy=oem this is the vehicle make, e.g. "DemoMotors" */
  group: string;
  /** Cost per mile for this group (total cost / total miles) */
  cpm: number;
  /** Total cost across all vehicles and months in the window */
  totalCost: number;
  /** Total miles across the same window */
  totalMiles: number;
  /** Distinct vehicles contributing to this group's figure */
  vehicleCount: number;
}

export interface CpmOemResponse {
  groupBy: "oem";
  yearMonth?: string;
  /**
   * The handler returns `data`, not `items`, and each row carries `group`/`cpm`.
   * Contract source of truth: services/fleet_intelligence/cpm.py::group_cpm_by,
   * pinned by services/fleet_intelligence/tests/test_cpm.py (§ D2).
   * See issues/2026-09-03-fleet-intelligence-ui-api-contract-mismatch/.
   */
  data: CpmOemItem[];
  provenance: ProvenanceValue;
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const fmtCpm = (n: number) => `$${n.toFixed(3)}/mi`;
const fmtMoney = (n: number) => {
  if (n >= 1_000_000) return `$${(n / 1_000_000).toFixed(1)}M`;
  if (n >= 1_000) return `$${(n / 1_000).toFixed(1)}K`;
  return `$${n.toFixed(2)}`;
};

const chartI18n = {
  filterLabel: "Filter",
  filterPlaceholder: "Filter data",
  filterSelectedAriaLabel: "selected",
  legendAriaLabel: "Legend",
  chartAriaRoleDescription: "bar chart",
  xAxisAriaRoleDescription: "x axis",
  yAxisAriaRoleDescription: "y axis",
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

interface CpmOemViewProps {
  /** Optional fleet filter — forwarded as query param if provided */
  fleetId?: string;
}

export const CpmOemView: React.FC<CpmOemViewProps> = ({ fleetId }) => {
  const [data, setData] = useState<CpmOemResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);

    const base = getApiEndpoint().replace(/\/$/, "");
    const params = new URLSearchParams({ groupBy: "oem" });
    if (fleetId) params.set("fleetId", fleetId);

    authFetch(`${base}/api/v1/fleet-intelligence/cpm?${params.toString()}`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: CpmOemResponse) => setData(d))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e))
      )
      .finally(() => setLoading(false));
  }, [fleetId]);

  const items = data?.data ?? [];
  const panelProvenance: ProvenanceValue = data?.provenance ?? null;

  // Build bar-chart series from the items
  const chartSeries =
    items.length > 0
      ? [
          {
            title: "CPM (cents/mile × 100)",
            type: "bar" as const,
            data: items.map((item) => ({
              x: item.group,
              y: Math.round(item.cpm * 100),
            })),
          },
        ]
      : [];
  const chartXDomain = items.map((item) => item.group);
  const chartYMax =
    items.length > 0
      ? Math.ceil(Math.max(...items.map((i) => i.cpm * 100)) * 1.25)
      : 100;

  return (
    <Container
      header={
        <Header
          variant="h2"
          description={
            <SpaceBetween size="xs" direction="horizontal">
              <span>Cost per mile by OEM — the PRD payoff metric</span>
              {/* Panel-level badge: entire dataset is simulated today */}
              <ProvenanceBadge provenance={panelProvenance} variant="panel" />
            </SpaceBetween>
          }
        >
          CPM by OEM
        </Header>
      }
    >
      {error ? (
        <Alert type="error" header="Failed to load CPM data">
          {error}
        </Alert>
      ) : (
        <SpaceBetween size="l">
          {/* Bar chart — visual comparison across OEMs */}
          <BarChart
            height={240}
            statusType={loading ? "loading" : "finished"}
            loadingText="Loading CPM data…"
            xDomain={chartXDomain}
            xScaleType="categorical"
            xTitle="OEM"
            yTitle="Cost (¢/mi)"
            series={chartSeries}
            hideFilter
            ariaLabel="CPM by OEM"
            i18nStrings={chartI18n}
            detailPopoverSeriesContent={({ series, y }) => ({
              key: series.title,
              value: `$${(y / 100).toFixed(3)}/mi`,
            })}
            empty={
              <Box textAlign="center" color="inherit">
                <b>No CPM data available</b>
              </Box>
            }
            noMatch={
              <Box textAlign="center" color="inherit">
                <b>No matching data</b>
              </Box>
            }
          />

          {/* Detail table */}
          <Table<CpmOemItem>
            loading={loading}
            loadingText="Loading CPM breakdown…"
            items={items}
            empty={
              <Box textAlign="center" padding="l" color="text-body-secondary">
                No CPM data available
              </Box>
            }
            columnDefinitions={[
              {
                id: "oem",
                header: "OEM",
                cell: (item) => item.group,
                sortingField: "oem",
              },
              {
                id: "avgCpm",
                header: "Avg CPM",
                cell: (item) => (
                  <SpaceBetween size="xs" direction="horizontal">
                    <span>{fmtCpm(item.cpm)}</span>
                    {/* Row-level badge: each figure is labelled at the row */}
                    <ProvenanceBadge
                      provenance={panelProvenance}
                      variant="row"
                    />
                  </SpaceBetween>
                ),
                sortingField: "cpm",
              },
              {
                id: "totalCost",
                header: "Total Cost",
                cell: (item) => fmtMoney(item.totalCost),
                sortingField: "totalCost",
              },
              {
                id: "totalMiles",
                header: "Total Miles",
                cell: (item) => item.totalMiles.toLocaleString(),
                sortingField: "totalMiles",
              },
              {
                id: "vehicleCount",
                header: "Vehicles",
                cell: (item) => item.vehicleCount,
                sortingField: "vehicleCount",
              },
              {
                id: "status",
                header: "vs. Fleet Avg",
                cell: (item) => {
                  if (items.length < 2) return "—";
                  const fleetAvg =
                    items.reduce((s, i) => s + i.cpm, 0) /
                    items.length;
                  const pct = ((item.cpm - fleetAvg) / fleetAvg) * 100;
                  if (Math.abs(pct) < 1) return <StatusIndicator type="success">At avg</StatusIndicator>;
                  return (
                    <StatusIndicator type={pct > 0 ? "warning" : "success"}>
                      {pct > 0 ? "+" : ""}
                      {pct.toFixed(1)}%
                    </StatusIndicator>
                  );
                },
              },
            ]}
            variant="embedded"
            sortingDisabled={false}
          />
        </SpaceBetween>
      )}
    </Container>
  );
};

export default CpmOemView;
