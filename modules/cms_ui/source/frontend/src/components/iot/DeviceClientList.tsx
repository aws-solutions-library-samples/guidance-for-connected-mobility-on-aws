// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Table,
  Badge,
  StatusIndicator,
  Pagination,
  TextFilter,
  CollectionPreferences,
  Link,
} from '@cloudscape-design/components';
import { useLocation } from 'react-router-dom';
import iotMetricsService, { 
  ConnectionItem, 
  FilterSpec, 
  SortSpec 
} from '../../services/iotMetricsService';

export default function DeviceClientList() {
  const location = useLocation();
  const navigationState = location.state as { activeConnection?: boolean } | null;

  const [loading, setLoading] = useState(false);
  const [connections, setConnections] = useState<ConnectionItem[]>([]);
  const [totalConnections, setTotalConnections] = useState(0);
  const [selectedItems, setSelectedItems] = useState<ConnectionItem[]>([]);
  
  const [currentPageIndex, setCurrentPageIndex] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [filteringText, setFilteringText] = useState('');
  const [sortingColumn, setSortingColumn] = useState<{ sortingField?: string; sortingDescending?: boolean }>({});

  const [preferences, setPreferences] = useState({
    pageSize: 25,
    visibleContent: ['clientId', 'status', 'connectedAt', 'lastActivity', 'protocol', 'sourceIp'],
    wrapLines: false,
    stripedRows: false,
    contentDensity: 'comfortable' as const,
  });

  const fetchConnections = async () => {
    try {
      setLoading(true);
      
      const filters: FilterSpec[] = [];
      if (filteringText) {
        filters.push({
          field: 'clientId',
          operator: 'contains',
          value: filteringText
        });
      }
      
      if (navigationState?.activeConnection) {
        filters.push({
          field: 'status',
          operator: 'equals',
          value: 'connected'
        });
      }

      const sorts: SortSpec[] = sortingColumn.sortingField ? [{
        field: sortingColumn.sortingField,
        direction: sortingColumn.sortingDescending ? 'desc' : 'asc'
      }] : [];

      const response = await iotMetricsService.listConnections(
        filters,
        sorts,
        pageSize,
        (currentPageIndex - 1) * pageSize
      );

      setConnections(response.items);
      setTotalConnections(response.totalCount);
    } catch (error) {
      console.error('Failed to fetch connections:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConnections();
  }, [currentPageIndex, pageSize, filteringText, sortingColumn]);

  const columnDefinitions = [
    {
      id: 'clientId',
      header: 'Client ID',
      cell: (item: any) => (
        <Link href={`#/devices/connections/${item.client_id}`}>
          {item.client_id}
        </Link>
      ),
      sortingField: 'client_id',
      isRowHeader: true,
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: any) => (
        <StatusIndicator type={item.status === 'CONNECTED' ? 'success' : 'error'}>
          {item.status}
        </StatusIndicator>
      ),
      sortingField: 'status',
    },
    {
      id: 'connectedAt',
      header: 'Connected At',
      cell: (item: any) => item.connect_timestamp ? new Date(item.connect_timestamp * 1000).toLocaleString() : '-',
      sortingField: 'connect_timestamp',
    },
    {
      id: 'lastActivity',
      header: 'Last Activity',
      cell: (item: any) => item.updated_at ? new Date(item.updated_at).toLocaleString() : '-',
      sortingField: 'updated_at',
    },
    {
      id: 'sourceIp',
      header: 'Source IP',
      cell: (item: any) => item.ip_address || '-',
      sortingField: 'ip_address',
    },
    {
      id: 'lastActivity',
      header: 'Last Activity',
      cell: (item: any) => item.updated_at ? new Date(item.updated_at).toLocaleString() : '-',
      sortingField: 'updated_at',
    },
    {
      id: 'protocol',
      header: 'Protocol',
      cell: (item: ConnectionItem) => <Badge color="blue">{item.protocol}</Badge>,
      sortingField: 'protocol',
    },
    {
      id: 'sourceIp',
      header: 'Source IP',
      cell: (item: ConnectionItem) => item.sourceIp || '-',
      sortingField: 'sourceIp',
    },
  ];

  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Monitor and manage IoT device connections, view connection status, and track device activity."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={fetchConnections}>
                Refresh
              </Button>
            </SpaceBetween>
          }
        >
          Client List
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={connections}
        loading={loading}
        loadingText="Loading connections..."
        selectedItems={selectedItems}
        onSelectionChange={({ detail }) => setSelectedItems(detail.selectedItems)}
        selectionType="multi"
        ariaLabels={{
          selectionGroupLabel: "Items selection",
          allItemsSelectionLabel: ({ selectedItems }) =>
            `${selectedItems.length} ${selectedItems.length === 1 ? "item" : "items"} selected`,
          itemSelectionLabel: ({ selectedItems }, item) => {
            const isItemSelected = selectedItems.filter(i => i.clientId === item.clientId).length;
            return `${item.clientId} is ${isItemSelected ? "" : "not"} selected`;
          }
        }}
        sortingColumn={sortingColumn}
        onSortingChange={({ detail }) => setSortingColumn(detail)}
        header={
          <Header
            counter={`(${totalConnections})`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button disabled={selectedItems.length === 0}>
                  Disconnect
                </Button>
                <Button disabled={selectedItems.length === 0}>
                  View Details
                </Button>
              </SpaceBetween>
            }
          >
            Device Connections
          </Header>
        }
        filter={
          <TextFilter
            filteringText={filteringText}
            onChange={({ detail }) => setFilteringText(detail.filteringText)}
            filteringPlaceholder="Find connections"
            countText={`${connections.length} ${connections.length === 1 ? 'match' : 'matches'}`}
          />
        }
        pagination={
          <Pagination
            currentPageIndex={currentPageIndex}
            onChange={({ detail }) => setCurrentPageIndex(detail.currentPageIndex)}
            pagesCount={Math.ceil(totalConnections / pageSize)}
            ariaLabels={{
              nextPageLabel: "Next page",
              previousPageLabel: "Previous page",
              pageLabel: (pageNumber) => `Page ${pageNumber} of all pages`
            }}
          />
        }
        preferences={
          <CollectionPreferences
            title="Preferences"
            confirmLabel="Confirm"
            cancelLabel="Cancel"
            preferences={preferences}
            onConfirm={({ detail }) => setPreferences(detail)}
            pageSizePreference={{
              title: "Page size",
              options: [
                { value: 10, label: "10 connections" },
                { value: 25, label: "25 connections" },
                { value: 50, label: "50 connections" }
              ]
            }}
            wrapLinesPreference={{
              label: "Wrap lines",
              description: "Check to see all the text and wrap the lines"
            }}
            stripedRowsPreference={{
              label: "Striped rows",
              description: "Check to add alternating shaded rows"
            }}
            contentDensityPreference={{
              label: "Compact mode",
              description: "Check to display content in a denser, more compact mode"
            }}
            visibleContentPreference={{
              title: "Select visible columns",
              options: [
                {
                  label: "Connection properties",
                  options: columnDefinitions.map(({ id, header }) => ({ id, label: header }))
                }
              ]
            }}
          />
        }
        empty={
          <Box textAlign="center" color="inherit">
            <b>No connections</b>
            <Box variant="p" color="inherit">
              No device connections found.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
