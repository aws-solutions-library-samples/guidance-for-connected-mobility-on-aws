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
} from '@cloudscape-design/components';

export default function DeviceLogTrace() {
  const [loading, setLoading] = useState(false);

  // Mock CloudWatch log events
  const logEvents = [
    {
      timestamp: Date.now() - 300000,
      level: 'INFO',
      message: 'Device vehicle-001 connected successfully',
      source: 'IoT Core',
      client_id: 'vehicle-001',
      event_type: 'CONNECTION',
    },
    {
      timestamp: Date.now() - 600000,
      level: 'ERROR',
      message: 'Failed to process message from topic fleet/vehicle/002/telemetry: Invalid JSON format',
      source: 'Rules Engine',
      client_id: 'vehicle-002',
      event_type: 'MESSAGE_PROCESSING',
    },
    {
      timestamp: Date.now() - 900000,
      level: 'WARN',
      message: 'Device vehicle-002 disconnected unexpectedly',
      source: 'IoT Core',
      client_id: 'vehicle-002',
      event_type: 'DISCONNECTION',
    },
    {
      timestamp: Date.now() - 1200000,
      level: 'INFO',
      message: 'Rule VehicleTelemetryProcessor executed successfully for 15 messages',
      source: 'Rules Engine',
      client_id: null,
      event_type: 'RULE_EXECUTION',
    },
  ];

  const handleRefresh = () => {
    setLoading(true);
    setTimeout(() => setLoading(false), 1000);
  };

  const getLevelBadge = (level: string) => {
    const colors = { ERROR: 'red', WARN: 'orange', INFO: 'blue', DEBUG: 'grey' };
    return <Badge color={colors[level as keyof typeof colors] as any}>{level}</Badge>;
  };

  const columnDefinitions = [
    {
      id: 'timestamp',
      header: 'Timestamp',
      cell: (item: any) => new Date(item.timestamp).toLocaleString(),
      width: 180,
    },
    {
      id: 'level',
      header: 'Level',
      cell: (item: any) => getLevelBadge(item.level),
      width: 80,
    },
    {
      id: 'source',
      header: 'Source',
      cell: (item: any) => item.source,
      width: 120,
    },
    {
      id: 'client_id',
      header: 'Client ID',
      cell: (item: any) => item.client_id || '-',
      width: 120,
    },
    {
      id: 'message',
      header: 'Message',
      cell: (item: any) => <Box fontSize="body-s">{item.message}</Box>,
    },
  ];

  return (
    <Container
      header={
        <Header
          variant="h1"
          description="View CloudWatch logs and trace IoT device events, connections, and message processing."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={handleRefresh} loading={loading}>
                Refresh
              </Button>
              <Button iconName="external" disabled>
                View in CloudWatch
              </Button>
            </SpaceBetween>
          }
          counter={`(${logEvents.length})`}
        >
          Device Logs & Traces
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={logEvents}
        loadingText="Loading log events..."
        loading={loading}
        variant="borderless"
        empty={
          <Box textAlign="center" color="inherit">
            <b>No log events found</b>
            <Box variant="p" color="inherit">
              No CloudWatch log events are available.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
