// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// ActionQueue: renders cost-agent recommendations pending operator approval.
// Fabricated rows removed per spec 2026-09-02-cms-fleet-intelligence-v1 T5.3.
// Real rows arrive from /api/v1/fleet-intelligence/actions (a Tier 2 follow-on);
// until that endpoint exists this panel correctly shows an empty queue.

import React from "react";
import {
  Box,
  Container,
  Header,
  SpaceBetween,
  Table,
  Tabs,
} from "@cloudscape-design/components";

const ActionQueue: React.FC = () => (
  <SpaceBetween size="l">
    <Tabs
      tabs={[
        {
          label: "Pending Approval (0)",
          id: "pending",
          content: (
            <Table
              selectionType="multi"
              items={[]}
              columnDefinitions={[
                { id: "priority",       header: "Priority",           cell: () => null, width: 120 },
                { id: "vehicle",        header: "Vehicle",            cell: () => null, width: 110 },
                { id: "agent",          header: "Agent",              cell: () => null, width: 140 },
                { id: "recommendation", header: "Recommendation",     cell: () => null, width: 300 },
                { id: "rootCause",      header: "Root Cause",         cell: () => null, width: 280 },
                { id: "savings",        header: "Estimated Savings",  cell: () => null, width: 130 },
                { id: "confidence",     header: "Confidence",         cell: () => null, width: 160 },
                { id: "time",           header: "Time",               cell: () => null, width:  90 },
                { id: "actions",        header: "Actions",            cell: () => null, width: 180 },
              ]}
              variant="embedded"
              empty={
                <Box textAlign="center" padding="l" color="text-body-secondary">
                  No pending recommendations. Cost-optimization agents are not yet deployed.
                </Box>
              }
            />
          ),
        },
        {
          label: "Auto-Approved (0)",
          id: "auto",
          content: (
            <Container>
              <Box padding="l" textAlign="center" color="text-body-secondary">
                No auto-approved recommendations this period.
              </Box>
            </Container>
          ),
        },
        {
          label: "History (0)",
          id: "history",
          content: (
            <Container>
              <Box padding="l" textAlign="center" color="text-body-secondary">
                No resolved recommendations.
              </Box>
            </Container>
          ),
        },
      ]}
    />
  </SpaceBetween>
);

export default ActionQueue;
