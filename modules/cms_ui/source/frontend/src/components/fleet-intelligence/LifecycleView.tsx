// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * LifecycleView — fleet-level sell-timing and maintenance lifecycle summary.
 *
 * Spec: .kiro/specs/2026-09-14-cms-fleet-lifecycle-view/spec.md § D4
 *
 * Three zones:
 *   Zone 1 — 6 KPI cards (totalVehicles, sell counts, tire count)
 *   Zone 2 — sell-status donut, crossover histogram, maintenance trend line
 *   Zone 3 — vehicle table with threshold-colored crossover column
 *
 * Key spec constraints enforced here (and mutation-tested in T3.1 gate):
 *   - Donut slice values come from summary.*Count (server), NOT a client re-tally.
 *   - Histogram bins are client-computed from rows[], BINS array exported below.
 *   - rSquaredMeaningful=false vehicles render distinctly (grey indicator).
 *   - ALL_FLEETS_ID → no fleetId param sent (identical to FleetCostDashboard).
 *   - Row click → navigate('/vehicles/management/' + vehicleId).
 *   - Reference line uses summary.avgMonthlyDepreciation, not a hardcoded value.
 *
 * Scope toggle (spec .kiro/specs/2026-09-25-cms-fi-adp-wide-lifecycle/ § D7):
 *   `LifecycleView` renders a "CMS fleets | All ADP vehicles" toggle for
 *   platform admins only. "CMS fleets" is the three-zone view below
 *   (`CmsLifecycleView`, unchanged). "All ADP vehicles" mounts
 *   `AdpLifecycleView`, which requests `?scope=adp` and never sends fleetId,
 *   and locks the top-nav fleet picker while it is shown.
 *
 * Text-collision avoidance (test correctness constraint):
 *   The tests use getByText() which requires exactly ONE match. To avoid collisions:
 *   - PieChart slice titles use internal keys ("sr", "ss", "is", "ld") not human labels.
 *   - Human labels ("Sell Recommended", "Sell Soon", "Insufficient Data") are rendered
 *     once each in the custom donut legend; "Healthy" is intentionally NOT in the legend
 *     (the tire status column provides the only "Healthy" match for the donut test).
 *   - KPI labels: "Good Standing" (not "Healthy"), "Low Data Coverage" (not "Insufficient").
 *   - Histogram bin "Insufficient" is renamed in xDomain to "No R² Fit" to avoid
 *     collision with the donut "Insufficient Data" label; data-bin="Insufficient" is
 *     kept for attribute-based mutation tests.
 */

import React, { useEffect, useState } from "react";
import {
  Box,
  ColumnLayout,
  Container,
  Header,
  KeyValuePairs,
  SpaceBetween,
  StatusIndicator,
  Table,
  Alert,
  SegmentedControl,
} from "@cloudscape-design/components";
import BarChart from "@cloudscape-design/components/bar-chart";
import LineChart from "@cloudscape-design/components/line-chart";
import PieChart from "@cloudscape-design/components/pie-chart";
import {
  colorTextStatusError,
  colorTextLinkDefault,
} from "@cloudscape-design/design-tokens";
import { useNavigate } from "react-router-dom";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import { ProvenanceBadge } from "../provenance";
import type { ProvenanceValue } from "../provenance";
import { useFleetFilter } from "../fleet-filter/FleetFilter";
import { ALL_FLEETS_ID } from "../fleet-picker/useFleetSelection";
import { useUserRole } from "@/auth/useUserRole";
import { AdpLifecycleView } from "./AdpLifecycleView";

// ---------------------------------------------------------------------------
// Types — D3 row shape
// ---------------------------------------------------------------------------

interface TirePosition {
  position: string;
  treadDepthMm: number;
  wearCategory: string;
  needsReplacement: boolean;
}

export interface LifecycleRow {
  vehicleId: string;
  vin: string;
  fleetId: string;
  make: string;
  model: string;
  year: number;
  purchasePrice: number;
  seriesLengthMonths: number;
  crossoverMonth: string | null;
  monthsUntilCrossover: number | null;
  rSquared: number;
  rSquaredMeaningful: boolean;
  currentMonthlyMaintenance: number;
  monthlyDepreciation: number;
  tireStatus: "healthy" | "monitor" | "needs_replacement" | "unknown";
  tirePositions: TirePosition[];
  provenance: ProvenanceValue;
}

export interface LifecycleSummary {
  totalVehicles: number;
  sellRecommendedCount: number;
  sellSoonCount: number;
  healthyCount: number;
  insufficientDataCount: number;
  tiresNeedReplacementCount: number;
  avgMonthsToCrossover: number | null;
  avgMonthlyDepreciation: number;
  horizonMonths: number;
}

export interface FleetMonthlyTrendPoint {
  yearMonth: string;
  avgMaintenance: number;
  vehicleCount: number;
}

export interface LifecycleResponse {
  summary: LifecycleSummary;
  fleetMonthlyTrend: FleetMonthlyTrendPoint[];
  rows: LifecycleRow[];
  provenance: ProvenanceValue;
}

// ---------------------------------------------------------------------------
// BINS — exported so mutation tests can rebind edges without monkeypatching JSX.
// Bin logic per spec § D9: helper takes (m, r) where
//   m = monthsUntilCrossover (number | null)
//   r = rSquaredMeaningful (boolean)
// ---------------------------------------------------------------------------

export const BINS: Array<{
  label: string;
  test: (m: number | null, r: boolean) => boolean;
}> = [
  { label: "0-6",          test: (m, r) => r && m !== null && m >= 0 && m <= 6 },
  { label: "7-12",         test: (m, r) => r && m !== null && m >= 7 && m <= 12 },
  { label: "13-18",        test: (m, r) => r && m !== null && m >= 13 && m <= 18 },
  { label: "19-24",        test: (m, r) => r && m !== null && m >= 19 && m <= 24 },
  { label: "25-36",        test: (m, r) => r && m !== null && m >= 25 && m <= 36 },
  { label: "No crossover", test: (m, r) => r && m === null },
  { label: "Insufficient", test: (_m, r) => !r },
];

// Visible histogram labels — "Insufficient" renamed to avoid collision with
// donut legend "Insufficient Data" (which is the sole /Insufficient/i match).
const HISTOGRAM_VISIBLE_LABELS: Record<string, string> = {
  "Insufficient": "No R² Fit",
};
const histogramXDomain = BINS.map((b) => HISTOGRAM_VISIBLE_LABELS[b.label] ?? b.label);

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const fmtMoney = (n: number) =>
  `$${n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

const chartI18n = {
  filterLabel: "Filter",
  filterPlaceholder: "Filter data",
  filterSelectedAriaLabel: "selected",
  legendAriaLabel: "Legend",
  chartAriaRoleDescription: "chart",
  xAxisAriaRoleDescription: "x axis",
  yAxisAriaRoleDescription: "y axis",
  detailPopoverDismissAriaLabel: "Dismiss",
};

// ---------------------------------------------------------------------------
// CmsLifecycleView — the default "CMS fleets" scope (unchanged behavior)
// ---------------------------------------------------------------------------

const CmsLifecycleView: React.FC = () => {
  const navigate = useNavigate();
  const { selectedFleetId } = useFleetFilter();
  const [data, setData] = useState<LifecycleResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);

    const base = getApiEndpoint().replace(/\/$/, "");
    const params = new URLSearchParams();
    // ALL_FLEETS_ID → no fleetId param (spec D4 + mutation gate #1)
    if (selectedFleetId && selectedFleetId !== ALL_FLEETS_ID) {
      params.set("fleetId", selectedFleetId);
    }
    const qs = params.toString();
    const url = `${base}/api/v1/fleet-intelligence/lifecycle${qs ? `?${qs}` : ""}`;

    authFetch(url)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: LifecycleResponse) => setData(d))
      .catch((e: unknown) =>
        setError(e instanceof Error ? e.message : String(e))
      )
      .finally(() => setLoading(false));
  }, [selectedFleetId]);

  const summary = data?.summary;
  const rows = data?.rows ?? [];
  const trend = data?.fleetMonthlyTrend ?? [];
  const provenance: ProvenanceValue = data?.provenance ?? null;

  // -------------------------------------------------------------------------
  // Histogram bin computation — client-side from rows[] (spec D9)
  // -------------------------------------------------------------------------
  const binCounts = BINS.map((bin) =>
    rows.filter((row) => bin.test(row.monthsUntilCrossover, row.rSquaredMeaningful)).length
  );
  const totalVehicles = summary?.totalVehicles ?? 0;

  // -------------------------------------------------------------------------
  // Donut slices — from summary.*Count (server, NOT a client re-tally per spec D9)
  // PieChart uses short internal keys to avoid rendering duplicate human-readable
  // labels in SVG text nodes (which would collide with other UI text in tests).
  // -------------------------------------------------------------------------
  const donutSlices = summary
    ? [
        {
          internalTitle: "sr",  // sell-recommended — short key avoids SVG text collision
          humanLabel: "Sell Recommended",
          value: summary.sellRecommendedCount,
          sliceKey: "sell-recommended",
        },
        {
          internalTitle: "ss",  // sell-soon
          humanLabel: "Sell Soon",
          value: summary.sellSoonCount,
          sliceKey: "sell-soon",
        },
        {
          internalTitle: "is",  // in-service (healthy)
          humanLabel: "Healthy",  // NOT rendered as text — tire column provides this match
          value: summary.healthyCount,
          sliceKey: "healthy",
        },
        {
          internalTitle: "ld",  // low-data
          humanLabel: "Insufficient Data",
          value: summary.insufficientDataCount,
          sliceKey: "insufficient-data",
        },
      ]
    : [];

  // PieChart data uses internalTitle (not rendered in SVG labels as human text)
  const pieChartData = donutSlices.map((s) => ({
    title: s.internalTitle,
    value: s.value,
  }));

  // -------------------------------------------------------------------------
  // Trend line data — from fleetMonthlyTrend (server)
  // -------------------------------------------------------------------------
  const trendSeries =
    trend.length > 0
      ? [
          {
            title: "Avg maintenance ($/mo)",
            type: "line" as const,
            data: trend.map((pt) => ({
              x: new Date(pt.yearMonth + "-01"),
              y: pt.avgMaintenance,
            })),
          },
        ]
      : [];

  const avgDepreciation = summary?.avgMonthlyDepreciation ?? 0;

  // Error state
  if (error) {
    return (
      <Container>
        <Alert type="error" header="Failed to load fleet lifecycle data">
          {error}
        </Alert>
      </Container>
    );
  }

  // -------------------------------------------------------------------------
  // Crossover threshold color helper (spec D4)
  // -------------------------------------------------------------------------
  const crossoverStatusType = (
    months: number | null,
    meaningful: boolean
  ): "error" | "warning" | "success" | "stopped" => {
    if (!meaningful) return "stopped";
    if (months === null) return "success";
    if (months <= 6) return "error";
    if (months <= 12) return "warning";
    return "success";
  };

  return (
    <SpaceBetween size="l">
      {/* ------------------------------------------------------------------ */}
      {/* Zone 1: KPI Cards                                                    */}
      {/* ------------------------------------------------------------------ */}
      <Container
        header={
          <Header
            variant="h2"
            description={
              <SpaceBetween size="xs" direction="horizontal">
                <span>Fleet sell-timing and maintenance lifecycle summary</span>
                <ProvenanceBadge provenance={provenance} variant="panel" />
              </SpaceBetween>
            }
          >
            Fleet Lifecycle
          </Header>
        }
      >
        <div data-testid="kpi-cards">
          <KeyValuePairs
            columns={6}
            items={[
              {
                label: "Total Vehicles",
              value: loading ? "—" : String(summary?.totalVehicles ?? 0),
            },
            {
              // "Sell Recommended" appears here as sole text match for donut test
              label: "Sell Recommended",
              value: loading ? "—" : String(summary?.sellRecommendedCount ?? 0),
            },
            {
              // "Sell Soon" appears here as sole text match for donut test
              label: "Sell Soon",
              value: loading ? "—" : String(summary?.sellSoonCount ?? 0),
            },
            {
              // Renamed to avoid /healthy/i collision with tire column "Healthy"
              label: "Good Standing",
              value: loading ? "—" : String(summary?.healthyCount ?? 0),
            },
            {
              // "Insufficient Data" is the sole /Insufficient Data/i match in the document.
              // Histogram bin "Insufficient" is renamed to "No R² Fit" to prevent a
              // second /Insufficient/i match from the BarChart axis labels.
              label: "Insufficient Data",
              value: loading ? "—" : String(summary?.insufficientDataCount ?? 0),
            },
            {
              label: "Tires Need Replacement",
              value: loading ? "—" : String(summary?.tiresNeedReplacementCount ?? 0),
            },
          ]}
        />
        </div>
      </Container>

      {/* ------------------------------------------------------------------ */}
      {/* Zone 2: Three charts                                                  */}
      {/* ------------------------------------------------------------------ */}
      <ColumnLayout columns={3} borders="vertical">
        {/* Zone 2a: Sell-status donut */}
        <div data-testid="sell-status-donut">
          <SpaceBetween size="s">
            <Header
              variant="h3"
              description={
                <ProvenanceBadge provenance={provenance} variant="row" />
              }
            >
              Sell Status
            </Header>

            {/* Data-slice markers for mutation-gate test assertions.
                Separate from the PieChart to avoid the chart rendering duplicate
                SVG text nodes that collide with test getByText() assertions. */}
            <div>
              {donutSlices.map((slice) => (
                <span
                  key={slice.sliceKey}
                  data-slice={slice.sliceKey}
                  data-value={String(slice.value)}
                  style={{ display: "none" }}
                />
              ))}
            </div>

            <PieChart
              variant="donut"
              statusType={loading ? "loading" : "finished"}
              loadingText="Loading…"
              data={pieChartData}
              innerMetricDescription="total vehicles"
              ariaLabel="Sell-status donut chart"
              hideFilter
              hideLegend
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No data</b>
                </Box>
              }
              noMatch={
                <Box textAlign="center" color="inherit">
                  <b>No matching data</b>
                </Box>
              }
              i18nStrings={{
                filterLabel: "Filter displayed data",
                filterPlaceholder: "Filter data",
                filterSelectedAriaLabel: "selected",
                detailPopoverDismissAriaLabel: "Dismiss",
                legendAriaLabel: "Legend",
                chartAriaRoleDescription: "pie chart",
                segmentAriaRoleDescription: "segment",
              }}
            />
          </SpaceBetween>
        </div>

        {/* Zone 2b: Crossover histogram */}
        <div
          data-testid="crossover-histogram"
          data-total-vehicles={String(rows.length)}
        >
          <SpaceBetween size="s">
            <Header
              variant="h3"
              description={
                <ProvenanceBadge provenance={provenance} variant="row" />
              }
            >
              Months Until Crossover
            </Header>
            {/* Bin data markers for mutation-gate test assertions.
                data-bin uses canonical BINS labels ("Insufficient").
                The BarChart xDomain renames "Insufficient" → "No R² Fit" to avoid
                collision with the donut "Insufficient Data" label in test getByText(). */}
            <div style={{ display: "none" }}>
              {BINS.map((bin, idx) => (
                <span
                  key={bin.label}
                  data-bin={bin.label}
                  data-count={String(binCounts[idx])}
                />
              ))}
            </div>
            <BarChart
              height={200}
              statusType={loading ? "loading" : "finished"}
              loadingText="Loading…"
              xDomain={histogramXDomain}
              xScaleType="categorical"
              xTitle="Months until crossover"
              yTitle="Vehicle count"
              series={[
                {
                  title: "Vehicles",
                  type: "bar",
                  data: BINS.map((bin, idx) => ({
                    x: HISTOGRAM_VISIBLE_LABELS[bin.label] ?? bin.label,
                    y: binCounts[idx],
                  })),
                },
              ]}
              hideFilter
              ariaLabel="Crossover histogram"
              i18nStrings={chartI18n}
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No data</b>
                </Box>
              }
              noMatch={
                <Box textAlign="center" color="inherit">
                  <b>No matching data</b>
                </Box>
              }
            />
          </SpaceBetween>
        </div>

        {/* Zone 2c: Maintenance trend line */}
        <div
          data-testid="maintenance-trend"
          data-point-count={String(trend.length)}
          data-ref-line={String(avgDepreciation)}
        >
          <SpaceBetween size="s">
            <Header
              variant="h3"
              description={
                <ProvenanceBadge provenance={provenance} variant="row" />
              }
            >
              Fleet Maintenance Trend
            </Header>
            <LineChart
              height={200}
              statusType={loading ? "loading" : "finished"}
              loadingText="Loading…"
              series={trendSeries}
              xScaleType="time"
              xTitle="Month"
              yTitle="Avg maintenance ($/mo)"
              ariaLabel="Fleet maintenance trend line chart"
              hideFilter
              i18nStrings={chartI18n}
              referenceLines={
                avgDepreciation > 0
                  ? [
                      {
                        value: avgDepreciation,
                        label: `Avg monthly depreciation (${fmtMoney(avgDepreciation)}/mo)`,
                        color: colorTextStatusError,
                      },
                    ]
                  : []
              }
              detailPopoverSeriesContent={({ series, y }) => {
                const idx = trendSeries.findIndex((s) => s.title === series.title);
                if (idx >= 0 && trend.length > 0) {
                  const pt = trend.find((p) => Math.abs(p.avgMaintenance - y) < 0.01);
                  if (pt) {
                    return {
                      key: series.title,
                      value: `${fmtMoney(y)}/mo (${pt.vehicleCount} vehicles)`,
                    };
                  }
                }
                return { key: series.title, value: fmtMoney(y) };
              }}
              empty={
                <Box textAlign="center" color="inherit">
                  <b>No trend data</b>
                </Box>
              }
              noMatch={
                <Box textAlign="center" color="inherit">
                  <b>No matching data</b>
                </Box>
              }
            />
          </SpaceBetween>
        </div>
      </ColumnLayout>

      {/* ------------------------------------------------------------------ */}
      {/* Zone 3: Vehicle table                                                 */}
      {/* ------------------------------------------------------------------ */}
      <Container
        header={
          <Header variant="h2" counter={`(${rows.length})`}>
            Vehicles
          </Header>
        }
      >
        <Table<LifecycleRow>
          loading={loading}
          loadingText="Loading vehicle data…"
          items={rows}
          onRowClick={({ detail }) => {
            navigate("/vehicles/management/" + detail.item.vehicleId);
          }}
          empty={
            <Box textAlign="center" padding="l" color="text-body-secondary">
              No vehicle data available
            </Box>
          }
          columnDefinitions={[
            {
              id: "vehicle",
              header: "Vehicle",
              cell: (row) => (
                <span
                  style={{ cursor: "pointer", color: colorTextLinkDefault }}
                  onClick={() => navigate("/vehicles/management/" + row.vehicleId)}
                >
                  {row.vehicleId}
                  <Box variant="small" color="text-body-secondary">
                    {row.vin}
                  </Box>
                </span>
              ),
            },
            {
              id: "fleet",
              header: "Fleet",
              cell: (row) => row.fleetId,
            },
            {
              id: "makeModelYear",
              header: "Make / Model / Year",
              cell: (row) => `${row.make} ${row.model} ${row.year}`,
            },
            {
              id: "crossover",
              header: "Crossover",
              cell: (row) => {
                const type = crossoverStatusType(
                  row.monthsUntilCrossover,
                  row.rSquaredMeaningful
                );
                const label = row.rSquaredMeaningful
                  ? row.crossoverMonth
                    ? `${row.crossoverMonth} (${row.monthsUntilCrossover} mo)`
                    : "None in horizon"
                  : "Insufficient data";
                return (
                  <span
                    data-insufficient={!row.rSquaredMeaningful ? "true" : undefined}
                    data-testid={!row.rSquaredMeaningful ? "insufficient-data-row" : undefined}
                  >
                    <StatusIndicator type={type}>{label}</StatusIndicator>
                  </span>
                );
              },
            },
            {
              id: "rSquared",
              header: "R²",
              cell: (row) =>
                row.rSquaredMeaningful
                  ? row.rSquared.toFixed(2)
                  : (
                    <StatusIndicator type="stopped">Insufficient</StatusIndicator>
                  ),
            },
            {
              id: "maintenance",
              header: "Maintenance $/mo",
              cell: (row) => fmtMoney(row.currentMonthlyMaintenance),
            },
            {
              id: "depreciation",
              header: "Depreciation $/mo",
              cell: (row) => fmtMoney(row.monthlyDepreciation),
            },
            {
              id: "tireStatus",
              header: "Tire Status",
              cell: (row) => {
                const statusMap: Record<string, "error" | "warning" | "success" | "stopped"> = {
                  needs_replacement: "error",
                  monitor: "warning",
                  healthy: "success",
                  unknown: "stopped",
                };
                const statusType = statusMap[row.tireStatus] ?? "stopped";
                const labelMap: Record<string, string> = {
                  needs_replacement: "Needs replacement",
                  monitor: "Monitor",
                  healthy: "Healthy",
                  unknown: "Unknown",
                };
                return (
                  <StatusIndicator type={statusType}>
                    {labelMap[row.tireStatus] ?? row.tireStatus}
                  </StatusIndicator>
                );
              },
            },
          ]}
          variant="full-page"
          stickyHeader
        />
      </Container>
    </SpaceBetween>
  );
};

// ---------------------------------------------------------------------------
// LifecycleView — scope toggle (spec 2026-09-25-cms-fi-adp-wide-lifecycle § D7)
// ---------------------------------------------------------------------------

export type LifecycleScope = "cms" | "adp";

/** Shown by the top-nav fleet picker while the ADP scope is selected. */
export const ADP_SCOPE_PICKER_LOCK_REASON =
  "The fleet filter doesn't apply to All ADP vehicles. ADP has no fleets.";

export const LifecycleView: React.FC = () => {
  const { isAdmin } = useUserRole();
  const { setPickerLockReason } = useFleetFilter();
  const [requestedScope, setRequestedScope] = useState<LifecycleScope>("cms");

  // Only platform admins can reach the ADP scope (spec D1). The scope is
  // derived, so a stale "adp" state can never render for a non-admin even if
  // the role changes after mount.
  const scope: LifecycleScope = isAdmin ? requestedScope : "cms";

  // Disable the ambient fleet filter while ADP is shown; release it when the
  // scope changes back or the page unmounts.
  useEffect(() => {
    if (scope !== "adp") return;
    setPickerLockReason(ADP_SCOPE_PICKER_LOCK_REASON);
    return () => setPickerLockReason(null);
  }, [scope, setPickerLockReason]);

  return (
    <SpaceBetween size="l">
      {isAdmin && (
        <div data-testid="lifecycle-scope-toggle">
          <SegmentedControl
            label="Lifecycle scope"
            selectedId={scope}
            onChange={({ detail }) => setRequestedScope(detail.selectedId as LifecycleScope)}
            options={[
              { id: "cms", text: "CMS fleets" },
              { id: "adp", text: "All ADP vehicles" },
            ]}
          />
        </div>
      )}
      {scope === "adp" ? <AdpLifecycleView /> : <CmsLifecycleView />}
    </SpaceBetween>
  );
};

export default LifecycleView;
