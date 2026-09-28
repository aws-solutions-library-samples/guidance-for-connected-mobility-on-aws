// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * auditLog.fixture.ts — simulated data for the Audit Log screen (T6.4).
 *
 * ## One event model, two views
 *
 * The Audit Log and the Command Center activity feed share the SAME event shape:
 * ActivityFeedEvent from commandCenter.fixture.ts. This is load-bearing — a second
 * event model is exactly the drift that already cost this spec one defect.
 *
 * The Audit Log re-exports COMMAND_CENTER_ACTIVITY as its event source. The view
 * imports this module; the guard suite imports from here. Neither imports
 * commandCenter.fixture.ts directly — this module is the single mediation point.
 *
 * ## Actors
 *
 * Activity events do not carry an actor field in the base ActivityFeedEvent shape.
 * The Audit Log adds actor attribution by augmenting events with a wrapper that
 * pairs each ActivityFeedEvent with an actor ProvenanceValue<string>.
 *
 * ## ProvenanceValue contract
 *
 * Every displayed-value field is a ProvenanceValue<T> with provenance: 'simulated'.
 * Structural identity fields (id) are exempt per spec D4.
 *
 * ## No location fields
 *
 * No trip / GPS / driver-identity / vehicle-location field appears anywhere.
 */

import type { ProvenanceValue } from "../../../types";
import {
  COMMAND_CENTER_ACTIVITY,
  type ActivityFeedEvent,
} from "../command-center/commandCenter.fixture";

// ── Helper ────────────────────────────────────────────────────────────────────

function sim<T>(value: T): ProvenanceValue<T> {
  return { value, provenance: "simulated" };
}

// ── Domain types ──────────────────────────────────────────────────────────────

/**
 * AuditLogEntry wraps ActivityFeedEvent (the shared event model) with actor
 * attribution. The base event shape is preserved unchanged — the Audit Log is
 * a view over the same events, with an extra field added here.
 */
export interface AuditLogEntry {
  /** The base event — same shape as Command Center activity feed. */
  event: ActivityFeedEvent;
  /**
   * Actor who performed or triggered the action.
   * ProvenanceValue<string> per fixture-guard requirements (spec D4).
   */
  actor: ProvenanceValue<string>;
}

// ── Actors for each event ─────────────────────────────────────────────────────

const ACTORS: ProvenanceValue<string>[] = [
  sim("ops-portal / system"),
  sim("campaign-approver@example.internal"),
  sim("campaign-approver@example.internal"),
  sim("diagnostics-engine / system"),
  sim("manufacturing-controller / system"),
  sim("manufacturing-controller / system"),
];

// ── Audit log entries ─────────────────────────────────────────────────────────

/**
 * AUDIT_LOG_ENTRIES pairs each ActivityFeedEvent with an actor.
 * Events are in reverse-chronological order — newest first — matching the order
 * of COMMAND_CENTER_ACTIVITY.
 *
 * The event model is NOT duplicated here. Events are imported from
 * commandCenter.fixture.ts via COMMAND_CENTER_ACTIVITY.
 */
export const AUDIT_LOG_ENTRIES: AuditLogEntry[] = COMMAND_CENTER_ACTIVITY.map(
  (event, index) => ({
    event,
    actor: ACTORS[index] ?? sim("system"),
  }),
);

/**
 * Re-export ActivityFeedEvent so consumers of this module have the type
 * available without needing to import commandCenter.fixture.ts themselves.
 */
export type { ActivityFeedEvent };

/** Re-export for test assertions. */
export { COMMAND_CENTER_ACTIVITY };
