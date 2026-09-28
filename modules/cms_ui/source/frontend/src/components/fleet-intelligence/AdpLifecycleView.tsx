// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * AdpLifecycleView — the "All ADP vehicles" scope of the lifecycle screen.
 *
 * Spec: .kiro/specs/2026-09-25-cms-fi-adp-wide-lifecycle/spec.md § D1, D3, D5, D7
 *
 * Renders the precomputed rollup served by
 * `GET /api/v1/fleet-intelligence/lifecycle?scope=adp` (platform-admin only).
 * The route never computes live: a missing or stale artifact is a 503, which
 * this view shows as "Rollup not computed yet" with the last computedAt, not
 * as an error banner (D3, D7).
 *
 * Layout (D7): "As of", bucket counts, monthly maintenance trend, cohort table
 * (model x model year), top-N soonest crossovers, and a drill-down section with
 * the cached series of the selected top-N row.
 *
 * The purchase-price assumption (D5) comes from the response's `assumptions`
 * object, not a constant, and is stated beside each crossover figure: the
 * summary, the top-N table and the drill-down.
 *
 * Callers must only mount this for platform admins; `LifecycleView` gates it on
 * `useUserRole().isAdmin`. The backend enforces the same rule (403).
 */

import React, { useEffect, useRef, useState } from "react";
import {
  Alert,
  Box,
  Button,
  Container,
  ExpandableSection,
  Header,
  KeyValuePairs,
  SpaceBetween,
  StatusIndicator,
  Table,
} from "@cloudscape-design/components";
import LineChart from "@cloudscape-design/components/line-chart";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import { ProvenanceBadge } from "../provenance";
import type { ProvenanceValue } from "../provenance";

// ---------------------------------------------------------------------------
// Types — spec § Design "Response (scope=adp, 200)"
// ---------------------------------------------------------------------------

export interface AdpRollupSummary {
  totalVehicles: number;
  sellRecommendedCount: number;
  sellSoonCount: number;
  healthyCount: number;
  insufficientDataCount: number;
  avgMonthsToCrossover: number | null;
}

export interface AdpTrendPoint {
  yearMonth: string;
  avgMaintenance: number;
  vehicleCount: number;
}

export interface AdpCohortRow {
  model: string;
  modelYear: number | null;
  vehicles: number;
  sellRecommendedCount: number;
  sellSoonCount: number;
  healthyCount: number;
  insufficientDataCount: number;
  avgMonthlyMaintenance: number;
  avgCostPerMile: number | null;
}

export interface AdpSeriesPoint {
  yearMonth: string;
  maintenanceCost: number;
}

export interface AdpTopCrossover {
  vin: string;
  model: string;
  modelYear: number | null;
  monthsUntilCrossover: number | null;
  currentMonthlyMaintenance: number;
  rSquared: number;
  series: AdpSeriesPoint[];
}

export interface AdpRollupResponse {
  scope: "adp";
  computedAt: string;
  windowMonths: number;
  horizonMonths: number;
  assumptions: { purchasePriceUsd: number; straightLineLifeMonths: number };
  summary: AdpRollupSummary;
  monthlyTrend: AdpTrendPoint[];
  cohorts: AdpCohortRow[];
  topCrossovers: AdpTopCrossover[];
  provenance: ProvenanceValue;
}

type LoadState =
  | { kind: "loading" }
  | { kind: "ok"; data: AdpRollupResponse }
  | { kind: "not-computed"; lastComputedAt: string | null }
  | { kind: "error"; message: string };

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

const fmtMoney = (n: number) =>
  `$${n.toLocaleString("en-US", { minimumFractionDigits: 0, maximumFractionDigits: 0 })}`;

const fmtInt = (n: number) => n.toLocaleString("en-US");

/** ISO timestamp → "YYYY-MM-DD HH:MM UTC"; an unparseable value is shown as-is. */
export const fmtComputedAt = (iso: string): string => {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return `${d.toISOString().slice(0, 16).replace("T", " ")} UTC`;
};

/** The D5 assumption sentence, built from what the server says it used. */
export const assumptionText = (a: AdpRollupResponse["assumptions"]): string => {
  const monthly = a.straightLineLifeMonths > 0 ? a.purchasePriceUsd / a.straightLineLifeMonths : 0;
  return (
    `Crossover assumes a ${fmtMoney(a.purchasePriceUsd)} purchase price, ` +
    `depreciated straight-line over ${a.straightLineLifeMonths} months ` +
    `(${fmtMoney(monthly)}/mo). ADP has no vehicle price.`
  );
};

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

const ADP_LIFECYCLE_PATH = "/api/v1/fleet-intelligence/lifecycle?scope=adp";

/** The `error` value the FI Lambda writes on its 503 (index.py `_handle_adp_rollup`).
 *  A 503 without it (API Gateway, CloudFront) is an outage, not "not computed". */
export const ROLLUP_NOT_COMPUTED_ERROR = "rollup not yet computed";

/** Minimal shape check so a malformed artifact shows the error state instead of
 *  throwing during render. */
export const isAdpRollupResponse = (x: unknown): x is AdpRollupResponse => {
  if (!x || typeof x !== "object") return false;
  const r = x as Record<string, unknown>;
  const a = r.assumptions as Record<string, unknown> | undefined;
  const s = r.summary as Record<string, unknown> | undefined;
  return (
    r.scope === "adp" &&
    typeof r.computedAt === "string" &&
    !!a &&
    typeof a.purchasePriceUsd === "number" &&
    typeof a.straightLineLifeMonths === "number" &&
    !!s &&
    typeof s.totalVehicles === "number" &&
    Array.isArray(r.monthlyTrend) &&
    Array.isArray(r.cohorts) &&
    Array.isArray(r.topCrossovers)
  );
};

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

export const AdpLifecycleView: React.FC = () => {
  const [state, setState] = useState<LoadState>({ kind: "loading" });
  const [selectedVin, setSelectedVin] = useState<string | null>(null);
  const drilldownRef = useRef<HTMLDivElement | null>(null);

  // The drill-down sits below a top-N table of up to 100 rows, so bring it
  // into view when a row is chosen (review T3.1 cycle 1, W1).
  useEffect(() => {
    if (selectedVin === null) return;
    drilldownRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
  }, [selectedVin]);

  useEffect(() => {
    let cancelled = false;
    const base = getApiEndpoint().replace(/\/$/, "");
    // scope=adp only; fleetId is never sent — ADP has no fleets (spec D1).
    authFetch(`${base}${ADP_LIFECYCLE_PATH}`)
      .then(async (r) => {
        if (r.status === 503) {
          let body: { error?: unknown; computedAt?: unknown } | null = null;
          try {
            body = await r.json();
          } catch {
            body = null;
          }
          if (body && body.error === ROLLUP_NOT_COMPUTED_ERROR) {
            const lastComputedAt = typeof body.computedAt === "string" ? body.computedAt : null;
            return { kind: "not-computed", lastComputedAt } as LoadState;
          }
          return { kind: "error", message: "Service unavailable (HTTP 503)" } as LoadState;
        }
        if (r.status === 403) {
          return {
            kind: "error",
            message: "The All ADP vehicles view needs the platform-admin role.",
          } as LoadState;
        }
        if (!r.ok) return { kind: "error", message: `HTTP ${r.status}` } as LoadState;
        const data: unknown = await r.json();
        if (!isAdpRollupResponse(data)) {
          return { kind: "error", message: "The rollup response has an unexpected shape." } as LoadState;
        }
        return { kind: "ok", data } as LoadState;
      })
      .catch((e: unknown) => ({
        kind: "error",
        message: e instanceof Error ? e.message : String(e),
      }) as LoadState)
      .then((next) => {
        if (!cancelled) setState(next);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  if (state.kind === "loading") {
    return (
      <Container header={<Header variant="h2">All ADP vehicles</Header>}>
        <StatusIndicator type="loading">Loading rollup…</StatusIndicator>
      </Container>
    );
  }

  if (state.kind === "not-computed") {
    return (
      <Container header={<Header variant="h2">All ADP vehicles</Header>}>
        <div data-testid="adp-rollup-not-computed">
          <SpaceBetween size="xs">
            <StatusIndicator type="pending">Rollup not computed yet</StatusIndicator>
            <Box variant="small" color="text-body-secondary">
              {state.lastComputedAt
                ? `Last computed ${fmtComputedAt(state.lastComputedAt)}. The rollup refreshes hourly; results older than 26 hours are not shown.`
                : "No previous run is recorded. The rollup refreshes hourly."}
            </Box>
          </SpaceBetween>
        </div>
      </Container>
    );
  }

  if (state.kind === "error") {
    return (
      <Container>
        <Alert type="error" header="Failed to load the All ADP vehicles rollup">
          {state.message}
        </Alert>
      </Container>
    );
  }

  const { data } = state;
  const { summary } = data;
  const assumption = assumptionText(data.assumptions);
  const asOf = `As of ${fmtComputedAt(data.computedAt)}`;
  const selected = data.topCrossovers.find((t) => t.vin === selectedVin) ?? null;
  const depreciation =
    data.assumptions.straightLineLifeMonths > 0
      ? data.assumptions.purchasePriceUsd / data.assumptions.straightLineLifeMonths
      : 0;

  // The assumed depreciation is drawn as a threshold series: LineChart has no
  // `referenceLines` prop, so a threshold is the supported way to show it.
  const trendSeries =
    data.monthlyTrend.length > 0
      ? [
          {
            title: "Avg maintenance ($/mo)",
            type: "line" as const,
            data: data.monthlyTrend.map((pt) => ({
              x: new Date(pt.yearMonth + "-01"),
              y: pt.avgMaintenance,
            })),
          },
          ...(depreciation > 0
            ? [
                {
                  title: `Assumed monthly depreciation (${fmtMoney(depreciation)}/mo)`,
                  type: "threshold" as const,
                  y: depreciation,
                },
              ]
            : []),
        ]
      : [];
  // Read back from the series the chart receives, so the marker can't drift
  // from what is drawn.
  const thresholdSeries = trendSeries.find((s) => s.type === "threshold") as
    | { y: number }
    | undefined;
  const thresholdY = thresholdSeries?.y ?? 0;

  return (
    <SpaceBetween size="l">
      {/* Summary: as-of, assumption, bucket counts */}
      <Container
        header={
          <Header
            variant="h2"
            description={
              <>
                <span data-testid="adp-as-of">{asOf}</span>{" "}
                <ProvenanceBadge provenance={data.provenance} variant="panel" />
                <br />
                <span data-testid="adp-assumption-summary">{assumption}</span>
              </>
            }
          >
            All ADP vehicles
          </Header>
        }
      >
        <div data-testid="adp-kpi-cards">
          <KeyValuePairs
            columns={3}
            items={[
              {
                label: "Vehicles",
                value: <span data-testid="adp-kpi-total">{fmtInt(summary.totalVehicles)}</span>,
              },
              {
                label: "Sell recommended (≤ 6 mo)",
                value: (
                  <span data-testid="adp-kpi-sell-recommended">
                    {fmtInt(summary.sellRecommendedCount)}
                  </span>
                ),
              },
              {
                label: "Sell soon (7–12 mo)",
                value: (
                  <span data-testid="adp-kpi-sell-soon">{fmtInt(summary.sellSoonCount)}</span>
                ),
              },
              {
                label: "Good standing",
                value: <span data-testid="adp-kpi-healthy">{fmtInt(summary.healthyCount)}</span>,
              },
              {
                label: "Not enough history",
                value: (
                  <span data-testid="adp-kpi-insufficient">
                    {fmtInt(summary.insufficientDataCount)}
                  </span>
                ),
              },
              {
                label: "Avg months to crossover",
                value: (
                  <span data-testid="adp-kpi-avg-months">
                    {summary.avgMonthsToCrossover === null
                      ? "—"
                      : summary.avgMonthsToCrossover.toFixed(1)}
                  </span>
                ),
              },
            ]}
          />
        </div>
      </Container>

      {/* Monthly maintenance trend */}
      <Container
        header={
          <Header
            variant="h3"
            description={`Average monthly maintenance across vehicles with service that month, trailing ${data.windowMonths} months`}
          >
            Maintenance trend
          </Header>
        }
      >
        <div
          data-testid="adp-trend"
          data-point-count={String(data.monthlyTrend.length)}
          data-threshold={String(thresholdY)}
        >
          <LineChart
            height={200}
            statusType="finished"
            series={trendSeries}
            xScaleType="time"
            xTitle="Month"
            yTitle="Avg maintenance ($/mo)"
            ariaLabel="All ADP vehicles maintenance trend line chart"
            hideFilter
            i18nStrings={chartI18n}
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
        </div>
      </Container>

      {/* Cohorts: model x model year */}
      <Table<AdpCohortRow>
        data-testid="adp-cohort-table"
        header={
          <Header variant="h3" counter={`(${data.cohorts.length})`}>
            Cohorts by model and model year
          </Header>
        }
        items={data.cohorts}
        trackBy={(c) => `${c.model}|${c.modelYear ?? ""}`}
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            No cohorts
          </Box>
        }
        columnDefinitions={[
          { id: "model", header: "Model", cell: (c) => c.model },
          { id: "modelYear", header: "Model year", cell: (c) => (c.modelYear ?? "—") },
          { id: "vehicles", header: "Vehicles", cell: (c) => fmtInt(c.vehicles) },
          { id: "sr", header: "Sell recommended", cell: (c) => fmtInt(c.sellRecommendedCount) },
          { id: "ss", header: "Sell soon", cell: (c) => fmtInt(c.sellSoonCount) },
          { id: "healthy", header: "Good standing", cell: (c) => fmtInt(c.healthyCount) },
          { id: "insufficient", header: "Not enough history", cell: (c) => fmtInt(c.insufficientDataCount) },
          {
            id: "maint",
            header: "Avg maintenance $/mo",
            cell: (c) => fmtMoney(c.avgMonthlyMaintenance),
          },
          {
            id: "cpm",
            header: "Avg cost per mile",
            cell: (c) => (c.avgCostPerMile === null ? "—" : `$${c.avgCostPerMile.toFixed(3)}`),
          },
        ]}
      />

      {/* Top-N soonest crossovers */}
      <Table<AdpTopCrossover>
        data-testid="adp-top-crossovers"
        header={
          <Header
            variant="h3"
            counter={`(${data.topCrossovers.length})`}
            description={<span data-testid="adp-assumption-top-n">{assumption}</span>}
          >
            Soonest crossovers
          </Header>
        }
        items={data.topCrossovers}
        trackBy="vin"
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            No vehicle crosses within {data.horizonMonths} months
          </Box>
        }
        columnDefinitions={[
          { id: "vin", header: "VIN", cell: (t) => t.vin },
          { id: "model", header: "Model", cell: (t) => t.model },
          { id: "modelYear", header: "Year", cell: (t) => (t.modelYear ?? "—") },
          {
            id: "months",
            header: "Months to crossover",
            cell: (t) => (t.monthsUntilCrossover === null ? "—" : String(t.monthsUntilCrossover)),
          },
          {
            id: "maint",
            header: "Current maintenance $/mo",
            cell: (t) => fmtMoney(t.currentMonthlyMaintenance),
          },
          { id: "r2", header: "R²", cell: (t) => t.rSquared.toFixed(2) },
          {
            id: "series",
            header: "Series",
            cell: (t) => (
              <Button
                variant="inline-link"
                ariaLabel={`Show maintenance series for ${t.vin}`}
                ariaExpanded={selectedVin === t.vin}
                onClick={() => setSelectedVin((prev) => (prev === t.vin ? null : t.vin))}
              >
                {selectedVin === t.vin ? "Hide series" : "View series"}
              </Button>
            ),
          },
        ]}
      />

      {/* Drill-down: the cached series for the selected top-N row */}
      {/* scrollMarginTop keeps the section header clear of the fixed top nav. */}
      <div ref={drilldownRef} data-testid="adp-drilldown" style={{ scrollMarginTop: 56 }}>
        <ExpandableSection
          variant="container"
          headerText={selected ? `Maintenance series: ${selected.vin}` : "Maintenance series"}
          headerDescription={
            selected ? undefined : "Choose View series on a row above to see its monthly maintenance."
          }
          expanded={selected !== null}
          onChange={({ detail }) => {
            if (!detail.expanded) setSelectedVin(null);
          }}
        >
          {selected && (
            <SpaceBetween size="s">
              <Box variant="p" data-testid="adp-drilldown-summary">
                {selected.model} {selected.modelYear ?? ""} · crossover in{" "}
                {selected.monthsUntilCrossover ?? "—"} months · R² {selected.rSquared.toFixed(2)}
              </Box>
              <Box variant="small" color="text-body-secondary" data-testid="adp-assumption-drilldown">
                {assumption}
              </Box>
              <Table<AdpSeriesPoint>
                data-testid="adp-drilldown-series"
                variant="embedded"
                items={selected.series}
                trackBy="yearMonth"
                empty={
                  <Box textAlign="center" color="text-body-secondary">
                    No cached series
                  </Box>
                }
                columnDefinitions={[
                  { id: "ym", header: "Month", cell: (p) => p.yearMonth },
                  { id: "mc", header: "Maintenance $", cell: (p) => fmtMoney(p.maintenanceCost) },
                ]}
              />
            </SpaceBetween>
          )}
        </ExpandableSection>
      </div>
    </SpaceBetween>
  );
};

export default AdpLifecycleView;
