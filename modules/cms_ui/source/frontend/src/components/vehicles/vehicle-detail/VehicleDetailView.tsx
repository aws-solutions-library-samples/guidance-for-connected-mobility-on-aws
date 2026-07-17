// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from 'react';
import { getRuntimeConfig, getApiEndpoint } from '../../../config/api';
import { DocumentViewer } from '../../documents';
import { getSimulationApiBase, getSimulationMode } from '../../../utils/simulation-config';
import { useAuth } from '../../../auth/useAuth';
import { useParams, useNavigate } from 'react-router-dom';
import {
  Container,
  Header,
  ColumnLayout,
  Box,
  StatusIndicator,
  Tabs,
  Table,
  Button,
  Badge,
  SpaceBetween,
  Spinner,
  Alert,
  Pagination,
  ProgressBar,
  Popover,
  Select,
  CollectionPreferences
} from '@cloudscape-design/components';
import { UserContext } from '../../commons/UserContext';
import { UI_ROUTES } from "../../../utils/constants";
import { RouteMapModal } from './RouteMapModal';
import { TripMap } from '../trip-detail/TripMap';
import { SafetyEventsTable } from '../../commons/SafetyEventsTable';
import { SafetyEventLocationModal } from '../../commons/SafetyEventLocationModal';
import TirePressureWidget from './TirePressureWidget';
import TripSimulatorModal from './TripSimulatorModal';
import { TripsTable } from '../../commons/TripsTable';
import { useVehicle } from '../../../contexts/VehicleContext';
import { VehicleStatusBadge } from './EnrollmentStatusSection';
import VehicleCampaignsTable from './VehicleCampaignsTable';
import VehicleDTCsTable from './VehicleDTCsTable';
import FWELogViewer from './FWELogViewer';
import SimLogViewer from './SimLogViewer';
import RemoteCommandsPanel from './RemoteCommandsPanel';
import OEM1RemoteCommandsPanel from './OEM1RemoteCommandsPanel';
import GeofenceWidget from './GeofenceWidget';
import VehicleRecallWidget from './VehicleRecallWidget';
import VehicleWarrantyWidget from './VehicleWarrantyWidget';
import VehicleHealthScoreWidget from './VehicleHealthScoreWidget';
import { nhtsaRecalls } from '../../recall-warranty/nhtsaRecallData';

// Warranty data keyed by vehicle (same source as VehicleWarrantyWidget)
const warrantyVehicles = ['VEH-0049', 'VEH-0026', 'VEH-0043', 'VEH-0004', 'VEH-0017', 'VEH-0008', 'VEH-0047', 'VEH-0025'];

// ── OEM1 inline helpers (previously in OEM1VehicleDetailView.tsx) ─────────────

const REFRESH_COOLDOWN_MS = 60_000;

function secondsSinceOEM1(iso: string | undefined): number | null {
  if (!iso) return null;
  const diff = Date.now() - new Date(iso).getTime();
  return diff >= 0 ? Math.floor(diff / 1000) : null;
}

interface OEM1EnrollmentPanelProps {
  vehicle: VehicleItem & { [k: string]: unknown };
  vin: string;
  canRefresh: boolean;
}

const OEM1EnrollmentPanel: React.FC<OEM1EnrollmentPanelProps> = ({ vehicle, vin, canRefresh }) => {
  const [refreshing, setRefreshing] = React.useState(false);
  const [refreshError, setRefreshError] = React.useState<string | null>(null);
  const [localRefreshedAt, setLocalRefreshedAt] = React.useState<string | undefined>(
    (vehicle as any).oem1_status_refreshed_at,
  );

  const status = getOEM1Status(vehicle);
  const secondsAgo = secondsSinceOEM1(localRefreshedAt);
  const isRateLimited = secondsAgo !== null && secondsAgo < 60;
  const refreshLabel = isRateLimited ? `Last refreshed ${secondsAgo}s ago` : 'Refresh now';

  const handleRefresh = React.useCallback(async () => {
    if (isRateLimited || refreshing) return;
    setRefreshing(true);
    setRefreshError(null);
    try {
      await oem1RefreshStatus({ vehicle_ids: [vin] });
      setLocalRefreshedAt(new Date().toISOString());
    } catch (err) {
      setRefreshError(err instanceof Error ? err.message : 'Refresh failed');
    } finally {
      setRefreshing(false);
    }
  }, [vin, isRateLimited, refreshing]);

  const showRetry = status.enrollmentStatus === 'FAILED' && status.fcsCode === 8020;
  const formatOptional = (v: string | number | undefined) => (v != null ? String(v) : '—');

  // Use actual fields present on OEM1 vehicle records.
  // M-MGR fields (oem1_enrollment_status, oem1_active_sku etc.) are only populated
  // for vehicles enrolled via the bulk-enroll flow. Most OEM1 vehicles have:
  // enrolledAt, lastSeenAt, status, oem1_status_refreshed_at.
  // (Snake-case dual-read removed 2026-06-22 — Lambda boundary
  //  normalization in spec 2026-06-09-cms-api-field-normalization
  //  guarantees camelCase on the wire; the snake_case branch was
  //  dead since commit 87be7df.)
  const enrolledAt = (vehicle as any).enrolledAt;
  const lastSeenAt = (vehicle as any).lastSeenAt;
  const vehicleStatus = (vehicle as any).status;
  const refreshedAt = (vehicle as any).oem1_status_refreshed_at || status.refreshedAt;

  const pairs = [
    { label: 'Enrollment status', value: enrolledAt ? 'Enrolled' : (formatOptional(status.enrollmentStatus) || 'Not enrolled') },
    { label: 'Enrolled at', value: enrolledAt ? new Date(enrolledAt).toLocaleString() : '—' },
    { label: 'Vehicle status', value: formatOptional(vehicleStatus) },
    { label: 'Last seen', value: lastSeenAt ? new Date(lastSeenAt).toLocaleString() : '—' },
    { label: 'Status refreshed at', value: refreshedAt ? new Date(refreshedAt).toLocaleString() : '—' },
    // M-MGR fields (populated when enrolled via bulk-enroll flow)
    ...(status.enrollmentStatus && status.enrollmentStatus !== 'UNKNOWN' ? [{ label: 'OEM1 enrollment status', value: formatOptional(status.enrollmentStatus) }] : []),
    ...((vehicle as any).oem1_active_sku ? [{ label: 'Active SKU', value: formatOptional((vehicle as any).oem1_active_sku) }] : []),
    ...(status.readiness ? [{ label: 'Readiness summary', value: formatOptional(status.readiness) }] : []),
  ];

  return (
    <Container
      data-testid="oem1-enrollment-panel"
      header={
        <Header
          variant="h2"
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {canRefresh && (
                <Button
                  data-testid="refresh-now-btn"
                  loading={refreshing}
                  disabled={isRateLimited}
                  onClick={handleRefresh}
                  iconName="refresh"
                >
                  {refreshLabel}
                </Button>
              )}
              {showRetry && (
                <Button data-testid="retry-enrollment-btn" variant="normal" onClick={() => {}}>
                  Retry enrollment
                </Button>
              )}
            </SpaceBetween>
          }
        >
          Off-board enrollment &amp; readiness
        </Header>
      }
    >
      <SpaceBetween size="m">
        <KeyValuePairs columns={3} items={pairs} />
        {refreshError && <StatusIndicator type="error">{refreshError}</StatusIndicator>}
        <Link data-testid="enrollment-history-link" href={`#enrollment-history-${encodeURIComponent(vin)}`}>
          View enrollment history
        </Link>
      </SpaceBetween>
    </Container>
  );
};
import SimulationLogViewer from './SimulationLogViewer';
// SimulationLogViewer not used directly - logs shown in SimLogViewer
import VehicleFinancialWidget from './VehicleFinancialWidget';
import ScheduleServiceModal from './ScheduleServiceModal';
import './vehicle-detail-tabs-borderless.css';
import { useIsEngineerTenant } from '@/auth/useIsEngineerTenant';
import EngineeringVehicleDetailView from '@/components/engineering/EngineeringVehicleDetailView';
import type { VehicleItem } from '@/types/fleet-types';
import { isOEM1Vehicle, getVehicleSource } from '@/types/fleet-types';
import KeyValuePairs from '@cloudscape-design/components/key-value-pairs';
import Link from '@cloudscape-design/components/link';
import { oem1RefreshStatus } from '@/api/oem1RefreshStatus';
import { useUserRole } from '@/auth/useUserRole';
import { getOEM1Status } from '@/types/fleet-types';

interface VehicleMetadata {
  vin: string;
  licensePlate: string;
  make: string;
  model: string;
  year: number;
  color: string;
  vehicleType: string;
  fleetId: string;
  fleetName: string;
  fuelType: string;
  status: string;
  vehicleStatus?: string;
  enrollmentStatus?: string;
  enrolledAt?: string;
  activatedAt?: string;
  lastSeenAt?: string;
  connectionStatus: string;
  activityStatus: string;
  lastConnected: string | null;
  lastDisconnected: string | null;
  odometer: number;
  mileage?: number;
  fuelLevel: number;
  batteryLevel: number;
  lastMaintenance: string;
  nextMaintenanceDue: string;
  insuranceExpiry: string;
  registrationExpiry: string;
  driverAssigned: string;
  autoRegistered: boolean;
  hasCertificate?: boolean;
  updatedAt: string;
  createdAt?: string;
  // Two location shapes preserved per docs/tech.md § "Vehicle API field
  // convention": currentLocation = live telemetry (camelCase nested);
  // lastKnownLocation = simpler last-seen (lat/lng allowlist). Legacy
  // snake-case `location` shape collapsed into currentLocation by the
  // Lambda boundary (_camelize is non-recursive — UI consumes
  // currentLocation only).
  lastKnownLocation?: {
    lat: number;
    lng: number;
  };
  currentLocation?: {
    latitude: number;
    longitude: number;
    address: string;
    lastUpdated: number;
  };
  calculatedOdometer?: number;
  calculatedOdometerKm?: number;
  // Lambda-attached driver context (main_api/index.py around line 5677)
  currentDriverName?: string | null;
  currentDriverId?: string | null;
  currentDriver?: { fullName?: string; driverId?: string } | null;
}

interface Trip {
  tripId: string;
  vehicleId: string;
  startTime: number;
  endTime?: number;
  duration: number;
  distance: number;
  totalDistance?: number;
  driverName: string;
  driverScore: number;
  safetyEventsCount?: number;
  route?: Array<{
    lat: number | string;
    lng: number | string;
    timestamp?: number;
  }>;
}

interface SafetyEvent {
  eventId: string;
  tripId: string;
  vehicleId: string;
  timestamp: number;
  eventType: string;
  message: string;
  severity: string;
  speed?: number;
  // lat/lng allowlist per docs/tech.md (abbreviation form, not snake_case)
  lat?: number;
  lng?: number;
}

interface MaintenanceAlert {
  alertId: string;
  vehicleId: string;
  alertType: string;
  severity: string;
  description: string;
  dueDate?: string;
  status?: string;
}

const VehicleDetailView: React.FC<{ vehicleIdProp?: string }> = ({ vehicleIdProp }) => {
  
  const params = useParams<{ vehicleId: string }>();
  const vehicleId = vehicleIdProp || params.vehicleId;
  const navigate = useNavigate();
  const { setVehicleVin } = useVehicle();
  const userContext = useContext(UserContext);
  const { getAuthHeaders } = useAuth();
  
  
  const [activeTab, setActiveTab] = useState(() => {
    const params = new URLSearchParams(window.location.search);
    return params.get('tab') || 'overview';
  });
  const [scheduleModalVisible, setScheduleModalVisible] = useState(false);
  const [invoiceKey, setInvoiceKey] = useState('');
  const [invoiceVisible, setInvoiceVisible] = useState(false);
  const [selectedServiceAlerts, setSelectedServiceAlerts] = useState<any[]>([]);
  const [activeSimId, setActiveSimId] = useState<string | null>(null);
  const [showSimulator, setShowSimulator] = useState(false);
  
  // Data states
  const [vehicleData, setVehicleData] = useState<VehicleMetadata | null>(null);
  const [trips, setTrips] = useState<Trip[]>([]);
  const [tripsTotal, setTripsTotal] = useState(0);
  const [hasMoreTrips, setHasMoreTrips] = useState(false);
  const [tripsPagination, setTripsPagination] = useState({
    currentPage: 1,
    pageSize: 20,
    totalItems: 0,
    totalPages: 0,
    hasNextPage: false,
    hasPrevPage: false
  });
  const [safetyEvents, setSafetyEvents] = useState<SafetyEvent[]>([]);
  const [safetyEventsTotal, setSafetyEventsTotal] = useState(0);
  const [hasMoreSafetyEvents, setHasMoreSafetyEvents] = useState(false);
  const [safetyEventsPagination, setSafetyEventsPagination] = useState({
    currentPage: 1,
    pageSize: 20,
    totalItems: 0,
    totalPages: 0
  });
  const [maintenanceAlerts, setMaintenanceAlerts] = useState<MaintenanceAlert[]>([]);
  const [maintenanceAlertsTotal, setMaintenanceAlertsTotal] = useState(0);
  const [serviceFilter, setServiceFilter] = useState("ACTIVE");
  const [hasMoreMaintenanceAlerts, setHasMoreMaintenanceAlerts] = useState(false);
  const [maintenanceAlertsPagination, setMaintenanceAlertsPagination] = useState({
    currentPage: 1,
    pageSize: 20,
    totalItems: 0,
    totalPages: 0
  });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [debugData, setDebugData] = useState<any>(null);
  const [agentRunning, setAgentRunning] = useState(false);
  const [agentLoading, setAgentLoading] = useState(false);
  const [simReachable, setSimReachable] = useState(false);
  
  // Separate pagination state for each table
  const [tripsCurrentPage, setTripsCurrentPage] = useState(1);
  const [tripsPageSize, setTripsPageSize] = useState(10);
  const [safetyCurrentPage, setSafetyCurrentPage] = useState(1);
  const [safetyPageSize, setSafetyPageSize] = useState(10);
  const [maintenanceCurrentPage, setMaintenanceCurrentPage] = useState(1);
  const [maintenancePageSize, setMaintenancePageSize] = useState(10);
  
  // Last trip details for overview map
  const [lastTripDetails, setLastTripDetails] = useState<any>(null);
  const [loadingLastTrip, setLoadingLastTrip] = useState(false);
  
  // Latest telemetry data for tire pressure and other metrics
  const [latestTelemetry, setLatestTelemetry] = useState<any>(null);

  // Request deduplication
  const [selectedEvent, setSelectedEvent] = useState<any | null>(null);
  const [locationModalVisible, setLocationModalVisible] = useState(false);
  const [ongoingRequests, setOngoingRequests] = useState<Set<string>>(new Set());
  const [campaignsData, setCampaignsData] = useState<any>(null);

  // -----------------------------------------------------------------
  // Active DTC severity counts — drives the red "X critical DTC(s)"
  // pill in the header and the DTC tab badge. We fetch once at page
  // load rather than waiting for the user to click the DTCs tab so
  // the signal is visible immediately. VehicleDTCsTable does its own
  // fetch when the tab is opened (it needs the full list for its
  // table + filters); duplicating this small summary call is cheaper
  // than coordinating state across two tabs, and the DDB query is
  // ~10-20ms.
  // -----------------------------------------------------------------
  const [activeDtcSeverityCounts, setActiveDtcSeverityCounts] = useState<{
    critical: number;
    high: number;
    medium: number;
    low: number;
    unknown: number;
    total: number;
  }>({ critical: 0, high: 0, medium: 0, low: 0, unknown: 0, total: 0 });

  const handleLocationClick = (location: {latitude: number, longitude: number}, event?: any) => {
    setSelectedEvent(event || { location });
    setLocationModalVisible(true);
  };

  // Derived data for recent activity table
  const recentActivity = [
    ...trips.slice(0, 3).map((trip: Trip) => ({
      type: 'Trip',
      date: new Date(trip.startTime > 9999999999 ? trip.startTime : trip.startTime * 1000).toLocaleDateString(),
      description: `${trip.driverName || 'Unknown Driver'} - ${parseFloat(String(trip.totalDistance || trip.distance || 0)).toFixed(1)} km`
    })),
    ...maintenanceAlerts.slice(0, 2).map((alert: MaintenanceAlert) => ({
      type: 'Maintenance',
      date: alert.dueDate ? new Date(alert.dueDate).toLocaleDateString() : 'N/A',
      description: `${alert.alertType} - ${alert.severity}`
    }))
  ];

  const isRequestInProgress = (requestKey: string): boolean => {
    return ongoingRequests.has(requestKey);
  };

  const markRequestStarted = (requestKey: string) => {
    setOngoingRequests(prev => new Set(prev).add(requestKey));
  };

  const markRequestCompleted = (requestKey: string) => {
    setOngoingRequests(prev => {
      const newSet = new Set(prev);
      newSet.delete(requestKey);
      return newSet;
    });
  };

  useEffect(() => {
    if (vehicleId && vehicleId !== 'undefined') {
      fetchVehicleData();
    } else {
      setError('Invalid vehicle identifier');
      setLoading(false);
    }
  }, [vehicleId]);

  // -----------------------------------------------------------------
  // Active DTC severity breakdown — fetches a small summary of the
  // vehicle's currently-active DTCs so the page header can render
  // a red "X critical DTC(s)" pill and the DTCs tab can show a
  // counter badge without waiting for the user to click into the
  // DTCs tab. Re-runs when the vehicleId changes.
  //
  // We don't need the full DTC payload here — just counts by
  // severity. We ask for up to 200 ACTIVE rows (well above anything
  // realistic for one vehicle) and tally client-side. The DDB query
  // is already fast and is the same one VehicleDTCsTable makes when
  // opened, so worst case we have one extra 10-20ms round-trip.
  // -----------------------------------------------------------------
  useEffect(() => {
    if (!vehicleId) return;
    const apiEndpoint = getApiEndpoint();
    if (!apiEndpoint) return;
    let cancelled = false;
    (async () => {
      try {
        const url = `${apiEndpoint}api/v1/vehicles/${vehicleId}/dtcs?status=ACTIVE&limit=200`;
        const res = await fetch(url, {
          headers: { 'Content-Type': 'application/json', ...getAuthHeaders() },
        });
        if (!res.ok) return;
        const data = await res.json();
        const dtcs: Array<{ severity?: string }> = data.dtcs || [];
        const counts = { critical: 0, high: 0, medium: 0, low: 0, unknown: 0, total: dtcs.length };
        for (const d of dtcs) {
          const sev = (d.severity || '').toUpperCase();
          if (sev === 'CRITICAL') counts.critical += 1;
          else if (sev === 'HIGH') counts.high += 1;
          else if (sev === 'MEDIUM') counts.medium += 1;
          else if (sev === 'LOW') counts.low += 1;
          else counts.unknown += 1;
        }
        if (!cancelled) setActiveDtcSeverityCounts(counts);
      } catch (e) {
        // Soft-fail: absence of this widget shouldn't break the page.
      }
    })();
    return () => { cancelled = true; };
    // IMPORTANT: deliberately omit `getAuthHeaders` from deps.
    // useAuth() returns a fresh getAuthHeaders closure on every render,
    // so including it here causes an infinite re-fetch loop (setState
    // → re-render → new getAuthHeaders → effect fires → setState → …).
    // We only need to re-run when vehicleId changes; the function is
    // called inside the effect at invocation time, which always reads
    // the latest token via the closure over simpleAuth.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [vehicleId]);

  // Update document title when vehicle data loads
  useEffect(() => {
    if (vehicleData?.vin) {
      document.title = `${vehicleData.vin} - Vehicle Details`;
    } else if (vehicleId) {
      document.title = `${vehicleId} - Vehicle Details`;
    }
  }, [vehicleData, vehicleId]);

  // Poll FWE agent status for THIS vehicle
  useEffect(() => {
    if (!vehicleData?.vin) return;
    const checkStatus = () => {
      const base = getSimulationApiBase();
      fetch(`${base}/api/simulation/agent/status`)
        .then(r => r.ok ? r.json() : null)
        .then(d => {
          if (d) {
            setSimReachable(true);
            const thisVehicleRunning = (d.agents || []).some((a: any) => 
              (a.status === 'RUNNING' || a.status === 'PENDING' || a.status === 'PROVISIONING') &&
              (a.vin === vehicleData.vin || a.vehicleName === vehicleData.vin)
            );
            setAgentRunning(thisVehicleRunning);
          }
        })
        .catch(() => {
          if (getSimulationMode() === 'cloud') setSimReachable(true);
          else setSimReachable(false);
        });
    };
    checkStatus();
    const id = setInterval(checkStatus, 10000);
    return () => clearInterval(id);
  }, [vehicleData?.vin]);

  const toggleAgent = async () => {
    if (!vehicleData) return;
    setAgentLoading(true);
    try {
      const base = getSimulationApiBase();
      if (agentRunning) {
        await fetch(`${base}/api/simulation/agent/stop`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ vin: vehicleData.vin })
        });
        setAgentRunning(false);
      } else {
        await fetch(`${base}/api/simulation/agent/start`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ vin: vehicleData.vin, vehicleId: vehicleId })
        });
        setAgentRunning(true);
      }
    } catch (e) { console.error('Agent toggle failed:', e); }
    setAgentLoading(false);
  };

  // Fetch last trip details when trips are loaded
  // Remove this useEffect since lastTrip details now come from vehicle response
  // useEffect(() => {
  //   if (trips.length > 0 && !lastTripDetails) {
  //     fetchLastTripDetails();
  //   }
  // }, [trips]);

  // Helper functions for pagination
  const getPaginatedTrips = () => {
    const startIndex = (tripsCurrentPage - 1) * tripsPageSize;
    const endIndex = startIndex + tripsPageSize;
    return trips.slice(startIndex, endIndex);
  };

  const getPaginatedSafetyEvents = () => {
    const startIndex = (safetyCurrentPage - 1) * safetyPageSize;
    const endIndex = startIndex + safetyPageSize;
    return safetyEvents.slice(startIndex, endIndex);
  };

  const getPaginatedMaintenanceAlerts = () => {
    const startIndex = (maintenanceCurrentPage - 1) * maintenancePageSize;
    const endIndex = startIndex + maintenancePageSize;
    return maintenanceAlerts.slice(startIndex, endIndex);
  };

  // Server-side pagination functions
  const fetchTripsPage = async (page: number) => {
    // For now, use client-side pagination since backend may not support it
    setTripsCurrentPage(page);
  };

  const fetchSafetyEventsPage = async (page: number) => {
    try {
      setLoading(true);
      const apiEndpoint = getRuntimeConfig().apiEndpoint;
      const response = await fetch(`${apiEndpoint}api/v1/vehicles/${vehicleId}/safety-events?page=${page}&limit=${safetyPageSize}`, {
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        }
      });
      
      if (response.ok) {
        const data = await response.json();
        setSafetyEvents(data.safetyEvents || []);
        setSafetyEventsTotal(data.total || 0);
        setSafetyCurrentPage(page);
      }
    } catch (error) {
      console.error('Error fetching safety events page:', error);
    } finally {
      setLoading(false);
    }
  };

  const fetchMaintenanceAlertsPage = async (page: number) => {
    // Use client-side pagination since backend returns mixed alert types
    setMaintenanceCurrentPage(page);
  };

  // Helper function to format coordinates as address
  const formatLocationAddress = (lat: number, lon: number) => {
    if (!lat || !lon) return 'Unknown location';
    return `${lat.toFixed(4)}, ${lon.toFixed(4)}`;
  };

  const fetchVehicleData = async () => {
    if (!vehicleId || vehicleId === 'undefined') {
      setError('Invalid vehicle identifier');
      setLoading(false);
      return;
    }

    const requestKey = `vehicle-${vehicleId}`;
    
    if (isRequestInProgress(requestKey)) {
      return;
    }

    try {
      markRequestStarted(requestKey);
      setLoading(true);
      setError(null);
      
      const runtimeConfig = getRuntimeConfig();
      const apiEndpoint = runtimeConfig.apiEndpoint;
      
      
      const response = await fetch(`${apiEndpoint}api/v1/vehicles/${vehicleId}`, {
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        }
      });
      
      if (!response.ok) {
        throw new Error(`Failed to fetch vehicle: ${response.status} ${response.statusText}`);
      }
      
      const data = await response.json();
      
      // Handle consolidated response format
      if (data.vehicle) {
        // New consolidated format
        setVehicleData(data.vehicle);
        setVehicleVin(data.vehicle.vin || null);
        
        // Set trips data (sorted by most recent first)
        if (data.trips) {
          const sortedItems = (data.trips.items || []).sort((a: any, b: any) => {
            const tsA = Number(a.timestamp || a.startTime || 0);
            const tsB = Number(b.timestamp || b.startTime || 0);
            return tsB - tsA;
          });
          setTrips(sortedItems);
          setTripsTotal(data.trips.total || 0);
          setHasMoreTrips(data.trips.hasMore || false);
        }
        
        // Set safety alerts data
        if (data.safetyAlerts) {
          setSafetyEvents(data.safetyAlerts.items || []);
          setSafetyEventsTotal(data.safetyAlerts.total || 0);
          setHasMoreSafetyEvents(data.safetyAlerts.hasMore || false);
        } else {
        }
        
        // Set maintenance alerts data
        if (data.maintenanceAlerts) {
          // Get all maintenance alerts from the items array.
          //
          // The /api/v1/vehicles/{id} response merges two underlying tables:
          // (1) maintenance_alerts (uppercase status: OPEN, IN_PROGRESS,
          //     COMPLETED) and (2) service_history. Service-history rows
          //     are written with mixed casing — older seed/historical rows
          //     use uppercase ("COMPLETED", "SCHEDULED"), but the voice
          //     agent's book() tool and _approve_dtc_action_followups now
          //     write lowercase "scheduled" (see main_api/index.py L380-L389
          //     which calls out the iOS read path also tolerates both).
          //     The CMS UI's filter, status indicator, type badge and
          //     invoice-link logic all do exact-case comparisons against
          //     uppercase canonical values, so lowercase "scheduled" rows
          //     were silently filtered out of every view (the "Scheduled"
          //     dropdown option in particular looked broken). Normalising
          //     here at the single data-load boundary keeps every downstream
          //     comparison working without scattering toUpperCase() calls
          //     across the file. Falsy (undefined/null) statuses are passed
          //     through unchanged so the !a.status check in the OPEN filter
          //     still treats stale rows as open alerts.
          const rawAlerts = data.maintenanceAlerts.items || data.maintenanceAlerts.alerts || [];
          const alerts = rawAlerts.map((a: any) => ({
            ...a,
            status: a.status ? String(a.status).toUpperCase() : a.status,
          }));
          setMaintenanceAlerts(alerts);
          setMaintenanceAlertsTotal(data.maintenanceAlerts.total || alerts.length);
          setHasMoreMaintenanceAlerts(data.maintenanceAlerts.hasMore || false);
        } else {
        }
        
        // Set campaigns count from consolidated response
        if (data.campaigns) {
          setCampaignsData(data.campaigns || { items: [], total: 0 });
        }
        
        // Set last trip: prefer the server-assembled lastTrip (has route data),
        // fall back to most recent trip from the list (no route, map won't show).
        const lastTripFromServer = data.lastTrip;
        if (lastTripFromServer) {
          setLastTripDetails(lastTripFromServer);
          setLoadingLastTrip(false);
        } else if (data.trips && data.trips.items && data.trips.items.length > 0) {
          const sorted = [...data.trips.items].sort((a: any, b: any) => {
            const tsA = Number(a.timestamp || a.startTime || 0);
            const tsB = Number(b.timestamp || b.startTime || 0);
            return tsB - tsA;
          });
          setLastTripDetails(sorted[0]);
          setLoadingLastTrip(false);
        } else {
          setLoadingLastTrip(false);
        }
        
        // Set latest telemetry data
        if (data.latestTelemetry) {
          setLatestTelemetry(data.latestTelemetry);
        } else {
          setLatestTelemetry(null);
        }
        
      } else {
        // Fallback to old format
        let vehicle;
        if (data.vehicles && Array.isArray(data.vehicles)) {
          vehicle = data.vehicles.find((v: any) => v.vehicleId === vehicleId);
          if (!vehicle) {
            setError(`Vehicle with ID ${vehicleId} not found`);
            setLoading(false);
            markRequestCompleted(requestKey);
            return;
          }
        } else if (data.vehicleId || data.vin) {
          vehicle = data;
        } else {
          setError(`Vehicle with ID ${vehicleId} not found`);
          setLoading(false);
          markRequestCompleted(requestKey);
          return;
        }
        
        setVehicleData(vehicle);
        setVehicleVin(vehicle.vin || null);
      }
      
      setLoading(false);
      
    } catch (error) {
      console.error('❌ Error fetching vehicle data:', error);
      setError(error instanceof Error ? error.message : 'Failed to fetch vehicle data');
      setLoading(false);
    } finally {
      markRequestCompleted(requestKey);
    }
  };

  const handleViewTrip = (trip: Trip) => {
    const tripId = trip.tripId;
    const encodedTripId = encodeURIComponent(tripId);
    const url = `${UI_ROUTES.VEHICLE_MANAGEMENT}/${vehicleId}/trips/${encodedTripId}`;
    navigate(url);
  };

  // fetchLastTripDetails function removed - lastTrip now comes from vehicle detail response

  // Stub pagination functions for now
  const fetchTrips = async (page: number = 1, pageSize: number = 20) => {
  };

  const fetchSafetyEvents = async (page: number = 1, pageSize: number = 20) => {
  };

  const fetchMaintenanceAlerts = async (page: number = 1, pageSize: number = 20) => {
  };

  // Engineering tenant branch — hook is called UNCONDITIONALLY here (above
  // all early-return guards) so the hook count stays stable across renders.
  // The conditional return below uses the already-computed value.
  const isEngineerTenant = useIsEngineerTenant({ tenantType: (vehicleData as any)?.tenantType });
  const { isAdmin, isOperator, fleetIds } = useUserRole();
  const isOEM1 = vehicleData != null && isOEM1Vehicle(vehicleData as unknown as VehicleItem);
  const canRefreshOEM1: boolean =
    isOEM1 && (isAdmin || (isOperator && (vehicleData as any)?.fleetId != null && fleetIds.includes((vehicleData as any).fleetId)));

  if (loading) {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <Spinner size="large" />
        <Box variant="p" margin={{ top: 'm' }}>Loading vehicle details...</Box>
      </div>
    );
  }

  if (error) {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <Alert type="error" header="Error loading vehicle">
          {error}
        </Alert>
      </div>
    );
  }

  if (!vehicleData) {
    return (
      <div style={{ padding: '20px', textAlign: 'center' }}>
        <Box variant="p">No vehicle data available</Box>
      </div>
    );
  }

  // Engineering branch using the already-computed value (no hook call here).
  if (isEngineerTenant) {
    return <EngineeringVehicleDetailView vehicle={vehicleData as unknown as VehicleItem} />;
  }

  return (
    <Container>
      <SpaceBetween size="l">
        {/* Header */}
        <Header
          variant="h1"
          description={
            // Render a red "X critical DTC(s)" pill when there are
            // active CRITICAL DTCs, so operators opening a vehicle
            // can't miss a serious issue. Also surface HIGH DTCs in
            // an amber pill (only when there are no CRITICALs, to
            // avoid split attention). Clicking either pill switches
            // the detail view's active tab to "dtcs" so the operator
            // lands directly on the table.
            activeDtcSeverityCounts.critical > 0 ? (
              <Box
                color="text-status-error"
                fontSize="body-s"
                // Using role+onClick rather than a <Button> so the
                // visual weight matches a subtitle, not a CTA.
              >
                <span
                  role="button"
                  tabIndex={0}
                  onClick={() => setActiveTab('dtcs')}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') setActiveTab('dtcs');
                  }}
                  style={{ cursor: 'pointer', marginRight: 8 }}
                >
                  <Badge color="red">
                    ⚠ {activeDtcSeverityCounts.critical} critical DTC
                    {activeDtcSeverityCounts.critical === 1 ? '' : 's'}
                  </Badge>
                </span>
                {activeDtcSeverityCounts.high > 0 && (
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={() => setActiveTab('dtcs')}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter' || e.key === ' ') setActiveTab('dtcs');
                    }}
                    style={{ cursor: 'pointer' }}
                  >
                    <Badge color="severity-high">
                      {activeDtcSeverityCounts.high} high
                    </Badge>
                  </span>
                )}
              </Box>
            ) : activeDtcSeverityCounts.high > 0 ? (
              <Box fontSize="body-s">
                <span
                  role="button"
                  tabIndex={0}
                  onClick={() => setActiveTab('dtcs')}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' || e.key === ' ') setActiveTab('dtcs');
                  }}
                  style={{ cursor: 'pointer' }}
                >
                  <Badge color="severity-high">
                    {activeDtcSeverityCounts.high} high-severity DTC
                    {activeDtcSeverityCounts.high === 1 ? '' : 's'}
                  </Badge>
                </span>
              </Box>
            ) : undefined
          }
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              {!isOEM1 && (
                <>
                  <span
                    title={
                      !simReachable
                        ? 'Simulator is offline'
                        : vehicleData?.hasCertificate === false
                          ? 'No certificate provisioned for this vehicle'
                          : ''
                    }
                  >
                    <Button
                      loading={agentLoading}
                      disabled={!simReachable || vehicleData?.hasCertificate === false}
                      iconName={agentRunning ? 'close' : 'caret-right-filled'}
                      onClick={toggleAgent}
                    >
                      {agentRunning ? 'Stop Agent' : 'Start Agent'}
                    </Button>
                  </span>
                  <Button disabled={!simReachable} iconName="caret-right-filled" onClick={() => setShowSimulator(true)}>
                    {simReachable ? 'Trip Simulator' : 'Simulator Offline'}
                  </Button>
                </>
              )}
            </SpaceBetween>
          }
        >
          Vehicle Details: {vehicleData?.vin || vehicleId}
        </Header>
        <TripSimulatorModal
          visible={showSimulator}
          vehicleId={vehicleId!}
          vin={vehicleData?.vin}
          onDismiss={() => setShowSimulator(false)}
          onStarted={(simId) => { setActiveSimId(simId); setActiveTab('logs'); }}
        />
          {/* Tabs */}
          <Tabs
            activeTabId={activeTab}
            onChange={({ detail }) => setActiveTab(detail.activeTabId)}
            tabs={[
                {
                  id: "overview",
                  label: "Overview",
                  content: (
                    <SpaceBetween size="l">
                      {/* Quick Status KPI Cards */}
                      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '12px' }}>
                        <Container>
                          <SpaceBetween size="xxs">
                            <span style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Odometer</span>
                            <span style={{ fontSize: '24px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{vehicleData.calculatedOdometer?.toLocaleString() || vehicleData.odometer?.toLocaleString() || (isOEM1 ? '—' : '0')} {!isOEM1 || vehicleData.odometer ? 'mi' : ''}</span>
                          </SpaceBetween>
                        </Container>
                        <Container>
                          <SpaceBetween size="xxs">
                            <span style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Trips</span>
                            <span style={{ fontSize: '24px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{tripsTotal}</span>
                          </SpaceBetween>
                        </Container>
                        <Container>
                          <SpaceBetween size="xxs">
                            <span style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Service Alerts</span>
                            {(() => {
                              // Derive open/closed split from the loaded maintenanceAlerts.
                              // Alerts with missing status default to OPEN (matches the
                              // filter semantic on ~line 1071 elsewhere in this view).
                              // If hasMoreMaintenanceAlerts is true, some alerts aren't
                              // loaded yet — we surface that with a "+" suffix rather
                              // than lying about the counts.
                              const openCount = maintenanceAlerts.filter(a => !a.status || a.status === 'OPEN').length;
                              const completedCount = maintenanceAlerts.filter(a => a.status === 'COMPLETED').length;
                              const suffix = hasMoreMaintenanceAlerts ? '+' : '';
                              return (
                                <>
                                  <span style={{ fontSize: '24px', fontWeight: 700, display: 'block', lineHeight: 1.2, color: openCount > 0 ? '#8D6605' : undefined }}>
                                    {openCount}{suffix} <span style={{ fontSize: '12px', fontWeight: 500, color: '#656871' }}>open</span>
                                  </span>
                                  <span style={{ fontSize: '12px', color: '#656871', display: 'block', marginTop: '2px' }}>
                                    {completedCount}{suffix} completed
                                  </span>
                                </>
                              );
                            })()}
                          </SpaceBetween>
                        </Container>
                        <Container>
                          <SpaceBetween size="xxs">
                            <span style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Active Recalls</span>
                            <span style={{ fontSize: '24px', fontWeight: 700, display: 'block', lineHeight: 1.2, color: (nhtsaRecalls.filter(r => r.vehicles?.includes(vehicleId)).length > 0) ? '#d91515' : undefined }}>
                              {nhtsaRecalls.filter(r => r.vehicles?.includes(vehicleId)).length}
                            </span>
                          </SpaceBetween>
                        </Container>
                        <Container>
                          <SpaceBetween size="xxs">
                            <span style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Warranty Claims</span>
                            <span style={{ fontSize: '24px', fontWeight: 700, display: 'block', lineHeight: 1.2, color: warrantyVehicles.includes(vehicleId) ? '#8D6605' : undefined }}>
                              {warrantyVehicles.includes(vehicleId) ? '1+' : '0'}
                            </span>
                          </SpaceBetween>
                        </Container>
                        <Container>
                          <SpaceBetween size="xxs">
                            {/* Top-of-page energy chip: label flips
                                between "Fuel" and "Battery" based on
                                fuelType so BEVs don't confusingly show
                                a "Fuel Level" header above their SOC. */}
                            <span style={{ fontSize: '11px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>
                              {(() => {
                                const ft = (vehicleData.fuelType || '').toLowerCase();
                                const isEV = ft === 'bev' || ft === 'electric' || ft === 'ev';
                                return isEV ? 'Battery' : 'Fuel Level';
                              })()}
                            </span>
                            <span style={{ fontSize: '24px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{isOEM1 && !vehicleData.fuelLevel ? '—' : `${Math.round((vehicleData.fuelLevel || 0) * 10) / 10}%`}</span>
                          </SpaceBetween>
                        </Container>
                      </div>

                      {/* Vehicle Details and Map in one container */}
                      <Container
                        header={<Header variant="h2">Vehicle Information</Header>}
                      >
                        <SpaceBetween size="l">
                          {/* All Vehicle Metadata - 4x4 Grid */}
                          <ColumnLayout columns={4} variant="text-grid">
                            {/* Row 1 */}
                            <div>
                              <Box variant="awsui-key-label">VIN</Box>
                              <div>{vehicleData.vin || vehicleId}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Make & Model</Box>
                              <div>{vehicleData.make} {vehicleData.model} ({vehicleData.year})</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">License Plate</Box>
                              <div>{vehicleData.licensePlate || 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Color</Box>
                              <div>{vehicleData.color || 'N/A'}</div>
                            </div>

                            {/* Row 2 */}
                            <div>
                              <Box variant="awsui-key-label">Vehicle Type</Box>
                              <div>{vehicleData.vehicleType || 'N/A'}</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Source</Box>
                              <div>
                                <Badge color={isOEM1 ? 'severity-medium' : 'blue'}>
                                  {isOEM1 ? 'Off-board' : 'On-board'}
                                </Badge>
                              </div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Fleet</Box>
                              <div>{vehicleData.fleetName || 'Unassigned'}</div>
                            </div>
                            {(vehicleData as any).defaultVehicleModelId && (
                              <div>
                                <Box variant="awsui-key-label">Vehicle Model</Box>
                                <div style={{ fontFamily: 'monospace' }}>{(vehicleData as any).defaultVehicleModelId}</div>
                              </div>
                            )}
                            <div>
                              <Box variant="awsui-key-label">Driver Assigned</Box>
                              <div>{vehicleData.currentDriverName || vehicleData.driverAssigned || 'Unassigned'}</div>
                            </div>

                            {/* Row 3 - Status Badges */}
                            <SpaceBetween direction="vertical" size="xs">
                              <Box variant="awsui-key-label">Enrollment Status</Box>
                              {(() => {
                                // OEM1: enrolledAt present means enrolled; status drives active/inactive
                                const enrolledAt = (vehicleData as any).enrolledAt;
                                const oem1Status = (vehicleData as any).status;
                                if (isOEM1) {
                                  if (!enrolledAt) return <Badge color="grey">Not enrolled</Badge>;
                                  if (oem1Status === 'Connected' || oem1Status === 'Active') return <Badge color="green">Enrolled</Badge>;
                                  return <Badge color="blue">Enrolled</Badge>;
                                }
                                return (
                                  <Badge color={
                                    vehicleData.enrollmentStatus === 'ACTIVE' ? 'green' :
                                    vehicleData.enrollmentStatus === 'ENROLLED' || vehicleData.enrollmentStatus === 'PENDING_ACTIVATION' ? 'blue' :
                                    vehicleData.enrollmentStatus === 'INACTIVE' ? 'red' : 'grey'
                                  }>
                                    {vehicleData.enrollmentStatus || 'NOT_ENROLLED'}
                                  </Badge>
                                );
                              })()}
                            </SpaceBetween>
                            {isOEM1
                              ? <StatusIndicator type="success">Enrolled</StatusIndicator>
                              : <VehicleStatusBadge vehicleStatus={vehicleData.vehicleStatus || vehicleData.status || 'UNKNOWN'} />
                            }
                            <div>
                              <SpaceBetween direction="vertical" size="xs">
                                {isOEM1 ? (
                                  <>
                                    <Box variant="awsui-key-label">Last data received</Box>
                                    <div>{(() => {
                                      const ts = (vehicleData as any).lastSeenAt;
                                      if (!ts) return 'Never';
                                      const d = new Date(ts);
                                      return isNaN(d.getTime()) ? 'N/A' : d.toLocaleString();
                                    })()}</div>
                                  </>
                                ) : (
                                  <>
                                    <Box variant="awsui-key-label">Connection Status</Box>
                                    <Badge color={vehicleData.connectionStatus === 'connected' ? 'blue' : 'red'}>
                                      {vehicleData.connectionStatus === 'connected' ? 'Connected' : 'Disconnected'}
                                    </Badge>
                                  </>
                                )}
                              </SpaceBetween>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Last Connected</Box>
                              <div>{(() => {
                                const ts = isOEM1
                                  ? (vehicleData as any).lastSeenAt
                                  : (vehicleData.lastConnectedAt || vehicleData.lastUpdated || vehicleData.lastConnected || vehicleData.lastSeenAt);
                                if (!ts) return 'N/A';
                                const n = Number(ts);
                                if (!isNaN(n) && n > 0) return new Date(n > 9999999999 ? n : n * 1000).toLocaleString();
                                const d = new Date(ts);
                                return isNaN(d.getTime()) ? 'N/A' : d.toLocaleString();
                              })()}</div>
                            </div>

                            {/* Row 4 - Metrics
                                Conditional energy display (2026-05-05):
                                for BEVs show "Battery %" (SOC), for ICE
                                show "Fuel %". Previously the page
                                unconditionally rendered both rows, which
                                meant ICE vehicles showed "Battery Level 0%"
                                next to a real Fuel value, and BEVs showed
                                "Fuel Level 0%" next to a real Battery
                                value. The underlying data has a single
                                fuelLevel field per vehicle (the backend
                                aliases ev_soc → fuelLevel for BEVs) so
                                we collapse to one row with label + icon
                                chosen by fuelType. Uses the same
                                lowercase-tolerant BEV detection as iOS. */}
                            <div>
                              <Box variant="awsui-key-label">
                                {(() => {
                                  const ft = (vehicleData.fuelType || '').toLowerCase();
                                  const isEV = ft === 'bev' || ft === 'electric' || ft === 'ev';
                                  return isEV ? 'Battery' : 'Fuel';
                                })()}
                              </Box>
                              <div>{Math.round((vehicleData.fuelLevel || 0) * 10) / 10}%</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Odometer</Box>
                              <div>{vehicleData.calculatedOdometer?.toLocaleString() || vehicleData.odometer?.toLocaleString() || 0} mi</div>
                            </div>
                            <div>
                              <Box variant="awsui-key-label">Last Updated</Box>
                              <div>{(() => {
                                const ts = vehicleData.lastUpdated || vehicleData.lastConnectedAt || vehicleData.lastSeenAt;
                                if (!ts) return 'N/A';
                                const n = Number(ts);
                                if (!isNaN(n) && n > 0) return new Date(n > 9999999999 ? n : n * 1000).toLocaleString();
                                const d = new Date(ts);
                                return isNaN(d.getTime()) ? 'N/A' : d.toLocaleString();
                              })()}</div>
                            </div>
                          </ColumnLayout>

                          {/* Vehicle Map */}
                          {(vehicleData.currentLocation || vehicleData.lastKnownLocation) && (
                            <div>
                              <Box variant="h3" margin={{ bottom: 's' }}>Vehicle Location</Box>
                              <TripMap
                                route={[]}
                                startLocation={
                                  vehicleData.currentLocation 
                                    ? { lat: vehicleData.currentLocation.latitude, lng: vehicleData.currentLocation.longitude, address: vehicleData.currentLocation.address }
                                    : vehicleData.lastKnownLocation 
                                      ? { lat: vehicleData.lastKnownLocation.lat, lng: vehicleData.lastKnownLocation.lng }
                                      : undefined
                                }
                                vehicleType={vehicleData.vehicleType || 'Sedan'}
                                safetyEvents={safetyEvents.map(event => ({
                                  latitude: event.lat || 0,
                                  longitude: event.lng || 0,
                                  eventType: event.eventType || event.message || 'Unknown',
                                  timestamp: event.timestamp,
                                  severity: event.severity
                                }))}
                                height="400px"
                              />
                              <Box margin={{ top: 's' }} variant="small" color="text-body-secondary">
                                {vehicleData.currentLocation ? (
                                  `Current location: ${vehicleData.currentLocation.address || `${vehicleData.currentLocation.latitude?.toFixed(4)}, ${vehicleData.currentLocation.longitude?.toFixed(4)}`} (Updated: ${new Date(vehicleData.currentLocation.lastUpdated > 9999999999 ? vehicleData.currentLocation.lastUpdated : vehicleData.currentLocation.lastUpdated * 1000).toLocaleString()})`
                                ) : (
                                  `Last known location: ${vehicleData.lastKnownLocation!.lat.toFixed(4)}, ${vehicleData.lastKnownLocation!.lng.toFixed(4)}`
                                )}
                              </Box>
                            </div>
                          )}
                        </SpaceBetween>
                      </Container>

                      {/* OEM1-specific panels — only shown for OEM1-sourced vehicles */}
                      {isOEM1 && (
                        <>
                          <OEM1EnrollmentPanel
                            vehicle={vehicleData as unknown as VehicleItem & { [k: string]: unknown }}
                            vin={vehicleData.vin || vehicleId || ''}
                            canRefresh={canRefreshOEM1}
                          />
                          <Box>
                            <Link href="#signal-coverage" onFollow={(e) => { e.preventDefault(); }}>
                              View off-board signal coverage (oem1-transform.json)
                            </Link>
                          </Box>
                        </>
                      )}

                      {/* Financial Overview */}
                      <VehicleFinancialWidget vehicleData={vehicleData} />

                      {/* Vehicle Health Score (server-computed via VSA backend).
                          Single source of truth shared with the iOS Home tab —
                          identical number on both surfaces by construction.
                          Placed alongside the financial overview so the at-a-
                          glance row of high-level vehicle KPIs reads:
                          [ Identity / location ]  →  [ Financials ]  →  [ Health ]. */}
                      <VehicleHealthScoreWidget
                        vehicleId={vehicleId || ''}
                        // Pass the Vehicle Information section's
                        // connection state down so the widget's KPI
                        // tile + score reconciliation align with what
                        // the operator already sees in the row above.
                        // Without this, VSA-backend's live-state
                        // result and CMS-API's vehicle.connectionStatus
                        // can diverge briefly during reconnects, and
                        // the operator sees one widget say
                        // "Connected" while another says "Offline" on
                        // the same page.
                        overrideConnectionStatus={vehicleData?.connectionStatus}
                        overrideLastConnectedAt={
                          (vehicleData as any)?.lastConnectedAt
                            || vehicleData?.lastSeenAt
                            || vehicleData?.lastUpdated
                            || null
                        }
                      />

                      {/* Vehicle Location, Tire Pressure, and Recent Activity */}
                      <ColumnLayout columns={3} variant="text-grid">
                        {/* Tire Pressure Monitor */}
                        <div style={{ minHeight: '420px' }}>
                          {(latestTelemetry && (latestTelemetry.tire_pressure_fl || latestTelemetry.tire_pressure_fr)) || vehicleData.tire_pressure_fl ? (
                          <TirePressureWidget
                            tirePressure={{
                              tire_fl: latestTelemetry?.tire_pressure_fl || vehicleData.tire_pressure_fl,
                              tire_fr: latestTelemetry?.tire_pressure_fr || vehicleData.tire_pressure_fr,
                              tire_rl: latestTelemetry?.tire_pressure_rl || vehicleData.tire_pressure_rl,
                              tire_rr: latestTelemetry?.tire_pressure_rr || vehicleData.tire_pressure_rr,
                              tire_temp_max: latestTelemetry?.tire_temp_max
                            }}
                            lastUpdated={latestTelemetry?.timestamp ? new Date(latestTelemetry.timestamp > 9999999999 ? latestTelemetry.timestamp : latestTelemetry.timestamp * 1000).toISOString() : undefined}
                          />
                          ) : (
                          <Container header={<Header variant="h3" description="Current tire pressure readings">Tire Pressure Monitor</Header>}>
                            <Box textAlign="center" color="text-status-inactive" padding="l">No tire pressure data available</Box>
                          </Container>
                          )}
                        </div>

                        {/* Last Trip Map */}
                        <div style={{ minHeight: '420px' }}>
                        <Container
                          header={<Header variant="h3">Last Trip</Header>}
                        >
                          {loadingLastTrip ? (
                            <Box textAlign="center" padding="xl">
                              <Spinner size="normal" />
                              <Box variant="p" color="text-body-secondary">Loading trip details...</Box>
                            </Box>
                          ) : trips.length > 0 && lastTripDetails ? (
                            <SpaceBetween size="m">
                              <TripMap
                                route={lastTripDetails.route || []}
                                startLocation={
                                  lastTripDetails.startLocation || 
                                  (lastTripDetails.route && lastTripDetails.route.length > 0 
                                    ? { lat: parseFloat(lastTripDetails.route[0].lat), lng: parseFloat(lastTripDetails.route[0].lng) }
                                    : undefined)
                                }
                                endLocation={
                                  lastTripDetails.endLocation || 
                                  (lastTripDetails.route && lastTripDetails.route.length > 0 
                                    ? { lat: parseFloat(lastTripDetails.route[lastTripDetails.route.length - 1].lat), lng: parseFloat(lastTripDetails.route[lastTripDetails.route.length - 1].lng) }
                                    : undefined)
                                }
                                showStartEndMarkers={true}
                                safetyEvents={safetyEvents.filter(event => 
                                  event.tripId === trips[0].tripId
                                ).map(event => ({
                                  latitude: event.lat || 0,
                                  longitude: event.lng || (lastTripDetails.route && lastTripDetails.route.length > 0 ? parseFloat(lastTripDetails.route[0].lng) : -74.0),
                                  eventType: event.eventType || event.message || 'Unknown',
                                  timestamp: event.timestamp,
                                  severity: event.severity
                                }))}
                                height="300px"
                              />
                              <Box variant="small" color="text-body-secondary">
                                {new Date(trips[0].startTime > 9999999999 ? trips[0].startTime : trips[0].startTime * 1000).toLocaleDateString()} - 
                                {trips[0].driverName || 'Unknown Driver'} - 
                                {(trips[0].distance || trips[0].totalDistance || 0).toFixed(1)} km
                              </Box>
                            </SpaceBetween>
                          ) : trips.length > 0 ? (
                            <Box textAlign="center" padding="xl" color="text-body-secondary">
                              <Box variant="strong" color="inherit">Trip route not available</Box>
                              <Box variant="p" color="inherit">
                                Route data for the last trip could not be loaded.
                              </Box>
                            </Box>
                          ) : (
                            <Box textAlign="center" padding="xl" color="text-body-secondary">
                              <Box variant="strong" color="inherit">No trips available</Box>
                              <Box variant="p" color="inherit">
                                Trip routes will appear here when trips are recorded.
                              </Box>
                            </Box>
                          )}
                        </Container>
                        </div>

                        {/* Recent Activity */}
                        <div style={{ minHeight: '420px' }}>
                          <Table
                            variant="container"
                            items={recentActivity}
                            header={<Header variant="h3">Recent Activity</Header>}
                            columnDefinitions={[
                              {
                                id: "type",
                                header: "Type",
                                cell: (item: any) => (
                                  <Badge color={item.type === 'Trip' ? 'blue' : item.type === 'Safety' ? 'red' : 'grey'}>
                                    {item.type}
                                  </Badge>
                                ),
                                width: 110,
                              },
                              {
                                id: "description",
                                header: "Description",
                                cell: (item: any) => (
                                  <span>
                                    <span style={{ color: '#666', fontSize: '12px', marginRight: 6 }}>{item.date}</span>
                                    {item.description}
                                  </span>
                                ),
                              }
                            ]}
                            empty={
                              <Box textAlign="center" color="text-body-secondary">No recent activity</Box>
                            }
                          />
                        </div>
                      </ColumnLayout>
                    </SpaceBetween>
                  )
                },
                {
                  id: "trips",
                  label: 'Trips',
                  content: (
                    <TripsTable
                      vehicleId={vehicleId}
                      showVehicleColumn={false}
                      showDriverColumn={true}
                      totalTripsCount={tripsTotal}
                    />
                  )
                },
                {
                  id: "maintenance",
                  label: 'Service',
                  content: (
                    <Container>
                      <SpaceBetween size="s">
                        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', flexWrap: 'nowrap', minHeight: '40px' }}>
                          <Header variant="h2" counter={`(${maintenanceAlertsTotal} total)`}>
                            Service
                          </Header>
                          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
                            <Select
                              selectedOption={{
                                value: serviceFilter,
                                label:
                                  serviceFilter === 'ACTIVE' ? 'Active (Open + Scheduled)' :
                                  serviceFilter === 'ALL' ? 'All Records' :
                                  serviceFilter === 'OPEN' ? 'Open Alerts Only' :
                                  serviceFilter === 'COMPLETED' ? 'Service History' :
                                  'Scheduled Only'
                              }}
                              onChange={({ detail }) => setServiceFilter(detail.selectedOption.value || 'ACTIVE')}
                              options={[
                                { value: 'ACTIVE', label: 'Active (Open + Scheduled)' },
                                { value: 'ALL', label: 'All Records' },
                                { value: 'OPEN', label: 'Open Alerts Only' },
                                { value: 'IN_PROGRESS', label: 'Scheduled Only' },
                                { value: 'COMPLETED', label: 'Service History' },
                              ]}
                            />
                            <Button variant="primary" disabled={selectedServiceAlerts.length === 0}
                              onClick={() => setScheduleModalVisible(true)}>
                              Schedule ({selectedServiceAlerts.length})
                            </Button>
                            <CollectionPreferences
                              title="Preferences"
                              confirmLabel="Confirm"
                              cancelLabel="Cancel"
                              preferences={{
                                pageSize: maintenancePageSize
                              }}
                              pageSizePreference={{
                                title: "Page size",
                                options: [
                                  { value: 10, label: "10 items" },
                                  { value: 20, label: "20 items" },
                                  { value: 50, label: "50 items" }
                                ]
                              }}
                            />
                            <Pagination
                              currentPageIndex={maintenanceCurrentPage}
                              pagesCount={Math.ceil(maintenanceAlertsTotal / maintenancePageSize)}
                              onChange={({ detail }) => fetchMaintenanceAlertsPage(detail.currentPageIndex)}
                            />
                          </div>
                        </div>
                        <Table
                      loading={loading}
                      loadingText="Loading service records..."
                      enableKeyboardNavigation={true}
                      selectionType="multi"
                      selectedItems={selectedServiceAlerts}
                      onSelectionChange={({ detail }) => setSelectedServiceAlerts(detail.selectedItems)}
                      items={getPaginatedMaintenanceAlerts().filter((a: any) => {
                        if (serviceFilter === 'ALL') return true;
                        // ACTIVE = work that needs operator attention. Bundles
                        // open alerts (no service booked yet) with scheduled
                        // ones (booked but not yet performed). This is the
                        // default landing view because operators want a single
                        // pane showing "what still needs to happen on this
                        // vehicle". Keep OPEN/IN_PROGRESS as separate options
                        // for the rarer case where someone wants only one
                        // bucket.
                        if (serviceFilter === 'ACTIVE') return !a.status || a.status === 'OPEN' || a.status === 'IN_PROGRESS' || a.status === 'SCHEDULED';
                        if (serviceFilter === 'OPEN') return a.status === 'OPEN' || (!a.status);
                        if (serviceFilter === 'COMPLETED') return a.status === 'COMPLETED';
                        if (serviceFilter === 'IN_PROGRESS') return a.status === 'IN_PROGRESS' || a.status === 'SCHEDULED';
                        return true;
                      })}
                      columnDefinitions={[
                        {
                          id: "type",
                          header: "Type",
                          cell: (item: any) => {
                            // Three logical buckets: Completed (work done),
                            // Scheduled (booked, awaiting service), Alert
                            // (open, needs triage). Status is already
                            // upper-cased on data load (see fetchVehicleData
                            // ~L575) so a single equality check covers both
                            // the legacy uppercase 'SCHEDULED' rows and the
                            // newer lowercase 'scheduled' rows from the
                            // voice agent's book() tool.
                            if (item.status === 'COMPLETED') {
                              return <Badge color="green">Completed</Badge>;
                            }
                            if (item.status === 'SCHEDULED' || item.status === 'IN_PROGRESS') {
                              return <Badge color="blue">Scheduled</Badge>;
                            }
                            const isUrgent = item.severity === 'CRITICAL' || item.severity === 'HIGH';
                            return <Badge color={isUrgent ? 'red' : 'blue'}>Alert</Badge>;
                          },
                          width: 110
                        },
                        {
                          id: "alertType",
                          header: "Service Type",
                          cell: (item: any) => {
                            // Raw values come in as e.g. 'maintenance.coolant_critical_overheat'
                            // (older alerts) or 'BRAKE_PAD_WEAR' (newer alerts) or
                            // 'VSA_VOICE_TRIAGE' (book() tool). The 'maintenance.'
                            // prefix is internal categorisation and has no
                            // operator value in this column — strip any leading
                            // dotted prefix, swap underscores for spaces, then
                            // title-case so "coolant_critical_overheat" reads as
                            // "Coolant Critical Overheat". A short allowlist of
                            // automotive acronyms (VSA, DEF, AC, DTC) is preserved
                            // in caps so 'VSA_VOICE_TRIAGE' renders as
                            // 'VSA Voice Triage' and 'AC_COMPRESSOR' as
                            // 'AC Compressor', without falsely preserving
                            // common 3-letter words like OIL, PAD, GAS that
                            // happen to be uppercase in the source enum.
                            const ACRONYMS = new Set(['VSA', 'DEF', 'AC', 'DTC', 'ABS', 'ECM', 'RPM', 'EGR', 'API', 'GPS']);
                            const raw = String(item.serviceType || item.alertType || item.type || 'Unknown');
                            const stripped = raw.replace(/^[^.]+\./, '').replace(/_/g, ' ');
                            return stripped
                              .split(/\s+/)
                              .filter(Boolean)
                              .map((word: string) => {
                                if (ACRONYMS.has(word.toUpperCase())) return word.toUpperCase();
                                return word.charAt(0).toUpperCase() + word.slice(1).toLowerCase();
                              })
                              .join(' ');
                          }
                        },
                        {
                          id: "severity",
                          header: "Severity",
                          cell: (item: any) => item.status === 'COMPLETED' ? (
                            <Badge color="grey">{item.category || '—'}</Badge>
                          ) : (
                            <Badge color={item.severity === 'HIGH' || item.severity === 'CRITICAL' ? 'red' : item.severity === 'MEDIUM' ? 'blue' : 'grey'}>
                              {item.severity || 'Unknown'}
                            </Badge>
                          ),
                          width: 100
                        },
                        {
                          id: "description",
                          header: "Description",
                          cell: (item: any) => {
                            // Source rows have wildly different description
                            // shapes:
                            //   • Newer alerts: short single-clause descriptions
                            //     ("Brake Pad Wear required for the connected truck").
                            //   • Older alerts: structured em-dash strings
                            //     ("Engine coolant critically overheated —
                            //     stop driving, let engine cool —
                            //     coolant_temp > 125.0 (actual: 182.2)") whose
                            //     trailing segment is a raw threshold dump
                            //     that's noise in a table cell.
                            //   • Service-history rows: dealer notes, varying
                            //     lengths.
                            // Display strategy:
                            //   1. Prefer the human-readable head segment
                            //      (everything before the first em-dash) —
                            //      this drops the raw threshold tail without
                            //      losing the "what's wrong" sentence.
                            //   2. If that head is still long, ellipsis-clip
                            //      it to keep the row a single line.
                            //   3. Wrap in a Popover so operators can still
                            //      see the full original text on hover/click
                            //      — no information is lost, just hidden.
                            const fullText = String(item.description || item.message || item.notes || 'N/A');
                            const head = fullText.split(/\s+—\s+|\s+--\s+/)[0];
                            const MAX = 60;
                            const summary = head.length > MAX ? head.slice(0, MAX).trimEnd() + '…' : head;
                            const needsPopover = summary !== fullText;
                            const cellSpan = (
                              <span
                                style={{
                                  display: 'inline-block',
                                  maxWidth: 320,
                                  overflow: 'hidden',
                                  textOverflow: 'ellipsis',
                                  whiteSpace: 'nowrap',
                                  verticalAlign: 'middle',
                                  cursor: needsPopover ? 'help' : 'default',
                                }}
                              >
                                {summary}
                              </span>
                            );
                            if (!needsPopover) return cellSpan;
                            return (
                              <Popover
                                dismissButton={false}
                                position="top"
                                size="large"
                                triggerType="text"
                                content={<Box variant="p">{fullText}</Box>}
                              >
                                {cellSpan}
                              </Popover>
                            );
                          }
                        },
                        {
                          id: "cost",
                          header: "Cost",
                          cell: (item: any) => {
                            const cost = item.cost || item.estimatedCost;
                            return cost ? `$${parseFloat(cost).toLocaleString()}` : '—';
                          },
                          width: 80
                        },
                        {
                          id: "provider",
                          header: "Provider",
                          cell: (item: any) => item.provider || '—',
                          width: 180
                        },
                        {
                          id: "date",
                          header: "Date",
                          cell: (item: any) => {
                            const d = item.serviceDate || item.dueDate || item.scheduledDate;
                            if (!d) return 'N/A';
                            if (typeof d === 'string' && d.includes('T')) return d.slice(0, 10);
                            const n = Number(d);
                            if (!isNaN(n) && n > 0) return new Date(n > 9999999999 ? n : n * 1000).toLocaleDateString();
                            return String(d).slice(0, 10);
                          },
                          width: 100
                        },
                        {
                          id: "status",
                          header: "Status",
                          cell: (item: any) => (
                            <StatusIndicator type={
                              item.status === 'COMPLETED' ? 'success' :
                              item.status === 'IN_PROGRESS' || item.status === 'SCHEDULED' ? 'in-progress' :
                              item.status === 'OPEN' ? 'warning' : 'pending'
                            }>
                              {item.status || 'Pending'}
                            </StatusIndicator>
                          ),
                          width: 110
                        },
                        {
                          id: "invoice",
                          header: "Invoice",
                          cell: (item: any) => {
                            if (item.status !== 'COMPLETED' || !item.serviceId) return '—';
                            const key = `service-invoices/INV-${item.serviceId}_${item.vehicleId || vehicleId}_${(item.serviceType || 'service').toLowerCase()}.pdf`;
                            return <Button variant="inline-link" onClick={() => { setInvoiceKey(key); setInvoiceVisible(true); }}>View PDF</Button>;
                          },
                          width: 90
                        }
                      ]}
                      variant="full-page"
                      stickyHeader={true}
                      empty={
                        <Box textAlign="center" color="inherit">
                          <Box variant="strong" textAlign="center" color="inherit">
                            No maintenance alerts
                          </Box>
                          <Box variant="p" padding={{ bottom: "s" }} color="inherit">
                            No maintenance alerts recorded for this vehicle.
                          </Box>
                        </Box>
                      }
                    />
                      </SpaceBetween>
                    </Container>
                  )
                },
                {
                  id: "dtcs",
                  // Attach a counter badge to the tab label when there
                  // are active criticals or highs so operators see the
                  // signal even when they're looking at another tab.
                  // Cloudscape's Tabs supports per-tab badges via
                  // `badge` + `badgeVariant` ("error"|"info"|etc).
                  // Using severity=critical → error (red); else if
                  // highs > 0 → warning-style amber. Counter reflects
                  // total criticals+highs, not all-severity total.
                  label: 'DTCs',
                  badge: activeDtcSeverityCounts.critical + activeDtcSeverityCounts.high > 0,
                  content: (
                    <VehicleDTCsTable vehicleId={vehicleId} />
                  )
                },
                {
                  id: "campaigns",
                  label: 'Campaigns',
                  disabled: isOEM1,
                  content: isOEM1 ? <></> : (
                    <VehicleCampaignsTable vehicleId={vehicleData?.vin || ''} />
                  )
                },
                {
                  id: "recalls",
                  label: "Recalls",
                  content: (
                    <VehicleRecallWidget vehicleId={vehicleId} make={vehicleData?.make} model={vehicleData?.model} />
                  )
                },
                {
                  id: "warranty",
                  label: "Warranty",
                  disabled: isOEM1,
                  content: isOEM1 ? <></> : (
                    <VehicleWarrantyWidget vehicleId={vehicleId} />
                  )
                },
                {
                  id: "commands",
                  label: "Remote Commands",
                  content: isOEM1
                    ? <OEM1RemoteCommandsPanel vin={vehicleData?.vin || vehicleId || ''} />
                    : <RemoteCommandsPanel vehicleId={vehicleId} />
                },
                {
                  id: "logs",
                  label: "Logs",
                  disabled: isOEM1,
                  content: isOEM1 ? <></> : (
                    <ColumnLayout columns={2}>
                      <SimLogViewer vehicleId={vehicleId} vin={vehicleData?.vin} simReachable={simReachable} simulationId={activeSimId} />
                      <FWELogViewer vin={vehicleData?.vin || vehicleId} simReachable={simReachable} agentRunning={agentRunning} />
                    </ColumnLayout>
                  )
                }
              ]}
            />

            {/* Schedule Service Modal */}
            <ScheduleServiceModal
              visible={scheduleModalVisible}
              onDismiss={() => setScheduleModalVisible(false)}
              vehicleId={vehicleId}
              vin={vehicleData?.vin}
              selectedAlerts={selectedServiceAlerts}
            />

            <DocumentViewer
              documentKey={invoiceKey}
              visible={invoiceVisible}
              onDismiss={() => setInvoiceVisible(false)}
            />

            {/* Safety Event Location Modal */}
            {selectedEvent && (
              <SafetyEventLocationModal
                visible={locationModalVisible}
                onDismiss={() => setLocationModalVisible(false)}
                eventLocation={{
                  latitude: selectedEvent.location?.latitude || 0,
                  longitude: selectedEvent.location?.longitude || 0
                }}
                eventDetails={{
                  eventType: selectedEvent.eventType,
                  severity: selectedEvent.severity,
                  vehicleId: selectedEvent.vehicleId,
                  timestamp: selectedEvent.timestamp,
                  description: selectedEvent.description
                }}
              />
            )}
      </SpaceBetween>
    </Container>
  );
};

export default VehicleDetailView;
