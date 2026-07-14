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
  ProgressBar,
  SpaceBetween,
  Badge,
  Button,
  Alert,
  Link,
  Modal,
  Form,
  FormField,
  Select,
  DatePicker,
  Textarea
} from '@cloudscape-design/components';

const ServiceManagementView: React.FC = () => {
  const [activeTabId, setActiveTabId] = useState('scheduled');
  const [showScheduleModal, setShowScheduleModal] = useState(false);
  const [selectedRecall, setSelectedRecall] = useState<any>(null);
  const [selectedDealership, setSelectedDealership] = useState<any>(null);
  const [selectedDate, setSelectedDate] = useState('');
  const [notes, setNotes] = useState('');

  const dealershipOptions = [
    { label: 'Rivian Service Center - Austin', value: 'rivian-austin', address: '123 Electric Ave, Austin, TX', phone: '(512) 555-0123' },
    { label: 'OEM-A Dealership - Downtown', value: 'oem-a-downtown', address: '456 Main St, Austin, TX', phone: '(512) 555-0456' },
    { label: 'GM Service Center - North', value: 'gm-north', address: '789 Highway 35, Austin, TX', phone: '(512) 555-0789' }
  ];

  const scheduledAppointments: { vin: string; serviceType: string; repairType: string; expectedCompletion: string; percentComplete: number }[] = [];

  const recommendedServices: { vin: string; serviceType: string; repairType: string; dueDate: string; priority: string }[] = [];

  // NHTSA Recall Data
  const activeRecalls: { recallId: string; manufacturer: string; model: string; modelYear: string; affectedVins: string[]; recallTitle: string; severity: string; nhtsaNumber: string; dateIssued: string; description: string; remedy: string; estimatedRepairTime: string; pdfUrl: string; status: string }[] = [];

  const handleScheduleRecallService = (recall: any) => {
    setSelectedRecall(recall);
    setShowScheduleModal(true);
  };

  const handleScheduleSubmit = () => {
    // Integration point for scheduling recall service
    console.log('Scheduling recall service:', {
      recall: selectedRecall,
      dealership: selectedDealership,
      date: selectedDate,
      notes: notes
    });
    // This would integrate with your service scheduling system
    setShowScheduleModal(false);
    setSelectedRecall(null);
    setSelectedDealership(null);
    setSelectedDate('');
    setNotes('');
  };

  const handleDownloadRecallPDF = (recall: any) => {
    // Open NHTSA PDF in new tab
    window.open(recall.pdfUrl, '_blank');
  };

  return (
    <SpaceBetween size="l">
      {/* Service Tiles - Updated with Recall tile */}
      <Grid gridDefinition={[{ colspan: 3 }, { colspan: 3 }, { colspan: 3 }, { colspan: 3 }]}>
        <Container header={<Header variant="h2">In Service</Header>}>
          <Box variant="h1" color="text-body-secondary">—</Box>
          <Box variant="small" color="text-body-secondary">No data available</Box>
        </Container>

        <Container header={<Header variant="h2">Service Expenses</Header>}>
          <Box variant="h1" color="text-body-secondary">—</Box>
          <Box variant="small" color="text-body-secondary">No data available</Box>
        </Container>

        <Container header={<Header variant="h2">On-Time PM Compliance</Header>}>
          <Box variant="h1" color="text-body-secondary">—</Box>
          <Box variant="small" color="text-body-secondary">No data available</Box>
        </Container>

        <Container header={<Header variant="h2">Active Recalls</Header>}>
          <Box variant="h1" color="text-body-secondary">{activeRecalls.length || '—'}</Box>
          <Box variant="small" color="text-body-secondary">
            {activeRecalls.length === 0 ? 'No data available' : `Affecting ${activeRecalls.reduce((s, r) => s + r.affectedVins.length, 0)} vehicles`}
          </Box>
        </Container>
      </Grid>

      {/* Upcoming Service Today */}
      <Container header={<Header variant="h2">Upcoming Service (Today)</Header>}>
        <Box textAlign="center" padding="l" color="text-body-secondary">No service scheduled for today</Box>
      </Container>

      {/* Service Tabs - Updated with Recalls tab */}
      <Tabs
        activeTabId={activeTabId}
        onChange={({ detail }) => setActiveTabId(detail.activeTabId)}
        tabs={[
          {
            id: 'scheduled',
            label: 'Scheduled Appointments',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'vin',
                    header: 'VIN',
                    cell: item => item.vin
                  },
                  {
                    id: 'serviceType',
                    header: 'Service Type',
                    cell: item => (
                      <StatusIndicator
                        type={
                          item.serviceType === 'Mobile' ? 'success' :
                          item.serviceType === 'Service Center' ? 'info' :
                          item.serviceType === 'Collision Center' ? 'warning' : 'pending'
                        }
                      >
                        {item.serviceType}
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'repairType',
                    header: 'Repair or PM Services',
                    cell: item => item.repairType
                  },
                  {
                    id: 'expectedCompletion',
                    header: 'Expected Completion',
                    cell: item => item.expectedCompletion
                  },
                  {
                    id: 'percentComplete',
                    header: 'Progress',
                    cell: item => (
                      <ProgressBar
                        value={item.percentComplete}
                        additionalInfo={`${item.percentComplete}%`}
                        variant={item.percentComplete === 100 ? 'success' : undefined}
                      />
                    )
                  }
                ]}
                items={scheduledAppointments}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No scheduled appointments</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      No appointments scheduled for this month.
                    </Box>
                  </Box>
                }
              />
            )
          },
          {
            id: 'recommended',
            label: 'Recommended Services',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'vin',
                    header: 'VIN',
                    cell: item => item.vin
                  },
                  {
                    id: 'serviceType',
                    header: 'Service Type',
                    cell: item => item.serviceType
                  },
                  {
                    id: 'repairType',
                    header: 'Service Needed',
                    cell: item => item.repairType
                  },
                  {
                    id: 'dueDate',
                    header: 'Due Date',
                    cell: item => item.dueDate
                  },
                  {
                    id: 'priority',
                    header: 'Priority',
                    cell: item => (
                      <StatusIndicator
                        type={
                          item.priority === 'High' ? 'error' :
                          item.priority === 'Medium' ? 'warning' : 'success'
                        }
                      >
                        {item.priority}
                      </StatusIndicator>
                    )
                  }
                ]}
                items={recommendedServices}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No recommended services</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      All vehicles are up to date with maintenance.
                    </Box>
                  </Box>
                }
              />
            )
          },
          {
            id: 'recalls',
            label: 'NHTSA Recalls & Safety',
            content: (
              <Table
                columnDefinitions={[
                  {
                    id: 'nhtsaNumber',
                    header: 'NHTSA Recall #',
                    cell: item => (
                      <Badge color="red">
                        {item.nhtsaNumber}
                      </Badge>
                    )
                  },
                  {
                    id: 'manufacturer',
                    header: 'Manufacturer',
                    cell: item => `${item.manufacturer} ${item.model} ${item.modelYear}`
                  },
                  {
                    id: 'recallTitle',
                    header: 'Recall Title',
                    cell: item => item.recallTitle
                  },
                  {
                    id: 'affectedVins',
                    header: 'Affected Vehicles',
                    cell: item => `${item.affectedVins.length} vehicles`
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
                        type={item.status === 'Scheduled' ? 'success' : 'warning'}
                      >
                        {item.status}
                      </StatusIndicator>
                    )
                  },
                  {
                    id: 'estimatedRepairTime',
                    header: 'Est. Repair Time',
                    cell: item => item.estimatedRepairTime
                  },
                  {
                    id: 'actions',
                    header: 'Actions',
                    cell: item => (
                      <SpaceBetween direction="horizontal" size="xs">
                        <Button
                          variant="icon"
                          iconName="download"
                          onClick={() => handleDownloadRecallPDF(item)}
                          ariaLabel="Download NHTSA PDF"
                        />
                        <Button
                          variant="icon"
                          iconName="calendar"
                          onClick={() => handleScheduleRecallService(item)}
                          disabled={item.status === 'Scheduled'}
                          ariaLabel={item.status === 'Scheduled' ? 'Service already scheduled' : 'Schedule recall service'}
                        />
                      </SpaceBetween>
                    )
                  }
                ]}
                items={activeRecalls}
                empty={
                  <Box textAlign="center" color="inherit">
                    <b>No active recalls</b>
                    <Box padding={{ bottom: "s" }} variant="p" color="inherit">
                      No NHTSA recalls affecting your fleet vehicles.
                    </Box>
                  </Box>
                }
              />
            )
          }
        ]}
      />

      {/* Schedule Recall Service Modal */}
      <Modal
        visible={showScheduleModal}
        onDismiss={() => setShowScheduleModal(false)}
        header="Schedule Recall Service"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={() => setShowScheduleModal(false)}>
                Cancel
              </Button>
              <Button 
                variant="primary" 
                onClick={handleScheduleSubmit}
                disabled={!selectedDealership || !selectedDate}
              >
                Schedule Service
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        {selectedRecall && (
          <Form>
            <SpaceBetween size="m">
              <Container>
                <SpaceBetween size="s">
                  <Box variant="h3">Recall Details</Box>
                  <Box><strong>NHTSA #:</strong> {selectedRecall.nhtsaNumber}</Box>
                  <Box><strong>Vehicle:</strong> {selectedRecall.manufacturer} {selectedRecall.model} {selectedRecall.modelYear}</Box>
                  <Box><strong>Issue:</strong> {selectedRecall.recallTitle}</Box>
                  <Box><strong>Estimated Repair Time:</strong> {selectedRecall.estimatedRepairTime}</Box>
                  <Box><strong>Affected VINs:</strong> {selectedRecall.affectedVins.join(', ')}</Box>
                </SpaceBetween>
              </Container>

              <FormField label="Select Dealership" constraintText="Choose an authorized service center">
                <Select
                  selectedOption={selectedDealership}
                  onChange={({ detail }) => setSelectedDealership(detail.selectedOption)}
                  options={dealershipOptions}
                  placeholder="Choose a dealership..."
                />
                {selectedDealership && (
                  <Box margin={{ top: 's' }} color="text-body-secondary">
                    <Box fontSize="body-s">{selectedDealership.address}</Box>
                    <Box fontSize="body-s">{selectedDealership.phone}</Box>
                  </Box>
                )}
              </FormField>

              <FormField label="Preferred Service Date" constraintText="Select your preferred appointment date">
                <DatePicker
                  value={selectedDate}
                  onChange={({ detail }) => setSelectedDate(detail.value)}
                  placeholder="YYYY-MM-DD"
                />
              </FormField>

              <FormField label="Additional Notes" constraintText="Any special instructions or requirements">
                <Textarea
                  value={notes}
                  onChange={({ detail }) => setNotes(detail.value)}
                  placeholder="Enter any special instructions, preferred time, or additional requirements..."
                  rows={3}
                />
              </FormField>
            </SpaceBetween>
          </Form>
        )}
      </Modal>
    </SpaceBetween>
  );
};

export default ServiceManagementView;
