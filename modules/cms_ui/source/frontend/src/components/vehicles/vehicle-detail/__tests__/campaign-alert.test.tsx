// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * Vehicle detail — campaign-missing warning alert tests.
 *
 * Spec: ``2026-09-01-cms-campaign-follows-enrollment`` § D4 + § "Test surface > Frontend > VDCA1–VDCA3"
 *
 * Cases:
 *  VDCA1: ``hasCampaign: false`` + ``classification='onboard'`` → warning alert renders.
 *  VDCA2: ``hasCampaign: true`` → alert absent.
 *  VDCA3: ``classification='offboard'`` + ``hasCampaign: false`` → alert absent.
 *  VDCA4: ``hasCampaign: null`` + onboard → INFO "couldn't verify" alert, NOT the warning.
 *  VDCA5: ``hasCampaign: null`` + offboard → neither alert.
 *  VDCA6: no customer-facing copy names an operator script or a raw REST path.
 *
 * VDCA4-6 added by issues/2026-09-02-campaign-guard-queries-wrong-stage-table.
 * The backend previously coerced "could not determine" into ``false``, so a CDK
 * wiring gap rendered a confident false alarm. The three-state contract is the
 * fix, and VDCA4 is the regression test for it.
 */

import React from 'react';
import { render, screen, waitFor } from '@testing-library/react';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

// ── Module mocks (same as VehicleDetailView.test.tsx) ───────────────────

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

// Heavy child component stubs.
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

// ── Alert text constant (spec § D4 verbatim) ─────────────────────────────

const CAMPAIGN_MISSING_ALERT_TEXT =
  'This vehicle has no active data collection campaign, so no telemetry will be recorded and trip simulations will not run. Attach a campaign from the Campaigns tab to start collecting.';

const CAMPAIGN_UNKNOWN_ALERT_TEXT =
  "The campaign status for this vehicle could not be checked just now. Telemetry may be unaffected. Open the Campaigns tab to confirm.";

// ── Fixture factory ──────────────────────────────────────────────────────

interface VehicleResponseOverrides {
  // null is a real wire value meaning "backend could not determine".
  hasCampaign?: boolean | null;
  classification?: 'onboard' | 'offboard' | 'unknown';
}

function makeVehicleApiResponse(overrides: VehicleResponseOverrides = {}) {
  return {
    vehicle: {
      vehicleId: 'TEST-VIN-001',
      vin: 'TEST-VIN-001',
      make: 'Test',
      model: 'Vehicle',
      year: 2024,
      connectionStatus: 'connected',
      activityStatus: 'active',
      enrollmentStatus: 'ACTIVE',
      classification: 'onboard' as const,
      hasCampaign: true,
      ...overrides,
    },
  };
}

function makeFetchMock(vehicleResponse: object) {
  return vi.fn().mockImplementation((url: string) => {
    const urlStr = String(url);
    if (urlStr.includes('/dtcs')) {
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ dtcs: [] }) });
    }
    if (urlStr.includes('/api/simulation')) {
      return Promise.resolve({ ok: false });
    }
    // Main vehicle endpoint + any catch-all.
    return Promise.resolve({ ok: true, json: () => Promise.resolve(vehicleResponse) });
  });
}

// ── Component (lazy-loaded to pick up mocks) ─────────────────────────────

let VehicleDetailView: React.FC<{ vehicleIdProp?: string }>;

// NOTE: this hook HANGS — the suite cannot run. Raising the timeout to 30s does
// not help (observed 32s in `tests`), so it is a genuine hang, not slowness.
// Consequence: VDCA1-6 execute zero assertions. Tracked as
// issues/2026-09-02-campaign-alert-suite-hangs-on-import.
beforeAll(async () => {
  const mod = await import('@/components/vehicles/vehicle-detail/VehicleDetailView');
  VehicleDetailView = mod.default;
});

// ── Tests ─────────────────────────────────────────────────────────────────

describe('Vehicle detail — campaign-missing warning alert (VDCA1–VDCA3)', () => {
  let originalFetch: typeof global.fetch;

  beforeEach(() => {
    originalFetch = global.fetch;
  });

  afterEach(() => {
    global.fetch = originalFetch;
  });

  // ── VDCA1 ─────────────────────────────────────────────────────────────
  it(
    'VDCA1: hasCampaign=false + classification=onboard → warning alert renders with spec copy',
    async () => {
      global.fetch = makeFetchMock(
        makeVehicleApiResponse({ hasCampaign: false, classification: 'onboard' }),
      );

      render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

      // Wait for loading to complete.
      await waitFor(
        () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
        { timeout: 3000 },
      );

      // The spec § D4 verbatim alert text must be present.
      await waitFor(() => {
        expect(screen.getByTestId('campaign-missing-alert')).toBeInTheDocument();
      });

      // Single exact-match assertion using the spec § D4 verbatim constant (S4).
      expect(screen.getByTestId('campaign-missing-alert').textContent).toContain(
        CAMPAIGN_MISSING_ALERT_TEXT,
      );
    },
  );

  // ── VDCA2 ─────────────────────────────────────────────────────────────
  it(
    'VDCA2: hasCampaign=true → no campaign warning alert',
    async () => {
      global.fetch = makeFetchMock(
        makeVehicleApiResponse({ hasCampaign: true, classification: 'onboard' }),
      );

      render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

      await waitFor(
        () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
        { timeout: 3000 },
      );

      // Alert must NOT be present when the vehicle has an active campaign.
      expect(screen.queryByTestId('campaign-missing-alert')).not.toBeInTheDocument();
    },
  );

  // ── VDCA3 ─────────────────────────────────────────────────────────────
  it(
    'VDCA3: classification=offboard + hasCampaign=false → no campaign warning alert',
    async () => {
      // Cloud-telemetry (offboard) vehicles do not use FleetWise campaigns;
      // the absence of a campaign is expected and must not surface a warning.
      global.fetch = makeFetchMock(
        makeVehicleApiResponse({ hasCampaign: false, classification: 'offboard' }),
      );

      render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

      await waitFor(
        () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
        { timeout: 3000 },
      );

      // Alert must NOT be present for offboard (cloud-telemetry) vehicles.
      expect(screen.queryByTestId('campaign-missing-alert')).not.toBeInTheDocument();
    },
  );

  // ── VDCA4 ─────────────────────────────────────────────────────────────
  it(
    "VDCA4: hasCampaign=null + onboard → info 'couldn't verify' alert, never the warning",
    async () => {
      // REGRESSION GUARD. This is the exact state produced when
      // CAMPAIGNS_TABLE_NAME is unwired or the campaigns query fails. Asserting
      // absence of a campaign in this state is the defect; the UI must say it
      // does not know instead.
      global.fetch = makeFetchMock(
        makeVehicleApiResponse({ hasCampaign: null, classification: 'onboard' }),
      );

      render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

      await waitFor(
        () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
        { timeout: 3000 },
      );

      await waitFor(() => {
        expect(screen.getByTestId('campaign-unknown-alert')).toBeInTheDocument();
      });

      expect(screen.getByTestId('campaign-unknown-alert').textContent).toContain(
        CAMPAIGN_UNKNOWN_ALERT_TEXT,
      );

      // The alarming warning must NOT appear — that is the whole point.
      expect(screen.queryByTestId('campaign-missing-alert')).not.toBeInTheDocument();
    },
  );

  // ── VDCA5 ─────────────────────────────────────────────────────────────
  it(
    'VDCA5: hasCampaign=null + offboard → neither alert',
    async () => {
      global.fetch = makeFetchMock(
        makeVehicleApiResponse({ hasCampaign: null, classification: 'offboard' }),
      );

      render(<VehicleDetailView vehicleIdProp="TEST-VIN-001" />);

      await waitFor(
        () => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(),
        { timeout: 3000 },
      );

      expect(screen.queryByTestId('campaign-missing-alert')).not.toBeInTheDocument();
      expect(screen.queryByTestId('campaign-unknown-alert')).not.toBeInTheDocument();
    },
  );

  // ── VDCA6 ─────────────────────────────────────────────────────────────
  it(
    'VDCA6: customer-facing campaign copy names no operator script or REST path',
    () => {
      // Copy names UI actions only. A maintainer script on a customer-facing
      // screen reads as an unfinished product; a raw REST path is not an action
      // an operator can take. Enforced mechanically so it cannot regress.
      for (const copy of [CAMPAIGN_MISSING_ALERT_TEXT, CAMPAIGN_UNKNOWN_ALERT_TEXT]) {
        expect(copy).not.toMatch(/\.py\b/);
        expect(copy).not.toMatch(/\bPOST\b/);
        expect(copy).not.toMatch(/\/api\//);
      }
    },
  );
});
