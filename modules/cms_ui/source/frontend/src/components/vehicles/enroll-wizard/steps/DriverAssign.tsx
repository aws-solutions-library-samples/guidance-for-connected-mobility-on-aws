// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState } from 'react';
import {
  SpaceBetween,
  Table,
  Select,
  Button,
  Modal,
  FormField,
  Input,
  Box,
  Alert,
} from '@cloudscape-design/components';
import { createDriver } from '@/api/createDriver';
import type { EnrollWizardState, WizardAction } from '../state/reducer';

// Minimal driver option type for the Select component
interface DriverOption {
  value: string;
  label: string;
}

interface StepDriverAssignProps {
  state: EnrollWizardState;
  dispatch: React.Dispatch<WizardAction>;
  /** Available drivers — caller fetches; pass [] when loading. */
  drivers: DriverOption[];
}

const StepDriverAssign: React.FC<StepDriverAssignProps> = ({ state, dispatch, drivers }) => {
  const { rows } = state;
  const [createModalVisible, setCreateModalVisible] = useState(false);
  const [createForVin, setCreateForVin] = useState<string | null>(null);
  const [driverFirstName, setDriverFirstName] = useState('');
  const [driverLastName, setDriverLastName] = useState('');
  const [driverEmail, setDriverEmail] = useState('');
  const [createError, setCreateError] = useState('');
  const [creating, setCreating] = useState(false);

  const allAssigned = rows.length > 0 && rows.every((r) => r.driverId.trim().length > 0);

  function openCreateModal(vin: string) {
    setCreateForVin(vin);
    setDriverFirstName('');
    setDriverLastName('');
    setDriverEmail('');
    setCreateError('');
    setCreateModalVisible(true);
  }

  async function handleCreateDriver() {
    setCreating(true);
    setCreateError('');
    try {
      const result = await createDriver({
        firstName: driverFirstName,
        lastName: driverLastName,
        email: driverEmail,
        fleetId: state.fleetId || undefined,
      });
      const newId = result.driver.id;
      const newName = `${driverFirstName} ${driverLastName}`.trim();
      if (createForVin) {
        dispatch({ type: 'SET_DRIVER', vin: createForVin, driverId: newId, driverName: newName });
      }
      setCreateModalVisible(false);
    } catch (err) {
      setCreateError(err instanceof Error ? err.message : 'Failed to create driver');
    } finally {
      setCreating(false);
    }
  }

  return (
    <>
      {!allAssigned && rows.length > 0 && (
        <Alert type="info">
          Each VIN must have a driver before you can submit (C4). Assign or create a driver per row.
        </Alert>
      )}

      <Table
        items={rows}
        columnDefinitions={[
          { id: 'vin', header: 'VIN', cell: (r) => r.vin },
          {
            id: 'driver',
            header: 'Assigned driver',
            cell: (r) => (
              <SpaceBetween size="xs" direction="horizontal">
                <Select
                  selectedOption={
                    r.driverId
                      ? { value: r.driverId, label: r.driverName ?? r.driverId }
                      : null
                  }
                  onChange={({ detail }) => {
                    const opt = detail.selectedOption;
                    if (opt?.value) {
                      dispatch({
                        type: 'SET_DRIVER',
                        vin: r.vin,
                        driverId: opt.value,
                        driverName: opt.label,
                      });
                    }
                  }}
                  options={drivers}
                  placeholder="Select driver"
                  empty="No drivers found"
                  filteringType="auto"
                />
                <Button
                  variant="inline-link"
                  onClick={() => openCreateModal(r.vin)}
                >
                  + Create driver
                </Button>
              </SpaceBetween>
            ),
          },
        ]}
        empty={<Box textAlign="center">No VINs loaded</Box>}
      />

      {/* "+ Create driver" sub-modal (OQ11) */}
      <Modal
        visible={createModalVisible}
        onDismiss={() => setCreateModalVisible(false)}
        header="Create driver"
        footer={
          <SpaceBetween size="xs" direction="horizontal">
            <Button onClick={() => setCreateModalVisible(false)} disabled={creating}>
              Cancel
            </Button>
            <Button
              variant="primary"
              onClick={() => void handleCreateDriver()}
              loading={creating}
              disabled={!driverFirstName || !driverEmail}
            >
              Create
            </Button>
          </SpaceBetween>
        }
      >
        <SpaceBetween size="m">
          {createError && <Alert type="error">{createError}</Alert>}
          <FormField label="First name">
            <Input value={driverFirstName} onChange={({ detail }) => setDriverFirstName(detail.value)} />
          </FormField>
          <FormField label="Last name">
            <Input value={driverLastName} onChange={({ detail }) => setDriverLastName(detail.value)} />
          </FormField>
          <FormField label="Email" description="Required">
            <Input
              value={driverEmail}
              onChange={({ detail }) => setDriverEmail(detail.value)}
              type="email"
            />
          </FormField>
        </SpaceBetween>
      </Modal>
    </>
  );
};

export default StepDriverAssign;
