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
  Link
} from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../config/api';
import { useAuth } from '../../auth/useAuth';

interface MaintenanceAlert {
  alertId: string;
  vehicleId: string;
  timestamp: number;
  alertType: string;
  severity: string;
  message: string;
  component?: string;
  status: string;
  mileage?: number;
  location?: {
    latitude: number;
    longitude: number;
  };
}

interface MaintenanceAlertsTableProps {
  vehicleId?: string;
  fleetId?: string;
  showVehicleColumn?: boolean;
  pageSize?: number;
  height?: string;
}

export const MaintenanceAlertsTable: React.FC<MaintenanceAlertsTableProps> = ({
  vehicleId,
  fleetId,
  showVehicleColumn = false,
  pageSize = 10,
  height = 'auto'
}) => {
  const { getAuthHeaders } = useAuth();
  const [alerts, setAlerts] = useState<MaintenanceAlert[]>([]);
  const [loading, setLoading] = useState(true);
  const [currentPage, setCurrentPage] = useState(1);
  const [totalPages, setTotalPages] = useState(1);
  const [totalItems, setTotalItems] = useState(0);

  const fetchMaintenanceAlerts = async (page: number = 1) => {
    try {
      setLoading(true);
      const apiEndpoint = getRuntimeConfig().apiEndpoint;
      
      let url = '';
      if (vehicleId) {
        url = `${apiEndpoint}api/v1/vehicles/${vehicleId}/maintenance-alerts?page=${page}&limit=${pageSize}`;
      } else {
        url = `${apiEndpoint}api/v1/maintenance-alerts?page=${page}&limit=${pageSize}`;
        if (fleetId && fleetId !== 'all') url += `&fleetId=${fleetId}`;
      }

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
      setAlerts(data.alerts || []);
      setTotalPages(data.pagination?.totalPages || 1);
      setTotalItems(data.pagination?.total || data.total || 0);
      setCurrentPage(page);
    } catch (error) {
      console.error('Error fetching maintenance alerts:', error);
      setAlerts([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchMaintenanceAlerts(1);
  }, [vehicleId, fleetId]);

  const getSeverityBadge = (severity: string) => {
    const severityMap = {
      critical: { color: 'red' as const, text: 'Critical' },
      high: { color: 'red' as const, text: 'High' },
      medium: { color: 'yellow' as const, text: 'Medium' },
      low: { color: 'green' as const, text: 'Low' }
    };
    const config = severityMap[severity?.toLowerCase() as keyof typeof severityMap] || { color: 'grey' as const, text: severity };
    return <Badge color={config.color}>{config.text}</Badge>;
  };

  const getStatusBadge = (status: string) => {
    const statusMap = {
      active: { color: 'red' as const, text: 'Active' },
      resolved: { color: 'green' as const, text: 'Resolved' },
      pending: { color: 'yellow' as const, text: 'Pending' },
      acknowledged: { color: 'blue' as const, text: 'Acknowledged' }
    };
    const config = statusMap[status?.toLowerCase() as keyof typeof statusMap] || { color: 'grey' as const, text: status };
    return <Badge color={config.color}>{config.text}</Badge>;
  };

  const columnDefinitions = [
    {
      id: 'timestamp',
      header: 'Date/Time',
      cell: (item: MaintenanceAlert) => new Date(item.timestamp * 1000).toLocaleString(),
      sortingField: 'timestamp',
      width: 150
    },
    {
      id: 'alertType',
      header: 'Alert Type',
      cell: (item: MaintenanceAlert) => item.alertType || 'Unknown',
      width: 120
    },
    {
      id: 'severity',
      header: 'Severity',
      cell: (item: MaintenanceAlert) => getSeverityBadge(item.severity),
      width: 100
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: MaintenanceAlert) => getStatusBadge(item.status),
      width: 100
    },
    ...(showVehicleColumn ? [{
      id: 'vehicleId',
      header: 'Vehicle',
      cell: (item: MaintenanceAlert) => (
        <Link href={`/fleets/vehicles/${item.vehicleId}`}>
          {item.vehicleId}
        </Link>
      ),
      width: 120
    }] : []),
    {
      id: 'component',
      header: 'Component',
      cell: (item: MaintenanceAlert) => item.component || 'N/A',
      width: 120
    },
    {
      id: 'message',
      header: 'Message',
      cell: (item: MaintenanceAlert) => item.message || 'No message available',
      width: 250
    },
    {
      id: 'mileage',
      header: 'Mileage',
      cell: (item: MaintenanceAlert) => item.mileage ? `${item.mileage.toLocaleString()} mi` : 'N/A',
      width: 100
    },
    {
      id: 'location',
      header: 'Location',
      cell: (item: MaintenanceAlert) => item.location ? 
        `${item.location.latitude.toFixed(4)}, ${item.location.longitude.toFixed(4)}` : 'N/A',
      width: 120
    }
  ];

  return (
    <Table
      columnDefinitions={columnDefinitions}
      items={alerts}
      loading={loading}
      loadingText="Loading maintenance alerts..."
      empty={
        <Box textAlign="center" color="inherit">
          <b>No maintenance alerts found</b>
          <Box padding={{ bottom: 's' }} variant="p" color="inherit">
            No maintenance alerts to display.
          </Box>
        </Box>
      }
      pagination={
        <Pagination
          currentPageIndex={currentPage}
          pagesCount={totalPages}
          onChange={({ detail }) => fetchMaintenanceAlerts(detail.currentPageIndex)}
        />
      }
      header={
        <SpaceBetween direction="horizontal" size="xs">
          <Box variant="h3">Maintenance Alerts</Box>
          <Badge>{totalItems} total</Badge>
          <Button
            iconName="refresh"
            variant="icon"
            onClick={() => fetchMaintenanceAlerts(currentPage)}
          />
        </SpaceBetween>
      }
      variant="embedded"
      stickyHeader
      resizableColumns
      wrapLines
      {...(height !== 'auto' && { 
        style: { height, overflowY: 'auto' as const } 
      })}
    />
  );
};
