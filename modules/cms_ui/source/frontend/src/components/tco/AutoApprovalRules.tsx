// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  Box,
  Table,
  Button,
  StatusIndicator,
} from '@cloudscape-design/components';

const AutoApprovalRules: React.FC = () => {
  return (
    <Container
      header={
        <Header variant="h2" actions={<Button variant="primary">Create Rule</Button>}>
          Auto-Approval Rules
        </Header>
      }
    >
      <Table
        items={[
          { rule: 'Maintenance scheduling', condition: 'Confidence > 90% AND cost < $500', action: 'Auto-approve', status: 'Active', statusType: 'success' as const },
          { rule: 'EV charge schedule optimization', condition: 'Any confidence', action: 'Auto-approve', status: 'Active', statusType: 'success' as const },
          { rule: 'Route reassignment', condition: 'Confidence > 85% AND savings > $200/mo', action: 'Auto-approve', status: 'Paused', statusType: 'warning' as const },
          { rule: 'Vehicle retirement', condition: 'Any', action: 'Always require approval', status: 'Active', statusType: 'info' as const },
        ]}
        columnDefinitions={[
          { id: 'rule', header: 'Rule', cell: (item: any) => item.rule },
          { id: 'condition', header: 'Condition', cell: (item: any) => item.condition },
          { id: 'action', header: 'Action', cell: (item: any) => item.action },
          {
            id: 'status',
            header: 'Status',
            cell: (item: any) => <StatusIndicator type={item.statusType}>{item.status}</StatusIndicator>,
          },
        ]}
        empty={<Box textAlign="center">No auto-approval rules configured</Box>}
      />
    </Container>
  );
};

export default AutoApprovalRules;
