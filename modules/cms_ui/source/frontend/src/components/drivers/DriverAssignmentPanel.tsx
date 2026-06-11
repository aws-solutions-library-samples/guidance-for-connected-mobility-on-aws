// SPDX-License-Identifier: Apache-2.0

/**
 * DriverAssignmentPanel — driver-to-vehicle assignment control.
 *
 * Driver-centric assignment by design: a driver "owns" the assignment,
 * vehicles are more fungible than people, and the iOS app signs in as a
 * person (then asks "which vehicle is mine?") so the mutable field
 * belongs on the drivers side. See the CRUD docs / MANAGE_DRIVERS.md for
 * the full rationale.
 *
 * Integration contract
 * --------------------
 *  - Read: the driver object passed from the parent already carries
 *    assignedVehicleId (or null/undefined if unassigned) and fleetId.
 *    We fetch the vehicle record separately for display (make/model/vin).
 *  - List: GET /api/v1/vehicles?fleetId={driver.fleetId}
 *          (scopes the dropdown to in-fleet vehicles so cross-fleet assigns
 *          aren't even offered as options). If the driver has no fleetId,
 *          we fall back to GET /api/v1/vehicles (all) and rely on the
 *          backend's same-fleet check to keep things honest.
 *  - Write: PUT /api/v1/drivers/{driverId} body {"assignedVehicleId": "VEH-X"}
 *           Empty string or null → unassign.
 *  - Cognito mirror: the backend also updates the driver's VSA Cognito
 *    custom:vehicleId; iOS picks up the change on the next sign-in.
 *    The response may include a cognitoMirrorNote which we surface as
 *    an info alert (e.g. "no VSA user provisioned yet").
 *
 * The panel has two visual states:
 *  - Read: show the current assignment (vehicle title + link) or
 *    "No vehicle assigned", plus a "Change" / "Assign vehicle" button.
 *  - Edit: searchable dropdown + Save/Cancel.
 */

import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Container,
  Header,
  Link,
  Select,
  SelectProps,
  SpaceBetween,
  Spinner,
  StatusIndicator,
} from '@cloudscape-design/components';
import { getRuntimeConfig } from '../../config/api';

interface VehicleLite {
  vehicleId: string;
  vin?: string;
  make?: string;
  model?: string;
  year?: number | string;
  fleetId?: string;
}

interface Props {
  driverId: string;
  driverFleetId?: string | null;
  assignedVehicleId?: string | null;
  /** Called after a successful assign/unassign so the parent can refresh. */
  onAssignmentChanged?: (newVehicleId: string | null) => void;
  /** Pull from useAuth() in parent. */
  getIdToken: () => string | null;
}

export const DriverAssignmentPanel: React.FC<Props> = ({
  driverId,
  driverFleetId,
  assignedVehicleId,
  onAssignmentChanged,
  getIdToken,
}) => {
  const [currentVehicle, setCurrentVehicle] = useState<VehicleLite | null>(null);
  const [loadingCurrent, setLoadingCurrent] = useState(false);
  const [editing, setEditing] = useState(false);
  const [candidateVehicles, setCandidateVehicles] = useState<VehicleLite[]>([]);
  const [candidatesLoading, setCandidatesLoading] = useState(false);
  const [selectedOption, setSelectedOption] = useState<SelectProps.Option | null>(null);
  const [saving, setSaving] = useState(false);
  const [alert, setAlert] = useState<{ type: 'success' | 'error' | 'info' | 'warning'; msg: string } | null>(null);

  const apiBase =
    (window as any).runtimeConfig?.apiEndpoint || getRuntimeConfig().apiEndpoint || '';

  const hdrs = useCallback(() => {
    const token =
      getIdToken() ||
      sessionStorage.getItem('idToken') ||
      localStorage.getItem('idToken');
    return {
      'Content-Type': 'application/json',
      ...(token ? { Authorization: `Bearer ${token}` } : {}),
    };
  }, [getIdToken]);

  // Fetch the currently-assigned vehicle's details when the prop changes.
  useEffect(() => {
    let cancelled = false;
    if (!assignedVehicleId) {
      setCurrentVehicle(null);
      return;
    }
    setLoadingCurrent(true);
    (async () => {
      try {
        const resp = await fetch(`${apiBase}api/v1/vehicles/${assignedVehicleId}`, {
          headers: hdrs(),
        });
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
        const data = await resp.json();
        if (!cancelled) {
          setCurrentVehicle(data.vehicle || data);
        }
      } catch {
        // Non-fatal: we just can't show nice make/model info. The vehicleId
        // itself is still displayable and linkable.
        if (!cancelled) setCurrentVehicle({ vehicleId: assignedVehicleId });
      } finally {
        if (!cancelled) setLoadingCurrent(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [assignedVehicleId, apiBase, hdrs]);

  // Lazy-fetch candidate vehicles when the user enters edit mode. Scoped
  // to the driver's fleet when one is available.
  const loadCandidates = useCallback(async () => {
    setCandidatesLoading(true);
    try {
      const q = driverFleetId ? `?fleetId=${encodeURIComponent(driverFleetId)}` : '';
      const resp = await fetch(`${apiBase}api/v1/vehicles${q}`, { headers: hdrs() });
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`);
      const data = await resp.json();
      const list: VehicleLite[] = (data.vehicles || data.items || []).map(
        (v: any) => ({
          vehicleId: v.vehicleId,
          vin: v.vin,
          make: v.make,
          model: v.model,
          year: v.year,
          fleetId: v.fleetId,
        }),
      );
      setCandidateVehicles(list);
    } catch (e: any) {
      setAlert({ type: 'error', msg: `Failed to load vehicles: ${e.message || e}` });
      setCandidateVehicles([]);
    } finally {
      setCandidatesLoading(false);
    }
  }, [apiBase, driverFleetId, hdrs]);

  const startEdit = () => {
    setAlert(null);
    setEditing(true);
    setSelectedOption(
      assignedVehicleId
        ? { value: assignedVehicleId, label: formatVehicleLabel(currentVehicle) }
        : null,
    );
    loadCandidates();
  };

  const cancelEdit = () => {
    setEditing(false);
    setSelectedOption(null);
  };

  const save = async () => {
    setSaving(true);
    setAlert(null);
    const newVehicleId = selectedOption?.value || '';
    try {
      const resp = await fetch(`${apiBase}api/v1/drivers/${driverId}`, {
        method: 'PUT',
        headers: hdrs(),
        body: JSON.stringify({ assignedVehicleId: newVehicleId }),
      });
      const data = await resp.json();
      if (!resp.ok) {
        throw new Error(data.error || `HTTP ${resp.status}`);
      }
      // Surface any Cognito-mirror note as info — not a failure, but good to see.
      if (data.cognitoMirrorNote) {
        setAlert({ type: 'info', msg: data.cognitoMirrorNote });
      } else {
        setAlert({
          type: 'success',
          msg: newVehicleId ? `Assigned to ${newVehicleId}.` : 'Vehicle unassigned.',
        });
      }
      setEditing(false);
      onAssignmentChanged?.(newVehicleId || null);
    } catch (e: any) {
      setAlert({ type: 'error', msg: e.message || 'Assignment failed' });
    } finally {
      setSaving(false);
    }
  };

  const unassign = async () => {
    setSaving(true);
    setAlert(null);
    try {
      const resp = await fetch(`${apiBase}api/v1/drivers/${driverId}`, {
        method: 'PUT',
        headers: hdrs(),
        body: JSON.stringify({ assignedVehicleId: '' }),
      });
      const data = await resp.json();
      if (!resp.ok) throw new Error(data.error || `HTTP ${resp.status}`);
      if (data.cognitoMirrorNote) {
        setAlert({ type: 'info', msg: data.cognitoMirrorNote });
      } else {
        setAlert({ type: 'success', msg: 'Vehicle unassigned.' });
      }
      onAssignmentChanged?.(null);
    } catch (e: any) {
      setAlert({ type: 'error', msg: e.message || 'Unassign failed' });
    } finally {
      setSaving(false);
    }
  };

  // Build the options for the Select. Empty-string option at top for
  // explicit "unassign" in edit mode so users don't have to navigate away.
  const options: SelectProps.Option[] = useMemo(
    () => [
      { value: '', label: '— No vehicle —', description: 'Unassign the driver' },
      ...candidateVehicles.map((v) => ({
        value: v.vehicleId,
        label: formatVehicleLabel(v),
        description: v.fleetId ? `Fleet ${v.fleetId}` : undefined,
      })),
    ],
    [candidateVehicles],
  );

  // ----- Render -----

  const headerActions = (
    <SpaceBetween size="xs" direction="horizontal">
      {!editing && (
        <Button iconName={assignedVehicleId ? 'edit' : 'add-plus'} onClick={startEdit}>
          {assignedVehicleId ? 'Change vehicle' : 'Assign vehicle'}
        </Button>
      )}
      {!editing && assignedVehicleId && (
        <Button
          iconName="close"
          onClick={unassign}
          loading={saving}
          disabled={saving}
        >
          Unassign
        </Button>
      )}
      {editing && (
        <>
          <Button onClick={cancelEdit} disabled={saving}>Cancel</Button>
          <Button variant="primary" onClick={save} loading={saving} disabled={saving}>
            Save
          </Button>
        </>
      )}
    </SpaceBetween>
  );

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Which vehicle this driver is assigned to. Change here to update CMS and the driver's iOS app identity in one step."
          actions={headerActions}
        >
          Vehicle assignment
        </Header>
      }
    >
      {alert && (
        <Box padding={{ bottom: 's' }}>
          <Alert type={alert.type} dismissible onDismiss={() => setAlert(null)}>
            {alert.msg}
          </Alert>
        </Box>
      )}

      {editing ? (
        <SpaceBetween size="s">
          <Select
            selectedOption={selectedOption}
            onChange={({ detail }) => setSelectedOption(detail.selectedOption)}
            options={options}
            filteringType="auto"
            loadingText="Loading vehicles..."
            statusType={candidatesLoading ? 'loading' : 'finished'}
            placeholder="Select a vehicle"
            empty={
              driverFleetId
                ? `No vehicles found in fleet ${driverFleetId}.`
                : 'No vehicles available.'
            }
          />
          {driverFleetId && (
            <Box fontSize="body-s" color="text-body-secondary">
              Limited to vehicles in fleet <strong>{driverFleetId}</strong>.
              To assign a vehicle from another fleet, change the driver's fleet first.
            </Box>
          )}
        </SpaceBetween>
      ) : loadingCurrent ? (
        <Box textAlign="center" padding="s">
          <Spinner />
        </Box>
      ) : !assignedVehicleId ? (
        <Box color="text-body-secondary">
          <StatusIndicator type="stopped">No vehicle assigned</StatusIndicator>
          <Box padding={{ top: 'xs' }} fontSize="body-s">
            This driver won't see a vehicle in the iOS app until assigned here.
          </Box>
        </Box>
      ) : (
        <SpaceBetween size="xs">
          <Box>
            <StatusIndicator type="success">Assigned</StatusIndicator>
          </Box>
          <Box>
            <Link href={`/vehicles/management/${assignedVehicleId}`}>
              {formatVehicleLabel(currentVehicle)}
            </Link>
          </Box>
          {currentVehicle?.vin && (
            <Box fontSize="body-s" color="text-body-secondary">
              VIN {currentVehicle.vin}
              {currentVehicle.fleetId && <> · Fleet {currentVehicle.fleetId}</>}
            </Box>
          )}
        </SpaceBetween>
      )}
    </Container>
  );
};

function formatVehicleLabel(v?: VehicleLite | null): string {
  if (!v) return '';
  const parts: string[] = [];
  if (v.year) parts.push(String(v.year));
  if (v.make) parts.push(v.make);
  if (v.model) parts.push(v.model);
  if (parts.length === 0) return v.vehicleId;
  return `${parts.join(' ')} (${v.vehicleId})`;
}
