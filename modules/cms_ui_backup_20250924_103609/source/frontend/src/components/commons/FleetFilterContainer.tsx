// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Container,
  Header,
  SpaceBetween,
  DateRangePicker,
  FormField,
  ColumnLayout,
} from '@cloudscape-design/components';
import { FleetSelector } from './FleetSelector';
import { TimeRangeSelector } from './TimeRangeSelector';

export interface FleetFilterContainerProps {
  selectedFleet?: string;
  onFleetChange?: (fleetId: string) => void;
  dateRange?: any;
  onDateRangeChange?: (range: any) => void;
  selectedTimeRange?: string;
  onTimeRangeChange?: (timeRange: string) => void;
  fleetOptions?: Array<{ label: string; value: string }>;
  timeRangeOptions?: Array<{ label: string; value: string }>;
  showDateRange?: boolean;
  showTimeRange?: boolean;
  title?: string;
}

export const FleetFilterContainer: React.FC<FleetFilterContainerProps> = ({
  selectedFleet,
  onFleetChange,
  dateRange,
  onDateRangeChange,
  selectedTimeRange,
  onTimeRangeChange,
  fleetOptions,
  timeRangeOptions,
  showDateRange = true,
  showTimeRange = false,
  title = 'Filters'
}) => {
  const columnCount = 1 + (showDateRange ? 1 : 0) + (showTimeRange ? 1 : 0);

  return (
    <Container header={<Header variant="h2">{title}</Header>}>
      <SpaceBetween size="m">
        <ColumnLayout columns={columnCount} borders="vertical">
          <FleetSelector
            selectedFleet={selectedFleet}
            onFleetChange={onFleetChange}
            fleetOptions={fleetOptions}
          />
          
          {showTimeRange && (
            <TimeRangeSelector
              selectedTimeRange={selectedTimeRange}
              onTimeRangeChange={onTimeRangeChange}
              timeRangeOptions={timeRangeOptions}
            />
          )}
          
          {showDateRange && (
            <FormField label="Date Range">
              <DateRangePicker
                value={dateRange}
                onChange={({ detail }) => onDateRangeChange?.(detail.value)}
                relativeOptions={[
                  { key: 'previous-5-minutes', amount: 5, unit: 'minute', type: 'relative' },
                  { key: 'previous-30-minutes', amount: 30, unit: 'minute', type: 'relative' },
                  { key: 'previous-1-hour', amount: 1, unit: 'hour', type: 'relative' },
                  { key: 'previous-6-hours', amount: 6, unit: 'hour', type: 'relative' },
                  { key: 'previous-1-day', amount: 1, unit: 'day', type: 'relative' },
                  { key: 'previous-3-days', amount: 3, unit: 'day', type: 'relative' },
                  { key: 'previous-1-week', amount: 1, unit: 'week', type: 'relative' }
                ]}
                isValidRange={range => range?.type === 'absolute' ? range.startDate <= range.endDate : true}
                placeholder="Filter by date range"
              />
            </FormField>
          )}
        </ColumnLayout>
      </SpaceBetween>
    </Container>
  );
};
