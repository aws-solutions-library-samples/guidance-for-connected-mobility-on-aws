// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  Box,
  Table,
  StatusIndicator,
} from '@cloudscape-design/components';

const CostAlerts: React.FC = () => {
  return (
    <Container header={<Header variant="h2" counter="(6)">Cost Alerts</Header>}>
      <Table
        items={[
          { type: 'COST_ANOMALY', vehicleId: 'VEH-1042', message: 'Cost/mile exceeded fleet threshold (1.5x)', severity: 'Critical', timestamp: 'Mar 25, 5:12 PM' },
          { type: 'MAINTENANCE_SPIKE', vehicleId: 'VEH-1042', message: 'Maintenance spend 2.3x rolling 90-day average', severity: 'High', timestamp: 'Mar 25, 4:30 PM' },
          { type: 'FUEL_EFFICIENCY_DROP', vehicleId: 'VEH-0387', message: 'Fuel consumption +31% vs 30-day baseline', severity: 'High', timestamp: 'Mar 25, 4:30 PM' },
          { type: 'CHARGING_COST_SPIKE', vehicleId: 'VEH-0891', message: 'Charging cost $0.38/kWh vs $0.22 optimal', severity: 'Medium', timestamp: 'Mar 25, 3:45 PM' },
          { type: 'COST_ANOMALY', vehicleId: 'VEH-0156', message: 'Cost/mile trending upward — 3 consecutive weeks', severity: 'Medium', timestamp: 'Mar 25, 2:00 PM' },
          { type: 'FUEL_EFFICIENCY_DROP', vehicleId: 'VEH-0723', message: 'MPG dropped 12% — correlates with tire pressure variance', severity: 'Low', timestamp: 'Mar 24, 11:30 AM' },
        ]}
        columnDefinitions={[
          { id: 'type', header: 'Alert Type', cell: (item: any) => item.type },
          { id: 'vehicleId', header: 'Vehicle', cell: (item: any) => item.vehicleId },
          { id: 'message', header: 'Message', cell: (item: any) => item.message },
          {
            id: 'severity',
            header: 'Severity',
            cell: (item: any) => {
              const typeMap: Record<string, 'error' | 'warning' | 'info' | 'stopped'> = {
                Critical: 'error',
                High: 'warning',
                Medium: 'info',
                Low: 'stopped',
              };
              return <StatusIndicator type={typeMap[item.severity]}>{item.severity}</StatusIndicator>;
            },
          },
          { id: 'timestamp', header: 'Timestamp', cell: (item: any) => item.timestamp },
        ]}
        empty={<Box textAlign="center">No cost alerts</Box>}
      />
    </Container>
  );
};

export default CostAlerts;
