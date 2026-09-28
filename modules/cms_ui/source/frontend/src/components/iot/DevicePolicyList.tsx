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

export default function DevicePolicyList() {
  const [loading, setLoading] = useState(true);
  const [policies, setPolicies] = useState<any[]>([]);

  const fetchPolicies = async () => {
    try {
      setLoading(true);
      const result = await iotMetricsService.listPolicies();
      setPolicies(result.items || []);
    } catch (error) {
      console.error('Failed to fetch policies:', error);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPolicies();
  }, []);

  const columnDefinitions = [
    {
      id: 'name',
      header: 'Policy Name',
      cell: (item: any) => (
        <Link href={`#/policy-details/${item.uid}`}>
          {item.name}
        </Link>
      ),
      isRowHeader: true,
    },
    {
      id: 'description',
      header: 'Description',
      cell: (item: any) => item.description,
    },
    {
      id: 'related_user_count',
      header: 'Attached Users',
      cell: (item: any) => (
        <Badge color="blue">{item.related_user_count}</Badge>
      ),
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
          description="Manage IoT policies for device authentication, authorization, and access control."
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button iconName="refresh" onClick={fetchPolicies}>
                Refresh
              </Button>
              <Button variant="primary" disabled>
                Create Policy
              </Button>
            </SpaceBetween>
          }
          counter={`(${policies.length})`}
        >
          IoT Policies
        </Header>
      }
    >
      <Table
        columnDefinitions={columnDefinitions}
        items={policies}
        loadingText="Loading policies..."
        loading={loading}
        empty={
          <Box textAlign="center" color="inherit">
            <b>No policies found</b>
            <Box variant="p" color="inherit">
              No IoT policies are currently configured.
            </Box>
          </Box>
        }
      />
    </Container>
  );
}
