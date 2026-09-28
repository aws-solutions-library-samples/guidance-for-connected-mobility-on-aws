// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React, { useState, SetStateAction } from "react";
import { Container, SpaceBetween } from "@cloudscape-design/components";
import { FormFull, FormHeader } from "./components/form";
import { HelpPanel } from "@cloudscape-design/components";
import { ExternalLinkGroup, HelpPanelProvider } from "../../commons";

export function CreateVehicleInfoPanel() {
  return (
    <HelpPanel
      header={<h2>Vehicle</h2>}
      footer={
        <ExternalLinkGroup
          items={[{ href: "#", text: "User Guide for CMS" }]}
        />
      }
    >
      <p>Create vehicle help...TODO</p>
    </HelpPanel>
  );
}

export function CreateVehiclePage() {
  const [toolsIndex, setToolsIndex] = useState(0);

  const loadHelpPanelContent: any = (content: SetStateAction<number>): any => {
    setToolsIndex(content);
  };

  return (
    <HelpPanelProvider value={loadHelpPanelContent}>
      <Container>
        <SpaceBetween size="l">
          <FormFull
            loadHelpPanelContent={loadHelpPanelContent}
            header={<FormHeader loadHelpPanelContent={loadHelpPanelContent} />}
          />
        </SpaceBetween>
      </Container>
    </HelpPanelProvider>
  );
}
