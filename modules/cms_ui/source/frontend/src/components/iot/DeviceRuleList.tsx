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
  Link,
} from '@cloudscape-design/components';

export default function DeviceRuleList() {
  const [loading, setLoading] = useState(false);

  // Mock IoT rules data based on AIOT reference
  const rules = [
    {
      rule_name: 'VehicleTelemetryProcessor',
      topic_pattern: 'fleet/vehicle/+/telemetry',
      status: 'ENABLED',
      created_at: Date.now() - 86400000, // 1 day ago
      description: 'Process vehicle telemetry data and store in DynamoDB',
    },
    {
      rule_name: 'EmergencyAlertHandler',
      topic_pattern: 'fleet/alerts/emergency',
      status: 'ENABLED',
      created_at: Date.now() - 172800000, // 2 days ago
      description: 'Handle emergency alerts and trigger notifications',
    },
    {
      rule_name: 'DeviceHeartbeatMonitor',
      topic_pattern: 'fleet/+/heartbeat',
      status: 'DISABLED',
      created_at: Date.now() - 259200000, // 3 days ago
      description: 'Monitor device heartbeat and update connection status',
    },
    {
      rule_name: 'CommandResponseRouter',
      topic_pattern: 'fleet/vehicle/+/commands/response',
      status: 'ENABLED',
      created_at: Date.now() - 345600000, // 4 days ago
      description: 'Route command responses to appropriate handlers',
    },
  ];

  const handleRefresh = () => {
    setLoading(true);
    setTimeout(() => setLoading(false), 1000);
  };

  const getStatusIndicator = (status: string) => {
    return status === 'ENABLED' 
      ? <StatusIndicator type="success">ENABLED</StatusIndicator>
      : <StatusIndicator type="stopped">DISABLED</StatusIndicator>;
  };

  const columnDefinitions = [
    {
      id: 'rule_name',
      header: 'Rule Name',
      cell: (item: any) => (
        <Link href={`#/devices/rules/${item.rule_name}`}>
          {item.rule_name}
        </Link>
      ),
      isRowHeader: true,
    },
    {
      id: 'topic_pattern',
      header: 'Topic Pattern',
      cell: (item: any) => (
        <code style={{ fontSize: '12px' }}>{item.topic_pattern}</code>
      ),
    },
    {
      id: 'status',
      header: 'Status',
      cell: (item: any) => getStatusIndicator(item.status),
    },
    {
      id: 'description',
      header: 'Description',
      cell: (item: any) => item.description,
    },
    {
      id: 'created_at',
      header: 'Created',
      cell: (item: any) => new Date(item.created_at).toLocaleString(),
    },
  ];

  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Manage IoT rules for data processing, routing, and automated actions."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={handleRefresh} loading={loading}>
                Refresh
              </Button>
              <Button variant="primary" disabled>
                Create Rule
              </Button>
            </SpaceBetween>
          }
          counter={`(${rules.length})`}
        >
          IoT Rules
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={rules}
        loadingText="Loading rules..."
        loading={loading}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No rules found</b>
            <Box variant="p" color="inherit">
              No IoT rules are currently configured.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
