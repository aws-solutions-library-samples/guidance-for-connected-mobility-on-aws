// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// VehicleDiagnosticsPanel — IA Redesign (spec 2026-09-24-cms-diagnostics-tab-ia-redesign)
//
// Group 4, Task 4.1: single-writer compose pass.
//
// Structural changes from the previous 2,014-line version:
//   - One page, actions first: DiagnosticsHealthStrip (status, the only scan
//     button, Dispatch) → self-tests (RoutineOffering) → DiagnosticSessionsTable
//     → VehicleDTCsTable → technician detail.
//   - Session detail: SessionDetailView in a Modal over the page. This replaced a
//     list⇄detail view swap after the first UAT (issue
//     2026-09-25-diagnostics-ia-uat-defects).
//   - Command rows are normalised once at the fetch boundary (utils/sovdRow.ts).
//     The health strip's latest scan is derived from them.
//   - DELETED from this file: LiveDataSection, IdentityDriftSection, ECU cards, ECU
//     inventory disclosure, SessionLog.  All now live in SessionDetailView (Task 3.3).
//   - DELETED: Technician details disclosure.  VehicleDTCsTable is now a peer table.
//   - RoutinesSection replaced by RoutineOffering (Task 3.4).
//   - sessionCommandsProp short-circuit in refreshSessionLog REMOVED so DXN2/DXN3
//     tests can assert the fetch path (FG1.2 resolution — option 1 from decisions.md).
//   - The poll is OWNED BY THIS PANEL.  Opening or closing the session modal must
//     NOT stop the poll — this is the load-bearing behaviour guarded by DXN2/DXN3.
//
// Retained from the previous version (moved, not reimplemented):
//   - VerdictBanner (local helper — rendered inside DiagnosticsHealthStrip indirectly)
//   - EcuDiagnosticReasons (rendered in list view above the scan button)
//   - Scan lifecycle (handleRunDiagnosticScan + pollIntervalRef)
//   - handleRoutinePostSuccess (post-invocation outcome poll)
//   - All exported types (SessionCommandEntry, RoutineCatalogEntry, etc.)
//   - HARD GATE H (scan button only when connectionStatus === 'connected' exactly)
//   - STATIONARY gate (moved into RoutineOffering via Task 3.4)
//   - DX49 copy-lint invariant (no HTTP endpoint paths or Python-file references in string literals here)
//
// Props:
//   vehicleId            — required
//   connectionStatus     — optional; HARD GATE H
//   vin                  — optional; required for dispatch
//   callerGroups         — optional; required for dispatch gating
//   catalog              — optional; verdict + health strip
//   latestScanResult     — optional; verdict + health strip
//   ecuDiagnosticResult  — optional; T7.1a per-ECU reasons (rendered in list + passed to detail)
//   liveDataResult       — optional; T7.1a live data (passed to detail view)
//   ecuInventory         — optional; T7.1a ECU inventory (passed to detail view)
//   identityResult       — optional; T7.1a identity/drift (passed to detail view)
//   routines             — optional; routine catalog (test seam / parent override)
//   routinesError        — optional; error from routines endpoint
//   latestRoutineResult  — optional; outcome of latest routine invocation (test seam)
//   activeDtcs           — optional; drives suggested routines chip row
//   sessionCommands      — optional; pre-seeded session commands (test seam)
//   priorSessionCommands — optional; pre-seeded all-session commands (test seam)
//   suggestedRoutinesOverride — optional; override for DTC-suggested routines (test seam)

import React, { useState, useEffect, useRef } from 'react';
import { flushSync } from 'react-dom';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Spinner from '@cloudscape-design/components/spinner';
import Header from '@cloudscape-design/components/header';
import Alert from '@cloudscape-design/components/alert';
import Modal from '@cloudscape-design/components/modal';
import {
  startFullScan,
  fetchLatestSovdCommand,
  fetchRoutines,
  runRoutineInSession,
  fetchSessionCommands,
  fetchAllSovdCommands,
  fetchEventCatalog,
  fetchDtcSuggestedRoutines,
} from '@/utils/sovdScanClient';
import { groupSovdRowsIntoSessions, UNSESSIONED_SESSION_ID } from '@/utils/diagnosticSessions';
import type { DiagnosticSession } from '@/utils/diagnosticSessions';
import { normalizeSovdRow, deriveLatestScan, summarizeScanRow } from '@/utils/sovdRow';
import type { LatestScan, ScanResults } from '@/utils/sovdRow';
import { mergeDispatchDtcs } from '@/utils/dispatchDtcs';
import type { RecordedDtc } from '@/utils/dispatchDtcs';
import VehicleDTCsTable from './VehicleDTCsTable';
import RunRoutineModal from './RunRoutineModal';
import type { RunRoutineAttestation } from './RunRoutineModal';
import DispatchModal from './DispatchModal';
import type { DispatchEvidence } from './DispatchModal';
import { MANIFEST_ECU_LABELS, ECU_OPTIONS } from './ecu_names';
import { rendererFor } from './routine-renderers';
import type { RoutineResponse } from './routine-renderers';
import DiagnosticsHealthStrip from './diagnostics/DiagnosticsHealthStrip';
import DiagnosticSessionsTable from './diagnostics/DiagnosticSessionsTable';
import SessionDetailView from './diagnostics/SessionDetailView';
import RoutineOffering from './diagnostics/RoutineOffering';
import { computeVerdict } from '@/utils/diagnosticsVerdict';

// ── Types ────────────────────────────────────────────────────────────────────

/** Shape of one ECU entry in the SOVD response `components` map. */
interface SovdEcuEntry {
  id?: string;
  dtcs?: Array<{ code: string; freeze_frame?: Record<string, unknown> | null }>;
}

/**
 * T7.1b: PROGRESS payload shape persisted by command_response_handler
 * onto the command row when a PROGRESS status event is received.
 * Sourced from T6.5's `_publish_ecu_progress` sidecar function.
 *
 * ecu_index is 0-based (first ECU is index 0); display as ecu_index + 1.
 * ecu_name is a sidecar vocabulary key (e.g. 'ECU_BATTERY_HV'); resolved
 * to a human-readable label via resolveEcuLabel.
 *
 * This path has NOT been verified against live staging — the panel fails
 * safe by treating an absent progress field as indeterminate (DX47).
 */
interface SovdProgressEntry {
  ecu_index: number;
  ecu_total: number;
  ecu_name: string;
  ecu_status: 'ok' | 'timeout' | 'error';
}

/** Partial shape of the SOVD command result. */
interface SovdCommandResult {
  commandId?: string;
  correlationId?: string;
  status?: string;
  commandType?: string;
  type?: string;
  components?: Record<string, SovdEcuEntry>;
  latency_ms?: number;
  storage_uri?: string | null;
  /** T7.1b: per-ECU progress persisted by command_response_handler. */
  progress?: SovdProgressEntry;
}

/** Card item rendered in ECU cards. */
interface EcuCardItem {
  id: string;
  dtcCount: number;
  hasFreezeFrame: boolean;
}

/** Shape of a catalog entry (received as prop; T3.2 uses it for verdict rendering). */
export interface CatalogEntry {
  event_id?: string;
  category?: string;
  severity?: number;
  severity_hint?: string;
  description?: string;
  dtc_code?: string;
  [key: string]: unknown;
}

/** Shape of a scan result (received as prop; T3.2 uses it for verdict rendering). */
export interface ScanResult {
  vehicleId?: string;
  scannedAt?: string;
  dtcs?: Array<{ code: string; status?: string; ecu?: string }>;
  [key: string]: unknown;
}

/**
 * Per-ECU entry in ecuDiagnosticResult.components.
 * Server-side adjudication supplies the unreachable reason (F22 Decision 2).
 * T7.1a renders it verbatim.
 */
export interface EcuDiagnosticEntry {
  unreachableReason?: string;
}

/**
 * T7.1a ecuDiagnosticResult prop — carries per-ECU resolved diagnostic state.
 * Backend adjudicates the 8-hop resolution chain; frontend renders as given.
 */
export interface EcuDiagnosticResult {
  vehicleId?: string;
  commandType?: string;
  status?: string;
  components?: Record<string, EcuDiagnosticEntry>;
}

/** Per-ECU live data reading (T7.1a liveDataResult prop). */
export interface LiveDataEntry {
  did?: string;
  value?: string | number;
  unit?: string;
}

/** T7.1a liveDataResult prop shape. */
export interface LiveDataResult {
  vehicleId?: string;
  commandType?: string;
  status?: string;
  components?: Record<string, LiveDataEntry>;
}

/** T7.1a ecuInventory item (F22 Decision 2: backend resolves labels and reachability). */
export interface EcuInventoryItem {
  ecuName: string;
  /** Human-readable label; if absent the panel looks up MANIFEST_ECU_LABELS. */
  label?: string;
  /** Whether this ECU is reachable on this vehicle. */
  reachable: boolean;
  /**
   * Whether this ECU is applicable to this vehicle's powertrain (C15).
   * Items with applicable === false are excluded from the inventory entirely.
   */
  applicable?: boolean;
  /** Reason string when reachable === false. Rendered verbatim (F22 Decision 2). */
  unreachableReason?: string;
}

/** Per-ECU version entry for drift rendering (T6.4 RESULT shape). */
export interface EcuVersionEntry {
  actual?: { sw_version?: string; hw_version?: string };
  expected?: { sw_version?: string; hw_version?: string } | null;
  delta?: { sw?: string | null; hw?: string | null } | null;
}

/** T7.1a identityResult prop shape (T6.4 RESULT). */
export interface IdentityResult {
  vehicleId?: string;
  commandType?: string;
  status?: string;
  components?: Record<string, EcuVersionEntry>;
}

/**
 * T9.1 / D26: routine catalog entry.
 *
 * Sourced from the server-side routine catalog module under services/_shared/
 * (namely get_routines_for_profile).  The three safety classes come from D14;
 * SERVICE_ONLY carries a required non-empty `reason` per D17 (D25 for the
 * site-precondition rationale).
 *
 * Field names are camelCase per the Lambda boundary convention (D26 —
 * spec `2026-06-09-cms-api-field-normalization` settled this direction).
 *
 * The frontend does NOT re-derive `safetyClass` — it is authoritative on
 * the server (F28) and rendered as given.  Same discipline as the ECU
 * unreachable-reason surface: server adjudicates, frontend renders.
 *
 * T3.1 (SOVD sessions v1.5): `suggestedForDtc` is set by the ?dtc= endpoint
 * (T2.6) when this routine was flagged as matching the supplied DTC code.
 */
export interface RoutineCatalogEntry {
  routineId: string;
  safetyClass: 'INERT' | 'STATIONARY' | 'SERVICE_ONLY';
  /** D27: Run button gate. Absent means no button (fail-closed, same lesson as F28). */
  invocable?: boolean;
  /**
   * Per-caller invocation gate (spec 2026-09-14-cms-frontend-fleet-persona-alignment).
   * Computed by the commands_lambda module at lines 1226-1233; emitted in every routine
   * catalog response for every caller.  Optional (not required) because rollout order
   * does not guarantee every response includes it — undefined falls back to invocable.
   * false means the server has explicitly denied this caller; the Run button MUST NOT
   * be shown.  See issue 2026-09-12-diagnostics-persona-matrix-not-group-enforced.
   */
  invocableByCaller?: boolean;
  /** D28: server-supplied class-level precondition text, rendered as the group header. */
  precondition?: string;
  /** Required non-empty for SERVICE_ONLY (D17). Empty for INERT/STATIONARY. */
  reason: string;
  /**
   * T3.1 (SOVD sessions v1.5): set by the ?dtc= endpoint to flag this routine
   * as suggested for the active DTC.  Used to render the suggested chips row.
   */
  suggestedForDtc?: boolean;
}

/**
 * The single per-caller invocation predicate for this panel.
 *
 * spec `2026-09-14-cms-frontend-fleet-persona-alignment` T2.2 + F2.1.
 *
 * There are two Run affordances in the Diagnostics tab — the main routine
 * list's Run button and the DTC-suggested chip row. Both now call THIS
 * function so they cannot drift apart structurally.
 *
 * Semantics (D27 fail-closed):
 *   - `invocableByCaller === false` → denied for this caller, even when the
 *     static `invocable` is true.
 *   - `invocableByCaller` absent/undefined/null → fall back to the static
 *     `invocable` field (server-compatibility during rollout).
 *   - Strict `=== true`: any non-boolean that survives the JSON boundary
 *     (`1`, `"true"`, `{}`) is treated as NOT invocable.
 *
 * NOT AN AUTHORIZATION CHECK.
 */
export function mayInvokeForCaller(entry: RoutineCatalogEntry): boolean {
  return (entry.invocableByCaller ?? entry.invocable) === true;
}

/**
 * T9.1: outcome of the most recent routine invocation.
 *
 * `reason` is server-supplied and rendered verbatim (DX56).
 */
export interface RoutineRunResult {
  routineId?: string;
  status?: 'SUCCEEDED' | string;
  /** Verbatim from the server; undefined means "no explanation supplied". */
  reason?: string;
}

/**
 * T3.1 (SOVD sessions v1.5): one command row from the session history endpoint.
 * Shape matches the commands endpoint response row.
 */
export interface SessionCommandEntry {
  commandId?: string;
  commandType?: string;
  type?: string;
  status?: string;
  reason?: string;
  routineId?: string;
  /** ISO timestamp when the command was submitted. */
  submittedAt?: string;
  /** Session correlation ID persisted by T2.4. */
  sessionId?: string;
  /**
   * T2A.1 / T2A.2: response payload persisted by command_response_handler when
   * a terminal-status SOVD sidecar message arrives. Added by the concurrent
   * backend task in the same group. Treat as possibly-absent on every row —
   * older rows predate this field.
   *
   * Shape is the raw JSON from the sidecar, typed as unknown because the schema
   * depends on the routine invoked and is intentionally not parsed here (the
   * routine-contracts spec owns schema-specific rendering).
   *
   * When absent: no expandable drawer is rendered (no caret).
   * When present: a Cloudscape ExpandableSection renders the JSON pretty-printed.
   * When the serialized string exceeds 4 KB: content is truncated and a download
   * affordance is provided instead of dumping the full payload.
   */
  response?: unknown;
}

export interface VehicleDiagnosticsPanelProps {
  vehicleId: string;
  connectionStatus?: string;
  /**
   * T3.2 (SOVD sessions v1.5): vehicle VIN, sourced from the vehicle record.
   * Required for the dispatch payload to DMS; optional so existing callers
   * that don't yet pass it are unaffected (the Dispatch button is suppressed
   * when vin is absent).
   */
  vin?: string;
  /**
   * T3.2 (SOVD sessions v1.5): Cognito groups the current caller belongs to.
   * Used to gate the Dispatch button to fleet-operator and platform-admin.
   * Optional — when absent or empty the Dispatch button is hidden (fail-closed).
   * Sourced from the auth token by the parent; the panel does not call useAuth
   * directly so it remains renderable in test environments without an auth provider.
   */
  callerGroups?: string[];
  catalog?: CatalogEntry[];
  latestScanResult?: ScanResult | null;
  /** T7.1a: per-ECU diagnostic state with 8 named unresolvable-hop reasons. */
  ecuDiagnosticResult?: EcuDiagnosticResult;
  /** T7.1a: live data readings per ECU. */
  liveDataResult?: LiveDataResult;
  /**
   * T7.1a: ECU inventory list.
   * Rendered in the technician section and passed to the session-detail modal.
   */
  ecuInventory?: EcuInventoryItem[];
  /** T7.1a: identity/drift result carrying actual, expected, and delta per ECU. */
  identityResult?: IdentityResult;
  /**
   * T9.1 / D26: routine catalog for this vehicle's powertrain profile (camelCase fields).
   *
   * Server-adjudicated per F28 — the frontend renders as given and never
   * derives `safetyClass` locally.  Absent or empty → the routines section
   * is not rendered at all (DX57): an empty catalog is a valid resolved
   * state, not a placeholder condition.
   */
  routines?: RoutineCatalogEntry[];
  /**
   * T11.5: error from the routines listing endpoint (e.g. unresolvable powertrain).
   * Rendered verbatim in the routines area only; the rest of the Diagnostics tab
   * renders normally (F29 — do not blank the tab on a partial failure).
   */
  routinesError?: string;
  /**
   * T9.1: outcome of the most recent routine invocation.
   *
   * When present, its `reason` renders verbatim (DX56).  The panel does not
   * translate or map — same discipline as ECU unreachable reasons.
   */
  latestRoutineResult?: RoutineRunResult | null;
  /**
   * T3.1 (SOVD sessions v1.5): active DTC codes to drive suggested-routines
   * chip row.  Sourced from the latest scan result or from the parent page.
   * The panel fetches suggestions via the ?dtc= endpoint for the first code.
   * Absent → no suggested routines row rendered.
   */
  activeDtcs?: string[];
  /**
   * FG3: the vehicle's ACTIVE fault record (`GET /vehicles/{id}/dtcs?status=ACTIVE`),
   * which the parent page already loads for its severity badge. Added to the
   * dispatch evidence after the scan's codes, so a code the cloud raised from
   * telemetry (not read from an ECU, so a scan need not return it) still reaches service.
   * Dispatch only; it does not drive the suggested-routines row.
   * Absent (not loaded, or the fetch failed) → dispatch carries scan codes only.
   */
  recordedActiveDtcs?: RecordedDtc[];
  /**
   * T3.1 (SOVD sessions v1.5): pre-seeded session commands for testing.
   * When provided, the panel uses this as the initial session commands state.
   * NOTE: the sessionCommandsProp short-circuit in refreshSessionLog has been
   * REMOVED (FG1.2 resolution — option 1).  The prop is now only used to seed
   * initial state; the poll always runs through the real fetch path.
   */
  sessionCommands?: SessionCommandEntry[];
  /**
   * T3.1 (SOVD sessions v1.5): pre-seeded prior session commands for testing.
   * When provided, the panel uses this as the initial all-commands state.
   * The short-circuit has been REMOVED alongside sessionCommands.
   */
  priorSessionCommands?: SessionCommandEntry[];
  /**
   * T3.1 (SOVD sessions v1.5): pre-seeded suggested routines for testing.
   * When provided, overrides the ?dtc= endpoint fetch. Each entry must have
   * `suggestedForDtc: true` to appear in the chip row.
   */
  suggestedRoutinesOverride?: RoutineCatalogEntry[];
}

// ── Constants ────────────────────────────────────────────────────────────────

const POLL_INTERVAL_MS = 10_000;
const POLL_MAX_MS = 60_000;

// ── Helpers ──────────────────────────────────────────────────────────────────

/**
 * Parse the SOVD components map from the polled command record into a list
 * of ECU card items. Each card carries the ECU id, DTC count, and a flag
 * indicating whether any DTC in that ECU carries a non-empty freeze_frame.
 *
 * Lifted verbatim from VehicleDiagnose.tsx (T3.1: lift, do not rewrite).
 */
function buildEcuCards(components: Record<string, SovdEcuEntry>): EcuCardItem[] {
  return Object.entries(components).map(([ecuId, ecu]) => {
    const dtcs = ecu.dtcs ?? [];
    const hasFreezeFrame = dtcs.some(
      d => d.freeze_frame !== null && d.freeze_frame !== undefined && Object.keys(d.freeze_frame).length > 0,
    );
    return { id: ecuId, dtcCount: dtcs.length, hasFreezeFrame };
  });
}

/**
 * Resolve a human-readable label for an ECU name.
 * Checks MANIFEST_ECU_LABELS (manifest vocabulary) first, then ECU_OPTIONS
 * (sidecar vocabulary). Returns the bare key if neither table has an entry.
 */
function resolveEcuLabel(ecuName: string): string {
  if (ecuName in MANIFEST_ECU_LABELS) {
    return MANIFEST_ECU_LABELS[ecuName];
  }
  const sidecarEntry = ECU_OPTIONS.find(e => e.value === ecuName);
  if (sidecarEntry) {
    return sidecarEntry.label;
  }
  return ecuName;
}

// ── T7.1a: ECU diagnostic reasons section ────────────────────────────────────
//
// Renders a visible section for each component in ecuDiagnosticResult that
// carries an unreachableReason. Server-side adjudication supplies the reason
// string drawn from the 8-hop resolution chain (F22 Decision 2); the frontend
// renders it verbatim. This section is NOT inside an ExpandableSection so the
// reasons are always visible (DX36, DX37, DX50).

function EcuDiagnosticReasons({ result }: { result: EcuDiagnosticResult }) {
  const components = result.components ?? {};
  const entries = Object.entries(components).filter(([, entry]) => !!entry.unreachableReason);

  if (entries.length === 0) return null;

  return (
    <Box margin={{ top: 's' }}>
      {entries.map(([ecuName, entry]) => (
        <Box key={ecuName} margin={{ bottom: 'xs' }}>
          <Box variant="p">
            {`${resolveEcuLabel(ecuName)}: ${entry.unreachableReason}`}
          </Box>
        </Box>
      ))}
    </Box>
  );
}

// ── Suggested routines chip row ───────────────────────────────────────────────
//
// Retained here (not moved to RoutineOffering) because it appears in list view,
// above the sessions table, as a quick-action affordance for the active DTCs.

function SuggestedRoutinesChips({
  suggestions,
  onRunSuggested,
}: {
  suggestions: RoutineCatalogEntry[];
  onRunSuggested: (routine: RoutineCatalogEntry) => void;
}) {
  const inertSuggestions = suggestions.filter(
    r => r.safetyClass === 'INERT' && mayInvokeForCaller(r),
  );
  const nonInertSuggestions = suggestions.filter(
    r => r.safetyClass !== 'INERT' || !mayInvokeForCaller(r),
  );

  if (suggestions.length === 0) return null;

  return (
    <Box margin={{ top: 's', bottom: 's' }} data-testid="suggested-routines-chips">
      <Box variant="p"><strong>Suggested for active fault code</strong></Box>
      <Box margin={{ top: 'xs' }}>
        {inertSuggestions.map(r => (
          <span key={r.routineId} style={{ marginRight: '8px', display: 'inline-block', marginBottom: '4px' }}>
            <Button
              variant="normal"
              onClick={() => onRunSuggested(r)}
              data-testid={`suggested-chip-${r.routineId}`}
            >
              {r.routineId}
            </Button>
          </span>
        ))}
        {nonInertSuggestions.map(r => (
          <span key={r.routineId} style={{ marginRight: '8px', display: 'inline-block', marginBottom: '4px' }}>
            <Button
              variant="normal"
              disabled
              data-testid={`suggested-chip-disabled-${r.routineId}`}
            >
              {r.routineId}
            </Button>
          </span>
        ))}
      </Box>
    </Box>
  );
}

// ── ECU inventory section (list-view accessor) ────────────────────────────
//
// Renders the ECU inventory in list view when the prop is provided.
// Mirrors the lazy-mount pattern from the original panel (F22 Decision 1).
// Gated on ecuInventoryExpanded && !scanning to prevent ECU names appearing
// during scan (DX8/DX40).

function EcuInventorySection({
  ecuInventory,
  scanning,
}: {
  ecuInventory: EcuInventoryItem[];
  scanning: boolean;
}) {
  const [ecuInventoryExpanded, setEcuInventoryExpanded] = useState(true);
  return (
    <Box margin={{ top: 'l' }}>
      <ExpandableSection
        headerText="ECU inventory"
        expanded={ecuInventoryExpanded}
        onChange={({ detail }) => setEcuInventoryExpanded(detail.expanded)}
      >
        {ecuInventoryExpanded && !scanning && (
          <Box>
            {ecuInventory.map(item => {
              const label = item.label ?? resolveEcuLabel(item.ecuName);
              return (
                <Box key={item.ecuName} margin={{ bottom: 'xs' }}>
                  <Box variant="p">
                    {label}
                    {!item.reachable && item.unreachableReason && (
                      <> — {item.unreachableReason}</>
                    )}
                  </Box>
                </Box>
              );
            })}
          </Box>
        )}
      </ExpandableSection>
    </Box>
  );
}

// ── Live data section (list-view proxy) ──────────────────────────────────────
//
// Renders the LiveDataSection in list view when liveDataResult is provided (DX38).
// Mirrors the LiveDataSection from the original panel.

function LiveDataSectionProxy({ result }: { result: LiveDataResult }) {
  const components = result.components ?? {};
  const entries = Object.entries(components);

  return (
    <Box margin={{ top: 's' }}>
      <Header variant="h3">Live data</Header>
      {entries.length === 0 ? (
        <Box variant="p" color="text-status-inactive">No live data available.</Box>
      ) : (
        entries.map(([ecuName, entry]) => (
          <Box key={ecuName} margin={{ bottom: 'xs' }}>
            <Box variant="p">
              {`${resolveEcuLabel(ecuName)}: DID ${entry.did ?? '—'} = ${entry.value ?? '—'}${entry.unit ? ` ${entry.unit}` : ''}`}
            </Box>
          </Box>
        ))
      )}
    </Box>
  );
}

// ── Identity drift section (list-view proxy) ──────────────────────────────────
//
// Renders the IdentityDriftSection in list view when identityResult is provided
// (DX41/DX42). Mirrors the IdentityDriftSection from the original panel.

function IdentityDriftSectionProxy({ result }: { result: IdentityResult }) {
  const components = result.components ?? {};
  const entries = Object.entries(components);

  if (entries.length === 0) return null;

  return (
    <Box margin={{ top: 's' }}>
      <Header variant="h3">Identity and version drift</Header>
      {entries.map(([ecuName, entry]) => {
        const actual = entry.actual;
        const expected = entry.expected;
        const delta = entry.delta;

        const swDrift = delta?.sw;
        const hwDrift = delta?.hw;
        const cannotVerifySw = expected === null || swDrift === 'unknown';
        const cannotVerifyHw = expected === null || hwDrift === 'unknown';

        return (
          <Box key={ecuName} margin={{ bottom: 's' }}>
            <Box variant="p"><strong>{resolveEcuLabel(ecuName)}</strong></Box>
            {actual && (
              <>
                <Box variant="p">{`SW actual: ${actual.sw_version ?? '—'}`}</Box>
                <Box variant="p">
                  {cannotVerifySw
                    ? 'SW expected: cannot verify'
                    : `SW expected: ${expected?.sw_version ?? '—'}`}
                </Box>
                {swDrift && !cannotVerifySw
                  ? <Box variant="p">{`SW delta: ${swDrift}`}</Box>
                  : cannotVerifySw
                    ? <Box variant="p">SW delta: unknown</Box>
                    : <Box variant="p">SW delta: none</Box>
                }
                <Box variant="p">{`HW actual: ${actual.hw_version ?? '—'}`}</Box>
                <Box variant="p">
                  {cannotVerifyHw
                    ? 'HW expected: cannot verify'
                    : `HW expected: ${expected?.hw_version ?? '—'}`}
                </Box>
                {hwDrift && !cannotVerifyHw
                  ? <Box variant="p">{`HW delta: ${hwDrift}`}</Box>
                  : cannotVerifyHw
                    ? <Box variant="p">HW delta: unknown</Box>
                    : <Box variant="p">HW delta: none</Box>
                }
              </>
            )}
          </Box>
        );
      })}
    </Box>
  );
}

// ── Component ────────────────────────────────────────────────────────────────

/**
 * VehicleDiagnosticsPanel
 *
 * Hosts the SOVD Full Scan lifecycle and the session-detail modal.
 *
 * HARD GATE H: the scan button is enabled ONLY when connectionStatus === 'connected'
 * exactly. All other values disable the button with an explanatory message (C1).
 *
 * Poll ownership (spec § Design 4 / DXN2 / DXN3):
 *   This component owns ALL polling — both the scan poll and the post-invocation
 *   routine-outcome poll.  Opening or closing the session modal DOES NOT affect
 *   any poll.  SessionDetailView accepts poll results as props and has no timers.
 *
 * FG1.2 resolution (decisions.md 2026-09-24):
 *   The `sessionCommandsProp !== undefined` short-circuit in `refreshSessionLog`
 *   has been REMOVED.  The props are now used only to seed initial state; the poll
 *   always runs through the real fetch path so DXN2/DXN3 can assert it.
 */
const VehicleDiagnosticsPanel: React.FC<VehicleDiagnosticsPanelProps> = ({
  vehicleId,
  connectionStatus,
  vin,
  callerGroups,
  catalog,
  latestScanResult,
  ecuDiagnosticResult,
  liveDataResult,
  ecuInventory,
  identityResult,
  routines,
  routinesError,
  latestRoutineResult,
  activeDtcs,
  recordedActiveDtcs,
  sessionCommands: sessionCommandsProp,
  priorSessionCommands: priorSessionCommandsProp,
  suggestedRoutinesOverride,
}) => {
  // ── Session-detail modal ──────────────────────────────────────────────────
  //
  // The id of the session open in the detail modal; null when it is closed.
  // Stored as an id and resolved from the live `sessions` list on each render,
  // so the modal follows the poll (status, dispatch stamp) instead of showing a
  // snapshot taken at click time.
  const [selectedSessionId, setSelectedSessionId] = useState<string | null>(null);

  // ── Session notes (operator-authored; passed to SessionDetailView) ─────────
  const [sessionNotes, setSessionNotes] = useState('');

  // ── Scan lifecycle state ──────────────────────────────────────────────────
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [ecuCards, setEcuCards] = useState<EcuCardItem[] | null>(null);
  /**
   * Set when a finished scan's stored result is incomplete ('partial') or
   * missing ('unavailable'); rendered next to the scan result so a missing
   * result is never shown as "no fault codes detected".
   */
  const [scanOutcome, setScanOutcome] = useState<'complete' | 'partial' | 'unavailable' | null>(null);

  /**
   * Internal routines state — fetched on mount via fetchRoutines.
   * The `routines` prop takes precedence (optional test seam per D29);
   * if absent, the panel fetches for itself (DX58).
   */
  const [fetchedRoutines, setFetchedRoutines] = useState<RoutineCatalogEntry[] | undefined>(undefined);

  /**
   * The DTC/event catalog, fetched when the `catalog` prop is absent (the
   * parent never passes one). Without it every scanned DTC read "Undetermined"
   * and no severity verdict could be shown (review FG2, W1).
   */
  const [fetchedCatalog, setFetchedCatalog] = useState<CatalogEntry[] | undefined>(undefined);
  useEffect(() => {
    if (catalog !== undefined) return;
    let cancelled = false;
    const result = fetchEventCatalog();
    if (!result) return;
    result
      .then((res: Response | undefined) => (res && res.ok ? res.json() : undefined))
      .then((data: { events?: CatalogEntry[] } | undefined) => {
        if (!cancelled && data && Array.isArray(data.events)) setFetchedCatalog(data.events);
      })
      .catch(() => { /* no catalog: the strip reports DTC counts without severity */ });
    return () => { cancelled = true; };
  }, [catalog]);
  const effectiveCatalog: CatalogEntry[] | undefined = catalog ?? fetchedCatalog;
  /**
   * D29: internal routines error — owned by the panel, which owns the fetch.
   * The `routinesError` prop is also accepted as an optional override (test seam).
   */
  const [internalRoutinesError, setInternalRoutinesError] = useState<string | undefined>(undefined);
  /**
   * D29 / F31: internal latest routine run result, updated after a
   * post-invocation outcome poll.  The `latestRoutineResult` prop is also
   * accepted as an optional override (DX57/DX63 test seam).
   */
  const [internalLatestRoutineResult, setInternalLatestRoutineResult] = useState<RoutineRunResult | null | undefined>(undefined);

  /**
   * Tracks whether the routine confirmation modal is open.
   * When true, the scan button is hidden from accessibility so
   * the confirm button is the unique action in the DOM (DX60).
   */
  const [routineModalOpen, setRoutineModalOpen] = useState(false);

  /**
   * T7.1b: progress text for the in-flight scan indicator.
   * No progress received → "Scanning in progress…" (DX47 indeterminate state).
   * Progress present → "Scanning ECU N of M: <label>" (DX46).
   * Reset to the indeterminate string on terminal states (SUCCEEDED / FAILED / timeout).
   *
   * FAIL-SAFE: this persistence path has NOT been verified against live
   * staging. With no progress the panel degrades to indeterminate (DX47).
   * ECU labels are written ONLY from stream evidence (F2 / DX8 boundary).
   */
  const [progressText, setProgressText] = useState<string>('Scanning in progress…');

  /**
   * T3.2 (SOVD sessions v1.5): Dispatch modal open state.
   * The Dispatch button is only shown to fleet-operator or platform-admin.
   */
  const [dispatchModalOpen, setDispatchModalOpen] = useState(false);

  /**
   * T3.2: Whether the caller may dispatch. Computed from the auth token's
   * cognito:groups claim.  Fail-closed: absent group list → false.
   */
  const canDispatch = Boolean(
    callerGroups?.includes('fleet-operator') || callerGroups?.includes('platform-admin'),
  );

  /**
   * T3.1 (SOVD sessions v1.5): session correlation ID — minted once on tab mount.
   * Sent in the request body on each command so the backend can correlate all
   * commands in this fleet-operator session. Never changes for the lifetime of
   * this panel mount (navigating away and back mints a new UUID).
   */
  const [sessionId] = useState<string>(() => crypto.randomUUID());

  /**
   * T3.1: DTC-suggested routines fetched from the ?dtc= endpoint (T2.6).
   */
  const [suggestedRoutines, setSuggestedRoutines] = useState<RoutineCatalogEntry[]>([]);

  /** T3.1: current session's command rows, fetched on mount and after each invoke. */
  const [internalSessionCommands, setInternalSessionCommands] = useState<SessionCommandEntry[]>(
    (sessionCommandsProp ?? []).map(normalizeSovdRow),
  );

  /** T3.1: all SOVD commands for this vehicle (used to build the sessions table). */
  const [internalAllCommands, setInternalAllCommands] = useState<SessionCommandEntry[]>(
    (priorSessionCommandsProp ?? []).map(normalizeSovdRow),
  );

  /**
   * Whether the all-commands history (the source of the sessions table and of
   * the health strip's latest scan) has been answered. 'unavailable' only when
   * no fetch has ever succeeded; a later failed refresh keeps the last good data.
   */
  const [commandHistory, setCommandHistory] = useState<'loading' | 'loaded' | 'unavailable'>(
    priorSessionCommandsProp !== undefined ? 'loaded' : 'loading',
  );

  /** T3.1: pending routine from a suggested-chips click (feeds into the run modal). */
  const [pendingSuggestedRoutine, setPendingSuggestedRoutine] = useState<RoutineCatalogEntry | null>(null);

  // Cleanup ref for the polling interval so it is always cleared on unmount or
  // on completion, even if the component re-renders while polling is in flight.
  // Lifted verbatim from VehicleDiagnose.tsx.
  const pollIntervalRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const pollStartRef = useRef<number>(0);
  // The routine-outcome poll has its own timer. It used to share the scan's,
  // and stopping it on a routine result left a scan started just before it
  // spinning forever (review FG2 cycle 2, W1).
  const routinePollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Set after one extra read of a finished scan that had no results yet.
  const scanResultRecheckedRef = useRef(false);
  const routinePollStartRef = useRef<number>(0);

  // Session-refresh interval ref — separate from the scan/routine-outcome poll.
  // Declared here so the cleanup effect below can reference it.
  const sessionRefreshRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    return () => {
      if (pollIntervalRef.current !== null) {
        clearInterval(pollIntervalRef.current);
      }
      if (routinePollRef.current !== null) {
        clearInterval(routinePollRef.current);
      }
      if (sessionRefreshRef.current !== null) {
        clearInterval(sessionRefreshRef.current);
      }
    };
  }, []);

  // Fetch routines on mount (DX58).  D29: the panel owns this transport.
  // The `routines` prop is an optional override (test seam); this fetch
  // populates internal state the section falls back to when the prop is absent.
  // A 400 (unresolvable powertrain) surfaces as internalRoutinesError verbatim;
  // the rest of the Diagnostics tab renders normally (F29).
  useEffect(() => {
    let cancelled = false;
    const result = fetchRoutines(vehicleId);
    if (!result) return; // authFetch returned undefined (stub/mock)
    result
      .then((res: Response | undefined) => {
        if (cancelled || !res) return;
        if (!res.ok) {
          return res.json()
            .then((body: { error?: string }) => {
              if (!cancelled) {
                setInternalRoutinesError(body?.error ?? `HTTP ${res.status}`);
              }
            })
            .catch(() => {
              if (!cancelled) setInternalRoutinesError(`HTTP ${res.status}`);
            });
        }
        return res.json();
      })
      .then((data: { routines?: RoutineCatalogEntry[] } | undefined) => {
        if (!cancelled && data && Array.isArray(data.routines)) {
          setFetchedRoutines(data.routines);
        }
      })
      .catch(() => {
        if (!cancelled) {
          setInternalRoutinesError('Network error fetching routine catalog.');
        }
      });
    return () => { cancelled = true; };
  }, [vehicleId]);

  // T3.1 (SOVD sessions v1.5): Fetch DTC-suggested routines when activeDtcs is
  // provided. Uses only the first DTC code to call the ?dtc= endpoint (T2.6).
  useEffect(() => {
    // Use the prop override when provided (test seam).
    if (suggestedRoutinesOverride !== undefined) {
      setSuggestedRoutines(suggestedRoutinesOverride.filter(r => r.suggestedForDtc === true));
      return;
    }
    if (!activeDtcs || activeDtcs.length === 0) return;
    const dtc = activeDtcs[0];
    let cancelled = false;
    const result = fetchDtcSuggestedRoutines(vehicleId, dtc);
    if (!result) return;
    result
      .then((res: Response | undefined) => {
        if (cancelled || !res || !res.ok) return;
        return res.json();
      })
      .then((data: { routines?: RoutineCatalogEntry[] } | undefined) => {
        if (!cancelled && data && Array.isArray(data.routines)) {
          setSuggestedRoutines(data.routines.filter(r => r.suggestedForDtc === true));
        }
      })
      .catch(() => { /* DTC suggestion failure is silent — the chip row is absent */ });
    return () => { cancelled = true; };
  }, [vehicleId, activeDtcs, suggestedRoutinesOverride]);

  // T3.1 (SOVD sessions v1.5): Fetch session log and all-session history on mount.
  // Re-fetched after each successful routine invocation (see handleRoutinePostSuccess).
  //
  // FG1.2 resolution: the `sessionCommandsProp !== undefined` short-circuit has
  // been REMOVED.  The poll now ALWAYS runs through the real fetch path so that
  // DXN2/DXN3 can assert that fetchSessionCommands is called after navigation.
  // Props are used only to seed initial state (see useState initializers above).
  const refreshSessionLog = () => {
    let cancelledSession = false;
    let cancelledAll = false;

    const sessionResult = fetchSessionCommands(vehicleId, sessionId);
    if (sessionResult) {
      sessionResult
        .then((res: Response | undefined) => {
          if (cancelledSession || !res || !res.ok) return;
          return res.json();
        })
        .then((data: { commands?: SessionCommandEntry[] } | undefined) => {
          if (!cancelledSession && data && Array.isArray(data.commands)) {
            setInternalSessionCommands(data.commands.map(normalizeSovdRow));
          }
        })
        .catch(() => {});
    }

    const allResult = fetchAllSovdCommands(vehicleId);
    if (allResult) {
      allResult
        .then((res: Response | undefined) => {
          if (cancelledAll) return;
          if (!res || !res.ok) {
            setCommandHistory((prev) => (prev === 'loaded' ? prev : 'unavailable'));
            return;
          }
          return res.json();
        })
        .then((data: { commands?: SessionCommandEntry[] } | undefined) => {
          if (!cancelledAll && data && Array.isArray(data.commands)) {
            setInternalAllCommands(data.commands.map(normalizeSovdRow));
            setCommandHistory('loaded');
          }
        })
        .catch(() => {
          if (!cancelledAll) {
            setCommandHistory((prev) => (prev === 'loaded' ? prev : 'unavailable'));
          }
        });
    }
    return () => { cancelledSession = true; cancelledAll = true; };
  };

  // ── Session-refresh polling interval ──────────────────────────────────────
  //
  // Polls refreshSessionLog at POLL_INTERVAL_MS while any session is Active.
  // This keeps the sessions table live without coupling the poll to the
  // routine-invocation outcome poll (which uses pollIntervalRef for scan results).
  //
  // spec § Design 4: "The poll is owned by the panel, not the view.  Navigating
  // back to the list while a routine runs must not abort it."  This is the
  // mechanism — the interval is independent of which view is rendered.
  //
  // DXN2 guard: the interval fires after navigation back to the list, so
  // fetchSessionCommands is called more than once total.  If this interval
  // were owned by SessionDetailView (the wrong implementation the spec predicts),
  // it would stop when the detail view unmounts — DXN2 would fail precisely there.
  useEffect(() => {
    // Load once immediately so the sessions table and the health strip's
    // latest scan are populated when the tab opens, not POLL_INTERVAL_MS later.
    const cancelInitial = refreshSessionLog();

    // Start the session-refresh interval unconditionally.  The interval calls
    // refreshSessionLog which is lightweight (two fetches, both fire-and-forget).
    sessionRefreshRef.current = setInterval(() => {
      refreshSessionLog();
    }, POLL_INTERVAL_MS);

    return () => {
      cancelInitial();
      if (sessionRefreshRef.current !== null) {
        clearInterval(sessionRefreshRef.current);
        sessionRefreshRef.current = null;
      }
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleId, sessionId]);

  // HARD GATE H: allow-list — only the exact string 'connected' enables the scan.
  const isConnected = connectionStatus === 'connected';

  const stopPolling = () => {
    if (pollIntervalRef.current !== null) {
      clearInterval(pollIntervalRef.current);
      pollIntervalRef.current = null;
    }
  };

  /**
   * D29 / F31: post-invocation outcome poll.
   *
   * Called by RoutineOffering → RoutinesSection (via onPostSuccess callback) after a
   * successful (200) POST to runRoutine.  Reuses the scan flow's single
   * pollIntervalRef — no third independent timer.
   *
   * POLL OWNERSHIP NOTE: this function lives in the panel, not in SessionDetailView.
   * Navigating to detail or back to list does NOT cancel this poll — it uses a ref
   * that is independent of view state.  This satisfies spec § Design 4 + DXN2/DXN3.
   */
  const stopRoutinePolling = () => {
    if (routinePollRef.current !== null) {
      clearInterval(routinePollRef.current);
      routinePollRef.current = null;
    }
  };

  const handleRoutinePostSuccess = () => {
    // Uses its own timer (routinePollRef). The scan poll, if one is running,
    // is left alone.
    stopRoutinePolling();
    routinePollStartRef.current = Date.now();

    const readAndUpdate = async (): Promise<boolean> => {
      try {
        const getRes = await fetchLatestSovdCommand(vehicleId);
        if (!getRes || !getRes.ok) return false;

        const data: { commands?: Array<{ commandType?: string; type?: string; status?: string; reason?: string; routineId?: string; response?: { reason?: string } }> } = await getRes.json();
        const latest = data.commands?.[0];
        if (!latest) return false;

        const commandType = latest.commandType ?? latest.type ?? '';
        if (commandType !== 'run_routine') return false;

        const status = latest.status ?? '';
        const inProgress = status === 'IN_PROGRESS' || status === 'SENT' || status === '';
        if (!inProgress) {
          setInternalLatestRoutineResult({
            routineId: latest.routineId,
            status: latest.status,
            reason: latest.reason ?? latest.response?.reason,
          });
          // T3.1: refresh session log so the newly-completed command appears.
          refreshSessionLog();
          return true;
        }
        return false;
      } catch {
        return false;
      }
    };

    readAndUpdate().then(done => {
      if (done) return;
      routinePollRef.current = setInterval(async () => {
        const elapsed = Date.now() - routinePollStartRef.current;
        if (elapsed > POLL_MAX_MS) {
          stopRoutinePolling();
          return;
        }
        const done = await readAndUpdate();
        if (done) stopRoutinePolling();
      }, POLL_INTERVAL_MS);
    }).catch(() => { /* ignore */ });
  };

  const handleRunDiagnosticScan = async () => {
    if (!vehicleId) return;
    flushSync(() => {
      setScanning(true);
      setError(null);
      setEcuCards(null);
      setScanOutcome(null);
    });
    scanResultRecheckedRef.current = false;

    try {
      const postRes = await startFullScan(vehicleId);

      if (!postRes || !postRes.ok) {
        const status = postRes?.status ?? 0;
        let text = '';
        try { text = (await postRes?.text()) ?? ''; } catch { /* ignore */ }
        setError(`Failed to start diagnostic scan (HTTP ${status}): ${text.slice(0, 200)}`);
        setScanning(false);
        return;
      }

      // The POST returns the new command's id. Poll rows are matched to it so a
      // newer command (a self-test run while the scan is in flight) is never
      // read as this scan's result.
      let scanCommandId: string | undefined;
      try {
        const body = await postRes.clone().json();
        const id = body?.commandId ?? body?.correlationId;
        scanCommandId = typeof id === 'string' && id !== '' ? id : undefined;
      } catch { /* body unreadable — fall back to matching by command type */ }
      const isThisScan = (row: SovdCommandResult): boolean => {
        const id = row.commandId ?? row.correlationId;
        if (scanCommandId !== undefined && id !== undefined) return id === scanCommandId;
        return row.commandType === undefined || row.commandType === 'read_dtcs';
      };

      pollStartRef.current = Date.now();

      pollIntervalRef.current = setInterval(async () => {
        const elapsed = Date.now() - pollStartRef.current;
        if (elapsed > POLL_MAX_MS) {
          stopPolling();
          setError('Diagnostic scan timed out (60 s). The vehicle may be offline or unresponsive.');
          setProgressText('Scanning in progress…');
          setScanning(false);
          return;
        }

        try {
          const getRes = await fetchLatestSovdCommand(vehicleId);
          if (!getRes || !getRes.ok) return;

          const data: { commands?: SovdCommandResult[] } = await getRes.json();
          let latest = data.commands?.[0];

          if (!latest) return;

          if (!isThisScan(latest)) {
            // Something newer than the scan is at the head of the list. Find
            // the scan itself in the vehicle's recent history instead.
            const allRes = await fetchAllSovdCommands(vehicleId);
            if (!allRes || !allRes.ok) return;
            const allData: { commands?: SovdCommandResult[] } = await allRes.json();
            latest = allData.commands?.find(isThisScan);
            if (!latest) return;
          }

          if (latest.progress) {
            const p = latest.progress;
            setProgressText(
              `Scanning ECU ${p.ecu_index + 1} of ${p.ecu_total}: ${resolveEcuLabel(p.ecu_name)}`,
            );
          }

          if (latest.status === 'SUCCEEDED' || latest.status === 'PARTIAL') {
            // summarizeScanRow is the same classifier the health strip uses
            // (utils/sovdRow.ts): results come from response.components (the
            // top-level `components` is the request list), and only ECU entries
            // carrying a `dtcs` list count as answered. A row whose result was
            // offloaded or dropped has none and must not read as "no fault codes
            // detected" (issue 2026-09-25-diagnostics-ia-uat-defects).
            const summary = summarizeScanRow(latest);
            // The response handler writes `status` and `response` in two
            // updates, so a tick can land in between. Read once more before
            // concluding a result is missing (review FG2 cycle 2, S3).
            if (summary.results === 'unavailable' && !summary.offloaded && !scanResultRecheckedRef.current) {
              scanResultRecheckedRef.current = true;
              return;
            }
            stopPolling();
            if (summary.results === 'unavailable') {
              setEcuCards(null);
            } else {
              setEcuCards(buildEcuCards(summary.answered as Record<string, SovdEcuEntry>));
            }
            setScanOutcome(summary.results);
            setProgressText('Scanning in progress…');
            setScanning(false);
            // Re-read history so the health strip's latest scan updates.
            refreshSessionLog();
          } else if (latest.status === 'FAILED' || latest.status === 'RATE_LIMITED') {
            stopPolling();
            setError(`Diagnostic scan ${latest.status.toLowerCase().replace('_', ' ')}.`);
            setProgressText('Scanning in progress…');
            setScanning(false);
          }
        } catch {
          // Network error on poll — keep polling until timeout
        }
      }, POLL_INTERVAL_MS);
    } catch (err: unknown) {
      const message = (err instanceof Error) ? err.message : 'Unexpected error starting scan.';
      setError(message);
      setScanning(false);
    }
  };

  // ── Derived session data for the sessions table ───────────────────────────
  //
  // Build sessions from internal all-commands + current session commands.
  // The sessions table uses groupSovdRowsIntoSessions from Task 2.2.
  // internalAllCommands is refreshed by refreshSessionLog after each invoke.
  // We also include internalSessionCommands (current session) so that an in-flight
  // session that is not yet in allCommands still appears in the table.
  // groupSovdRowsIntoSessions deduplicates by commandId; merging both lists and
  // deduplicating here avoids double-counting.
  const allCommandsForSessions: SessionCommandEntry[] = (() => {
    const seen = new Set<string>();
    const merged: SessionCommandEntry[] = [];
    for (const cmd of [...internalAllCommands, ...internalSessionCommands]) {
      const key = cmd.commandId ?? JSON.stringify(cmd);
      if (!seen.has(key)) {
        seen.add(key);
        merged.push(cmd);
      }
    }
    return merged;
  })();
  // Convert SessionCommandEntry to SovdCommandRow for the grouping helper.
  // Rows are normalised at the fetch boundary, so `session_id` is already
  // filled from whichever key the source used. Do not overwrite it from
  // `sessionId` here: live API rows carry only `session_id`, and overwriting it
  // put every run into one unsessioned bucket (issue 2026-09-25-diagnostics-ia-uat-defects).
  const allCommandsAsRows = allCommandsForSessions.map(cmd => ({
    ...cmd,
    // Cast response to the SovdCommandRow.response shape (unknown → object)
    response: cmd.response as { verdict?: string; [key: string]: unknown } | undefined,
  }));
  const sessions: DiagnosticSession[] = groupSovdRowsIntoSessions(allCommandsAsRows);

  const selectedSession: DiagnosticSession | null =
    selectedSessionId === null
      ? null
      : sessions.find((sess) => sess.sessionId === selectedSessionId) ?? null;
  const setSelectedSession = (sess: DiagnosticSession | null) =>
    setSelectedSessionId(sess === null ? null : sess.sessionId);

  /** Rows belonging to one session, for the session-detail modal. */
  const commandsForSession = (id: string): SessionCommandEntry[] =>
    allCommandsForSessions.filter((cmd) =>
      id === UNSESSIONED_SESSION_ID ? !cmd.sessionId : cmd.sessionId === id,
    );

  // ── Dispatch evidence builder ─────────────────────────────────────────────
  // DTCs come from the `activeDtcs` prop when a caller supplies it, else from
  // the latest full scan. The parent supplies neither `activeDtcs` nor
  // `latestScanResult`, so without the fallback every dispatch carried no DTCs.
  // The vehicle's recorded ACTIVE codes are appended after them (FG3): a code
  // the cloud raised from telemetry is not read from an ECU, so a scan need not return it.
  // Notes are the operator's session notes; they were typed into the session
  // view but never sent (issue 2026-09-25-diagnostics-ia-uat-defects).
  const scanDtcCodesForDispatch = (() => {
    const scan = latestScanResult !== undefined
      ? latestScanResult
      : commandHistory === 'loaded' ? deriveLatestScan(allCommandsForSessions) : null;
    return scan ? [...new Set((scan.dtcs ?? []).map((d) => d.code))] : [];
  })();
  const trimmedNotes = sessionNotes.trim();
  const dispatchEvidence: DispatchEvidence = {
    sessionId,
    notes: trimmedNotes ? [{ text: trimmedNotes }] : [],
    dtcs: mergeDispatchDtcs(activeDtcs ?? scanDtcCodesForDispatch, recordedActiveDtcs),
    routinesRun: internalSessionCommands
      .filter((cmd) => cmd.routineId || cmd.commandType === 'run_routine')
      .map((cmd) => ({
        routineId: cmd.routineId ?? cmd.commandType ?? '',
        status: cmd.status,
        submittedAt: cmd.submittedAt,
      })),
  };

  // ── Routines resolved from prop or internal fetch ────────────────────────
  const displayRoutines = routines ?? fetchedRoutines;
  const displayRoutinesError = routinesError ?? internalRoutinesError;
  const displayLatestRoutineResult = latestRoutineResult ?? internalLatestRoutineResult;

  // Session detail reads the SELECTED session's rows (commandsForSession), not
  // the current mount's session: the old view passed the current session's rows
  // for whichever session was opened (issue 2026-09-25-diagnostics-ia-uat-defects).

  // ── RoutineOffering modal state ───────────────────────────────────────────
  const [pendingRoutine, setPendingRoutine] = useState<RoutineCatalogEntry | null>(null);
  const [invokeError, setInvokeError] = useState<{ error?: string; reason?: string } | null>(null);
  const [runningRoutineId, setRunningRoutineId] = useState<string | null>(null);

  useEffect(() => {
    if (runningRoutineId && displayLatestRoutineResult?.status) {
      setRunningRoutineId(null);
    }
  }, [runningRoutineId, displayLatestRoutineResult]);

  useEffect(() => {
    if (!runningRoutineId) return;
    const timeoutId = setTimeout(() => {
      setRunningRoutineId(null);
      setInvokeError({
        error: `Timed out after ${Math.round(POLL_MAX_MS / 1000)} s waiting for the vehicle to respond. `
          + 'The command was accepted by the platform but the vehicle did not report a terminal '
          + 'status within the window. It may be offline, or the sidecar may not recognise this '
          + 'routine.',
      });
    }, POLL_MAX_MS);
    return () => clearTimeout(timeoutId);
  }, [runningRoutineId]);

  const handleRunClick = (entry: RoutineCatalogEntry) => {
    setInvokeError(null);
    setPendingRoutine(entry);
    setRoutineModalOpen(true);
  };

  const handleModalDismiss = () => {
    setPendingRoutine(null);
    setRoutineModalOpen(false);
  };

  const handleModalConfirm = async (attestation: RunRoutineAttestation) => {
    if (!pendingRoutine) return;
    const routineId = pendingRoutine.routineId;
    setPendingRoutine(null);
    setRoutineModalOpen(false);
    // Clear the previous run's outcome first. Otherwise the effect that clears
    // "Running…" when a result is present fires at once on the OLD result, and
    // the previous "completed successfully" stays on screen for this run
    // (review FG2 cycle 2, C1).
    setInternalLatestRoutineResult(undefined);
    setRunningRoutineId(routineId);
    setInvokeError(null);

    try {
      // Task 2.3: `runRoutineInSession` returns `RoutineInvocationOutcome`.
      // Handle 'ok', 'RateLimited', and undefined (stub/mock) cases.
      const outcome = await runRoutineInSession(vehicleId, routineId, sessionId, attestation);

      if (!outcome) {
        // Stub / mock returned undefined — treat as network failure.
        setInvokeError({ error: 'Network error contacting the command service.' });
        setRunningRoutineId(null);
        return;
      }

      // Task 2.3: handle the RoutineInvocationOutcome shape.
      // The type is { kind: 'ok', response: Response } | { kind: 'RateLimited', attempts: number }
      // but legacy mocks may still return a raw Response-shaped object.
      // We support both by checking for .kind first (new), then falling back to .ok (legacy).
      const outcomeAny = outcome as unknown as Record<string, unknown>;
      const isNewShape = typeof outcomeAny.kind === 'string';

      if (isNewShape) {
        if (outcomeAny.kind === 'RateLimited') {
          const attempts = (outcomeAny.attempts as number) ?? 1;
          setInvokeError({
            error: `Rate limited: the vehicle refused the command after ${attempts} attempt(s). Try again in a moment.`,
          });
          setRunningRoutineId(null);
          return;
        }
        if (outcomeAny.kind === 'network_error') {
          setInvokeError({ error: 'Network error contacting the command service.' });
          setRunningRoutineId(null);
          return;
        }
        if (outcomeAny.kind === 'error' && typeof outcomeAny.status === 'string') {
          // The vehicle answered with a terminal non-success (FAILED, a
          // refusal). The POST itself succeeded, so its body says nothing
          // useful: the outcome is the row's status and reason.
          setInternalLatestRoutineResult({
            routineId,
            status: outcomeAny.status,
            reason: typeof outcomeAny.reason === 'string' ? outcomeAny.reason : undefined,
          });
          setRunningRoutineId(null);
          refreshSessionLog();
          return;
        }
        if (outcomeAny.kind === 'error') {
          // Synchronous HTTP failure (non-2xx); read body for error/reason.
          const response = outcomeAny.response as Response;
          let body: { error?: string; reason?: string } = {};
          try { body = await response.json(); } catch { /* ignore parse error */ }
          setInvokeError(body);
          setRunningRoutineId(null);
          return;
        }
        // kind === 'ok'
        const response = outcomeAny.response as Response;
        if (outcomeAny.status === 'SUCCEEDED' && response?.ok) {
          // The client already saw the terminal row; no second poll needed.
          setInternalLatestRoutineResult({ routineId, status: 'SUCCEEDED' });
          setRunningRoutineId(null);
          refreshSessionLog();
          return;
        }
        if (!response || !response.ok) {
          let body: { error?: string; reason?: string } = {};
          try { body = await response.json(); } catch { /* ignore parse error */ }
          setInvokeError(body);
          setRunningRoutineId(null);
        } else {
          handleRoutinePostSuccess();
        }
      } else {
        // Legacy shape: raw Response (for backward compat with older tests/mocks)
        const res = outcome as unknown as Response;
        if (!res.ok) {
          let body: { error?: string; reason?: string } = {};
          try { body = await res.json(); } catch { /* ignore parse error */ }
          setInvokeError(body);
          setRunningRoutineId(null);
        } else {
          handleRoutinePostSuccess();
        }
      }
    } catch {
      setInvokeError({ error: 'Network error contacting the command service.' });
      setRunningRoutineId(null);
    }
  };

  // T3.1: When a chip from the suggested-routines row is clicked, open the modal.
  useEffect(() => {
    if (pendingSuggestedRoutine && !pendingRoutine) {
      setInvokeError(null);
      setPendingRoutine(pendingSuggestedRoutine);
      setRoutineModalOpen(true);
      setPendingSuggestedRoutine(null);
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pendingSuggestedRoutine]);

  // ── DTC section expansion state ────────────────────────────────────────────
  //
  // The DTC table is now a peer table (spec § Design 3). But VehicleDTCsTable calls
  // useAuth — so to prevent auth context errors in tests that render the panel
  // without a full auth provider (DX1/DX4/DX5 and similar), we keep the same
  // lazy-mount pattern as the original: mount the table only when the section is
  // expanded. The ExpandableSection starts expanded by default so production users
  // see it immediately. Tests that click to expand will also need auth, but tests
  // that only check verdict/scan behavior do not expand the section and don't crash.
  const [dtcSectionExpanded, setDtcSectionExpanded] = useState(true);
  const applicableInventory = (ecuInventory ?? []).filter(
    item => item.applicable !== false,
  );

  // ── DTC-derived fields for the health strip ───────────────────────────────
  //
  // The `latestScanResult` prop (a test seam) wins when supplied. Otherwise the
  // latest completed scan is read from the command history this panel already
  // loads. The parent never passes the prop, so before this the strip had no
  // scan data at all, and a null-only check turned `undefined` into "scanned,
  // zero DTCs" and printed "No issues found" (issue 2026-09-25-diagnostics-ia-uat-defects).
  const scanHistory: 'loading' | 'unavailable' | 'loaded' =
    latestScanResult !== undefined ? 'loaded' : commandHistory;
  const effectiveScan: ScanResult | LatestScan | null | undefined =
    latestScanResult !== undefined
      ? latestScanResult
      : commandHistory === 'loaded'
        ? deriveLatestScan(allCommandsForSessions)
        : undefined;
  const hasScan = effectiveScan !== null && effectiveScan !== undefined;
  const latestDtcs = hasScan ? (effectiveScan.dtcs ?? []) : [];
  const openFaultCount = hasScan ? latestDtcs.length : undefined;
  const highestSeverity: string | undefined = (() => {
    if (!effectiveCatalog || !hasScan || latestDtcs.length === 0) return undefined;
    const verdict = computeVerdict(latestDtcs, effectiveCatalog);
    if (verdict.tier === 'Healthy' || verdict.tier === 'Unrecognised') return undefined;
    return verdict.tier;
  })();
  const lastScanAt: string | undefined = hasScan ? (effectiveScan.scannedAt ?? undefined) : undefined;
  // Completeness of the stored result (utils/sovdRow.ts). A scan passed in via
  // the latestScanResult prop carries no such field and is taken as complete.
  const derivedScan = hasScan && typeof (effectiveScan as { results?: unknown }).results === 'string'
    ? (effectiveScan as LatestScan)
    : undefined;
  const scanResults: ScanResults = derivedScan?.results ?? 'complete';
  const ecusAnswered: number | undefined = derivedScan?.ecusAnswered;
  const scanResultsOffloaded = derivedScan?.offloaded ?? false;
  // dtcs stays undefined unless a scan exists, so the strip cannot compute a
  // Healthy verdict from an empty placeholder list (DX5 guard).
  const dtcReadings = hasScan
    ? latestDtcs.map(d => ({ code: d.code, status: d.status, ecu: d.ecu }))
    : undefined;

  // ── Render ────────────────────────────────────────────────────────────────
  //
  // Order (issue 2026-09-25-diagnostics-ia-uat-defects): everything the operator
  // can DO comes first — status + scan/dispatch, then self-tests — and history
  // (sessions, DTCs, technician detail) follows. Session detail opens in a modal
  // over this page rather than replacing it, so the actions and the list stay
  // put and a running routine keeps its place on screen.

  const sessionModalTitle = (() => {
    if (!selectedSession) return 'Diagnostic session';
    if (selectedSession.sessionId === UNSESSIONED_SESSION_ID) return 'Earlier activity (no session)';
    const started = selectedSession.startedAt ? new Date(selectedSession.startedAt) : null;
    return started && !isNaN(started.getTime())
      ? `Diagnostic session — started ${started.toLocaleString()}`
      : 'Diagnostic session';
  })();

  return (
    <Box>
      <Header variant="h2">Diagnostic Triage</Header>

      {/* T7.1a: ECU diagnostic reason strings — 8 named unresolvable states.
          Always-visible (not inside an expandable); backend adjudicates, frontend
          renders verbatim (F22 Decision 2). DX36, DX37, DX50. */}
      {ecuDiagnosticResult && (
        <EcuDiagnosticReasons result={ecuDiagnosticResult} />
      )}

      {/* ── 1. Status and actions ────────────────────────────────────────────
          Connection, fault count, last scan, recommended action, and the only
          scan button plus Dispatch. Spec § Design 1 + Task 3.1.
          aria-hidden when the routine modal is open so its confirm button is
          the unique action in the DOM (DX60).
          T3.2: Dispatch to service button lives inside DiagnosticsHealthStrip,
          with data-testid="dispatch-to-service-button" and DispatchModal unchanged. */}
      <div aria-hidden={routineModalOpen || undefined}>
        <DiagnosticsHealthStrip
          connectionStatus={connectionStatus}
          openFaultCount={openFaultCount}
          highestSeverity={highestSeverity}
          lastScanAt={lastScanAt}
          dtcs={dtcReadings}
          catalog={effectiveCatalog}
          onRunScan={handleRunDiagnosticScan}
          scanning={scanning}
          scanHistory={scanHistory}
          scanResults={scanResults}
          ecusAnswered={ecusAnswered}
          scanResultsOffloaded={scanResultsOffloaded}
          vin={vin}
          callerGroups={callerGroups}
          vehicleId={vehicleId}
          dispatchEvidence={dispatchEvidence}
          onDispatchSuccess={(_roId) => {
            // Refresh session log after dispatch so the dispatched session
            // shows its RO link in the sessions table.
            refreshSessionLog();
          }}
        />
      </div>

      {/* HARD GATE H: disabled reason shown as inline callout (C1) */}
      {!isConnected && (
        <Box margin={{ bottom: 's' }}>
          <Box variant="p" color="text-status-inactive">
            Vehicle is not connected. The scan action is only available when the vehicle is online.
          </Box>
        </Box>
      )}

      {/* Scan in-progress indicator (DX46, DX47, DX8) */}
      {scanning && (
        <Box margin={{ top: 's' }}>
          <Spinner size="normal" data-testid="scan-spinner" />
          {/*
            T7.1b: progress text rendered via React state.
            Default "Scanning in progress…" = indeterminate state (DX47).
            When the poll returns a progress object, state is updated to
            "Scanning ECU N of M: <label>" (DX46).
            Reset to indeterminate on terminal states.
            The bare sidecar key (e.g. ECU_BATTERY_HV) is never placed in the
            DOM — only the resolved label from resolveEcuLabel (DX8 / F2).
          */}
          <span>{progressText}</span>
        </Box>
      )}

      {error && (
        <Box margin={{ top: 'xs' }} color="text-status-error">
          {error}
        </Box>
      )}

      {/* Scan result ECU cards — the result of the scan action just above.
          From the current scan poll (ecuCards state); DX8 guards (scan in-flight
          honesty) assert they appear only after completion. Also passed to the
          session-detail modal. */}
      {scanOutcome === 'unavailable' && !scanning && (
        <Box margin={{ top: 'm' }} data-testid="scan-results-unavailable">
          <Box variant="p" color="text-status-warning">
            The scan finished, but its results aren't available here. Run a new scan to check for fault codes.
          </Box>
        </Box>
      )}
      {scanOutcome === 'partial' && !scanning && (
        <Box margin={{ top: 'm' }} data-testid="scan-results-partial">
          <Box variant="p" color="text-status-warning">
            Some ECUs didn't answer this scan, so the result below is incomplete.
          </Box>
        </Box>
      )}
      {ecuCards !== null && !scanning && (
        <Box margin={{ top: 'm' }}>
          {scanOutcome === 'complete' && ecuCards.every(c => c.dtcCount === 0) ? (
            <Box variant="p" color="text-status-success">
              {`Scan completed — no fault codes detected in ${ecuCards.length} ECU${ecuCards.length !== 1 ? 's' : ''}.`}
            </Box>
          ) : (
            <Box data-testid="ecu-cards">
              {ecuCards.map(card => (
                <Box key={card.id} margin={{ bottom: 'xs' }}>
                  <Box variant="p">
                    <strong>{card.id}</strong>: {card.dtcCount} DTC(s)
                    {card.hasFreezeFrame && ' [freeze frame]'}
                  </Box>
                </Box>
              ))}
            </Box>
          )}
        </Box>
      )}

      {/* ── 2. Self-tests ───────────────────────────────────────────────────── */}
      {suggestedRoutines.length > 0 && (
        <SuggestedRoutinesChips
          suggestions={suggestedRoutines}
          onRunSuggested={(routine) => {
            setPendingSuggestedRoutine(routine);
          }}
        />
      )}

      {/* Routine offering (spec § Design 5 / Task 3.4). The STATIONARY gate,
          disabled testids, and visible static text live inside RoutineOffering. */}
      {displayRoutinesError && !displayRoutines?.length && (
        <Box margin={{ top: 's', bottom: 's' }}>
          <Alert type="warning" header="Routine catalog unavailable">
            {displayRoutinesError}
          </Alert>
        </Box>
      )}
      {displayRoutines && displayRoutines.length > 0 && (
        <Box margin={{ top: 'l' }}>
          {/* F33: "Running <routineId>…" indicator */}
          {runningRoutineId && (
            <Box margin={{ top: 'xs', bottom: 's' }} color="text-status-info" data-testid="routine-running-indicator">
              <Spinner /> <Box variant="span">Running {runningRoutineId}… waiting for vehicle response.</Box>
            </Box>
          )}
          {/* F30: refusal reason */}
          {!runningRoutineId
            && displayLatestRoutineResult
            && displayLatestRoutineResult.status !== 'SUCCEEDED'
            && displayLatestRoutineResult.reason
            && (
              <Box margin={{ top: 'xs', bottom: 's' }} color="text-status-warning">
                <Box variant="p">{displayLatestRoutineResult.reason}</Box>
              </Box>
            )
          }
          {/* F33: success */}
          {!runningRoutineId
            && displayLatestRoutineResult
            && displayLatestRoutineResult.status === 'SUCCEEDED'
            && (
              <Box margin={{ top: 'xs', bottom: 's' }} color="text-status-success" data-testid="routine-success">
                <Box variant="p">
                  <strong>{displayLatestRoutineResult.routineId ?? 'Routine'}</strong> completed successfully.
                </Box>
              </Box>
            )
          }
          {/* DX64: invoke error */}
          {invokeError && (invokeError.error || invokeError.reason) && (
            <Box margin={{ top: 'xs', bottom: 's' }} color="text-status-error">
              {invokeError.error && <Box variant="p">{invokeError.error}</Box>}
              {invokeError.reason && <Box variant="p">{invokeError.reason}</Box>}
            </Box>
          )}
          <RoutineOffering
            routines={displayRoutines.filter(
              // D17 / DX53: SERVICE_ONLY entries without a reason are filtered out
              // at the render layer. The server already rejects them at construction
              // time, but if one leaks through the panel must not amplify it.
              e => !(e.safetyClass === 'SERVICE_ONLY' && (!e.reason || e.reason.trim() === ''))
            )}
            onRunClick={handleRunClick}
            runInFlight={runningRoutineId !== null || routineModalOpen}
          />
        </Box>
      )}

      {/* ── 3. Diagnostic sessions (spec § Design 2 / Task 3.2) ─────────────── */}
      <Box margin={{ top: 'l' }}>
        <DiagnosticSessionsTable
          sessions={sessions}
          onSessionSelect={(session) => setSelectedSession(session)}
        />
      </Box>

      {/* ── 4. DTC history (spec § Design 3) ───────────────────────────────────
          VehicleDTCsTable is unmodified (standing rule 1). Expanded by default;
          lazy-mounted (content gated on dtcSectionExpanded) so tests that render
          without a full auth provider do not crash. */}
      <Box margin={{ top: 'l' }}>
        <ExpandableSection
          headerText="Diagnostic Trouble Codes"
          expanded={dtcSectionExpanded}
          onChange={({ detail }) => setDtcSectionExpanded(detail.expanded)}
        >
          {dtcSectionExpanded && (
            <VehicleDTCsTable
              vehicleId={vehicleId}
              connectionStatus={connectionStatus}
            />
          )}
        </ExpandableSection>
      </Box>

      {/* ── 5. Technician detail ───────────────────────────────────────────────
          ECU inventory: lazy-mounted per F22 Decision 1 (gated on
          ecuInventoryExpanded && !scanning) so ECU names never appear during a
          scan (DX8/DX40). Live data: DX38. Identity drift: DX41/DX42. */}
      {applicableInventory.length > 0 && (
        <EcuInventorySection
          ecuInventory={applicableInventory}
          scanning={scanning}
        />
      )}
      {liveDataResult && (
        <LiveDataSectionProxy result={liveDataResult} />
      )}
      {identityResult && (
        <IdentityDriftSectionProxy result={identityResult} />
      )}

      {/* ── Session detail modal ───────────────────────────────────────────────
          Replaces the list⇄detail view swap (spec Q7, reversed by the operator
          after the first UAT). SessionDetailView still accepts poll data as
          props and DOES NOT OWN THE POLL — the panel's session refresh keeps
          running while the modal is open or closed (spec § Design 4, DXN2/DXN3).
          Mounted only while a session is open: a Cloudscape Modal keeps a
          dialog element in the DOM even when hidden, which would give the page
          two dialogs whenever the routine attestation modal opens.
          Scan cards, ECU reasons, live data, identity and the notes box belong
          to THIS visit, so they are passed only for the current session. An
          older session shows its own command rows and nothing from today. */}
      {selectedSession !== null && (() => {
        const isCurrent = selectedSession.sessionId === sessionId;
        return (
          <Modal
            visible
            onDismiss={() => setSelectedSession(null)}
            size="large"
            header={sessionModalTitle}
            footer={
              <Box float="right">
                <Button
                  variant="primary"
                  onClick={() => setSelectedSession(null)}
                  data-testid="close-session-detail"
                >
                  Close
                </Button>
              </Box>
            }
          >
            <Box data-testid="session-detail-view">
              <SessionDetailView
                sessionId={selectedSession.sessionId}
                sessionCommands={commandsForSession(selectedSession.sessionId)}
                ecuDiagnosticResult={isCurrent ? ecuDiagnosticResult : undefined}
                ecuCards={isCurrent ? ecuCards ?? undefined : undefined}
                ecuInventory={isCurrent && applicableInventory.length > 0 ? applicableInventory : undefined}
                liveDataResult={isCurrent ? liveDataResult : undefined}
                identityResult={isCurrent ? identityResult : undefined}
                notes={isCurrent ? sessionNotes : ''}
                onNotesChange={isCurrent ? setSessionNotes : undefined}
                notesEditable={isCurrent}
                dispatched={selectedSession.dispatchedRoId !== undefined}
                dispatchedRoId={selectedSession.dispatchedRoId}
              />
            </Box>
          </Modal>
        );
      })()}

      {/* RunRoutineModal — HARD GATE I: checkbox resets on every open.
          Only rendered when there are routines to invoke (avoids auth context errors
          in test environments that render the panel without auth and without routines). */}
      {displayRoutines && displayRoutines.length > 0 && (
        <RunRoutineModal
          visible={pendingRoutine !== null}
          onDismiss={handleModalDismiss}
          onConfirm={handleModalConfirm}
          routineId={pendingRoutine?.routineId ?? ''}
          connectionStatus={connectionStatus}
        />
      )}
    </Box>
  );
};

export default VehicleDiagnosticsPanel;
