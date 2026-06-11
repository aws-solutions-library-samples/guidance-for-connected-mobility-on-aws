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
  StatusIndicator,
  Link,
  Container,
  Header,
  TextFilter
} from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../config/api';
import { useAuth } from '../../auth/useAuth';
import { TablePreferences } from './TablePreferences';

interface SafetyEvent {
  eventId: string;
  tripId?: string;
  vehicleId: string;
  driverId?: string;
  eventType: string;
  severity: string;
  timestamp: number;
  detection?: string;
  campaignSyncId?: string;
  location?: {
    latitude: number;
    longitude: number;
  };
  description: string;
  speed?: number;
  gForce?: number;
}

interface SafetyEventsTableProps {
  vehicleId?: string;
  driverId?: string;
  tripId?: string;
  showVehicleColumn?: boolean;
  showDriverColumn?: boolean;
  showTripColumn?: boolean;
  pageSize?: number;
  height?: string;
  onLocationClick?: (location: { latitude: number; longitude: number }, eventDetails?: SafetyEvent) => void;
  vehicleVinMap?: Record<string, string>;
  totalEventsCount?: number; // Pass total from parent
}

export const SafetyEventsTable: React.FC<SafetyEventsTableProps> = ({
  vehicleId,
  driverId,
  tripId,
  showVehicleColumn = false,
  showDriverColumn = false,
  showTripColumn = true,
  pageSize = 10,
  height = 'auto',
  onLocationClick,
  vehicleVinMap = {},
  totalEventsCount
}) => {
  const { getAuthHeaders } = useAuth();
  const [events, setEvents] = useState<SafetyEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);
  const [filterText, setFilterText] = useState('');
  const [preferences, setPreferences] = useState({
    pageSize: pageSize,
    visibleContent: ['timestamp', 'eventType', 'severity', 'detection', 'description', 'location']
  });

  const fetchSafetyEvents = async (page: number = 1) => {
    try {
      setLoading(true);
      const apiEndpoint = getRuntimeConfig().apiEndpoint;
      
      let url = '';
      if (vehicleId) {
        url = `${apiEndpoint}api/v1/vehicles/${vehicleId}/safety-events?page=${page}&limit=${preferences.pageSize}`;
        if (tripId) url += `&tripId=${tripId}`;
      } else if (driverId) {
        url = `${apiEndpoint}api/v1/safety-events?driverId=${driverId}&page=${page}&limit=${preferences.pageSize}`;
      } else {
        url = `${apiEndpoint}api/v1/safety-events?page=${page}&limit=${preferences.pageSize}`;
      }

      console.log('Fetching safety events from:', url);

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
      console.log('Safety events API response:', data);
      
      setEvents(data.events || []);
      
      // Use passed total count if available, otherwise fetch total
      if (totalEventsCount !== undefined) {
        setTotalItems(totalEventsCount);
        setTotalPages(Math.ceil(totalEventsCount / preferences.pageSize));
      } else {
        // Since the API doesn't provide proper pagination, let's fetch all events to get the real total
        if (page === 1) {
          // Fetch all events to get total count, keeping the same filters
          let allEventsUrl = '';
          if (vehicleId) {
            allEventsUrl = `${apiEndpoint}api/v1/vehicles/${vehicleId}/safety-events?limit=1000`;
            if (tripId) allEventsUrl += `&tripId=${tripId}`;
          } else if (driverId) {
            allEventsUrl = `${apiEndpoint}api/v1/safety-events?driverId=${driverId}&limit=1000`;
          } else {
            allEventsUrl = `${apiEndpoint}api/v1/safety-events?limit=1000`;
          }
          
          try {
            const allResponse = await fetch(allEventsUrl, {
              headers: {
                'Content-Type': 'application/json',
                ...getAuthHeaders()
              }
            });
            
            if (allResponse.ok) {
              const allData = await allResponse.json();
              const totalEvents = allData.events?.length || 0;
              console.log('Total events for this driver:', totalEvents);
              setTotalItems(totalEvents);
              setTotalPages(Math.ceil(totalEvents / preferences.pageSize));
            }
          } catch (error) {
            console.warn('Failed to fetch total count:', error);
            // Fallback to current page data
            setTotalItems(data.events?.length || 0);
            setTotalPages(1);
          }
        }
      }
      
      setCurrentPage(page);
    } catch (error) {
      console.error('Error fetching safety events:', error);
      setEvents([]);
      setTotalPages(1);
      setTotalItems(0);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSafetyEvents(1);
  }, [vehicleId, driverId, tripId, preferences.pageSize]);

  const getSeverityColor = (severity: string | number) => {
    switch (String(severity ?? "").toLowerCase()) {
      case 'high': return 'red' as const;
      case 'medium': return 'blue' as const;
      case 'low': return 'green' as const;
      default: return 'grey' as const;
    }
  };

  const getEventTypeBadge = (eventType: string) => {
    const typeMap = {
      'hard_braking': 'Hard Braking',
      'rapid_acceleration': 'Rapid Acceleration',
      'harsh_cornering': 'Harsh Cornering',
      'speeding': 'Speeding',
      'collision': 'Collision',
      'near_miss': 'Near Miss'
    };
    return typeMap[eventType as keyof typeof typeMap] || eventType;
  };

  const columnDefinitions = [
    {
      id: 'timestamp',
      header: 'Event Time',
      cell: (item: SafetyEvent) => {
        const timestamp = item.timestamp > 1e12 ? item.timestamp : item.timestamp * 1000;
        return new Date(timestamp).toLocaleString();
      },
      sortingField: 'timestamp',
      width: 150
    },
    ...(showVehicleColumn ? [{
      id: 'vehicleId',
      header: 'Vehicle VIN',
      cell: (item: SafetyEvent) => vehicleVinMap[item.vehicleId] || item.vehicleId,
      sortingField: 'vehicleId',
      width: 120
    }] : []),
    {
      id: 'eventType',
      header: 'Event Type',
      cell: (item: SafetyEvent) => item.eventType.replace(/_/g, ' '),
      sortingField: 'eventType',
      width: 140
    },
    {
      id: 'severity',
      header: 'Severity',
      cell: (item: SafetyEvent) => (
        <Badge color={getSeverityColor(item.severity)}>
          {item.severity}
        </Badge>
      ),
      sortingField: 'severity',
      width: 100
    },
    {
      id: 'detection',
      header: 'Detection',
      cell: (item: SafetyEvent) => item.detection === 'edge' ? 'Edge' : 'Cloud',
      sortingField: 'detection',
      width: 110
    },
    ...(showDriverColumn ? [{
      id: 'driverId',
      header: 'Driver',
      cell: (item: SafetyEvent) => item.driverId || 'N/A',
      width: 120
    }] : []),
    {
      id: 'description',
      header: 'Description',
      cell: (item: SafetyEvent) => item.description || 'N/A'
    },
    {
      id: 'location',
      header: 'Location',
      cell: (item: SafetyEvent) => item.location && onLocationClick ? (
        <Button
          variant="icon"
          iconName="external"
          ariaLabel="View location"
          onClick={() => onLocationClick(item.location!, item)}
        />
      ) : 'N/A',
      width: 80
    }
  ].filter(column => preferences.visibleContent.includes(column.id));

  // Filter events based on search text
  const filteredEvents = events.filter(event =>
    event.eventType.toLowerCase().includes(filterText.toLowerCase()) ||
    event.severity.toLowerCase().includes(filterText.toLowerCase()) ||
    (event.description && event.description.toLowerCase().includes(filterText.toLowerCase())) ||
    (vehicleVinMap[event.vehicleId] && vehicleVinMap[event.vehicleId].toLowerCase().includes(filterText.toLowerCase()))
  );

  return (
    <Container>
      <Table
        columnDefinitions={columnDefinitions}
        items={filteredEvents}
        loading={loading}
        sortingColumn={{ sortingField: 'timestamp' }}
        sortingDescending={true}
        loadingText="Loading safety events..."
        empty={
          <Box textAlign="center" color="inherit">
            <b>No safety events</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              No safety events found for this driver.
            </Box>
          </Box>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            onChange={({ detail }) => setFilterText(detail.filteringText)}
            placeholder="Search safety events..."
          />
        }
        pagination={
          <Pagination
            currentPageIndex={currentPage}
            pagesCount={totalPages}
            onChange={({ detail }) => fetchSafetyEvents(detail.currentPageIndex)}
          />
        }
        header={
          <Header
            variant="h2"
            counter={totalItems > 0 ? `(${((currentPage - 1) * preferences.pageSize) + 1}-${Math.min(currentPage * preferences.pageSize, totalItems)} of ${totalItems} total)` : '(0 total)'}
          >
            Safety Events
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
