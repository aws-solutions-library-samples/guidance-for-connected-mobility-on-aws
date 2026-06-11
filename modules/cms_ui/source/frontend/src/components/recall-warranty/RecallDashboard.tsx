// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from "react";
import {
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  Link,
  ProgressBar,
  SpaceBetween,
  StatusIndicator,
  Table,
  Tabs,
} from "@cloudscape-design/components";
import WarrantyDashboard from "./WarrantyDashboard";
import RecallActionQueue from "./RecallActionQueue";
import RecallAgentFeed from "./RecallAgentFeed";
import { nhtsaRecalls } from "./nhtsaRecallData";
import ScheduleRecallServiceModal from "./ScheduleRecallServiceModal";

// Use real NHTSA data
const activeRecalls = nhtsaRecalls.map(r => ({
  ...r,
  severity: r.severity as "Critical" | "High" | "Medium" | "Low",
}));

// Build vehicle statuses from real recall data
const vehicleStatuses = activeRecalls.flatMap(recall =>
  (recall.vehicles || []).slice(0, 4).map((vid: string, idx: number) => ({
    id: vid,
    vin: `1HGBH41JXMN${vid.replace('VEH-', '').padStart(6, '0')}`,
    recall: recall.id,
    component: recall.component.split(':')[0],
    matchType: idx === 0 ? "CONFIRMED" : "POPULATION",
    severity: idx === 0 ? recall.severity : "Monitor",
    status: idx === 0 ? "Scheduled" : "Monitoring",
    dealer: idx === 0 ? "Rush Truck — Dallas" : "—",
    scheduledDate: idx === 0 ? "Apr 2" : "—",
  }))
).slice(0, 12);

const totalAffected = activeRecalls.reduce((s, r) => s + r.affected, 0);
const totalCompleted = activeRecalls.reduce((s, r) => s + r.completed, 0);
const totalGrounded = activeRecalls.reduce((s, r) => s + r.grounded, 0);
const completionPct = Math.round((totalCompleted / totalAffected) * 100);

const RecallDashboard: React.FC = () => {
  // Schedule Recall Service modal — same component used by the Recalls
  // tab on /alerts/maintenance, so the Schedule action behaves
  // identically wherever an Active Recalls table appears in the app.
  // We only own visibility + which recall is being scheduled; the modal
  // owns the form, multi-VIN selection, and the API submit.
  const [scheduleModalVisible, setScheduleModalVisible] = useState(false);
  const [scheduleRecall, setScheduleRecall] = useState<any>(null);

  const openScheduleModal = (recall: any) => {
    setScheduleRecall(recall);
    setScheduleModalVisible(true);
  };

  return (
    <SpaceBetween size="l">
      {/* KPI Cards */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '16px' }}>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Active Recalls</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{activeRecalls.length}</span>
            <StatusIndicator type="warning">Across fleet</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Vehicles Affected</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{totalAffected}</span>
            <Box color="text-body-secondary" fontSize="body-s">VIN matched + telemetry confirmed</Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Confirmed via Telemetry</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{activeRecalls.reduce((s, r) => s + r.confirmed, 0)}</span>
            <StatusIndicator type="error">Defect detected</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Grounded</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{totalGrounded}</span>
            <StatusIndicator type="error">Out of service</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Completed</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{totalCompleted}</span>
            <StatusIndicator type="success">Serviced</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Compliance</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{completionPct}%</span>
            <ProgressBar value={completionPct} variant="key-value" />
          </SpaceBetween>
        </Container>
      </div>

      {/* Active Recalls Table */}
      <Container header={
        <Header variant="h2" counter={`(${activeRecalls.length})`}
          description="Source: NHTSA Recalls API + OEM feeds"
          actions={<Button iconName="refresh">Check for New Recalls</Button>}>
          Active Recalls
        </Header>
      }>
        <Table
          columnDefinitions={[
            { id: "id", header: "NHTSA #", cell: (item) => <Link href={`https://www.nhtsa.gov/recalls?nhtsaId=${item.id}`} external>{item.id}</Link>, width: 110 },
            { id: "severity", header: "Severity", cell: (item) => (
              <StatusIndicator type={item.severity === "Critical" ? "error" : item.severity === "High" ? "warning" : "info"}>{item.severity}</StatusIndicator>
            ), width: 95 },
            { id: "component", header: "Component", cell: (item) => item.component, width: 200 },
            { id: "summary", header: "Summary", cell: (item) => (
              <Box color="text-body-secondary" fontSize="body-s">{item.summary}</Box>
            ) },
            { id: "affected", header: "Affected", cell: (item) => (
              <span>{item.confirmed} confirmed / {item.population} pop.</span>
            ), width: 140 },
            { id: "progress", header: "Progress", cell: (item) => {
              const pct = item.affected > 0 ? Math.round(((item.completed + item.scheduled) / item.affected) * 100) : 0;
              return <ProgressBar value={pct} additionalInfo={`${item.completed} done, ${item.scheduled} scheduled`} variant="key-value" />;
            }, width: 180 },
            { id: "grounded", header: "Grounded", cell: (item) => item.grounded > 0 ? (
              <StatusIndicator type="error">{item.grounded} vehicles</StatusIndicator>
            ) : <StatusIndicator type="success">None</StatusIndicator>, width: 110 },
            // Icon-only Schedule action; see ServiceDashboard.tsx for
            // the rationale. Same shared <ScheduleRecallServiceModal />
            // is opened, so operators get one consistent recall-service
            // experience whichever page they're on.
            { id: "actions", header: "Actions", cell: (item) => {
              const hasVehicles = (item.vehicles || []).length > 0;
              if (!hasVehicles) {
                return <Box color="text-body-secondary">—</Box>;
              }
              return (
                <span title="Schedule recall service for affected VINs">
                  <Button
                    iconName="calendar"
                    variant="inline-icon"
                    ariaLabel="Schedule recall service for affected VINs"
                    onClick={() => openScheduleModal(item)}
                  />
                </span>
              );
            }, width: 80 },
          ]}
          items={activeRecalls}
          variant="embedded"
        />
      </Container>

      {/* Tabs */}
      <Tabs
        tabs={[
          {
            label: "Vehicle Status",
            id: "vehicles",
            content: (
              <Container header={<Header variant="h2" counter={`(${vehicleStatuses.length})`}>Vehicle Recall Status</Header>}>
                <Table
                  columnDefinitions={[
                    { id: "id", header: "Vehicle", cell: (item) => <Link>{item.id}</Link> },
                    { id: "recall", header: "Recall #", cell: (item) => item.recall },
                    { id: "component", header: "Component", cell: (item) => item.component },
                    { id: "matchType", header: "Match", cell: (item) => (
                      <StatusIndicator type={item.matchType === "CONFIRMED" ? "error" : "info"}>{item.matchType}</StatusIndicator>
                    )},
                    { id: "severity", header: "Severity", cell: (item) => (
                      <StatusIndicator type={item.severity === "Critical" ? "error" : item.severity === "High" ? "warning" : item.severity === "Monitor" ? "info" : "stopped"}>{item.severity}</StatusIndicator>
                    )},
                    { id: "status", header: "Status", cell: (item) => (
                      <StatusIndicator type={
                        item.status === "Completed" ? "success" : item.status === "Grounded" ? "error" : item.status === "Scheduled" ? "in-progress" : "pending"
                      }>{item.status}</StatusIndicator>
                    )},
                    { id: "dealer", header: "Dealer", cell: (item) => item.dealer },
                    { id: "date", header: "Scheduled", cell: (item) => item.scheduledDate },
                  ]}
                  items={vehicleStatuses}
                  variant="embedded"
                  stickyHeader
                />
              </Container>
            ),
          },
          {
            label: "Warranty Claims",
            id: "warranty",
            content: <WarrantyDashboard />,
          },
          {
            label: "Actions",
            id: "actions",
            content: <RecallActionQueue />,
          },
          {
            label: "Agent Activity",
            id: "agent",
            content: <RecallAgentFeed />,
          },
        ]}
      />

      {/*
       * Shared Schedule Recall Service modal — same component the
       * Recalls tab on /alerts/maintenance renders. We don't pass a
       * vehicleVinMap here, so the modal fetches its own copy on first
       * open. Single fetch per mount, cached for subsequent opens.
       */}
      <ScheduleRecallServiceModal
        visible={scheduleModalVisible}
        recall={scheduleRecall}
        onDismiss={() => setScheduleModalVisible(false)}
      />
    </SpaceBetween>
  );
};

export default RecallDashboard;
