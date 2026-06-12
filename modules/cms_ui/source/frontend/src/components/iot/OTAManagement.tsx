// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import {
  Container,
  Header,
  Table,
  Button,
  StatusIndicator,
  Badge,
  SpaceBetween,
  Box,
  Modal,
  Form,
  FormField,
  Select,
  Multiselect,
  DatePicker,
  Textarea,
  ProgressBar,
  Alert
} from '@cloudscape-design/components';

const OTAManagement: React.FC = () => {
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [selectedVehicles, setSelectedVehicles] = useState<any[]>([]);
  const [selectedUpdate, setSelectedUpdate] = useState<any>(null);
  const [scheduledDate, setScheduledDate] = useState('');
  const [notes, setNotes] = useState('');

  const availableUpdates = [
    { label: 'Infotainment System v3.2.1 - Security patches and UI improvements', value: 'infotainment-3.2.1' },
    { label: 'Battery Management v2.1.0 - Enhanced thermal management', value: 'battery-2.1.0' },
    { label: 'Autopilot v4.5.2 - Improved lane detection', value: 'autopilot-4.5.2' },
    { label: 'Charging System v1.8.3 - Faster DC charging support', value: 'charging-1.8.3' }
  ];

  const vehicleOptions = [
    { label: 'VIN123456789 - Rivian R1T (v2.4.1)', value: 'VIN123456789' },
    { label: 'VIN987654321 - Ford F-150 Lightning (v2.3.8)', value: 'VIN987654321' },
    { label: 'VIN456789123 - GM Silverado EV (v1.9.2)', value: 'VIN456789123' },
    { label: 'VIN111222333 - Tesla Model Y (v2024.20.9)', value: 'VIN111222333' }
  ];

  const otaUpdates = [
    {
      updateId: 'OTA-2025-001',
      vehicleVin: 'VIN123456789',
      vehicleModel: 'Rivian R1T',
      currentVersion: 'v2.4.1',
      targetVersion: 'v2.5.0',
      updateType: 'Infotainment System',
      status: 'In Progress',
      progress: 65,
      scheduledDate: '2025-09-24 02:00',
      estimatedCompletion: '2025-09-24 03:30',
      priority: 'Medium'
    },
    {
      updateId: 'OTA-2025-002',
      vehicleVin: 'VIN987654321',
      vehicleModel: 'Ford F-150 Lightning',
      currentVersion: 'v2.3.8',
      targetVersion: 'v2.4.0',
      updateType: 'Battery Management',
      status: 'Scheduled',
      progress: 0,
      scheduledDate: '2025-09-25 01:00',
      estimatedCompletion: '2025-09-25 02:15',
      priority: 'High'
    },
    {
      updateId: 'OTA-2025-003',
      vehicleVin: 'VIN456789123',
      vehicleModel: 'GM Silverado EV',
      currentVersion: 'v1.9.2',
      targetVersion: 'v2.0.0',
      updateType: 'Full System Update',
      status: 'Completed',
      progress: 100,
      scheduledDate: '2025-09-23 03:00',
      estimatedCompletion: '2025-09-23 05:45',
      priority: 'High'
    }
  ];

  const handleScheduleOTA = () => {
    console.log('Scheduling OTA update:', {
      vehicles: selectedVehicles,
      update: selectedUpdate,
      date: scheduledDate,
      notes: notes
    });
    setShowScheduleModal(false);
    setSelectedVehicles([]);
    setSelectedUpdate(null);
    setScheduledDate('');
    setNotes('');
  };

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Manage over-the-air software updates for fleet vehicles"
          actions={
            <Button variant="primary" onClick={() => setShowScheduleModal(true)}>
              Schedule OTA Update
            </Button>
          }
        >
          OTA Update Management
        </Header>
      }
    >
      <SpaceBetween size="l">
        <Alert type="info" header="OTA Update Best Practices">
          Schedule updates during off-peak hours (1-4 AM) when vehicles are parked and charging. Critical security updates should be prioritized.
        </Alert>

        <Table
          columnDefinitions={[
            {
              id: 'updateId',
              header: 'Update ID',
              cell: item => item.updateId
            },
            {
              id: 'vehicle',
              header: 'Vehicle',
              cell: item => (
                <div>
                  <div>{item.vehicleVin}</div>
                  <div style={{ fontSize: '12px', color: '#666' }}>{item.vehicleModel}</div>
                </div>
              )
            },
            {
              id: 'versions',
              header: 'Version Update',
              cell: item => (
                <div>
                  <div>{item.currentVersion} → {item.targetVersion}</div>
                  <div style={{ fontSize: '12px', color: '#666' }}>{item.updateType}</div>
                </div>
              )
            },
            {
              id: 'status',
              header: 'Status',
              cell: item => (
                <StatusIndicator
                  type={
                    item.status === 'Completed' ? 'success' :
                    item.status === 'In Progress' ? 'in-progress' :
                    item.status === 'Scheduled' ? 'pending' : 'error'
                  }
                >
                  {item.status}
                </StatusIndicator>
              )
            },
            {
              id: 'progress',
              header: 'Progress',
              cell: item => (
                <ProgressBar
                  value={item.progress}
                  additionalInfo={`${item.progress}%`}
                  variant={item.progress === 100 ? 'success' : undefined}
                />
              )
            },
            {
              id: 'priority',
              header: 'Priority',
              cell: item => (
                <Badge
                  color={
                    item.priority === 'High' ? 'red' :
                    item.priority === 'Medium' ? 'blue' : 'grey'
                  }
                >
                  {item.priority}
                </Badge>
              )
            },
            {
              id: 'scheduledDate',
              header: 'Scheduled Time',
              cell: item => item.scheduledDate
            },
            {
              id: 'estimatedCompletion',
              header: 'Est. Completion',
              cell: item => item.estimatedCompletion
            }
          ]}
          items={otaUpdates}
          empty={
            <Box textAlign="center" color="inherit">
              <b>No OTA updates</b>
              <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                No over-the-air updates scheduled or in progress.
              </Box>
            </Box>
          }
        />

        {/* Schedule OTA Modal */}
        <Modal
          visible={showScheduleModal}
          onDismiss={() => setShowScheduleModal(false)}
          header="Schedule OTA Update"
          footer={
            <Box float="right">
              <SpaceBetween direction="horizontal" size="xs">
                <Button variant="link" onClick={() => setShowScheduleModal(false)}>
                  Cancel
                </Button>
                <Button 
                  variant="primary" 
                  onClick={handleScheduleOTA}
                  disabled={!selectedUpdate || selectedVehicles.length === 0 || !scheduledDate}
                >
                  Schedule Update
                </Button>
              </SpaceBetween>
            </Box>
          }
        >
          <Form>
            <SpaceBetween size="m">
              <FormField label="Select Update Package" constraintText="Choose the software update to deploy">
                <Select
                  selectedOption={selectedUpdate}
                  onChange={({ detail }) => setSelectedUpdate(detail.selectedOption)}
                  options={availableUpdates}
                  placeholder="Choose update package..."
                />
              </FormField>

              <FormField label="Select Vehicles" constraintText="Choose which vehicles to update">
                <Multiselect
                  selectedOptions={selectedVehicles}
                  onChange={({ detail }) => setSelectedVehicles(detail.selectedOptions)}
                  options={vehicleOptions}
                  placeholder="Select vehicles..."
                />
              </FormField>

              <FormField label="Schedule Date & Time" constraintText="Updates should be scheduled during off-peak hours (1-4 AM)">
                <DatePicker
                  value={scheduledDate}
                  onChange={({ detail }) => setScheduledDate(detail.value)}
                  placeholder="YYYY-MM-DD"
                />
              </FormField>

              <FormField label="Update Notes" constraintText="Any special instructions or requirements">
                <Textarea
                  value={notes}
                  onChange={({ detail }) => setNotes(detail.value)}
                  placeholder="Enter any special instructions, rollback conditions, or monitoring requirements..."
                  rows={3}
                />
              </FormField>
            </SpaceBetween>
          </Form>
        </Modal>
      </SpaceBetween>
    </Container>
  );
};

export default OTAManagement;
