// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * RoutineOffering — the routine-listing surface for the diagnostics tab redesign.
 *
 * Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign`
 *       Task 3.4 — "Routine offering"
 *       § Design 5 — Routine offering
 *
 * Layout
 * ──────
 *   Directly (top-level): the three operator-invocable INERT routines, each
 *     with an active Run button.
 *
 *   <ExpandableSection> framed as "What a technician can run at the shop":
 *     the remaining 16 STATIONARY and SERVICE_ONLY routines, each group laid
 *     out in columns.  The STATIONARY gate (disabled button + visible static
 *     explanation text) and its `run-routine-disabled-<id>` testids are
 *     unchanged from VehicleDiagnosticsPanel.tsx.  The explanation is shown
 *     ONCE per group (`stationary-note`) and every disabled button points at
 *     it with aria-describedby (FG4, operator 2026-09-25: the same line under
 *     each of 11 routines added nothing).
 *
 * Tooltip rule (spec § Constraints — "Tooltips split by kind")
 * ──────────────────────────────────────────────────────────────
 *   <Popover> is used for DEFINITIONS only — what a routine does, what its
 *   safety class means.
 *
 *   PROHIBITIONS (why an action is unavailable) MUST remain as visible static
 *   DOM text.  The 2026-09-24 operator finding: "a safety explanation reachable
 *   only by hover is discoverable only by accident."  The STATIONARY explanation
 *   text is NEVER inside a Popover.
 *
 * Safety invariant
 * ─────────────────
 *   The STATIONARY gate (cls === 'STATIONARY') is the ONLY UI enforcement point
 *   preventing a fleet-operator from invoking a STATIONARY routine.  The Lambda
 *   has no STATIONARY-specific gate for this persona — see
 *   `issues/2026-09-12-diagnostics-persona-matrix-not-group-enforced/`.
 *   A STATIONARY routine becoming clickable is a SAFETY REGRESSION, not a UI bug.
 *
 *   Task 1.2's tests (diagnostics-stationary-safety-gate.test.tsx) guard the
 *   gate.  Their disabled-button assertions (SG1/2/4/5/7/8) must pass against
 *   this component unmodified.  SG3/6/9 assert that each disabled button is
 *   described by the static prohibition; they changed with FG4's copy change
 *   (decisions.md), not because the gate moved.
 *
 * Ownership of invocation
 * ────────────────────────
 *   This component does NOT own the poll.  It receives callbacks for the run
 *   flow (onRunClick, pendingRoutine).  The panel owns the poll per spec § Design 4.
 */

import React, { useId } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import ColumnLayout from '@cloudscape-design/components/column-layout';
import ExpandableSection from '@cloudscape-design/components/expandable-section';
import Header from '@cloudscape-design/components/header';
import Popover from '@cloudscape-design/components/popover';
import SpaceBetween from '@cloudscape-design/components/space-between';
import type { RoutineCatalogEntry } from '../VehicleDiagnosticsPanel';
import { mayInvokeForCaller } from '../VehicleDiagnosticsPanel';

// ── Safety-class definitions (DEFINITION-only, for <Popover> use) ────────────

const SAFETY_CLASS_DEFINITIONS: Record<
  RoutineCatalogEntry['safetyClass'],
  { label: string; definition: string }
> = {
  INERT: {
    label: 'INERT',
    definition:
      "Read-only self-tests that query the vehicle's ECUs without commanding any actuation. " +
      'Safe to run remotely \u2014 the vehicle is not required to be at rest.',
  },
  STATIONARY: {
    label: 'STATIONARY',
    definition:
      'Routines that require the vehicle to be stationary, in park or neutral, with ignition on. ' +
      'Must be run in person at the vehicle; the sidecar re-checks these conditions immediately before execution.',
  },
  SERVICE_ONLY: {
    label: 'SERVICE_ONLY',
    definition:
      'Routines that require an active repair order and a technician at the vehicle. ' +
      'These are invoked through DMS against an active service visit — CMS does not run them.',
  },
};

// ── Props ─────────────────────────────────────────────────────────────────────

export interface RoutineOfferingProps {
  /** Full catalog returned by the server. Split internally into INERT vs the rest. */
  routines: RoutineCatalogEntry[];

  /**
   * Called when the operator clicks Run on an INERT routine.  The panel opens
   * the RunRoutineModal; this component does not own the modal.
   */
  onRunClick: (entry: RoutineCatalogEntry) => void;

  /**
   * Whether a run is currently in-flight (any routine).  When true, the INERT
   * Run buttons are aria-hidden to prevent double-submission.
   */
  runInFlight?: boolean;

  /** Optional: if supplied, the "technician" ExpandableSection starts expanded. */
  technicianSectionDefaultExpanded?: boolean;
}

// ── Component ─────────────────────────────────────────────────────────────────

/**
 * RoutineOffering
 *
 * Renders the operator-visible INERT routines directly, and everything else
 * inside an ExpandableSection framed as "What a technician can run at the shop".
 *
 * The component changes PRESENTATION only.  `safetyClass` and `invocableByCaller`
 * are server-derived and are not altered here.
 */
const RoutineOffering: React.FC<RoutineOfferingProps> = ({
  routines,
  onRunClick,
  runInFlight = false,
  technicianSectionDefaultExpanded = false,
}) => {
  // One id per mounted instance for the STATIONARY prohibition line, so every
  // disabled STATIONARY button can point at it with aria-describedby.
  const stationaryNoteId = `stationary-note-${useId()}`;

  // ── Split the catalog ────────────────────────────────────────────────────────
  //
  // "Operator-invocable INERT" = INERT + mayInvokeForCaller === true.
  //   The spec says "the three operator-invocable INERT routines" — these are
  //   lamp_self_check, pack_isolation_test, cell_balance_check.  The component
  //   does not hardcode those IDs; it derives them from the server-supplied
  //   safetyClass + invocableByCaller/invocable fields so it stays correct if
  //   the catalog changes.
  //
  // Everything else (STATIONARY, SERVICE_ONLY, INERT with invocable===false) goes
  // into the technician section.
  const operatorInertRoutines = routines.filter(
    r => r.safetyClass === 'INERT' && mayInvokeForCaller(r),
  );
  const technicianRoutines = routines.filter(
    r => !(r.safetyClass === 'INERT' && mayInvokeForCaller(r)),
  );

  // Group technician routines by safetyClass for section rendering, preserving
  // the original panel's GROUP_ORDER: INERT (non-invocable), STATIONARY, SERVICE_ONLY.
  const technicianGroups: Partial<Record<RoutineCatalogEntry['safetyClass'], RoutineCatalogEntry[]>> = {};
  for (const entry of technicianRoutines) {
    if (!technicianGroups[entry.safetyClass]) {
      technicianGroups[entry.safetyClass] = [];
    }
    technicianGroups[entry.safetyClass]!.push(entry);
  }
  const TECH_GROUP_ORDER: RoutineCatalogEntry['safetyClass'][] = ['INERT', 'STATIONARY', 'SERVICE_ONLY'];

  // ── Render ───────────────────────────────────────────────────────────────────

  return (
    <Box>
      {/* ── Directly rendered: operator-invocable INERT routines ────────────── */}
      <Header variant="h3">
        Self-tests you can run here
      </Header>
      <Box variant="p" color="text-body-secondary">
        Read-only self-tests that query the vehicle's ECUs without commanding
        any actuation. Safe to run remotely.
      </Box>

      {operatorInertRoutines.length === 0 ? (
        <Box color="text-status-inactive" variant="p">
          No operator-invocable routines available for this vehicle.
        </Box>
      ) : (
        <SpaceBetween size="xs">
          {operatorInertRoutines.map(entry => (
            <Box key={entry.routineId} margin={{ top: 'xs', bottom: 'xs' }}>
              <SpaceBetween size="xs" direction="horizontal">
                {/* Run button — enabled, aria-hidden while a run is in-flight */}
                <div
                  aria-hidden={runInFlight ? true : undefined}
                  data-testid={`inert-run-wrapper-${entry.routineId}`}
                >
                  <Button
                    variant="normal"
                    onClick={() => onRunClick(entry)}
                    data-testid={`run-routine-${entry.routineId}`}
                  >
                    Run {entry.routineId}
                  </Button>
                </div>

                {/* DEFINITION popover — what this routine does, what its verdict means.
                    Per spec § Constraints: Popovers for definitions only, never for
                    prohibitions.  This popover describes what the routine IS, not why
                    any action is unavailable. */}
                <Popover
                  triggerType="custom"
                  header={entry.routineId}
                  content={
                    entry.precondition
                      ? `${entry.precondition} A passed result means all ECU-reported self-test parameters are within expected ranges.`
                      : 'Read-only self-test of ECU-reported parameters.'
                  }
                  data-testid={`routine-info-popover-${entry.routineId}`}
                >
                  <Button variant="icon" iconName="status-info" ariaLabel={`About ${entry.routineId}`} />
                </Popover>
              </SpaceBetween>
            </Box>
          ))}
        </SpaceBetween>
      )}

      {/* ── ExpandableSection: what a technician can run at the shop ─────────── */}
      {technicianRoutines.length > 0 && (
        <Box margin={{ top: 'l' }}>
          <ExpandableSection
            headerText="What a technician can run at the shop"
            defaultExpanded={technicianSectionDefaultExpanded}
            data-testid="technician-routines-section"
          >
            {TECH_GROUP_ORDER.map(cls => {
              const entries = technicianGroups[cls];
              if (!entries || entries.length === 0) return null;

              const TRIAGE_LABELS: Record<RoutineCatalogEntry['safetyClass'], string> = {
                INERT: 'Self-tests you can run here',
                STATIONARY: 'Self-tests that require the vehicle at rest',
                SERVICE_ONLY: 'Handled by service at the dealership',
              };
              const triageLabel = TRIAGE_LABELS[cls];
              const preconditionText = entries[0]?.precondition ?? cls;

              const classDef = SAFETY_CLASS_DEFINITIONS[cls];

              return (
                <Box key={cls} margin={{ top: 's' }}>
                  {/* Safety-class heading with DEFINITION popover (definition, not prohibition) */}
                  <SpaceBetween size="xs" direction="horizontal">
                    <Box variant="h4">{triageLabel}</Box>
                    <Popover
                      triggerType="custom"
                      header={classDef.label}
                      content={classDef.definition}
                      data-testid={`safety-class-info-${cls}`}
                    >
                      <Button
                        variant="icon"
                        iconName="status-info"
                        ariaLabel={`About ${classDef.label} routines`}
                      />
                    </Popover>
                  </SpaceBetween>

                  {/* D28 preservation: server-supplied precondition rendered verbatim */}
                  <Box variant="p" color="text-body-secondary">{preconditionText}</Box>

                  {/* PROHIBITION TEXT for the STATIONARY group: static visible DOM text,
                      NEVER a Popover or the Button's disabledReason tooltip (spec
                      § Constraints, "Tooltips split by kind"). Shown once for the group,
                      not under each routine (FG4, operator 2026-09-25); each disabled
                      button below points at it with aria-describedby. */}
                  {cls === 'STATIONARY' && (
                    <Box
                      variant="p"
                      color="text-status-inactive"
                      data-testid="stationary-note"
                    >
                      <span id={stationaryNoteId}>Not available for remote fleet operation.</span>
                    </Box>
                  )}

                  {/* SERVICE_ONLY caption */}
                  {cls === 'SERVICE_ONLY' && (
                    <Box variant="p" color="text-body-secondary">
                      These routines are invoked by a technician against an active repair order in DMS. CMS does not run them.
                    </Box>
                  )}

                  <Box margin={{ top: 'xs' }}>
                    <ColumnLayout
                      columns={cls === 'SERVICE_ONLY' ? 2 : 4}
                      minColumnWidth={cls === 'SERVICE_ONLY' ? 320 : 240}
                      data-testid={`technician-routines-columns-${cls}`}
                    >
                      {entries.map(entry => (
                        <Box key={entry.routineId}>
                          {/* DX53: SERVICE_ONLY reason rendered verbatim */}
                          {cls === 'SERVICE_ONLY' && (
                            <>
                              <Box variant="p"><strong>{entry.routineId}</strong></Box>
                              <Box variant="p" color="text-status-inactive">
                                {entry.reason}
                              </Box>
                            </>
                          )}

                          {/* T3.1(e) / spec § Constraints: STATIONARY routines visible-but-disabled.
                              `run-routine-disabled-<id>` is preserved UNCHANGED from
                              VehicleDiagnosticsPanel.tsx — Task 1.2's SG1/2/4/5/7/8 must pass
                              against this component unmodified. */}
                          {cls === 'STATIONARY' && (
                            <div data-testid={`stationary-disabled-${entry.routineId}`}>
                              <Button
                                variant="normal"
                                disabled
                                ariaDescribedby={stationaryNoteId}
                                data-testid={`run-routine-disabled-${entry.routineId}`}
                              >
                                Run {entry.routineId}
                              </Button>
                            </div>
                          )}

                          {/* INERT routines in the technician section (invocable===false):
                              these are INERT by safety class but not invocable for this caller.
                              Render as informational only — no Run button. */}
                          {cls === 'INERT' && !mayInvokeForCaller(entry) && (
                            <Box variant="p" color="text-status-inactive">
                              <strong>{entry.routineId}</strong> — not available for this caller.
                            </Box>
                          )}
                        </Box>
                      ))}
                    </ColumnLayout>
                  </Box>
                </Box>
              );
            })}
          </ExpandableSection>
        </Box>
      )}
    </Box>
  );
};

export default RoutineOffering;
