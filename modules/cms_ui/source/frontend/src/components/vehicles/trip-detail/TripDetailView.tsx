// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect, useContext } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
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
import { getRuntimeConfig } from '../../../config/api';

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
  driverScore: number;
  route?: Array<{ lat: number; lng: number; timestamp?: string; speed?: number }>;
  startLocation?: { lat: number; lng: number; address?: string };
  endLocation?: { lat: number; lng: number; address?: string };
  purpose?: string;
  status: 'completed' | 'in_progress' | 'cancelled' | 'ACTIVE';
}

export default function TripDetailView() {
  const { vehicleId, tripId } = useParams<{ vehicleId: string; tripId: string }>();
  const navigate = useNavigate();
  const userContext = useContext(UserContext);
  
  const [tripData, setTripData] = useState<Trip | null>(null);
  const [safetyEvents, setSafetyEvents] = useState<any[]>([]);
  const [safetyEventsTotal, setSafetyEventsTotal] = useState<number>(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (vehicleId && tripId) {
      fetchTripData();
      // Remove separate safety events call - now included in trip data
    }
  }, [vehicleId, tripId]);

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
                value: tripData.driverName || 'Unknown'
              },
              {
                label: 'Status',
                value: <Badge color={tripData.status === 'completed' ? 'green' : tripData.status === 'ACTIVE' ? 'blue' : 'blue'}>
                  {tripData.status === 'ACTIVE' ? 'Active' : tripData.status}
                </Badge>
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
                value: tripData.status === 'ACTIVE' ? '-' : new Date(tripData.endTime * 1000).toLocaleString()
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
                label: 'Current Fuel Level',
                value: tripData.currentFuelLevel ? `${tripData.currentFuelLevel}%` : '-'
              },
              {
                label: 'Engine Temperature',
                value: tripData.currentEngineTemp ? `${tripData.currentEngineTemp}°F` : '-'
              }
            ]}
          />
        </ColumnLayout>
      </Container>

      {/* Trip Map */}
      {tripData.route && tripData.route.length > 0 && (
        <Container
          header={<Header variant="h2">Trip Route</Header>}
        >
          <TripMap
            route={tripData.route}
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
            isActive={tripData.status === 'ACTIVE'}
          />
        </Container>
      )}
    </SpaceBetween>
  );
}
