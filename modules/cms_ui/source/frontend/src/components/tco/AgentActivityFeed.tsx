// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// AgentActivityFeed: placeholder — the cost-optimization agent (Monitor / Diagnose /
// Recommend / Learn / Lifecycle) is designed but not yet deployed. A Tier 2 spec will
// build the agents and the artifact contract; until then this panel renders an
// explicit "not yet available" state rather than fabricated agent messages.
// See spec 2026-09-02-cms-fleet-intelligence-v1 § D2 / § D7 and decisions.md.

import React from 'react';
import {
  Box,
  Container,
  Header,
} from '@cloudscape-design/components';

const AgentActivityFeed: React.FC = () => {
  return (
    <Container header={<Header variant="h2">Agent Activity</Header>}>
      <Box
        textAlign="center"
        padding="l"
        color="text-body-secondary"
      >
        Cost optimization agents are not yet deployed. A Tier 2 spec will build
        the Monitor, Diagnose, Recommend, Learn, and Lifecycle agents and surface
        their activity here.
      </Box>
    </Container>
  );
};

export default AgentActivityFeed;
