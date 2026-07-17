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
  Link,
} from '@cloudscape-design/components';
import iotMetricsService from '../../services/iotMetricsService';

export default function DeviceUserList() {
  const [loading, setLoading] = useState(true);
  const [users, setUsers] = useState<any[]>([]);

  const fetchUsers = async () => {
    try {
      setLoading(true);
      const result = await iotMetricsService.listUsers();
      setUsers(result.items || []);
    } catch (error) {
      console.error('Failed to fetch users:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUsers();
  }, []);

  const getStatusIndicator = (status: string) => {
    return status === 'ENABLED' 
      ? <StatusIndicator type="success">ENABLED</StatusIndicator>
      : <StatusIndicator type="stopped">DISABLED</StatusIndicator>;
  };

  const columnDefinitions = [
    {
      id: 'name',
      header: 'User Name',
      cell: (item: any) => (
        <Link href={`#/user-details/${item.uid}`}>
          {item.name}
        </Link>
      ),
      isRowHeader: true,
    },
    {
      id: 'uid',
      header: 'User ID',
      cell: (item: any) => (
        <code style={{ fontSize: '12px' }}>{item.uid}</code>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: any) => getStatusIndicator(item.status),
    },
    {
      id: 'disconnect_after_in_seconds',
      header: 'Session Timeout',
      cell: (item: any) => `${Math.floor(item.disconnect_after_in_seconds / 60)} min`,
    },
    {
      id: 'refresh_after_in_seconds',
      header: 'Refresh Interval',
      cell: (item: any) => `${Math.floor(item.refresh_after_in_seconds / 60)} min`,
    },
    {
      id: 'created_at',
      header: 'Created',
      cell: (item: any) => item.created_at ? new Date(item.created_at).toLocaleString() : '-',
    },
  ];

  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Manage user access and permissions for IoT device management and operations."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={fetchUsers}>
                Refresh
              </Button>
              <Button variant="primary" disabled>
                Add User
              </Button>
            </SpaceBetween>
          }
          counter={`(${users.length})`}
        >
          IoT Users
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={users}
        loadingText="Loading users..."
        loading={loading}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No users found</b>
            <Box variant="p" color="inherit">
              No IoT users are currently configured.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
