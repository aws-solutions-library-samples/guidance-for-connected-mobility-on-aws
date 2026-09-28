// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// RecallAgentFeed: placeholder — the recall/warranty agent is designed but not yet
// deployed. Fabricated agent messages removed per spec 2026-09-02-cms-fleet-intelligence-v1
// T5.3 / § D7. A Tier 2 spec will build the agent and surface its activity here.

import React from "react";
import {
  Box,
  Container,
  Header,
} from "@cloudscape-design/components";

const RecallAgentFeed: React.FC = () => {
  return (
    <Container header={<Header variant="h2">Agent Activity</Header>}>
      <Box
        textAlign="center"
        padding="l"
        color="text-body-secondary"
      >
        Recall and warranty agents are not yet deployed. A Tier 2 spec will build
        agents for recall ingestion, warranty claim drafting, and service scheduling,
        and will surface their activity here.
      </Box>
    </Container>
  );
};

export default RecallAgentFeed;
