// SPDX-License-Identifier: Apache-2.0

// ╔══════════════════════════════════════════════════════════════════════════╗
// ║  DO NOT DELETE. DO NOT "CLEAN UP". This file exists to FAIL.             ║
// ╚══════════════════════════════════════════════════════════════════════════╝
//
// This is a POSITIVE guard: it asserts that the per-vehicle simulation
// affordances on the vehicle detail page CONTINUE TO EXIST.
//
// Why it exists
// -------------
// Spec `2026-09-14-cms-frontend-fleet-persona-alignment` Phase B (Group 5)
// planned to REMOVE these affordances. That plan was superseded on 2026-09-15
// (`decisions.md` § "2026-09-15 — Tasks 5.2–5.5 are SUPERSEDED; simulation
// stays in CMS") because `2026-09-15-cms-cs-campaign-ownership`:
//
//   * Decision 8  — Trip Simulator and Start Agent STAY in CMS. A fleet
//                   operator authoring and testing its own collection campaign
//                   is within operator authority, and the Trip Simulator is the
//                   affordance that exercises that loop. There is no CS-side
//                   replacement.
//   * Decision 11 — CS's `POST /simulate/start` is a SECOND, independently
//                   gated dispatch path, not a replacement. Simulation runs
//                   from BOTH portals.
//
// Group 5 has nevertheless been rebuilt TWICE against that standing decision:
// commit `989f2127` (2026-09-18, reverted by `ec4cd1eb`) and again on
// 2026-09-24. Both times the session read `tasks.md`'s task bodies and not
// `decisions.md`. Both times the full test suite stayed GREEN, because the only
// assertions that covered these affordances lived inside describe blocks named
// for unrelated issues — and Task 5.4's Verify step explicitly instructs the
// implementer to delete "any test that pins removed state". Following that
// instruction dismantles the guard that would have stopped the change.
//
// So this file is deliberately:
//   * named for the DECISION (`decision8-affordances`), not for an issue, so an
//     issue-guard cleanup pass has no reason to touch it;
//   * self-contained, so it survives edits to any other test file;
//   * loud about being a tripwire, so a session that makes it red reads this
//     header before "fixing" it.
//
// If this file goes red, you are probably re-executing VOID Group 5. Read
// `.kiro/specs/2026-09-14-cms-frontend-fleet-persona-alignment/decisions.md`
// § "2026-09-15 — Tasks 5.2–5.5 are SUPERSEDED" IN FULL before changing
// anything here. Reverting your change is almost certainly the correct fix.
// Deleting or weakening these assertions requires a NEW recorded decision that
// supersedes Decision 8 — a task body alone is not sufficient authority.
//
// See `issues/2026-09-24-void-task-group-rebuilt-twice/`.

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';

// ── Module mocks ────────────────────────────────────────────────────────────
// Mirrors VehicleDetailView.test.tsx's mock set. Duplicated ON PURPOSE: this
// guard must not break, or be silently disabled, by edits to that file.

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
vi.mock('@/components/vehicles/vehicle-detail/VehicleCampaignsTable', () => ({
  default: () => <div data-testid="cms-campaigns-table" />,
}));
vi.mock('@/components/vehicles/vehicle-detail/TirePressureWidget', () => ({
  default: () => <div />,
}));
vi.mock('@/components/vehicles/vehicle-detail/TripSimulatorModal', () => ({
  default: () => <div data-testid="cms-trip-simulator-modal" />,
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
  useUserRole: () => ({
    isAdmin: true,
    isOperator: false,
    isViewer: false,
    isConnectAgent: false,
    isEngineer: false,
    canWrite: true,
    fleetIds: [],
  }),
}));
vi.mock(
  '@/components/vehicles/vehicle-detail/vehicle-detail-tabs-borderless.css',
  () => ({}),
);

// ── Fixtures ────────────────────────────────────────────────────────────────

// `classification: 'onboard'` is required: the agent actions are gated on
// `canUseOnboardAgentActions` per
// issues/2026-09-18-start-agent-button-shown-for-offboard-vehicles/. An
// onboard vehicle is the case in which these affordances MUST appear.
function makeOnboardVehicleResponse() {
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

let VehicleDetailView: React.FC<{ vehicleIdProp?: string }>;

beforeAll(async () => {
  const mod = await import('@/components/vehicles/vehicle-detail/VehicleDetailView');
  VehicleDetailView = mod.default;
});

async function renderOnboardVehicle() {
  globalThis.fetch = makeFetchMock(makeOnboardVehicleResponse()) as unknown as typeof fetch;

  render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

  await waitFor(
    () => expect(screen.queryByText('Loading vehicle details...')).toBeNull(),
    { timeout: 2000 },
  );
}

describe('VehicleDetailView — Decision 8 affordances MUST remain (VOID Group 5 tripwire)', () => {
  let originalFetch: typeof globalThis.fetch;

  beforeEach(() => {
    originalFetch = globalThis.fetch;
  });

  afterEach(() => {
    globalThis.fetch = originalFetch;
  });

  // Inverse of VOID task 5.2. Redundant with the classification guard in
  // VehicleDetailView.test.tsx by design — that one is named for an issue and
  // is therefore deletable by a cleanup pass; this one is not.
  test('Start Agent / Stop Agent control is present (VOID 5.2 would remove it)', async () => {
    await renderOnboardVehicle();

    expect(screen.queryByText(/^(Start Agent|Stop Agent)$/)).not.toBeNull();
  });

  // Also inverse of VOID task 5.2. Asserts the union of both label states: the
  // label is `simReachable ? 'Trip Simulator' : 'Simulator Offline'`, and the
  // fetch mock above makes `/api/simulation` unreachable, so under test the
  // control renders as 'Simulator Offline'. Either label satisfies the
  // decision — what must not happen is the control disappearing entirely.
  test('Trip Simulator / Simulator Offline control is present (VOID 5.2 would remove it)', async () => {
    await renderOnboardVehicle();

    expect(screen.queryByText(/^(Trip Simulator|Simulator Offline)$/)).not.toBeNull();
  });

  // Inverse of VOID task 5.3. SimLogViewer is scoped to a `simId` the CS portal
  // does not have, so this tab cannot move to CS at all.
  test('Logs tab is present (VOID 5.3 would remove it)', async () => {
    await renderOnboardVehicle();

    expect(screen.queryByText('Logs')).not.toBeNull();
  });

  // Inverse of VOID task 5.5 — the one assertion here that is not merely
  // "don't delete this". That caption is FACTUALLY FALSE: per Decision 11
  // simulation runs from BOTH portals, so telling an operator it runs from
  // Connected Services misdirects them away from a working local control.
  // `decisions.md:920` quotes this exact sentence and rejects it in advance.
  test('the false "simulation is run from Connected Services" caption is absent (VOID 5.5 would add it)', async () => {
    await renderOnboardVehicle();

    expect(screen.queryByText(/simulation is run from Connected Services/i)).toBeNull();
  });
});
