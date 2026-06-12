// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// OEM1VehicleDetailView was merged into VehicleDetailView on 2026-06-10.
// These tests now render VehicleDetailView with an OEM1-vehicle fetch mock
// and verify the same coverage: badge state, enrollment panel fields, role gate.

import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import type { VehicleItem } from '@/types/fleet-types';

// ── Module mocks ────────────────────────────────────────────────────────────

vi.mock('react-router-dom', async () => {
  const actual = await vi.importActual('react-router-dom');
  return { ...(actual as object), useParams: () => ({ vehicleId: 'TEST-OEM1-VIN' }), useNavigate: () => vi.fn() };
});

vi.mock('@/auth/useAuth', () => ({ useAuth: () => ({ getAuthHeaders: () => ({}) }) }));
vi.mock('@/auth/useIsEngineerTenant', () => ({ useIsEngineerTenant: () => false }));
vi.mock('@/contexts/VehicleContext', () => ({ useVehicle: () => ({ setVehicleVin: vi.fn(), vehicleVin: null }) }));
vi.mock('@/components/commons/UserContext', async () => {
  const React = await import('react');
  return { UserContext: React.createContext({}) };
});
vi.mock('@/config/api', () => ({ getRuntimeConfig: () => ({ apiEndpoint: 'http://localhost/' }), getApiEndpoint: () => 'http://localhost/' }));
vi.mock('@/utils/simulation-config', () => ({ getSimulationApiBase: () => 'http://localhost', getSimulationMode: () => 'local' }));
vi.mock('@/utils/constants', () => ({ UI_ROUTES: { VEHICLE_MANAGEMENT: '/vehicles', DATA_PROCESSING: '/data-processing' } }));

// Heavy child stubs
vi.mock('@/components/vehicles/vehicle-detail/FWELogViewer', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/SimLogViewer', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/RemoteCommandsPanel', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/VehicleDTCsTable', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/VehicleCampaignsTable', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/TirePressureWidget', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/TripSimulatorModal', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/RouteMapModal', () => ({ RouteMapModal: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/VehicleRecallWidget', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/VehicleWarrantyWidget', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/VehicleHealthScoreWidget', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/VehicleFinancialWidget', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/ScheduleServiceModal', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/EnrollmentStatusSection', () => ({ VehicleStatusBadge: () => <span /> }));
vi.mock('@/components/vehicles/vehicle-detail/SimulationLogViewer', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/GeofenceWidget', () => ({ default: () => <div /> }));
vi.mock('@/components/commons/TripsTable', () => ({ TripsTable: () => <div data-testid="mock-trips-table" /> }));
vi.mock('@/components/commons/SafetyEventsTable', () => ({ SafetyEventsTable: () => <div /> }));
vi.mock('@/components/commons/SafetyEventLocationModal', () => ({ SafetyEventLocationModal: () => <div /> }));
vi.mock('@/components/documents', () => ({ DocumentViewer: () => <div /> }));
vi.mock('@/components/vehicles/trip-detail/TripMap', () => ({ TripMap: () => <div /> }));
vi.mock('@/components/recall-warranty/nhtsaRecallData', () => ({ nhtsaRecalls: [] }));
vi.mock('@/components/engineering/EngineeringVehicleDetailView', () => ({ default: () => <div /> }));
vi.mock('@/components/vehicles/vehicle-detail/vehicle-detail-tabs-borderless.css', () => ({}));
vi.mock('@/pages/PlatformAdmin/VehicleDiagnose', () => ({ default: () => <div data-testid="mock-vehicle-diagnose" /> }));

vi.mock('@/api/oem1RefreshStatus', () => ({
  oem1RefreshStatus: vi.fn().mockResolvedValue({ refreshed: [] }),
  OEM1RefreshStatusError: class extends Error {},
}));

vi.mock('@/auth/useUserRole', () => ({ useUserRole: vi.fn() }));

import { useUserRole } from '@/auth/useUserRole';
import { oem1RefreshStatus } from '@/api/oem1RefreshStatus';

const mockUseUserRole = vi.mocked(useUserRole);
const mockRefresh = vi.mocked(oem1RefreshStatus);

function makeRole(overrides: Partial<ReturnType<typeof useUserRole>> = {}): ReturnType<typeof useUserRole> {
  return { isAdmin: false, isOperator: false, isViewer: false, isConnectAgent: false, isEngineer: false, canWrite: false, fleetIds: [], ...overrides };
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function makeOEM1FetchMock(vehicleOverrides: Record<string, unknown> = {}) {
  return vi.fn().mockImplementation((url: string) => {
    if (String(url).includes('/dtcs')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ dtcs: [] }) });
    if (String(url).includes('/api/simulation')) return Promise.resolve({ ok: false });
    return Promise.resolve({
      ok: true,
      json: () => Promise.resolve({
        vehicle: {
          vehicleId: 'TEST-OEM1-VIN',
          vin: '1FTFW1E16JFD55835',
          oem_source: 'oem1',
          status: 'Active',
          fleetId: 'fleet-alpha',
          oem1_active_sku: 'SKU-00000069',
          oem1_request_id: 42,
          oem1_enrollment_status: 'COMPLETED',
          oem1_fcs_code: 3,
          oem1_status_message: 'Vehicle has been successfully enrolled',
          oem1_readiness_summary: 'READY',
          oem1_status_refreshed_at: new Date(Date.now() - 90_000).toISOString(),
          subscription_service_activation_date: '2026-06-01T12:00:00Z',
          connectionStatus: 'connected',
          ...vehicleOverrides,
        },
      }),
    });
  });
}

let VehicleDetailView: React.FC<{ vehicleIdProp?: string }>;
let originalFetch: typeof global.fetch;

beforeAll(async () => {
  const mod = await import('@/components/vehicles/vehicle-detail/VehicleDetailView');
  VehicleDetailView = mod.default;
});

beforeEach(() => {
  originalFetch = global.fetch;
  mockUseUserRole.mockReturnValue(makeRole({ isAdmin: true, canWrite: true }));
  mockRefresh.mockResolvedValue({ refreshed: [] });
});

afterEach(() => {
  global.fetch = originalFetch;
});

async function renderAndLoad(vehicleOverrides: Record<string, unknown> = {}) {
  global.fetch = makeOEM1FetchMock(vehicleOverrides);
  render(
    <MemoryRouter>
      <VehicleDetailView vehicleIdProp="TEST-OEM1-VIN" />
    </MemoryRouter>,
  );
  await waitFor(() => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(), { timeout: 2000 });
}

// ── Badge / status tests ──────────────────────────────────────────────────────

describe('OEM1 status badge in unified VehicleDetailView', () => {
  it('renders enrollment status badge for OEM1 vehicle', async () => {
    await renderAndLoad({ status: 'Active', enrollmentStatus: 'ACTIVE' });
    // The CMS page renders enrollmentStatus via the enrollment status badge row
    expect(screen.getAllByText(/ACTIVE/i).length).toBeGreaterThan(0);
  });
});

// ── Enrollment panel tests ────────────────────────────────────────────────────

describe('OEM1 enrollment & readiness panel in unified VehicleDetailView', () => {
  it('renders the enrollment panel container', async () => {
    await renderAndLoad();
    expect(screen.getByTestId('oem1-enrollment-panel')).toBeInTheDocument();
  });

  it('renders all 8 M-MGR field labels', async () => {
    await renderAndLoad();
    expect(screen.getByText('Active SKU')).toBeInTheDocument();
    expect(screen.getByText('OEM1 Request ID')).toBeInTheDocument();
    expect(screen.getByText('Enrollment status')).toBeInTheDocument();
    expect(screen.getByText('FCS code')).toBeInTheDocument();
    expect(screen.getByText('Status message')).toBeInTheDocument();
    expect(screen.getByText('Readiness summary')).toBeInTheDocument();
    expect(screen.getByText('Status refreshed at')).toBeInTheDocument();
    expect(screen.getByText('Subscription activation date')).toBeInTheDocument();
  });

  it('renders M-MGR field values', async () => {
    await renderAndLoad();
    expect(screen.getByText('SKU-00000069')).toBeInTheDocument();
    expect(screen.getByText('42')).toBeInTheDocument();
    expect(screen.getByText('COMPLETED')).toBeInTheDocument();
    expect(screen.getByText('Vehicle has been successfully enrolled')).toBeInTheDocument();
    expect(screen.getByText('READY')).toBeInTheDocument();
  });

  it('"Refresh now" button enabled when last refreshed > 60s ago', async () => {
    await renderAndLoad();
    const btn = screen.getByTestId('refresh-now-btn');
    expect(btn).not.toBeDisabled();
    expect(btn).toHaveTextContent('Refresh now');
  });

  it('"Refresh now" button disabled (rate-limited) when last refreshed < 60s ago', async () => {
    await renderAndLoad({ oem1_status_refreshed_at: new Date(Date.now() - 10_000).toISOString() });
    const btn = screen.getByTestId('refresh-now-btn');
    expect(btn).toBeDisabled();
    expect(btn.textContent).toMatch(/Last refreshed \d+s ago/);
  });

  it('"Refresh now" button calls oem1RefreshStatus with the vehicle VIN', async () => {
    await renderAndLoad();
    fireEvent.click(screen.getByTestId('refresh-now-btn'));
    await waitFor(() => expect(mockRefresh).toHaveBeenCalledWith({ vehicle_ids: ['1FTFW1E16JFD55835'] }));
  });

  it('"Retry enrollment" not shown for COMPLETED', async () => {
    await renderAndLoad();
    expect(screen.queryByTestId('retry-enrollment-btn')).not.toBeInTheDocument();
  });

  it('"Retry enrollment" shown for FAILED + fcs_code 8020', async () => {
    await renderAndLoad({ oem1_enrollment_status: 'FAILED', oem1_fcs_code: 8020 });
    expect(screen.getByTestId('retry-enrollment-btn')).toBeInTheDocument();
  });

  it('"Retry enrollment" not shown for FAILED with fcs_code != 8020', async () => {
    await renderAndLoad({ oem1_enrollment_status: 'FAILED', oem1_fcs_code: 1002 });
    expect(screen.queryByTestId('retry-enrollment-btn')).not.toBeInTheDocument();
  });
});

// ── Role gate tests ──────────────────────────────────────────────────────────

describe('T3.4 — Refresh button role-gate in unified VehicleDetailView', () => {
  it('button hidden for viewer', async () => {
    mockUseUserRole.mockReturnValue(makeRole({ isViewer: true }));
    await renderAndLoad();
    expect(screen.queryByTestId('refresh-now-btn')).not.toBeInTheDocument();
  });

  it('button visible for operator with matching fleetId', async () => {
    mockUseUserRole.mockReturnValue(makeRole({ isOperator: true, canWrite: true, fleetIds: ['fleet-alpha'] }));
    await renderAndLoad();
    expect(screen.getByTestId('refresh-now-btn')).toBeInTheDocument();
  });

  it('button hidden for operator with mismatched fleetId', async () => {
    mockUseUserRole.mockReturnValue(makeRole({ isOperator: true, canWrite: true, fleetIds: ['fleet-other'] }));
    await renderAndLoad();
    expect(screen.queryByTestId('refresh-now-btn')).not.toBeInTheDocument();
  });

  it('button visible for platform-admin regardless of fleetId', async () => {
    mockUseUserRole.mockReturnValue(makeRole({ isAdmin: true, canWrite: true }));
    await renderAndLoad();
    expect(screen.getByTestId('refresh-now-btn')).toBeInTheDocument();
  });
});

// ── Unified page smoke test ───────────────────────────────────────────────────

describe('Unified page — OEM1 vehicle gets full CMS page + OEM1 panels', () => {
  it('renders the standard vehicle header', async () => {
    await renderAndLoad();
    expect(screen.getAllByText(/Vehicle Details:/i).length).toBeGreaterThan(0);
  });

  it('OEM1 enrollment panel absent for CMS vehicle', async () => {
    global.fetch = vi.fn().mockImplementation((url: string) => {
      if (String(url).includes('/dtcs')) return Promise.resolve({ ok: true, json: () => Promise.resolve({ dtcs: [] }) });
      if (String(url).includes('/api/simulation')) return Promise.resolve({ ok: false });
      return Promise.resolve({ ok: true, json: () => Promise.resolve({ vehicle: { vehicleId: 'TEST-OEM1-VIN', vin: 'CMS-VIN', connectionStatus: 'connected' } }) });
    });
    render(<MemoryRouter><VehicleDetailView vehicleIdProp="TEST-OEM1-VIN" /></MemoryRouter>);
    await waitFor(() => expect(screen.queryByText('Loading vehicle details...')).not.toBeInTheDocument(), { timeout: 2000 });
    expect(screen.queryByTestId('oem1-enrollment-panel')).not.toBeInTheDocument();
  });
});
