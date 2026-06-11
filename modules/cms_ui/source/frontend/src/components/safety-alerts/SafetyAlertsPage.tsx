// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Safety screen at /alerts/safety. Was a stub (4 cards rendering '—',
// 3 empty tables) — now reads real data from
//   * GET /api/v1/dashboard/metrics  (30-day severity counts)
//   * GET /api/v1/safety-events?limit=500  (recent events for the table
//     and for client-side driver / vehicle aggregation)
//   * GET /api/v1/vehicles?limit=500  (vehicleId -> VIN map for the
//     events table cell renderer)
//
// 14k+ events live in cms-prod-storage-safety-events with rich detail
// (severity, eventType, speed, location, weather/road context, driver,
// vehicle). The 30-day cached counts come back through the dashboard
// metrics endpoint; the 500-event sample lets us derive 'top risky
// driver / vehicle' leaderboards without a dedicated aggregation API.

import React, { useEffect, useMemo, useState } from "react";
import {
  Alert,
  Badge,
  Box,
  Container,
  Header,
  Link,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  Table,
  Tabs,
} from "@cloudscape-design/components";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";
import { SafetyEventsTable } from "../commons/SafetyEventsTable";

interface SafetyEvent {
  eventId: string;
  vehicleId: string;
  vin?: string;
  driverId?: string;
  driverName?: string;
  eventType: string;
  severity: string;
  timestamp: number;
  description?: string;
  speed?: number;
  resolved?: boolean;
  fleetId?: string;
  tripType?: string;
}

interface SafetyMetrics {
  total: number;
  critical: number;
  high: number;
  medium: number;
  low: number;
}

interface DriverStat {
  driverId: string;
  driverName: string;
  total: number;
  high: number;
  medium: number;
  low: number;
  topType: string;
  vehicleCount: number;
}

interface VehicleStat {
  vehicleId: string;
  vin: string;
  total: number;
  high: number;
  medium: number;
  low: number;
  topType: string;
  driverCount: number;
}

// Map raw eventType enum (SPEEDING / HARD_BRAKING / etc.) to a humane
// label for the leaderboards' 'most common type' column.
const humanizeEventType = (raw: string): string =>
  String(raw || "")
    .replace(/[._]/g, " ")
    .toLowerCase()
    .replace(/\b\w/g, (c) => c.toUpperCase());

const sevType = (s: string): "error" | "warning" | "info" | "stopped" => {
  const u = String(s || "").toUpperCase();
  if (u === "HIGH" || u === "CRITICAL" || u === "3") return "error";
  if (u === "MEDIUM" || u === "2") return "warning";
  if (u === "LOW" || u === "1") return "info";
  return "stopped";
};

const SafetyAlertsPage: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState("events");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [metrics, setMetrics] = useState<SafetyMetrics>({ total: 0, critical: 0, high: 0, medium: 0, low: 0 });
  const [eventSample, setEventSample] = useState<SafetyEvent[]>([]);
  const [vehicleVinMap, setVehicleVinMap] = useState<Record<string, string>>({});

  useEffect(() => {
    void fetchAll();
  }, []);

  const fetchAll = async () => {
    setLoading(true);
    setError(null);
    try {
      const apiEndpoint = getApiEndpoint().replace(/\/$/, "");
      const [metricsResp, eventsResp, vehiclesResp] = await Promise.allSettled([
        authFetch(`${apiEndpoint}/api/v1/dashboard/metrics`),
        // 500-event sample: enough to make the driver/vehicle
        // leaderboards meaningful without paginating through the
        // full 14k. The events table itself still paginates.
        authFetch(`${apiEndpoint}/api/v1/safety-events?limit=500`),
        authFetch(`${apiEndpoint}/api/v1/vehicles?limit=500`),
      ]);

      // ── Metrics ─────────────────────────────────────────────────
      if (metricsResp.status === "fulfilled" && metricsResp.value.ok) {
        const data = await metricsResp.value.json();
        const sa = data.safetyAlerts || {};
        setMetrics({
          total: Number(sa.total) || 0,
          critical: Number(sa.critical) || 0,
          high: Number(sa.high) || 0,
          medium: Number(sa.medium) || 0,
          low: Number(sa.low) || 0,
        });
      }

      // ── Event sample (for leaderboards) ─────────────────────────
      if (eventsResp.status === "fulfilled" && eventsResp.value.ok) {
        const data = await eventsResp.value.json();
        const list: SafetyEvent[] = (data.events || []).map((e: any) => ({
          eventId: e.eventId,
          vehicleId: e.vehicleId,
          vin: e.vin,
          driverId: e.driverId,
          driverName: e.driverName,
          eventType: e.eventType || "UNKNOWN",
          severity: String(e.severity || "").toUpperCase(),
          timestamp: Number(e.timestamp) || 0,
          description: e.description,
          speed: Number(e.speed),
          resolved: !!e.resolved,
          fleetId: e.fleetId,
          tripType: e.tripType,
        }));
        setEventSample(list);
      } else {
        const msg = eventsResp.status === "fulfilled" ? `${eventsResp.value.status}` : (eventsResp.reason as Error)?.message;
        console.warn("Safety events fetch failed:", msg);
      }

      // ── Vehicle VIN map ────────────────────────────────────────
      if (vehiclesResp.status === "fulfilled" && vehiclesResp.value.ok) {
        const data = await vehiclesResp.value.json();
        const next: Record<string, string> = {};
        for (const v of data.vehicles || []) {
          if (v.vehicleId && v.vin) next[v.vehicleId] = v.vin;
        }
        setVehicleVinMap(next);
      }
    } catch (e: any) {
      console.error("Failed to load safety data:", e);
      setError(e?.message || "Failed to load safety data");
    } finally {
      setLoading(false);
    }
  };

  // ── Derived: leaderboards from the 500-event sample ──────────────
  // Both leaderboards aggregate by their natural key, count by severity,
  // and pick the most common eventType per row. Computed lazily; cheap
  // enough to recompute on every render of the active tab.
  const driverStats = useMemo<DriverStat[]>(() => {
    const byDriver: Record<string, DriverStat & { _types: Record<string, number>; _vehicles: Set<string> }> = {};
    for (const e of eventSample) {
      const key = e.driverId || e.driverName || "unknown";
      if (!byDriver[key]) {
        byDriver[key] = {
          driverId: e.driverId || "—",
          driverName: e.driverName || e.driverId || "Unknown",
          total: 0, high: 0, medium: 0, low: 0,
          topType: "",
          vehicleCount: 0,
          _types: {},
          _vehicles: new Set(),
        };
      }
      const r = byDriver[key];
      r.total++;
      if (e.severity === "HIGH" || e.severity === "CRITICAL") r.high++;
      else if (e.severity === "MEDIUM") r.medium++;
      else if (e.severity === "LOW") r.low++;
      r._types[e.eventType] = (r._types[e.eventType] || 0) + 1;
      if (e.vehicleId) r._vehicles.add(e.vehicleId);
    }
    return Object.values(byDriver)
      .map((r) => {
        const top = Object.entries(r._types).sort((a, b) => b[1] - a[1])[0];
        return {
          driverId: r.driverId,
          driverName: r.driverName,
          total: r.total,
          high: r.high,
          medium: r.medium,
          low: r.low,
          topType: top ? humanizeEventType(top[0]) : "—",
          vehicleCount: r._vehicles.size,
        };
      })
      .sort((a, b) => b.high - a.high || b.total - a.total)
      .slice(0, 25);
  }, [eventSample]);

  const vehicleStats = useMemo<VehicleStat[]>(() => {
    const byVehicle: Record<string, VehicleStat & { _types: Record<string, number>; _drivers: Set<string> }> = {};
    for (const e of eventSample) {
      const key = e.vehicleId || "unknown";
      if (!byVehicle[key]) {
        byVehicle[key] = {
          vehicleId: e.vehicleId || "—",
          vin: e.vin || vehicleVinMap[e.vehicleId || ""] || "",
          total: 0, high: 0, medium: 0, low: 0,
          topType: "",
          driverCount: 0,
          _types: {},
          _drivers: new Set(),
        };
      }
      const r = byVehicle[key];
      r.total++;
      if (e.severity === "HIGH" || e.severity === "CRITICAL") r.high++;
      else if (e.severity === "MEDIUM") r.medium++;
      else if (e.severity === "LOW") r.low++;
      r._types[e.eventType] = (r._types[e.eventType] || 0) + 1;
      if (e.driverId) r._drivers.add(e.driverId);
    }
    return Object.values(byVehicle)
      .map((r) => {
        const top = Object.entries(r._types).sort((a, b) => b[1] - a[1])[0];
        return {
          vehicleId: r.vehicleId,
          vin: r.vin,
          total: r.total,
          high: r.high,
          medium: r.medium,
          low: r.low,
          topType: top ? humanizeEventType(top[0]) : "—",
          driverCount: r._drivers.size,
        };
      })
      .sort((a, b) => b.high - a.high || b.total - a.total)
      .slice(0, 25);
  }, [eventSample, vehicleVinMap]);

  // 'Top risky driver' / 'most common event type' headlines for KPIs
  const topDriver = driverStats[0];
  const topEventTypeEntry = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const e of eventSample) counts[e.eventType] = (counts[e.eventType] || 0) + 1;
    const sorted = Object.entries(counts).sort((a, b) => b[1] - a[1]);
    return sorted[0];
  }, [eventSample]);

  const sevTotalForBar = metrics.high + metrics.medium + metrics.low + metrics.critical;

  return (
    <SpaceBetween size="l">
      {/* KPI cards */}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(4, 1fr)", gap: "16px" }}>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", color: "#656871", letterSpacing: 0.5 }}>
              Safety events (30d)
            </span>
            <span style={{ fontSize: 32, fontWeight: 700, lineHeight: 1.2 }}>
              {loading ? "…" : metrics.total.toLocaleString()}
            </span>
            <Box color="text-body-secondary" fontSize="body-s">
              All severities, fleet-wide
            </Box>
          </SpaceBetween>
        </Container>

        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", color: "#656871", letterSpacing: 0.5 }}>
              High / Critical
            </span>
            <span style={{ fontSize: 32, fontWeight: 700, lineHeight: 1.2, color: metrics.high + metrics.critical > 0 ? "#d91515" : undefined }}>
              {loading ? "…" : (metrics.high + metrics.critical).toLocaleString()}
            </span>
            {sevTotalForBar > 0 && (
              <ProgressBar
                value={Math.round(((metrics.high + metrics.critical) / sevTotalForBar) * 100)}
                additionalInfo={`${metrics.medium} med, ${metrics.low} low`}
                variant="key-value"
              />
            )}
          </SpaceBetween>
        </Container>

        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", color: "#656871", letterSpacing: 0.5 }}>
              Most common type
            </span>
            <span style={{ fontSize: 24, fontWeight: 700, lineHeight: 1.2 }}>
              {loading
                ? "…"
                : topEventTypeEntry
                ? humanizeEventType(topEventTypeEntry[0])
                : "—"}
            </span>
            <Box color="text-body-secondary" fontSize="body-s">
              {topEventTypeEntry ? `${topEventTypeEntry[1].toLocaleString()} of last ${eventSample.length} events` : "No data"}
            </Box>
          </SpaceBetween>
        </Container>

        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: 12, fontWeight: 700, textTransform: "uppercase", color: "#656871", letterSpacing: 0.5 }}>
              Top risky driver
            </span>
            <span style={{ fontSize: 20, fontWeight: 700, lineHeight: 1.2 }}>
              {loading ? "…" : topDriver ? topDriver.driverName : "—"}
            </span>
            {topDriver && (
              <Box color="text-body-secondary" fontSize="body-s">
                {topDriver.total} events · {topDriver.high} high · {topDriver.topType}
              </Box>
            )}
          </SpaceBetween>
        </Container>
      </div>

      {error && (
        <Alert type="error" header="Could not load safety data">
          {error}
        </Alert>
      )}

      {/* Tabs */}
      <Container>
        <Tabs
          activeTabId={activeTabId}
          onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
          tabs={[
            {
              id: "events",
              label: `Safety events (${metrics.total.toLocaleString()})`,
              content: (
                // SafetyEventsTable already exists for the per-vehicle
                // and per-driver views; passing no vehicleId/driverId
                // gives the fleet-wide list. Vehicle + Driver columns
                // turned on, Trip column off (operators can drill into
                // a vehicle for trip context).
                <SafetyEventsTable
                  showVehicleColumn={true}
                  showDriverColumn={true}
                  showTripColumn={false}
                  pageSize={20}
                  vehicleVinMap={vehicleVinMap}
                  totalEventsCount={metrics.total || undefined}
                />
              ),
            },
            {
              id: "drivers",
              label: `Risky drivers (${driverStats.length})`,
              content: (
                <Table
                  loading={loading}
                  loadingText="Loading driver leaderboard…"
                  variant="embedded"
                  stickyHeader
                  items={driverStats}
                  columnDefinitions={[
                    {
                      id: "rank",
                      header: "#",
                      cell: (_item, idx) => String((idx ?? 0) + 1),
                      width: 50,
                    },
                    {
                      id: "driver",
                      header: "Driver",
                      cell: (item) => (
                        <Link href={`/drivers/${item.driverId}`}>
                          {item.driverName}
                        </Link>
                      ),
                      width: 200,
                    },
                    {
                      id: "vehicles",
                      header: "Vehicles",
                      cell: (item) => item.vehicleCount,
                      width: 90,
                    },
                    {
                      id: "total",
                      header: "Events",
                      cell: (item) => item.total,
                      width: 90,
                    },
                    {
                      id: "high",
                      header: "High",
                      cell: (item) =>
                        item.high > 0 ? (
                          <StatusIndicator type="error">{item.high}</StatusIndicator>
                        ) : (
                          <Box color="text-body-secondary">0</Box>
                        ),
                      width: 90,
                    },
                    {
                      id: "medium",
                      header: "Medium",
                      cell: (item) =>
                        item.medium > 0 ? (
                          <StatusIndicator type="warning">{item.medium}</StatusIndicator>
                        ) : (
                          <Box color="text-body-secondary">0</Box>
                        ),
                      width: 100,
                    },
                    {
                      id: "low",
                      header: "Low",
                      cell: (item) =>
                        item.low > 0 ? (
                          <StatusIndicator type="info">{item.low}</StatusIndicator>
                        ) : (
                          <Box color="text-body-secondary">0</Box>
                        ),
                      width: 90,
                    },
                    {
                      id: "topType",
                      header: "Most common",
                      cell: (item) => <Badge color="grey">{item.topType}</Badge>,
                    },
                  ]}
                  empty={
                    <Box textAlign="center" color="inherit" padding="l">
                      <b>No driver data</b>
                      <Box variant="p" color="inherit">
                        Leaderboard populates from the most recent {eventSample.length} safety events.
                      </Box>
                    </Box>
                  }
                />
              ),
            },
            {
              id: "vehicles",
              label: `Risky vehicles (${vehicleStats.length})`,
              content: (
                <Table
                  loading={loading}
                  loadingText="Loading vehicle leaderboard…"
                  variant="embedded"
                  stickyHeader
                  items={vehicleStats}
                  columnDefinitions={[
                    {
                      id: "rank",
                      header: "#",
                      cell: (_item, idx) => String((idx ?? 0) + 1),
                      width: 50,
                    },
                    {
                      id: "vehicle",
                      header: "Vehicle",
                      cell: (item) => (
                        <Link href={`/vehicles/${item.vehicleId}`}>
                          <span style={{ fontFamily: "ui-monospace, SFMono-Regular, Menlo, Monaco, monospace", fontSize: "12.5px" }}>
                            {item.vin || item.vehicleId}
                          </span>
                          {item.vin && (
                            <Box display="inline" color="text-body-secondary" margin={{ left: "xs" }}>
                              ({item.vehicleId})
                            </Box>
                          )}
                        </Link>
                      ),
                      width: 280,
                    },
                    {
                      id: "drivers",
                      header: "Drivers",
                      cell: (item) => item.driverCount,
                      width: 90,
                    },
                    {
                      id: "total",
                      header: "Events",
                      cell: (item) => item.total,
                      width: 90,
                    },
                    {
                      id: "high",
                      header: "High",
                      cell: (item) =>
                        item.high > 0 ? (
                          <StatusIndicator type="error">{item.high}</StatusIndicator>
                        ) : (
                          <Box color="text-body-secondary">0</Box>
                        ),
                      width: 90,
                    },
                    {
                      id: "medium",
                      header: "Medium",
                      cell: (item) =>
                        item.medium > 0 ? (
                          <StatusIndicator type="warning">{item.medium}</StatusIndicator>
                        ) : (
                          <Box color="text-body-secondary">0</Box>
                        ),
                      width: 100,
                    },
                    {
                      id: "low",
                      header: "Low",
                      cell: (item) =>
                        item.low > 0 ? (
                          <StatusIndicator type="info">{item.low}</StatusIndicator>
                        ) : (
                          <Box color="text-body-secondary">0</Box>
                        ),
                      width: 90,
                    },
                    {
                      id: "topType",
                      header: "Most common",
                      cell: (item) => <Badge color="grey">{item.topType}</Badge>,
                    },
                  ]}
                  empty={
                    <Box textAlign="center" color="inherit" padding="l">
                      <b>No vehicle data</b>
                      <Box variant="p" color="inherit">
                        Leaderboard populates from the most recent {eventSample.length} safety events.
                      </Box>
                    </Box>
                  }
                />
              ),
            },
          ]}
        />
      </Container>
    </SpaceBetween>
  );
};

export default SafetyAlertsPage;
