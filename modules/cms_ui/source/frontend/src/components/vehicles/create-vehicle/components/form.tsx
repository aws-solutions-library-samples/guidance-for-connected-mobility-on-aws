// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useContext, ReactElement, useRef, useEffect } from "react";
import { OEM1AddVehicleSubFlow } from '../oem1';
import { deriveVehicleSourceFromFleet, getFleetDataSource } from '@/types/fleet-types';
import type { FleetItem, VehicleSource } from '@/types/fleet-types';
import { ApiContext } from "@/api/provider";
import {
  Button,
  Form,
  Header,
  SpaceBetween,
  Alert,
  Box,
} from "@cloudscape-design/components";
import { InfoLink } from "../../../commons";
import { CreateVehicleInputPanel, CreateVehicleInputPanelRef } from "./input-panel";
import { TagsPanel } from "../../../commons";
import { getRuntimeConfig } from "../../../../config/api";
import FleetPicker from "@/components/fleet-picker/FleetPicker";
import { useFleetSelection } from "@/components/fleet-picker/useFleetSelection";
import { ListFleetsCommand } from "@/api/fleet-management-client";

// Simple interface to replace CreateVehicleEntry
interface CreateVehicleEntry {
  name?: string;
  vin?: string;
  modelManifestArn?: string;
  decoderManifestArn?: string;
  createCertificate?: boolean;
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

function BaseForm({ content, onCancelClick, onSubmitClick, header, isLoading }: BaseFormProps) {
  return (
    <form onSubmit={(event) => event.preventDefault()}>
      <Form
        actions={
          <FormActions
            onCancelClick={onCancelClick}
            onSubmitClick={onSubmitClick}
            isLoading={isLoading}
          />
        }
        header={header}
      >
        {content}
      </Form>
    </form>
  );
}

// Updated CreateVehicleEntry without decoder manifest but with certificate support
interface SimpleCreateVehicleEntry {
  vin: string;
  make: string;
  model: string;
  year: number;
  licensePlate: string;
  tags?: any[];
  createCertificate?: boolean;
  certificateArn?: string;
  certificateId?: string;
  [key: string]: any;
}

const defaultData: SimpleCreateVehicleEntry = {
  vin: "",
  make: "",
  model: "",
  year: new Date().getFullYear(),
  licensePlate: "",
  tags: [],
  createCertificate: true,  // Default to true so certificates are always created
};

/** Thin wrapper that returns the full FleetItem (with data_source) for a selected fleet id. */
function useFleetItem(fleetId: string | null): FleetItem | null {
  const { client } = useContext(ApiContext);
  const [fleetItem, setFleetItem] = useState<FleetItem | null>(null);

  useEffect(() => {
    if (!fleetId) {
      setFleetItem(null);
      return;
    }
    let cancelled = false;
    client.send(new ListFleetsCommand()).then((output: { fleets?: FleetItem[] }) => {
      if (cancelled) return;
      const match = (output.fleets ?? []).find(
        (f) => (f.id ?? f.fleetId) === fleetId,
      );
      setFleetItem(match ?? null);
    }).catch(() => { /* ignore; fleetItem stays null */ });
    return () => { cancelled = true; };
  }, [client, fleetId]);

  return fleetItem;
}

export function FormFull({ loadHelpPanelContent, header }: any) {
  const [data, _setData] = useState<SimpleCreateVehicleEntry>(defaultData);
  const setData = (updateObj = {}) =>
    _setData((prevData) => ({ ...prevData, ...updateObj }));

  // selectedFleetId drives source derivation — replaces the old SourcePickerStep.
  const [selectedFleetId, setSelectedFleetId] = useState<string | null>(null);
  const selectedFleet = useFleetItem(selectedFleetId);

  // Derived source — null until a fleet is selected
  const vehicleSource: VehicleSource | null = selectedFleet
    ? deriveVehicleSourceFromFleet(selectedFleet)
    : null;

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
        createCertificate: data.createCertificate,
      };

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
    if (!inputPanelRef.current?.validate()) {
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
    setSelectedFleetId(null);
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
                OQ3 decision: wraps existing FleetPicker (see decisions.md). */}
            <FleetPicker
              label="Fleet"
              onChange={(fleetId) => {
                // Filter out the "all fleets" sentinel
                setSelectedFleetId(fleetId === '__all__' ? null : fleetId);
              }}
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
                <CreateVehicleInputPanel
                  ref={inputPanelRef}
                  loadHelpPanelContent={loadHelpPanelContent}
                  inputData={data}
                  setInputData={setData}
                />
                <TagsPanel
                  ref={tagsPanelRef}
                  tags={data.tags || []}
                  setTags={(tags: any[]) => setData({ tags })}
                />
              </>
            )}
          </SpaceBetween>
        }
        onCancelClick={onCancel}
        onSubmitClick={onSubmit}
        isLoading={isLoading}
        header={header}
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
