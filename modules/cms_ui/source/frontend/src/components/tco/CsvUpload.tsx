// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  Box,
  Button,
  Alert,
  SpaceBetween,
  Table,
} from '@cloudscape-design/components';

const CsvUpload: React.FC = () => {
  return (
    <SpaceBetween size="l">
      <Container header={<Header variant="h2">Upload Cost Data</Header>}>
        <SpaceBetween size="l">
          <Box
            padding="xl"
            textAlign="center"
            fontSize="body-m"
            color="text-body-secondary"
          >
            <div
              style={{
                border: '2px dashed #aab7b8',
                borderRadius: '8px',
                padding: '40px',
              }}
            >
              <SpaceBetween size="xs">
                <Box fontWeight="bold">Drag and drop CSV file here or click to browse</Box>
                <Box color="text-body-secondary" fontSize="body-s">
                  Supported: .csv up to 10 MB
                </Box>
              </SpaceBetween>
            </div>
          </Box>
          <Button variant="primary">Upload CSV</Button>
          <Alert type="info">
            Supported formats: CSV. Max file size: 10MB. Columns: date, vehicle_id, category, amount, description.
          </Alert>
        </SpaceBetween>
      </Container>

      <Container header={<Header variant="h2" counter="(3)">Recent Uploads</Header>}>
        <Table
          items={[
            { file: 'fuel_transactions_march.csv', records: '2,340 records', status: 'Processed', date: 'Mar 24, 2:15 PM' },
            { file: 'maintenance_q1_2026.csv', records: '847 records', status: 'Processed', date: 'Mar 20, 10:30 AM' },
            { file: 'charging_sessions_feb.csv', records: '1,203 records', status: 'Processed', date: 'Mar 15, 9:00 AM' },
          ]}
          columnDefinitions={[
            { id: 'file', header: 'File Name', cell: (item: any) => item.file },
            { id: 'records', header: 'Records', cell: (item: any) => item.records },
            { id: 'status', header: 'Status', cell: (item: any) => item.status },
            { id: 'date', header: 'Uploaded', cell: (item: any) => item.date },
          ]}
        />
      </Container>
    </SpaceBetween>
  );
};

export default CsvUpload;
