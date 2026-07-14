// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import {
  Box,
  Button,
  ButtonGroup,
  ColumnLayout,
  Container,
  ExpandableSection,
  Header,
  Link,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  Table,
  Tabs,
} from "@cloudscape-design/components";

const pendingItems = [
  {
    priority: "Critical" as const,
    vehicle: "VEH-1042",
    agent: "Recommend Agent",
    recommendation:
      "Schedule brake pad replacement — wear at 92%, failure risk within 500mi",
    rootCause: "Deferred brake maintenance causing cost/mile spike",
    savings: "$2,400",
    confidence: 94,
    time: "5:14 PM",
  },
  {
    priority: "High" as const,
    vehicle: "VEH-0387",
    agent: "Recommend Agent",
    recommendation:
      "Reassign from Route 7A to Route 3B — 22% fuel savings",
    rootCause: "Route elevation causing excess fuel consumption",
    savings: "$340/mo",
    confidence: 87,
    time: "4:45 PM",
  },
  {
    priority: "High" as const,
    vehicle: "VEH-0891",
    agent: "Recommend Agent",
    recommendation: "Shift EV charging to off-peak (11PM–6AM)",
    rootCause: "Peak-rate charging at $0.38/kWh vs $0.22 off-peak",
    savings: "$180/mo",
    confidence: 91,
    time: "3:50 PM",
  },
  {
    priority: "Medium" as const,
    vehicle: "Fleet-wide",
    agent: "Lifecycle Agent",
    recommendation:
      "Negotiate bulk fuel contract — volume qualifies for Tier 2",
    rootCause:
      "Fleet consuming 12,400 gal/month exceeds discount threshold",
    savings: "$4,200/mo",
    confidence: 78,
    time: "2:30 PM",
  },
];

const priorityType = {
  Critical: "error",
  High: "warning",
  Medium: "info",
} as const;

const columnDefs = [
  {
    id: "priority",
    header: "Priority",
    cell: (item: (typeof pendingItems)[0]) => (
      <StatusIndicator type={priorityType[item.priority]}>
        {item.priority}
      </StatusIndicator>
    ),
    width: 120,
  },
  {
    id: "vehicle",
    header: "Vehicle",
    cell: (item: (typeof pendingItems)[0]) => <Link>{item.vehicle}</Link>,
    width: 110,
  },
  {
    id: "agent",
    header: "Agent",
    cell: (item: (typeof pendingItems)[0]) => item.agent,
    width: 140,
  },
  {
    id: "recommendation",
    header: "Recommendation",
    cell: (item: (typeof pendingItems)[0]) => item.recommendation,
    width: 300,
  },
  {
    id: "rootCause",
    header: "Root Cause",
    cell: (item: (typeof pendingItems)[0]) => item.rootCause,
    width: 280,
  },
  {
    id: "savings",
    header: "Estimated Savings",
    cell: (item: (typeof pendingItems)[0]) => (
      <Box fontWeight="bold">{item.savings}</Box>
    ),
    width: 130,
  },
  {
    id: "confidence",
    header: "Confidence",
    cell: (item: (typeof pendingItems)[0]) => (
      <ProgressBar value={item.confidence} label={`${item.confidence}%`} />
    ),
    width: 160,
  },
  {
    id: "time",
    header: "Time",
    cell: (item: (typeof pendingItems)[0]) => item.time,
    width: 90,
  },
  {
    id: "actions",
    header: "Actions",
    cell: () => (
      <SpaceBetween direction="horizontal" size="xs">
        <Button variant="primary">Approve</Button>
        <Button variant="normal">Reject</Button>
      </SpaceBetween>
    ),
    width: 180,
  },
];

const ActionQueue: React.FC = () => (
  <SpaceBetween size="l">
    <Tabs
      tabs={[
        {
          label: "Pending Approval (4)",
          id: "pending",
          content: (
            <SpaceBetween size="l">
              <Table
                selectionType="multi"
                items={pendingItems}
                columnDefinitions={columnDefs}
                variant="embedded"
                empty={<Box textAlign="center">No pending recommendations</Box>}
              />

              <ExpandableSection
                defaultExpanded
                variant="container"
                headerText="Recommendation Detail — VEH-1042 Brake Replacement"
              >
                <ColumnLayout columns={2}>
                  <SpaceBetween size="m">
                    <Box>
                      <Box variant="h4">What was detected</Box>
                      <Box variant="p">
                        Monitor Agent detected a 58% cost-per-mile spike on
                        VEH-1042 over the past 14 days. Brake telemetry shows
                        pad thickness at 8% remaining (92% worn), with
                        vibration frequency increasing 3.2× above baseline.
                      </Box>
                    </Box>
                    <Box>
                      <Box variant="h4">What data was queried</Box>
                      <Box variant="p">
                        • Vehicle telemetry — brake pad wear sensor, vibration
                        data (last 30 days)
                        <br />
                        • Maintenance history — last brake service 18 months
                        ago at 42,000 mi
                        <br />• Cost data lake — per-mile cost trend,
                        parts pricing, labor rates
                      </Box>
                    </Box>
                    <Box>
                      <Box variant="h4">Root cause</Box>
                      <Box variant="p">
                        Deferred brake maintenance. Pads exceeded recommended
                        replacement interval by 6,200 miles. Continued
                        operation risks rotor damage and caliper failure.
                        <br />
                        <strong>Confidence: 94%</strong>
                      </Box>
                    </Box>
                  </SpaceBetween>

                  <SpaceBetween size="m">
                    <Box>
                      <Box variant="h4">Recommended action</Box>
                      <Box variant="p">
                        Schedule brake pad replacement within 500 miles.
                        Preferred vendor: FleetParts Direct — $380 parts +
                        $220 labor. Estimated 2-hour downtime during
                        scheduled maintenance window (Tue/Thu 6–8 AM).
                      </Box>
                    </Box>
                    <Box>
                      <Box variant="h4">If approved</Box>
                      <Box variant="p">
                        • Brake pad replacement cost: $600
                        <br />
                        • Avoided emergency repair: $2,400
                        <br />
                        • Avoided towing: $800
                        <br />
                        • Avoided unplanned downtime: ~1.5 days
                        <br />
                        <strong>Net savings: $2,600+</strong>
                      </Box>
                    </Box>
                    <Box>
                      <Box variant="h4">If no action taken</Box>
                      <Box variant="p">
                        Projected cost trajectory: $2,400 emergency brake
                        repair + $800 towing + 1.5 days unplanned downtime
                        ($1,200 lost revenue). Rotor damage probability
                        reaches 80% within 500 miles at current wear rate.
                      </Box>
                    </Box>
                  </SpaceBetween>
                </ColumnLayout>
              </ExpandableSection>
            </SpaceBetween>
          ),
        },
        {
          label: "Auto-Approved (1)",
          id: "auto",
          content: (
            <Container>
              <Box padding="l" textAlign="center" color="text-status-inactive">
                1 auto-approved recommendation this period
              </Box>
            </Container>
          ),
        },
        {
          label: "History (23)",
          id: "history",
          content: (
            <Container>
              <Box padding="l" textAlign="center" color="text-status-inactive">
                23 resolved recommendations
              </Box>
            </Container>
          ),
        },
      ]}
    />
  </SpaceBetween>
);

export default ActionQueue;
