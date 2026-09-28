// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0
//
// RecallActionQueue: renders recall actions pending operator approval.
// Fabricated rows (hardcoded service-center name references) removed per spec
// 2026-09-02-cms-fleet-intelligence-v1 T5.3. Real rows will arrive from
// /api/v1/vfo-action-queue once the recall agent is deployed; until then the
// table correctly shows an empty queue. The DynamoDB table and main_api route
// are intentionally preserved per § D7 — an unconsumed route makes no claim.

import React from "react";
import {
  Box,
  Button,
  Container,
  Header,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  Table,
} from "@cloudscape-design/components";

type PendingAction = {
  priority: 'Critical' | 'High' | 'Medium';
  type: string;
  vehicle: string;
  recall: string;
  detail: string;
  confidence: number;
  time: string;
};

const RecallActionQueue: React.FC = () => {
  const pendingActions: PendingAction[] = [];

  return (
    <SpaceBetween size="l">
      <Container
        header={
          <Header
            variant="h2"
            counter={`(${pendingActions.length})`}
            actions={
              <SpaceBetween direction="horizontal" size="xs">
                <Button>Configure Auto-Approval</Button>
                <Button variant="primary">Approve All Critical</Button>
              </SpaceBetween>
            }
          >
            Pending Actions
          </Header>
        }
      >
        <Table
          columnDefinitions={[
            {
              id: "priority",
              header: "Priority",
              cell: (item) => (
                <StatusIndicator
                  type={
                    item.priority === "Critical"
                      ? "error"
                      : item.priority === "High"
                      ? "warning"
                      : "info"
                  }
                >
                  {item.priority}
                </StatusIndicator>
              ),
              width: 90,
            },
            {
              id: "type",
              header: "Action",
              cell: (item) => <span style={{ fontWeight: 700 }}>{item.type}</span>,
              width: 140,
            },
            { id: "vehicle",    header: "Vehicle(s)", cell: (item) => item.vehicle,  width: 130 },
            { id: "recall",     header: "Recall",     cell: (item) => item.recall !== "—" ? item.recall : "Warranty", width: 95 },
            { id: "detail",     header: "Detail",     cell: (item) => item.detail,   width: 230 },
            {
              id: "confidence",
              header: "Confidence",
              cell: (item) => (
                <ProgressBar
                  value={item.confidence}
                  additionalInfo={`${item.confidence}%`}
                  variant="key-value"
                />
              ),
              width: 110,
            },
            { id: "time",    header: "Time", cell: (item) => item.time, width: 70 },
            {
              id: "actions",
              header: "",
              cell: () => (
                <div style={{ display: "flex", gap: "4px", flexWrap: "nowrap" }}>
                  <Button variant="primary" iconName="check" />
                  <Button iconName="close" />
                </div>
              ),
              width: 85,
            },
          ]}
          items={pendingActions}
          variant="embedded"
          stickyHeader
          empty={
            <Box textAlign="center" padding="l" color="text-body-secondary">
              No pending recall actions. Recall agents are not yet deployed.
            </Box>
          }
        />
      </Container>
    </SpaceBetween>
  );
};

export default RecallActionQueue;
