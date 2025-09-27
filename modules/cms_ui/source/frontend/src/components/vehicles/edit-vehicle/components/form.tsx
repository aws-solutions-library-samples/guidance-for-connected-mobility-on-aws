// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useContext, ReactElement, useEffect } from "react";
import { ApiContext } from "@/api/provider";
import {
  Button,
  Form,
  Header,
  SpaceBetween,
} from "@cloudscape-design/components";
import { InfoLink } from "../../../commons";
import { CreateVehicleAttributesInputPanel } from "./attributes-panel";
import { TagsPanel } from "../../../commons";
import { getRuntimeConfig } from "../../../../config/api";

// Simple interface to replace EditVehicleEntry
interface EditVehicleEntry {
  name?: string;
  vin?: string;
  modelManifestArn?: string;
  decoderManifestArn?: string;
  [key: string]: any;
}

import { UI_ROUTES } from "@/utils/constants";
import { Modal, Box } from "@cloudscape-design/components";
import { useNavigate, useSearchParams } from "react-router-dom";

interface BaseFormProps {
  content: React.ReactElement;
  onCancelClick: any;
  onSubmitClick: any;
  header: ReactElement;
}

export function FormHeader({ loadHelpPanelContent }: any) {
  return (
    <Header
      variant="h1"
      info={
        <InfoLink
          id="form-main-info-link"
          onFollow={() => loadHelpPanelContent(0)}
        />
      }
      description={"Edit vehicle details and save changes."}
    >
      Edit Vehicle
    </Header>
  );
}

function FormActions({ onCancelClick, onSubmitClick }: any) {
  return (
    <SpaceBetween direction="horizontal" size="xs">
      <Button variant="link" onClick={onCancelClick}>
        Cancel
      </Button>
      <Button data-testid="create" variant="primary" onClick={onSubmitClick}>
        Save Changes
      </Button>
    </SpaceBetween>
  );
}

function BaseForm({
  content,
  onCancelClick,
  onSubmitClick,
  header,
}: BaseFormProps) {
  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        if (onSubmitClick) {
          onSubmitClick();
        }
      }}
    >
      <Form
        header={header}
        actions={
          <FormActions
            onCancelClick={onCancelClick}
            onSubmitClick={onSubmitClick}
          />
        }
        errorIconAriaLabel="Error"
      >
        {content}
      </Form>
    </form>
  );
}

const defaultData: EditVehicleEntry = {
  name: "",
  vin: "",
  make: "",
  model: "",
  year: 0,
  licensePlate: "",
  tags: [],
};

export function FormFull({ loadHelpPanelContent, header }: any) {
  const [data, _setData] = useState<EditVehicleEntry>(defaultData);
  const setData = (updateObj = {}) =>
    _setData((prevData) => ({ ...prevData, ...updateObj }));

  const api = useContext(ApiContext);

  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const vehicleId = searchParams.get('vehicleId');

  const [loading, setLoading] = useState(false);
  const [modalVisible, setModalVisible] = useState(false);
  const [modalConfig, setModalConfig] = useState<{
    title: string;
    message: string;
    type: "success" | "error";
  }>({
    title: "",
    message: "",
    type: "success",
  });

  const showModal = (
    type: "success" | "error",
    title: string,
    message: string,
  ) => {
    setModalConfig({
      type,
      title,
      message,
    });
    setModalVisible(true);
  };

  const handleModalDismiss = () => {
    setModalVisible(false);

    // If it was a successful submission, we might want to perform additional actions
    if (modalConfig.type === "success") {
      navigate(UI_ROUTES.VEHICLE_MANAGEMENT);
    }
  };

  useEffect(() => {
    async function setVehicle() {
      if (!vehicleId) return;
      
      setLoading(true);
      try {
        const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/vehicles/${vehicleId}`);
        const data = await response.json();
        const vehicleData = data.vehicle;
        
        setData({
          vin: vehicleData.vin,
          make: vehicleData.make,
          model: vehicleData.model,
          year: vehicleData.year,
          licensePlate: vehicleData.licensePlate,
          tags: vehicleData.tags || [],
        });
      } catch (error) {
        console.error('Error loading vehicle data:', error);
      } finally {
        setLoading(false);
      }
    }

    setVehicle();
  }, [vehicleId]);

  const editVehicle = async () => {
    try {
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/vehicles/${vehicleId}`, {
        method: 'PUT',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ name: vehicleId, entry: data })
      });

      if (response.ok) {
        showModal("success", "Success!", "Vehicle edited successfully.");
        // Reset form
        setData(defaultData);
      } else {
        showModal(
          "error",
          "Failed",
          "There was an error editing the vehicle. Please try again.",
        );
      }
    } catch (error) {
      showModal(
        "error",
        "Error",
        "An unexpected error occurred. Please try again later.",
      );
      console.error("Submit error:", error);
    } finally {
      setLoading(false);
    }
  };

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    setLoading(true);
    await editVehicle();
  };

  if (loading) {
    return (
      <div>
        <Box textAlign="center" color="inherit">
          <p>Loading ...</p>
        </Box>
      </div>
    );
  }

  return (
    <div>
      <BaseForm
        header={header}
        content={
          <SpaceBetween size="l">
            <CreateVehicleAttributesInputPanel
              loadHelpPanelContent={loadHelpPanelContent}
              inputData={data}
              setInputData={setData}
            />
            <TagsPanel
              loadHelpPanelContent={loadHelpPanelContent}
              inputData={data}
              setInputData={setData}
            />
          </SpaceBetween>
        }
        onCancelClick={() => {
          setData(defaultData);
          navigate(UI_ROUTES.VEHICLE_MANAGEMENT);
        }}
        onSubmitClick={onSubmit}
      />
      <Modal
        visible={modalVisible}
        onDismiss={handleModalDismiss}
        header={modalConfig.title}
        closeAriaLabel="Close modal"
      >
        <Box
          color={
            modalConfig.type === "success"
              ? "text-status-success"
              : "text-status-error"
          }
        >
          <SpaceBetween size="m">
            <Box>{modalConfig.message}</Box>
            <Button onClick={handleModalDismiss} variant="primary">
              {modalConfig.type === "success" ? "Continue" : "Close"}
            </Button>
          </SpaceBetween>
        </Box>
      </Modal>
    </div>
  );
}
