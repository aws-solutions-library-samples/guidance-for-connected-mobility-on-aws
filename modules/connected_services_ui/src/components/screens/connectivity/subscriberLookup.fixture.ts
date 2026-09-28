// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * subscriberLookup.fixture.ts — Simulated data for the Subscriber Lookup screen.
 *
 * Spec: .kiro/specs/2026-09-04-cms-connected-services-portal-v2/tasks.md T4.3
 *
 * ## VIN vocabulary
 *
 * VINs are imported from fleetHealth.fixture.ts — the single source of truth
 * for the fleet's VIN catalogue. Every VIN in FLEET_ROWS has a corresponding
 * entry in SIMULATED_SUBSCRIBER_DETAILS so that clicking any Fleet Health row
 * lands on the detail view rather than the not-found alert.
 *
 * The fixtureReferentialIntegrity guard (Contract 1) enforces this at test time:
 * every Fleet Health navigable VIN must resolve here.
 *
 * ## VINs chosen for the click-path
 *
 * DEGRADED_VIN_0 — first degraded row in Fleet Health (the row clicked in Hop 2).
 *   Has state: 'degraded' and rootCause so the root-cause panel renders for Hop 3.
 * DEGRADED_VIN_1 — second degraded row (provides a second valid degraded detail).
 * UNREACHABLE_VIN_0 — first unreachable row (also has rootCause).
 *
 * ## Provenance contract
 *
 * Every displayed-value field is a ProvenanceValue<T> with provenance: 'simulated'.
 * The five Tier2Artifact metadata fields (computed_at, confidence, evidence,
 * agent_version, inputs_hash) are exempt from wrapping per spec D4 and the
 * provenanceFixtures guard's EXEMPT_KEYS allowlist.
 *
 * ## No location fields
 *
 * No trip/GPS/location field appears anywhere in this file.
 */

import type {
  EvidenceChip,
  ProvenanceValue,
  Tier2Artifact,
} from "../../../types";
import { FLEET_ROWS } from "./fleetHealth.fixture";

// ---------------------------------------------------------------------------
// VIN constants derived from Fleet Health fixture (single source of truth)
// ---------------------------------------------------------------------------

const degradedRows = FLEET_ROWS.filter((r) => r.connectivityState.value === "degraded");
const unreachableRows = FLEET_ROWS.filter((r) => r.connectivityState.value === "unreachable");

if (degradedRows.length < 2) {
  throw new Error(
    "subscriberLookup.fixture: expected at least 2 degraded rows in FLEET_ROWS; " +
    `found ${String(degradedRows.length)}. Check fleetHealth.fixture.ts.`
  );
}
if (unreachableRows.length < 1) {
  throw new Error(
    "subscriberLookup.fixture: expected at least 1 unreachable row in FLEET_ROWS; " +
    `found ${String(unreachableRows.length)}. Check fleetHealth.fixture.ts.`
  );
}

/** First degraded VIN in Fleet Health — the VIN Hop 2 navigates to. */
export const DEGRADED_VIN_0: string = degradedRows[0]!.vin.value!;

/** Second degraded VIN in Fleet Health. */
export const DEGRADED_VIN_1: string = degradedRows[1]!.vin.value!;

/** First unreachable VIN in Fleet Health. */
export const UNREACHABLE_VIN_0: string = unreachableRows[0]!.vin.value!;

// ---------------------------------------------------------------------------
// Domain types for this screen
// ---------------------------------------------------------------------------

export type ConnectivityState =
  | "connected"
  | "degraded"
  | "ntn_fallback"
  | "unreachable"
  | "denied";

export interface SessionRecord {
  id: string;
  startedAt: ProvenanceValue<string>;
  endedAt: ProvenanceValue<string | null>;
  bearer: ProvenanceValue<string>;
  outcome: ProvenanceValue<string>;
}

export interface ConnectivityStateCard {
  bearer: ProvenanceValue<string>;
  signalStrength: ProvenanceValue<string>;
  activePolicies: ProvenanceValue<string[]>;
  lastSessions: SessionRecord[];
  state: ProvenanceValue<ConnectivityState>;
}

export interface SubscriberBindingCard {
  vin: ProvenanceValue<string>;
  iccid: ProvenanceValue<string>;
  imsi: ProvenanceValue<string>;
  profile: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  plan: ProvenanceValue<string>;
  tcuTier: ProvenanceValue<string>;
}

export interface SoftwareStateCard {
  baselineVersion: ProvenanceValue<string>;
  actualVersion: ProvenanceValue<string>;
  hasDrift: ProvenanceValue<boolean>;
}

export interface RootCauseHypothesis {
  rank: ProvenanceValue<number>;
  label: ProvenanceValue<string>;
  description: ProvenanceValue<string>;
  probability: ProvenanceValue<number>;
}

export interface SubscriberDetail {
  id: string;
  binding: SubscriberBindingCard;
  connectivity: ConnectivityStateCard;
  rootCause?: Tier2Artifact<RootCauseHypothesis[]>;
  software: SoftwareStateCard;
}

export interface RecentLookup {
  id: string;
  vin: ProvenanceValue<string>;
  market: ProvenanceValue<string>;
  lastLookedUpAt: ProvenanceValue<string>;
  connectivityState: ProvenanceValue<ConnectivityState>;
}

// ---------------------------------------------------------------------------
// Evidence chips
// ---------------------------------------------------------------------------

const EVIDENCE_SIGNAL_DEGRADED: EvidenceChip = {
  label: "Signal −28 dBm",
  detail: "Measured signal strength 2 h before last disconnect; 3 dB below LTE-Cat1 threshold.",
};

const EVIDENCE_APN_MISMATCH: EvidenceChip = {
  label: "APN mismatch",
  detail: "Provisioned APN 'iot.connect.us' does not match carrier's preferred 'em.connect.us'.",
};

const EVIDENCE_ROAMING_POLICY: EvidenceChip = {
  label: "Roaming policy blocked",
  detail: "Market-DE roaming policy applied; cross-border bearer not authorised for this plan.",
};

const EVIDENCE_TCU_RESTART: EvidenceChip = {
  label: "TCU restart ×3",
  detail: "Three TCU software restarts logged in 6 h window preceding the degraded event.",
};

const EVIDENCE_CARRIER_OUTAGE: EvidenceChip = {
  label: "Carrier outage correlated",
  detail: "Carrier API reports elevated packet-loss in US-East region during the same window.",
};

// ---------------------------------------------------------------------------
// Helper to build a minimal connected subscriber detail
// ---------------------------------------------------------------------------

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

function connectedDetail(
  vin: string,
  market: string,
  bearer: string,
  signal: string,
  iccid: string,
  imsi: string,
  plan: string,
  tcuTier: string,
): SubscriberDetail {
  return {
    id: vin,
    binding: {
      vin: sim(vin),
      iccid: sim(iccid),
      imsi: sim(imsi),
      profile: sim("connected-standard"),
      market: sim(market),
      plan: sim(plan),
      tcuTier: sim(tcuTier),
    },
    connectivity: {
      bearer: sim(bearer),
      signalStrength: sim(signal),
      activePolicies: sim(["APN-" + market + "-IoT"]),
      state: sim("connected" as ConnectivityState),
      lastSessions: [
        {
          id: "sess-" + vin.slice(-4) + "-001",
          startedAt: sim("2024-03-15T10:00:00Z"),
          endedAt: sim(null),
          bearer: sim(bearer),
          outcome: sim("active"),
        },
      ],
    },
    // No rootCause — vehicle is connected.
    software: {
      baselineVersion: sim("2.4.1-release"),
      actualVersion: sim("2.4.1-release"),
      hasDrift: sim(false),
    },
  };
}

function ntnDetail(
  vin: string,
  market: string,
  signal: string,
  iccid: string,
  imsi: string,
  plan: string,
): SubscriberDetail {
  return {
    id: vin,
    binding: {
      vin: sim(vin),
      iccid: sim(iccid),
      imsi: sim(imsi),
      profile: sim("connected-pro"),
      market: sim(market),
      plan: sim(plan),
      tcuTier: sim("Cat-M1"),
    },
    connectivity: {
      bearer: sim("NTN-satellite"),
      signalStrength: sim(signal),
      activePolicies: sim(["NTN-policy"]),
      state: sim("ntn_fallback" as ConnectivityState),
      lastSessions: [
        {
          id: "sess-" + vin.slice(-4) + "-001",
          startedAt: sim("2024-03-15T06:00:00Z"),
          endedAt: sim(null),
          bearer: sim("NTN-satellite"),
          outcome: sim("active"),
        },
      ],
    },
    // No rootCause — ntn_fallback is a degraded state but not unreachable;
    // per spec, rootCause renders for degraded and unreachable only.
    // ntn_fallback is categorised as 'info', not an error state, so no panel.
    software: {
      baselineVersion: sim("2.4.1-release"),
      actualVersion: sim("2.4.1-release"),
      hasDrift: sim(false),
    },
  };
}

function degradedDetail(
  vin: string,
  market: string,
  signal: string,
  iccid: string,
  imsi: string,
  plan: string,
  hypothesisLabel: string,
  confidence: number,
): SubscriberDetail {
  return {
    id: vin,
    binding: {
      vin: sim(vin),
      iccid: sim(iccid),
      imsi: sim(imsi),
      profile: sim("connected-pro"),
      market: sim(market),
      plan: sim(plan),
      tcuTier: sim("Cat-1"),
    },
    connectivity: {
      bearer: sim("LTE-Cat-1"),
      signalStrength: sim(signal),
      activePolicies: sim(["APN-" + market + "-IoT", "Roaming-Restricted"]),
      state: sim("degraded" as ConnectivityState),
      lastSessions: [
        {
          id: "sess-" + vin.slice(-4) + "-001",
          startedAt: sim("2024-03-15T10:00:00Z"),
          endedAt: sim("2024-03-15T10:47:23Z"),
          bearer: sim("LTE-Cat-1"),
          outcome: sim("disconnected_signal_loss"),
        },
      ],
    },
    rootCause: {
      computed_at: "2024-03-15T11:00:00Z",
      confidence,
      evidence: [EVIDENCE_SIGNAL_DEGRADED, EVIDENCE_APN_MISMATCH],
      agent_version: "connectivity-health-agent@0.1.0-simulated",
      inputs_hash: "sha256:" + "a1b2" + vin.replace(/[^a-zA-Z0-9]/g, "").slice(0, 58).padEnd(58, "0"),
      payload: [
        {
          rank: sim(1),
          label: sim(hypothesisLabel),
          description: sim(`Signal ${signal} is at threshold; marginal coverage causing drops.`),
          probability: sim(confidence),
        },
      ],
    },
    software: {
      baselineVersion: sim("2.4.1-release"),
      actualVersion: sim("2.4.1-release"),
      hasDrift: sim(false),
    },
  };
}

function unreachableDetail(
  vin: string,
  market: string,
  signal: string,
  iccid: string,
  imsi: string,
  plan: string,
  confidence: number,
): SubscriberDetail {
  return {
    id: vin,
    binding: {
      vin: sim(vin),
      iccid: sim(iccid),
      imsi: sim(imsi),
      profile: sim("connected-pro"),
      market: sim(market),
      plan: sim(plan),
      tcuTier: sim("Cat-M1"),
    },
    connectivity: {
      bearer: sim("LTE-Cat-M1"),
      signalStrength: sim(signal),
      activePolicies: sim(["APN-" + market + "-IoT"]),
      state: sim("unreachable" as ConnectivityState),
      lastSessions: [
        {
          id: "sess-" + vin.slice(-4) + "-001",
          startedAt: sim("2024-03-14T23:59:00Z"),
          endedAt: sim("2024-03-14T23:59:01Z"),
          bearer: sim("LTE-Cat-M1"),
          outcome: sim("tcu_restart"),
        },
      ],
    },
    rootCause: {
      computed_at: "2024-03-15T00:30:00Z",
      confidence,
      evidence: [EVIDENCE_ROAMING_POLICY, EVIDENCE_TCU_RESTART, EVIDENCE_CARRIER_OUTAGE],
      agent_version: "connectivity-health-agent@0.1.0-simulated",
      inputs_hash: "sha256:" + "b2c3" + vin.replace(/[^a-zA-Z0-9]/g, "").slice(0, 58).padEnd(58, "0"),
      payload: [
        {
          rank: sim(1),
          label: sim("TCU software fault"),
          description: sim("Repeated TCU restarts caused full bearer loss; device is unreachable via all paths."),
          probability: sim(confidence),
        },
      ],
    },
    software: {
      baselineVersion: sim("2.4.1-release"),
      actualVersion: sim("2.4.1-release"),
      hasDrift: sim(false),
    },
  };
}

// ---------------------------------------------------------------------------
// Simulated VIN catalogue — one entry per Fleet Health row
//
// Fleet Health rows (in fixture order):
//   row-001  1HGCM82633A004352  US        connected
//   row-002  WBA3A5C51CF256985  Germany   degraded    ← DEGRADED_VIN_0
//   row-003  MAKA0000000000003  India     connected
//   row-004  1HGCM82633A004353  US        degraded    ← DEGRADED_VIN_1
//   row-005  WBA3A5C51CF256986  Germany   ntn_fallback
//   row-006  1HGCM82633A004354  US        unreachable ← UNREACHABLE_VIN_0
//   row-007  MAKA0000000000007  India     connected
//   row-008  WBA3A5C51CF256987  Germany   degraded
//   row-009  1HGCM82633A004355  US        connected
//   row-010  MAKA0000000000010  India     ntn_fallback
//   row-011  1HGCM82633A004356  US        degraded
//   row-012  WBA3A5C51CF256988  Germany   unreachable
// ---------------------------------------------------------------------------

export const SIMULATED_SUBSCRIBER_DETAILS: Record<string, SubscriberDetail> = {
  // row-001 — US / connected
  "1HGCM82633A004352": connectedDetail(
    "1HGCM82633A004352", "US", "LTE", "−73 dBm",
    "89012345678901234521", "310150123456021", "Enterprise Unlimited", "TCU-2"
  ),

  // row-002 — Germany / degraded ← DEGRADED_VIN_0 (Hop 2 target)
  [DEGRADED_VIN_0]: {
    id: DEGRADED_VIN_0,
    binding: {
      vin: sim(DEGRADED_VIN_0),
      iccid: sim("89049012345678901201"),
      imsi: sim("262010123456001"),
      profile: sim("connected-pro"),
      market: sim("DE"),
      plan: sim("Enterprise EU"),
      tcuTier: sim("Cat-1"),
    },
    connectivity: {
      bearer: sim("LTE-Cat-1"),
      signalStrength: sim("−98 dBm"),
      activePolicies: sim(["APN-DE-IoT", "Roaming-Restricted"]),
      state: sim("degraded"),
      lastSessions: [
        {
          id: "sess-de-001",
          startedAt: sim("2024-03-15T10:00:00Z"),
          endedAt: sim("2024-03-15T10:47:23Z"),
          bearer: sim("LTE-Cat-1"),
          outcome: sim("disconnected_signal_loss"),
        },
        {
          id: "sess-de-002",
          startedAt: sim("2024-03-15T08:12:00Z"),
          endedAt: sim("2024-03-15T10:00:00Z"),
          bearer: sim("LTE-Cat-1"),
          outcome: sim("normal_close"),
        },
        {
          id: "sess-de-003",
          startedAt: sim("2024-03-14T22:30:00Z"),
          endedAt: sim("2024-03-15T08:12:00Z"),
          bearer: sim("NTN-fallback"),
          outcome: sim("normal_close"),
        },
      ],
    },
    rootCause: {
      computed_at: "2024-03-15T11:00:00Z",
      confidence: 0.82,
      evidence: [EVIDENCE_SIGNAL_DEGRADED, EVIDENCE_APN_MISMATCH, EVIDENCE_CARRIER_OUTAGE, EVIDENCE_TCU_RESTART],
      agent_version: "connectivity-health-agent@0.1.0-simulated",
      inputs_hash: "sha256:a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
      payload: [
        {
          rank: sim(1),
          label: sim("APN misconfiguration"),
          description: sim("Provisioned APN does not match carrier preferred APN, causing bearer re-negotiation failures."),
          probability: sim(0.67),
        },
        {
          rank: sim(2),
          label: sim("Signal margin at threshold"),
          description: sim("Signal strength −98 dBm is at LTE-Cat-1 minimum; marginal coverage causing intermittent drops."),
          probability: sim(0.21),
        },
        {
          rank: sim(3),
          label: sim("Correlated carrier outage"),
          description: sim("Carrier reports elevated packet loss in DE region during the same window."),
          probability: sim(0.12),
        },
      ],
    },
    software: {
      baselineVersion: sim("2.4.1-release"),
      actualVersion: sim("2.4.1-release"),
      hasDrift: sim(false),
    },
  },

  // row-003 — India / connected
  "MAKA0000000000003": connectedDetail(
    "MAKA0000000000003", "IN", "4G", "−81 dBm",
    "89910123456789012031", "404450123456031", "APAC Pro", "TCU-1"
  ),

  // row-004 — US / degraded ← DEGRADED_VIN_1
  [DEGRADED_VIN_1]: {
    ...degradedDetail(
      DEGRADED_VIN_1, "US", "−104 dBm",
      "89012345678901234502", "310150123456002", "Standard US",
      "Signal below threshold", 0.74
    ),
    software: {
      baselineVersion: sim("2.4.1-release"),
      actualVersion: sim("2.3.9-hotfix-001"),
      hasDrift: sim(true),
    },
  },

  // row-005 — Germany / ntn_fallback
  "WBA3A5C51CF256986": ntnDetail(
    "WBA3A5C51CF256986", "DE", "−115 dBm",
    "89049012345678901205", "262010123456005", "Enterprise EU"
  ),

  // row-006 — US / unreachable ← UNREACHABLE_VIN_0
  [UNREACHABLE_VIN_0]: {
    id: UNREACHABLE_VIN_0,
    binding: {
      vin: sim(UNREACHABLE_VIN_0),
      iccid: sim("89012345678901234503"),
      imsi: sim("310150123456003"),
      profile: sim("connected-pro"),
      market: sim("US"),
      plan: sim("Enterprise Unlimited"),
      tcuTier: sim("Cat-M1"),
    },
    connectivity: {
      bearer: sim("LTE-Cat-M1"),
      signalStrength: sim("—"),
      activePolicies: sim(["APN-US-IoT"]),
      state: sim("unreachable"),
      lastSessions: [
        {
          id: "sess-unr-001",
          startedAt: sim("2024-03-14T23:59:00Z"),
          endedAt: sim("2024-03-14T23:59:01Z"),
          bearer: sim("LTE-Cat-M1"),
          outcome: sim("tcu_restart"),
        },
      ],
    },
    rootCause: {
      computed_at: "2024-03-15T00:30:00Z",
      confidence: 0.91,
      evidence: [EVIDENCE_ROAMING_POLICY, EVIDENCE_TCU_RESTART],
      agent_version: "connectivity-health-agent@0.1.0-simulated",
      inputs_hash: "sha256:c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2c3d4",
      payload: [
        {
          rank: sim(1),
          label: sim("TCU software fault"),
          description: sim("Repeated TCU restarts caused full bearer loss; device is unreachable via all paths."),
          probability: sim(0.91),
        },
      ],
    },
    software: {
      baselineVersion: sim("2.4.1-release"),
      actualVersion: sim("2.4.1-release"),
      hasDrift: sim(false),
    },
  },

  // row-007 — India / connected
  "MAKA0000000000007": connectedDetail(
    "MAKA0000000000007", "IN", "4G", "−77 dBm",
    "89910123456789012071", "404450123456071", "APAC Pro", "TCU-1"
  ),

  // row-008 — Germany / degraded
  "WBA3A5C51CF256987": degradedDetail(
    "WBA3A5C51CF256987", "DE", "−101 dBm",
    "89049012345678901208", "262010123456008", "Enterprise EU",
    "Signal at threshold", 0.71
  ),

  // row-009 — US / connected
  "1HGCM82633A004355": connectedDetail(
    "1HGCM82633A004355", "US", "LTE", "−69 dBm",
    "89012345678901234509", "310150123456009", "Enterprise Unlimited", "TCU-3"
  ),

  // row-010 — India / ntn_fallback
  "MAKA0000000000010": ntnDetail(
    "MAKA0000000000010", "IN", "−119 dBm",
    "89910123456789012101", "404450123456101", "APAC Pro"
  ),

  // row-011 — US / degraded
  "1HGCM82633A004356": degradedDetail(
    "1HGCM82633A004356", "US", "−99 dBm",
    "89012345678901234511", "310150123456011", "Standard US",
    "APN misconfiguration", 0.68
  ),

  // row-012 — Germany / unreachable
  "WBA3A5C51CF256988": unreachableDetail(
    "WBA3A5C51CF256988", "DE", "—",
    "89049012345678901212", "262010123456012", "Enterprise EU", 0.88
  ),
};

/**
 * SIMULATED_RECENT_LOOKUPS
 *
 * Shown in the search-state recent-lookups list.
 * All displayed fields are ProvenanceValue-wrapped per spec D4 guard requirements.
 * VINs are derived from the same fleet-health constants to keep vocabularies aligned.
 */
export const SIMULATED_RECENT_LOOKUPS: RecentLookup[] = [
  {
    id: "recent-1",
    vin: sim(DEGRADED_VIN_0),
    market: sim("DE"),
    lastLookedUpAt: sim("2024-03-15T11:23:00Z"),
    connectivityState: sim("degraded"),
  },
  {
    id: "recent-2",
    vin: sim(DEGRADED_VIN_1),
    market: sim("US"),
    lastLookedUpAt: sim("2024-03-15T10:15:00Z"),
    connectivityState: sim("degraded"),
  },
  {
    id: "recent-3",
    vin: sim(UNREACHABLE_VIN_0),
    market: sim("US"),
    lastLookedUpAt: sim("2024-03-15T09:47:00Z"),
    connectivityState: sim("unreachable"),
  },
];
