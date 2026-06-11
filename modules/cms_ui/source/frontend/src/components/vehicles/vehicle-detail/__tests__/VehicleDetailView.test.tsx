// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

// RED-PHASE SKELETONS — spec § 9 matrix T1, T2, T3
// Tests are authored here and expected to FAIL until Group 5.1 ships
// the `isOEM1Vehicle` early-return branch in VehicleDetailView.tsx.

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { isOEM1Vehicle, getVehicleSource } from '@/types/fleet-types';

// ── Module mocks ────────────────────────────────────────────────────────────

vi.mock('react-router-dom', () => ({
  useParams: () => ({ vehicleId: 'TEST-VIN-001' }),
  useNavigate: () => vi.fn(),
}));

vi.mock('@/auth/useAuth', () => ({
  useAuth: () => ({ getAuthHeaders: () => ({}) }),
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
vi.mock('@/components/vehicles/vehicle-detail/RemoteCommandsPanel', () => ({
  default: () => <div data-testid="cms-remote-commands" />,
}));
vi.mock('@/components/vehicles/vehicle-detail/VehicleDTCsTable', () => ({
  default: () => <div data-testid="cms-dtc-table" />,
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
vi.mock('@/components/documents', () => ({
  DocumentViewer: () => <div />,
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
