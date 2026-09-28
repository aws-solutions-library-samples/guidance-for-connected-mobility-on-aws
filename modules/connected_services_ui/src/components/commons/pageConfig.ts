// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * pageConfig.ts — route-to-config map for Connected Services portal page chrome.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md D9
 * Modelled on: DMS frontend/src/components/commons/pageConfig.ts (read-only)
 *
 * Every registry path maps to a {title, description, breadcrumbs} triple.
 * Parameterised sub-routes (/connectivity/subscriber-lookup/:vin,
 * /software/workbench/:signalId) resolve to their parent entry.
 * The fallback for unknown paths returns a non-empty title — a blank banner
 * ships silently, which is the failure mode this guard is here to prevent.
 *
 * ## No API imports
 * This file has zero runtime dependencies beyond its own types.
 */

import type { BreadcrumbGroupProps } from "@cloudscape-design/components";

export interface PageConfig {
  title: string;
  description: string;
  breadcrumbs: BreadcrumbGroupProps.Item[];
}

// ── Root crumb (shared by all non-landing routes) ─────────────────────────────

const ROOT_CRUMB: BreadcrumbGroupProps.Item = {
  text: "Connected Services",
  href: "/",
};

// ── Section crumbs ─────────────────────────────────────────────────────────────

const CONNECTIVITY_CRUMB: BreadcrumbGroupProps.Item = {
  text: "Connectivity",
  href: "/connectivity/fleet-health",
};

const SOFTWARE_CRUMB: BreadcrumbGroupProps.Item = {
  text: "Software",
  href: "/software/signals",
};

const DIAGNOSTICS_CRUMB: BreadcrumbGroupProps.Item = {
  text: "Population Diagnostics",
  href: "/diagnostics/fault-patterns",
};

const MANUFACTURING_CRUMB: BreadcrumbGroupProps.Item = {
  text: "Manufacturing & Lifecycle",
  href: "/manufacturing/build-order",
};

const DATA_MODEL_CRUMB: BreadcrumbGroupProps.Item = {
  text: "Data Model",
  href: "/data-model/signals",
};

const SALES_CRUMB: BreadcrumbGroupProps.Item = {
  text: "Sales & Subscriptions",
  href: "/sales/feature-catalog",
};

const COMPLIANCE_CRUMB: BreadcrumbGroupProps.Item = {
  text: "Markets & Compliance",
  href: "/compliance/market-posture",
};

// ── Route map ─────────────────────────────────────────────────────────────────

const ROUTE_MAP: Record<string, PageConfig> = {
  // ── Command Center ────────────────────────────────────────────────────────
  "/": {
    title: "Command Center",
    description:
      "Cross-domain fleet overview — connectivity, software, quality, and pending approvals",
    breadcrumbs: [ROOT_CRUMB],
  },

  // ── Connectivity ──────────────────────────────────────────────────────────
  "/connectivity/fleet-health": {
    title: "Fleet Health",
    description:
      "Connectivity state by market — connected, degraded, NTN-fallback, and unreachable",
    breadcrumbs: [
      ROOT_CRUMB,
      CONNECTIVITY_CRUMB,
      { text: "Fleet Health", href: "/connectivity/fleet-health" },
    ],
  },

  "/connectivity/subscriber-lookup": {
    title: "Subscriber Lookup",
    description:
      "Search by VIN, ICCID, or IMSI to inspect binding, state, and root-cause diagnostics",
    breadcrumbs: [
      ROOT_CRUMB,
      CONNECTIVITY_CRUMB,
      { text: "Subscriber Lookup", href: "/connectivity/subscriber-lookup" },
    ],
  },

  "/connectivity/policy": {
    title: "Policy & Control",
    description:
      "APN configuration, traffic priority, geo-fencing, and QoS policy authoring",
    breadcrumbs: [
      ROOT_CRUMB,
      CONNECTIVITY_CRUMB,
      { text: "Policy & Control", href: "/connectivity/policy" },
    ],
  },

  "/connectivity/rate-plans": {
    title: "Rate Plans",
    description:
      "Plan catalogue, fleet assignments, usage tracking, and billing-owner management",
    breadcrumbs: [
      ROOT_CRUMB,
      CONNECTIVITY_CRUMB,
      { text: "Rate Plans", href: "/connectivity/rate-plans" },
    ],
  },

  // ── Software ──────────────────────────────────────────────────────────────
  "/software/signals": {
    title: "Signal Detection",
    description:
      "Population-scale anomaly signals with Tier 2 confidence and evidence chains",
    breadcrumbs: [
      ROOT_CRUMB,
      SOFTWARE_CRUMB,
      { text: "Signal Detection", href: "/software/signals" },
    ],
  },

  "/software/workbench": {
    title: "Diagnosis Workbench",
    description:
      "Six-step guided diagnostic workflow — from signal to root cause to remediation",
    breadcrumbs: [
      ROOT_CRUMB,
      SOFTWARE_CRUMB,
      { text: "Diagnosis Workbench", href: "/software/workbench" },
    ],
  },

  "/software/campaigns": {
    title: "Campaigns",
    description:
      "Software campaign rollout list — create, monitor, and approve OTA deployments",
    breadcrumbs: [
      ROOT_CRUMB,
      SOFTWARE_CRUMB,
      { text: "Campaigns", href: "/software/campaigns" },
    ],
  },

  "/software/rollout": {
    title: "Rollout Monitor",
    description: "Live rollout progress by campaign — adoption rates, errors, and retries",
    breadcrumbs: [
      ROOT_CRUMB,
      SOFTWARE_CRUMB,
      { text: "Rollout Monitor", href: "/software/rollout" },
    ],
  },

  "/software/security": {
    title: "Security Monitor (R155)",
    description:
      "UNECE R155 cybersecurity monitoring — not yet available in this release",
    breadcrumbs: [
      ROOT_CRUMB,
      SOFTWARE_CRUMB,
      { text: "Security Monitor (R155)", href: "/software/security" },
    ],
  },

  // ── Population Diagnostics ────────────────────────────────────────────────
  "/diagnostics/fault-patterns": {
    title: "Fault Patterns",
    description:
      "DTC and fault-signature clusters across the fleet — population-level trend analysis",
    breadcrumbs: [
      ROOT_CRUMB,
      DIAGNOSTICS_CRUMB,
      { text: "Fault Patterns", href: "/diagnostics/fault-patterns" },
    ],
  },

  "/diagnostics/quality-signals": {
    title: "Quality Signals",
    description:
      "Cross-domain quality indicators — warranty proxies, recall triggers, and OEM notifications",
    breadcrumbs: [
      ROOT_CRUMB,
      DIAGNOSTICS_CRUMB,
      { text: "Quality Signals", href: "/diagnostics/quality-signals" },
    ],
  },

  // ── Manufacturing & Lifecycle ─────────────────────────────────────────────
  "/manufacturing/build-order": {
    title: "Build & Order",
    description:
      "Vehicle build configuration, factory order management, and production scheduling",
    breadcrumbs: [
      ROOT_CRUMB,
      MANUFACTURING_CRUMB,
      { text: "Build & Order", href: "/manufacturing/build-order" },
    ],
  },

  "/manufacturing/factory-registration": {
    title: "Factory Registration",
    description:
      "VIN-batch provisioning — bind TCU, assign SIM profile, and register connectivity",
    breadcrumbs: [
      ROOT_CRUMB,
      MANUFACTURING_CRUMB,
      { text: "Factory Registration", href: "/manufacturing/factory-registration" },
    ],
  },

  "/manufacturing/homologation": {
    title: "Homologation",
    description:
      "RXSWIN registry and type-approval tracking for each market and software baseline",
    breadcrumbs: [
      ROOT_CRUMB,
      MANUFACTURING_CRUMB,
      { text: "Homologation", href: "/manufacturing/homologation" },
    ],
  },

  "/manufacturing/ownership-transfer": {
    title: "Ownership Transfer",
    description:
      "Subscriber re-binding and vehicle handover — retail to fleet, fleet to fleet",
    breadcrumbs: [
      ROOT_CRUMB,
      MANUFACTURING_CRUMB,
      { text: "Ownership Transfer", href: "/manufacturing/ownership-transfer" },
    ],
  },

  "/manufacturing/stop-ship": {
    title: "Stop-Ship / Stop-Sale",
    description:
      "Authorise and track stop-ship and stop-sale holds with OEM-approval workflow",
    breadcrumbs: [
      ROOT_CRUMB,
      MANUFACTURING_CRUMB,
      { text: "Stop-Ship / Stop-Sale", href: "/manufacturing/stop-ship" },
    ],
  },

  // ── Sales & Subscriptions ─────────────────────────────────────────────────
  "/sales/feature-catalog": {
    title: "Feature Catalog",
    description:
      "OEM feature capabilities — active, planned, and market-restricted availability",
    breadcrumbs: [
      ROOT_CRUMB,
      SALES_CRUMB,
      { text: "Feature Catalog", href: "/sales/feature-catalog" },
    ],
  },

  "/sales/connectivity-plans": {
    title: "Connectivity Plans",
    description:
      "Plan entitlements, market-tier assignments, and subscriber binding overview",
    breadcrumbs: [
      ROOT_CRUMB,
      SALES_CRUMB,
      { text: "Connectivity Plans", href: "/sales/connectivity-plans" },
    ],
  },

  "/sales/data-products": {
    title: "Data Products",
    description: "Partner data-feed catalogue — signal definitions and access management",
    breadcrumbs: [
      ROOT_CRUMB,
      SALES_CRUMB,
      { text: "Data Products", href: "/sales/data-products" },
    ],
  },

  "/connectivity/available-vehicles": {
    title: "Available Vehicles",
    description:
      "VINs your account can enrol into a subscription — filtered to vehicles with live telemetry",
    breadcrumbs: [
      ROOT_CRUMB,
      CONNECTIVITY_CRUMB,
      { text: "Available Vehicles", href: "/connectivity/available-vehicles" },
    ],
  },

  "/data-model/simulate-vehicle": {
    title: "Simulate Vehicle",
    description:
      "Exercise a vehicle model against its decoder manifest — start a single-vehicle simulation session and monitor emitted telemetry",
    breadcrumbs: [
      ROOT_CRUMB,
      DATA_MODEL_CRUMB,
      { text: "Simulate Vehicle", href: "/data-model/simulate-vehicle" },
    ],
  },

  "/sales/subscribers": {
    title: "Subscribers",
    description:
      "Every 3P subscriber consuming data from this platform — product portfolio, VIN count, quota state",
    breadcrumbs: [
      ROOT_CRUMB,
      SALES_CRUMB,
      { text: "Subscribers", href: "/sales/subscribers" },
    ],
  },

  "/sales/grants": {
    title: "Grants",
    description:
      "VINs marked available for subscriber enrollment — held, unclaimed, or revoked",
    breadcrumbs: [
      ROOT_CRUMB,
      SALES_CRUMB,
      { text: "Grants", href: "/sales/grants" },
    ],
  },

  "/data-model/signals": {
    title: "Signal Catalog",
    description:
      "Atomic telemetry channels — the raw ingredients data products bundle",
    breadcrumbs: [
      ROOT_CRUMB,
      DATA_MODEL_CRUMB,
      { text: "Signal Catalog", href: "/data-model/signals" },
    ],
  },

  "/data-model/vehicle-models": {
    title: "Vehicle Models",
    description:
      "Vehicle platforms and the curated signal subsets each emits",
    breadcrumbs: [
      ROOT_CRUMB,
      DATA_MODEL_CRUMB,
      { text: "Vehicle Models", href: "/data-model/vehicle-models" },
    ],
  },

  "/data-model/ecus": {
    title: "ECUs",
    description:
      "Electronic Control Units — hardware sources of signals, targets of firmware campaigns",
    breadcrumbs: [
      ROOT_CRUMB,
      DATA_MODEL_CRUMB,
      { text: "ECUs", href: "/data-model/ecus" },
    ],
  },

  "/data-model/decoder-manifests": {
    title: "Decoder Manifests",
    description:
      "Versioned decoding recipes — how FleetWise translates raw bus frames into signals",
    breadcrumbs: [
      ROOT_CRUMB,
      DATA_MODEL_CRUMB,
      { text: "Decoder Manifests", href: "/data-model/decoder-manifests" },
    ],
  },

  "/data-model/data-collection-campaigns": {
    title: "Data Collection Campaigns",
    description:
      "Campaigns that govern what telemetry signals a vehicle collects",
    breadcrumbs: [
      ROOT_CRUMB,
      DATA_MODEL_CRUMB,
      { text: "Data Collection Campaigns", href: "/data-model/data-collection-campaigns" },
    ],
  },

  // ── Markets & Compliance ──────────────────────────────────────────────────
  "/compliance/market-posture": {
    title: "Market Posture",
    description:
      "Jurisdiction matrix — regional compliance status and market-entry readiness",
    breadcrumbs: [
      ROOT_CRUMB,
      COMPLIANCE_CRUMB,
      { text: "Market Posture", href: "/compliance/market-posture" },
    ],
  },

  "/compliance/consent": {
    title: "Consent & Privacy",
    description:
      "Privacy-policy version tracking and subscriber consent management by market",
    breadcrumbs: [
      ROOT_CRUMB,
      COMPLIANCE_CRUMB,
      { text: "Consent & Privacy", href: "/compliance/consent" },
    ],
  },

  "/compliance/audit-log": {
    title: "Audit Log",
    description:
      "Immutable event stream — operator actions, state changes, and policy decisions",
    breadcrumbs: [
      ROOT_CRUMB,
      COMPLIANCE_CRUMB,
      { text: "Audit Log", href: "/compliance/audit-log" },
    ],
  },
};

// ── Fallback ──────────────────────────────────────────────────────────────────
//
// The fallback must return a non-empty title. A blank banner ships silently —
// no user reports it as a crash, but every visitor sees a broken header.

const FALLBACK_CONFIG: PageConfig = {
  title: "Connected Services",
  description: "",
  breadcrumbs: [ROOT_CRUMB],
};

// ── getPageConfig ─────────────────────────────────────────────────────────────

/**
 * Return the page config for the given pathname.
 *
 * Handles:
 * - Exact matches for all 23 registry paths
 * - /connectivity/subscriber-lookup/:vin sub-route → resolved as
 *   /connectivity/subscriber-lookup (same screen, detail state)
 * - /software/workbench/:signalId sub-route → resolved as /software/workbench
 * - All unknown paths → FALLBACK_CONFIG (non-empty title guaranteed)
 */
export function getPageConfig(pathname: string): PageConfig {
  // Exact match — covers all 23 named routes
  if (ROUTE_MAP[pathname]) {
    return ROUTE_MAP[pathname];
  }

  // /connectivity/subscriber-lookup/:vin — same banner as the lookup screen
  if (pathname.startsWith("/connectivity/subscriber-lookup/")) {
    return ROUTE_MAP["/connectivity/subscriber-lookup"];
  }

  // /software/workbench/:signalId — same banner as the workbench screen
  if (pathname.startsWith("/software/workbench/")) {
    return ROUTE_MAP["/software/workbench"];
  }

  // /sales/subscribers/:consumerId — subscriber detail (stub pass 2026-09-14)
  if (pathname.startsWith("/sales/subscribers/")) {
    return {
      title: "Subscriber detail",
      description:
        "One subscriber's overview, subscription scope, and consumption history",
      breadcrumbs: [
        ROOT_CRUMB,
        SALES_CRUMB,
        { text: "Subscribers", href: "/sales/subscribers" },
        { text: "Detail", href: pathname },
      ],
    };
  }

  // /data-model/signals/:signalId — signal detail (stub pass 2026-09-14; moved from /sales/signals/)
  if (pathname.startsWith("/data-model/signals/")) {
    return {
      title: "Signal detail",
      description:
        "One signal's specification and the data products that bundle it",
      breadcrumbs: [
        ROOT_CRUMB,
        DATA_MODEL_CRUMB,
        { text: "Signal Catalog", href: "/data-model/signals" },
        { text: "Detail", href: pathname },
      ],
    };
  }

  // /data-model/vehicle-models/:modelId — vehicle model detail (stub pass 2026-09-14 Data Model extension)
  if (pathname.startsWith("/data-model/vehicle-models/")) {
    return {
      title: "Vehicle model detail",
      description:
        "One vehicle model's signals emitted, ECUs installed, and compatible data products",
      breadcrumbs: [
        ROOT_CRUMB,
        DATA_MODEL_CRUMB,
        { text: "Vehicle Models", href: "/data-model/vehicle-models" },
        { text: "Detail", href: pathname },
      ],
    };
  }

  // /data-model/decoder-manifests/:manifestId — decoder manifest detail (stub pass 2026-09-14 Data Model extension)
  if (pathname.startsWith("/data-model/decoder-manifests/")) {
    return {
      title: "Decoder manifest detail",
      description:
        "Signal mappings, ECU coverage, and vehicle models using this manifest version",
      breadcrumbs: [
        ROOT_CRUMB,
        DATA_MODEL_CRUMB,
        { text: "Decoder Manifests", href: "/data-model/decoder-manifests" },
        { text: "Detail", href: pathname },
      ],
    };
  }

  // /data-model/data-collection-campaigns/:campaignName — campaign detail
  // (spec 2026-09-20-cs-campaigns-screen-restructure T3.1)
  //
  // Without this branch the route falls through to FALLBACK_CONFIG, whose title is
  // "Connected Services" with a lone root crumb — which is exactly what the detail view
  // showed on its first staging deploy. Every other parameterised detail route has a branch
  // here; this one was added with the route and the branch was not. Found by user UAT.
  if (pathname.startsWith("/data-model/data-collection-campaigns/")) {
    return {
      title: "Campaign detail",
      description:
        "One data-collection campaign's definition, signals collected, ECU coverage, and assigned vehicles",
      breadcrumbs: [
        ROOT_CRUMB,
        DATA_MODEL_CRUMB,
        { text: "Data Collection Campaigns", href: "/data-model/data-collection-campaigns" },
        { text: "Detail", href: pathname },
      ],
    };
  }

  // /sales/data-products/:productId — product detail (stub pass 2026-09-14 extension)
  if (pathname.startsWith("/sales/data-products/")) {
    return {
      title: "Data product detail",
      description:
        "One product's spec, bundled signals, subscribers, and subscribe action",
      breadcrumbs: [
        ROOT_CRUMB,
        SALES_CRUMB,
        { text: "Data Products", href: "/sales/data-products" },
        { text: "Detail", href: pathname },
      ],
    };
  }

  return FALLBACK_CONFIG;
}
