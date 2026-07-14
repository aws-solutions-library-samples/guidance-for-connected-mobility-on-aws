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

  const activeClaims: { claimId: string; vehicleVin: string; component: string; issueDescription: string; status: string; filedDate: string; estimatedResolution: string }[] = [];

  const warrantyStatus: { vehicleVin: string; model: string; warrantyType: string; startDate: string; endDate: string; remainingMonths: number; coveragePercent: number }[] = [];

  const recalls: { recallId: string; component: string; affectedVehicles: number; severity: string; status: string; issuedDate: string; description: string }[] = [];

  return (
    <SpaceBetween size="l">
      {/* Warranty Tiles */}
      <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
        <Container header={<Header variant="h2">Active Claims</Header>}>
          <Box variant="h1" color="text-body-secondary">—</Box>
          <Box variant="small" color="text-body-secondary">No data available</Box>
        </Container>

        <Container header={<Header variant="h2">Warranty Coverage</Header>}>
          <Box variant="h1" color="text-body-secondary">—</Box>
          <Box variant="small" color="text-body-secondary">No data available</Box>
        </Container>

        <Container header={<Header variant="h2">Claims This Month</Header>}>
          <Box variant="h1" color="text-body-secondary">—</Box>
          <Box variant="small" color="text-body-secondary">No data available</Box>
        </Container>

        <Container header={<Header variant="h2">Active Recalls</Header>}>
          <Box variant="h1" color="text-body-secondary">—</Box>
          <Box variant="small" color="text-body-secondary">No data available</Box>
        </Container>
      </Grid>

      {/* Warranty Expiration Alerts */}
      <Container header={<Header variant="h2">Warranty Expiration Alerts</Header>}>
        <Box textAlign="center" padding="l" color="text-body-secondary">No upcoming warranty expirations</Box>
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
