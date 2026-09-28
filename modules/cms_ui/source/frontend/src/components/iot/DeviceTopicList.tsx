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
  Link,
} from '@cloudscape-design/components';
import iotMetricsService from '../../services/iotMetricsService';

export default function DeviceTopicList() {
  const [loading, setLoading] = useState(true);
  const [topics, setTopics] = useState<any[]>([]);

  const fetchTopics = async () => {
    try {
      setLoading(true);
      const result = await iotMetricsService.listTopics();
      setTopics(result.items || []);
    } catch (error) {
      console.error('Failed to fetch topics:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTopics();
  }, []);

  const getTopicType = (topicName: string) => {
    if (topicName.includes('telemetry')) return { type: 'Telemetry', color: 'blue' };
    if (topicName.includes('commands')) return { type: 'Commands', color: 'green' };
    if (topicName.includes('alerts')) return { type: 'Alerts', color: 'red' };
    if (topicName.includes('heartbeat')) return { type: 'Heartbeat', color: 'grey' };
    return { type: 'General', color: 'blue' };
  };

  const columnDefinitions = [
    {
      id: 'name',
      header: 'Topic Name',
      cell: (item: any) => (
        <code style={{ fontSize: '12px' }}>{item.name}</code>
      ),
      isRowHeader: true,
    },
    {
      id: 'type',
      header: 'Type',
      cell: (item: any) => {
        const { type, color } = getTopicType(item.name);
        return <Badge color={color as any}>{type}</Badge>;
      },
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
          description="Monitor MQTT topics, message flow, and topic-based communication patterns across your IoT devices."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={fetchTopics}>
                Refresh
              </Button>
            </SpaceBetween>
          }
          counter={`(${topics.length})`}
        >
          MQTT Topics
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={topics}
        loadingText="Loading topics..."
        loading={loading}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No topics found</b>
            <Box variant="p" color="inherit">
              No MQTT topics are currently configured.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
