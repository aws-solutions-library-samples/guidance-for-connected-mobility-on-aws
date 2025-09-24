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
  Alert
} from '@cloudscape-design/components';

const WarrantyManagementView: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState('active-claims');

  const activeClaims = [
    {
      claimId: 'WC-2025-001',
      vehicleVin: 'VIN123456789',
      component: 'Battery Pack',
      issueDescription: 'Reduced capacity below 80%',
      status: 'Under Review',
      filedDate: '2025-09-20',
      estimatedResolution: '2025-10-05'
    },
    {
      claimId: 'WC-2025-002',
      vehicleVin: 'VIN987654321',
      component: 'Motor Controller',
      issueDescription: 'Intermittent power loss',
      status: 'Approved',
      filedDate: '2025-09-18',
      estimatedResolution: '2025-09-28'
    },
    {
      claimId: 'WC-2025-003',
      vehicleVin: 'VIN456789123',
      component: 'Charging Port',
      issueDescription: 'Connection failure',
      status: 'Parts Ordered',
      filedDate: '2025-09-15',
      estimatedResolution: '2025-09-30'
    }
  ];

  const warrantyStatus = [
    {
      vehicleVin: 'VIN123456789',
      model: 'Model X Pro',
      warrantyType: 'Comprehensive',
      startDate: '2023-01-15',
      endDate: '2026-01-15',
      remainingMonths: 16,
      coveragePercent: 85
    },
    {
      vehicleVin: 'VIN987654321',
      model: 'Model Y Standard',
      warrantyType: 'Powertrain',
      startDate: '2022-06-10',
      endDate: '2030-06-10',
      remainingMonths: 68,
      coveragePercent: 95
    },
    {
      vehicleVin: 'VIN456789123',
      model: 'Model Z Fleet',
      warrantyType: 'Extended',
      startDate: '2024-03-20',
      endDate: '2029-03-20',
      remainingMonths: 54,
      coveragePercent: 100
    }
  ];

  const recalls = [
    {
      recallId: 'RC-2025-EV-001',
      component: 'Battery Management System',
      affectedVehicles: 15,
      severity: 'High',
      status: 'Active',
      issuedDate: '2025-09-10',
      description: 'Software update required for battery monitoring'
    },
    {
      recallId: 'RC-2025-EV-002',
      component: 'Door Handle Mechanism',
      affectedVehicles: 3,
      severity: 'Medium',
      status: 'Completed',
      issuedDate: '2025-08-15',
      description: 'Replacement of faulty door handle springs'
    }
  ];

  return (
    <SpaceBetween size="l">
      {/* Warranty Tiles */}
      <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
        <Container header={<Header variant="h2">Active Claims</Header>}>
          <Box variant="h1" color="text-status-info">8</Box>
          <Box variant="small" color="text-body-secondary">3 approved, 2 under review, 3 processing</Box>
        </Container>

        <Container header={<Header variant="h2">Warranty Coverage</Header>}>
          <Box variant="h1" color="text-status-success">94%</Box>
          <Box variant="small" color="text-body-secondary">Fleet average coverage</Box>
          <Box variant="small" color="text-body-secondary">2 vehicles expiring soon</Box>
        </Container>

        <Container header={<Header variant="h2">Claims This Month</Header>}>
          <Box variant="h1" color="text-status-warning">$45,200</Box>
          <Box variant="small" color="text-status-error">↑ 12% from last month</Box>
          <Box variant="small" color="text-body-secondary">Average: $38,500</Box>
        </Container>

        <Container header={<Header variant="h2">Active Recalls</Header>}>
          <Box variant="h1" color="text-status-error">2</Box>
          <Box variant="small" color="text-body-secondary">18 vehicles affected</Box>
          <Box variant="small" color="text-body-secondary">1 high priority, 1 completed</Box>
        </Container>
      </Grid>

      {/* Critical Recall Alert */}
      <Alert type="error" header="Critical Recall Notice">
        Recall RC-2025-EV-001 affects 15 vehicles in your fleet. Battery Management System software update required immediately.
      </Alert>

      {/* Warranty Expiration Alerts */}
      <Container header={<Header variant="h2">Warranty Expiration Alerts</Header>}>
        <SpaceBetween size="s">
          <Box>• VIN789012345 - Comprehensive warranty expires in 3 months (Dec 24, 2025)</Box>
          <Box>• VIN345678901 - Powertrain warranty expires in 6 months (Mar 15, 2026)</Box>
          <Box>• Consider extended warranty options for vehicles nearing expiration</Box>
        </SpaceBetween>
      </Container>

      {/* Warranty Tabs */}
      <Tabs
        activeTabId={activeTabId}
        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
        tabs={[
          {
            id: 'active-claims',
            label: 'Active Claims',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'claimId',
                    header: 'Claim ID',
                    cell: item => item.claimId
                  },
                  {
                    id: 'vehicleVin',
                    header: 'Vehicle VIN',
                    cell: item => item.vehicleVin
                  },
                  {
                    id: 'component',
                    header: 'Component',
                    cell: item => (
                      <Badge
                        color={
                          item.component === 'Battery Pack' ? 'blue' :
                          item.component === 'Motor Controller' ? 'green' : 'grey'
                        }
                      >
                        {item.component}
                      </Badge>
                    )
                  },
                  {
                    id: 'issueDescription',
                    header: 'Issue Description',
                    cell: item => item.issueDescription
                  },
                  {
                    id: 'status',
                    header: 'Status',
                    cell: item => (
                      <StatusIndicator
                        type={
                          item.status === 'Approved' ? 'success' :
                          item.status === 'Under Review' ? 'pending' :
                          item.status === 'Parts Ordered' ? 'info' : 'warning'
                        }
                      >
                        {item.status}
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'filedDate',
                    header: 'Filed Date',
                    cell: item => item.filedDate
                  },
                  {
                    id: 'estimatedResolution',
                    header: 'Est. Resolution',
                    cell: item => item.estimatedResolution
                  }
                ]}
                items={activeClaims}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No active claims</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      No warranty claims currently active.
                    </Box>
                  </Box>
                }
              />
            )
          },
          {
            id: 'warranty-status',
            label: 'Warranty Status',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'vehicleVin',
                    header: 'Vehicle VIN',
                    cell: item => item.vehicleVin
                  },
                  {
                    id: 'model',
                    header: 'Model',
                    cell: item => item.model
                  },
                  {
                    id: 'warrantyType',
                    header: 'Warranty Type',
                    cell: item => (
                      <Badge
                        color={
                          item.warrantyType === 'Comprehensive' ? 'blue' :
                          item.warrantyType === 'Extended' ? 'green' : 'grey'
                        }
                      >
                        {item.warrantyType}
                      </Badge>
                    )
                  },
                  {
                    id: 'endDate',
                    header: 'Expiration Date',
                    cell: item => item.endDate
                  },
                  {
                    id: 'remainingMonths',
                    header: 'Remaining',
                    cell: item => `${item.remainingMonths} months`
                  },
                  {
                    id: 'coverage',
                    header: 'Coverage',
                    cell: item => (
                      <ProgressBar
                        value={item.coveragePercent}
                        additionalInfo={`${item.coveragePercent}%`}
                        variant={item.coveragePercent >= 90 ? 'success' : item.coveragePercent >= 70 ? undefined : 'error'}
                      />
                    )
                  }
                ]}
                items={warrantyStatus}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No warranty information</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      Warranty status will appear here.
                    </Box>
                  </Box>
                }
              />
            )
          },
          {
            id: 'recalls',
            label: 'Recalls & Service Bulletins',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'recallId',
                    header: 'Recall ID',
                    cell: item => item.recallId
                  },
                  {
                    id: 'component',
                    header: 'Component',
                    cell: item => item.component
                  },
                  {
                    id: 'affectedVehicles',
                    header: 'Affected Vehicles',
                    cell: item => item.affectedVehicles
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
                    id: 'status',
                    header: 'Status',
                    cell: item => (
                      <StatusIndicator
                        type={item.status === 'Completed' ? 'success' : 'warning'}
                      >
                        {item.status}
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'issuedDate',
                    header: 'Issued Date',
                    cell: item => item.issuedDate
                  },
                  {
                    id: 'description',
                    header: 'Description',
                    cell: item => item.description
                  }
                ]}
                items={recalls}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No active recalls</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      No recalls or service bulletins affecting your fleet.
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

export default WarrantyManagementView;
