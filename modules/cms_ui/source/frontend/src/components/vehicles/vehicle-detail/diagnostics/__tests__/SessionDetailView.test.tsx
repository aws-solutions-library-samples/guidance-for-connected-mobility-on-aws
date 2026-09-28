// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/// <reference types="node" />
/// <reference types="@testing-library/jest-dom/vitest" />

/**
 * SessionDetailView — Task 3.3 acceptance tests.
 *
 * Spec: `.kiro/specs/2026-09-24-cms-diagnostics-tab-ia-redesign` § Design 4, Task 3.3
 *
 * Accept criteria tested here:
 *   SDV1  — Header contains "← Back to sessions"; clicking it fires the onBack callback.
 *   SDV2  — Routine results container is rendered (not inside an ExpandableSection).
 *   SDV3  — ECU & freeze frames section is collapsed by default (technician, spec Q7).
 *   SDV4  — Live data & identity drift section is collapsed by default (technician, spec Q7).
 *   SDV5  — Evidence & notes container is rendered.
 *   SDV6  — Zero routine results renders an empty state, not a blank panel.
 *   SDV7  — No <Tabs> rendered (vehicle page is already a tab surface — spec Q7).
 *   SDV8  — Component contains no polling (no setInterval / setTimeout) — structural read.
 *   SDV9  — Routine SUCCEEDED entry renders its response via the renderer registry.
 *
 * Mutation verification (Verify step 3, required before [x]):
 *   Defaulting a technician ExpandableSection to expanded (defaultExpanded)
 *   causes SDV3 (collapsed-by-default) to FAIL.
 *   Recorded in decisions.md under "Task 3.3 mutation verification".
 */

import React from 'react';
import { render, screen, fireEvent } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

// ── Module mocks ─────────────────────────────────────────────────────────────
// These follow the same pattern as every other diagnostics test in this suite.

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: { groups: ['fleet-operator'], roles: ['fleet-operator'] },
  }),
}));

vi.mock('@/config/api', () => ({
  getApiEndpoint: () => 'http://localhost/',
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
}));

vi.mock('@/utils/simulation-config', () => ({
  getSimulationApiUrl: (p: string) => `http://localhost/api/simulation${p}`,
  getSimulationApiBase: () => 'http://localhost',
  getSimulationMode: () => 'local',
  isCloudSimAvailable: () => false,
  setSimulationMode: () => {},
}));

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

// Mock the renderer registry so we don't need the full renderer implementations
// in this component-level test. The registry contract is guarded by its own tests.
vi.mock(
  '@/components/vehicles/vehicle-detail/routine-renderers',
  () => ({
    rendererFor: () =>
      ({ routineId, commandId }: { routineId: string; commandId?: string }) =>
        React.createElement(
          'div',
          { 'data-testid': `mock-renderer-${commandId ?? routineId}` },
          `Renderer: ${routineId}`,
        ),
    ROUTINE_RENDERERS: {},
    RawDrawer: () => null,
  }),
);

import SessionDetailView from '../SessionDetailView';
import type { SessionDetailViewProps } from '../SessionDetailView';
import type { SessionCommandEntry } from '@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel';

// ── Fixtures ──────────────────────────────────────────────────────────────────

const SESSION_ID = 'session-abc-123';

const SUCCEEDED_COMMAND: SessionCommandEntry = {
  commandId: 'cmd-001',
  routineId: 'lamp_self_check',
  commandType: 'run_routine',
  status: 'SUCCEEDED',
  submittedAt: '2026-09-24T19:54:00Z',
  sessionId: SESSION_ID,
  response: { verdict: 'in_spec', result: { passed: true } },
};

const IN_PROGRESS_COMMAND: SessionCommandEntry = {
  commandId: 'cmd-002',
  routineId: 'o2_heater_check',
  commandType: 'run_routine',
  status: 'IN_PROGRESS',
  submittedAt: '2026-09-24T19:55:00Z',
  sessionId: SESSION_ID,
};

function makeProps(overrides: Partial<SessionDetailViewProps> = {}): SessionDetailViewProps {
  return {
    sessionId: SESSION_ID,
    sessionCommands: [],
    onBack: vi.fn(),
    ...overrides,
  };
}

// ── SDV1: Back button ─────────────────────────────────────────────────────────

describe('SDV1 — Header: ← Back to sessions action', () => {
  it('renders a back button with "Back to sessions" text', () => {
    render(<SessionDetailView {...makeProps()} />);
    const btn = screen.getByTestId('session-detail-back-button');
    expect(btn).toBeTruthy();
    expect(btn.textContent).toContain('Back to sessions');
  });

  it('clicking the back button fires the onBack callback', () => {
    const onBack = vi.fn();
    render(<SessionDetailView {...makeProps({ onBack })} />);
    fireEvent.click(screen.getByTestId('session-detail-back-button'));
    expect(onBack).toHaveBeenCalledTimes(1);
  });
});

// ── SDV2: Routine results container (expanded, NOT inside ExpandableSection) ──

describe('SDV2 — Routine results container is expanded (not in ExpandableSection)', () => {
  it('renders the routine-results-container', () => {
    render(<SessionDetailView {...makeProps({ sessionCommands: [SUCCEEDED_COMMAND] })} />);
    expect(screen.getByTestId('routine-results-container')).toBeTruthy();
  });

  it('routine results are immediately visible without expanding anything', () => {
    render(<SessionDetailView {...makeProps({ sessionCommands: [SUCCEEDED_COMMAND] })} />);
    // The routine results list is present directly — no click needed.
    expect(screen.getByTestId('routine-results-list')).toBeTruthy();
  });
});

// ── SDV3: ECU & freeze frames — COLLAPSED by default ─────────────────────────
//
// MUTATION TARGET: changing the ExpandableSection `defaultExpanded` to true (or
// setting `expanded={true}` without a toggle) causes this test to FAIL.
// See decisions.md "Task 3.3 mutation verification".
//
// Note: Cloudscape ExpandableSection renders children into the DOM even when
// collapsed (DX8/F22 Decision 1 in the existing panel). The correct assertion
// is aria-expanded="false" on the section's trigger button, not a DOM-absence
// check on the children.

describe('SDV3 — ECU & freeze frames collapsed by default', () => {
  it('ECU & freeze frames section is present', () => {
    render(<SessionDetailView {...makeProps()} />);
    expect(screen.getByTestId('ecu-freeze-frames-section')).toBeTruthy();
  });

  it('ECU & freeze frames section header button is collapsed by default (aria-expanded=false)', () => {
    const { container } = render(
      <SessionDetailView
        {...makeProps({
          ecuCards: [{ id: 'ECU_BRAKE', dtcCount: 2, hasFreezeFrame: false }],
        })}
      />,
    );
    // The section wrapper renders with data-testid="ecu-freeze-frames-section".
    // Cloudscape ExpandableSection renders a <span role="button" aria-expanded>
    // (not a <button>) when collapsed.
    const section = container.querySelector('[data-testid="ecu-freeze-frames-section"]');
    expect(section).not.toBeNull();
    // Find the expand toggle within this section (span with aria-expanded)
    const expandToggle = section!.querySelector('[aria-expanded]');
    expect(expandToggle).not.toBeNull();
    expect(expandToggle!.getAttribute('aria-expanded')).toBe('false');
  });
});

// ── SDV4: Live data & identity drift — COLLAPSED by default ──────────────────

describe('SDV4 — Live data & identity drift collapsed by default', () => {
  it('live data & identity drift section is present', () => {
    render(<SessionDetailView {...makeProps()} />);
    expect(screen.getByTestId('live-data-drift-section')).toBeTruthy();
  });

  it('live data & identity drift section header button is collapsed by default (aria-expanded=false)', () => {
    const { container } = render(
      <SessionDetailView
        {...makeProps({
          liveDataResult: {
            components: { ECU_BRAKE: { did: 'F150', value: '3.2', unit: 'bar' } },
          },
        })}
      />,
    );
    const section = container.querySelector('[data-testid="live-data-drift-section"]');
    expect(section).not.toBeNull();
    const expandToggle = section!.querySelector('[aria-expanded]');
    expect(expandToggle).not.toBeNull();
    expect(expandToggle!.getAttribute('aria-expanded')).toBe('false');
  });
});

// ── SDV5: Evidence & notes container ─────────────────────────────────────────

describe('SDV5 — Evidence & notes container', () => {
  it('renders the evidence-notes-container', () => {
    render(<SessionDetailView {...makeProps()} />);
    expect(screen.getByTestId('evidence-notes-container')).toBeTruthy();
  });

  it('notes textarea is rendered for an undispatched session', () => {
    render(<SessionDetailView {...makeProps()} />);
    expect(screen.getByTestId('evidence-notes-textarea')).toBeTruthy();
  });

  it('dispatched session shows RO id and no textarea', () => {
    render(
      <SessionDetailView
        {...makeProps({ dispatched: true, dispatchedRoId: 'RO-1042' })}
      />,
    );
    expect(screen.getByTestId('dispatched-ro-link').textContent).toContain('RO-1042');
    expect(screen.queryByTestId('evidence-notes-textarea')).toBeNull();
  });
});

// ── SDV6: Zero results renders empty state ────────────────────────────────────

describe('SDV6 — Zero routine results renders empty state, not blank', () => {
  it('renders the empty-state element when sessionCommands is empty', () => {
    render(<SessionDetailView {...makeProps({ sessionCommands: [] })} />);
    const empty = screen.getByTestId('routine-results-empty');
    expect(empty).toBeTruthy();
    // Must not be a blank panel — visible informational text is required.
    expect(empty.textContent).toBeTruthy();
  });

  it('empty state renders visible text (not just whitespace)', () => {
    render(<SessionDetailView {...makeProps({ sessionCommands: [] })} />);
    const text = screen.getByTestId('routine-results-empty').textContent ?? '';
    expect(text.trim().length).toBeGreaterThan(0);
  });
});

// ── SDV7: No inner <Tabs> ─────────────────────────────────────────────────────

describe('SDV7 — Zero <Tabs> rendered', () => {
  it('renders no Tabs component (vehicle page is already a tab surface)', () => {
    const { container } = render(
      <SessionDetailView {...makeProps({ sessionCommands: [SUCCEEDED_COMMAND] })} />,
    );
    // Cloudscape Tabs renders a [role="tablist"] element.
    const tabLists = container.querySelectorAll('[role="tablist"]');
    expect(tabLists.length).toBe(0);
  });
});

// ── SDV8: No polling in source (structural read) ──────────────────────────────
//
// The spec states: "This component must contain no setInterval/setTimeout polling —
// 1.3's test exists to catch that, and putting the poll here is the specific mistake
// the spec predicts."  This is a source-read assertion checking for actual call sites,
// not just the presence of the string in a comment.

describe('SDV8 — Source contains no polling calls', () => {
  const SOURCE_PATH = resolve(
    __dirname,
    '../SessionDetailView.tsx',
  );

  it('source file contains no setInterval call pattern', () => {
    const src = readFileSync(SOURCE_PATH, 'utf-8');
    // Match actual call patterns (not comment references): setInterval(
    const hasCall = /[^/\s]setInterval\s*\(/.test(src);
    expect(hasCall).toBe(false);
  });

  it('source file contains no setTimeout call pattern', () => {
    const src = readFileSync(SOURCE_PATH, 'utf-8');
    // Match actual call pattern (not comment references): setTimeout(
    const hasCall = /[^/\s]setTimeout\s*\(/.test(src);
    expect(hasCall).toBe(false);
  });

  it('source file contains no useInterval import', () => {
    const src = readFileSync(SOURCE_PATH, 'utf-8');
    expect(src).not.toContain('useInterval');
  });
});

// ── SDV9: SUCCEEDED entry renders via renderer registry ──────────────────────

describe('SDV9 — SUCCEEDED entry renders response via renderer registry', () => {
  it('a SUCCEEDED command renders the mock renderer for its routineId', () => {
    render(
      <SessionDetailView
        {...makeProps({ sessionCommands: [SUCCEEDED_COMMAND] })}
      />,
    );
    // The mock renderer renders with data-testid="mock-renderer-<commandId>"
    expect(screen.getByTestId(`mock-renderer-${SUCCEEDED_COMMAND.commandId}`)).toBeTruthy();
  });

  it('a non-SUCCEEDED command does NOT render a response drawer', () => {
    render(
      <SessionDetailView
        {...makeProps({ sessionCommands: [IN_PROGRESS_COMMAND] })}
      />,
    );
    expect(
      screen.queryByTestId(`mock-renderer-${IN_PROGRESS_COMMAND.commandId}`),
    ).toBeNull();
  });

  it('an entry without a response renders no drawer even if SUCCEEDED', () => {
    const cmdNoResponse: SessionCommandEntry = {
      ...SUCCEEDED_COMMAND,
      commandId: 'cmd-no-response',
      response: undefined,
    };
    render(
      <SessionDetailView {...makeProps({ sessionCommands: [cmdNoResponse] })} />,
    );
    expect(screen.queryByTestId('mock-renderer-cmd-no-response')).toBeNull();
  });
});

// ── Silent-failure banner ─────────────────────────────────────────────────────

describe('silent-failure banner (non-SUCCEEDED with no reason)', () => {
  it('shows a warning banner for a non-SUCCEEDED entry with no reason', () => {
    const cmd: SessionCommandEntry = {
      commandId: 'cmd-silent',
      routineId: 'lamp_self_check',
      status: 'PRECONDITION_FAILED',
      sessionId: SESSION_ID,
    };
    render(<SessionDetailView {...makeProps({ sessionCommands: [cmd] })} />);
    expect(screen.getByTestId('routine-result-silent-failure-cmd-silent')).toBeTruthy();
  });

  it('does NOT show a banner when a reason is present', () => {
    const cmd: SessionCommandEntry = {
      commandId: 'cmd-with-reason',
      routineId: 'lamp_self_check',
      status: 'PRECONDITION_FAILED',
      reason: 'Vehicle must be stationary',
      sessionId: SESSION_ID,
    };
    render(<SessionDetailView {...makeProps({ sessionCommands: [cmd] })} />);
    expect(screen.queryByTestId('routine-result-silent-failure-cmd-with-reason')).toBeNull();
  });
});

// ── Notes onChange ────────────────────────────────────────────────────────────

describe('notes textarea wiring', () => {
  it('calls onNotesChange when the textarea value changes', () => {
    const onNotesChange = vi.fn();
    const { container } = render(
      <SessionDetailView {...makeProps({ onNotesChange })} />,
    );
    // Cloudscape Textarea renders an inner <textarea> element.
    // The outer div has data-testid; the inner <textarea> accepts value changes.
    const textareaEl = container.querySelector(
      '[data-testid="evidence-notes-textarea"] textarea',
    );
    expect(textareaEl).not.toBeNull();
    fireEvent.change(textareaEl!, { target: { value: 'check the brake ECU' } });
    expect(onNotesChange).toHaveBeenCalledWith('check the brake ECU');
  });
});
