// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useContext, ReactElement, useRef, useEffect } from "react";
import { ApiContext } from "@/api/provider";
import {
  Button,
  Form,
  Header,
  SpaceBetween,
  Alert,
} from "@cloudscape-design/components";
import { InfoLink } from "../../../commons";
import { CreateVehicleInputPanel, CreateVehicleInputPanelRef } from "./input-panel";
import { TagsPanel } from "../../../commons";
import { getRuntimeConfig } from "../../../../config/api";

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
import { Modal, Box } from "@cloudscape-design/components";
import { useNavigate } from "react-router-dom";

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
}

const defaultData: SimpleCreateVehicleEntry = {
  vin: "",
  make: "",
  model: "",
  year: new Date().getFullYear(),
  licensePlate: "",
  tags: [],
  createCertificate: false,
};

export function FormFull({ loadHelpPanelContent, header }: any) {
  const [data, _setData] = useState<SimpleCreateVehicleEntry>(defaultData);
  const setData = (updateObj = {}) =>
    _setData((prevData) => ({ ...prevData, ...updateObj }));

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
    }
  }, []);

  const createVehicle = async () => {
    try {
      console.log('🚗 Creating vehicle with data:', data);
      console.log('🔐 Certificate creation flag:', data.createCertificate);
      
      // Create the vehicle entry with a default decoder manifest
      const vehicleEntry: CreateVehicleEntry = {
        vin: data.vin,
        decoderManifestName: "default-manifest", // Use a default value
        make: data.make,
        model: data.model,
        year: data.year,
        licensePlate: data.licensePlate,
        fleetId: data.fleetId,
        color: data.color,
        fuelType: data.fuelType,
        vehicleType: data.vehicleType,
        tags: data.tags || {},
        createCertificate: data.createCertificate,
      };

      console.log('🚗 Sending vehicle entry:', vehicleEntry);

      const response = await fetch(`${getRuntimeConfig().apiEndpoint}/api/v1/vehicles`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify(vehicleEntry)
      });
      const output = await response.json();
      
      console.log('✅ Vehicle created successfully:', output);
      
      // Don't navigate immediately - let the success modal handle navigation
      
    } catch (error) {
      console.error('❌ Submit error:', error);
      throw error;
    }
  };

  const onSubmit = async () => {
    if (!inputPanelRef.current?.validate()) {
      return;
    }

    setIsLoading(true);
    setError(null); // Clear any previous errors
    
    try {
      await createVehicle();
      // Store the created vehicle data before showing modal
      setCreatedVehicle({ ...data });
      setShowModal(true);
    } catch (error) {
      console.error('Submit error:', error);
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
  };

  return (
    <>
      <BaseForm
        content={
          <SpaceBetween size="l">
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
              <div>
                <strong>VIN:</strong> {createdVehicle.vin}
              </div>
              <div>
                <strong>Make/Model:</strong> {createdVehicle.make} {createdVehicle.model} ({createdVehicle.year})
              </div>
              {createdVehicle.licensePlate && (
                <div>
                  <strong>License Plate:</strong> {createdVehicle.licensePlate}
                </div>
              )}
            </>
          )}
        </SpaceBetween>
      </Modal>
    </>
  );
}
