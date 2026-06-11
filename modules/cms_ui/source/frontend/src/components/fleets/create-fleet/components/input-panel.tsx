// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, {
  useState,
  useImperativeHandle,
  forwardRef,
  useRef,
} from "react";
import {
  Container,
  Input,
  FormField,
  SpaceBetween,
  Textarea,
  Header,
} from "@cloudscape-design/components";

// Simple interface to replace fleet-management-client
interface CreateFleetEntry {
  name?: string;
  description?: string;
  operationalCity?: string;
  [key: string]: any;
}

export interface CreateFleetInputPanelProps {
  loadHelpPanelContent: any;
  inputData: CreateFleetEntry;
  setInputData: any;
}

export interface CreateFleetInputPanelRef {
  validate: () => boolean;
}

export const CreateFleetInputPanel = forwardRef<
  CreateFleetInputPanelRef,
  CreateFleetInputPanelProps
>(function CreateFleetInputPanel(
  { loadHelpPanelContent, inputData, setInputData },
  ref,
) {
  const [errors, setErrors] = useState({
    id: "",
    name: "",
  });

  const displayNameRef = useRef<HTMLDivElement>(null);
  const fleetIdRef = useRef<HTMLDivElement>(null);

  const validateDisplayName = (value: string): string => {
    if (!value || value.trim() === "") {
      return "Fleet Name is required.";
    }
    return "";
  };

  const validateFleetId = (value: string): string => {
    if (!value || value.trim() === "") {
      return "Fleet ID is required.";
    }
    if (!/^[A-Z0-9_-]+$/i.test(value)) {
      return "Fleet ID can only contain letters, numbers, hyphens, and underscores.";
    }
    return "";
  };

  useImperativeHandle(ref, () => ({
    validate: () => {
      const nameError = validateDisplayName(inputData.name || "");

      setErrors({
        id: "",
        name: nameError,
      });

      if (nameError) {
        displayNameRef.current?.scrollIntoView({ behavior: "smooth" });
        return false;
      }

      return true;
    },
  }));

  return (
    <Container
      header={
        <Header
          variant="h2"
          description="Provide basic information about the fleet you want to create."
        >
          Fleet Information
        </Header>
      }
    >
      <SpaceBetween size="l">
        <div ref={displayNameRef}>
          <FormField
            label="Fleet Name"
            description="A descriptive display name for the fleet."
            i18nStrings={{ errorIconAriaLabel: "Error" }}
            errorText={errors.name}
          >
            <Input
              ariaRequired={true}
              value={inputData.name ?? ""}
              onChange={({ detail: { value } }) => {
                setInputData({ name: value });
                setErrors((prev) => ({
                  ...prev,
                  name: validateDisplayName(value),
                }));
              }}
              onBlur={() => {
                const name = inputData.name || "";
                setErrors((prev) => ({
                  ...prev,
                  name: validateDisplayName(name),
                }));
              }}
            />
          </FormField>
        </div>

        <FormField
          label="Description"
          description="Optional description for the fleet."
          i18nStrings={{ errorIconAriaLabel: "Error" }}
        >
          <Textarea
            placeholder="Enter description"
            value={inputData.description ?? ""}
            onChange={({ detail: { value } }) => {
              setInputData({ description: value });
            }}
          />
        </FormField>
      </SpaceBetween>
    </Container>
  );
});
