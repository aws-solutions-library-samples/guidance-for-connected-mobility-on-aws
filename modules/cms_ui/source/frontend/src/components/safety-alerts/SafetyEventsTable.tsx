// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import {
  Table,
  Box,
  SpaceBetween,
  Button,
  Header,
  Pagination,
  TextFilter,
  StatusIndicator,
  Badge,
  Link,
  Select,
  DateRangePicker,
} from '@cloudscape-design/components';
import { TablePreferences, SAFETY_ALERT_COLUMNS, DEFAULT_PAGE_SIZE_OPTIONS } from '@/components/commons/TablePreferences';

interface SafetyEvent {
  id: string;
  vehicleId: string;
  vin: string;
  actualVin?: string;
  driverId?: string;
  driverName?: string;
  eventType: 'hard_braking' | 'lane_departure' | 'rapid_acceleration' | 'speeding' | 'driver_score_decline' | 'hard_acceleration' | 'drowsiness' | 'no_seatbelt' | 'hard_cornering';
  severity: 'low' | 'medium' | 'high' | 'critical';
  timestamp: string;
  location: {
    lat: number;
    lon: number;
  };
  driverScore?: number;
  fleetName: string;
  details?: any;
  resolved?: boolean;
}

interface PaginationInfo {
  total: number;
  page: number;
  limit: number;
  totalPages: number;
  hasNextPage: boolean;
  hasPrevPage: boolean;
  returned: number;
}

interface SafetyEventsTableProps {
  safetyEvents: SafetyEvent[];
  pagination: PaginationInfo;
  loading: boolean;
  onRefresh: () => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
  onFilterChange: (filters: { fleetId?: string; eventType?: string; timeRange?: string }) => void;
}

const SafetyEventsTable: React.FC<SafetyEventsTableProps> = ({
  safetyEvents,
  pagination,
  loading,
  onRefresh,
  onPageChange,
  onPageSizeChange,
  onFilterChange,
}) => {
  const [selectedEventType, setSelectedEventType] = useState<{ label: string; value: string } | null>(null);
  const [filterText, setFilterText] = useState('');
  const [sortingColumn, setSortingColumn] = useState<any>(null);
  const [isDescending, setIsDescending] = useState(true);
  const [preferences, setPreferences] = useState({
    pageSize: pagination?.limit || 100,
    visibleContent: ['timestamp', 'vin', 'eventType', 'severity', 'location', 'fleetName', 'resolved'],
  });

  // Update preferences when pagination limit changes
  useEffect(() => {
    if (pagination.limit !== preferences.pageSize) {
      setPreferences(prev => ({ ...prev, pageSize: pagination.limit }));
    }
  }, [pagination.limit]);

  // Event type options for filtering
  const eventTypeOptions = [
    { label: 'All Events', value: '' },
    { label: 'Lane Departure', value: 'LANE_DEPARTURE' },
    { label: 'Hard Braking', value: 'HARD_BRAKING' },
    { label: 'Rapid Acceleration', value: 'RAPID_ACCELERATION' },
    { label: 'Speeding', value: 'SPEEDING' },
    { label: 'Drowsiness', value: 'DROWSINESS_DETECTED' },
    { label: 'Seatbelt Violation', value: 'SEATBELT_VIOLATION' },
    { label: 'Phone Usage', value: 'PHONE_USAGE' },
  ];

  // Handle filter changes and call API
  const handleFilterChange = () => {
    const filters: { fleetId?: string; eventType?: string; timeRange?: string } = {
      timeRange: '7d', // Default time range
    };
    
    if (selectedEventType && selectedEventType.value) {
      filters.eventType = selectedEventType.value;
    }
    
    onFilterChange(filters);
  };

  // Trigger API call when filters change (debounced)
  useEffect(() => {
    const timeoutId = setTimeout(() => {
      handleFilterChange();
    }, 300); // 300ms debounce
    
    return () => clearTimeout(timeoutId);
  }, [selectedEventType]);
  
  // Client-side filtering for display (only text search now, API handles other filters)
  const filteredEvents = safetyEvents.filter(event => {
    // Text filter only
    if (filterText) {
      const searchText = filterText.toLowerCase();
      const searchableText = [
        event.vehicleId,
        event.vin,
        formatEventType(event.eventType),
        event.severity,
        event.fleetName,
      ].join(' ').toLowerCase();
      
      if (!searchableText.includes(searchText)) {
        return false;
      }
    }
    
    return true;
  });

  // Client-side sorting
  const sortedEvents = [...filteredEvents];
  if (sortingColumn) {
    sortedEvents.sort((a, b) => {
      const aValue = a[sortingColumn.sortingField as keyof SafetyEvent];
      const bValue = b[sortingColumn.sortingField as keyof SafetyEvent];
      
      let comparison = 0;
      if (aValue < bValue) comparison = -1;
      if (aValue > bValue) comparison = 1;
      
      return isDescending ? -comparison : comparison;
    });
  }

  // Handle pagination controls
  const handlePageChange = (page: number) => {
    onPageChange(page);
  };

  const handlePageSizeChange = (pageSize: number) => {
    setPreferences(prev => ({ ...prev, pageSize }));
    onPageSizeChange(pageSize);
  };

  const handleSortingChange = (event: any) => {
    setSortingColumn(event.detail.sortingColumn);
    setIsDescending(event.detail.isDescending);
  };

  // Helper functions
  const formatEventType = (eventType: string): string => {
    const formatted: Record<string, string> = {
      LANE_DEPARTURE: 'Lane Departure',
      HARD_BRAKING: 'Hard Braking',
      RAPID_ACCELERATION: 'Rapid Acceleration',
      SPEEDING: 'Speeding',
      DROWSINESS_DETECTED: 'Drowsiness Detected',
      SEATBELT_VIOLATION: 'Seatbelt Violation',
      PHONE_USAGE: 'Phone Usage',
      // Legacy formats
      hard_braking: 'Hard Braking',
      hard_acceleration: 'Hard Acceleration',
      rapid_acceleration: 'Rapid Acceleration',
      lane_departure: 'Lane Departure',
      speeding: 'Speeding',
      drowsiness: 'Drowsiness',
      no_seatbelt: 'Seatbelt Violation',
      hard_cornering: 'Hard Cornering',
      driver_score_decline: 'Driver Score Decline',
    };
    return formatted[eventType] || eventType;
  };

  const getSeverityStatus = (severity: string): 'error' | 'warning' | 'success' | 'info' => {
    const statusMap: Record<string, 'error' | 'warning' | 'success' | 'info'> = {
      critical: 'error',    // Red
      high: 'warning',      // Yellow/Orange
      medium: 'info',       // Blue
      low: 'success',       // Green
    };
    return statusMap[severity] || 'info';
  };

  const getDurationMultiplier = (unit: string): number => {
    const multipliers: Record<string, number> = {
      minute: 60 * 1000,
      hour: 60 * 60 * 1000,
      day: 24 * 60 * 60 * 1000,
      week: 7 * 24 * 60 * 60 * 1000,
    };
    return multipliers[unit] || 60 * 1000;
  };

  const handleExportData = () => {
    // Convert to CSV
    const headers = ['Time', 'Vehicle ID', 'VIN', 'Event Type', 'Severity', 'Latitude', 'Longitude', 'Fleet', 'Driver Score', 'Status'];
    const csvData = [
      headers,
      ...sortedEvents.map(event => [
        new Date(event.timestamp).toISOString(),
        event.vehicleId,
        event.vin || '',
        formatEventType(event.eventType),
        event.severity,
        event.location.lat.toString(),
        event.location.lon.toString(),
        event.fleetName,
        event.driverScore?.toString() || '',
        event.resolved ? 'Resolved' : 'Active'
      ])
    ].map(row => row.map(field => `"${field}"`).join(',')).join('\n');

    const blob = new Blob([csvData], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `safety-events-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  };

  // Table column definitions
  const columnDefinitions = [
    {
      id: 'timestamp',
      header: 'Time',
      cell: (item: SafetyEvent) => {
        try {
          // Handle different timestamp formats
          let date;
          if (typeof item.timestamp === 'string' && item.timestamp.includes('T')) {
            // ISO string format
            date = new Date(item.timestamp);
          } else if (typeof item.timestamp === 'number') {
            // Unix timestamp (seconds)
            date = new Date(item.timestamp * 1000);
          } else if (typeof item.timestamp === 'string' && !isNaN(Number(item.timestamp))) {
            // Unix timestamp as string
            date = new Date(Number(item.timestamp) * 1000);
          } else {
            // Try parsing as is
            date = new Date(item.timestamp);
          }
          
          return isNaN(date.getTime()) ? 'Invalid Date' : date.toLocaleString();
        } catch (e) {
          return 'Invalid Date';
        }
      },
      sortingField: 'timestamp',
      isRowHeader: true,
    },
    {
      id: 'driver',
      header: 'Driver',
      cell: (item: SafetyEvent) => item.driverName || item.driverId || 'Unknown',
      sortingField: 'driverName',
    },
    {
      id: 'vin',
      header: 'VIN',
      cell: (item: SafetyEvent) => item.vin || 'N/A',
      sortingField: 'vin',
    },
    {
      id: 'eventType',
      header: 'Event Type',
      cell: (item: SafetyEvent) => (
        <span>{formatEventType(item.eventType)}</span>
      ),
      sortingField: 'eventType',
    },
    {
      id: 'severity',
      header: 'Severity',
      cell: (item: SafetyEvent) => (
        <StatusIndicator type={getSeverityStatus(item.severity)}>
          {item.severity.toUpperCase()}
        </StatusIndicator>
      ),
      sortingField: 'severity',
    },
  ];

  return (
    <SpaceBetween size="l">
      {/* Filters */}
      <SpaceBetween direction="horizontal" size="l">
        <Select
          selectedOption={selectedEventType}
          onChange={({ detail }) => setSelectedEventType(detail.selectedOption)}
          options={eventTypeOptions}
          placeholder="Filter by event type"
        />
      </SpaceBetween>

      {/* Safety Events Table */}
      <Table
        columnDefinitions={columnDefinitions}
        items={sortedEvents}
        loading={loading}
        loadingText="Loading safety events..."
        trackBy="id"
        sortingColumn={sortingColumn}
        sortingDescending={isDescending}
        onSortingChange={handleSortingChange}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No safety events</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              No safety events found. Events will appear here as they are detected by the system.
            </Box>
          </Box>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            onChange={({ detail }) => setFilterText(detail.filteringText)}
            countText={`${filteredEvents.length} ${filteredEvents.length === 1 ? 'match' : 'matches'} (${pagination.returned} on page, ${pagination.total} total)`}
            filteringPlaceholder="Search safety events..."
          />
        }
        pagination={
          <Pagination
            currentPageIndex={pagination.page}
            pagesCount={pagination.totalPages}
            ariaLabels={{
              nextPageLabel: 'Next page',
              previousPageLabel: 'Previous page',
              pageLabel: (pageNumber) => `Page ${pageNumber} of all pages`
            }}
            onChange={({ detail }) => {
              handlePageChange(detail.currentPageIndex);
            }}
            disabled={loading}
          />
        }
        preferences={
          <TablePreferences
            preferences={preferences}
            onConfirm={({ detail }) => {
              setPreferences(detail);
              if (detail.pageSize !== preferences.pageSize) {
                handlePageSizeChange(detail.pageSize);
              }
            }}
            pageSizeOptions={DEFAULT_PAGE_SIZE_OPTIONS}
            visibleContentOptions={SAFETY_ALERT_COLUMNS}
            resourceName="events"
          />
        }
        header={
          <Header
            counter={`(${(pagination.page - 1) * pagination.limit + 1}-${(pagination.page - 1) * pagination.limit + pagination.returned} of ${pagination.total} total)`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button onClick={onRefresh} loading={loading}>
                  Refresh
                </Button>
                <Button onClick={handleExportData}>
                  Export CSV
                </Button>
              </SpaceBetween>
            }
          >
            Safety Events
          </Header>
        }
      />
    </SpaceBetween>
  );
};

export default SafetyEventsTable;
