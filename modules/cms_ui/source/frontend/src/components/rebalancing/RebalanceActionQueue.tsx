// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

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

const pendingMoves: { priority: "Critical" | "High" | "Medium" | "Low"; from: string; to: string; count: number; type: string; cost: string; revenue: string; confidence: number; time: string }[] = [];

const executedMoves: { from: string; to: string; count: number; date: string; impact: string; status: "success" | "warning" | "error" }[] = [];

const RebalanceActionQueue: React.FC = () => {
  return (
    <SpaceBetween size="l">
      <Container header={
        <Header variant="h2" counter={`(${pendingMoves.length})`}
          actions={<Button variant="primary">Approve All High+</Button>}>
          Pending Recommendations
        </Header>
      }>
        <Table
          columnDefinitions={[
            { id: "priority", header: "Priority", cell: (item) => (
              <StatusIndicator type={
                item.priority === "Critical" ? "error" :
                item.priority === "High" ? "warning" :
                item.priority === "Medium" ? "info" : "stopped"
              }>{item.priority}</StatusIndicator>
            ), width: 110 },
            { id: "move", header: "Move", cell: (item) => (
              <span style={{ fontWeight: 700 }}>{item.from} → {item.to}</span>
            ), width: 160 },
            { id: "count", header: "Vehicles", cell: (item) => `${item.count}× ${item.type}`, width: 140 },
            { id: "cost", header: "Cost", cell: (item) => item.cost, width: 80 },
            { id: "revenue", header: "Recovery", cell: (item) => (
              <span style={{ color: '#037f0c' }}>{item.revenue}</span>
            ), width: 100 },
            { id: "confidence", header: "Confidence", cell: (item) => (
              <ProgressBar value={item.confidence} additionalInfo={`${item.confidence}%`} variant="key-value" />
            ), width: 130 },
            { id: "time", header: "Time", cell: (item) => item.time, width: 80 },
            { id: "actions", header: "", cell: () => (
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="primary" iconName="check" />
                <Button iconName="close" />
              </SpaceBetween>
            ), width: 100 },
          ]}
          items={pendingMoves}
          variant="embedded"
          stickyHeader
        />
      </Container>

      <Container header={<Header variant="h2">Recently Executed</Header>}>
        <Table
          columnDefinitions={[
            { id: "move", header: "Move", cell: (item) => <span style={{ fontWeight: 700 }}>{item.from} → {item.to}</span> },
            { id: "count", header: "Vehicles", cell: (item) => item.count },
            { id: "date", header: "Date", cell: (item) => item.date },
            { id: "impact", header: "Actual Impact", cell: (item) => (
              <StatusIndicator type={item.status}>{item.impact}</StatusIndicator>
            )},
          ]}
          items={executedMoves}
          variant="embedded"
        />
      </Container>
    </SpaceBetween>
  );
};

export default RebalanceActionQueue;
