// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useEffect } from "react";
import {
  Container,
  Input,
  FormField,
  SpaceBetween,
} from "@cloudscape-design/components";
import useLocationHash from "../../vehicle-management/use-location-hash";

// Simple interface to replace EditVehicleEntry
interface EditVehicleEntry {
  name?: string;
  vin?: string;
  make?: string;
  model?: string;
  year?: number;
  [key: string]: any;
}

export interface EditVehicleInputPanelProps {
  loadHelpPanelContent: any;
  inputData: EditVehicleEntry;
  setInputData: any;
}

export function EditVehicleInputPanel({
  loadHelpPanelContent,
  inputData,
  setInputData,
}: EditVehicleInputPanelProps) {
  const locationHash = useLocationHash();

  useEffect(() => {
    setInputData({ name: locationHash });
  }, []);

  return (
    <Container id="origin-panel" className="custom-screenshot-hide">
      <SpaceBetween size="l">
        <FormField
          label="Vehicle Name"
          description="A unique identifier for the vehicle."
          constraintText="Valid characters are a-z, A-Z, 0-9, underscores (_), hypens (-) and colons (:)."
          i18nStrings={{ errorIconAriaLabel: "Error" }}
        >
          <Input
            ariaRequired={true}
            value={locationHash ?? inputData.name}
            disabled={true}
            onChange={({ detail: { value } }) => {
              setInputData({ name: value });
            }}
          />
        </FormField>
      </SpaceBetween>
    </Container>
  );
}
