// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  Box,
  Button,
  Table,
  Badge,
  StatusIndicator,
} from '@cloudscape-design/components';

export default function DeviceRetainMessageList() {
  const [loading, setLoading] = useState(false);

  // Mock retained messages data
  const retainedMessages = [
    {
      topic: 'fleet/vehicle/001/status',
      payload: '{"status":"online","battery":85,"location":{"lat":37.7749,"lng":-122.4194}}',
      timestamp: Date.now() - 300000, // 5 minutes ago
      size: 78,
      qos: 1,
    },
    {
      topic: 'fleet/gateway/001/config',
      payload: '{"version":"1.2.3","heartbeat_interval":30,"max_connections":100}',
      timestamp: Date.now() - 1800000, // 30 minutes ago
      size: 65,
      qos: 0,
    },
    {
      topic: 'fleet/alerts/maintenance',
      payload: '{"alert_type":"scheduled_maintenance","vehicle_id":"vehicle-001","due_date":"2025-09-01"}',
      timestamp: Date.now() - 3600000, // 1 hour ago
      size: 89,
      qos: 2,
    },
  ];

  const handleRefresh = () => {
    setLoading(true);
    setTimeout(() => setLoading(false), 1000);
  };

  const getQoSBadge = (qos: number) => {
    const colors = { 0: 'grey', 1: 'blue', 2: 'green' };
    return <Badge color={colors[qos as keyof typeof colors] as any}>QoS {qos}</Badge>;
  };

  const columnDefinitions = [
    {
      id: 'topic',
      header: 'Topic',
      cell: (item: any) => (
        <code style={{ fontSize: '12px' }}>{item.topic}</code>
      ),
      isRowHeader: true,
    },
    {
      id: 'payload',
      header: 'Payload Preview',
      cell: (item: any) => (
        <Box>
          <code style={{ fontSize: '10px', color: '#666' }}>
            {item.payload.length > 50 ? `${item.payload.substring(0, 50)}...` : item.payload}
          </code>
        </Box>
      ),
    },
    {
      id: 'size',
      header: 'Size',
      cell: (item: any) => `${item.size} bytes`,
    },
    {
      id: 'qos',
      header: 'QoS',
      cell: (item: any) => getQoSBadge(item.qos),
    },
    {
      id: 'timestamp',
      header: 'Last Updated',
      cell: (item: any) => new Date(item.timestamp).toLocaleString(),
    },
  ];

  return (
    <Container
      header={
        <Header
          variant="h1"
          description="View and manage retained MQTT messages across your IoT infrastructure."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={handleRefresh} loading={loading}>
                Refresh
              </Button>
            </SpaceBetween>
          }
          counter={`(${retainedMessages.length})`}
        >
          Retained Messages
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={retainedMessages}
        loadingText="Loading retained messages..."
        loading={loading}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No retained messages found</b>
            <Box variant="p" color="inherit">
              No MQTT retained messages are currently stored.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
