// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useEffect } from 'react';
import {
  Table,
  Button,
  SpaceBetween,
  Pagination,
  TextFilter,
  StatusIndicator,
  Box,
  Link
} from '@cloudscape-design/components';
import { useNavigate } from 'react-router-dom';

interface Driver {
  driverId: string;
  name: string;
  email: string;
  licenseNumber: string;
  status: 'active' | 'inactive' | 'suspended';
  fleetId?: string;
  fleetName?: string;
  totalTrips: number;
  safetyScore: number;
  lastActive: string;
}

const DriversView: React.FC = () => {
  const navigate = useNavigate();
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [selectedItems, setSelectedItems] = useState<Driver[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [filteringText, setFilteringText] = useState('');
  const [currentPage, setCurrentPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);

  useEffect(() => {
    const mockDrivers: Driver[] = [
      {
        driverId: 'DRIVER-001',
        name: 'John Smith',
        email: 'john.smith@example.com',
        licenseNumber: 'DL123456789',
        status: 'active',
        fleetId: 'FLEET-001',
        fleetName: 'Delivery Fleet',
        totalTrips: 245,
        safetyScore: 92,
        lastActive: '2025-09-24T10:30:00Z'
      },
      {
        driverId: 'DRIVER-002',
        name: 'Sarah Johnson',
        email: 'sarah.johnson@example.com',
        licenseNumber: 'DL987654321',
        status: 'active',
        fleetId: 'FLEET-002',
        fleetName: 'Executive Fleet',
        totalTrips: 156,
        safetyScore: 98,
        lastActive: '2025-09-24T09:15:00Z'
      },
      {
        driverId: 'DRIVER-003',
        name: 'Mike Wilson',
        email: 'mike.wilson@example.com',
        licenseNumber: 'DL456789123',
        status: 'inactive',
        fleetId: 'FLEET-001',
        fleetName: 'Delivery Fleet',
        totalTrips: 89,
        safetyScore: 85,
        lastActive: '2025-09-20T16:45:00Z'
      }
    ];

    setTimeout(() => {
      setDrivers(mockDrivers);
      setIsLoading(false);
    }, 1000);
  }, []);

  const filteredDrivers = drivers.filter(driver =>
    driver.name.toLowerCase().includes(filteringText.toLowerCase()) ||
    driver.email.toLowerCase().includes(filteringText.toLowerCase()) ||
    driver.licenseNumber.toLowerCase().includes(filteringText.toLowerCase())
  );

  const paginatedDrivers = filteredDrivers.slice(
    (currentPage - 1) * pageSize,
    currentPage * pageSize
  );

  const columnDefinitions = [
    {
      id: 'name',
      header: 'Driver Name',
      cell: (item: Driver) => (
        <Link onFollow={() => navigate(`/drivers/${item.driverId}`)}>
          {item.name}
        </Link>
      ),
      sortingField: 'name'
    },
    {
      id: 'email',
      header: 'Email',
      cell: (item: Driver) => item.email,
      sortingField: 'email'
    },
    {
      id: 'licenseNumber',
      header: 'License Number',
      cell: (item: Driver) => item.licenseNumber,
      sortingField: 'licenseNumber'
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: Driver) => (
        <StatusIndicator
          type={
            item.status === 'active' ? 'success' :
            item.status === 'inactive' ? 'stopped' : 'error'
          }
        >
          {item.status.charAt(0).toUpperCase() + item.status.slice(1)}
        </StatusIndicator>
      ),
      sortingField: 'status'
    },
    {
      id: 'fleetName',
      header: 'Fleet',
      cell: (item: Driver) => item.fleetName || '-',
      sortingField: 'fleetName'
    },
    {
      id: 'totalTrips',
      header: 'Total Trips',
      cell: (item: Driver) => item.totalTrips.toLocaleString(),
      sortingField: 'totalTrips'
    },
    {
      id: 'safetyScore',
      header: 'Safety Score',
      cell: (item: Driver) => `${item.safetyScore}%`,
      sortingField: 'safetyScore'
    },
    {
      id: 'lastActive',
      header: 'Last Active',
      cell: (item: Driver) => new Date(item.lastActive).toLocaleDateString(),
      sortingField: 'lastActive'
    }
  ];

  return (
    <Table
      columnDefinitions={columnDefinitions}
      items={paginatedDrivers}
      loading={isLoading}
      selectedItems={selectedItems}
      onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
      selectionType="multi"
      ariaLabels={{
        selectionGroupLabel: "Items selection",
        allItemsSelectionLabel: ({ selectedItems }) =>
          `${selectedItems.length} ${selectedItems.length === 1 ? "item" : "items"} selected`,
        itemSelectionLabel: ({ selectedItems }, item) => {
          const isItemSelected = selectedItems.filter(i => i.driverId === item.driverId).length;
          return `${item.name} is ${isItemSelected ? "" : "not"} selected`;
        }
      }}
      filter={
        <TextFilter
          filteringText={filteringText}
          onChange={({ detail }) => setFilteringText(detail.filteringText)}
          filteringPlaceholder="Search drivers..."
          countText={`${filteredDrivers.length} ${filteredDrivers.length === 1 ? 'match' : 'matches'}`}
        />
      }
      pagination={
        <Pagination
          currentPageIndex={currentPage}
          pagesCount={Math.ceil(filteredDrivers.length / pageSize)}
          onChange={({ detail }) => setCurrentPage(detail.currentPageIndex)}
        />
      }
      empty={
        <Box textAlign="center" color="inherit">
          <b>No drivers</b>
          <Box padding={{ bottom: "s" }} variant="p" color="inherit">
            No drivers to display.
          </Box>
          <Button onClick={() => navigate('/drivers/create')}>Add Driver</Button>
        </Box>
      }
    />
  );
};

export default DriversView;
