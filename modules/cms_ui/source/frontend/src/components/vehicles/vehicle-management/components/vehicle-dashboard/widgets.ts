// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { StoredWidgetPlacement } from './interfaces';

export function getDefaultLayout(width: number): ReadonlyArray<StoredWidgetPlacement> {
  if (width >= 2160) {
    // 6-col layout
    return [
      { id: 'vehicle-status', columnOffset: 0, columnSpan: 2, rowSpan: 6 },
      { id: 'telemetry-chart', columnOffset: 2, columnSpan: 4, rowSpan: 6 },
      { id: 'safety-events', columnOffset: 0, columnSpan: 3, rowSpan: 4 },
      { id: 'maintenance-alerts', columnOffset: 3, columnSpan: 3, rowSpan: 4 },
    ];
  } else if (width >= 1440) {
    // 4-col layout
    return [
      { id: 'vehicle-status', columnOffset: 0, columnSpan: 2, rowSpan: 6 },
      { id: 'telemetry-chart', columnOffset: 2, columnSpan: 2, rowSpan: 6 },
      { id: 'safety-events', columnOffset: 0, columnSpan: 2, rowSpan: 4 },
      { id: 'maintenance-alerts', columnOffset: 2, columnSpan: 2, rowSpan: 4 },
    ];
  } else {
    // 2-col layout
    return [
      { id: 'vehicle-status', columnOffset: 0, columnSpan: 2, rowSpan: 6 },
      { id: 'telemetry-chart', columnOffset: 0, columnSpan: 2, rowSpan: 6 },
      { id: 'safety-events', columnOffset: 0, columnSpan: 2, rowSpan: 4 },
      { id: 'maintenance-alerts', columnOffset: 0, columnSpan: 2, rowSpan: 4 },
    ];
  }
}
