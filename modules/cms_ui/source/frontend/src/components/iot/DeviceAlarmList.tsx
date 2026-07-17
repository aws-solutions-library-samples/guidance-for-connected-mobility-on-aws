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
} from '@cloudscape-design/components';
import iotMetricsService from '../../services/iotMetricsService';

export default function DeviceAlarmList() {
  const [loading, setLoading] = useState(true);
  const [alarms, setAlarms] = useState<any[]>([]);

  const fetchAlarms = async () => {
    try {
      setLoading(true);
      const result = await iotMetricsService.listAlarms();
      setAlarms(result.items || []);
    } catch (error) {
      console.error('Failed to fetch alarms:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchAlarms();
  }, []);

  const getStatusIndicator = (state: string) => {
    switch (state) {
      case 'ALARM':
        return <StatusIndicator type="error">ALARM</StatusIndicator>;
      case 'OK':
        return <StatusIndicator type="success">OK</StatusIndicator>;
      case 'INSUFFICIENT_DATA':
        return <StatusIndicator type="warning">INSUFFICIENT_DATA</StatusIndicator>;
      default:
        return <StatusIndicator type="info">{state}</StatusIndicator>;
    }
  };

  const columnDefinitions = [
    {
      id: 'alarm_name',
      header: 'Alarm Name',
      cell: (item: any) => item.alarm_name,
      isRowHeader: true,
    },
    {
      id: 'alarm_description',
      header: 'Description',
      cell: (item: any) => item.alarm_description,
    },
    {
      id: 'old_state_value',
      header: 'Previous State',
      cell: (item: any) => getStatusIndicator(item.old_state_value),
    },
    {
      id: 'new_state_value',
      header: 'Current State',
      cell: (item: any) => getStatusIndicator(item.new_state_value),
    },
    {
      id: 'state_change_time',
      header: 'State Changed',
      cell: (item: any) => item.state_change_time 
        ? new Date(item.state_change_time).toLocaleString() 
        : '-',
    },
  ];

  return (
    <Container
      header={
        <Header
          variant="h1"
          description="Monitor CloudWatch alarms and IoT device alerts across your infrastructure."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={fetchAlarms}>
                Refresh
              </Button>
            </SpaceBetween>
          }
          counter={`(${alarms.length})`}
        >
          Device Alarms
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={alarms}
        loadingText="Loading alarms..."
        loading={loading}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No alarms found</b>
            <Box variant="p" color="inherit">
              No CloudWatch alarms are currently configured.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
