// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Provenance markers and typed value wrappers for the Connected Services portal.
 *
 * Spec: .kiro/specs/2026-09-03-cms-connected-services-portal/spec.md § D5
 *
 * Every value rendered in the UI must carry one of exactly three provenance
 * markers: "live", "simulated", or "absent".  There is no fourth case.
 *
 * Enforcement (T3.2 constraint):
 *   - Components accept ProvenanceValue<T> rather than raw values.
 *   - assertProvenance() throws at runtime if a ProvenanceValue is missing its
 *     marker or carries an unrecognised marker.
 *   - The test suite includes a case that asserts the throw.
 *
 * No trip/GPS/location field is defined here (compliance control (d)).
 */

// D5: exactly three values.  'measured' is valid in fleet_intelligence but NOT here.
export type ProvenanceMarker = "live" | "simulated" | "absent";

export const VALID_PROVENANCE_MARKERS: ReadonlySet<string> = new Set<ProvenanceMarker>([
  "live",
  "simulated",
  "absent",
]);

/**
 * A value paired with its provenance marker.
 *
 * "absent" means the field is not available — the component renders an explicit
 * empty state (not null-rendered-as-blank).
 */
export interface ProvenanceValue<T> {
  value: T | null;
  provenance: ProvenanceMarker;
}

/**
 * Assert that pv carries a recognised provenance marker.
 *
 * Throws if pv is undefined/null or if its provenance is not in VALID_PROVENANCE_MARKERS.
 * This enforces the spec D5 rule: "No component may render a value whose marker it did
 * not receive."  A component given a value with no marker throws rather than renders.
 *
 * @param pv - The ProvenanceValue to validate.
 * @param fieldName - Human-readable name for the field (used in the error message).
 */
export function assertProvenance<T>(
  pv: ProvenanceValue<T> | undefined | null,
  fieldName: string
): asserts pv is ProvenanceValue<T> {
  if (pv == null) {
    throw new Error(
      `Connected Services portal: field "${fieldName}" was rendered without a provenance ` +
        `marker. Spec D5: "No component may render a value whose marker it did not receive." ` +
        `Received: ${JSON.stringify(pv)}`
    );
  }
  if (!VALID_PROVENANCE_MARKERS.has(pv.provenance)) {
    throw new Error(
      `Connected Services portal: field "${fieldName}" has an unrecognised provenance ` +
        `marker "${pv.provenance}". ` +
        `Valid markers: ${[...VALID_PROVENANCE_MARKERS].join(", ")}. ` +
        `Spec D5: "There is no fourth case." ('measured' is valid in fleet_intelligence ` +
        `but NOT in the connected-services portal.)`
    );
  }
}

// ---------------------------------------------------------------------------
// Domain types — mirror Python response shapes in services/connectivity_api/
// No trip/GPS/location fields (control (d)).
// Cell/network location excluded — diagnosis path only (control (e)).
// ---------------------------------------------------------------------------

export interface SubscriberBinding {
  vin: ProvenanceValue<string>;
  iccid: ProvenanceValue<string | null>;
  imsi: ProvenanceValue<string | null>;
  profile: ProvenanceValue<string | null>;
  market: ProvenanceValue<string | null>;
  policyReference: ProvenanceValue<string | null>;
  // Denial flag — deterministic seam, not a GPS field.
  isDenied: ProvenanceValue<boolean>;
}

export type FleetHealthStatus =
  | "connected"
  | "degraded"
  | "ntn_fallback"
  | "unreachable";

export interface FleetHealthEntry {
  vin: ProvenanceValue<string>;
  status: ProvenanceValue<FleetHealthStatus>;
  // Denial flag — deterministic (spec D4 / T2.4).
  isDenied: ProvenanceValue<boolean>;
}

export interface FleetHealthRollup {
  entries: ProvenanceValue<FleetHealthEntry[]>;
}


// ---------------------------------------------------------------------------
// Tier 2 artifact-contract types
//
// Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/spec.md
//       § "Provenance and artifact-contract types" and § Tier classification item 3
//
// Four surfaces render the output of a Tier 2 autonomous agent:
//   - Signal Detection (§3.1)
//   - Diagnosis Workbench hypothesis list (§3.2 step 2)
//   - Subscriber Lookup root-cause panel (§2.2)
//   - Quality Signals (§4.2)
//
// Their fixtures carry the full artifact contract now so that wiring a real agent
// later is a data-source swap, not a redesign.
//
// The five metadata fields (computed_at, confidence, evidence, agent_version,
// inputs_hash) are EXEMPT from provenance-wrapping in the guard (spec D4 layer 2)
// because they describe a conclusion rather than being displayed data values.
// Everything inside `payload` is ProvenanceValue-wrapped as normal.
// ---------------------------------------------------------------------------

/** A rendered chip summarising one piece of evidence from a Tier 2 agent run. */
export interface EvidenceChip {
  /** Short label shown on the chip. */
  label: string;
  /** Optional detail rendered in a tooltip or expandable row. */
  detail?: string;
}

/**
 * The Tier 2 artifact envelope.
 *
 * T is the domain payload type — everything inside it must be ProvenanceValue-wrapped.
 * The five outer fields are metadata about the conclusion and are exempt from wrapping.
 */
export interface Tier2Artifact<T> {
  /** ISO-8601 timestamp of when the agent run completed. */
  computed_at: string;
  /** Agent's stated confidence in the conclusion, 0..1. */
  confidence: number;
  /** Evidence chips supporting the conclusion. */
  evidence: EvidenceChip[];
  /** Identifies the agent and version that produced this artifact. */
  agent_version: string;
  /** Hash of the inputs consumed, for idempotency and reproducibility. */
  inputs_hash: string;
  /** The domain payload. All fields within must be ProvenanceValue-wrapped. */
  payload: T;
}

// ---------------------------------------------------------------------------
// Section enum
//
// Mirrors the ScreenSection union in screenRegistry.ts as a const enum so that
// TypeScript types derived from it remain portable without importing the registry.
// ---------------------------------------------------------------------------

export const ScreenSectionValues = {
  CommandCenter: "command-center",
  Connectivity: "connectivity",
  Software: "software",
  PopulationDiagnostics: "population-diagnostics",
  ManufacturingLifecycle: "manufacturing-lifecycle",
  SalesSubscriptions: "sales-subscriptions",
  MarketsCompliance: "markets-compliance",
} as const;

export type ScreenSectionValue =
  (typeof ScreenSectionValues)[keyof typeof ScreenSectionValues];
