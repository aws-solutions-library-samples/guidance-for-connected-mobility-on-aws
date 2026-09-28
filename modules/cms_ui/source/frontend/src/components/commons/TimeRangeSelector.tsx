// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import { Select, FormField } from '@cloudscape-design/components';

export interface TimeRangeSelectorProps {
  selectedTimeRange?: string;
  onTimeRangeChange?: (timeRange: string) => void;
  timeRangeOptions?: Array<{ label: string; value: string }>;
  label?: string;
}

const defaultTimeRangeOptions = [
  { label: 'Last 5 minutes', value: '5m' },
  { label: 'Last 30 minutes', value: '30m' },
  { label: 'Last 1 hour', value: '1h' },
  { label: 'Last 6 hours', value: '6h' },
  { label: 'Last 24 hours', value: '24h' },
  { label: 'Last 3 days', value: '3d' },
  { label: 'Last 7 days', value: '7d' }
];

export const TimeRangeSelector: React.FC<TimeRangeSelectorProps> = ({
  selectedTimeRange = '24h',
  onTimeRangeChange,
  timeRangeOptions = defaultTimeRangeOptions,
  label = 'Time Range'
}) => {
  return (
    <FormField label={label}>
      <Select
        selectedOption={timeRangeOptions.find(option => option.value === selectedTimeRange)}
        onChange={({ detail }) => onTimeRangeChange?.(detail.selectedOption.value!)}
        options={timeRangeOptions}
        placeholder="Select time range"
      />
    </FormField>
  );
};
