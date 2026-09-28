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

export default function DeviceSubscriptionList() {
  const [loading, setLoading] = useState(true);
  const [subscriptions, setSubscriptions] = useState<any[]>([]);

  const fetchSubscriptions = async () => {
    try {
      setLoading(true);
      const result = await iotMetricsService.listSubscriptions();
      setSubscriptions(result.items || []);
    } catch (error) {
      console.error('Failed to fetch subscriptions:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchSubscriptions();
  }, []);

  const columnDefinitions = [
    {
      id: 'client_id',
      header: 'Client ID',
      cell: (item: any) => (
        <Link href={`#/devices/connections/${item.client_id}`}>
          {item.client_id}
        </Link>
      ),
      isRowHeader: true,
    },
    {
      id: 'topic_name',
      header: 'Topic',
      cell: (item: any) => (
        <code style={{ fontSize: '12px' }}>{item.topic_name}</code>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: any) => (
        <StatusIndicator type={item.status === 'SUBSCRIBED' ? 'success' : 'stopped'}>
          {item.status}
        </StatusIndicator>
      ),
    },
    {
      id: 'subscribe_timestamp',
      header: 'Subscribed At',
      cell: (item: any) => item.subscribe_timestamp 
        ? new Date(item.subscribe_timestamp * 1000).toLocaleString() 
        : '-',
    },
    {
      id: 'session_identifier',
      header: 'Session ID',
      cell: (item: any) => (
        <code style={{ fontSize: '10px' }}>{item.session_identifier}</code>
      ),
    },
  ];

  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Manage MQTT subscriptions and monitor subscription patterns across your IoT devices."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={fetchSubscriptions}>
                Refresh
              </Button>
            </SpaceBetween>
          }
          counter={`(${subscriptions.length})`}
        >
          MQTT Subscriptions
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={subscriptions}
        loadingText="Loading subscriptions..."
        loading={loading}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No subscriptions found</b>
            <Box variant="p" color="inherit">
              No MQTT subscriptions are currently active.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
