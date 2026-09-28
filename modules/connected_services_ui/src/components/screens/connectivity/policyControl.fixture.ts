// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * policyControl.fixture.ts — simulated data for the Policy & Control screen (T5.1).
 *
 * Four policy classes:
 *   1. APN Configuration  — carrier/access-point configuration applied per market
 *   2. Traffic Priority   — driving / parked / stolen priority tiers
 *   3. Geo-fencing        — market-boundary list (NOT a map of vehicle positions)
 *   4. QoS                — eCall priority and background-suppression threshold
 *
 * Every leaf value is a ProvenanceValue with provenance: 'simulated'.
 * No location field anywhere (spec T5.1 Constraints).
 * No trip / GPS / driver-identity fields anywhere.
 *
 * Structural keys (`id`) are exempt from provenance wrapping per spec D4.
 */

import type { ProvenanceValue } from "../../../types";

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── APN Configuration ─────────────────────────────────────────────────────────

export interface ApnConfigEntry {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  market: ProvenanceValue<string>;
  apnName: ProvenanceValue<string>;
  authType: ProvenanceValue<string>;
  pdpType: ProvenanceValue<"IPv4" | "IPv6" | "IPv4v6">;
  vehicleCount: ProvenanceValue<number>;
}

export const APN_CONFIG_ENTRIES: ApnConfigEntry[] = [
  {
    id: "apn-us-oemnet",
    market: sim("US"),
    apnName: sim("oemnet.us"),
    authType: sim("PAP"),
    pdpType: sim("IPv4v6"),
    vehicleCount: sim(2906),
  },
  {
    id: "apn-de-oemnet",
    market: sim("Germany"),
    apnName: sim("oemnet.de"),
    authType: sim("CHAP"),
    pdpType: sim("IPv4v6"),
    vehicleCount: sim(1251),
  },
  {
    id: "apn-in-oemnet",
    market: sim("India"),
    apnName: sim("oemnet.in"),
    authType: sim("PAP"),
    pdpType: sim("IPv4"),
    vehicleCount: sim(900),
  },
];

// ── APN form defaults (used for the "Apply to N vehicles" preview) ────────────

export interface ApnFormDefaults {
  market: ProvenanceValue<string>;
  apnName: ProvenanceValue<string>;
  authType: ProvenanceValue<string>;
  pdpType: ProvenanceValue<"IPv4" | "IPv6" | "IPv4v6">;
  /**
   * Preview: number of vehicles the change would affect in the selected market.
   * Drives the "Apply to N vehicles" confirmation copy.
   */
  vehicleCount: ProvenanceValue<number>;
}

export const APN_FORM_DEFAULTS: ApnFormDefaults = {
  market: sim("US"),
  apnName: sim("oemnet.us"),
  authType: sim("PAP"),
  pdpType: sim("IPv4v6"),
  vehicleCount: sim(2906),
};

// ── Traffic Priority ──────────────────────────────────────────────────────────

export type VehicleMode = "driving" | "parked" | "stolen";

export interface TrafficPriorityRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: VehicleMode;
  mode: ProvenanceValue<string>;
  priority: ProvenanceValue<"critical" | "standard" | "background" | "suppressed">;
  description: ProvenanceValue<string>;
}

export const TRAFFIC_PRIORITY_ROWS: TrafficPriorityRow[] = [
  {
    id: "driving",
    mode: sim("Driving"),
    priority: sim("critical"),
    description: sim("All bearers active; eCall path reserved"),
  },
  {
    id: "parked",
    mode: sim("Parked"),
    priority: sim("standard"),
    description: sim("Background OTA allowed; telemetry heartbeat only"),
  },
  {
    id: "stolen",
    mode: sim("Stolen"),
    priority: sim("critical"),
    description: sim("Location reporting elevated; remote-command channel open"),
  },
];

// ── Geo-fencing (market-boundary list) ───────────────────────────────────────
//
// Geo-fencing is a market-boundary list, NOT a map of vehicle positions.
// No location field anywhere (spec T5.1 Constraints).

export interface GeofenceEntry {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  name: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  /** Human-readable boundary descriptor, e.g. "Continental US", "Germany (excl. Bornholm)" */
  boundary: ProvenanceValue<string>;
  serviceRestriction: ProvenanceValue<string>;
  active: ProvenanceValue<boolean>;
}

export const GEOFENCE_ENTRIES: GeofenceEntry[] = [
  {
    id: "gf-us-contiguous",
    name: sim("Continental US"),
    market: sim("US"),
    boundary: sim("Continental United States"),
    serviceRestriction: sim("Full services"),
    active: sim(true),
  },
  {
    id: "gf-de-domestic",
    name: sim("Germany"),
    market: sim("Germany"),
    boundary: sim("Federal Republic of Germany"),
    serviceRestriction: sim("Full services"),
    active: sim(true),
  },
  {
    id: "gf-in-domestic",
    name: sim("India"),
    market: sim("India"),
    boundary: sim("Republic of India"),
    serviceRestriction: sim("Data-capped services"),
    active: sim(true),
  },
  {
    id: "gf-eu-roaming",
    name: sim("EU Roaming Zone"),
    market: sim("Germany"),
    boundary: sim("European Union (all member states)"),
    serviceRestriction: sim("Roaming — reduced bandwidth"),
    active: sim(false),
  },
];

// ── QoS ──────────────────────────────────────────────────────────────────────

export interface QosConfig {
  /** eCall priority is a deterministic seam — value is boolean, not free text. */
  ecallPriorityEnabled: ProvenanceValue<boolean>;
  /** Threshold (kbps) below which background data is suppressed. */
  backgroundSuppressionThresholdKbps: ProvenanceValue<number>;
  /** Maximum concurrent OTA streams per TCU. */
  maxConcurrentOtaStreams: ProvenanceValue<number>;
}

export const QOS_CONFIG: QosConfig = {
  ecallPriorityEnabled: sim(true),
  backgroundSuppressionThresholdKbps: sim(128),
  maxConcurrentOtaStreams: sim(4),
};

// ── Composite export ──────────────────────────────────────────────────────────

export interface PolicyControlFixture {
  apnConfigEntries: ApnConfigEntry[];
  apnFormDefaults: ApnFormDefaults;
  trafficPriorityRows: TrafficPriorityRow[];
  geofenceEntries: GeofenceEntry[];
  qosConfig: QosConfig;
}

export const POLICY_CONTROL_FIXTURE: PolicyControlFixture = {
  apnConfigEntries: APN_CONFIG_ENTRIES,
  apnFormDefaults: APN_FORM_DEFAULTS,
  trafficPriorityRows: TRAFFIC_PRIORITY_ROWS,
  geofenceEntries: GEOFENCE_ENTRIES,
  qosConfig: QOS_CONFIG,
};
