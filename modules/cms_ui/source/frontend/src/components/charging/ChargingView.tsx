// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ChargingView (Energy panel) — reads CMS's own charging_sessions table via
 * GET /api/v1/charging/{summary,sessions}.
 *
 * ⚠️  Data source: CMS's `cms-{stage}-storage-charging-sessions` (DynamoDB).
 *     NOT ADP's same-named curated product — spec § F6 confirms there is no
 *     cross-account read path in this repo. Any future ADP integration is a
 *     separate spec.
 *
 * De-mock summary (T4.3):
 *   - Removed hardcoded `activeSessions` array (was at line ~36)
 *   - Removed hardcoded `batteryHealth` array (was at line ~60)
 *   - Removed hardcoded `chargingStations` array (was at line ~89)
 *   - Removed hardcoded `energyConsumptionData` array (was at line ~115)
 *   - Removed hardcoded `chargingCostData` array (was at line ~126)
 *   - Removed hardcoded "Today's Charging Summary" text panel
 *   - All data now comes from /api/v1/charging/* or shows explicit empty/error states
 *
 * Station data: GET /api/v1/charging/stations is not yet implemented in the
 * main_api handler. Per spec § F6 and T4.3 constraints, the tab renders an
 * explicit "not yet implemented" state rather than a plausible placeholder.
 * Do NOT substitute a fake list — that is the defect class being eliminated.
 *
 * Simulated values carry a <ProvenanceBadge> per spec § D1.
 */

import React, { useEffect, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Container,
  Grid,
  Header,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  Table,
  Tabs,
} from "@cloudscape-design/components";
import LineChart from "@cloudscape-design/components/line-chart";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import { ProvenanceBadge } from "../provenance";
import type { ProvenanceValue } from "../provenance";

// ---------------------------------------------------------------------------
// API response shapes (matching main_api's charging routes)
// ---------------------------------------------------------------------------

interface ChargingSummary {
  sessionsToday: number;
  bevVehicles: number;
  kwhToday: number;
  costMTD: number;
  totalSessionsMTD: number;
  provenance?: ProvenanceValue;
}

interface ChargingSession {
  sessionId: string;
  vehicleId: string;
  stationId?: string;
  startTime: string;
  endTime?: string;
  energyKwh?: number;
  durationMinutes?: number;
  cost?: number;
  currentCharge?: number;
  targetCharge?: number;
  chargingRateKw?: number;
  estimatedCompletionTime?: string;
  provenance?: ProvenanceValue;
}

interface ChargingSessionsResponse {
  sessions: ChargingSession[];
  provenance?: ProvenanceValue;
}

// Energy trend data point  returned by /api/v1/charging/trend (if available)
interface EnergyTrendPoint {
  date: string;
  kwhTotal: number;
}

// ---------------------------------------------------------------------------
// Component
// ---------------------------------------------------------------------------

const ChargingManagementView: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState("active-sessions");

  // Summary tile data
  const [summary, setSummary] = useState<ChargingSummary | null>(null);
  const [summaryLoading, setSummaryLoading] = useState(true);
  const [summaryError, setSummaryError] = useState<string | null>(null);

  // Session list data
  const [sessions, setSessions] = useState<ChargingSession[]>([]);
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);

  // Energy trend data (may not be implemented yet)
  const [trendData, setTrendData] = useState<EnergyTrendPoint[]>([]);
  const [trendLoading, setTrendLoading] = useState(true);

  useEffect(() => {
    const base = getApiEndpoint().replace(/\/$/, "");

    // Summary
    setSummaryLoading(true);
    authFetch(`${base}/api/v1/charging/summary`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: ChargingSummary) => setSummary(d))
      .catch((e: unknown) =>
        setSummaryError(e instanceof Error ? e.message : String(e))
      )
      .finally(() => setSummaryLoading(false));

    // Sessions
    setSessionsLoading(true);
    authFetch(`${base}/api/v1/charging/sessions`)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        return r.json();
      })
      .then((d: ChargingSessionsResponse) =>
        setSessions(d.sessions ?? [])
      )
      .catch((e: unknown) =>
        setSessionsError(e instanceof Error ? e.message : String(e))
      )
      .finally(() => setSessionsLoading(false));

    // Trend — optional endpoint; degrade gracefully if absent
    setTrendLoading(true);
    authFetch(`${base}/api/v1/charging/trend`)
      .then((r) => {
        if (!r.ok) return null;
        return r.json();
      })
      .then((d: { trend?: EnergyTrendPoint[] } | null) => {
        if (d?.trend) setTrendData(d.trend);
      })
      .catch(() => {
        /* trend is optional — no error surfaced to user */
      })
      .finally(() => setTrendLoading(false));
  }, []);

  // Provenance from summary (all simulated today)
  const panelProvenance: ProvenanceValue = summary?.provenance ?? "simulated";

  // ---------------------------------------------------------------------------
  // KPI tile helpers
  // ---------------------------------------------------------------------------
  const KpiTile = ({
    label,
    value,
    sub,
  }: {
    label: string;
    value: React.ReactNode;
    sub?: string;
  }) => (
    <Container header={<Header variant="h2">{label}</Header>}>
      <Box variant="h1" color="text-status-info" data-testid={`kpi-${label.toLowerCase().replace(/\s+/g, '-')}`}>
        {value}
      </Box>
      <Box variant="small" color="text-body-secondary">
        {sub ?? (summaryLoading ? "Loading…" : "No data available")}
      </Box>
    </Container>
  );

  // ---------------------------------------------------------------------------
  // Trend chart data
  // ---------------------------------------------------------------------------
  const trendSeries =
    trendData.length > 0
      ? [
          {
            title: "Daily Energy (kWh)",
            type: "line" as const,
            data: trendData.map((p) => ({
              x: new Date(p.date),
              y: p.kwhTotal,
            })),
          },
        ]
      : [];

  const trendXDomain: [Date, Date] | undefined =
    trendData.length >= 2
      ? [
          new Date(trendData[0].date),
          new Date(trendData[trendData.length - 1].date),
        ]
      : undefined;

  // ---------------------------------------------------------------------------
  // Render
  // ---------------------------------------------------------------------------
  return (
    <SpaceBetween size="l">
      {/* Panel-level provenance badge — entire dataset is simulated today */}
      <Box>
        <ProvenanceBadge provenance={panelProvenance} variant="panel" />
      </Box>

      {/* Summary error */}
      {summaryError && (
        <Alert type="error" header="Failed to load charging summary">
          {summaryError}
        </Alert>
      )}

      {/* KPI Tiles */}
      <Grid
        gridDefinition={[
          { colspan: 3 },
          { colspan: 3 },
          { colspan: 3 },
          { colspan: 3 },
        ]}
      >
        <KpiTile
          label="Sessions Today"
          value={summary?.sessionsToday ?? "—"}
          sub={
            summary
              ? "Charging sessions started today"
              : summaryLoading
              ? "Loading…"
              : "No data available"
          }
        />
        <KpiTile
          label="BEV Vehicles"
          value={summary?.bevVehicles ?? "—"}
          sub={
            summary
              ? "Vehicles with charging history"
              : summaryLoading
              ? "Loading…"
              : "No data available"
          }
        />
        <KpiTile
          label="Energy Delivered Today"
          value={
            summary?.kwhToday != null ? `${summary.kwhToday} kWh` : "—"
          }
          sub={
            summary
              ? "Today's total consumption"
              : summaryLoading
              ? "Loading…"
              : "No data available"
          }
        />
        <KpiTile
          label="Charging Cost MTD"
          value={
            summary?.costMTD != null
              ? `$${summary.costMTD.toFixed(0)}`
              : "—"
          }
          sub={
            summary?.totalSessionsMTD != null
              ? `${summary.totalSessionsMTD} sessions this month`
              : summaryLoading
              ? "Loading…"
              : "No data available"
          }
        />
      </Grid>

      {/* Energy Analytics Charts */}
      <Grid gridDefinition={[{ colspan: 12 }]}>
        <Container
          header={
            <Header variant="h3">
              Energy Consumption Trend (Last 7 Days)
            </Header>
          }
        >
          {trendLoading ? (
            <Box color="text-body-secondary">Loading trend data…</Box>
          ) : trendSeries.length === 0 ? (
            <Box
              textAlign="center"
              color="text-body-secondary"
              padding="l"
              data-testid="trend-empty"
            >
              Energy trend data not yet available
            </Box>
          ) : (
            <LineChart
              series={trendSeries}
              xDomain={trendXDomain}
              i18nStrings={{
                filterLabel: "Filter displayed data",
                filterPlaceholder: "Filter data",
                filterSelectedAriaLabel: "selected",
                legendAriaLabel: "Legend",
                chartAriaRoleDescription: "line chart",
              }}
              ariaLabel="Energy consumption over time"
              height={200}
            />
          )}
        </Container>
      </Grid>

      {/* Charging Management Tabs */}
      <Tabs
        activeTabId={activeTabId}
        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
        tabs={[
          {
            id: "active-sessions",
            label: "Charging Sessions",
            content: (
              <SpaceBetween size="m">
                {sessionsError && (
                  <Alert type="error" header="Failed to load sessions">
                    {sessionsError}
                  </Alert>
                )}
                <Table<ChargingSession>
                  loading={sessionsLoading}
                  loadingText="Loading sessions…"
                  columnDefinitions={[
                    {
                      id: "sessionId",
                      header: "Session ID",
                      cell: (item) => item.sessionId,
                    },
                    {
                      id: "vehicleId",
                      header: "Vehicle",
                      cell: (item) => item.vehicleId,
                    },
                    {
                      id: "startTime",
                      header: "Start Time",
                      cell: (item) =>
                        item.startTime
                          ? new Date(item.startTime).toLocaleString()
                          : "—",
                    },
                    {
                      id: "energyKwh",
                      header: "Energy (kWh)",
                      cell: (item) =>
                        item.energyKwh != null
                          ? item.energyKwh.toFixed(1)
                          : "—",
                    },
                    {
                      id: "progress",
                      header: "Charge Progress",
                      cell: (item) =>
                        item.currentCharge != null ? (
                          <ProgressBar
                            value={item.currentCharge}
                            additionalInfo={
                              item.targetCharge != null
                                ? `${item.currentCharge}% → ${item.targetCharge}%`
                                : `${item.currentCharge}%`
                            }
                            variant={
                              item.currentCharge >=
                              (item.targetCharge ?? 100)
                                ? "success"
                                : undefined
                            }
                          />
                        ) : (
                          "—"
                        ),
                    },
                    {
                      id: "chargingRate",
                      header: "Rate",
                      cell: (item) =>
                        item.chargingRateKw != null ? (
                          <Badge color="blue">{item.chargingRateKw} kW</Badge>
                        ) : (
                          "—"
                        ),
                    },
                    {
                      id: "cost",
                      header: "Cost",
                      cell: (item) => (
                        <SpaceBetween size="xs" direction="horizontal">
                          <span>
                            {item.cost != null
                              ? `$${item.cost.toFixed(2)}`
                              : "—"}
                          </span>
                          <ProvenanceBadge
                            provenance={item.provenance ?? panelProvenance}
                            variant="row"
                          />
                        </SpaceBetween>
                      ),
                    },
                  ]}
                  items={sessions}
                  empty={
                    <Box textAlign="center" color="inherit" padding="l">
                      <b>No charging sessions</b>
                      <Box variant="p" color="inherit">
                        No sessions found in the current period.
                      </Box>
                    </Box>
                  }
                />
              </SpaceBetween>
            ),
          },
          {
            id: "charging-stations",
            label: "Charging Infrastructure",
            content: (
              <Box
                textAlign="center"
                color="text-body-secondary"
                padding="l"
                data-testid="stations-not-implemented"
              >
                {/*
                 * Station data: GET /api/v1/charging/stations is not yet
                 * implemented in main_api. Rendering an explicit placeholder
                 * rather than fabricated data — per spec § F6 + T4.3 Constraints:
                 * "If a real endpoint does not exist for stations, render an
                 *  explicit 'not yet implemented' state; do not substitute a
                 *  plausible-looking placeholder."
                 */}
                <StatusIndicator type="pending">
                  Charging infrastructure data — not yet implemented
                </StatusIndicator>
                <Box variant="p">
                  Station-level data will appear here once
                  GET /api/v1/charging/stations is available.
                </Box>
              </Box>
            ),
          },
        ]}
      />
    </SpaceBetween>
  );
};

export default ChargingManagementView;
