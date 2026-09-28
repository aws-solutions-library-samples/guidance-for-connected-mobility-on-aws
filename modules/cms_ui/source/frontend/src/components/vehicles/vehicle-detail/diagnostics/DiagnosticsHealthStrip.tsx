// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// DiagnosticsHealthStrip — Task 3.1 of
// `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign`
//
// Renders the top-of-panel summary strip for the vehicle diagnostics tab:
//   • Connection state      — Cloudscape StatusIndicator
//   • Open fault count      — with highest DTC severity tier
//   • Last scan age         — as relative time ("14 min ago")
//   • Recommended action    — verbatim from computeVerdict() logic (spec Q4)
//   • Run diagnostic scan   — forwarded to parent via onRunScan
//   • Dispatch to service   — existing DispatchModal, existing testid
//
// Absent data never renders as a default, zero, or blank. It says why it is
// absent: still checking, could not load, or no scan on record (`scanHistory`
// prop). An earlier version printed the bare word "unknown", which the first
// UAT could not interpret (issue 2026-09-25-diagnostics-ia-uat-defects).
//
// ── Recommended-action sourcing (spec Q4 / Task 3.1 constraint) ──────────────
//
// The spec says: "recommended action verbatim from existing VerdictBanner logic".
// The VehicleDiagnosticsPanel's local VerdictBanner calls computeVerdict() and
// derives display text from its output.  To avoid reimplementing that logic,
// this component calls computeVerdict() directly and derives the action phrase
// from the VerdictResult via the exported `verdictToRecommendedAction` helper.
//
// The mutation test (Verify step 2) replaces `verdictToRecommendedAction` with a
// hardcoded string and confirms the test asserting derivation fails.

import React, { useState } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import {
  computeVerdict,
  type CatalogEntry,
  type DtcReading,
  type VerdictResult,
} from '@/utils/diagnosticsVerdict';
import DispatchModal from '../DispatchModal';
import type { DispatchEvidence } from '../DispatchModal';
import type { ScanResults } from '@/utils/sovdRow';

// ── Types ─────────────────────────────────────────────────────────────────────

export interface DiagnosticsHealthStripProps {
  /** Exact string 'connected' → connected indicator. Any other value → not connected. */
  connectionStatus?: string;

  /**
   * Number of open DTC fault codes in the latest scan result.
   * Absent → "unknown" (never rendered as 0).
   */
  openFaultCount?: number;

  /**
   * Highest severity tier among the open faults: 'P0' | 'P1' | 'P2' | 'P3'.
   * Absent or unrecognised → "unknown".
   */
  highestSeverity?: string;

  /**
   * ISO timestamp of the last scan, or number of ms since epoch.
   * Absent → "unknown".
   */
  lastScanAt?: string | number;

  /**
   * DTC readings from the latest scan — passed to computeVerdict for the
   * recommended action.  Absent → no verdict computable.
   */
  dtcs?: DtcReading[];

  /**
   * Event catalog — passed to computeVerdict for the recommended action.
   * Absent → no verdict computable.
   */
  catalog?: CatalogEntry[];

  /**
   * Called when the operator clicks "Run scan".
   * HARD GATE: disabled when connectionStatus !== 'connected'.
   */
  onRunScan: () => void;

  /** Whether a scan is currently in progress — disables the Run scan button. */
  scanning?: boolean;

  /**
   * State of the scan-history fetch that the scan fields come from.
   * - 'loading'     — not answered yet; show "checking", claim nothing
   * - 'unavailable' — the fetch failed; say so, claim nothing
   * - 'loaded'      — the scan fields are authoritative
   * With 'loaded' and no `dtcs`, the vehicle has no scan on record.
   * Required, so a caller cannot get a health claim by omitting it.
   */
  scanHistory: 'loading' | 'unavailable' | 'loaded';

  /**
   * How much of the latest scan's result is stored (utils/sovdRow.ts
   * `ScanResults`). Only 'complete' may back "No issues found".
   * Required, like `scanHistory`: a default would be a fail-open health claim.
   */
  scanResults: ScanResults;

  /** ECUs that answered the latest scan; shown for a partial result. */
  ecusAnswered?: number;

  /** The latest scan's results are stored elsewhere (too large to show here). */
  scanResultsOffloaded?: boolean;

  // ── Dispatch props (forwarded to DispatchModal) ────────────────────────────

  /** Required for dispatch; button hidden when absent (fail-closed). */
  vin?: string;

  /** Cognito groups; button shown only for fleet-operator / platform-admin. */
  callerGroups?: string[];

  /** Vehicle ID — forwarded to DispatchModal. */
  vehicleId: string;

  /** Evidence for the dispatch payload. */
  dispatchEvidence: DispatchEvidence;

  /** Called after a successful dispatch with the new RO id. */
  onDispatchSuccess: (roId: string) => void;
}

// ── Relative time helper ──────────────────────────────────────────────────────
//
// Matches the format used by VehicleDTCsTable.tsx `formatAge`:
// "just now", "Xs ago", "N min ago", "N hrs ago", "N days ago".
// Exported for testing.

export function formatRelativeAge(ts: string | number | undefined): string {
  if (ts === undefined || ts === null) return 'unknown';
  const ms = typeof ts === 'number' ? ts : Date.parse(ts as string);
  if (isNaN(ms)) return 'unknown';
  const secs = Math.max(0, Math.round((Date.now() - ms) / 1000));
  if (secs < 10) return 'just now';
  if (secs < 60) return `${secs}s ago`;
  const mins = Math.round(secs / 60);
  if (mins < 60) return `${mins} min ago`;
  const hrs = Math.round(mins / 60);
  if (hrs < 24) return `${hrs} hr${hrs === 1 ? '' : 's'} ago`;
  const days = Math.round(hrs / 24);
  return `${days} day${days === 1 ? '' : 's'} ago`;
}

// ── verdictToRecommendedAction ────────────────────────────────────────────────
//
// Derives the recommended-action text from the output of computeVerdict().
// This is the function the mutation test targets — replacing it with a
// hardcoded string must break a test that asserts the text varies with the
// verdict result.
//
// Derived from the same verdict tier the pre-redesign VerdictBanner used. The
// Healthy and never-scanned copy was revised after the first UAT (issue
// 2026-09-25-diagnostics-ia-uat-defects) to say what the scan covers.
//
// Exported so tests can verify the derivation directly.

export function verdictToRecommendedAction(
  verdict: VerdictResult | undefined,
  catalog: CatalogEntry[],
  dtcs: DtcReading[],
): string {
  if (verdict === undefined) {
    return 'Run a scan to check this vehicle for fault codes.';
  }

  switch (verdict.tier) {
    case 'Healthy':
      return 'No issues found in the last scan.';

    case 'Unrecognised':
      return 'Undetermined — one or more fault codes could not be matched to the event catalog. Manual review required.';

    case 'P0':
    case 'P1':
    case 'P2':
    case 'P3': {
      // Surface catalog description(s) verbatim — same logic as VerdictBanner
      // in VehicleDiagnosticsPanel.tsx:520-530.
      const tier = verdict.tier;
      const descriptions: string[] = [];
      for (const dtc of dtcs) {
        const entry = catalog.find(
          (e) => e.dtc_code === dtc.code && e.severity_hint === tier,
        );
        const description = entry?.description;
        if (description && !descriptions.includes(description)) {
          descriptions.push(description);
        }
      }
      const prefix = `${tier} — ${dtcs.length} fault code${dtcs.length !== 1 ? 's' : ''} detected.`;
      return descriptions.length > 0
        ? `${prefix} ${descriptions.join(' ')}`
        : prefix;
    }
  }
}

// ── DiagnosticsHealthStrip ────────────────────────────────────────────────────

const DiagnosticsHealthStrip: React.FC<DiagnosticsHealthStripProps> = ({
  connectionStatus,
  openFaultCount,
  highestSeverity,
  lastScanAt,
  dtcs,
  catalog,
  onRunScan,
  scanning = false,
  scanHistory,
  scanResults,
  ecusAnswered,
  scanResultsOffloaded = false,
  vin,
  callerGroups,
  vehicleId,
  dispatchEvidence,
  onDispatchSuccess,
}) => {
  const [dispatchModalOpen, setDispatchModalOpen] = useState(false);

  // ── Connection indicator ───────────────────────────────────────────────────

  const isConnected = connectionStatus === 'connected';
  const connectionText = isConnected
    ? 'Connected'
    : connectionStatus
      ? connectionStatus.charAt(0).toUpperCase() + connectionStatus.slice(1)
      : 'Connection status unavailable';

  // ── Scan-derived fields ───────────────────────────────────────────────────
  //
  // Every absent value says WHY it is absent (still checking, could not load,
  // or no scan on record). None renders as 0, and none is the bare word
  // "unknown" that the first UAT could not interpret
  // (issue 2026-09-25-diagnostics-ia-uat-defects).

  const validSeverities = ['P0', 'P1', 'P2', 'P3'];
  const severityText =
    highestSeverity && validSeverities.includes(highestSeverity) ? highestSeverity : undefined;

  const faultsPhrase = (n: number) =>
    `${n} open fault${n !== 1 ? 's' : ''}${severityText ? ` (${severityText} highest)` : ''}`;
  const answeredPhrase =
    ecusAnswered !== undefined ? `the ${ecusAnswered} ECU${ecusAnswered !== 1 ? 's' : ''} that answered` : 'the ECUs that answered';

  let faultText: string;
  if (scanHistory === 'loading') {
    faultText = 'Checking for fault codes…';
  } else if (scanHistory === 'unavailable') {
    faultText = 'Fault codes unavailable';
  } else if (openFaultCount === undefined || openFaultCount === null) {
    faultText = 'Not scanned yet';
  } else if (scanResults === 'unavailable') {
    faultText = 'Fault results not available';
  } else if (scanResults === 'partial') {
    faultText = openFaultCount === 0
      ? `No open faults in ${answeredPhrase}`
      : `${faultsPhrase(openFaultCount)} in ${answeredPhrase}`;
  } else if (openFaultCount === 0) {
    faultText = 'No open faults';
  } else {
    faultText = faultsPhrase(openFaultCount);
  }

  let lastScanText: string;
  if (scanHistory === 'loading') {
    lastScanText = 'Last scan: checking…';
  } else if (scanHistory === 'unavailable') {
    lastScanText = 'Last scan: unavailable';
  } else if (lastScanAt === undefined || lastScanAt === null) {
    lastScanText = 'Last scan: none found';
  } else {
    const age = formatRelativeAge(lastScanAt);
    lastScanText = age === 'unknown' ? 'Last scan: time unavailable' : `Last scan: ${age}`;
  }

  // ── Recommended action ────────────────────────────────────────────────────
  //
  // Derived via computeVerdict() then verdictToRecommendedAction().
  // MUTATION TARGET: replacing this block with a hardcoded string must break
  // the test that asserts recommended-action varies with the verdict.
  //
  // While the history is loading or unavailable there is nothing to judge, so
  // those branches below return before any verdict text — the first UAT found
  // "No issues found" rendered with no scan data behind it.

  const resolvedDtcs = dtcs ?? [];
  const resolvedCatalog = catalog ?? [];

  const verdict: VerdictResult | undefined =
    dtcs !== undefined ? computeVerdict(resolvedDtcs, resolvedCatalog) : undefined;

  // A scan only backs a verdict when its result is complete and a catalog is
  // available to judge severity. Otherwise the text says what is known, and
  // never "No issues found":
  //   - results not stored  → say so, suggest a new scan
  //   - partial result      → say it is incomplete (faults found are still named)
  //   - no catalog          → report the count and point to the DTC list, which
  //                           carries server-side severity (the tab's parent
  //                           does not load the event catalog)
  const scanCount = resolvedDtcs.length;
  let recommendedAction: string;
  if (scanHistory === 'loading') {
    recommendedAction = "Checking this vehicle's scan history…";
  } else if (scanHistory === 'unavailable') {
    recommendedAction = "Couldn't load scan history. Run a scan to check this vehicle for fault codes.";
  } else if (dtcs !== undefined && scanResults === 'unavailable') {
    recommendedAction = scanResultsOffloaded
      // Re-scanning would offload again, so don't suggest it.
      ? "The last scan's results are too large to show here."
      : "The last scan finished, but its results aren't available here. Run a new scan to check for fault codes.";
  } else if (dtcs !== undefined && scanCount > 0 && catalog === undefined) {
    // No catalog loaded (the fetch failed or is still in flight). Name the
    // codes; do not point elsewhere for severity, because the DTC table below
    // does not necessarily hold the scan's codes (review FG2 cycle 2, W2).
    const codes = [...new Set(resolvedDtcs.map((d) => d.code))];
    recommendedAction =
      `${scanCount} fault code${scanCount !== 1 ? 's' : ''} found in the last scan: ${codes.join(', ')}. ` +
      "Severity isn't available right now." +
      (scanResults === 'partial' ? " Some ECUs didn't answer, so there may be more." : '');
  } else if (dtcs !== undefined && scanResults === 'partial' && (verdict === undefined || verdict.tier === 'Healthy')) {
    recommendedAction =
      "Some ECUs didn't answer the last scan, so this result is incomplete. Run a scan again for a full result.";
  } else {
    recommendedAction =
      verdictToRecommendedAction(verdict, resolvedCatalog, resolvedDtcs) +
      (dtcs !== undefined && scanResults === 'partial' ? " Some ECUs didn't answer, so there may be more." : '');
  }

  // ── Dispatch gating ───────────────────────────────────────────────────────
  // True if either fleet-operator or platform-admin group is present (fail-closed).
  const mayDispatch = Boolean(
    callerGroups?.includes('fleet-operator') ||
      callerGroups?.includes('platform-admin'),
  );

  return (
    <Box data-testid="diagnostics-health-strip" padding="s">
      <SpaceBetween direction="vertical" size="xs">

        {/* Row 1: Connection · Faults · Last scan */}
        <SpaceBetween direction="horizontal" size="l">
          <Box data-testid="health-strip-connection">
            <StatusIndicator
              type={isConnected ? 'success' : 'stopped'}
              data-testid="health-strip-connection-indicator"
            >
              {connectionText}
            </StatusIndicator>
          </Box>

          <Box data-testid="health-strip-faults">
            {faultText}
          </Box>

          <Box data-testid="health-strip-last-scan">
            {lastScanText}
          </Box>
        </SpaceBetween>

        {/* Row 2: Recommended action */}
        <Box
          data-testid="health-strip-recommended-action"
          variant="p"
          color="text-body-secondary"
        >
          {recommendedAction}
        </Box>

        {/* Row 3: Actions */}
        <SpaceBetween direction="horizontal" size="xs">
          <Button
            onClick={onRunScan}
            disabled={!isConnected || scanning}
            loading={scanning}
            iconName="search"
            data-testid="health-strip-run-scan-button"
          >
            Run diagnostic scan
          </Button>

          {mayDispatch && vin && (
            <>
              <Button
                onClick={() => setDispatchModalOpen(true)}
                data-testid="dispatch-to-service-button"
              >
                Dispatch to service
              </Button>
              <DispatchModal
                visible={dispatchModalOpen}
                onDismiss={() => setDispatchModalOpen(false)}
                onSuccess={(roId) => {
                  setDispatchModalOpen(false);
                  onDispatchSuccess(roId);
                }}
                vehicleId={vehicleId}
                vin={vin}
                evidence={dispatchEvidence}
              />
            </>
          )}
        </SpaceBetween>

      </SpaceBetween>
    </Box>
  );
};

export default DiagnosticsHealthStrip;
