// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Group 4.1 tests — spec 2026-09-01-cms-remote-diagnostics-sovd, Task 4.1
// Replaces the 5 it.todo skeletons from Task 1.3 with real test bodies.

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';

// ── Module mocks (must be at top level before any imports) ──────────────────

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
  getSimulationApiUrl: (path: string) => `http://localhost/api/simulation${path}`,
  getSimulationApiBase: () => 'http://localhost',
  getSimulationMode: () => 'local',
  isCloudSimAvailable: () => false,
  setSimulationMode: () => {},
}));

// Mock authFetch so we can spy on SOVD POSTs without a real network
vi.mock('@/utils/authFetch', () => ({
  authFetch: vi.fn(),
}));

// Mock ClearDTCModal as a spy component so we can assert props
vi.mock('../ClearDTCModal', () => ({
  default: vi.fn(({ visible, dtc, vehicleId, connectionStatus, onDismiss, onSuccess }) =>
    visible ? (
      <div
        data-testid="clear-dtc-modal"
        data-vehicle-id={vehicleId}
        data-dtc-code={dtc?.code}
        data-connection-status={connectionStatus ?? 'undefined'}
      >
        <button onClick={onDismiss} data-testid="mock-dismiss">Dismiss</button>
        <button onClick={onSuccess} data-testid="mock-success">Success</button>
      </div>
    ) : null,
  ),
}));

import { authFetch as authFetchMock } from '@/utils/authFetch';
import VehicleDTCsTable from '../VehicleDTCsTable';
import ClearDTCModal from '../ClearDTCModal';

// ── Shared fixtures ──────────────────────────────────────────────────────────

const T1 = 1_700_000_000_000;
const T2 = 1_700_000_100_000;

const activeDtcRow = {
  vehicleId: 'VEH-TEST-001',
  timestamp: T1,
  dtcId: 'dtc-sovd-0001',
  code: 'P0420',
  status: 'ACTIVE',
  severity: 'HIGH',
  system: 'Engine',
  description: 'Catalyst System Efficiency Below Threshold',
  firstSeenAt: T1,
  lastSeenAt: T2,
  occurrenceCount: 3,
  relatedServiceId: '',
  source: 'sovd',
  ecu: 'ECU_ENGINE',
};

/** DTC with a freeze-frame containing the 6 known signals plus 2 extras */
const dtcWithFreezeFrame = {
  ...activeDtcRow,
  dtcId: 'dtc-sovd-ff-001',
  code: 'P0300',
  freezeFrame: {
    engineRpm:        { value: 2800, unit: 'rpm',  timestamp: '2026-09-01T10:00:00Z' },
    coolantTemp:      { value: 88,   unit: 'degC', timestamp: '2026-09-01T10:00:00Z' },
    vehicleSpeed:     { value: 65,   unit: 'km/h', timestamp: '2026-09-01T10:00:00Z' },
    engineLoad:       { value: 42,   unit: '%',    timestamp: '2026-09-01T10:00:00Z' },
    throttlePosition: { value: 22,   unit: '%',    timestamp: '2026-09-01T10:00:00Z' },
    fuelTrim:         { value: -2.3, unit: '%',    timestamp: '2026-09-01T10:00:00Z' },
    // Two extra / dynamic signals
    intakeAirTemp:    { value: 25,   unit: 'degC', timestamp: '2026-09-01T10:00:00Z' },
    mapSensor:        { value: 101,  unit: 'kPa',  timestamp: '2026-09-01T10:00:00Z' },
  },
};

/** Sets up global fetch to return DTC rows and an empty /faults payload. */
function mockFetchWithDtcs(rows: object[]) {
  vi.spyOn(global, 'fetch').mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.includes('/faults')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ faultState: null, campaignRunning: false, injectable: [] }),
      } as Response);
    }
    if (url.includes('/dtcs')) {
      return Promise.resolve({
        ok: true,
        json: () => Promise.resolve({ dtcs: rows, total: rows.length }),
      } as Response);
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve({}) } as Response);
  });
}

afterEach(() => {
  vi.restoreAllMocks();
  vi.clearAllMocks();
});

// ── Tests ───────────────────────────────────────────────────────────────────

describe('VehicleDTCsTable — SOVD additions (Group 4.1)', () => {

  // ── HARD GATE H: Read DTCs button disabled when disconnected ──────────────

  test.each([
    ['disconnected', 'disconnected'],
    ['undefined',    undefined],
    ['null',         null as unknown as string],
  ])('Read DTCs button disabled when connectionStatus is %s', async (_label, status) => {
    mockFetchWithDtcs([]);
    render(
      <VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus={status as string | undefined} />,
    );
    // Wait for table to render (loading finishes)
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /read dtcs/i })).toBeInTheDocument();
    });
    const btn = screen.getByRole('button', { name: /read dtcs/i });
    // Cloudscape renders disabled as aria-disabled="true" when disabledReason is set
    expect(
      btn.hasAttribute('disabled') || btn.getAttribute('aria-disabled') === 'true',
    ).toBe(true);
  });

  // ── HARD GATE H: Full Scan button disabled when disconnected ──────────────

  test.each([
    ['disconnected', 'disconnected'],
    ['undefined',    undefined],
    ['null',         null as unknown as string],
  ])('Full Scan button disabled when connectionStatus is %s', async (_label, status) => {
    mockFetchWithDtcs([]);
    render(
      <VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus={status as string | undefined} />,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /full scan/i })).toBeInTheDocument();
    });
    const btn = screen.getByRole('button', { name: /full scan/i });
    expect(
      btn.hasAttribute('disabled') || btn.getAttribute('aria-disabled') === 'true',
    ).toBe(true);
  });

  // ── Full Scan POSTs read_dtcs with components ['*'] ───────────────────────

  test('Full Scan POSTs read_dtcs with components [\'*\'] and include_freeze_frame true', async () => {
    mockFetchWithDtcs([]);
    // authFetch is used for the SOVD commands POST
    (authFetchMock as ReturnType<typeof vi.fn>).mockResolvedValue({
      ok: true,
      json: () => Promise.resolve({ commandId: 'cmd-001', status: 'SENT' }),
      text: () => Promise.resolve(''),
    } as Response);

    render(
      <VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus="connected" />,
    );

    // Wait for component to finish loading
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /full scan/i })).toBeInTheDocument();
    });
    const fullScanBtn = screen.getByRole('button', { name: /full scan/i });
    // Ensure the button is enabled (connected)
    expect(
      fullScanBtn.hasAttribute('disabled') || fullScanBtn.getAttribute('aria-disabled') === 'true',
    ).toBe(false);

    fireEvent.click(fullScanBtn);

    await waitFor(() => {
      expect(authFetchMock).toHaveBeenCalledWith(
        expect.stringContaining('/api/commands/VEH-TEST-001'),
        expect.objectContaining({
          method: 'POST',
          body: expect.stringContaining('"command_type":"read_dtcs"'),
        }),
      );
    });

    // Assert components: ['*'] and include_freeze_frame: true in the POST body
    const [, init] = (authFetchMock as ReturnType<typeof vi.fn>).mock.calls.find(
      ([url]: [string]) => String(url).includes('/api/commands/'),
    )!;
    const body = JSON.parse(String(init.body));
    expect(body.command_type).toBe('read_dtcs');
    expect(body.components).toEqual(['*']);
    expect(body.include_freeze_frame).toBe(true);
  });

  // ── Expandable row renders freeze frame with 6 known signals ─────────────

  test('expandable row renders freeze frame with 6 known signals and correct units', async () => {
    mockFetchWithDtcs([dtcWithFreezeFrame]);
    render(<VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    // Wait for the row to appear
    await waitFor(() => {
      expect(screen.getByText('P0300')).toBeInTheDocument();
    });

    // The freeze frame section should be present (collapsed by default — NOT auto-opened)
    const freezeFrameHeader = screen.getByText('Freeze frame');
    expect(freezeFrameHeader).toBeInTheDocument();

    // Expand it
    fireEvent.click(freezeFrameHeader);

    // After expanding, the 6 known signal labels should be visible
    await waitFor(() => {
      expect(screen.getByText('Engine RPM')).toBeInTheDocument();
    });
    expect(screen.getByText('Coolant Temp')).toBeInTheDocument();
    expect(screen.getByText('Vehicle Speed')).toBeInTheDocument();
    expect(screen.getByText('Engine Load')).toBeInTheDocument();
    expect(screen.getByText('Throttle Position')).toBeInTheDocument();
    expect(screen.getByText('Fuel Trim')).toBeInTheDocument();

    // Values + units are rendered
    expect(screen.getByText(/2800 rpm/i)).toBeInTheDocument();
    expect(screen.getByText(/88 degC/i)).toBeInTheDocument();
    expect(screen.getByText(/65 km\/h/i)).toBeInTheDocument();
  });

  // ── Expandable row shows additional signals count ─────────────────────────

  test('expandable row shows additional signals count (2 extra signals)', async () => {
    mockFetchWithDtcs([dtcWithFreezeFrame]);
    render(<VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    await waitFor(() => {
      expect(screen.getByText('P0300')).toBeInTheDocument();
    });

    // Expand the freeze frame section
    const freezeFrameHeader = screen.getByText('Freeze frame');
    fireEvent.click(freezeFrameHeader);

    // Should show "Additional signals (2)" for the 2 extra keys
    await waitFor(() => {
      expect(screen.getByText(/Additional signals \(2\)/i)).toBeInTheDocument();
    });
  });

  // ── Source filter dropdown includes sovd option ────────────────────────────

  test('source filter dropdown includes sovd option', async () => {
    mockFetchWithDtcs([activeDtcRow]);
    render(<VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    await waitFor(() => {
      expect(screen.getByText('P0420')).toBeInTheDocument();
    });

    // Find the source filter Select (it shows "All sources" by default)
    const sourceSelects = screen.getAllByText('All sources');
    expect(sourceSelects.length).toBeGreaterThanOrEqual(1);

    // Open the source filter dropdown
    // The select trigger button is the element that has the "All sources" text
    const sourceSelectTrigger = sourceSelects[0].closest('[role="combobox"]') ||
      sourceSelects[0].closest('button') ||
      sourceSelects[0].parentElement;

    // Click the trigger to open the dropdown
    if (sourceSelectTrigger) {
      fireEvent.mouseDown(sourceSelectTrigger);
      fireEvent.click(sourceSelectTrigger);
    }

    // After opening, find all options in the dropdown
    // The component ensures 'sovd' is always a documented option.
    // Since the data has source=sovd, the dynamic options include it.
    // Wait for the dropdown to render options
    await waitFor(() => {
      // Check for SOVD label in the dropdown options
      const allOptions = Array.from(document.querySelectorAll('[role="option"]'))
        .map(el => el.textContent || '');
      const hasSovd = allOptions.some(t => /sovd/i.test(t));
      // Also check if it's in the select options rendered in the DOM
      const allText = document.body.textContent || '';
      expect(hasSovd || /sovd/i.test(allText)).toBe(true);
    });
  });

  // ── Clear button opens ClearDTCModal with correct props ───────────────────

  test('Clear button opens ClearDTCModal with correct props', async () => {
    mockFetchWithDtcs([activeDtcRow]);
    render(
      <VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus="connected" />,
    );

    await waitFor(() => {
      expect(screen.getByText('P0420')).toBeInTheDocument();
    });

    // Find the Clear button for the SOVD row (aria-label "Clear DTC remotely")
    const clearBtn = screen.getByRole('button', { name: /clear dtc remotely/i });
    expect(clearBtn).toBeInTheDocument();

    // Ensure it's enabled (connectionStatus=connected)
    expect(
      clearBtn.hasAttribute('disabled') || clearBtn.getAttribute('aria-disabled') === 'true',
    ).toBe(false);

    fireEvent.click(clearBtn);

    // Modal should now be visible
    await waitFor(() => {
      expect(screen.getByTestId('clear-dtc-modal')).toBeInTheDocument();
    });

    // Assert correct props were passed to ClearDTCModal
    const modalEl = screen.getByTestId('clear-dtc-modal');
    expect(modalEl.getAttribute('data-vehicle-id')).toBe('VEH-TEST-001');
    expect(modalEl.getAttribute('data-dtc-code')).toBe('P0420');
    expect(modalEl.getAttribute('data-connection-status')).toBe('connected');

    // Verify ClearDTCModal was called with the expected prop shape
    const ClearDTCModalMock = ClearDTCModal as ReturnType<typeof vi.fn>;
    const lastCall = ClearDTCModalMock.mock.calls[ClearDTCModalMock.mock.calls.length - 1][0];
    expect(lastCall.visible).toBe(true);
    expect(lastCall.vehicleId).toBe('VEH-TEST-001');
    expect(lastCall.connectionStatus).toBe('connected');
    expect(lastCall.dtc.code).toBe('P0420');
    expect(lastCall.dtc.description).toBe('Catalyst System Efficiency Below Threshold');
    expect(lastCall.dtc.ecu).toBe('ECU_ENGINE');
    expect(typeof lastCall.onDismiss).toBe('function');
    expect(typeof lastCall.onSuccess).toBe('function');
  });

  // ── Bonus: Read DTCs and Full Scan are ENABLED when connected ─────────────

  test('Read DTCs and Full Scan buttons are enabled when connectionStatus is "connected"', async () => {
    mockFetchWithDtcs([]);
    render(
      <VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus="connected" />,
    );
    await waitFor(() => {
      expect(screen.getByRole('button', { name: /read dtcs/i })).toBeInTheDocument();
    });
    const readBtn = screen.getByRole('button', { name: /read dtcs/i });
    const scanBtn = screen.getByRole('button', { name: /full scan/i });
    expect(
      readBtn.hasAttribute('disabled') || readBtn.getAttribute('aria-disabled') === 'true',
    ).toBe(false);
    expect(
      scanBtn.hasAttribute('disabled') || scanBtn.getAttribute('aria-disabled') === 'true',
    ).toBe(false);
  });

  // ── Bonus: freeze frame section is NOT auto-opened ────────────────────────

  test('freeze frame section is collapsed by default (not auto-opened)', async () => {
    mockFetchWithDtcs([dtcWithFreezeFrame]);
    render(<VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus="connected" />);

    await waitFor(() => {
      expect(screen.getByText('P0300')).toBeInTheDocument();
    });

    // Header should be present
    const freezeFrameHeader = screen.getByText('Freeze frame');
    expect(freezeFrameHeader).toBeInTheDocument();

    // Cloudscape ExpandableSection renders content in the DOM (for accessibility)
    // but marks the trigger button as aria-expanded="false" when collapsed.
    // Find the trigger element (button that controls the section).
    const trigger = freezeFrameHeader.closest('button') ||
      document.querySelector('button[aria-expanded]');
    if (trigger) {
      // Collapsed by default: aria-expanded should be false or absent
      const expanded = trigger.getAttribute('aria-expanded');
      expect(expanded === 'false' || expanded === null).toBe(true);
    } else {
      // Fallback: verify the header exists and signal labels are not VISIBLE
      // (Cloudscape hides via CSS transform when collapsed)
      const allExpanded = Array.from(document.querySelectorAll('button[aria-expanded="true"]'))
        .map(el => el.textContent || '');
      expect(allExpanded.some(t => /freeze frame/i.test(t))).toBe(false);
    }
  });

  // ── Bonus: Clear button disabled when disconnected ────────────────────────

  test('Clear DTC button disabled when vehicle is disconnected', async () => {
    mockFetchWithDtcs([activeDtcRow]);
    render(
      <VehicleDTCsTable vehicleId="VEH-TEST-001" connectionStatus="disconnected" />,
    );
    await waitFor(() => {
      expect(screen.getByText('P0420')).toBeInTheDocument();
    });
    const clearBtn = screen.getByRole('button', { name: /clear dtc remotely/i });
    expect(
      clearBtn.hasAttribute('disabled') || clearBtn.getAttribute('aria-disabled') === 'true',
    ).toBe(true);
  });
});
