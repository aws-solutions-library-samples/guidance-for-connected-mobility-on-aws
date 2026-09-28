// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Group 4.2 tests — spec 2026-09-01-cms-remote-diagnostics-sovd, Task 4.2
// Replaces the 3 it.todo skeletons from Task 1.3 with 8 required test bodies.
//
// HARD GATE H: Clear button enabled only when connectionStatus === 'connected'.
// HARD GATE I: Clear button enabled only after attestation checkbox is checked.
//              Checkbox resets to unchecked on every new modal open.
// user_email is sourced from Cognito claim (useAuth().user.email), NEVER from
// a user-editable field.

import React from 'react';
import { render, screen, fireEvent, waitFor, act } from '@testing-library/react';

// ── Module mocks (must be at top level before any imports) ──────────────────

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

vi.mock('@/config/api', () => ({
  getApiEndpoint: () => 'http://localhost/',
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
}));

// Mock authFetch so we can control every HTTP interaction
vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

import * as authFetchModule from '@/utils/authFetch';
import ClearDTCModal from '../ClearDTCModal';

const mockAuthFetch = vi.mocked(authFetchModule.authFetch);

// ── Shared fixture ────────────────────────────────────────────────────────────

const DTC_FIXTURE = {
  code: 'P0420',
  description: 'Catalyst System Efficiency Below Threshold',
  firstSeenAt: 1_700_000_000_000,
  occurrenceCount: 5,
  ecu: 'ECU_ENGINE',
};

const VEHICLE_ID = 'VEH-TEST-001';
const ATTESTATION_LABEL = 'I have verified the underlying repair is complete';

/** Helper: render the modal. connectionStatus defaults to 'connected' unless explicitly set. */
function renderConnected(opts: { onSuccess?: () => void; onDismiss?: () => void } = {}) {
  const onSuccess = opts.onSuccess ?? vi.fn();
  const onDismiss = opts.onDismiss ?? vi.fn();
  const utils = render(
    <ClearDTCModal
      visible={true}
      onDismiss={onDismiss}
      onSuccess={onSuccess}
      dtc={DTC_FIXTURE}
      vehicleId={VEHICLE_ID}
      connectionStatus="connected"
    />,
  );
  return { ...utils, onSuccess, onDismiss };
}

/** Helper: render with an explicit non-connected status (or no status at all). */
function renderWithStatus(
  connectionStatus: string | null | undefined,
  opts: { onSuccess?: () => void; onDismiss?: () => void } = {},
) {
  const onSuccess = opts.onSuccess ?? vi.fn();
  const onDismiss = opts.onDismiss ?? vi.fn();
  const props: Record<string, unknown> = {
    visible: true,
    onDismiss,
    onSuccess,
    dtc: DTC_FIXTURE,
    vehicleId: VEHICLE_ID,
  };
  // Only set connectionStatus if not undefined — lets us test the "prop absent" case
  // without the ?? operator converting undefined to 'connected'.
  if (connectionStatus !== undefined) {
    props.connectionStatus = connectionStatus;
  }
  const utils = render(<ClearDTCModal {...(props as React.ComponentProps<typeof ClearDTCModal>)} />);
  return { ...utils, onSuccess, onDismiss };
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

// ── 1. renders locked until attestation checked (HARD GATE I) ─────────────────

describe('ClearDTCModal', () => {
  it('renders locked until attestation checked', () => {
    renderConnected();

    // DTC summary fields must be visible
    expect(screen.getByText('P0420')).toBeInTheDocument();
    expect(screen.getByText('Catalyst System Efficiency Below Threshold')).toBeInTheDocument();
    expect(screen.getByText('ECU_ENGINE')).toBeInTheDocument();
    expect(screen.getByText('5')).toBeInTheDocument();

    // Checkbox must be UNCHECKED by default
    const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    expect(checkbox).not.toBeChecked();

    // Clear button must be DISABLED until checkbox is ticked (even when connected)
    const clearBtn = screen.getByTestId('clear-dtc-confirm-btn');
    expect(clearBtn).toBeDisabled();
  });

  // ── 2. Clear button disabled when disconnected (parametrized on 3 values) ──
  //
  // HARD GATE H: allow-list gate. Only 'connected' enables; fail-closed on
  // undefined, null, 'disconnected', or any other value.

  it.each([
    ['undefined (prop absent)', undefined    as unknown as string | null],
    ['null',                    null                                     ],
    ['disconnected',            'disconnected'                           ],
  ])(
    'Clear button disabled when connectionStatus is %s (even with checkbox checked)',
    (_label, status) => {
      renderWithStatus(status);

      // Tick the attestation checkbox to isolate the connection gate
      const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
      fireEvent.click(checkbox);
      expect(checkbox).toBeChecked();

      // Clear button must still be DISABLED because connectionStatus is not 'connected'
      const clearBtn = screen.getByTestId('clear-dtc-confirm-btn');
      expect(clearBtn).toBeDisabled();
    },
  );

  // ── 3. Clear button disabled when checkbox unchecked ─────────────────────

  it('Clear button disabled when checkbox unchecked (even when connected)', () => {
    renderConnected();

    // Attestation checkbox starts unchecked
    const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    expect(checkbox).not.toBeChecked();

    const clearBtn = screen.getByTestId('clear-dtc-confirm-btn');
    expect(clearBtn).toBeDisabled();
  });

  // ── 4. Clear button enabled when BOTH conditions are met ─────────────────

  it('Clear button enabled when checkbox is checked AND connectionStatus is connected', () => {
    renderConnected();

    const clearBtn = screen.getByTestId('clear-dtc-confirm-btn');
    expect(clearBtn).toBeDisabled(); // starts disabled

    const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    fireEvent.click(checkbox);

    expect(clearBtn).not.toBeDisabled(); // now enabled
  });

  // ── 5. emits attestation payload on confirm ──────────────────────────────

  it('emits attestation payload on confirm with correct body and user_email from cognito', async () => {
    mockAuthFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ commandId: 'cmd-001', status: 'SENT' }),
      text: () => Promise.resolve(''),
    } as Response);

    const { onSuccess, onDismiss } = renderConnected();

    // Check the attestation checkbox
    const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    fireEvent.click(checkbox);

    // Click Clear
    const clearBtn = screen.getByTestId('clear-dtc-confirm-btn');
    await act(async () => {
      fireEvent.click(clearBtn);
    });

    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledTimes(1);
    });

    // Verify the POST URL
    const [url, init] = mockAuthFetch.mock.calls[0];
    expect(String(url)).toContain(`/api/commands/${VEHICLE_ID}`);
    expect(init?.method).toBe('POST');

    // Parse and assert the request body
    const body = JSON.parse(String(init?.body));
    expect(body.command_type).toBe('clear_dtcs');
    expect(body.components).toEqual(['ECU_ENGINE']);

    // Attestation must carry the exact label text
    expect(body.attestation.text).toBe(ATTESTATION_LABEL);

    // user_email MUST come from the Cognito claim (mocked as operator@example.com)
    // It must NEVER be a user-editable value — sourced from useAuth().user.email
    expect(body.attestation.user_email).toBe('operator@example.com');

    // timestamp_ms must be a number
    expect(typeof body.attestation.timestamp_ms).toBe('number');

    // onSuccess and onDismiss should be called after successful POST
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledTimes(1);
      expect(onDismiss).toHaveBeenCalledTimes(1);
    });
  });

  // ── 6. displays server 403 verbatim ──────────────────────────────────────

  it('displays server 403 response body verbatim in Alert', async () => {
    const errorPayload = { error: 'not allowed' };
    mockAuthFetch.mockResolvedValueOnce({
      ok: false,
      status: 403,
      json: () => Promise.resolve(errorPayload),
      text: () => Promise.resolve(JSON.stringify(errorPayload)),
    } as Response);

    const { onSuccess } = renderConnected();

    // Tick checkbox to enable the button
    const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    fireEvent.click(checkbox);

    const clearBtn = screen.getByTestId('clear-dtc-confirm-btn');
    await act(async () => {
      fireEvent.click(clearBtn);
    });

    // An error message with the 403 body should appear
    await waitFor(() => {
      expect(screen.getByText(/not allowed/i)).toBeInTheDocument();
    });

    // onSuccess must NOT be called on an error
    expect(onSuccess).not.toHaveBeenCalled();
  });

  // ── 7. displays server 400 verbatim ──────────────────────────────────────

  it('displays server 400 response body verbatim in Alert', async () => {
    const errorPayload = { error: 'invalid request body' };
    mockAuthFetch.mockResolvedValueOnce({
      ok: false,
      status: 400,
      json: () => Promise.resolve(errorPayload),
      text: () => Promise.resolve(JSON.stringify(errorPayload)),
    } as Response);

    const { onSuccess } = renderConnected();

    const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    fireEvent.click(checkbox);

    const clearBtn = screen.getByTestId('clear-dtc-confirm-btn');
    await act(async () => {
      fireEvent.click(clearBtn);
    });

    await waitFor(() => {
      expect(screen.getByText(/invalid request body/i)).toBeInTheDocument();
    });

    expect(onSuccess).not.toHaveBeenCalled();
  });

  // ── 8. retries once on network error ─────────────────────────────────────
  //
  // First authFetch throws (network error); second returns 200.
  // Uses fake timers to fast-forward the 500 ms backoff.

  it('retries once on network error then succeeds', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });

    // First call: network failure
    mockAuthFetch.mockRejectedValueOnce(new Error('NetworkError: Failed to fetch'));

    // Second call (retry): success
    mockAuthFetch.mockResolvedValueOnce({
      ok: true,
      json: () => Promise.resolve({ commandId: 'cmd-retry-001', status: 'SENT' }),
      text: () => Promise.resolve(''),
    } as Response);

    const { onSuccess } = renderConnected();

    const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    fireEvent.click(checkbox);

    const clearBtn = screen.getByTestId('clear-dtc-confirm-btn');

    // Click (do NOT await fully yet — we need to control the timer)
    act(() => {
      fireEvent.click(clearBtn);
    });

    // Advance past the 500ms backoff delay
    await act(async () => {
      vi.advanceTimersByTime(600);
    });

    // Wait for the retry to complete
    await waitFor(() => {
      expect(mockAuthFetch).toHaveBeenCalledTimes(2);
    });

    // Both calls go to the same URL
    const urls = mockAuthFetch.mock.calls.map(([url]) => String(url));
    expect(urls[0]).toContain(`/api/commands/${VEHICLE_ID}`);
    expect(urls[1]).toContain(`/api/commands/${VEHICLE_ID}`);

    // After retry succeeds, onSuccess should be called
    await waitFor(() => {
      expect(onSuccess).toHaveBeenCalledTimes(1);
    });

    vi.useRealTimers();
  });

  // ── Bonus: checkbox resets to unchecked when modal re-opens ─────────────

  it('checkbox resets to unchecked when modal re-opens (visible false → true)', async () => {
    const { rerender } = renderConnected();

    // Tick the checkbox in first open
    const checkbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    fireEvent.click(checkbox);
    expect(checkbox).toBeChecked();

    // Close the modal
    rerender(
      <ClearDTCModal
        visible={false}
        onDismiss={vi.fn()}
        onSuccess={vi.fn()}
        dtc={DTC_FIXTURE}
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
      />,
    );

    // Re-open the modal
    rerender(
      <ClearDTCModal
        visible={true}
        onDismiss={vi.fn()}
        onSuccess={vi.fn()}
        dtc={DTC_FIXTURE}
        vehicleId={VEHICLE_ID}
        connectionStatus="connected"
      />,
    );

    // Checkbox must be unchecked again on re-open
    const reopenedCheckbox = screen.getByRole('checkbox', { name: ATTESTATION_LABEL });
    expect(reopenedCheckbox).not.toBeChecked();
  });
});
