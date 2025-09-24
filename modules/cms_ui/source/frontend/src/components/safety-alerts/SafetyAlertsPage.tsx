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
  Alert
} from '@cloudscape-design/components';

const SafetyManagementView: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState('active-alerts');

  const activeAlerts = [
    {
      alertId: 'SA-001',
      vehicleVin: 'VIN123456789',
      driverName: 'John Smith',
      alertType: 'Hard Braking',
      severity: 'High',
      timestamp: '2025-09-24 10:45:00',
      location: 'Highway 101, Mile 45'
    },
    {
      alertId: 'SA-002',
      vehicleVin: 'VIN987654321',
      driverName: 'Sarah Johnson',
      alertType: 'Speeding',
      severity: 'Medium',
      timestamp: '2025-09-24 09:30:00',
      location: 'Main St & 5th Ave'
    },
    {
      alertId: 'SA-003',
      vehicleVin: 'VIN456789123',
      driverName: 'Mike Wilson',
      alertType: 'Rapid Acceleration',
      severity: 'Low',
      timestamp: '2025-09-24 08:15:00',
      location: 'Industrial Blvd'
    }
  ];

  const incidents = [
    {
      incidentId: 'INC-001',
      vehicleVin: 'VIN111222333',
      driverName: 'Alex Brown',
      incidentType: 'Minor Collision',
      status: 'Under Investigation',
      reportedDate: '2025-09-23',
      severity: 'Medium'
    },
    {
      incidentId: 'INC-002',
      vehicleVin: 'VIN444555666',
      driverName: 'Lisa Davis',
      incidentType: 'Property Damage',
      status: 'Resolved',
      reportedDate: '2025-09-22',
      severity: 'Low'
    }
  ];

  return (
    <SpaceBetween size="l">
      {/* Safety Tiles */}
      <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
        <Container header={<Header variant="h2">Active Safety Alerts</Header>}>
          <Box variant="h1" color="text-status-error">12</Box>
          <Box variant="small" color="text-body-secondary">3 High, 5 Medium, 4 Low</Box>
        </Container>

        <Container header={<Header variant="h2">Fleet Safety Score</Header>}>
          <Box variant="h1" color="text-status-success">87%</Box>
          <Box variant="small" color="text-status-success">↑ 3% from last month</Box>
          <Box variant="small" color="text-body-secondary">Industry average: 82%</Box>
        </Container>

        <Container header={<Header variant="h2">Incidents This Month</Header>}>
          <Box variant="h1" color="text-status-warning">2</Box>
          <Box variant="small" color="text-status-success">↓ 50% from last month</Box>
          <Box variant="small" color="text-body-secondary">1 resolved, 1 investigating</Box>
        </Container>

        <Container header={<Header variant="h2">Driver Training Due</Header>}>
          <Box variant="h1" color="text-status-info">8</Box>
          <Box variant="small" color="text-body-secondary">Drivers requiring safety training</Box>
          <Box variant="small" color="text-body-secondary">Next deadline: Oct 1, 2025</Box>
        </Container>
      </Grid>

      {/* Critical Alerts */}
      <Alert type="warning" header="Critical Safety Alert">
        Vehicle VIN123456789 has exceeded speed limit by 25+ mph. Driver John Smith requires immediate attention.
      </Alert>

      {/* Today's Safety Summary */}
      <Container header={<Header variant="h2">Today's Safety Summary</Header>}>
        <SpaceBetween size="s">
          <Box>• 3 Hard braking events detected across fleet</Box>
          <Box>• 2 Speeding violations recorded</Box>
          <Box>• 1 Rapid acceleration event</Box>
          <Box>• 0 Collision alerts (Good!)</Box>
          <Box>• Average driver safety score: 92%</Box>
        </SpaceBetween>
      </Container>

      {/* Safety Tabs */}
      <Tabs
        activeTabId={activeTabId}
        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
        tabs={[
          {
            id: 'active-alerts',
            label: 'Active Safety Alerts',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'alertId',
                    header: 'Alert ID',
                    cell: item => item.alertId
                  },
                  {
                    id: 'vehicleVin',
                    header: 'Vehicle VIN',
                    cell: item => item.vehicleVin
                  },
                  {
                    id: 'driverName',
                    header: 'Driver',
                    cell: item => item.driverName
                  },
                  {
                    id: 'alertType',
                    header: 'Alert Type',
                    cell: item => (
                      <Badge
                        color={
                          item.alertType === 'Hard Braking' ? 'red' :
                          item.alertType === 'Speeding' ? 'blue' : 'grey'
                        }
                      >
                        {item.alertType}
                      </Badge>
                    )
                  },
                  {
                    id: 'severity',
                    header: 'Severity',
                    cell: item => (
                      <StatusIndicator
                        type={
                          item.severity === 'High' ? 'error' :
                          item.severity === 'Medium' ? 'warning' : 'info'
                        }
                      >
                        {item.severity}
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'timestamp',
                    header: 'Time',
                    cell: item => item.timestamp
                  },
                  {
                    id: 'location',
                    header: 'Location',
                    cell: item => item.location
                  }
                ]}
                items={activeAlerts}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No active safety alerts</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      All vehicles are operating safely.
                    </Box>
                  </Box>
                }
              />
            )
          },
          {
            id: 'incidents',
            label: 'Safety Incidents',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'incidentId',
                    header: 'Incident ID',
                    cell: item => item.incidentId
                  },
                  {
                    id: 'vehicleVin',
                    header: 'Vehicle VIN',
                    cell: item => item.vehicleVin
                  },
                  {
                    id: 'driverName',
                    header: 'Driver',
                    cell: item => item.driverName
                  },
                  {
                    id: 'incidentType',
                    header: 'Incident Type',
                    cell: item => item.incidentType
                  },
                  {
                    id: 'status',
                    header: 'Status',
                    cell: item => (
                      <StatusIndicator
                        type={
                          item.status === 'Resolved' ? 'success' :
                          item.status === 'Under Investigation' ? 'pending' : 'info'
                        }
                      >
                        {item.status}
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'reportedDate',
                    header: 'Reported Date',
                    cell: item => item.reportedDate
                  },
                  {
                    id: 'severity',
                    header: 'Severity',
                    cell: item => (
                      <StatusIndicator
                        type={
                          item.severity === 'High' ? 'error' :
                          item.severity === 'Medium' ? 'warning' : 'success'
                        }
                      >
                        {item.severity}
                      </StatusIndicator>
                    )
                  }
                ]}
                items={incidents}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No safety incidents</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      No incidents reported this period.
                    </Box>
                  </Box>
                }
              />
            )
          },
          {
            id: 'driver-scores',
            label: 'Driver Safety Scores',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'driverName',
                    header: 'Driver Name',
                    cell: () => 'John Smith'
                  },
                  {
                    id: 'safetyScore',
                    header: 'Safety Score',
                    cell: () => (
                      <StatusIndicator type="success">
                        92%
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'alertsThisMonth',
                    header: 'Alerts This Month',
                    cell: () => '2'
                  },
                  {
                    id: 'trainingStatus',
                    header: 'Training Status',
                    cell: () => (
                      <StatusIndicator type="success">
                        Current
                      </StatusIndicator>
                    )
                  }
                ]}
                items={[{ id: '1' }]}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No driver data</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      Driver safety scores will appear here.
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

export default SafetyManagementView;
