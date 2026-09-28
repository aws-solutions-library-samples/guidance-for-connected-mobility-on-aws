// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from 'react';
import {
  Box,
  SpaceBetween,
  Badge,
  Popover,
  Button,
  ColumnLayout
} from '@cloudscape-design/components';

interface EnrollmentStatusProps {
  enrollmentStatus: string;
  enrolledAt?: string;
  activatedAt?: string;
  lastSeenAt?: string;
  lastConnectedAt?: string;
}

const ENROLLMENT_STATUS_CONFIG = {
  NOT_ENROLLED: {
    color: 'grey' as const,
    label: 'Not Enrolled',
    description: 'Vehicle created in system but no certificate issued. Cannot receive telemetry.'
  },
  PENDING_ACTIVATION: {
    color: 'blue' as const,
    label: 'Pending Activation',
    description: 'Certificate issued and waiting for first telemetry from vehicle.'
  },
  ENROLLED: {
    color: 'blue' as const,
    label: 'Enrolled',
    description: 'OEM/external platform confirmed enrollment. Ready to receive telemetry.'
  },
  ACTIVE: {
    color: 'green' as const,
    label: 'Active',
    description: 'Vehicle is actively sending telemetry data.'
  },
  INACTIVE: {
    color: 'red' as const,
    label: 'Inactive',
    description: 'Vehicle has been deactivated by administrator. Not accepting telemetry.'
  }
};

const VEHICLE_STATUS_CONFIG = {
  UNKNOWN: {
    color: 'grey' as const,
    label: 'Unknown',
    description: 'No telemetry received yet or vehicle state cannot be determined.'
  },
  PARKED: {
    color: 'grey' as const,
    label: 'Parked',
    description: 'Vehicle is stationary with ignition off.'
  },
  DRIVING: {
    color: 'green' as const,
    label: 'Driving',
    description: 'Vehicle is in motion with ignition on.'
  },
  IDLE: {
    color: 'blue' as const,
    label: 'Idle',
    description: 'Vehicle is stationary with ignition on.'
  },
  CHARGING: {
    color: 'blue' as const,
    label: 'Charging',
    description: 'Electric vehicle is currently charging.'
  },
  MAINTENANCE: {
    color: 'grey' as const,
    label: 'Maintenance',
    description: 'Vehicle is in service or maintenance mode.'
  },
  OFFLINE: {
    color: 'red' as const,
    label: 'Offline',
    description: 'No telemetry received for more than 24 hours.'
  }
};

export const EnrollmentStatusSection: React.FC<EnrollmentStatusProps> = ({
  enrollmentStatus,
  enrolledAt,
  activatedAt,
  lastSeenAt,
  lastConnectedAt
}) => {
  const statusConfig = ENROLLMENT_STATUS_CONFIG[enrollmentStatus as keyof typeof ENROLLMENT_STATUS_CONFIG] || {
    color: 'grey' as const,
    label: enrollmentStatus || 'Unknown',
    description: 'Unknown enrollment status'
  };

  const formatDate = (dateString?: string) => {
    if (!dateString) return 'N/A';
    try {
      return new Date(dateString).toLocaleString();
    } catch {
      return dateString;
    }
  };

  return (
    <ColumnLayout columns={4} variant="text-grid">
      <SpaceBetween direction="vertical" size="xs">
        <SpaceBetween direction="horizontal" size="xs">
          <Box variant="awsui-key-label">Enrollment Status</Box>
          <Popover
            size="medium"
            position="top"
            triggerType="custom"
            dismissButton={false}
            content={
              <SpaceBetween size="xs">
                <Box variant="p">{statusConfig.description}</Box>
                <Box variant="h4">Status Flow:</Box>
                <Box variant="small">
                  <strong>NOT_ENROLLED</strong> → Vehicle created, no certificate
                  <br />
                  <strong>PENDING_ACTIVATION</strong> → Certificate issued, waiting for telemetry
                  <br />
                  <strong>ENROLLED</strong> → OEM platform confirmed enrollment
                  <br />
                  <strong>ACTIVE</strong> → Telemetry flowing
                  <br />
                  <strong>INACTIVE</strong> → Deactivated by admin
                </Box>
              </SpaceBetween>
            }
          >
            <Button variant="inline-icon" iconName="status-info" />
          </Popover>
        </SpaceBetween>
        <Badge color={statusConfig.color}>
          {statusConfig.label}
        </Badge>
      </SpaceBetween>

      <SpaceBetween direction="vertical" size="xs">
        <SpaceBetween direction="horizontal" size="xs">
          <Box variant="awsui-key-label">Enrolled At</Box>
          <Popover
            size="small"
            position="top"
            triggerType="custom"
            dismissButton={false}
            content="Timestamp when vehicle certificate was issued and enrollment initiated"
          >
            <Button variant="inline-icon" iconName="status-info" />
          </Popover>
        </SpaceBetween>
        <div>{formatDate(enrolledAt)}</div>
      </SpaceBetween>

      <SpaceBetween direction="vertical" size="xs">
        <SpaceBetween direction="horizontal" size="xs">
          <Box variant="awsui-key-label">Activated At</Box>
          <Popover
            size="small"
            position="top"
            triggerType="custom"
            dismissButton={false}
            content="Timestamp when first telemetry was received from vehicle"
          >
            <Button variant="inline-icon" iconName="status-info" />
          </Popover>
        </SpaceBetween>
        <div>{formatDate(activatedAt)}</div>
      </SpaceBetween>

      <SpaceBetween direction="vertical" size="xs">
        <SpaceBetween direction="horizontal" size="xs">
          <Box variant="awsui-key-label">Last Seen</Box>
          <Popover
            size="small"
            position="top"
            triggerType="custom"
            dismissButton={false}
            content="Timestamp of most recent telemetry message received"
          >
            <Button variant="inline-icon" iconName="status-info" />
          </Popover>
        </SpaceBetween>
        <div>{formatDate(lastConnectedAt || lastSeenAt)}</div>
      </SpaceBetween>
    </ColumnLayout>
  );
};

export const VehicleStatusBadge: React.FC<{ vehicleStatus: string }> = ({ vehicleStatus }) => {
  const statusConfig = VEHICLE_STATUS_CONFIG[vehicleStatus as keyof typeof VEHICLE_STATUS_CONFIG] || {
    color: 'grey' as const,
    label: vehicleStatus || 'Unknown',
    description: 'Unknown vehicle operational status'
  };

  return (
    <SpaceBetween direction="vertical" size="xs">
      <SpaceBetween direction="horizontal" size="xs">
        <Box variant="awsui-key-label">Vehicle Status</Box>
        <Popover
          size="medium"
          position="top"
          triggerType="custom"
          dismissButton={false}
          content={
            <SpaceBetween size="xs">
              <Box variant="p">{statusConfig.description}</Box>
              <Box variant="h4">Operational States:</Box>
              <Box variant="small">
                <strong>UNKNOWN</strong> - No telemetry or state unknown
                <br />
                <strong>PARKED</strong> - Stationary, ignition off
                <br />
                <strong>DRIVING</strong> - In motion, ignition on
                <br />
                <strong>IDLE</strong> - Stationary, ignition on
                <br />
                <strong>CHARGING</strong> - EV charging
                <br />
                <strong>MAINTENANCE</strong> - Service mode
                <br />
                <strong>OFFLINE</strong> - No telemetry &gt; 24 hours
              </Box>
            </SpaceBetween>
          }
        >
          <Button variant="inline-icon" iconName="status-info" />
        </Popover>
      </SpaceBetween>
      <Badge color={statusConfig.color}>
        {statusConfig.label}
      </Badge>
    </SpaceBetween>
  );
};
