// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * DataSourceBadge — renders the data-source lineage of a vehicle's
 * most-recent telemetry row as a Cloudscape Badge.
 *
 * Props:
 *   dataSourceRoute?: string  — the source-lineage discriminator. Accepts
 *   either a row-level `dataSourceRoute` (from the most-recent canonical
 *   telemetry row) OR a vehicle-level `dataSource` (from the vehicle
 *   record) — the row-level value is more granular, but the vehicle-level
 *   is stable for a vehicle without any recent telemetry.
 *
 * Label mapping:
 *   "cs-meridian"          → "Connected Services (Meridian)"   [blue]
 *   "cloud-telemetry"      → "OEM (Cloud)"                     [green]
 *   "vehicle-telemetry"    → "Onboard FWE"                     [green]
 *   "onboard-fwe"          → "Onboard FWE"           [legacy]  [green]
 *   "cloud-oem1"           → "OEM (Cloud)"           [legacy]  [green]
 *   missing / unrecognised → "Unknown"                         [grey]
 *
 * Spec: 2026-09-11-cms-cs-meridian-ingestion § MVP item 8
 */

import React from 'react';
import { Badge } from '@cloudscape-design/components';

export interface DataSourceBadgeProps {
  dataSourceRoute?: string;
}

/**
 * DataSourceBadge — thin renderer, no state, no fetches.
 *
 * cs-meridian                       → blue  — "Connected Services (Meridian)"
 * cloud-telemetry / cloud-oem1      → green — "OEM (Cloud)"
 * vehicle-telemetry / onboard-fwe   → green — "Onboard FWE"
 * missing / unknown                 → grey  — "Unknown"
 */
export const DataSourceBadge: React.FC<DataSourceBadgeProps> = ({
  dataSourceRoute,
}) => {
  switch (dataSourceRoute) {
    case 'cs-meridian':
      return <Badge color="blue">Connected Services (Meridian)</Badge>;
    case 'cloud-telemetry':
    case 'cloud-oem1':
      return <Badge color="green">OEM (Cloud)</Badge>;
    case 'vehicle-telemetry':
    case 'onboard-fwe':
      return <Badge color="green">Onboard FWE</Badge>;
    default:
      return <Badge color="grey">Unknown</Badge>;
  }
};

export default DataSourceBadge;
