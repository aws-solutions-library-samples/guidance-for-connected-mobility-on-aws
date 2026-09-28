// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// CostAlerts: renders cost anomaly alerts.
// Fabricated rows removed per spec 2026-09-02-cms-fleet-intelligence-v1 T5.3.
// Real alerts arrive from /api/v1/tco/alerts; the endpoint exists in main_api
// (see main_api/index.py) — this component will be wired in a follow-on once
// the route contract is stabilised.

import React from 'react';
import {
  Box,
  Container,
  Header,
  StatusIndicator,
  Table,
} from '@cloudscape-design/components';

type AlertItem = {
  type: string;
  vehicleId: string;
  message: string;
  severity: 'Critical' | 'High' | 'Medium' | 'Low';
  timestamp: string;
};

const severityTypeMap: Record<AlertItem['severity'], 'error' | 'warning' | 'info' | 'stopped'> = {
  Critical: 'error',
  High: 'warning',
  Medium: 'info',
  Low: 'stopped',
};

const CostAlerts: React.FC = () => {
  const items: AlertItem[] = [];

  return (
    <Container header={<Header variant="h2">Cost Alerts</Header>}>
      <Table
        items={items}
        columnDefinitions={[
          { id: 'type',      header: 'Alert Type', cell: (item) => item.type },
          { id: 'vehicleId', header: 'Vehicle',    cell: (item) => item.vehicleId },
          { id: 'message',   header: 'Message',    cell: (item) => item.message },
          {
            id: 'severity',
            header: 'Severity',
            cell: (item) => (
              <StatusIndicator type={severityTypeMap[item.severity]}>
                {item.severity}
              </StatusIndicator>
            ),
          },
          { id: 'timestamp', header: 'Timestamp',  cell: (item) => item.timestamp },
        ]}
        empty={
          <Box textAlign="center" padding="l" color="text-body-secondary">
            No cost alerts
          </Box>
        }
      />
    </Container>
  );
};

export default CostAlerts;
