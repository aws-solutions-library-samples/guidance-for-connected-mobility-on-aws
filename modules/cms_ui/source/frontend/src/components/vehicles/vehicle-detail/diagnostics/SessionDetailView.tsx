// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// SessionDetailView — Session drill-in for the Diagnostics tab IA redesign.
//
// Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign`
//       § Design 4 — "The poll is owned by the panel, not the view"
//       Task 3.3
//
// CRITICAL OWNERSHIP RULE:
//   This component accepts poll data as PROPS.  It does NOT own the poll.
//   Zero interval-based or timeout-based polling.  The panel (VehicleDiagnosticsPanel)
//   owns the poll and passes session-scoped data down; navigating away from this
//   view must not abort a running routine (spec § Design 4).
//
// Structure (spec § Design 4, Q7):
//   - Header: ← Back to sessions (onBack callback)
//   - <Container> "Routine results"      — expanded; uses the renderer registry, unchanged
//   - <ExpandableSection> "ECU & freeze frames"   — collapsed by default (technician)
//   - <ExpandableSection> "Live data & identity drift" — collapsed by default (technician)
//   - <Container> "Evidence & notes"    — what dispatch consumes
//
// No inner tab-set: the vehicle detail page is already a tab surface (spec Q7).
// Renderers are IMPORTED, never edited (standing rule 1 — renderer parity guard).

import React, { useState } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Container from '@cloudscape-design/components/container';
import Header from '@cloudscape-design/components/header';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Cards from '@cloudscape-design/components/cards';
import Badge from '@cloudscape-design/components/badge';
import Alert from '@cloudscape-design/components/alert';
import Textarea from '@cloudscape-design/components/textarea';

import { rendererFor } from '../routine-renderers';
import type { RoutineResponse } from '../routine-renderers';
import { MANIFEST_ECU_LABELS, ECU_OPTIONS } from '../ecu_names';
import type {
  SessionCommandEntry,
  EcuDiagnosticResult,
  LiveDataResult,
  IdentityResult,
  EcuInventoryItem,
} from '../VehicleDiagnosticsPanel';

// ── Helpers ──────────────────────────────────────────────────────────────────

/** Resolve a human-readable label for an ECU name — mirrors the helper in the panel. */
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

// ── Props ─────────────────────────────────────────────────────────────────────

export interface SessionDetailViewProps {
  /** The session being displayed. */
  sessionId: string;

  /** Commands for this specific session (already filtered by caller). */
  sessionCommands: SessionCommandEntry[];

  /**
   * Called when the operator clicks "← Back to sessions". When absent the back
   * button is not rendered (the Diagnostics tab shows this view in a modal,
   * whose Close does the same job). The parent controls visibility; this
   * component never mutates it.
   */
  onBack?: () => void;

  // ── Technician data (collapsed by default, spec § Design 4) ───────────────

  /** T7.1a: per-ECU diagnostic state with unreachable reasons. Optional. */
  ecuDiagnosticResult?: EcuDiagnosticResult;

  /**
   * T7.1a: SOVD scan ECU cards from the scan response components map.
   * Each entry carries dtcCount and hasFreezeFrame (pre-computed by caller).
   */
  ecuCards?: Array<{ id: string; dtcCount: number; hasFreezeFrame: boolean }>;

  /** T7.1a: ECU inventory list. Optional. */
  ecuInventory?: EcuInventoryItem[];

  /** T7.1a: live data readings per ECU. Optional. */
  liveDataResult?: LiveDataResult;

  /** T7.1a: identity/drift result. Optional. */
  identityResult?: IdentityResult;

  // ── Evidence & notes ──────────────────────────────────────────────────────

  /** Any notes the operator has entered. */
  notes?: string;
  /** Called when the operator edits the notes textarea. */
  onNotesChange?: (value: string) => void;
  /**
   * Whether notes can be added here. Notes go out with this visit's dispatch,
   * so only the current session is editable; an older session says so
   * instead of showing a text box that saves nothing. Defaults to true.
   */
  notesEditable?: boolean;

  /**
   * Whether this session is terminal (dispatched).
   * If true, notes are read-only and the evidence section shows the dispatched state.
   */
  dispatched?: boolean;
  /** RO id when dispatched — shown in evidence section. */
  dispatchedRoId?: string;
}

// ── Sub-components ────────────────────────────────────────────────────────────

/**
 * Routine results — expanded by default.
 * Uses the renderer registry exactly as the existing SessionLog does.
 * Standing rule 1: renderers are imported, never edited.
 */
function RoutineResultsSection({ sessionCommands }: { sessionCommands: SessionCommandEntry[] }) {
  if (sessionCommands.length === 0) {
    return (
      <Box data-testid="routine-results-empty">
        <Box variant="p" color="text-status-inactive">
          No routine results for this session yet.
        </Box>
      </Box>
    );
  }

  return (
    <Box data-testid="routine-results-list">
      {sessionCommands.map((cmd, index) => {
        const id = cmd.commandId ?? `cmd-${index}`;
        const routineLabel = cmd.routineId ?? cmd.commandType ?? cmd.type ?? 'command';
        const status = cmd.status ?? 'UNKNOWN';
        const submittedAt = cmd.submittedAt ?? '';
        const hasReason = cmd.reason !== undefined && cmd.reason !== null && cmd.reason.trim() !== '';
        // A RATE_LIMITED attempt is explained by its status: the vehicle was
        // busy and asked for a retry. Live rows carry `response.retry_after_ms`,
        // not `reason`, so without this every retried attempt showed the
        // "no explanation" warning next to a session the table calls Complete
        // (issue 2026-09-25-diagnostics-ia-uat-defects, review S8).
        const rateLimitedNote =
          status === 'RATE_LIMITED' && !hasReason
            ? 'the vehicle was busy and asked to retry later'
            : undefined;
        const showSilentFailureBanner = status !== 'SUCCEEDED' && !hasReason && rateLimitedNote === undefined;
        const submittedLabel = (() => {
          if (!submittedAt) return '';
          const d = new Date(submittedAt);
          return isNaN(d.getTime()) ? submittedAt : d.toLocaleString();
        })();

        // Renderer registry — same discipline as the existing SessionLog in the panel.
        // Only render for SUCCEEDED entries with a response payload.
        let responseDrawer: React.ReactNode = null;
        if (status === 'SUCCEEDED' && cmd.response !== undefined && cmd.response !== null) {
          const routineId = cmd.routineId ?? cmd.type ?? '';
          const Renderer = rendererFor(routineId);
          const responseAsRoutine = cmd.response as RoutineResponse;
          responseDrawer = (
            <Renderer response={responseAsRoutine} routineId={routineId} commandId={id} />
          );
        }

        return (
          <Box key={id} margin={{ bottom: 'xs' }} data-testid={`routine-result-entry-${id}`}>
            <Box variant="p">
              <strong>{routineLabel}</strong>
              {' → '}
              {status}
              {submittedLabel ? ` (${submittedLabel})` : ''}
              {hasReason ? `: ${cmd.reason}` : ''}
              {rateLimitedNote ? `: ${rateLimitedNote}` : ''}
            </Box>
            {showSilentFailureBanner && (
              <Alert
                type="warning"
                data-testid={`routine-result-silent-failure-${id}`}
              >
                {`The vehicle reported a non-success status (${status}) without an explanation. Retry, or check the command history for details.`}
              </Alert>
            )}
            {responseDrawer}
          </Box>
        );
      })}
    </Box>
  );
}

/**
 * ECU & freeze frames — collapsed by default (technician content).
 * Contains: ECU cards, freeze-frame badges, ECU inventory disclosure.
 */
function EcuAndFreezeFramesContent({
  ecuCards,
  ecuDiagnosticResult,
  ecuInventory,
}: {
  ecuCards?: Array<{ id: string; dtcCount: number; hasFreezeFrame: boolean }>;
  ecuDiagnosticResult?: EcuDiagnosticResult;
  ecuInventory?: EcuInventoryItem[];
}) {
  const [ecuInventoryExpanded, setEcuInventoryExpanded] = useState(false);

  const hasEcuCards = ecuCards && ecuCards.length > 0;
  const hasUnreachableReasons =
    ecuDiagnosticResult &&
    Object.values(ecuDiagnosticResult.components ?? {}).some(e => !!e.unreachableReason);

  if (!hasEcuCards && !hasUnreachableReasons && (!ecuInventory || ecuInventory.length === 0)) {
    return (
      <Box variant="p" color="text-status-inactive" data-testid="ecu-section-empty">
        No ECU data available for this session.
      </Box>
    );
  }

  // Applicable inventory items only (C15: items with applicable === false are excluded)
  const applicableInventory = (ecuInventory ?? []).filter(item => item.applicable !== false);

  return (
    <Box>
      {/* ECU cards + freeze-frame badges */}
      {hasEcuCards && (
        <Box margin={{ bottom: 's' }}>
          <Cards
            data-testid="ecu-cards"
            items={ecuCards!}
            cardDefinition={{
              header: item => (
                <Box>
                  {item.id}
                  {item.hasFreezeFrame && (
                    <Badge color="blue" data-testid={`freeze-frame-badge-${item.id}`}>
                      Freeze Frame
                    </Badge>
                  )}
                </Box>
              ),
              sections: [
                {
                  id: 'dtcCount',
                  content: item => `${item.dtcCount} DTC${item.dtcCount !== 1 ? 's' : ''}`,
                },
              ],
            }}
          />
        </Box>
      )}

      {/* ECU unreachable reasons (verbatim from server, F22 Decision 2) */}
      {hasUnreachableReasons && (
        <Box margin={{ bottom: 's' }}>
          {Object.entries(ecuDiagnosticResult!.components ?? {})
            .filter(([, entry]) => !!entry.unreachableReason)
            .map(([ecuName, entry]) => (
              <Box key={ecuName} margin={{ bottom: 'xs' }} data-testid={`ecu-unreachable-${ecuName}`}>
                <Box variant="p">
                  {`${resolveEcuLabel(ecuName)}: ${entry.unreachableReason}`}
                </Box>
              </Box>
            ))}
        </Box>
      )}

      {/* ECU inventory disclosure — lazy-mounted per DX8/F22 Decision 1 */}
      {applicableInventory.length > 0 && (
        <ExpandableSection
          headerText="ECU inventory"
          data-testid="ecu-inventory-disclosure"
          expanded={ecuInventoryExpanded}
          onChange={({ detail }) => setEcuInventoryExpanded(detail.expanded)}
        >
          {ecuInventoryExpanded && (
            <Box data-testid="ecu-inventory-content">
              {applicableInventory.map(item => (
                <Box key={item.ecuName} margin={{ bottom: 'xs' }}>
                  <Box variant="p">
                    {`${item.label ?? resolveEcuLabel(item.ecuName)}: ${item.reachable ? 'Reachable' : `Not reachable${item.unreachableReason ? ` — ${item.unreachableReason}` : ''}`}`}
                  </Box>
                </Box>
              ))}
            </Box>
          )}
        </ExpandableSection>
      )}
    </Box>
  );
}

/**
 * Live data & identity drift — collapsed by default (technician content).
 */
function LiveDataAndDriftContent({
  liveDataResult,
  identityResult,
}: {
  liveDataResult?: LiveDataResult;
  identityResult?: IdentityResult;
}) {
  const liveDataEntries = Object.entries(liveDataResult?.components ?? {});
  const driftEntries = Object.entries(identityResult?.components ?? {});

  if (liveDataEntries.length === 0 && driftEntries.length === 0) {
    return (
      <Box variant="p" color="text-status-inactive" data-testid="live-data-section-empty">
        No live data or identity drift data available for this session.
      </Box>
    );
  }

  return (
    <Box>
      {/* Live data readings */}
      {liveDataEntries.length > 0 && (
        <Box margin={{ bottom: 's' }} data-testid="live-data-section">
          <Header variant="h3">Live data</Header>
          {liveDataEntries.map(([ecuName, entry]) => (
            <Box key={ecuName} margin={{ bottom: 'xs' }}>
              <Box variant="p">
                {`${resolveEcuLabel(ecuName)}: DID ${entry.did ?? '—'} = ${entry.value ?? '—'}${entry.unit ? ` ${entry.unit}` : ''}`}
              </Box>
            </Box>
          ))}
        </Box>
      )}

      {/* Identity / drift */}
      {driftEntries.length > 0 && (
        <Box data-testid="identity-drift-section">
          <Header variant="h3">Identity and version drift</Header>
          {driftEntries.map(([ecuName, entry]) => {
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
      )}
    </Box>
  );
}

// ── Main component ─────────────────────────────────────────────────────────────

/**
 * SessionDetailView — detail surface for one diagnostic session.
 *
 * POLL OWNERSHIP: this component does NOT own any poll.  It receives all
 * session data as props.  The poll lives in VehicleDiagnosticsPanel and
 * must continue running while this view is mounted (spec § Design 4).
 */
const SessionDetailView: React.FC<SessionDetailViewProps> = ({
  sessionId,
  sessionCommands,
  onBack,
  ecuDiagnosticResult,
  ecuCards,
  ecuInventory,
  liveDataResult,
  identityResult,
  notes = '',
  onNotesChange,
  notesEditable = true,
  dispatched = false,
  dispatchedRoId,
}) => {
  return (
    <Box data-testid={`session-detail-view-${sessionId}`}>
      {/* Header: ← Back to sessions. Rendered only when the parent supplies
          onBack; inside the session-detail modal the modal's Close does this. */}
      {onBack && (
        <Box margin={{ bottom: 'm' }}>
          <Button
            variant="link"
            iconName="arrow-left"
            onClick={onBack}
            data-testid="session-detail-back-button"
          >
            ← Back to sessions
          </Button>
        </Box>
      )}

      {/* ── Routine results — EXPANDED (not in an ExpandableSection) ── */}
      <Container
        header={<Header variant="h2">Routine results</Header>}
        data-testid="routine-results-container"
      >
        <RoutineResultsSection sessionCommands={sessionCommands} />
      </Container>

      {/* ── ECU & freeze frames — COLLAPSED (technician content) ── */}
      <Box margin={{ top: 'm' }}>
        <ExpandableSection
          headerText="ECU & freeze frames"
          data-testid="ecu-freeze-frames-section"
          // collapsed by default — defaultExpanded is NOT set
        >
          <EcuAndFreezeFramesContent
            ecuCards={ecuCards}
            ecuDiagnosticResult={ecuDiagnosticResult}
            ecuInventory={ecuInventory}
          />
        </ExpandableSection>
      </Box>

      {/* ── Live data & identity drift — COLLAPSED (technician content) ── */}
      <Box margin={{ top: 'm' }}>
        <ExpandableSection
          headerText="Live data & identity drift"
          data-testid="live-data-drift-section"
          // collapsed by default — defaultExpanded is NOT set
        >
          <LiveDataAndDriftContent
            liveDataResult={liveDataResult}
            identityResult={identityResult}
          />
        </ExpandableSection>
      </Box>

      {/* ── Evidence & notes — what dispatch consumes ── */}
      <Box margin={{ top: 'm' }}>
        <Container
          header={<Header variant="h2">Evidence & notes</Header>}
          data-testid="evidence-notes-container"
        >
          {dispatched && dispatchedRoId && (
            <Box margin={{ bottom: 's' }}>
              <Box variant="p" color="text-status-success" data-testid="dispatched-ro-link">
                {`Dispatched — Service order: ${dispatchedRoId}`}
              </Box>
              <Box variant="p" color="text-status-inactive">
                This session is complete. Further diagnostics require a new session.
              </Box>
            </Box>
          )}
          {dispatched && !dispatchedRoId && (
            <Box margin={{ bottom: 's' }}>
              <Box variant="p" color="text-status-inactive" data-testid="dispatched-no-ro">
                This session has been dispatched.
              </Box>
            </Box>
          )}
          {!dispatched && !notesEditable && (
            <Box variant="p" color="text-status-inactive" data-testid="evidence-notes-not-current">
              Notes and dispatch apply to the current session only. This is an earlier session.
            </Box>
          )}
          {!dispatched && notesEditable && (
            <Box>
              <Box variant="p" color="text-label" margin={{ bottom: 'xs' }}>
                Session notes
              </Box>
              <Textarea
                value={notes}
                onChange={({ detail }) => onNotesChange?.(detail.value)}
                placeholder="Add notes for the service order (optional)"
                rows={4}
                data-testid="evidence-notes-textarea"
                disabled={dispatched}
              />
            </Box>
          )}
        </Container>
      </Box>
    </Box>
  );
};

export default SessionDetailView;
