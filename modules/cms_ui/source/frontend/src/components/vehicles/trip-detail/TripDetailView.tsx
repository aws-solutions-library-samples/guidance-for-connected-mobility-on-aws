// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from 'react';
import { useParams, useNavigate, Link } from 'react-router-dom';
import {
  Header,
  SpaceBetween,
  Container,
  ColumnLayout,
  Box,
  Badge,
  StatusIndicator,
  KeyValuePairs,
  ProgressBar,
  Table
} from '@cloudscape-design/components';
import { UserContext } from '../../commons/UserContext';
import { UI_ROUTES } from "../../../utils/constants";
import { TripMap } from './TripMap';
import { useVehicle } from '../../../contexts/VehicleContext';
import { getRuntimeConfig } from '../../../config/api';
import { authFetch } from '../../../utils/authFetch';
import { tripStatusBadge } from '../../../utils/statusRender';
import { isStatusActive } from '../../../utils/statusMatch';
import { severityLabel } from '../../../utils/severity';
import { formatEventTime, formatEventType, sortSafetyEvents, summariseSafety } from './safetyEventFormat';
import { formatEnergyUsed, TripEnergy } from './tripEnergyFormat';

interface Trip {
  tripId: string;
  vehicleId: string;
  startTime: number;
  endTime?: number;
  durationMs?: number;
  totalDistance?: number;
  averageSpeed?: number;
  maxSpeed?: number;
  currentSpeed?: number;
  currentFuelLevel?: number;
  currentEngineTemp?: number;
  driverName: string;
  driverId?: string;
  driverScore: number;
  route?: Array<{ lat: number; lng: number; timestamp?: string; speed?: number }>;
  startLocation?: { lat: number; lng: number; address?: string };
  endLocation?: { lat: number; lng: number; address?: string };
  purpose?: string;
  energy?: TripEnergy | null;
  status: 'completed' | 'in_progress' | 'cancelled' | 'ACTIVE';
}

export default function TripDetailView() {
  const { vehicleId, tripId } = useParams<{ vehicleId: string; tripId: string }>();
  const navigate = useNavigate();
  const userContext = useContext(UserContext);
  const { setVehicleVin } = useVehicle();
  
  const [tripData, setTripData] = useState<Trip | null>(null);
  const [snappedRoute, setSnappedRoute] = useState<Array<{ lat: number; lng: number }> | null>(null);
  const [safetyEvents, setSafetyEvents] = useState<any[]>([]);
  const [safetyEventsTotal, setSafetyEventsTotal] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Newest first. Derived rather than sorted in place so the map below keeps
  // receiving the array it already had.
  const safetyEventsSorted = React.useMemo(
    () => sortSafetyEvents(safetyEvents),
    [safetyEvents],
  );

  const safetySummary = React.useMemo(
    () => summariseSafety(safetyEvents),
    [safetyEvents],
  );

  useEffect(() => {
    if (vehicleId && tripId) {
      fetchTripData();
      // Fetch vehicle VIN for breadcrumb
      (async () => {
        try {
          const runtimeConfig = getRuntimeConfig();
          const resp = await authFetch(`${runtimeConfig.apiEndpoint}api/v1/vehicles/${vehicleId}`);
          if (resp.ok) {
            const data = await resp.json();
            setVehicleVin(data.vehicle?.vin || null);
          }
        } catch {}
      })();
    }
  }, [vehicleId, tripId]);

  // Snap raw GPS route to roads using Amazon Location Services route calculator.
  //
  // Auth strategy (mirrors `mapConfig._mapAuthHelper` — see
  // `../../../utils/mapConfig.ts`): prefer authenticated credentials whenever
  // an id-token is available, fall back to guest credentials otherwise. This
  // is critical: the authenticated Cognito role
  // (`cms-prod-ui-CognitoAuthenticatedRole*`) has `geo:CalculateRoute*` on
  // `route-calculator/*`, while the unauth role only has `geo:GetMap*` on the
  // specific prod HERE map. Without `logins`, `fromCognitoIdentityPool` would
  // always request guest credentials — 403-ing on CalculateRoute for
  // authenticated users AND intermittently 400-ing on the cognito-identity
  // GetCredentialsForIdentity call when it races with the parallel
  // `mapConfig` auth-preferred round trip for the same identity pool.
  //
  // See `issues/2026-07-16-route-calculator-403-and-cognito-identity-400/`.
  useEffect(() => {
    if (!tripData?.route || tripData.route.length < 2) return;
    const rc = (window as any).runtimeConfig;
    if (!rc?.locationServices?.enabled || !rc?.locationServices?.routeCalculatorName) return;
    (async () => {
      try {
        const { LocationClient, CalculateRouteCommand } = await import('@aws-sdk/client-location');
        const { fromCognitoIdentityPool } = await import('@aws-sdk/credential-providers');
        const region = rc.locationServices.region || 'us-east-1';
        const identityPoolId = rc?.awsCredentials?.identityPoolId;
        const userPoolId = rc?.awsCredentials?.userPoolId;
        if (!identityPoolId) {
          console.debug('[TripDetailView] Route snapping skipped: no identityPoolId in runtimeConfig');
          return;
        }
        const idToken =
          typeof window !== 'undefined'
            ? localStorage.getItem('idToken') || sessionStorage.getItem('idToken')
            : null;
        const logins =
          idToken && userPoolId
            ? { [`cognito-idp.${region}.amazonaws.com/${userPoolId}`]: idToken }
            : undefined;
        const client = new LocationClient({
          region,
          credentials: fromCognitoIdentityPool({
            clientConfig: { region },
            identityPoolId,
            ...(logins ? { logins } : {}),
          }),
        });
        const pts = tripData.route!;
        const start = pts[0];
        const end = pts[pts.length - 1];
        const result = await client.send(new CalculateRouteCommand({
          CalculatorName: rc.locationServices.routeCalculatorName,
          DeparturePosition: [parseFloat(start.lng as any), parseFloat(start.lat as any)],
          DestinationPosition: [parseFloat(end.lng as any), parseFloat(end.lat as any)],
          TravelMode: 'Car',
          IncludeLegGeometry: true,
        }));
        const coords = result?.Legs?.[0]?.Geometry?.LineString;
        if (coords?.length) {
          setSnappedRoute(coords.map(([lng, lat]: [number, number]) => ({ lat, lng })));
        }
      } catch (e) {
        // Fall back to raw GPS points; log at debug for observability. Common
        // legitimate causes: no id-token (guest/demo session with the unauth
        // role, which lacks `geo:CalculateRoute*` — expected 403), transient
        // network error, or expired id-token (auth token refresh in flight).
        console.debug('[TripDetailView] Route snapping failed, using raw GPS points:', e);
      }
    })();
  }, [tripData]);

  const fetchTripData = async () => {
    try {
      setLoading(true);
      
      const runtimeConfig = getRuntimeConfig();
      const apiEndpoint = runtimeConfig.apiEndpoint;
      
      // Use the full tripId as received from the URL parameter
      const decodedTripId = decodeURIComponent(tripId);
      
      // Fetch single trip data
      const response = await fetch(`${apiEndpoint}api/v1/vehicles/${vehicleId}/trips/${decodedTripId}`);
      if (!response.ok) {
        throw new Error(`Failed to fetch trip: ${response.statusText}`);
      }
      
      const data = await response.json();
      const trip = data; // API returns trip object directly, not wrapped
      
      if (!trip || !trip.tripId) {
        throw new Error('Trip not found');
      }
      
      setTripData(trip);
      
      // Extract safety events from trip response
      const events = trip.safetyEvents || [];
      setSafetyEvents(events);
      setSafetyEventsTotal(events.length);
    } catch (err) {
      console.error('Error fetching trip data:', err);
      setError(err instanceof Error ? err.message : 'Failed to fetch trip data');
    } finally {
      setLoading(false);
    }
  };

  // fetchTripSafetyEvents function removed - safety events now come from trip detail API

  if (loading) {
    return (
      <Box textAlign="center" padding="xxl">
        <StatusIndicator type="loading">Loading trip details...</StatusIndicator>
      </Box>
    );
  }

  if (error || !tripData) {
    return (
      <Box textAlign="center" padding="xxl">
        <StatusIndicator type="error">
          {error || 'Trip not found'}
        </StatusIndicator>
      </Box>
    );
  }

  return (
    <SpaceBetween size="l">
      {/* Trip Summary */}
      <Container
        header={<Header variant="h2">Trip Summary</Header>}
      >
        <ColumnLayout columns={4} variant="text-grid">
          <KeyValuePairs
            columns={1}
            items={[
              {
                label: 'Trip ID',
                value: tripData.tripId
              },
              {
                label: 'Driver',
                value: tripData.driverId ? (
                  <Link to={`/drivers/${tripData.driverId}`}>{tripData.driverName || tripData.driverId}</Link>
                ) : 'Unknown'
              },
              {
                label: 'Status',
                value: (() => {
                  const b = tripStatusBadge(tripData.status);
                  return <Badge color={b.color}>{b.label}</Badge>;
                })(),
              }
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              {
                label: 'Start Time',
                value: new Date(tripData.startTime * 1000).toLocaleString()
              },
              {
                label: 'End Time',
                value: isStatusActive(tripData.status) ? '-' : new Date(tripData.endTime * 1000).toLocaleString()
              },
              {
                label: 'Duration',
                value: tripData.durationMs ? `${Math.round(tripData.durationMs / 60000)} minutes` : '-'
              }
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              {
                label: 'Distance',
                value: `${tripData.totalDistance?.toFixed(1) || '0'} km`
              },
              {
                label: 'Average Speed',
                value: `${tripData.averageSpeed?.toFixed(1) || '0'} km/h`
              },
              {
                label: 'Max Speed',
                value: `${tripData.maxSpeed?.toFixed(1) || '0'} km/h`
              }
            ]}
          />
          <KeyValuePairs
            columns={1}
            items={[
              {
                label: 'Driver Score',
                value: <Box>
                  <ProgressBar
                    value={tripData.driverScore || 0}
                    additionalInfo={`${tripData.driverScore?.toFixed(1) || '0'}/100`}
                    description="Driver performance score"
                    variant={tripData.driverScore >= 80 ? 'success' : tripData.driverScore >= 60 ? 'warning' : 'error'}
                  />
                </Box>
              },
              {
                label: 'Safety Events',
                value: safetyEventsTotal
              },
              {
                // Energy consumed, computed backend-side from the trip's own
                // telemetry (which carries tripId and has a GSI, unlike safety
                // events). Renders "Not reported" — never 0 — when no signal
                // measured it, because on this fleet the EV signals exist but are
                // flat zeros and "0 kWh" would assert the trip used no energy.
                label: 'Energy Used',
                value: (
                  <Box>
                    {formatEnergyUsed(tripData.energy).headline}
                    {formatEnergyUsed(tripData.energy).detail && (
                      <Box variant="small" color="text-body-secondary">
                        {formatEnergyUsed(tripData.energy).detail}
                      </Box>
                    )}
                  </Box>
                )
              }
            ]}
          />
        </ColumnLayout>
      </Container>

      {/* Safety Events
        *
        * The data was already on this page — the trip-detail endpoint returns
        * `safetyEvents`, and it was being counted in the summary above and
        * plotted on the map below, but never listed. So an operator could see
        * THAT a trip had 3 safety events and where they happened, without being
        * able to see WHAT they were.
        *
        * Rendered unconditionally, unlike the map: an empty table states "we
        * looked and there were none", where an absent section is
        * indistinguishable from "this page does not show safety events".
        *
        * Severity arrives as the event catalog's reverse-ranked numeric — "3"
        * means HIGH, not "3 out of 4" — so it goes through the shared
        * `severityLabel` helper rather than being rendered raw. See
        * `src/utils/severity.ts`, which exists because rendering these two
        * conventions interchangeably already confused operators once.
        */}
      <Container
        header={
          <Header
            variant="h2"
            counter={safetyEventsTotal ? `(${safetyEventsTotal})` : undefined}
            description="Safety events recorded during this trip."
          >
            Safety Events
          </Header>
        }
      >
        <SpaceBetween size="m">
          {/* Severity breakdown. Bucketed via severityLabel, not the raw field:
            * the numeric scale is reverse-ranked, so bucketing on the raw value
            * would invert the ordering relative to the labels shown. */}
          {safetySummary.total > 0 && (
            <SpaceBetween size="xs" direction="horizontal">
              <StatusIndicator type={safetySummary.hasSevere ? 'warning' : 'success'}>
                {safetySummary.total} event{safetySummary.total === 1 ? '' : 's'}
              </StatusIndicator>
              {safetySummary.critical > 0 && (
                <Badge color="red">{safetySummary.critical} critical</Badge>
              )}
              {safetySummary.high > 0 && (
                <Badge color="red">{safetySummary.high} high</Badge>
              )}
              {safetySummary.medium > 0 && (
                <Badge color="yellow">{safetySummary.medium} medium</Badge>
              )}
              {safetySummary.low > 0 && (
                <Badge color="green">{safetySummary.low} low</Badge>
              )}
              {/* Shown rather than folded into low — an event whose severity
                * cannot be resolved still happened. */}
              {safetySummary.unknown > 0 && (
                <Badge color="grey">{safetySummary.unknown} unknown</Badge>
              )}
            </SpaceBetween>
          )}
          <Table
          variant="embedded"
          items={safetyEventsSorted}
          columnDefinitions={[
            {
              id: 'timestamp',
              header: 'Time',
              cell: (e: any) => formatEventTime(e.timestamp),
            },
            {
              id: 'eventType',
              header: 'Event',
              cell: (e: any) => formatEventType(e.eventType ?? e.event_id),
            },
            {
              id: 'severity',
              header: 'Severity',
              cell: (e: any) => {
                const label = severityLabel(e.severity);
                const color =
                  label === 'CRITICAL' || label === 'HIGH'
                    ? ('red' as const)
                    : label === 'MEDIUM'
                      ? ('yellow' as const)
                      : label === 'LOW'
                        ? ('green' as const)
                        : ('grey' as const);
                return <Badge color={color}>{label}</Badge>;
              },
            },
            {
              id: 'speed',
              header: 'Speed',
              cell: (e: any) =>
                typeof e.speed === 'number' ? `${e.speed.toFixed(1)} mph` : '—',
            },
            {
              id: 'detection',
              header: 'Detected by',
              cell: (e: any) => e.detection ?? '—',
            },
            {
              id: 'description',
              header: 'Description',
              cell: (e: any) => e.description || '—',
            },
          ]}
          empty={
            <Box textAlign="center" padding="m" color="inherit">
              <b>No safety events</b>
              <Box variant="p" color="inherit">
                No safety events were recorded during this trip.
              </Box>
            </Box>
          }
        />
        </SpaceBetween>
      </Container>

      {/* Trip Map */}
      {tripData.route && tripData.route.length > 0 && (
        <Container
          header={<Header variant="h2">Trip Route</Header>}
        >
          <TripMap
            route={snappedRoute || tripData.route}
            startLocation={tripData.route[0] && !isNaN(parseFloat(tripData.route[0].lat)) && !isNaN(parseFloat(tripData.route[0].lng)) ? { 
              lat: parseFloat(tripData.route[0].lat), 
              lng: parseFloat(tripData.route[0].lng) 
            } : undefined}
            endLocation={tripData.route[tripData.route.length - 1] && !isNaN(parseFloat(tripData.route[tripData.route.length - 1].lat)) && !isNaN(parseFloat(tripData.route[tripData.route.length - 1].lng)) ? { 
              lat: parseFloat(tripData.route[tripData.route.length - 1].lat), 
              lng: parseFloat(tripData.route[tripData.route.length - 1].lng) 
            } : undefined}
            safetyEvents={safetyEvents}
            height="400px"
            isActive={isStatusActive(tripData.status)}
          />
        </Container>
      )}
    </SpaceBetween>
  );
}
