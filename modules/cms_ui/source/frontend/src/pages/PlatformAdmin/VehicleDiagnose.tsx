// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import { VehicleItem, isOEM1Vehicle } from '@/types/fleet-types';
import {
  fetchVehicleState,
  VehicleActionItem,
  VehicleStateResponse,
  VehicleStateError,
} from '@/api/oem1Diagnose';

interface VehicleDiagnoseProps {
  vehicle: VehicleItem & { oem_source?: string };
}

function severityToStatusType(
  severity: VehicleActionItem['severity'],
): 'error' | 'warning' | 'info' {
  switch (severity) {
    case 'critical':
      return 'error';
    case 'warning':
      return 'warning';
    default:
      return 'info';
  }
}

const VehicleDiagnose: React.FC<VehicleDiagnoseProps> = ({ vehicle }) => {
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<VehicleStateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Data-driven gate: only render for vehicles with oem_source === 'oem1'
  if (!isOEM1Vehicle(vehicle)) {
    return null;
  }

  const vehicleId = vehicle.vehicleId ?? vehicle.id ?? '';

  const handleDiagnose = async () => {
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      const data = await fetchVehicleState(vehicleId);
      setResult(data);
    } catch (err) {
      if (err instanceof VehicleStateError) {
        setError(err.message);
      } else {
        setError('Unexpected error running diagnostics.');
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <Box>
      <Button loading={loading} onClick={handleDiagnose} data-testid="diagnose-button">
        Diagnose
      </Button>
      {error && (
        <Box margin={{ top: 'xs' }}>
          <StatusIndicator type="error">{error}</StatusIndicator>
        </Box>
      )}
      {result && result.actionItems.length > 0 && (
        <Box margin={{ top: 's' }}>
          {result.actionItems.map((item, idx) => (
            <Box key={idx} margin={{ bottom: 'xxs' }}>
              <StatusIndicator
                type={severityToStatusType(item.severity)}
                data-testid={`action-item-${item.category}`}
              >
                {item.message}
              </StatusIndicator>
            </Box>
          ))}
        </Box>
      )}
    </Box>
  );
};

export default VehicleDiagnose;
