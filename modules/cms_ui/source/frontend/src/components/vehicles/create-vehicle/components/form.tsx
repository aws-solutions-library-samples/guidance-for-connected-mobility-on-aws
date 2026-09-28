// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useContext, ReactElement, useRef, useEffect } from "react";
import { OEM1AddVehicleSubFlow } from '../oem1';
import { deriveVehicleSourceFromFleet, getFleetDataSource } from '@/types/fleet-types';
import type { FleetItem, FleetDataSource, VehicleSource } from '@/types/fleet-types';
import { ApiContext } from "@/api/provider";
import {
  Button,
  Form,
  Header,
  SpaceBetween,
  Alert,
  Box,
  RadioGroup,
  FormField,
} from "@cloudscape-design/components";
import { InfoLink } from "../../../commons";
import { CreateVehicleInputPanel, CreateVehicleInputPanelRef } from "./input-panel";
import { OffBoardVehiclePicker } from "./OffBoardVehiclePicker";
import { TagsPanel } from "../../../commons";
import { getRuntimeConfig } from "../../../../config/api";
import FleetPicker from "@/components/fleet-picker/FleetPicker";
import { CampaignPicker } from './campaign-picker';

// Simple interface to replace CreateVehicleEntry
interface CreateVehicleEntry {
  name?: string;
  vin?: string;
  modelManifestArn?: string;
  decoderManifestArn?: string;
  modelManifestName: string;
  [key: string]: any;
}

import { UI_ROUTES } from "@/utils/constants";
import { Modal } from "@cloudscape-design/components";
import { useNavigate } from "react-router-dom";
import { authFetch } from '../../../../utils/authFetch';

interface BaseFormProps {
  content: React.ReactElement;
  onCancelClick: any;
  onSubmitClick: any;
  header: ReactElement;
  isLoading?: boolean;
  /** When true, hides the standard Cancel/Create-vehicle button pair at
   *  the bottom of the form. Used by branches that render their own
   *  action buttons (e.g. the off-board VIN picker has its own
   *  Cancel/Import buttons and doesn't need duplicates here). */
  hideActions?: boolean;
}

function FormActions({ onCancelClick, onSubmitClick, isLoading }: any) {
  return (
    <SpaceBetween direction="horizontal" size="xs">
      <Button variant="link" onClick={onCancelClick} disabled={isLoading}>
        Cancel
      </Button>
      <Button variant="primary" onClick={onSubmitClick} loading={isLoading}>
        Create vehicle
      </Button>
    </SpaceBetween>
  );
}

function BaseForm({ content, onCancelClick, onSubmitClick, header, isLoading, hideActions }: BaseFormProps) {
  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <Form
        actions={
          hideActions ? undefined : (
            <FormActions
              onCancelClick={onCancelClick}
              onSubmitClick={onSubmitClick}
              isLoading={isLoading}
            />
          )
        }
        header={header}
      >
        {content}
      </Form>
    </form>
  );
}

export function FormHeader({ loadHelpPanelContent }: any) {
  return (
    <Header
      variant="h2"
      description="Configure the basic settings for your new vehicle."
    >
      Vehicle Configuration
    </Header>
  );
}

// Updated CreateVehicleEntry without decoder manifest but with model manifest support
interface SimpleCreateVehicleEntry {
  vin: string;
  make: string;
  model: string;
  year: number;
  licensePlate: string;
  modelManifestName: string;
  tags?: any[];
  [key: string]: any;
}

const defaultData: SimpleCreateVehicleEntry = {
  vin: "",
  make: "",
  model: "",
  year: new Date().getFullYear(),
  licensePlate: "",
  modelManifestName: "",
  tags: [],
};

/**
 * NOTE: an earlier version of this form used a `useFleetItem(fleetId)` helper
 * that called `ApiContext.client.send(new ListFleetsCommand())` to fetch the
 * full FleetItem (with `data_source`). That hook was removed on 2026-06-15 —
 * `createFleetManagementClient` is a stub returning `Promise.resolve({})`, so
 * the helper always resolved `null` and source derivation never advanced past
 * the "Select a fleet to continue" hint. The replacement: FleetPicker now
 * surfaces the matched FleetItem via `onFleetItemChange`, which avoids a
 * second fetch and works with the live `/api/v1/fleets` data the picker
 * already loaded.
 *
 * See: issues/2026-06-15-create-vehicle-fleet-picker-empty/
 */

export function FormFull({ loadHelpPanelContent, header }: any) {
  const [data, _setData] = useState<SimpleCreateVehicleEntry>(defaultData);
  const setData = (updateObj = {}) =>
    _setData((prevData) => ({ ...prevData, ...updateObj }));

  // selectedFleetId drives source derivation — replaces the old SourcePickerStep.
  const [selectedFleetId, setSelectedFleetId] = useState<string | null>(null);
  const [selectedFleet, setSelectedFleet] = useState<FleetItem | null>(null);

  // Derived source — null until a fleet is selected
  const vehicleSource: VehicleSource | null = selectedFleet
    ? deriveVehicleSourceFromFleet(selectedFleet)
    : null;

  // dataSource override — user-selected; defaults to fleet's derived source.
  // The fleet's getFleetDataSource() result is the default; user may override.
  const fleetDerivedDataSource: FleetDataSource = selectedFleet
    ? getFleetDataSource(selectedFleet)
    : 'vehicle-telemetry';

  const [dataSource, setDataSource] = useState<FleetDataSource>('vehicle-telemetry');

  // spec 2026-09-01-cms-campaign-follows-enrollment § D4:
  // Optional campaign template reference — included in POST body only when truthy.
  const [campaignTemplateRef, setCampaignTemplateRef] = useState<string | null>(null);

  // Keep dataSource in sync with fleet selection — resets to fleet default when fleet changes.
  // Also clears campaignTemplateRef to prevent stale selection carrying over to the new fleet.
  useEffect(() => {
    if (selectedFleet) {
      setDataSource(getFleetDataSource(selectedFleet));
      setCampaignTemplateRef(null);
    }
  }, [selectedFleet]);

  const [showModal, setShowModal] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [createdVehicle, setCreatedVehicle] = useState<SimpleCreateVehicleEntry | null>(null);

  const inputPanelRef = useRef<CreateVehicleInputPanelRef>(null);
  const tagsPanelRef = useRef<any>(null);

  const api = useContext(ApiContext);
  const navigate = useNavigate();

  // Initialize fleetId from URL parameters
  useEffect(() => {
    const urlParams = new URLSearchParams(window.location.search);
    const fleetIdFromUrl = urlParams.get('fleetId');
    if (fleetIdFromUrl) {
      setData({ fleetId: fleetIdFromUrl });
      setSelectedFleetId(fleetIdFromUrl);
    }
  }, []);

  const createVehicle = async () => {
    try {
      const vehicleEntry: CreateVehicleEntry = {
        vin: data.vin,
        decoderManifestName: "default-manifest",
        make: data.make,
        model: data.model,
        year: data.year,
        licensePlate: data.licensePlate,
        fleetId: selectedFleetId ?? data.fleetId,
        color: data.color,
        fuelType: data.fuelType,
        vehicleType: data.vehicleType,
        tags: data.tags || {},
        modelManifestName: data.modelManifestName,
        // spec 2026-08-29-cms-vehicle-classification § D7:
        // vehicle owns the truth — submit the user-chosen dataSource
        dataSource: dataSource,
      };

      // spec 2026-09-01-cms-campaign-follows-enrollment § D4:
      // Only include campaignTemplateRef when the user made a selection.
      // Cloud-telemetry vehicles silently ignore it on the backend;
      // the UI only renders the picker for vehicle-telemetry anyway.
      if (campaignTemplateRef) {
        vehicleEntry.campaignTemplateRef = campaignTemplateRef;
      }

      const response = await authFetch(`${getRuntimeConfig().apiEndpoint}api/v1/vehicles`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(vehicleEntry),
      });
      await response.json();
    } catch (error) {
      throw error;
    }
  };

  const onSubmit = async () => {
    if (!selectedFleet) {
      setError('Select a fleet to continue.');
      return;
    }

    // Off-board picker mode — the outer button is hidden in this case, but
    // this defensive early-return prevents a null-ref crash if the button
    // is somehow clicked (dev tooling, keyboard shortcut, etc.). The
    // picker owns the import path via its own Import button.
    if (dataSource === 'cloud-telemetry') {
      return;
    }

    if (!inputPanelRef.current?.validate()) {
      return;
    }

    if (vehicleSource === 'cms' && !data.modelManifestName) {
      // Model is required for CMS-native vehicles — surface a validation message
      // rather than sending a request that the API will reject with 400.
      setError('Select a model to continue. A vehicle model is required for vehicle-telemetry vehicles.');
      return;
    }

    setIsLoading(true);
    setError(null);

    try {
      await createVehicle();
      setCreatedVehicle({ ...data });
      setShowModal(true);
    } catch (error) {
      setError(error instanceof Error ? error.message : 'An error occurred while creating the vehicle');
    } finally {
      setIsLoading(false);
    }
  };

  const onCancel = () => {
    navigate(UI_ROUTES.VEHICLE_MANAGEMENT);
  };

  const resetForm = () => {
    _setData(defaultData);
    setError(null);
    setCreatedVehicle(null);
    setShowModal(false);
    // Keep selectedFleetId / selectedFleet intact so 'Create Another' keeps
    // working under the same fleet — most operators want to bulk-add
    // vehicles into one fleet. Reseting these here also broke the
    // outer form's onSubmit fleet check because FleetPicker's own
    // localStorage-backed selection state doesn't get reset alongside,
    // so visually the picker still showed the old fleet while the
    // parent form saw no fleet at all.
    setDataSource('vehicle-telemetry');
    // spec 2026-09-01-cms-campaign-follows-enrollment § D4
    setCampaignTemplateRef(null);
  };

  // Fleet source label for the read-only indicator
  const fleetSourceLabel = selectedFleet
    ? `${selectedFleet.name ?? selectedFleetId} (${getFleetDataSource(selectedFleet)})`
    : null;

  return (
    <>
      <BaseForm
        content={
          <SpaceBetween size="l">
            {/* Fleet selector — source derived from selected fleet's data_source.
                OQ3 decision: wraps existing FleetPicker (see decisions.md).
                onFleetItemChange surfaces the matched FleetItem (with data_source)
                without requiring a second fetch — the picker already has the data. */}
            <FleetPicker
              label="Fleet"
              onChange={(fleetId) => {
                // Filter out the "all fleets" sentinel
                setSelectedFleetId(fleetId === '__all__' ? null : fleetId);
              }}
              onFleetItemChange={(fleet) => setSelectedFleet(fleet)}
            />

            {!selectedFleet && (
              <Box color="text-body-secondary" data-testid="fleet-hint">
                Select a fleet to continue
              </Box>
            )}

            {fleetSourceLabel && (
              <Box color="text-body-secondary" data-testid="fleet-source-indicator">
                Selected fleet: {fleetSourceLabel}
              </Box>
            )}

            {error && (
              <Alert
                type="error"
                dismissible
                onDismiss={() => setError(null)}
                header="Error creating vehicle"
              >
                {error}
              </Alert>
            )}

            {/* Route input panel based on derived source */}
            {vehicleSource === 'oem1' && (
              <OEM1AddVehicleSubFlow />
            )}
            {vehicleSource === 'cms' && (
              <>
                {/* spec 2026-08-29-cms-vehicle-classification § D7:
                    data-source override — pre-selected from fleet; user may override.
                    Vehicle owns the truth, not the fleet. */}
                <FormField
                  label="Data source (vehicle-owned)"
                  description="Whether this vehicle has an onboard ECU connection (issues a certificate) or is tracked via a cloud feed."
                >
                  <RadioGroup
                    data-testid="datasource-radio-group"
                    value={dataSource}
                    onChange={({ detail }) => {
                      setDataSource(detail.value as FleetDataSource);
                      // Clear campaign selection when switching data source to avoid
                      // stale campaignTemplateRef reaching the POST payload (spec S2).
                      setCampaignTemplateRef(null);
                    }}
                    items={[
                      {
                        value: 'vehicle-telemetry',
                        label: 'Onboard (vehicle-telemetry) — issues a device certificate',
                      },
                      {
                        value: 'cloud-telemetry',
                        label: 'Offboard (cloud-telemetry) — no certificate',
                      },
                    ]}
                  />
                </FormField>

                {/* Fleet-disagreement info alert — vehicle wins per spec § D2 */}
                {selectedFleet && dataSource !== fleetDerivedDataSource && (
                  <Alert
                    type="info"
                    data-testid="datasource-disagreement-alert"
                  >
                    {`The fleet's default data source is ${fleetDerivedDataSource}. This vehicle will be created as ${dataSource}.`}
                    {fleetDerivedDataSource === 'cloud-telemetry' && dataSource === 'vehicle-telemetry' && (
                      <Box variant="p" padding={{ top: 'xs' }}>
                        This will create a vehicle-telemetry vehicle in a cloud-telemetry fleet. Uncommon but supported.
                      </Box>
                    )}
                  </Alert>
                )}

                {/* Off-board picker branch — when the operator picked
                    off-board (cloud-telemetry) on a real fleet, the manual
                    VIN/make/model panel is replaced with the producer VIN
                    picker. If the fleet has NO data-product assignment
                    yet, the picker's own empty state lets the operator
                    assign one inline before the VIN table appears. */}
                {selectedFleet && dataSource === 'cloud-telemetry' ? (
                  <OffBoardVehiclePicker
                    fleet={selectedFleet}
                    onCancel={onCancel}
                    onImported={(imported) => {
                      // After a successful import, bounce to the vehicle
                      // management list so the newly-imported VINs appear
                      // in context. The success flash inside the picker
                      // stays until dismissed / route change.
                      if (imported > 0) {
                        navigate(UI_ROUTES.VEHICLE_MANAGEMENT);
                      }
                    }}
                  />
                ) : (
                  <>
                    <CreateVehicleInputPanel
                      ref={inputPanelRef}
                      loadHelpPanelContent={loadHelpPanelContent}
                      inputData={data}
                      setInputData={setData}
                    />
                    {!data.modelManifestName && (
                      <Box color="text-body-secondary" data-testid="model-hint">
                        Select a model to continue
                      </Box>
                    )}
                    {/* spec 2026-09-01-cms-campaign-follows-enrollment § D4:
                        Campaign picker only shown for vehicle-telemetry vehicles;
                        cloud-telemetry has no FleetWise campaigns. */}
                    {dataSource === 'vehicle-telemetry' && (
                      <CampaignPicker
                        selectedCampaignTemplateRef={campaignTemplateRef}
                        onChange={setCampaignTemplateRef}
                      />
                    )}
                    <TagsPanel
                      ref={tagsPanelRef}
                      tags={data.tags || []}
                      setTags={(tags: any[]) => setData({ tags })}
                    />
                  </>
                )}
              </>
            )}
          </SpaceBetween>
        }
        onCancelClick={onCancel}
        onSubmitClick={onSubmit}
        isLoading={isLoading}
        header={header}
        /* When the off-board picker renders, IT owns Cancel + Import — no
           validate-and-POST is possible from the outer buttons anyway
           (there's no VIN/make/model form to validate). Hide the outer
           Cancel/Create-vehicle pair to prevent a silent no-op click. */
        hideActions={!!selectedFleet && dataSource === 'cloud-telemetry'}
      />

      <Modal
        onDismiss={() => setShowModal(false)}
        visible={showModal}
        closeAriaLabel="Close modal"
        footer={
          <Box float="right">
            <SpaceBetween direction="horizontal" size="xs">
              <Button variant="link" onClick={resetForm}>
                Create Another
              </Button>
              <Button variant="primary" onClick={() => navigate(UI_ROUTES.VEHICLE_MANAGEMENT)}>
                Go to Vehicle Management
              </Button>
            </SpaceBetween>
          </Box>
        }
        header="Vehicle Created Successfully"
      >
        <SpaceBetween size="m">
          <div>Your vehicle has been created successfully!</div>
          {createdVehicle && (
            <>
              <div><strong>VIN:</strong> {createdVehicle.vin}</div>
              <div><strong>Make/Model:</strong> {createdVehicle.make} {createdVehicle.model} ({createdVehicle.year})</div>
              {createdVehicle.licensePlate && (
                <div><strong>License Plate:</strong> {createdVehicle.licensePlate}</div>
              )}
            </>
          )}
        </SpaceBetween>
      </Modal>
    </>
  );
}
