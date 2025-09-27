// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, useContext, ReactElement, useRef } from "react";
import { ApiContext } from "@/api/provider";
import {
  Button,
  Form,
  Header,
  SpaceBetween,
} from "@cloudscape-design/components";
import { InfoLink } from "../../../commons";
import { CreateFleetInputPanel, CreateFleetInputPanelRef } from "./input-panel";
import { TagsPanel } from "../../../commons";
import { getRuntimeConfig } from "../../../../config/api";
import { UI_ROUTES } from "@/utils/constants";
import { Modal, Box } from "@cloudscape-design/components";
import { useNavigate } from "react-router-dom";

interface BaseFormProps {
  content: React.ReactElement;
  onCancelClick: any;
  onSubmitClick: any;
  header: ReactElement;
}

export function FormHeader({ loadHelpPanelContent }: any) {
  return (
    <Header
      variant="h2"
      description="Configure the basic settings for your new fleet."
    >
      Fleet Configuration
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
        Create fleet
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

const defaultData: CreateFleetEntry = {
  id: "",
  name: "",
  description: "",
  tags: [],
};

export function FormFull({ loadHelpPanelContent, header }: any) {
  const [data, _setData] = useState<CreateFleetEntry>(defaultData);
  const setData = (updateObj = {}) =>
    _setData((prevData) => ({ ...prevData, ...updateObj }));

  const api = useContext(ApiContext);
  const navigate = useNavigate();

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
    setModalConfig({ type, title, message });
    setModalVisible(true);
  };

  const handleModalDismiss = () => {
    setModalVisible(false);
    if (modalConfig.type === "success") {
      navigate(UI_ROUTES.FLEET_MANAGEMENT);
    }
  };

  const createFleet = async () => {
    try {
      const response = await fetch(`${getRuntimeConfig().apiEndpoint}api/v1/fleets`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({ entry: data })
      });

      if (response.ok) {
        showModal("success", "Success!", "Fleet created successfully.");
        setData(defaultData);
      } else {
        showModal(
          "error",
          "Failed",
          "There was an error creating the fleet. Please try again.",
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

  const createFleetInputPanelRef = useRef<CreateFleetInputPanelRef>(null);

  const onSubmit = async (event: React.FormEvent) => {
    event.preventDefault();
    console.log('🚢 Create Fleet onSubmit called', { data });

    if (createFleetInputPanelRef.current) {
      console.log('🚢 Validating form...');
      const valid = createFleetInputPanelRef.current.validate();
      console.log('🚢 Validation result:', valid);
      if (!valid) {
        console.log('🚢 Validation failed, stopping submission');
        return;
      }
    }

    console.log('🚢 Starting fleet creation...');
    setLoading(true);
    await createFleet();
  };

  return (
    <div>
      <BaseForm
        header={header}
        content={
          <SpaceBetween size="l">
            <CreateFleetInputPanel
              ref={createFleetInputPanelRef}
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
          navigate(UI_ROUTES.FLEET_MANAGEMENT);
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
