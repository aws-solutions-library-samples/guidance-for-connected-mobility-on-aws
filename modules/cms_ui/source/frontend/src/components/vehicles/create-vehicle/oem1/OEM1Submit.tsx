// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import {
  Button,
  Flashbar,
  SpaceBetween,
} from '@cloudscape-design/components';
import type { FlashbarProps } from '@cloudscape-design/components';
import {
  addOEM1Vehicle,
  AddOEM1VehicleError,
} from '@/api/oem1AddVehicle';
import { OEM1Form, validateOEM1Vin } from './OEM1Form';

const DEFAULT_FLEET = 'oem1-staging-fleet';

export const OEM1Submit: React.FC = () => {
  const [vin, setVin] = useState('');
  const [fleetId, setFleetId] = useState(DEFAULT_FLEET);
  const [vinError, setVinError] = useState('');
  const [isLoading, setIsLoading] = useState(false);
  const [notifications, setNotifications] = useState<FlashbarProps.MessageDefinition[]>([]);

  const addNotification = (notification: FlashbarProps.MessageDefinition) => {
    const id = notification.id ?? String(Date.now());
    setNotifications((prev) => [
      { ...notification, id, dismissible: true, onDismiss: () => removeNotification(id) },
      ...prev,
    ]);
  };

  const removeNotification = (id: string) => {
    setNotifications((prev) => prev.filter((n) => n.id !== id));
  };

  const handleSubmit = async () => {
    const err = validateOEM1Vin(vin);
    if (err) {
      setVinError(err);
      return;
    }
    setVinError('');
    setIsLoading(true);
    try {
      const result = await addOEM1Vehicle(vin, fleetId);

      if (result.enrollmentStatus === 'COMPLETED') {
        addNotification({ type: 'success', content: 'OEM1 vehicle enrolled' });
      } else if (
        result.enrollmentStatus === 'PENDING' ||
        result.enrollmentStatus === 'FAILED'
      ) {
        addNotification({
          type: 'info',
          content: 'Saved as pending — will reconcile on next OEM1 sync',
        });
      } else {
        // UNKNOWN — surface cap-hit reason from response (R8)
        const reason =
          (result as any).reason ?? 'VIN not found in OEM1 enrollment feed';
        addNotification({ type: 'warning', content: reason });
      }
    } catch (error) {
      if (error instanceof AddOEM1VehicleError && error.statusCode === 403) {
        addNotification({
          type: 'error',
          content: 'OEM1 add-vehicle requires platform admin',
        });
      } else {
        addNotification({
          type: 'error',
          content:
            error instanceof Error
              ? error.message
              : 'An unexpected error occurred',
        });
      }
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <SpaceBetween size="l">
      {notifications.length > 0 && <Flashbar items={notifications} stackItems />}

      <OEM1Form
        vin={vin}
        fleetId={fleetId}
        onVinChange={setVin}
        onFleetChange={setFleetId}
        vinError={vinError || undefined}
      />

      <Button variant="primary" loading={isLoading} onClick={handleSubmit}>
        Enroll vehicle
      </Button>
    </SpaceBetween>
  );
};
