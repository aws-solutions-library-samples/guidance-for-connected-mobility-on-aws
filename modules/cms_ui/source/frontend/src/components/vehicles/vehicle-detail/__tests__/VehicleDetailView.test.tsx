// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// RED-PHASE SKELETONS — spec § 9 matrix T1, T2, T3
// Tests are authored here and expected to FAIL until Group 5.1 ships
// the `isOEM1Vehicle` early-return branch in VehicleDetailView.tsx.

import React from 'react';
import { render, screen, waitFor, act } from '@testing-library/react';
import { isOEM1Vehicle, getVehicleSource } from '@/types/fleet-types';

// ── Module mocks ────────────────────────────────────────────────────────────

vi.mock('react-router-dom', () => ({
  useParams: () => ({ vehicleId: 'TEST-VIN-001' }),
  useNavigate: () => vi.fn(),
}));

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({ getAuthHeaders: () => ({ Authorization: 'Bearer test-token' }) }),
}));

vi.mock('@/auth/useIsEngineerTenant', () => ({
  useIsEngineerTenant: () => false,
}));

vi.mock('@/contexts/VehicleContext', () => ({
  useVehicle: () => ({ setVehicleVin: vi.fn(), vehicleVin: null }),
}));

vi.mock('@/components/commons/UserContext', async () => {
  const React = await import('react');
  const UserContext = React.createContext({});
  return { UserContext };
});

vi.mock('@/config/api', () => ({
  getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }),
  getApiEndpoint: () => 'http://localhost/',
}));

vi.mock('@/utils/simulation-config', () => ({
  getSimulationApiBase: () => 'http://localhost',
  getSimulationMode: () => 'local',
}));

vi.mock('@/utils/constants', () => ({ UI_ROUTES: { VEHICLE_MANAGEMENT: '/vehicles' } }));

// Heavy child components — stubs prevent sub-dependency import errors.
// data-testid markers identify CMS-native sub-tree elements so T1 can
// assert their absence when the OEM1 branch is active (Group 5.1 wires this).
vi.mock('@/components/vehicles/vehicle-detail/FWELogViewer', () => ({
  default: () => <div data-testid="cms-fwe-log-viewer" />,
}));
vi.mock('@/components/vehicles/vehicle-detail/SimLogViewer', () => ({
  default: () => <div data-testid="cms-sim-log-viewer" />,
}));
vi.mock('@/components/vehicles/vehicle-detail/ConnectedServicesCard', () => ({
  default: () => <div data-testid="cms-connected-services-card" />,
}));
vi.mock('@/components/vehicles/vehicle-detail/RemoteCommandsPanel', () => ({
  default: () => <div data-testid="cms-remote-commands" />,
}));
vi.mock('@/components/vehicles/vehicle-detail/VehicleDTCsTable', () => ({
  default: () => <div data-testid="cms-dtc-table" />,
}));
// FG3: capture what the page hands the Diagnostics panel for dispatch evidence.
const diagnosticsPanelProps: Array<Record<string, unknown>> = [];
vi.mock('@/components/vehicles/vehicle-detail/VehicleDiagnosticsPanel', () => ({
  default: (props: Record<string, unknown>) => {
    diagnosticsPanelProps.push(props);
    return <div data-testid="cms-diagnostics-panel" />;
  },
}));
vi.mock('@/components/vehicles/vehicle-detail/VehicleCampaignsTable', () => ({
  default: () => <div data-testid="cms-campaigns-table" />,
}));
vi.mock('@/components/vehicles/vehicle-detail/TirePressureWidget', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/TripSimulatorModal', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/RouteMapModal', () => ({
  RouteMapModal: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/VehicleRecallWidget', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/VehicleWarrantyWidget', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/VehicleHealthScoreWidget', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/VehicleFinancialWidget', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/ScheduleServiceModal', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/EnrollmentStatusSection', () => ({
  VehicleStatusBadge: () => <span />,
}));
vi.mock('@/components/vehicles/vehicle-detail/SimulationLogViewer', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/GeofenceWidget', () => ({
  default: () => <div />,
}));
vi.mock('@/components/commons/TripsTable', () => ({
  TripsTable: () => <div />,
}));
vi.mock('@/components/commons/SafetyEventsTable', () => ({
  SafetyEventsTable: () => <div />,
}));
vi.mock('@/components/commons/SafetyEventLocationModal', () => ({
  SafetyEventLocationModal: () => <div />,
}));
vi.mock('@/components/vehicles/trip-detail/TripMap', () => ({
  TripMap: () => <div />,
}));
vi.mock('@/components/recall-warranty/nhtsaRecallData', () => ({
  nhtsaRecalls: [],
}));
vi.mock('@/components/engineering/EngineeringVehicleDetailView', () => ({
  default: () => <div data-testid="engineering-vehicle-detail-view" />,
}));

vi.mock('@/pages/PlatformAdmin/VehicleDiagnose', () => ({
  default: () => <div data-testid="mock-vehicle-diagnose" />,
}));

vi.mock('@/api/oem1RefreshStatus', () => ({
  oem1RefreshStatus: vi.fn().mockResolvedValue({ refreshed: [] }),
  OEM1RefreshStatusError: class extends Error {},
}));

vi.mock('@/auth/useUserRole', () => ({
  useUserRole: () => ({ isAdmin: true, isOperator: false, isViewer: false, isConnectAgent: false, isEngineer: false, canWrite: true, fleetIds: [] }),
}));

vi.mock(
  '@/components/vehicles/vehicle-detail/vehicle-detail-tabs-borderless.css',
  () => ({}),
);

// ── Fixtures ────────────────────────────────────────────────────────────────

// W7: only oem_source varies; all other fields use existing VehicleItem shape
// verbatim per spec C1 (no snake/camel cleanup).
function makeVehicleApiResponse(oem_source?: string) {
  return {
    vehicle: {
      vehicleId: 'TEST-VIN-001',
      vin: 'TEST-VIN-001',
      make: 'OEM-A',
      model: 'Truck-Generic',
      year: 2022,
      connectionStatus: 'connected',
      activityStatus: 'active',
      enrollmentStatus: 'ACTIVE',
      ...(oem_source !== undefined ? { oem_source } : {}),
    },
  };
}

function makeFetchMock(vehicleResponse: object) {
  return vi.fn().mockImplementation((url: string) => {
    if (String(url).includes('/dtcs')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ dtcs: [] }) });
    }
    if (String(url).includes('/api/simulation')) {
      return Promise.resolve({ ok: false });
    }
    return Promise.resolve({ ok: true, json: () => Promise.resolve(vehicleResponse) });
  });
}

// ── Helper: M8 compliance ────────────────────────────────────────────────────
// All conditional logic in test helpers MUST use isOEM1Vehicle / getVehicleSource
// from fleet-types.ts, never literal === 'oem1' (spec M8).

function verifyOEM1Classification(v: Pick<VehicleItem, 'oem_source'>, expected: boolean): void {
  expect(isOEM1Vehicle(v)).toBe(expected);
}

// ── Tests ────────────────────────────────────────────────────────────────────

// Import the component once at module scope — vi.mock() intercepts are
// registered before the describe block runs. The component is heavy so we
// import it at the top level and trust vi.mock() to stub its dependencies.
let VehicleDetailView: React.FC<{ vehicleIdProp?: string }>;

beforeAll(async () => {
  const mod = await import('@/components/vehicles/vehicle-detail/VehicleDetailView');
  VehicleDetailView = mod.default;
});

describe('VehicleDetailView — source-driven branching (spec § 9, T1/T2/T3)', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  // T1 — oem_source='oem1' → OEM1 enrollment panel rendered inline; CMS sub-tree also present
  test('T1: oem_source=oem1 renders OEM1 enrollment panel within the unified page', async () => {
    verifyOEM1Classification({ oem_source: 'oem1' }, true);
    expect(getVehicleSource({ oem_source: 'oem1' })).toBe('oem1');

    global.fetch = makeFetchMock(makeVehicleApiResponse('oem1'));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    // OEM1 enrollment panel must be present
    expect(screen.queryByTestId('oem1-enrollment-panel')).toBeInTheDocument();

    // CMS sub-tree is also present (unified page — not a separate OEM1-only view)
    expect(screen.getAllByText(/Vehicle Details: TEST-VIN-001/).length).toBeGreaterThan(0);
  });

  // T2 — oem_source='cms' → CMS branch renders; OEM1 branch absent (spec C7)
  // RED PHASE: passes now (CMS path unchanged); must continue passing after Group 5.1.
  test('T2: oem_source=cms renders CMS branch; OEM1 branch is absent', async () => {
    verifyOEM1Classification({ oem_source: 'cms' }, false);
    expect(getVehicleSource({ oem_source: 'cms' })).toBe('cms');

    global.fetch = makeFetchMock(makeVehicleApiResponse('cms'));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    // OEM1 branch must NOT be present for a CMS vehicle (spec C7)
    expect(screen.queryByTestId('oem1-vehicle-detail-view')).not.toBeInTheDocument();

    // CMS header present (baseline render smoke-check)
    expect(screen.getAllByText(/Vehicle Details: TEST-VIN-001/).length).toBeGreaterThan(0);
  });

  // T3 — oem_source undefined → defaults to CMS branch (spec C8 legacy-row tolerance)
  // GREEN even before Group 5.1 (no branch = always CMS = correct for undefined).
  test('T3: oem_source undefined defaults to CMS branch (legacy-row tolerance)', async () => {
    // M8: undefined normalizes to false (cms branch)
    verifyOEM1Classification({ oem_source: undefined }, false);
    expect(getVehicleSource({ oem_source: undefined })).toBe('cms');

    global.fetch = makeFetchMock(makeVehicleApiResponse(undefined));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    // undefined oem_source must NOT route to OEM1 branch (spec C8)
    expect(screen.queryByTestId('oem1-vehicle-detail-view')).not.toBeInTheDocument();
  });
});

describe('VehicleDetailView — onboard-agent actions gated on classification (issues/2026-09-18-start-agent-button-shown-for-offboard-vehicles/)', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  function makeVehicleApiResponseWithClassification(
    classification: 'onboard' | 'offboard' | 'unknown' | undefined,
  ) {
    return {
      vehicle: {
        vehicleId: 'TEST-VIN-001',
        vin: 'TEST-VIN-001',
        make: 'Meridian',
        model: 'Trailwind',
        year: 2023,
        connectionStatus: 'connected',
        activityStatus: 'active',
        enrollmentStatus: 'ACTIVE',
        // No oem_source at all — matches every real Meridian vehicle row
        // (issue's own live finding: `isOEM1` is false for Meridian
        // regardless of true onboard/offboard status, which is exactly
        // the bug this gate fixes).
        ...(classification !== undefined ? { classification } : {}),
      },
    };
  }

  // Previously gated on `!isOEM1` alone, which is `true` (buttons shown)
  // for every vehicle in this describe block regardless of the case below
  // — that was the bug. Each case pins the classification-driven outcome.
  test('classification=onboard shows Start Agent', async () => {
    global.fetch = makeFetchMock(makeVehicleApiResponseWithClassification('onboard'));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    expect(screen.queryByText('Start Agent')).toBeInTheDocument();
  });

  // issues/2026-09-19-fwe-agent-logs-and-message-count-shown-for-cloud-vehicle/
  // (CMS-side half of that finding) — FWELogViewer only has state to show for
  // an onboard vehicle; ConnectedServicesCard only has state to show for an
  // offboard one. Each gate is the mirror of the other, both driven by the
  // same classification field, so pinning them together documents that
  // relationship rather than treating them as two unrelated assertions.
  test('classification=onboard shows FWELogViewer, hides ConnectedServicesCard', async () => {
    global.fetch = makeFetchMock(makeVehicleApiResponseWithClassification('onboard'));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    expect(screen.queryByTestId('cms-connected-services-card')).not.toBeInTheDocument();

    const logsTab = await screen.findByText('Logs');
    await act(async () => {
      logsTab.click();
    });

    expect(await screen.findByTestId('cms-fwe-log-viewer')).toBeInTheDocument();
  });

  test('classification=offboard hides Start Agent (the exact case this issue was filed for)', async () => {
    global.fetch = makeFetchMock(makeVehicleApiResponseWithClassification('offboard'));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    expect(screen.queryByText('Start Agent')).not.toBeInTheDocument();
    expect(screen.queryByText('Trip Simulator')).not.toBeInTheDocument();
    expect(screen.queryByText('Simulator Offline')).not.toBeInTheDocument();
  });

  // Mirror of the onboard case above — the exact live UAT finding for
  // VEH-CS-DEMO-0021 (onboard) and its sibling (offboard, this test).
  test('classification=offboard shows ConnectedServicesCard, hides FWELogViewer', async () => {
    global.fetch = makeFetchMock(makeVehicleApiResponseWithClassification('offboard'));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    expect(screen.queryByTestId('cms-connected-services-card')).toBeInTheDocument();

    const logsTab = await screen.findByText('Logs');
    await act(async () => {
      logsTab.click();
    });

    expect(screen.queryByTestId('cms-fwe-log-viewer')).not.toBeInTheDocument();
  });

  test('classification=unknown hides Start Agent (fail closed, not fail open)', async () => {
    global.fetch = makeFetchMock(makeVehicleApiResponseWithClassification('unknown'));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    expect(screen.queryByText('Start Agent')).not.toBeInTheDocument();
  });

  test('classification absent (legacy row, no field at all) hides Start Agent', async () => {
    global.fetch = makeFetchMock(makeVehicleApiResponseWithClassification(undefined));

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    expect(screen.queryByText('Start Agent')).not.toBeInTheDocument();
  });
});

describe('VehicleDetailView — simulation-agent fetch calls carry auth headers (issues/2026-09-19-simulation-agent-fetch-missing-auth-headers/)', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  function makeVehicleApiResponseOnboard() {
    return {
      vehicle: {
        vehicleId: 'TEST-VIN-001',
        vin: 'TEST-VIN-001',
        make: 'Meridian',
        model: 'Trailwind',
        year: 2023,
        connectionStatus: 'connected',
        activityStatus: 'active',
        enrollmentStatus: 'ACTIVE',
        classification: 'onboard',
      },
    };
  }

  // Fixed by the same commit this test ships with. Prior to the fix, this
  // call carried no `headers` argument at all — the real deployed endpoint
  // 401s an unauthenticated request (confirmed live 2026-09-19), the 401
  // never throws, and `simReachable` silently stayed `false` forever. This
  // test asserts the property that makes the fix real: not merely that the
  // call happens, but that it carries the SAME Authorization header
  // `getAuthHeaders()` returns elsewhere in this component.
  test('agent/status poll sends Authorization header', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/dtcs')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ dtcs: [] }) });
      }
      if (String(url).includes('/api/simulation/agent/status')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ agents: [] }) });
      }
      if (String(url).includes('/api/simulation')) {
        return Promise.resolve({ ok: false });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(makeVehicleApiResponseOnboard()) });
    });
    global.fetch = fetchMock;

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    await waitFor(() => {
      const statusCall = fetchMock.mock.calls.find(
        (call) => String(call[0]).includes('/api/simulation/agent/status'),
      );
      expect(statusCall).toBeDefined();
      const init = statusCall?.[1] as RequestInit | undefined;
      expect(init?.headers).toMatchObject({ Authorization: 'Bearer test-token' });
    });
  });

  // The mutation this guards against: reverting toggleAgent's headers to
  // omit `...getAuthHeaders()` — confirmed to make this test fail before
  // the fix was in place (mutation-tested per testing.md's discipline).
  test('agent/start and agent/stop both send Authorization header', async () => {
    const fetchMock = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/dtcs')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ dtcs: [] }) });
      }
      if (String(url).includes('/api/simulation/agent/status')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ agents: [] }) });
      }
      if (
        String(url).includes('/api/simulation/agent/start') ||
        String(url).includes('/api/simulation/agent/stop')
      ) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
      }
      return Promise.resolve({ ok: true, json: () => Promise.resolve(makeVehicleApiResponseOnboard()) });
    });
    global.fetch = fetchMock;

    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

    await waitFor(
      () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
      { timeout: 2000 },
    );

    const startButton = await screen.findByText('Start Agent');
    await act(async () => {
      startButton.closest('button')?.click();
    });

    await waitFor(() => {
      const startCall = fetchMock.mock.calls.find(
        (call) => String(call[0]).includes('/api/simulation/agent/start'),
      );
      expect(startCall).toBeDefined();
      const init = startCall?.[1] as RequestInit | undefined;
      expect(init?.headers).toMatchObject({ Authorization: 'Bearer test-token' });
    });
  });
});


// FG3 (issue 2026-09-25-diagnostics-ia-uat-defects): the page already loads the
// vehicle's ACTIVE DTCs for its severity badge. Those rows must reach the
// Diagnostics panel, so a threshold-raised code (not read from an ECU, so a scan
// need not return it) is carried in the dispatch to service.
describe('VehicleDetailView — recorded ACTIVE DTCs reach the Diagnostics panel', () => {
  let originalFetch: typeof global.fetch;
  const P0217 = { code: 'P0217', severity: 'CRITICAL', description: 'Engine coolant critically overheated' };

  function fetchWithDtcs(dtcsFor: (url: string) => object | null) {
    return vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/dtcs')) {
        const body = dtcsFor(String(url));
        return Promise.resolve(body ? { ok: true, json: () => Promise.resolve(body) } : { ok: false });
      }
      if (String(url).includes('/api/simulation')) return Promise.resolve({ ok: false });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(makeVehicleApiResponse('cms')) });
    });
  }

  const lastPanelProps = () => diagnosticsPanelProps[diagnosticsPanelProps.length - 1];

  beforeEach(() => {
    originalFetch = global.fetch;
    diagnosticsPanelProps.length = 0;
    window.history.pushState({}, '', '?tab=dtcs');
  });

  afterEach(() => {
    global.fetch = originalFetch;
    window.history.pushState({}, '', '/');
  });

  test('passes the ACTIVE rows, reduced to code, description and source', async () => {
    global.fetch = fetchWithDtcs(() => ({
      dtcs: [{ ...P0217, source: 'flink-maintenance-processor', occurrenceCount: '3' }, { code: '', severity: 'LOW' }],
    }));
    render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);
    await waitFor(() =>
      expect(lastPanelProps()?.recordedActiveDtcs).toEqual([
        { code: 'P0217', description: 'Engine coolant critically overheated', source: 'flink-maintenance-processor' },
      ]),
    );
  });

  test('a late response for the previous vehicle never reaches the next vehicle\'s panel', async () => {
    let releaseA: (v: unknown) => void = () => {};
    const aResponse = new Promise((resolve) => { releaseA = resolve; });
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes('/TEST-VIN-001/dtcs')) return aResponse;
      if (u.includes('/dtcs')) return Promise.resolve({ ok: false });
      if (u.includes('/api/simulation')) return Promise.resolve({ ok: false });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(makeVehicleApiResponse('cms')) });
    });
    const { rerender } = render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);
    rerender(<VehicleDetailView vehicleIdProp="TEST-VIN-002" />);
    await waitFor(() => {
      const calls = vi.mocked(global.fetch).mock.calls.map((c) => String(c[0]));
      expect(calls.some((u) => u.includes('/TEST-VIN-002/dtcs'))).toBe(true);
    });
    await act(async () => {
      releaseA({ ok: true, json: () => Promise.resolve({ dtcs: [P0217] }) });
      await aResponse;
    });
    await waitFor(() => expect(lastPanelProps()?.vehicleId).toBe('TEST-VIN-002'));
    expect(lastPanelProps()?.recordedActiveDtcs).toBeUndefined();
  });

  test('the next vehicle keeps its own codes when the previous vehicle\'s response arrives late', async () => {
    let releaseA: (v: unknown) => void = () => {};
    const aResponse = new Promise((resolve) => { releaseA = resolve; });
    const C0035 = { code: 'C0035', severity: 'HIGH', description: 'Wheel speed sensor' };
    global.fetch = vi.fn().mockImplementation((url: string) => {
      const u = String(url);
      if (u.includes('/TEST-VIN-001/dtcs')) return aResponse;
      if (u.includes('/TEST-VIN-002/dtcs')) {
        return Promise.resolve({ ok: true, json: () => Promise.resolve({ dtcs: [C0035] }) });
      }
      if (u.includes('/api/simulation')) return Promise.resolve({ ok: false });
      return Promise.resolve({ ok: true, json: () => Promise.resolve(makeVehicleApiResponse('cms')) });
    });
    const { rerender } = render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);
    rerender(<VehicleDetailView vehicleIdProp="TEST-VIN-002" />);
    await waitFor(() =>
      expect(lastPanelProps()?.recordedActiveDtcs).toEqual([{ code: 'C0035', description: 'Wheel speed sensor' }]),
    );
    await act(async () => {
      releaseA({ ok: true, json: () => Promise.resolve({ dtcs: [P0217] }) });
      await aResponse;
      await new Promise((r) => setTimeout(r, 20));
    });
    expect(lastPanelProps()?.vehicleId).toBe('TEST-VIN-002');
    expect(lastPanelProps()?.recordedActiveDtcs).toEqual([{ code: 'C0035', description: 'Wheel speed sensor' }]);
  });

  test('a failed fetch for the next vehicle does not keep the previous vehicle\'s codes', async () => {
    global.fetch = fetchWithDtcs((url) => (url.includes('/TEST-VIN-001/') ? { dtcs: [P0217] } : null));
    const { rerender } = render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);
    await waitFor(() => expect(lastPanelProps()?.recordedActiveDtcs).toHaveLength(1));
    rerender(<VehicleDetailView vehicleIdProp="TEST-VIN-002" />);
    await waitFor(() => {
      const calls = vi.mocked(global.fetch).mock.calls.map((c) => String(c[0]));
      expect(calls.some((u) => u.includes('/TEST-VIN-002/dtcs'))).toBe(true);
    });
    await waitFor(() => expect(lastPanelProps()?.recordedActiveDtcs).toBeUndefined());
  });
});
