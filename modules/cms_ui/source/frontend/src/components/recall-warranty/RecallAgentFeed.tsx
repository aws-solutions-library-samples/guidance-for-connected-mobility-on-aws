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

const feed = [
  { time: "2:17 PM", type: "success" as const, msg: "Rebalancing triggered: 4 vehicles grounded in Dallas → requesting 3 from Denver surplus to cover" },
  { time: "2:16 PM", type: "success" as const, msg: "Warranty claim drafted: VEH-0042 brake pads — $1,840, covered under powertrain warranty (38K/50K miles). Telemetry evidence attached." },
  { time: "2:15 PM", type: "warning" as const, msg: "Service scheduling: 8 vehicles for recall 24V832000. Rush Truck Dallas (4 slots Mar 28), Penske Denver (4 slots Apr 2). Parts confirmed." },
  { time: "2:14 PM", type: "error" as const, msg: "GROUND IMMEDIATELY: VEH-0042, VEH-0087 — brake chamber defect confirmed via telemetry. Severity: Critical. Awaiting approval." },
  { time: "2:14 PM", type: "warning" as const, msg: "Recall 24V832000 processed: 200 VIN matches → telemetry cross-ref → 12 CONFIRMED (defect detected), 35 POPULATION (VIN only)" },
  { time: "2:14 PM", type: "info" as const, msg: "New recall ingested: NHTSA 24V832000 — Freightliner Cascadia brake chamber mounting bolts. Scanning fleet..." },
  { time: "1:30 PM", type: "warning" as const, msg: "Warranty expiring: VEH-0301 EGR valve failure — coverage expires in 34 days. Claim $680. Draft ready for review." },
  { time: "1:15 PM", type: "info" as const, msg: "Warranty scan complete: 5 new eligible failures detected across fleet. Total recoverable: $6,430." },
  { time: "10:00 AM", type: "success" as const, msg: "Daily compliance review: Recall 25V041000 at 74% completion (6/23 done, 15 scheduled). On track." },
  { time: "9:30 AM", type: "success" as const, msg: "Warranty claim CLM-2026-035 paid: $320 for VEH-0201 coolant sensor. Total recovered YTD: $1,210." },
];

const RecallAgentFeed: React.FC = () => {
  return (
    <Container header={<Header variant="h2">Agent Activity</Header>}>
      <SpaceBetween size="s">
        {feed.map((item, i) => (
          <Box key={i} padding={{ vertical: "xs" }}>
            <SpaceBetween direction="horizontal" size="s">
              <Box color="text-body-secondary" fontSize="body-s" display="inline" variant="span">{item.time}</Box>
              <StatusIndicator type={item.type}>{item.msg}</StatusIndicator>
            </SpaceBetween>
          </Box>
        ))}
      </SpaceBetween>
    </Container>
  );
};

export default RecallAgentFeed;
