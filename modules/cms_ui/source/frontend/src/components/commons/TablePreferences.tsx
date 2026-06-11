// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import { 
  CollectionPreferences,
  Modal,
  Box,
  SpaceBetween,
  Button,
  FormField,
  Select
} from '@cloudscape-design/components';
import { CustomColumnVisibility, ColumnOption } from './CustomColumnVisibility';

export interface TablePreferencesProps {
  preferences: {
    pageSize: number;
    visibleContent?: string[];
  };
  onConfirm: (preferences: any) => void;
  pageSizeOptions?: Array<{ value: number; label: string }>;
  visibleContentOptions?: Array<{ id: string; label: string; editable?: boolean }>;
  resourceName?: string;
}

export const DEFAULT_PAGE_SIZE_OPTIONS = [
  { value: 25, label: '25 items' },
  { value: 50, label: '50 items' },
  { value: 100, label: '100 items' },
];

export function TablePreferences({
  preferences,
  onConfirm,
  pageSizeOptions = DEFAULT_PAGE_SIZE_OPTIONS,
  visibleContentOptions = [],
  resourceName = 'items'
}: TablePreferencesProps) {
  const [isModalOpen, setIsModalOpen] = useState(false);
  const [tempPageSize, setTempPageSize] = useState(preferences?.pageSize || 25);
  const [tempVisibleContent, setTempVisibleContent] = useState(
    preferences?.visibleContent || visibleContentOptions.map(option => option.id)
  );

  // If no visible content options, use CloudScape's built-in preferences (page size only)
  if (!visibleContentOptions || visibleContentOptions.length === 0) {
    const preferencesConfig = {
      title: "Preferences",
      confirmLabel: "Confirm",
      cancelLabel: "Cancel",
      preferences: {
        pageSize: preferences?.pageSize || 25,
      },
      onConfirm: ({ detail }) => onConfirm(detail),
      pageSizePreference: {
        title: 'Page size',
        options: pageSizeOptions.map(option => ({
          ...option,
          label: option.label.replace('items', resourceName)
        })),
      }
    };

    return <CollectionPreferences {...preferencesConfig} />;
  }

  // Custom modal implementation for tables with column visibility
  const handleConfirm = () => {
    onConfirm({
      pageSize: tempPageSize,
      visibleContent: tempVisibleContent,
    });
    setIsModalOpen(false);
  };

  const handleCancel = () => {
    // Reset temporary values
    setTempPageSize(preferences?.pageSize || 25);
    setTempVisibleContent(preferences?.visibleContent || visibleContentOptions.map(option => option.id));
    setIsModalOpen(false);
  };

  return (
    <>
      <Button
        variant="icon"
        iconName="settings"
        ariaLabel="Preferences"
        onClick={() => setIsModalOpen(true)}
        className="custom-preferences-button"
      />
      
      <Modal
        visible={isModalOpen}
        onDismiss={handleCancel}
        header="Preferences"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={handleCancel}>
                Cancel
              </Button>
              <Button variant="primary" onClick={handleConfirm}>
                Confirm
              </Button>
            </SpaceBetween>
          </Box>
        }
      >
        <SpaceBetween direction="vertical" size="l">
          <FormField label="Page size">
            <Select
              selectedOption={{ 
                value: tempPageSize, 
                label: `${tempPageSize} ${resourceName}` 
              }}
              onChange={({ detail }) => setTempPageSize(detail.selectedOption.value as number)}
              options={pageSizeOptions.map(option => ({
                ...option,
                label: option.label.replace('items', resourceName)
              }))}
            />
          </FormField>

          <CustomColumnVisibility
            options={visibleContentOptions as ColumnOption[]}
            selectedColumns={tempVisibleContent}
            onChange={setTempVisibleContent}
          />
        </SpaceBetween>
      </Modal>
    </>
  );
}

// Common column definitions for different table types
export const VEHICLE_COLUMNS = [
  { id: 'name', label: 'Vehicle Name', editable: false },
  { id: 'vin', label: 'VIN' },
  { id: 'make', label: 'Make' },
  { id: 'model', label: 'Model' },
  { id: 'year', label: 'Year' },
  { id: 'licensePlate', label: 'License Plate' },
  { id: 'status', label: 'Status' },
  { id: 'fleetName', label: 'Fleet' },
  { id: 'source', label: 'Source' },
];

export const FLEET_COLUMNS = [
  { id: 'name', label: 'Fleet Name', editable: false },
  { id: 'totalVehicles', label: 'Total Vehicles' },
  { id: 'description', label: 'Description' },
  { id: 'status', label: 'Status' },
  { id: 'createdAt', label: 'Created' },
];

export const SAFETY_ALERT_COLUMNS = [
  { id: 'timestamp', label: 'Time', editable: false },
  { id: 'vehicleId', label: 'Vehicle ID' },
  { id: 'vin', label: 'VIN' },
  { id: 'eventType', label: 'Event Type' },
  { id: 'severity', label: 'Severity' },
  { id: 'location', label: 'Location' },
  { id: 'fleetName', label: 'Fleet' },
  { id: 'driverScore', label: 'Driver Score' },
  { id: 'resolved', label: 'Status' },
];

export const MAINTENANCE_ALERT_COLUMNS = [
  { id: 'timestamp', label: 'Time', editable: false },
  { id: 'vehicleId', label: 'Vehicle ID' },
  { id: 'vin', label: 'VIN' },
  { id: 'alertType', label: 'Alert Type' },
  { id: 'severity', label: 'Severity' },
  { id: 'description', label: 'Description' },
  { id: 'mileage', label: 'Mileage' },
  { id: 'fleetName', label: 'Fleet' },
  { id: 'resolved', label: 'Status' },
];
