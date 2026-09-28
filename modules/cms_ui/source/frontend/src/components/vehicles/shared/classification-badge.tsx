// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

/**
 * ClassificationBadge — renders a vehicle's onboard/offboard/unknown
 * classification as a Cloudscape Badge.
 *
 * Props:
 *  classification: 'onboard' | 'offboard' | 'unknown' | undefined
 *
 * Spec: 2026-08-29-cms-vehicle-classification § D7
 * Classification is authoritative on the backend — never re-derived here.
 */

import React from 'react';
import { Badge } from '@cloudscape-design/components';

export type VehicleClassification = 'onboard' | 'offboard' | 'unknown';

export interface ClassificationBadgeProps {
  // S1: narrowed from `VehicleClassification | string` — the `default` branch
  // covers unrecognised values at runtime; `| string` only defeated type-safety.
  classification?: VehicleClassification | undefined;
}

/**
 * ClassificationBadge
 *
 * onboard  → green badge  — "Onboard"   (valid Cloudscape Badge color)
 * offboard → red badge    — "Offboard"  (valid Cloudscape Badge color)
 * unknown  → blue badge   — "Unknown"   (valid Cloudscape Badge color)
 * missing  → blue badge   — "Unknown"   (default branch)
 *
 * Valid Badge colors: 'blue' | 'grey' | 'green' | 'red' |
 *   'severity-critical' | 'severity-high' | 'severity-medium' |
 *   'severity-low' | 'severity-neutral'
 * (verified from @cloudscape-design/components/badge/interfaces.d.ts)
 *
 * NOTE: "success" and "stopped" are StatusIndicator values, NOT Badge colors.
 * Using them causes Cloudscape to fall back to grey, making badges
 * visually indistinguishable (C2 fix — 2026-08-29).
 */
export const ClassificationBadge: React.FC<ClassificationBadgeProps> = ({
  classification,
}) => {
  switch (classification) {
    case 'onboard':
      // green = positive/active (matches the intent of 'success')
      return <Badge color="green">Onboard</Badge>;
    case 'offboard':
      // red = stopped/inactive (matches the intent of 'stopped')
      return <Badge color="red">Offboard</Badge>;
    case 'unknown':
    default:
      // blue = informational
      return <Badge color="blue">Unknown</Badge>;
  }
};

export default ClassificationBadge;
