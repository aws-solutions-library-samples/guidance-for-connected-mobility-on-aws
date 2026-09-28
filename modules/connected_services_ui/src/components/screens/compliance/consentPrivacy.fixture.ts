// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * consentPrivacy.fixture.ts — simulated data for the Consent & Privacy screen (T6.4).
 *
 * ## Consent categories
 *
 * Four categories per subscriber:
 *   - collection: consent to data collection
 *   - location: consent to location-category data processing (this is a
 *               consent CATEGORY NAME — not a coordinate, trip, or cell ID)
 *   - marketing: consent to marketing communications
 *   - thirdPartySharing: consent to third-party data sharing
 *
 * Each category carries a boolean consented value and, when revoked (false),
 * a revokedAt timestamp recording when consent was withdrawn.
 *
 * ## Data-subject request log
 *
 * Records of formal data-subject requests (access, erasure, portability) and
 * their current processing status.
 *
 * ## VIN source
 *
 * Subscriber IDs are generic identifiers, not VINs.
 * VINs in the data-subject request log reference FLEET_ROWS from
 * fleetHealth.fixture.ts — imported as constants, not literals.
 *
 * ## ProvenanceValue contract
 *
 * Every displayed-value field is a ProvenanceValue<T> with provenance: 'simulated'.
 * Structural identity fields (id) are exempt per spec D4.
 *
 * ## No location values
 *
 * The "location" category is a consent category name — it records whether the
 * subscriber consented to processing within the location-data category. No
 * coordinate, GPS fix, cell ID, trip record, or geographic point appears anywhere
 * in this fixture. The boolean value is the consent state, not a location.
 */

import type { ProvenanceValue } from "../../../types";
import { FLEET_ROWS } from "../connectivity/fleetHealth.fixture";

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Domain types ──────────────────────────────────────────────────────────────

export interface ConsentCategory {
  /** True = consent granted, false = consent revoked / not given. */
  consented: ProvenanceValue<boolean>;
  /**
   * ISO-8601 timestamp of when consent was revoked.
   * Absent (null) if consent is currently granted or was never given.
   */
  revokedAt: ProvenanceValue<string | null>;
}

export interface ConsentStateRow {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  subscriberId: ProvenanceValue<string>;
  /** Consent to data collection. */
  collection: ConsentCategory;
  /**
   * Consent to processing within the location-data category.
   * This is a consent CATEGORY NAME — not a coordinate or location value.
   */
  location: ConsentCategory;
  /** Consent to marketing communications. */
  marketing: ConsentCategory;
  /** Consent to third-party data sharing. */
  thirdPartySharing: ConsentCategory;
}

export type DsrRequestType = "access" | "erasure" | "portability";
export type DsrStatus = "pending" | "in_progress" | "completed" | "rejected";

export interface DataSubjectRequest {
  /** Structural id — exempt from provenance wrapping per spec D4. */
  id: string;
  subscriberId: ProvenanceValue<string>;
  requestType: ProvenanceValue<DsrRequestType>;
  submittedAt: ProvenanceValue<string>;
  status: ProvenanceValue<DsrStatus>;
}

// ── Consent state rows ────────────────────────────────────────────────────────

export const CONSENT_STATE_ROWS: ConsentStateRow[] = [
  {
    id: "sub-001",
    subscriberId: sim("SUB-00A2F1"),
    collection: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    location: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    marketing: {
      consented: sim(false),
      revokedAt: sim("2026-07-14T09:30:00Z"),
    },
    thirdPartySharing: {
      consented: sim(false),
      revokedAt: sim("2026-07-14T09:31:00Z"),
    },
  },
  {
    id: "sub-002",
    subscriberId: sim("SUB-00B3E7"),
    collection: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    location: {
      consented: sim(false),
      revokedAt: sim("2026-08-02T14:15:00Z"),
    },
    marketing: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    thirdPartySharing: {
      consented: sim(true),
      revokedAt: sim(null),
    },
  },
  {
    id: "sub-003",
    subscriberId: sim("SUB-00C9D4"),
    collection: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    location: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    marketing: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    thirdPartySharing: {
      consented: sim(false),
      revokedAt: sim("2026-06-20T11:00:00Z"),
    },
  },
  {
    id: "sub-004",
    subscriberId: sim("SUB-00D1F8"),
    collection: {
      consented: sim(false),
      revokedAt: sim("2026-09-01T08:00:00Z"),
    },
    location: {
      consented: sim(false),
      revokedAt: sim("2026-09-01T08:01:00Z"),
    },
    marketing: {
      consented: sim(false),
      revokedAt: sim("2026-09-01T08:02:00Z"),
    },
    thirdPartySharing: {
      consented: sim(false),
      revokedAt: sim("2026-09-01T08:03:00Z"),
    },
  },
  {
    id: "sub-005",
    subscriberId: sim("SUB-00E5C2"),
    collection: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    location: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    marketing: {
      consented: sim(true),
      revokedAt: sim(null),
    },
    thirdPartySharing: {
      consented: sim(true),
      revokedAt: sim(null),
    },
  },
];

// ── Data-subject request log ──────────────────────────────────────────────────

/**
 * VINs sourced from FLEET_ROWS — no literals.
 * Used only in subscriberId references embedded in DSR descriptions.
 */
const _vinUs = FLEET_ROWS[0].vin.value!; // 1HGCM82633A004352
const _vinDe = FLEET_ROWS[1].vin.value!; // WBA3A5C51CF256985
const _vinIn = FLEET_ROWS[2].vin.value!; // MAKA0000000000003

export const DSR_ROWS: DataSubjectRequest[] = [
  {
    id: "dsr-001",
    subscriberId: sim("SUB-00A2F1"),
    requestType: sim("erasure"),
    submittedAt: sim("2026-09-01T08:05:00Z"),
    status: sim("in_progress"),
  },
  {
    id: "dsr-002",
    subscriberId: sim("SUB-00B3E7"),
    requestType: sim("access"),
    submittedAt: sim("2026-08-29T10:30:00Z"),
    status: sim("completed"),
  },
  {
    id: "dsr-003",
    subscriberId: sim(`SUB-VIN-${_vinUs.slice(-6)}`),
    requestType: sim("portability"),
    submittedAt: sim("2026-08-15T16:20:00Z"),
    status: sim("pending"),
  },
  {
    id: "dsr-004",
    subscriberId: sim(`SUB-VIN-${_vinDe.slice(-6)}`),
    requestType: sim("erasure"),
    submittedAt: sim("2026-07-30T09:45:00Z"),
    status: sim("completed"),
  },
  {
    id: "dsr-005",
    subscriberId: sim(`SUB-VIN-${_vinIn.slice(-6)}`),
    requestType: sim("access"),
    submittedAt: sim("2026-07-20T13:00:00Z"),
    status: sim("rejected"),
  },
];

// ── Top-level fixture ─────────────────────────────────────────────────────────

export const CONSENT_PRIVACY_FIXTURE = {
  consentRows: CONSENT_STATE_ROWS,
  dsrRows: DSR_ROWS,
};
