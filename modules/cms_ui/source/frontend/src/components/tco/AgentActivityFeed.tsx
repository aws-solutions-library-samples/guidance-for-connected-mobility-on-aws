// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  Box,
  StatusIndicator,
  SpaceBetween,
} from '@cloudscape-design/components';

const activities: { time: string; message: string; type: 'warning' | 'info' | 'error' | 'success' }[] = [
  { time: '5:14 PM', message: 'Recommend Agent: Brake replacement for VEH-1042, est. savings $2,400 → Awaiting approval', type: 'warning' },
  { time: '5:13 PM', message: 'Diagnose Agent: VEH-1042 cost spike root cause — deferred brake maintenance', type: 'info' },
  { time: '5:12 PM', message: 'Monitor Agent: Cost anomaly detected on VEH-1042 — cost/mile +58%', type: 'error' },
  { time: '4:58 PM', message: 'Learn Agent: Weekly threshold update — tightened fuel efficiency threshold by 3%', type: 'success' },
  { time: '4:45 PM', message: 'Recommend Agent: Route reassignment for VEH-0387, est. savings $340/mo → Awaiting approval', type: 'warning' },
  { time: '4:44 PM', message: 'Diagnose Agent: VEH-0387 fuel anomaly — route elevation analysis complete', type: 'info' },
  { time: '4:30 PM', message: 'Monitor Agent: Fuel cost anomaly on VEH-0387 — consumption +31%', type: 'error' },
  { time: '4:15 PM', message: 'Lifecycle Agent: VEH-0723 approaching TCO crossover in 4 months', type: 'info' },
  { time: '3:50 PM', message: 'Recommend Agent: EV charging optimization for VEH-0891 → Awaiting approval', type: 'warning' },
  { time: '3:48 PM', message: 'Auto-approved: Tire rotation for VEH-0156 (confidence 82%, under $1K threshold)', type: 'success' },
  { time: '3:30 PM', message: 'Monitor Agent: 3 new cost events processed for Region East fleet', type: 'info' },
  { time: '3:00 PM', message: 'Learn Agent: Model accuracy report — 89% prediction accuracy over last 30 days', type: 'success' },
];

const AgentActivityFeed: React.FC = () => {
  return (
    <Container header={<Header variant="h2">Agent Activity</Header>}>
      <SpaceBetween size="s">
        {activities.map((a, i) => (
          <Box key={i} display="flex">
            <Box color="text-status-inactive" fontSize="body-s" padding={{ right: 's' }}>
              {a.time}
            </Box>
            <StatusIndicator type={a.type}>{a.message}</StatusIndicator>
          </Box>
        ))}
      </SpaceBetween>
    </Container>
  );
};

export default AgentActivityFeed;
