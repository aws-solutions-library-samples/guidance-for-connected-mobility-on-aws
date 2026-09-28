// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { 
  FormField, 
  Checkbox, 
  SpaceBetween,
  ColumnLayout 
} from '@cloudscape-design/components';

export interface ColumnOption {
  id: string;
  label: string;
  editable?: boolean;
}

export interface CustomColumnVisibilityProps {
  options: ColumnOption[];
  selectedColumns: string[];
  onChange: (selectedColumns: string[]) => void;
}

export function CustomColumnVisibility({
  options,
  selectedColumns,
  onChange
}: CustomColumnVisibilityProps) {
  const handleColumnToggle = (columnId: string, checked: boolean) => {
    if (checked) {
      // Add column if not already selected
      if (!selectedColumns.includes(columnId)) {
        onChange([...selectedColumns, columnId]);
      }
    } else {
      // Remove column if currently selected
      onChange(selectedColumns.filter(id => id !== columnId));
    }
  };

  return (
    <FormField label="Select visible columns">
      <ColumnLayout columns={2} variant="text-grid">
        <SpaceBetween direction="vertical" size="xs">
          {options.slice(0, Math.ceil(options.length / 2)).map((option) => (
            <Checkbox
              key={option.id}
              checked={selectedColumns.includes(option.id)}
              disabled={option.editable === false}
              onChange={({ detail }) => handleColumnToggle(option.id, detail.checked)}
            >
              {option.label}
            </Checkbox>
          ))}
        </SpaceBetween>
        <SpaceBetween direction="vertical" size="xs">
          {options.slice(Math.ceil(options.length / 2)).map((option) => (
            <Checkbox
              key={option.id}
              checked={selectedColumns.includes(option.id)}
              disabled={option.editable === false}
              onChange={({ detail }) => handleColumnToggle(option.id, detail.checked)}
            >
              {option.label}
            </Checkbox>
          ))}
        </SpaceBetween>
      </ColumnLayout>
    </FormField>
  );
}
