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
import { TablePreferences, MAINTENANCE_ALERT_COLUMNS, DEFAULT_PAGE_SIZE_OPTIONS } from '@/components/commons/TablePreferences';

interface MaintenanceEvent {
  id: string;
  vehicleId: string;
  vin: string;
  type: string;
  alertType?: string; // Add alertType field
  severity: 'low' | 'medium' | 'high' | 'critical';
  priority: 'low' | 'medium' | 'high';
  urgency: 'routine' | 'moderate' | 'urgent';
  timestamp: string;
  location: {
    lat: number;
    lng: number;
  };
  estimatedCost: number;
  category: string;
  fleetName: string;
  description?: string;
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

interface MaintenanceEventsTableProps {
  maintenanceEvents: MaintenanceEvent[];
  pagination: PaginationInfo;
  loading: boolean;
  onRefresh: () => void;
  onPageChange: (page: number) => void;
  onPageSizeChange: (pageSize: number) => void;
}

const MaintenanceEventsTable: React.FC<MaintenanceEventsTableProps> = ({
  maintenanceEvents,
  pagination,
  loading,
  onRefresh,
  onPageChange,
  onPageSizeChange,
}) => {
  const [selectedEventType, setSelectedEventType] = useState<{ label: string; value: string } | null>(null);
  const [selectedPriority, setSelectedPriority] = useState<{ label: string; value: string } | null>(null);
  const [dateRange, setDateRange] = useState<any>(null);
  const [filterText, setFilterText] = useState('');
  const [sortingColumn, setSortingColumn] = useState<any>(null);
  const [isDescending, setIsDescending] = useState(true);
  const [preferences, setPreferences] = useState({
    pageSize: pagination.limit || 20,
    visibleContent: ['timestamp', 'vehicleId', 'vin', 'type', 'priority', 'severity', 'estimatedCost', 'category', 'location', 'fleetName', 'resolved'],
  });

  // Update preferences when pagination limit changes
  useEffect(() => {
    if (pagination.limit !== preferences.pageSize) {
      setPreferences(prev => ({ ...prev, pageSize: pagination.limit }));
    }
  }, [pagination.limit]);

  // Helper function to get severity color
  const getSeverityColor = (severity: string | number): 'red' | 'blue' | 'green' | 'grey' => {
    switch (severity?.toUpperCase()) {
      case 'HIGH':
      case 'CRITICAL':
        return 'red';
      case 'MEDIUM':
      case 'MODERATE':
        return 'blue';
      case 'LOW':
        return 'green';
      default:
        return 'grey';
    }
  };

  // Debug pagination changes
  useEffect(() => {
    console.log('🔄 MaintenanceEventsTable: Pagination state changed:', pagination);
    console.log('🔄 MaintenanceEventsTable: Events count:', maintenanceEvents.length);
  }, [pagination, maintenanceEvents.length]);

  // Debug component mount
  useEffect(() => {
    console.log('🔄 MaintenanceEventsTable: Component mounted');
  }, []);

  // Event type options for filtering
  const eventTypeOptions = [
    { label: 'All Types', value: 'all' },
    { label: 'Engine Service', value: 'engine_service' },
    { label: 'Oil Change', value: 'oil_change' },
    { label: 'Brake Inspection', value: 'brake_inspection' },
    { label: 'Tire Service', value: 'tire_service' },
    { label: 'Battery Replacement', value: 'battery_replacement' },
    { label: 'Transmission Service', value: 'transmission_service' },
    { label: 'Electrical System', value: 'electrical_system' },
    { label: 'Coolant Service', value: 'coolant_service' },
    { label: 'General Maintenance', value: 'general' },
  ];

  // Priority options for filtering
  const priorityOptions = [
    { label: 'All Priorities', value: 'all' },
    { label: 'High Priority', value: 'high' },
    { label: 'Medium Priority', value: 'medium' },
    { label: 'Low Priority', value: 'low' },
  ];

  // Client-side filtering for display (search, type filters, etc.)
  const filteredEvents = maintenanceEvents.filter(event => {
    // Text filter
    if (filterText) {
      const searchText = filterText.toLowerCase();
      const searchableText = [
        event.vehicleId,
        event.vin,
        formatEventType(event.type),
        event.priority,
        event.category,
        event.description,
      ].join(' ').toLowerCase();
      
      if (!searchableText.includes(searchText)) {
        return false;
      }
    }

    // Event type filter
    if (selectedEventType && selectedEventType.value !== 'all') {
      const eventTypeKey = event.type.toLowerCase().replace(/[_\s]+/g, '_');
      if (!eventTypeKey.includes(selectedEventType.value)) {
        return false;
      }
    }

    // Priority filter
    if (selectedPriority && selectedPriority.value !== 'all' && event.priority !== selectedPriority.value) {
      return false;
    }
    
    // Date range filter
    if (dateRange) {
      const eventDate = new Date(event.timestamp);
      if (dateRange.type === 'absolute') {
        const startDate = new Date(dateRange.startDate);
        const endDate = new Date(dateRange.endDate);
        if (eventDate < startDate || eventDate > endDate) {
          return false;
        }
      } else if (dateRange.type === 'relative') {
        const now = new Date();
        const duration = dateRange.amount * getDurationMultiplier(dateRange.unit);
        const startDate = new Date(now.getTime() - duration);
        if (eventDate < startDate) {
          return false;
        }
      }
    }
    
    return true;
  });

  // Client-side sorting
  const sortedEvents = [...filteredEvents];
  if (sortingColumn) {
    sortedEvents.sort((a, b) => {
      const aValue = a[sortingColumn.sortingField as keyof MaintenanceEvent];
      const bValue = b[sortingColumn.sortingField as keyof MaintenanceEvent];
      
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
      engine_service_due: 'Engine Service Due',
      oil_change_due: 'Oil Change Due',
      brake_inspection_required: 'Brake Inspection Required',
      tire_rotation_needed: 'Tire Rotation Needed',
      tire_wear_excessive: 'Tire Wear Excessive',
      battery_replacement: 'Battery Replacement',
      transmission_service: 'Transmission Service',
      transmission_filter_replacement: 'Transmission Filter Replacement',
      electrical_system_check: 'Electrical System Check',
      coolant_service: 'Coolant Service',
      power_steering_fluid_low: 'Power Steering Fluid Low',
      windshield_washer_fluid_low: 'Windshield Washer Fluid Low',
      hydraulic_fluid_service: 'Hydraulic Fluid Service',
      blower_motor_replacement: 'Blower Motor Replacement',
      general: 'General Maintenance',
    };
    const key = eventType.toLowerCase().replace(/[_\s]+/g, '_');
    return formatted[key] || eventType.replace(/_/g, ' ').replace(/\b\w/g, l => l.toUpperCase());
  };

  const getPriorityStatus = (priority: string): 'error' | 'warning' | 'success' | 'info' => {
    const statusMap: Record<string, 'error' | 'warning' | 'success' | 'info'> = {
      high: 'error',      // Red
      medium: 'warning',  // Yellow/Orange
      low: 'success',     // Green
    };
    return statusMap[priority.toLowerCase()] || 'info';
  };

  const getSeverityStatus = (severity: string): 'error' | 'warning' | 'success' | 'info' => {
    const statusMap: Record<string, 'error' | 'warning' | 'success' | 'info'> = {
      critical: 'error',  // Red
      high: 'error',      // Red
      medium: 'warning',  // Yellow/Orange
      low: 'success',     // Green
    };
    return statusMap[severity.toLowerCase()] || 'info';
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
    const headers = ['Time', 'Vehicle ID', 'VIN', 'Maintenance Type', 'Priority', 'Severity', 'Estimated Cost', 'Category', 'Latitude', 'Longitude', 'Fleet', 'Status'];
    const csvData = [
      headers,
      ...sortedEvents.map(event => [
        new Date(event.timestamp).toISOString(),
        event.vehicleId,
        event.vin || '',
        formatEventType(event.type),
        event.priority,
        event.severity,
        event.estimatedCost?.toString() || '0',
        event.category,
        event.location.lat.toString(),
        event.location.lng.toString(),
        event.fleetName,
        event.resolved ? 'Resolved' : 'Pending'
      ])
    ].map(row => row.map(field => `"${field}"`).join(',')).join('\n');

    const blob = new Blob([csvData], { type: 'text/csv' });
    const url = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = `maintenance-events-${new Date().toISOString().split('T')[0]}.csv`;
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(url);
  };

  // Table column definitions - reordered for better UX
  const columnDefinitions = [
    {
      id: 'timestamp',
      header: 'Date/Time',
      cell: (item: MaintenanceEvent) => {
        const timestamp = parseInt(item.timestamp) * 1000; // Convert Unix timestamp to milliseconds
        return new Date(timestamp).toLocaleString();
      },
      sortingField: 'timestamp',
      isRowHeader: true,
    },
    {
      id: 'type',
      header: 'Alert Type',
      cell: (item: MaintenanceEvent) => item.alertType || item.type || 'General',
      sortingField: 'type',
    },
    {
      id: 'severity',
      header: 'Severity',
      cell: (item: MaintenanceEvent) => item.severity.toUpperCase(),
      sortingField: 'severity',
    },
    {
      id: 'vin',
      header: 'VIN',
      cell: (item: MaintenanceEvent) => item.vin || 'N/A',
      sortingField: 'vin',
    },
    {
      id: 'priority',
      header: 'Priority',
      cell: (item: MaintenanceEvent) => item.priority.toUpperCase(),
      sortingField: 'priority',
    },
    {
      id: 'estimatedCost',
      header: 'Estimated Cost',
      cell: (item: MaintenanceEvent) => `$${item.estimatedCost?.toFixed(2) || '0.00'}`,
      sortingField: 'estimatedCost',
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
          placeholder="Filter by maintenance type"
        />
        <Select
          selectedOption={selectedPriority}
          onChange={({ detail }) => setSelectedPriority(detail.selectedOption)}
          options={priorityOptions}
          placeholder="Filter by priority"
        />
        <DateRangePicker
          onChange={({ detail }) => setDateRange(detail.value)}
          value={dateRange}
          relativeOptions={[
            { key: 'previous-5-minutes', amount: 5, unit: 'minute', type: 'relative' },
            { key: 'previous-30-minutes', amount: 30, unit: 'minute', type: 'relative' },
            { key: 'previous-1-hour', amount: 1, unit: 'hour', type: 'relative' },
            { key: 'previous-6-hours', amount: 6, unit: 'hour', type: 'relative' },
            { key: 'previous-1-day', amount: 1, unit: 'day', type: 'relative' },
            { key: 'previous-3-days', amount: 3, unit: 'day', type: 'relative' },
            { key: 'previous-1-week', amount: 1, unit: 'week', type: 'relative' },
          ]}
          isValidRange={(range) => {
            if (range?.type === 'absolute') {
              const [startDateWithoutTime] = range.startDate.split('T');
              const [endDateWithoutTime] = range.endDate.split('T');
              if (!startDateWithoutTime || !endDateWithoutTime) {
                return { valid: false, errorMessage: 'The selected date range is incomplete' };
              }
              if (new Date(range.startDate) - new Date(range.endDate) > 0) {
                return { valid: false, errorMessage: 'The selected date range is invalid' };
              }
            }
            return { valid: true };
          }}
          placeholder="Filter by date range"
        />
      </SpaceBetween>

      {/* Maintenance Events Table */}
      <Table
        columnDefinitions={columnDefinitions}
        items={sortedEvents}
        loading={loading}
        loadingText="Loading maintenance events..."
        trackBy="id"
        sortingColumn={sortingColumn}
        sortingDescending={isDescending}
        onSortingChange={handleSortingChange}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No maintenance events</b>
            <Box padding={{ bottom: 's' }} variant="p" color="inherit">
              No maintenance events found. Events will appear here when maintenance is required.
            </Box>
          </Box>
        }
        filter={
          <TextFilter
            filteringText={filterText}
            onChange={({ detail }) => setFilterText(detail.filteringText)}
            countText={`${filteredEvents.length} ${filteredEvents.length === 1 ? 'match' : 'matches'} (${pagination.returned} on page, ${pagination.total} total)`}
            filteringPlaceholder="Search maintenance events..."
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
            visibleContentOptions={MAINTENANCE_ALERT_COLUMNS}
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
            Maintenance Events
          </Header>
        }
      />
    </SpaceBetween>
  );
};

export default MaintenanceEventsTable;
