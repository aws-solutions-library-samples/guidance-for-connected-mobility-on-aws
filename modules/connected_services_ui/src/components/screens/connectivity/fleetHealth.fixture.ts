// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * fleetHealth.fixture.ts — simulated data for the Fleet Health screen (T4.2).
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * No location field beyond market attribution (spec T4.2 Constraints).
 * No trip / GPS / driver-identity fields anywhere.
 *
 * ## Shape notes
 *
 * `FleetHealthRow` — the per-VIN table row. The status union matches the
 * ConnectivityState values used by the ?state= filter:
 *   connected | degraded | ntn_fallback | unreachable
 *
 * `MarketSummary` — one summary card per market (US / Germany / India).
 *
 * `FleetHealthFixture` — the exported object consumed by the screen.
 */

import type { ProvenanceValue } from "../../../types";

// ── Domain types ──────────────────────────────────────────────────────────────

export type ConnectivityState =
  | "connected"
  | "degraded"
  | "ntn_fallback"
  | "unreachable";

export type TcuTier = "TCU-1" | "TCU-2" | "TCU-3";

export interface FleetHealthRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  vin: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  connectivityState: ProvenanceValue<ConnectivityState>;
  bearer: ProvenanceValue<string>;
  signal: ProvenanceValue<string>;
  lastSession: ProvenanceValue<string>;
  tcuTier: ProvenanceValue<TcuTier>;
}

export interface MarketSummary {
  /** Structural id — exempt. */
  id: string;
  market: ProvenanceValue<string>;
  connected: ProvenanceValue<number>;
  degraded: ProvenanceValue<number>;
  ntpFallback: ProvenanceValue<number>;
  unreachable: ProvenanceValue<number>;
}

export interface FleetHealthFixture {
  rows: FleetHealthRow[];
  marketSummaries: MarketSummary[];
}

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Market summaries ──────────────────────────────────────────────────────────

export const MARKET_SUMMARIES: MarketSummary[] = [
  {
    id: "us",
    market: sim("US"),
    connected: sim(2841),
    degraded: sim(47),
    ntpFallback: sim(12),
    unreachable: sim(6),
  },
  {
    id: "de",
    market: sim("Germany"),
    connected: sim(1209),
    degraded: sim(31),
    ntpFallback: sim(8),
    unreachable: sim(3),
  },
  {
    id: "in",
    market: sim("India"),
    connected: sim(874),
    degraded: sim(19),
    ntpFallback: sim(5),
    unreachable: sim(2),
  },
];

// ── Fleet rows ────────────────────────────────────────────────────────────────

export const FLEET_ROWS: FleetHealthRow[] = [
  {
    id: "row-001",
    vin: sim("1HGCM82633A004352"),
    market: sim("US"),
    connectivityState: sim("connected"),
    bearer: sim("LTE"),
    signal: sim("-73 dBm"),
    lastSession: sim("2026-09-03T14:22:01Z"),
    tcuTier: sim("TCU-2"),
  },
  {
    id: "row-002",
    vin: sim("WBA3A5C51CF256985"),
    market: sim("Germany"),
    connectivityState: sim("degraded"),
    bearer: sim("LTE"),
    signal: sim("-98 dBm"),
    lastSession: sim("2026-09-03T11:05:44Z"),
    tcuTier: sim("TCU-2"),
  },
  {
    id: "row-003",
    vin: sim("MAKA0000000000003"),
    market: sim("India"),
    connectivityState: sim("connected"),
    bearer: sim("4G"),
    signal: sim("-81 dBm"),
    lastSession: sim("2026-09-03T13:51:12Z"),
    tcuTier: sim("TCU-1"),
  },
  {
    id: "row-004",
    vin: sim("1HGCM82633A004353"),
    market: sim("US"),
    connectivityState: sim("degraded"),
    bearer: sim("LTE"),
    signal: sim("-104 dBm"),
    lastSession: sim("2026-09-03T09:30:00Z"),
    tcuTier: sim("TCU-2"),
  },
  {
    id: "row-005",
    vin: sim("WBA3A5C51CF256986"),
    market: sim("Germany"),
    connectivityState: sim("ntn_fallback"),
    bearer: sim("NTN-satellite"),
    signal: sim("-115 dBm"),
    lastSession: sim("2026-09-03T08:14:55Z"),
    tcuTier: sim("TCU-3"),
  },
  {
    id: "row-006",
    vin: sim("1HGCM82633A004354"),
    market: sim("US"),
    connectivityState: sim("unreachable"),
    bearer: sim("LTE"),
    signal: sim("—"),
    lastSession: sim("2026-09-02T23:59:01Z"),
    tcuTier: sim("TCU-2"),
  },
  {
    id: "row-007",
    vin: sim("MAKA0000000000007"),
    market: sim("India"),
    connectivityState: sim("connected"),
    bearer: sim("4G"),
    signal: sim("-77 dBm"),
    lastSession: sim("2026-09-03T14:00:00Z"),
    tcuTier: sim("TCU-1"),
  },
  {
    id: "row-008",
    vin: sim("WBA3A5C51CF256987"),
    market: sim("Germany"),
    connectivityState: sim("degraded"),
    bearer: sim("LTE"),
    signal: sim("-101 dBm"),
    lastSession: sim("2026-09-03T07:45:20Z"),
    tcuTier: sim("TCU-2"),
  },
  {
    id: "row-009",
    vin: sim("1HGCM82633A004355"),
    market: sim("US"),
    connectivityState: sim("connected"),
    bearer: sim("LTE"),
    signal: sim("-69 dBm"),
    lastSession: sim("2026-09-03T14:18:30Z"),
    tcuTier: sim("TCU-3"),
  },
  {
    id: "row-010",
    vin: sim("MAKA0000000000010"),
    market: sim("India"),
    connectivityState: sim("ntn_fallback"),
    bearer: sim("NTN-satellite"),
    signal: sim("-119 dBm"),
    lastSession: sim("2026-09-03T06:22:11Z"),
    tcuTier: sim("TCU-3"),
  },
  {
    id: "row-011",
    vin: sim("1HGCM82633A004356"),
    market: sim("US"),
    connectivityState: sim("degraded"),
    bearer: sim("LTE"),
    signal: sim("-99 dBm"),
    lastSession: sim("2026-09-03T10:10:05Z"),
    tcuTier: sim("TCU-1"),
  },
  {
    id: "row-012",
    vin: sim("WBA3A5C51CF256988"),
    market: sim("Germany"),
    connectivityState: sim("unreachable"),
    bearer: sim("LTE"),
    signal: sim("—"),
    lastSession: sim("2026-09-02T22:00:00Z"),
    tcuTier: sim("TCU-2"),
  },
];

export const FLEET_HEALTH_FIXTURE: FleetHealthFixture = {
  rows: FLEET_ROWS,
  marketSummaries: MARKET_SUMMARIES,
};
