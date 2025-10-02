// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import {
  Table,
  Box,
  SpaceBetween,
  Badge,
  Button,
  Pagination,
  TextFilter,
  Container,
  Header
} from '@cloudscape-design/components';
import { useNavigate } from 'react-router-dom';
import { getRuntimeConfig } from '../../config/api';
import { useAuth } from '../../auth/useAuth';
import { TablePreferences } from './TablePreferences';

interface Trip {
  tripId: string;
  vehicleId: string;
  driverId?: string;
  startTime: string;
  endTime: string;
  duration: number;
  distance: number;
  maxSpeed: number;
  avgSpeed: number;
  fuelConsumption: number;
  driverScore: number;
}

interface TripsTableProps {
  vehicleId?: string;
  driverId?: string;
  showVehicleColumn?: boolean;
  showDriverColumn?: boolean;
  vehicleVinMap?: Record<string, string>;
  totalTripsCount?: number;
  onTotalCountChange?: (count: number) => void;
}

export const TripsTable: React.FC<TripsTableProps> = ({
  vehicleId,
  driverId,
  showVehicleColumn = false,
  showDriverColumn = false,
  vehicleVinMap = {},
  totalTripsCount,
  onTotalCountChange
}) => {
  const navigate = useNavigate();
  const { getAuthHeaders } = useAuth();
  const [trips, setTrips] = useState<Trip[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [filterText, setFilterText] = useState('');
  const [preferences, setPreferences] = useState({
    pageSize: 10,
    visibleContent: ['vehicleId', 'driverId', 'startTime', 'duration', 'distance', 'avgSpeed', 'driverScore', 'actions']
  });

  const formatDuration = (minutes: number) => {
    const hours = Math.floor(minutes / 60);
    const mins = Math.round(minutes % 60);
    return hours > 0 ? `${hours}h ${mins}m` : `${mins}m`;
  };

  const getScoreColor = (score: number) => {
    if (score >= 90) return 'green';
    if (score >= 70) return 'blue';
    return 'red';
  };

  const fetchTrips = async (page: number = 1) => {
    try {
      setLoading(true);
      const apiEndpoint = getRuntimeConfig().apiEndpoint;
      
      let url = '';
      if (vehicleId) {
        url = `${apiEndpoint}api/v1/vehicles/${vehicleId}/trips?page=${page}&limit=${preferences.pageSize}`;
      } else if (driverId) {
        url = `${apiEndpoint}api/v1/drivers/${driverId}/trips?page=${page}&limit=${preferences.pageSize}`;
      } else {
        setTrips([]);
        setTotalItems(0);
        setTotalPages(1);
        setCurrentPage(page);
        setLoading(false);
        return;
      }

      console.log('Fetching trips from:', url);

      const response = await fetch(url, {
        headers: {
          'Content-Type': 'application/json',
          ...getAuthHeaders()
        }
      });

      if (!response.ok) {
        throw new Error(`HTTP error! status: ${response.status}`);
      }

      const data = await response.json();
      console.log('Trips API response:', data);
      
      setTrips(data.trips || data.items || []);
      
      // Log first trip to see field structure
      if ((data.trips || data.items || []).length > 0) {
        console.log('First trip data:', (data.trips || data.items || [])[0]);
      }
      
      // Use passed total count if available
      if (totalTripsCount !== undefined) {
        setTotalItems(totalTripsCount);
        setTotalPages(Math.ceil(totalTripsCount / preferences.pageSize));
        onTotalCountChange?.(totalTripsCount);
      } else {
        setTotalPages(data.pagination?.totalPages || 1);
        const total = data.pagination?.total || data.trips?.length || 0;
        setTotalItems(total);
        onTotalCountChange?.(total);
      }
      
      setCurrentPage(page);
    } catch (error) {
      console.error('Error fetching trips:', error);
      setTrips([]);
      setTotalPages(1);
      setTotalItems(0);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTrips(1);
  }, [vehicleId, driverId, preferences.pageSize]);

  const columnDefinitions = [
    ...(showVehicleColumn ? [{
      id: 'vehicleId',
      header: 'Vehicle VIN',
      cell: (item: Trip) => vehicleVinMap[item.vehicleId] || item.vehicleId,
      sortingField: 'vehicleId'
    }] : []),
    ...(showDriverColumn ? [{
      id: 'driverId',
      header: 'Driver',
      cell: (item: Trip) => {
        // driverName contains driver ID, show it as-is for now
        const driverInfo = item.driverName || item.driverId || 'N/A';
        return driverInfo;
      },
      sortingField: 'driverId'
    }] : []),
    {
      id: 'startTime',
      header: 'Start Time',
      cell: (item: Trip) => {
        const startTime = item.startTime;
        if (!startTime) return 'N/A';
        
        // Handle both milliseconds and seconds timestamps
        const timestamp = startTime > 1e12 ? startTime : startTime * 1000;
        return new Date(timestamp).toLocaleString();
      },
      sortingField: 'startTime'
    },
    {
      id: 'duration',
      header: 'Duration',
      cell: (item: Trip) => {
        const duration = item.duration || 0;
        return formatDuration(duration);
      },
      sortingField: 'duration'
    },
    {
      id: 'distance',
      header: 'Distance',
      cell: (item: Trip) => {
        const distance = item.distance || 0;
        return distance > 0 ? `${distance.toFixed(1)} mi` : 'N/A';
      },
      sortingField: 'distance'
    },
    {
      id: 'avgSpeed',
      header: 'Avg Speed',
      cell: (item: Trip) => {
        const avgSpeed = item.avgSpeed || 0;
        return avgSpeed > 0 ? `${avgSpeed.toFixed(1)} mph` : 'N/A';
      },
      sortingField: 'avgSpeed'
    },
    {
      id: 'driverScore',
      header: 'Score',
      cell: (item: Trip) => {
        const score = item.driverScore || 0;
        return (
          <Badge color={getScoreColor(score)}>
            {score.toFixed(1)}
          </Badge>
        );
      },
      sortingField: 'driverScore'
    },
    {
      id: 'actions',
      header: '',
      cell: (item: Trip) => (
        <Button
          variant="icon"
          iconName="external"
          ariaLabel="View trip details"
          onClick={() => {
            const encodedTripId = encodeURIComponent(item.tripId);
            navigate(`/vehicles/management/${item.vehicleId}/trips/${encodedTripId}`);
          }}
        />
      ),
      width: 60
    }
  ].filter(column => preferences.visibleContent.includes(column.id));

  // Filter trips based on search text
  const filteredTrips = trips.filter(trip =>
    trip.tripId.toLowerCase().includes(filterText.toLowerCase()) ||
    trip.vehicleId.toLowerCase().includes(filterText.toLowerCase()) ||
    (vehicleVinMap[trip.vehicleId] && vehicleVinMap[trip.vehicleId].toLowerCase().includes(filterText.toLowerCase()))
  );

  return (
    <Container>
      <Table
        columnDefinitions={columnDefinitions}
        items={filteredTrips}
        loading={loading}
        loadingText="Loading trips..."
        empty={
          <Box textAlign="center" color="inherit">
            <b>No trips found</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              No trips found.
            </Box>
          </Box>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            onChange={({ detail }) => setFilterText(detail.filteringText)}
            placeholder="Search trips..."
          />
        }
        pagination={
          <Pagination
            currentPageIndex={currentPage}
            pagesCount={totalPages}
            onChange={({ detail }) => fetchTrips(detail.currentPageIndex)}
          />
        }
        header={
          <Header
            variant="h2"
            counter={totalItems > 0 ? `(${((currentPage - 1) * preferences.pageSize) + 1}-${Math.min(currentPage * preferences.pageSize, totalItems)} of ${totalItems} total)` : '(0 total)'}
          >
            Trips
          </Header>
        }
        preferences={
          <TablePreferences
            preferences={preferences}
            onConfirm={(newPreferences) => {
              setPreferences(newPreferences);
              if (newPreferences.pageSize !== preferences.pageSize) {
                setCurrentPage(1);
              }
            }}
            pageSizeOptions={[
              { value: 10, label: '10 items' },
              { value: 25, label: '25 items' },
              { value: 50, label: '50 items' }
            ]}
          />
        }
        variant="full-page"
        stickyHeader
        sortingDisabled
      />
    </Container>
  );
};
