// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Screen registry — single source of truth for navigation, routes, and guards.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md § D6
 *
 * All 26 routes are declared here. Nav, the route table (App.tsx), and the
 * dead-link guard all derive from this registry — no second list.
 *
 * settleMarker: a string that appears in THIS screen's rendered body and nowhere
 * else in the app. The registry-completeness guard P1 relies on it being unique
 * (P4 asserts uniqueness). A settle marker that also appears in shell chrome or
 * another screen vacuates the guard.
 *
 * availability: 'placeholder' renders in nav AND as a route (not DMS's 'deferred',
 * which skips nav and routes). Security Monitor is placeholder; all others active.
 *
 * dataSource: all 23 are 'fixture' this pass. The field exists so a guard can assert
 * no screen claims 'live' while its fixture module exists.
 */

export type ScreenSection =
  | "command-center"
  | "connectivity"
  | "software"
  | "population-diagnostics"
  | "manufacturing-lifecycle"
  | "data-model"
  | "sales-subscriptions"
  | "markets-compliance";

export type ScreenAvailability = "active" | "placeholder";
// NOTE: 'deferred' is intentionally absent — spec D6 explains why this portal's
// semantics differ from DMS. placeholder renders in nav AND as a route.

export type ScreenDataSource = "fixture" | "live";

export interface ScreenEntry {
  id: string;
  label: string;
  path: string;
  section: ScreenSection;
  /** Unique string that appears in this screen's rendered body only. */
  settleMarker: string;
  availability: ScreenAvailability;
  dataSource: ScreenDataSource;
}

export interface NavSection {
  section: ScreenSection;
  label: string;
  entries: ScreenEntry[];
}

// ---------------------------------------------------------------------------
// Registry — 23 entries
// ---------------------------------------------------------------------------

export const SCREEN_REGISTRY: ScreenEntry[] = [
  // 1 — Command Center (root)
  {
    id: "command-center",
    label: "Command Center",
    path: "/",
    section: "command-center",
    settleMarker: "cs-settle-command-center-overview-grid",
    availability: "active",
    dataSource: "fixture",
  },

  // 2 — Fleet Health
  {
    id: "fleet-health",
    label: "Fleet Health",
    path: "/connectivity/fleet-health",
    section: "connectivity",
    settleMarker: "cs-settle-fleet-health-market-summary",
    availability: "active",
    dataSource: "fixture",
  },

  // 3 — Subscriber Lookup (+ /:vin detail)
  {
    id: "subscriber-lookup",
    label: "Subscriber Lookup",
    path: "/connectivity/subscriber-lookup",
    section: "connectivity",
    settleMarker: "cs-settle-subscriber-lookup-search-field",
    availability: "active",
    dataSource: "fixture",
  },

  // 4 — Policy & Control
  {
    id: "policy-control",
    label: "Policy & Control",
    path: "/connectivity/policy",
    section: "connectivity",
    settleMarker: "cs-settle-policy-control-apn-configuration",
    availability: "active",
    dataSource: "fixture",
  },

  // 4b — Available Vehicles (spec 2026-09-10-cms-connected-services-subscriptions T3.6)
  {
    id: "available-vehicles",
    label: "Available Vehicles",
    path: "/connectivity/available-vehicles",
    section: "connectivity",
    settleMarker: "cs-settle-available-vehicles-list",
    availability: "active",
    dataSource: "live",
  },

  // 4c — Simulate Vehicle (Data Model section — moved 2026-09-14 from connectivity)
  //   Simulations exercise vehicle models against decoder manifests to produce
  //   telemetry — belongs alongside the models/manifests they exercise.
  {
    id: "simulate-vehicle",
    label: "Simulate Vehicle",
    path: "/data-model/simulate-vehicle",
    section: "data-model",
    settleMarker: "cs-settle-simulate-vehicle-control-panel",
    availability: "active",
    dataSource: "live",
  },

  // 5 — Rate Plans
  {
    id: "rate-plans",
    label: "Rate Plans",
    path: "/connectivity/rate-plans",
    section: "connectivity",
    settleMarker: "cs-settle-rate-plans-catalogue-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 6 — Signal Detection
  {
    id: "signal-detection",
    label: "Signal Detection",
    path: "/software/signals",
    section: "software",
    settleMarker: "cs-settle-signal-detection-card-feed",
    availability: "active",
    dataSource: "fixture",
  },

  // 7 — Diagnosis Workbench (+ /:signalId)
  {
    id: "diagnosis-workbench",
    label: "Diagnosis Workbench",
    path: "/software/workbench",
    section: "software",
    settleMarker: "cs-settle-diagnosis-workbench-stepper",
    availability: "active",
    dataSource: "fixture",
  },

  // 8 — Campaigns (software campaigns)
  {
    id: "software-campaigns",
    label: "Campaigns",
    path: "/software/campaigns",
    section: "software",
    settleMarker: "cs-settle-software-campaigns-rollout-list",
    availability: "active",
    dataSource: "fixture",
  },

  // 9 — Rollout Monitor
  {
    id: "rollout-monitor",
    label: "Rollout Monitor",
    path: "/software/rollout",
    section: "software",
    settleMarker: "cs-settle-rollout-monitor-progress-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 10 — Security Monitor (R155) — placeholder
  {
    id: "security-monitor",
    label: "Security Monitor (R155)",
    path: "/software/security",
    section: "software",
    settleMarker: "cs-settle-security-monitor-placeholder-panel",
    availability: "placeholder",
    dataSource: "fixture",
  },

  // 11 — Fault Patterns
  {
    id: "fault-patterns",
    label: "Fault Patterns",
    path: "/diagnostics/fault-patterns",
    section: "population-diagnostics",
    settleMarker: "cs-settle-fault-patterns-signature-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 12 — Quality Signals
  {
    id: "quality-signals",
    label: "Quality Signals",
    path: "/diagnostics/quality-signals",
    section: "population-diagnostics",
    settleMarker: "cs-settle-quality-signals-trend-panel",
    availability: "active",
    dataSource: "fixture",
  },

  // 13 — Build & Order
  {
    id: "build-order",
    label: "Build & Order",
    path: "/manufacturing/build-order",
    section: "manufacturing-lifecycle",
    settleMarker: "cs-settle-build-order-configuration-summary",
    availability: "active",
    dataSource: "fixture",
  },

  // 14 — Factory Registration
  {
    id: "factory-registration",
    label: "Factory Registration",
    path: "/manufacturing/factory-registration",
    section: "manufacturing-lifecycle",
    settleMarker: "cs-settle-factory-registration-vin-batch-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 15 — Homologation
  {
    id: "homologation",
    label: "Homologation",
    path: "/manufacturing/homologation",
    section: "manufacturing-lifecycle",
    settleMarker: "cs-settle-homologation-rxswin-registry",
    availability: "active",
    dataSource: "fixture",
  },

  // 16 — Ownership Transfer
  {
    id: "ownership-transfer",
    label: "Ownership Transfer",
    path: "/manufacturing/ownership-transfer",
    section: "manufacturing-lifecycle",
    settleMarker: "cs-settle-ownership-transfer-request-queue",
    availability: "active",
    dataSource: "fixture",
  },

  // 17 — Stop-Ship / Stop-Sale
  {
    id: "stop-ship",
    label: "Stop-Ship / Stop-Sale",
    path: "/manufacturing/stop-ship",
    section: "manufacturing-lifecycle",
    settleMarker: "cs-settle-stop-ship-hold-authorization-panel",
    availability: "active",
    dataSource: "fixture",
  },

  // 18 — Feature Catalog
  {
    id: "feature-catalog",
    label: "Feature Catalog",
    path: "/sales/feature-catalog",
    section: "sales-subscriptions",
    settleMarker: "cs-settle-feature-catalog-capability-grid",
    availability: "active",
    dataSource: "fixture",
  },

  // 19 — Connectivity Plans
  {
    id: "connectivity-plans",
    label: "Connectivity Plans",
    path: "/sales/connectivity-plans",
    section: "sales-subscriptions",
    settleMarker: "cs-settle-connectivity-plans-entitlement-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 20 — Data Products
  {
    id: "data-products",
    label: "Data Products",
    path: "/sales/data-products",
    section: "sales-subscriptions",
    settleMarker: "cs-settle-data-products-partner-feed-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 20c — Subscribers roster (OEM lens — stub pass 2026-09-14-cs-portal-persona-lenses-stub)
  {
    id: "subscribers-roster",
    label: "Subscribers",
    path: "/sales/subscribers",
    section: "sales-subscriptions",
    settleMarker: "cs-settle-subscribers-roster-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 20d — Grants (OEM lens — stub pass 2026-09-14-cs-portal-persona-lenses-stub)
  {
    id: "grants",
    label: "Grants",
    path: "/sales/grants",
    section: "sales-subscriptions",
    settleMarker: "cs-settle-grants-availability-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 20e — Signal Catalog (Data Model section — moved 2026-09-14 from sales-subscriptions)
  //   Signals are the atomic channels; data products bundle signals. Moved
  //   here from CMS (whose SignalCatalogView is a zero-content placeholder).
  //   Section moved from `sales-subscriptions` to `data-model` in the
  //   Data Model section extension of the same stub pass — Signal Catalog is
  //   a reference catalog, not a commerce surface.
  {
    id: "signal-catalog",
    label: "Signal Catalog",
    path: "/data-model/signals",
    section: "data-model",
    settleMarker: "cs-settle-signal-catalog-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 20f — Vehicle Models (Data Model section — stub pass 2026-09-14 extension)
  {
    id: "vehicle-models",
    label: "Vehicle Models",
    path: "/data-model/vehicle-models",
    section: "data-model",
    settleMarker: "cs-settle-vehicle-models-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 20g — ECUs (Data Model section — stub pass 2026-09-14 extension)
  {
    id: "ecus-catalog",
    label: "ECUs",
    path: "/data-model/ecus",
    section: "data-model",
    settleMarker: "cs-settle-ecus-catalog-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 20h — Decoder Manifests (Data Model section — stub pass 2026-09-14 extension)
  //   Tells FleetWise HOW to decode signals off the raw CAN/Ethernet bus.
  {
    id: "decoder-manifests",
    label: "Decoder Manifests",
    path: "/data-model/decoder-manifests",
    section: "data-model",
    settleMarker: "cs-settle-decoder-manifests-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 20i — Data Collection Campaigns (Data Model section — spec 2026-09-14-cs-portal-data-model-backend T5.2)
  //   Campaigns that govern what telemetry signals a vehicle collects.
  //   Reads the existing CMS campaign API.
  //   Phase A: CMS's own campaign surface is untouched. See spec § C1.
  {
    id: "data-collection-campaigns",
    label: "Data Collection Campaigns",
    path: "/data-model/data-collection-campaigns",
    section: "data-model",
    settleMarker: "cs-settle-data-collection-campaigns-table",
    availability: "active",
    dataSource: "live",
  },

  // 21 — Market Posture
  {
    id: "market-posture",
    label: "Market Posture",
    path: "/compliance/market-posture",
    section: "markets-compliance",
    settleMarker: "cs-settle-market-posture-jurisdiction-matrix",
    availability: "active",
    dataSource: "fixture",
  },

  // 22 — Consent & Privacy
  {
    id: "consent-privacy",
    label: "Consent & Privacy",
    path: "/compliance/consent",
    section: "markets-compliance",
    settleMarker: "cs-settle-consent-privacy-policy-version-table",
    availability: "active",
    dataSource: "fixture",
  },

  // 23 — Audit Log
  {
    id: "audit-log",
    label: "Audit Log",
    path: "/compliance/audit-log",
    section: "markets-compliance",
    settleMarker: "cs-settle-audit-log-event-stream-table",
    availability: "active",
    dataSource: "fixture",
  },
];

// ---------------------------------------------------------------------------
// Section display metadata — order matches UX spec nav block (spec T1.2 constraint)
// ---------------------------------------------------------------------------

const SECTION_LABELS: Record<ScreenSection, string> = {
  "command-center": "Command Center",
  connectivity: "Connectivity",
  software: "Software",
  "population-diagnostics": "Population Diagnostics",
  "manufacturing-lifecycle": "Manufacturing & Lifecycle",
  "data-model": "Data Model",
  "sales-subscriptions": "Sales & Subscriptions",
  "markets-compliance": "Markets & Compliance",
};

/** Section display order per the UX spec nav block. */
const SECTION_ORDER: ScreenSection[] = [
  "command-center",
  "connectivity",
  "software",
  "population-diagnostics",
  "manufacturing-lifecycle",
  "data-model",
  "sales-subscriptions",
  "markets-compliance",
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/**
 * Returns the seven nav sections in display order, each with its entries.
 * The order matches the UX spec's nav block exactly:
 *   Command Center → Connectivity → Software → Population Diagnostics →
 *   Manufacturing & Lifecycle → Sales & Subscriptions → Markets & Compliance
 */
export function getNavSections(): NavSection[] {
  return SECTION_ORDER.map((section) => ({
    section,
    label: SECTION_LABELS[section],
    entries: SCREEN_REGISTRY.filter((e) => e.section === section),
  }));
}

/**
 * Returns all registered route paths (exact paths only, no parameterised sub-routes).
 */
export function getAllPaths(): string[] {
  return SCREEN_REGISTRY.map((e) => e.path);
}

/**
 * Returns the registry entry whose path matches exactly, or undefined.
 * Parameterised sub-routes (/:vin, /:signalId) are handled by the caller —
 * this helper matches the base path only.
 */
export function getEntryByPath(path: string): ScreenEntry | undefined {
  return SCREEN_REGISTRY.find((e) => e.path === path);
}
