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
    { label: 'Ford Dealership - Downtown', value: 'ford-downtown', address: '456 Main St, Austin, TX', phone: '(512) 555-0456' },
    { label: 'GM Service Center - North', value: 'gm-north', address: '789 Highway 35, Austin, TX', phone: '(512) 555-0789' }
  ];

  const scheduledAppointments = [
    {
      vin: 'VIN123456789',
      serviceType: 'Mobile',
      repairType: 'PM Service',
      expectedCompletion: '2025-09-24 14:00',
      percentComplete: 75
    },
    {
      vin: 'VIN987654321',
      serviceType: 'Service Center',
      repairType: 'Brake Repair',
      expectedCompletion: '2025-09-25 10:30',
      percentComplete: 25
    },
    {
      vin: 'VIN456789123',
      serviceType: 'Roadside Assistance',
      repairType: 'Tire Replacement',
      expectedCompletion: '2025-09-24 16:00',
      percentComplete: 100
    }
  ];

  const recommendedServices = [
    {
      vin: 'VIN111222333',
      serviceType: 'Service Center',
      repairType: 'Oil Change',
      dueDate: '2025-09-30',
      priority: 'Medium'
    },
    {
      vin: 'VIN444555666',
      serviceType: 'Mobile',
      repairType: 'Brake Inspection',
      dueDate: '2025-09-28',
      priority: 'High'
    }
  ];

  // NHTSA Recall Data
  const activeRecalls = [
    {
      recallId: 'NHTSA-24V-456',
      manufacturer: 'Rivian',
      model: 'R1T',
      modelYear: '2024',
      affectedVins: ['VIN123456789', 'VIN987654321'],
      recallTitle: 'Airbag Control Module Software Update',
      severity: 'High',
      nhtsaNumber: '24V-456',
      dateIssued: '2024-08-15',
      description: 'The airbag control module software may not properly deploy airbags in certain crash scenarios.',
      remedy: 'Dealers will update the airbag control module software, free of charge.',
      estimatedRepairTime: '2 hours',
      pdfUrl: 'https://static.nhtsa.gov/odi/rcl/2024/RCLRPT-24V456-1234.PDF',
      status: 'Open'
    },
    {
      recallId: 'NHTSA-24V-789',
      manufacturer: 'Ford',
      model: 'F-150 Lightning',
      modelYear: '2024',
      affectedVins: ['VIN456789123'],
      recallTitle: 'Battery Pack Thermal Management Issue',
      severity: 'Medium',
      nhtsaNumber: '24V-789',
      dateIssued: '2024-09-01',
      description: 'Battery pack thermal management system may not adequately cool the battery under certain conditions.',
      remedy: 'Dealers will inspect and replace the thermal management system components as necessary.',
      estimatedRepairTime: '4 hours',
      pdfUrl: 'https://static.nhtsa.gov/odi/rcl/2024/RCLRPT-24V789-5678.PDF',
      status: 'Open'
    },
    {
      recallId: 'NHTSA-24V-321',
      manufacturer: 'GM',
      model: 'Silverado EV',
      modelYear: '2024',
      affectedVins: ['VIN111222333'],
      recallTitle: 'Charging Port Door Mechanism',
      severity: 'Low',
      nhtsaNumber: '24V-321',
      dateIssued: '2024-07-20',
      description: 'Charging port door may not properly close, potentially allowing water ingress.',
      remedy: 'Dealers will replace the charging port door mechanism.',
      estimatedRepairTime: '1.5 hours',
      pdfUrl: 'https://static.nhtsa.gov/odi/rcl/2024/RCLRPT-24V321-9012.PDF',
      status: 'Scheduled'
    }
  ];

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
          <Box variant="h1" color="text-status-info">5</Box>
          <Box variant="small" color="text-body-secondary">Today - September 24, 2025</Box>
        </Container>

        <Container header={<Header variant="h2">Service Expenses</Header>}>
          <Box variant="h1" color="text-status-info">$12,450</Box>
          <Box variant="small" color="text-status-success">↓ 9% from monthly average</Box>
          <Box variant="small" color="text-body-secondary">This month</Box>
        </Container>

        <Container header={<Header variant="h2">On-Time PM Compliance</Header>}>
          <Box variant="h1" color="text-status-success">92%</Box>
          <Box variant="small" color="text-body-secondary">2 vehicles - Today</Box>
        </Container>

        <Container header={<Header variant="h2">Active Recalls</Header>}>
          <Box variant="h1" color="text-status-error">3</Box>
          <Box variant="small" color="text-body-secondary">Affecting 5 vehicles</Box>
          <Box variant="small" color="text-status-warning">1 high priority, 2 scheduled</Box>
        </Container>
      </Grid>

      {/* Critical Recall Alert */}
      <Alert type="error" header="Critical Safety Recall - Immediate Action Required">
        NHTSA Recall 24V-456 affects 2 Rivian R1T vehicles in your fleet. Airbag control module software update required immediately for safety compliance.
        <Box margin={{ top: 's' }}>
          <Button variant="primary" onClick={() => handleDownloadRecallPDF(activeRecalls[0])}>
            Download NHTSA PDF
          </Button>
        </Box>
      </Alert>

      {/* Upcoming Service Today */}
      <Container header={<Header variant="h2">Upcoming Service (Today)</Header>}>
        <SpaceBetween size="s">
          <Box>• Mobile Service - VIN123456789 at 2:00 PM</Box>
          <Box>• Drop off VIN987654321 at Service Center by 9:00 AM</Box>
          <Box>• Roadside Assistance - VIN456789123 at 4:00 PM</Box>
          <Box>• <strong>Recall Service:</strong> VIN111222333 - GM Charging Port Repair at 10:00 AM</Box>
        </SpaceBetween>
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
