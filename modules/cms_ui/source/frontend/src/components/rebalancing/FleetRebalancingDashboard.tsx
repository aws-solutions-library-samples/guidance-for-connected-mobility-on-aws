// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect, useState } from "react";
import {
  Box,
  Button,
  ColumnLayout,
  Container,
  Header,
  Link,
  SpaceBetween,
  StatusIndicator,
  Table,
  Tabs,
} from "@cloudscape-design/components";
import BarChart from "@cloudscape-design/components/bar-chart";
import RebalanceActionQueue from "./RebalanceActionQueue";
import VehicleAvailability from "./VehicleAvailability";
import RebalanceAgentFeed from "./RebalanceAgentFeed";
import UtilizationHeatmap from "./UtilizationHeatmap";
import { getApiEndpoint } from "../../config/api";
import { authFetch } from "../../utils/authFetch";

const chartI18n = {
  filterLabel: "Filter",
  filterPlaceholder: "Filter data",
  filterSelectedAriaLabel: "selected",
  legendAriaLabel: "Legend",
  chartAriaRoleDescription: "chart",
  xAxisAriaRoleDescription: "x axis",
  yAxisAriaRoleDescription: "y axis",
};

const emptyBox = (
  <Box textAlign="center" color="inherit">
    <b>No data available</b>
  </Box>
);

type Location = {
  locationId: string;
  totalVehicles: number;
  activeVehicles: number;
  idleVehicles: number;
  utilizationPercent: number;
  status: "surplus" | "deficit" | "balanced";
  surplus: number;
};

const FleetRebalancingDashboard: React.FC = () => {
  const [locations, setLocations] = useState<Location[]>([]);
  const [summary, setSummary] = useState<any>(null);

  useEffect(() => {
    const base = getApiEndpoint().replace(/\/$/, '');
    authFetch(`${base}/api/v1/rebalancing/locations`)
      .then(r => r.json())
      .then(d => {
        setLocations(d.locations || []);
        setSummary(d.summary || null);
      })
      .catch(() => {});
  }, []);

  const fleetUtil = summary?.fleetUtilizationPct || 0;
  const totalVehicles = summary?.totalVehicles || 0;
  const totalActive = summary?.totalActive || 0;
  const totalIdle = summary?.totalIdle || 0;
  const surplusLocations = summary?.surplusLocations || 0;
  const deficitLocations = summary?.deficitLocations || 0;
  return (
    <SpaceBetween size="l">
      {/* KPI Cards — matching TCO card style */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(6, 1fr)', gap: '16px' }}>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Fleet Utilization</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{fleetUtil}%</span>
            <StatusIndicator type={fleetUtil >= 80 ? "success" : fleetUtil >= 70 ? "warning" : "error"}>
              {fleetUtil >= 80 ? "Healthy range" : fleetUtil >= 70 ? "Below target (80%)" : "Critical — below 70%"}
            </StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Total Vehicles</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{totalVehicles}</span>
            <Box color="text-body-secondary" fontSize="body-s">Across {locations.length} locations</Box>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Active</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{totalActive}</span>
            <StatusIndicator type="success">In service</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Idle</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{totalIdle}</span>
            <StatusIndicator type="warning">Not generating revenue</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Surplus Locations</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{surplusLocations}</span>
            <StatusIndicator type="info">Move vehicles out</StatusIndicator>
          </SpaceBetween>
        </Container>
        <Container>
          <SpaceBetween size="xxs">
            <span style={{ fontSize: '12px', fontWeight: 700, textTransform: 'uppercase', color: '#656871', letterSpacing: '0.5px' }}>Deficit Locations</span>
            <span style={{ fontSize: '32px', fontWeight: 700, display: 'block', lineHeight: 1.2 }}>{deficitLocations}</span>
            <StatusIndicator type="error">Need vehicles</StatusIndicator>
          </SpaceBetween>
        </Container>
      </div>

      {/* Utilization by Location */}
      <Container header={<Header variant="h2" actions={<Button>Refresh</Button>}>Utilization by Location</Header>}>
        <BarChart
          series={[
            {
              title: "Utilization %",
              type: "bar",
              data: locations.map(l => ({ x: l.locationId, y: l.utilizationPercent })),
              color: "#0972d3",
            },
          ]}
          xDomain={locations.map(l => l.locationId)}
          yDomain={[0, 100]}
          xTitle="Location"
          yTitle="Utilization %"
          height={280}
          hideFilter
          i18nStrings={chartI18n}
          empty={emptyBox}
          additionalFilters={
            <Box color="text-body-secondary" fontSize="body-s">
              Target: 80–90% · Below 70% = surplus · Above 90% = deficit
            </Box>
          }
        />
      </Container>

      {/* Location Detail Table */}
      <Container header={<Header variant="h2">Location Status</Header>}>
        <Table
          columnDefinitions={[
            { id: "id", header: "Location", cell: (item: Location) => <Link>{item.locationId}</Link> },
            { id: "vehicles", header: "Vehicles", cell: (item: Location) => item.totalVehicles },
            { id: "active", header: "Active", cell: (item: Location) => item.activeVehicles },
            { id: "idle", header: "Idle", cell: (item: Location) => <span style={{ color: item.idleVehicles > 10 ? "#8D6605" : undefined }}>{item.idleVehicles}</span> },
            { id: "util", header: "Utilization", cell: (item: Location) => {
              const type: "success" | "warning" | "error" = item.status === 'balanced' ? 'success' : item.status === 'surplus' ? 'warning' : 'error';
              return <StatusIndicator type={type}>{item.utilizationPercent}%</StatusIndicator>;
            }},
            { id: "surplus", header: "Surplus / Deficit", cell: (item: Location) => (
              <span style={{ color: item.surplus > 0 ? "#0972d3" : item.surplus < 0 ? "#d91515" : undefined }}>
                {item.surplus > 0 ? `+${item.surplus} surplus` : item.surplus < 0 ? `${item.surplus} deficit` : "Balanced"}
              </span>
            )},
          ]}
          items={locations}
          sortingColumn={{ sortingField: "util" }}
          sortingDescending
          variant="embedded"
        />
      </Container>

      {/* Tabs */}
      <Tabs
        tabs={[
          {
            label: "Supply-Demand Heatmap",
            id: "heatmap",
            content: <UtilizationHeatmap locations={locations} />,
          },
          {
            label: "Rebalance Actions",
            id: "actions",
            content: <RebalanceActionQueue />,
          },
          {
            label: "Vehicle Availability",
            id: "availability",
            content: <VehicleAvailability />,
          },
          {
            label: "Agent Activity",
            id: "agent",
            content: <RebalanceAgentFeed />,
          },
        ]}
      />
    </SpaceBetween>
  );
};

export default FleetRebalancingDashboard;
