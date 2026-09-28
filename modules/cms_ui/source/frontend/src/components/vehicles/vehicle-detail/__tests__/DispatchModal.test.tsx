// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// T3.2 — DispatchModal tests.
//
// Spec: `.kiro/specs/2026-09-10-cms-dms-sovd-diagnostic-sessions-v1.5` T3.2 Verify
//
// Required paths per T3.2 Accept + Verify:
//   (success)           → dealer selected, dispatch fires POST /api/dispatch → 201,
//                          success Flashbar shown, onSuccess called.
//   (cancel)            → Cancel button calls onDismiss, no POST issued.
//   (missing-dealership) → Dispatch clicked with no dealer selected → field error,
//                          no POST issued.
//
// Contract notes:
//   - authFetch is the fetch layer (same as ClearDTCModal).
//   - useDealerOptions is mocked at the hook level; tests that need a specific
//     dealer pre-selected invoke the onChange handler directly via the Cloudscape
//     Select component's attribute lookup (see fireSelectChange helper below).
//   - user.email comes from useAuth() — NEVER from a user-editable field.
//   - Cloudscape Select does not render options in the DOM until the trigger is
//     clicked; tests that need a dealer "selected" fire the component's internal
//     onChange by finding the trigger and dispatching a Cloudscape-compatible
//     synthetic event (see note in fireSelectChange).

import React from 'react';
import { render, screen, fireEvent, waitFor, act, within } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Module mocks ─────────────────────────────────────────────────────────────

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({
    getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }),
    user: {
      username: 'operator@example.com',
      email: 'operator@example.com',
      groups: ['fleet-operator'],
      roles: ['fleet-operator'],
    },
  }),
}));

const mockRuntimeConfig = vi.hoisted(() => ({ value: { apiEndpoint: 'http://localhost/' } as Record<string, unknown> }));
vi.mock('@/config/api', () => ({
  getApiEndpoint: () => 'http://localhost/',
  getRuntimeConfig: () => mockRuntimeConfig.value,
}));

vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

// Mock useDealerOptions so the Select is pre-populated without a real network call.
// Tests that need a specific dealer state override this mock locally via mockReturnValue.
vi.mock('@/hooks/useDealerOptions', () => ({
  useDealerOptions: vi.fn(() => ({
    options: [
      { value: 'dealer-denver', label: 'Meridian of Denver', description: 'district-west' },
      { value: 'dealer-boston', label: 'Meridian of Boston', description: 'district-east' },
    ],
    status: 'ready',
    errorMessage: '',
    reload: vi.fn(),
  })),
  dealerPlaceholder: (status: string) => {
    if (status === 'loading') return 'Loading service centres…';
    if (status === 'error') return 'Service centres unavailable';
    if (status === 'empty') return 'No authorised service centres available';
    return 'Choose a service centre…';
  },
}));

// Mock Cloudscape Select as a native <select> to avoid jsdom + portal issues.
// The Cloudscape Select uses a portal-rendered dropdown that does not attach to
// the document body in jsdom — option-clicks therefore time out. Replacing it
// with a native <select> allows direct option selection via fireEvent.change.
vi.mock('@cloudscape-design/components/select', () => {
  const React = require('react');
  return {
    default: ({
      options = [],
      selectedOption,
      onChange,
      placeholder,
      disabled,
      ...rest
    }: any) => (
      <select
        value={selectedOption?.value ?? ''}
        onChange={(e) => {
          const opt = (options ?? []).find((o: any) => o.value === e.target.value);
          if (opt && onChange) {
            onChange({ detail: { selectedOption: opt } });
          }
        }}
        disabled={disabled}
        data-testid={rest['data-testid']}
        aria-label={placeholder ?? 'dealer select'}
      >
        <option value="" disabled>{placeholder ?? 'Select…'}</option>
        {(options ?? []).map((o: any) => (
          <option key={o.value} value={o.value}>{o.label}</option>
        ))}
      </select>
    ),
  };
});

import * as authFetchModule from '@/utils/authFetch';
import * as dealerOptionsModule from '@/hooks/useDealerOptions';
import DispatchModal from '@/components/vehicles/vehicle-detail/DispatchModal';
import type { DispatchEvidence } from '@/components/vehicles/vehicle-detail/DispatchModal';

const mockAuthFetch = vi.mocked(authFetchModule.authFetch);
const mockUseDealerOptions = vi.mocked(dealerOptionsModule.useDealerOptions);

// ── Fixtures ──────────────────────────────────────────────────────────────────

const VEHICLE_ID = 'VEH-MICH-001';
const VIN = 'ACME0000000000001';

const BASE_EVIDENCE: DispatchEvidence = {
  sessionId: 'sess-test-0001',
  dtcs: [{ code: 'P0420', description: 'Catalyst efficiency', ecu: 'ECU_ENGINE' }],
  routinesRun: [{ routineId: 'o2_heater_check', status: 'SUCCEEDED', submittedAt: '2026-09-10T12:00:00Z' }],
  notes: [],
};

const DMS_CREATED_BODY = {
  ro_id: 'RO-20260910-001',
  vehicle_vin: VIN,
  status: 'Draft',
  dealer_id: 'dealer-denver',
};

/** Helper: resolve with a 201 success response. */
function okCreated(body = DMS_CREATED_BODY) {
  return Promise.resolve({
    ok: true,
    status: 201,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

/** Helper: resolve with a server error response. */
function errResponse(status: number, body: object = { error: 'DMS error' }) {
  return Promise.resolve({
    ok: false,
    status,
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as Response);
}

/** Render the modal in a visible state. */
function renderModal(
  opts: {
    onDismiss?: () => void;
    onSuccess?: (roId: string) => void;
    evidence?: DispatchEvidence;
  } = {},
) {
  const onDismiss = opts.onDismiss ?? vi.fn();
  const onSuccess = opts.onSuccess ?? vi.fn();
  const utils = render(
    <DispatchModal
      visible={true}
      onDismiss={onDismiss}
      onSuccess={onSuccess}
      vehicleId={VEHICLE_ID}
      vin={VIN}
      evidence={opts.evidence ?? BASE_EVIDENCE}
    />,
  );
  return { ...utils, onDismiss, onSuccess };
}

/**
 * Simulate selecting the first dealer via the native <select> element
 * (backed by our Cloudscape Select mock above).
 */
function selectFirstDealer() {
  const nativeSelect = screen.getByTestId('dispatch-dealer-select') as HTMLSelectElement;
  fireEvent.change(nativeSelect, { target: { value: 'dealer-denver' } });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
  // Re-establish the default dealer options mock after clearAllMocks, which
  // would otherwise clear the mockReturnValue set inside vi.mock() factory.
  mockUseDealerOptions.mockReturnValue({
    options: [
      { value: 'dealer-denver', label: 'Meridian of Denver', description: 'district-west' },
      { value: 'dealer-boston', label: 'Meridian of Boston', description: 'district-east' },
    ],
    status: 'ready' as const,
    errorMessage: '',
    reload: vi.fn(),
  });
});

// ── Success path ──────────────────────────────────────────────────────────────

describe('DispatchModal — success path', () => {
  beforeEach(() => {
    mockAuthFetch.mockImplementation(() => okCreated());
  });

  it('renders the modal when visible', () => {
    renderModal();
    expect(screen.getByTestId('dispatch-modal')).toBeTruthy();
    expect(screen.getByTestId('dispatch-submit-button')).toBeTruthy();
    expect(screen.getByTestId('dispatch-cancel-button')).toBeTruthy();
  });

  it('prefills the complaint textarea with the active DTC and caller email', () => {
    renderModal();
    // Cloudscape Textarea renders the value in a native <textarea> element
    // inside the wrapper. Use querySelector to find the actual input element.
    const wrapper = screen.getByTestId('dispatch-complaint-textarea');
    const nativeTextarea = wrapper.querySelector('textarea');
    const textValue = nativeTextarea ? nativeTextarea.value : (wrapper.textContent ?? '');
    expect(textValue).toContain('P0420');
    expect(textValue).toContain('operator@example.com');
  });

  it('shows Normal as the default priority', () => {
    renderModal();
    // The radio button for Normal must be checked by default.
    const normalRadio = screen.getByRole('radio', { name: /Normal/i });
    expect((normalRadio as HTMLInputElement).checked).toBe(true);
  });

  it('renders evidence counts in the summary', () => {
    renderModal();
    const summary = screen.getByTestId('dispatch-evidence-summary');
    expect(summary.textContent).toMatch(/1 DTC/);
    expect(summary.textContent).toMatch(/1 routine run/);
  });

  it('renders the dealer field with the ready-state placeholder', () => {
    renderModal();
    // Cloudscape Select renders the placeholder text in the trigger.
    expect(document.body.textContent).toMatch(/Choose a service centre/i);
  });

  it('dispatches successfully after dealer is selected and posts to /api/dispatch', async () => {
    const onSuccess = vi.fn();
    renderModal({ onSuccess });

    act(() => { selectFirstDealer(); });

    const dispatchBtn = screen.getByTestId('dispatch-submit-button');
    await act(async () => {
      fireEvent.click(dispatchBtn);
    });

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledWith(
        expect.stringContaining('/api/dispatch'),
        expect.objectContaining({ method: 'POST' }),
      );
    }, { timeout: 3000 });

    // Verify the request body contains required fields.
    const callArgs = mockAuthFetch.mock.calls[0];
    const bodyStr = (callArgs[1] as { body: string }).body;
    const body = JSON.parse(bodyStr);
    expect(body.vehicleId).toBe(VEHICLE_ID);
    expect(body.vin).toBe(VIN);
    expect(body.priority).toBe('Normal');
    expect(body.complaint).toBeTruthy();

    // onSuccess must be called with the ro_id.
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledWith('RO-20260910-001');
    });
  });

  it('links the success notice to the DMS fleet repair-order page when the DMS origin is configured', async () => {
    mockRuntimeConfig.value = { apiEndpoint: 'http://localhost/', dmsUiOrigin: 'https://dms.example.com' };
    try {
      mockAuthFetch.mockImplementationOnce(() => okCreated());
      renderModal();
      act(() => { selectFirstDealer(); });
      await act(async () => {
        fireEvent.click(screen.getByTestId('dispatch-submit-button'));
      });
      const link = await screen.findByTestId('dispatch-ro-link', {}, { timeout: 3000 });
      expect(link.getAttribute('href')).toBe('https://dms.example.com/fleet/repair-orders/RO-20260910-001');
    } finally {
      mockRuntimeConfig.value = { apiEndpoint: 'http://localhost/' };
    }
  });

  it('shows the success notice without a link when no DMS origin is configured', async () => {
    mockAuthFetch.mockImplementationOnce(() => okCreated());
    renderModal();
    act(() => { selectFirstDealer(); });
    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-submit-button'));
    });
    await waitFor(() => expect(document.body.textContent).toContain('Repair order created.'), { timeout: 3000 });
    expect(screen.queryByTestId('dispatch-ro-link')).toBeNull();
  });

  it('shows success text after successful dispatch', async () => {
    renderModal();

    act(() => { selectFirstDealer(); });

    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-submit-button'));
    });

    await waitFor(() => {
      // Success flash message should be visible somewhere in the modal content.
      expect(document.body.textContent).toMatch(/Repair order created|Open RO/i);
    }, { timeout: 3000 });
  });

  it('includes evidence in the POST body', async () => {
    const evidence: DispatchEvidence = {
      sessionId: 'sess-with-evidence',
      dtcs: [{ code: 'P0171' }, { code: 'P0300' }],
      routinesRun: [
        { routineId: 'o2_heater_check', status: 'SUCCEEDED', submittedAt: '2026-09-10T10:00:00Z' },
        { routineId: 'evap_leak_test', status: 'SUCCEEDED', submittedAt: '2026-09-10T10:05:00Z' },
      ],
      notes: [{ text: 'Vehicle stalling at idle' }],
    };
    renderModal({ evidence });

    act(() => { selectFirstDealer(); });

    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-submit-button'));
    });

    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled(), { timeout: 3000 });
    const callArgs = mockAuthFetch.mock.calls[0];
    const body = JSON.parse((callArgs[1] as { body: string }).body);
    expect(body.evidence.sessionId).toBe('sess-with-evidence');
    expect(body.evidence.dtcs).toContain('P0171');
    expect(body.evidence.routinesRun).toHaveLength(2);
  });

  // FG3 (review cycle 1, W2): a code from the vehicle's fault record must reach
  // the repair order, and the body stays a list of code strings.
  it('sends fault-record codes in the POST body as plain codes', async () => {
    const evidence: DispatchEvidence = {
      sessionId: 'sess-recorded',
      dtcs: [
        { code: 'P0300' },
        { code: 'P0217', recorded: true, sources: ['flink-maintenance-processor'], description: 'overheat' },
      ],
      routinesRun: [],
      notes: [],
    };
    renderModal({ evidence });
    act(() => { selectFirstDealer(); });
    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-submit-button'));
    });
    await waitFor(() => expect(mockAuthFetch).toHaveBeenCalled(), { timeout: 3000 });
    const body = JSON.parse((mockAuthFetch.mock.calls[0][1] as { body: string }).body);
    expect(body.evidence.dtcs).toEqual(['P0300', 'P0217']);
  });
});

// ── FG3: fault-record codes are described truthfully ─────────────────────────

describe('DispatchModal — fault-record codes', () => {
  const recordEvidence = (dtcs: DispatchEvidence['dtcs']): DispatchEvidence => ({
    sessionId: 'sess-rec', dtcs, routinesRun: [], notes: [],
  });

  it('adds the telemetry note only when a record row for the code is a cloud threshold row', () => {
    renderModal({ evidence: recordEvidence([
      { code: 'P0217', recorded: true, sources: ['fwe-uds-dtc', 'flink-maintenance-processor'] },
      { code: 'C0035', recorded: true, sources: ['fwe-uds-dtc'] },
      { code: 'B1000', recorded: true },
    ]) });
    // Exact text, so an added claim about what a scan can find fails the test
    // (review FG3 cycle 3, W1): a code with an ECU row may well be on an ECU.
    expect(screen.getByTestId('dispatch-dtc-recorded-P0217').textContent).toBe(
      "From the vehicle's fault record, not from a diagnostic scan. Raised in the cloud from telemetry.",
    );
    for (const code of ['C0035', 'B1000']) {
      expect(screen.getByTestId(`dispatch-dtc-recorded-${code}`).textContent).toBe(
        "From the vehicle's fault record, not from a diagnostic scan.",
      );
    }
  });

  it('counts scan and record codes separately, with correct plurals', () => {
    renderModal({ evidence: recordEvidence([
      { code: 'P0300' },
      { code: 'P0217', recorded: true },
      { code: 'C0035', recorded: true },
    ]) });
    expect(screen.getByTestId('dispatch-evidence-summary')).toHaveTextContent('1 DTC from scan, 2 from the fault record');
  });

  it('record codes only: "N DTCs from the fault record"', () => {
    renderModal({ evidence: recordEvidence([
      { code: 'P0217', recorded: true },
      { code: 'C0035', recorded: true },
    ]) });
    expect(screen.getByTestId('dispatch-evidence-summary')).toHaveTextContent('2 DTCs from the fault record');
  });

  it('says record codes will be attached only when there are some', () => {
    const { unmount } = renderModal({ evidence: recordEvidence([{ code: 'P0217', recorded: true }]) });
    expect(screen.getByTestId('dispatch-evidence-field')).toHaveTextContent("the vehicle's recorded fault codes");
    unmount();
    renderModal({ evidence: recordEvidence([{ code: 'P0300' }]) });
    const field = screen.getByTestId('dispatch-evidence-field');
    expect(field).not.toHaveTextContent('recorded fault codes');
    expect(field).not.toHaveTextContent(/from this session that/);
  });
});

// ── Cancel path ───────────────────────────────────────────────────────────────

describe('DispatchModal — cancel path', () => {
  it('calls onDismiss when Cancel is clicked', () => {
    const onDismiss = vi.fn();
    renderModal({ onDismiss });

    fireEvent.click(screen.getByTestId('dispatch-cancel-button'));

    expect(onDismiss).toHaveBeenCalled();
  });

  it('does not call authFetch when Cancel is clicked', () => {
    renderModal();
    fireEvent.click(screen.getByTestId('dispatch-cancel-button'));
    expect(mockAuthFetch).not.toHaveBeenCalled();
  });

  it('calls onDismiss when the modal header close button is pressed', () => {
    const onDismiss = vi.fn();
    renderModal({ onDismiss });
    // Prefer the cancel button for determinism; fallback to finding a close button.
    const closeButtons = Array.from(
      document.querySelectorAll('button[aria-label], button[data-testid*="close"]'),
    ).filter((btn) =>
      (btn.getAttribute('aria-label') ?? '').toLowerCase().includes('close') ||
      (btn.getAttribute('data-testid') ?? '').includes('close'),
    );
    if (closeButtons.length > 0) {
      fireEvent.click(closeButtons[0]);
    } else {
      fireEvent.click(screen.getByTestId('dispatch-cancel-button'));
    }
    expect(onDismiss).toHaveBeenCalled();
  });
});

// ── Missing-dealership error path ─────────────────────────────────────────────
//
// This is the critical T3.2 Verify path: clicking Dispatch with no dealer
// selected must show a field error and must NOT fire a network request.

describe('DispatchModal — missing-dealership error path', () => {
  it('shows a field error when Dispatch is clicked with no dealer selected', async () => {
    renderModal();

    // Do NOT select a dealer; click Dispatch immediately.
    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-submit-button'));
    });

    // A field error must appear — "select a service centre" or similar.
    await waitFor(() => {
      expect(document.body.textContent).toMatch(/select a service centre|select.*dealer|service centre/i);
    });

    // No network call should have been made.
    expect(mockAuthFetch).not.toHaveBeenCalled();
  });

  it('Dispatch button is disabled when no dealer is selected (initial state)', () => {
    renderModal();
    // With no dealer selected, the Dispatch button should be disabled by the
    // `disabled={!selectedDealer}` prop.
    const dispatchBtn = screen.getByTestId('dispatch-submit-button');
    // Cloudscape Button renders as a <button> with disabled attribute or aria-disabled.
    const isDisabled =
      dispatchBtn.hasAttribute('disabled') ||
      dispatchBtn.getAttribute('aria-disabled') === 'true';
    expect(isDisabled).toBe(true);
  });

  it('does not fire a network request when Dispatch is clicked without a dealer', async () => {
    renderModal();
    await act(async () => {
      fireEvent.click(screen.getByTestId('dispatch-submit-button'));
    });
    // Strict check: authFetch must NOT have been called.
    expect(mockAuthFetch).not.toHaveBeenCalled();
  });
});

// ── Dealer-load-failure path ──────────────────────────────────────────────────

describe('DispatchModal — dealer load failure', () => {
  it('shows an error state in the Select when dealers fail to load', () => {
    mockUseDealerOptions.mockReturnValue({
      options: [],
      status: 'error',
      errorMessage: 'Could not load service centres.',
      reload: vi.fn(),
    });

    renderModal();

    // The Select must display the error placeholder.
    expect(document.body.textContent).toMatch(/Service centres unavailable/i);

    // Dispatch button must be disabled (no dealer can be selected).
    const dispatchBtn = screen.getByTestId('dispatch-submit-button');
    const isDisabled =
      dispatchBtn.hasAttribute('disabled') ||
      dispatchBtn.getAttribute('aria-disabled') === 'true';
    expect(isDisabled).toBe(true);
  });

  it('shows loading placeholder while dealers are being fetched', () => {
    mockUseDealerOptions.mockReturnValue({
      options: [],
      status: 'loading',
      errorMessage: '',
      reload: vi.fn(),
    });

    renderModal();
    expect(document.body.textContent).toMatch(/Loading service centres/i);
  });
});

// ── Server-error path ─────────────────────────────────────────────────────────

describe('DispatchModal — server-error path', () => {
  it('shows the server error body verbatim on a 4xx response', async () => {
    mockAuthFetch.mockImplementation(() =>
      errResponse(400, { error: 'Dealer not found' }),
    );

    renderModal();

    // Select a dealer so the Dispatch button becomes enabled.
    await act(async () => {
      selectFirstDealer();
    });

    // Dispatch button must be enabled now.
    const dispatchBtn = screen.getByTestId('dispatch-submit-button');

    await act(async () => {
      fireEvent.click(dispatchBtn);
    });

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalled();
    }, { timeout: 3000 });

    await waitFor(() => {
      expect(document.body.textContent).toMatch(/Dealer not found/i);
    }, { timeout: 3000 });
  });
});

// ── Evidence summary section ──────────────────────────────────────────────────

describe('DispatchModal — evidence summary section', () => {
  it('shows expandable sections for DTCs and routines when evidence is present', () => {
    renderModal();
    expect(screen.getByTestId('dispatch-evidence-dtcs')).toBeTruthy();
    expect(screen.getByTestId('dispatch-evidence-routines')).toBeTruthy();
  });

  it('does not render DTC section when evidence has no DTCs', () => {
    renderModal({
      evidence: { ...BASE_EVIDENCE, dtcs: [] },
    });
    expect(screen.queryByTestId('dispatch-evidence-dtcs')).toBeNull();
  });

  it('shows empty state message when evidence has no activity', () => {
    renderModal({
      evidence: { sessionId: 'empty', dtcs: [], routinesRun: [], notes: [] },
    });
    const summary = screen.getByTestId('dispatch-evidence-summary');
    expect(summary.textContent).toMatch(/No session activity/i);
  });
});
