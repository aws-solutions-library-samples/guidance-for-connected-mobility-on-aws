// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import {
  Box,
  Container,
  Header,
  SpaceBetween,
  StatusIndicator,
} from "@cloudscape-design/components";

const feed: { time: string; type: "error" | "warning" | "success" | "info"; msg: string }[] = [];

const RebalanceAgentFeed: React.FC = () => {
  return (
    <Container header={<Header variant="h2">Agent Activity</Header>}>
      <SpaceBetween size="s">
        {feed.map((item, i) => (
          <Box key={i} padding={{ vertical: "xs" }}>
            <SpaceBetween direction="horizontal" size="s">
              <Box color="text-body-secondary" fontSize="body-s" display="inline">{item.time}</Box>
              <StatusIndicator type={item.type}>{item.msg}</StatusIndicator>
            </SpaceBetween>
          </Box>
        ))}
      </SpaceBetween>
    </Container>
  );
};

export default RebalanceAgentFeed;
