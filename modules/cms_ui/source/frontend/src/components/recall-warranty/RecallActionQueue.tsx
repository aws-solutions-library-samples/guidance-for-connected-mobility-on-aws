// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import {
  Box,
  Button,
  Container,
  Header,
  Popover,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  Table,
} from "@cloudscape-design/components";

const pendingActions = [
  {
    priority: "Critical" as const,
    type: "Ground Vehicle",
    vehicle: "VEH-0049",
    recall: "24V832000",
    detail: "Brake chamber — telemetry confirms degradation. Ground immediately.",
    confidence: 96,
    time: "2:14 PM",
  },
  {
    priority: "Critical" as const,
    type: "Ground Vehicle",
    vehicle: "VEH-0008",
    recall: "24V832000",
    detail: "Brake chamber — telemetry confirms degradation. Ground immediately.",
    confidence: 94,
    time: "2:14 PM",
  },
  {
    priority: "High" as const,
    type: "Schedule Service",
    vehicle: "VEH-0047 + 7 others",
    recall: "24V832000",
    detail: "Brake recall — Rush Truck Dallas (4 slots), Penske Denver (4 slots).",
    confidence: 91,
    time: "2:15 PM",
  },
  {
    priority: "High" as const,
    type: "File Warranty Claim",
    vehicle: "VEH-0049",
    recall: "—",
    detail: "Brake pads at 38K mi — covered under warranty (50K limit). Claim: $1,840.",
    confidence: 94,
    time: "2:16 PM",
  },
  {
    priority: "High" as const,
    type: "Rebalance Fleet",
    vehicle: "4 vehicles",
    recall: "24V832000",
    detail: "4 grounded in Dallas — move 3 from Denver. Transfer: $900.",
    confidence: 88,
    time: "2:17 PM",
  },
  {
    priority: "Medium" as const,
    type: "File Warranty Claim",
    vehicle: "VEH-0004",
    recall: "—",
    detail: "EGR valve — warranty expires in 34 days. Claim: $680.",
    confidence: 82,
    time: "1:30 PM",
  },
];

const RecallActionQueue: React.FC = () => {
  return (
    <SpaceBetween size="l">
      <Container header={
        <Header variant="h2" counter={`(${pendingActions.length})`}
          actions={
            <SpaceBetween direction="horizontal" size="xs">
              <Button>Configure Auto-Approval</Button>
              <Button variant="primary">Approve All Critical</Button>
            </SpaceBetween>
          }>
          Pending Actions
        </Header>
      }>
        <Table
          columnDefinitions={[
            { id: "priority", header: "Priority", cell: (item) => (
              <StatusIndicator type={item.priority === "Critical" ? "error" : item.priority === "High" ? "warning" : "info"}>{item.priority}</StatusIndicator>
            ), width: 90 },
            { id: "type", header: "Action", cell: (item) => <span style={{ fontWeight: 700 }}>{item.type}</span>, width: 140 },
            { id: "vehicle", header: "Vehicle(s)", cell: (item) => item.vehicle, width: 130 },
            { id: "recall", header: "Recall", cell: (item) => item.recall !== "—" ? item.recall : "Warranty", width: 95 },
            // Detail strings can run 60-90 chars and were forcing the
            // Actions column off-screen on standard 1280px laptops.
            // Ellipsis-clip in the cell and surface the full text via a
            // Popover so the operator can still read it without losing
            // sight of the approve/reject buttons. The clip is loose
            // enough (~50 chars) that short details render in full and
            // only the verbose ones are truncated.
            { id: "detail", header: "Detail", cell: (item) => {
              const full = String(item.detail || "");
              const MAX = 50;
              const clipped = full.length > MAX ? full.slice(0, MAX).trimEnd() + "…" : full;
              if (clipped === full) return full;
              return (
                <Popover
                  dismissButton={false}
                  position="top"
                  size="large"
                  triggerType="text"
                  content={<Box variant="p">{full}</Box>}
                >
                  <span style={{ cursor: "help" }}>{clipped}</span>
                </Popover>
              );
            }, width: 230 },
            { id: "confidence", header: "Confidence", cell: (item) => (
              <ProgressBar value={item.confidence} additionalInfo={`${item.confidence}%`} variant="key-value" />
            ), width: 110 },
            { id: "time", header: "Time", cell: (item) => item.time, width: 70 },
            { id: "actions", header: "", cell: () => (
              <div style={{ display: 'flex', gap: '4px', flexWrap: 'nowrap' }}>
                <Button variant="primary" iconName="check" />
                <Button iconName="close" />
              </div>
            ), width: 85 },
          ]}
          items={pendingActions}
          variant="embedded"
          stickyHeader
        />
      </Container>
    </SpaceBetween>
  );
};

export default RecallActionQueue;
