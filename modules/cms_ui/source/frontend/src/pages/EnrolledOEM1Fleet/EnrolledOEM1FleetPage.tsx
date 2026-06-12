// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useCallback } from 'react';
import Box from '@cloudscape-design/components/box';
import Button from '@cloudscape-design/components/button';
import Header from '@cloudscape-design/components/header';
import SpaceBetween from '@cloudscape-design/components/space-between';
import StatusIndicator from '@cloudscape-design/components/status-indicator';
import Table from '@cloudscape-design/components/table';
import { oem1ListEnrolled, OEM1ListEnrolledError } from '@/api/oem1ListEnrolled';
import type { EnrolledVehicle, OEM1ListEnrolledResponse } from '@/api/oem1ListEnrolled';
import { addOEM1Vehicle, AddOEM1VehicleError } from '@/api/oem1AddVehicle';

const EnrolledOEM1FleetPage: React.FC = () => {
  const [loading, setLoading] = useState(false);
  const [data, setData] = useState<OEM1ListEnrolledResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [importingVins, setImportingVins] = useState<Set<string>>(new Set());
  const [importResults, setImportResults] = useState<Record<string, 'success' | 'error'>>({});

  const handleLoad = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const result = await oem1ListEnrolled();
      setData(result);
    } catch (err) {
      if (err instanceof OEM1ListEnrolledError) {
        setError(err.message);
      } else {
        setError('Unexpected error loading enrolled fleet.');
      }
    } finally {
      setLoading(false);
    }
  }, []);

  const handleImport = useCallback(
    async (vin: string) => {
      setImportingVins((prev) => new Set(prev).add(vin));
      try {
        // NG11: calls Phase 2 add-vehicle (NOT bulk-enroll); vehicles are already enrolled
        await addOEM1Vehicle(vin, '');
        setImportResults((prev) => ({ ...prev, [vin]: 'success' }));
        // Refresh data after import
        const refreshed = await oem1ListEnrolled();
        setData(refreshed);
      } catch (err) {
        const msg =
          err instanceof AddOEM1VehicleError ? err.message : 'Import failed';
        setImportResults((prev) => ({ ...prev, [vin]: 'error' }));
        setError(msg);
      } finally {
        setImportingVins((prev) => {
          const next = new Set(prev);
          next.delete(vin);
          return next;
        });
      }
    },
    [],
  );

  const missingVehicles = data
    ? data.vehicles.filter((v) => !v.in_cms)
    : [];

  return (
    <Box padding="l">
      <SpaceBetween size="m">
        <Header
          variant="h1"
          actions={
            <Button
              variant="primary"
              loading={loading}
              onClick={handleLoad}
              data-testid="load-button"
            >
              Load enrolled fleet
            </Button>
          }
        >
          View enrolled off-board fleet
        </Header>

        {error && (
          <StatusIndicator type="error" data-testid="error-message">
            {error}
          </StatusIndicator>
        )}

        {data && (
          <SpaceBetween size="s">
            <Box data-testid="reconciliation-summary">
              {`Found ${data.enrolled_at_oem1} enrolled off-board; ${data.enrolled_in_cms} in CMS — ${data.missing_in_cms} missing rows`}
            </Box>

            <Table
              data-testid="enrolled-vehicles-table"
              items={data.vehicles}
              columnDefinitions={[
                { id: 'vin', header: 'VIN', cell: (v: EnrolledVehicle) => v.vin },
                { id: 'sku', header: 'SKU', cell: (v: EnrolledVehicle) => (v as any).sku ?? '—' },
                {
                  id: 'in_cms',
                  header: 'In CMS',
                  cell: (v: EnrolledVehicle) =>
                    v.in_cms ? (
                      <StatusIndicator type="success">Yes</StatusIndicator>
                    ) : (
                      <StatusIndicator type="warning">No</StatusIndicator>
                    ),
                },
                {
                  id: 'action',
                  header: 'Action',
                  cell: (v: EnrolledVehicle) => {
                    if (v.in_cms) return null;
                    if (importResults[v.vin] === 'success') {
                      return <StatusIndicator type="success">Imported</StatusIndicator>;
                    }
                    return (
                      <Button
                        variant="inline-link"
                        loading={importingVins.has(v.vin)}
                        onClick={() => handleImport(v.vin)}
                        data-testid={`import-button-${v.vin}`}
                      >
                        Import missing
                      </Button>
                    );
                  },
                },
              ]}
              header={
                <Header
                  counter={`(${data.vehicles.length})`}
                  actions={
                    missingVehicles.length > 0 && (
                      <Button
                        variant="normal"
                        onClick={() =>
                          missingVehicles.forEach((v) => handleImport(v.vin))
                        }
                        data-testid="import-all-button"
                      >
                        Import all missing
                      </Button>
                    )
                  }
                >
                  Enrolled vehicles
                </Header>
              }
              empty={<Box>No vehicles found.</Box>}
            />
          </SpaceBetween>
        )}
      </SpaceBetween>
    </Box>
  );
};

export default EnrolledOEM1FleetPage;
