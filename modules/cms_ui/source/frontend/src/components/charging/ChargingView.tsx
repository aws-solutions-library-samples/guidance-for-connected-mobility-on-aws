// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import {
  Grid,
  Container,
  Header,
  Box,
  Tabs,
  Table,
  StatusIndicator,
  Badge,
  SpaceBetween,
  ProgressBar,
  Alert,
  LineChart,
  BarChart
} from '@cloudscape-design/components';

const ChargingManagementView: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState('active-sessions');

  const activeSessions = [
    {
      sessionId: 'CS-2025-001',
      vehicleVin: 'VIN123456789',
      stationId: 'CHG-STN-001',
      stationName: 'Fleet Depot Station A',
      startTime: '2025-09-24 09:30:00',
      currentCharge: 65,
      targetCharge: 80,
      estimatedCompletion: '2025-09-24 11:15:00',
      chargingRate: '45 kW',
      cost: '$12.50'
    },
    {
      sessionId: 'CS-2025-002',
      vehicleVin: 'VIN987654321',
      stationId: 'CHG-STN-003',
      stationName: 'Public Fast Charger',
      startTime: '2025-09-24 10:00:00',
      currentCharge: 25,
      targetCharge: 90,
      estimatedCompletion: '2025-09-24 12:30:00',
      chargingRate: '150 kW',
      cost: '$28.75'
    }
  ];

  const batteryHealth = [
    {
      vehicleVin: 'VIN123456789',
      batteryId: 'BAT-LFP-001',
      manufacturer: 'CATL',
      capacity: '75 kWh',
      currentHealth: 94,
      cycleCount: 1250,
      degradationRate: '0.8% per year',
      warrantyStatus: 'Active',
      lastInspection: '2025-09-15',
      nextInspection: '2025-12-15'
    },
    {
      vehicleVin: 'VIN987654321',
      batteryId: 'BAT-NCM-002',
      manufacturer: 'LG Chem',
      capacity: '85 kWh',
      currentHealth: 89,
      cycleCount: 1850,
      degradationRate: '1.2% per year',
      warrantyStatus: 'Active',
      lastInspection: '2025-09-10',
      nextInspection: '2025-12-10'
    }
  ];

  const chargingStations = [
    {
      stationId: 'CHG-STN-001',
      name: 'Fleet Depot Station A',
      location: 'Main Depot',
      type: 'Level 2 AC',
      power: '22 kW',
      status: 'Available',
      utilization: 75,
      totalSessions: 145,
      revenue: '$2,850'
    },
    {
      stationId: 'CHG-STN-002',
      name: 'Fleet Depot Station B',
      location: 'Main Depot',
      type: 'DC Fast',
      power: '150 kW',
      status: 'In Use',
      utilization: 92,
      totalSessions: 89,
      revenue: '$4,200'
    },
    {
      stationId: 'CHG-STN-003',
      name: 'Public Fast Charger',
      location: 'Highway Rest Stop',
      type: 'DC Ultra Fast',
      power: '350 kW',
      status: 'Available',
      utilization: 68,
      totalSessions: 234,
      revenue: '$8,750'
    }
  ];

  // Mock data for energy consumption chart
  const energyConsumptionData = [
    { x: new Date('2025-09-17'), y: 1250 },
    { x: new Date('2025-09-18'), y: 1180 },
    { x: new Date('2025-09-19'), y: 1320 },
    { x: new Date('2025-09-20'), y: 1100 },
    { x: new Date('2025-09-21'), y: 980 },
    { x: new Date('2025-09-22'), y: 850 },
    { x: new Date('2025-09-23'), y: 920 },
    { x: new Date('2025-09-24'), y: 1150 }
  ];

  const chargingCostData = [
    { x: 'Level 1', y: 450 },
    { x: 'Level 2', y: 1200 },
    { x: 'DC Fast', y: 2100 },
    { x: 'Ultra Fast', y: 850 }
  ];

  return (
    <SpaceBetween size="l">
      {/* Charging Tiles */}
      <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
        <Container header={<Header variant="h2">Active Charging Sessions</Header>}>
          <Box variant="h1" color="text-status-info">12</Box>
          <Box variant="small" color="text-body-secondary">8 fleet depot, 4 public stations</Box>
          <Box variant="small" color="text-status-success">↑ 15% from yesterday</Box>
        </Container>

        <Container header={<Header variant="h2">Fleet Battery Health</Header>}>
          <Box variant="h1" color="text-status-success">91%</Box>
          <Box variant="small" color="text-body-secondary">Average across 45 vehicles</Box>
          <Box variant="small" color="text-status-info">3 batteries need attention</Box>
        </Container>

        <Container header={<Header variant="h2">Energy Consumption</Header>}>
          <Box variant="h1" color="text-status-info">1,150 kWh</Box>
          <Box variant="small" color="text-status-success">↓ 8% from last week</Box>
          <Box variant="small" color="text-body-secondary">Today's total consumption</Box>
        </Container>

        <Container header={<Header variant="h2">Charging Costs</Header>}>
          <Box variant="h1" color="text-status-warning">$4,600</Box>
          <Box variant="small" color="text-status-error">↑ 12% from last month</Box>
          <Box variant="small" color="text-body-secondary">Monthly average: $4,100</Box>
        </Container>
      </Grid>

      {/* Critical Battery Alert */}
      <Alert type="warning" header="Battery Health Alert">
        Vehicle VIN456789123 battery health has dropped to 78%. Schedule battery inspection and consider replacement planning.
      </Alert>

      {/* Energy Analytics Charts */}
      <Grid gridDefinition={[{ colspan: 6 }, { colspan: 6 }]}>
        <Container header={<Header variant="h3">Energy Consumption Trend (Last 7 Days)</Header>}>
          <LineChart
            series={[
              {
                title: 'Daily Energy Consumption (kWh)',
                type: 'line',
                data: energyConsumptionData
              }
            ]}
            xDomain={[new Date('2025-09-17'), new Date('2025-09-24')]}
            yDomain={[0, 1500]}
            i18nStrings={{
              filterLabel: 'Filter displayed data',
              filterPlaceholder: 'Filter data',
              filterSelectedAriaLabel: 'selected',
              legendAriaLabel: 'Legend',
              chartAriaRoleDescription: 'line chart'
            }}
            ariaLabel="Energy consumption over time"
            height={200}
          />
        </Container>

        <Container header={<Header variant="h3">Charging Costs by Type (This Month)</Header>}>
          <BarChart
            series={[
              {
                title: 'Charging Costs ($)',
                type: 'bar',
                data: chargingCostData
              }
            ]}
            xDomain={['Level 1', 'Level 2', 'DC Fast', 'Ultra Fast']}
            yDomain={[0, 2500]}
            i18nStrings={{
              filterLabel: 'Filter displayed data',
              filterPlaceholder: 'Filter data',
              filterSelectedAriaLabel: 'selected',
              legendAriaLabel: 'Legend',
              chartAriaRoleDescription: 'bar chart'
            }}
            ariaLabel="Charging costs by charger type"
            height={200}
          />
        </Container>
      </Grid>

      {/* Today's Charging Summary */}
      <Container header={<Header variant="h2">Today's Charging Summary</Header>}>
        <SpaceBetween size="s">
          <Box>• 12 active charging sessions across fleet</Box>
          <Box>• 1,150 kWh total energy consumed</Box>
          <Box>• $285 in charging costs incurred</Box>
          <Box>• Average charging efficiency: 94%</Box>
          <Box>• 3 vehicles completed overnight charging</Box>
          <Box>• 2 fast charging sessions at public stations</Box>
        </SpaceBetween>
      </Container>

      {/* Charging Management Tabs */}
      <Tabs
        activeTabId={activeTabId}
        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
        tabs={[
          {
            id: 'active-sessions',
            label: 'Active Charging Sessions',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'sessionId',
                    header: 'Session ID',
                    cell: item => item.sessionId
                  },
                  {
                    id: 'vehicleVin',
                    header: 'Vehicle VIN',
                    cell: item => item.vehicleVin
                  },
                  {
                    id: 'stationName',
                    header: 'Charging Station',
                    cell: item => item.stationName
                  },
                  {
                    id: 'progress',
                    header: 'Charging Progress',
                    cell: item => (
                      <ProgressBar
                        value={item.currentCharge}
                        additionalInfo={`${item.currentCharge}% → ${item.targetCharge}%`}
                        variant={item.currentCharge >= item.targetCharge ? 'success' : undefined}
                      />
                    )
                  },
                  {
                    id: 'chargingRate',
                    header: 'Charging Rate',
                    cell: item => (
                      <Badge color="blue">
                        {item.chargingRate}
                      </Badge>
                    )
                  },
                  {
                    id: 'estimatedCompletion',
                    header: 'Est. Completion',
                    cell: item => item.estimatedCompletion
                  },
                  {
                    id: 'cost',
                    header: 'Current Cost',
                    cell: item => item.cost
                  }
                ]}
                items={activeSessions}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No active charging sessions</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      No vehicles are currently charging.
                    </Box>
                  </Box>
                }
              />
            )
          },
          {
            id: 'battery-passport',
            label: 'Battery Passport & Health',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'vehicleVin',
                    header: 'Vehicle VIN',
                    cell: item => item.vehicleVin
                  },
                  {
                    id: 'batteryId',
                    header: 'Battery ID',
                    cell: item => (
                      <Badge color="grey">
                        {item.batteryId}
                      </Badge>
                    )
                  },
                  {
                    id: 'manufacturer',
                    header: 'Manufacturer',
                    cell: item => item.manufacturer
                  },
                  {
                    id: 'capacity',
                    header: 'Capacity',
                    cell: item => item.capacity
                  },
                  {
                    id: 'currentHealth',
                    header: 'Health Status',
                    cell: item => (
                      <StatusIndicator
                        type={
                          item.currentHealth >= 90 ? 'success' :
                          item.currentHealth >= 80 ? 'warning' : 'error'
                        }
                      >
                        {item.currentHealth}%
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'cycleCount',
                    header: 'Cycle Count',
                    cell: item => item.cycleCount.toLocaleString()
                  },
                  {
                    id: 'degradationRate',
                    header: 'Degradation Rate',
                    cell: item => item.degradationRate
                  },
                  {
                    id: 'warrantyStatus',
                    header: 'Warranty',
                    cell: item => (
                      <StatusIndicator type="success">
                        {item.warrantyStatus}
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'nextInspection',
                    header: 'Next Inspection',
                    cell: item => item.nextInspection
                  }
                ]}
                items={batteryHealth}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No battery data</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      Battery health information will appear here.
                    </Box>
                  </Box>
                }
              />
            )
          },
          {
            id: 'charging-stations',
            label: 'Charging Infrastructure',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'stationId',
                    header: 'Station ID',
                    cell: item => item.stationId
                  },
                  {
                    id: 'name',
                    header: 'Station Name',
                    cell: item => item.name
                  },
                  {
                    id: 'location',
                    header: 'Location',
                    cell: item => item.location
                  },
                  {
                    id: 'type',
                    header: 'Charger Type',
                    cell: item => (
                      <Badge
                        color={
                          item.type.includes('Ultra Fast') ? 'red' :
                          item.type.includes('DC Fast') ? 'blue' : 'green'
                        }
                      >
                        {item.type}
                      </Badge>
                    )
                  },
                  {
                    id: 'power',
                    header: 'Max Power',
                    cell: item => item.power
                  },
                  {
                    id: 'status',
                    header: 'Status',
                    cell: item => (
                      <StatusIndicator
                        type={
                          item.status === 'Available' ? 'success' :
                          item.status === 'In Use' ? 'info' : 'error'
                        }
                      >
                        {item.status}
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'utilization',
                    header: 'Utilization',
                    cell: item => (
                      <ProgressBar
                        value={item.utilization}
                        additionalInfo={`${item.utilization}%`}
                        variant={item.utilization > 90 ? 'error' : item.utilization > 75 ? 'warning' : 'success'}
                      />
                    )
                  },
                  {
                    id: 'totalSessions',
                    header: 'Total Sessions',
                    cell: item => item.totalSessions
                  },
                  {
                    id: 'revenue',
                    header: 'Monthly Revenue',
                    cell: item => item.revenue
                  }
                ]}
                items={chargingStations}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No charging stations</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      Charging infrastructure data will appear here.
                    </Box>
                  </Box>
                }
              />
            )
          }
        ]}
      />
    </SpaceBetween>
  );
};

export default ChargingManagementView;
