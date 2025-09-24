// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import { HelpPanel, Header } from "@cloudscape-design/components";
import { ExternalLinkGroup } from "../commons";

export function VehiclesMainInfo() {
  return (
    <HelpPanel
      header={<h2>Vehicle</h2>}
      footer={
        <ExternalLinkGroup
          items={[{ href: "#", text: "User Guide for CMS" }]}
        />
      }
    >
      <p>View vehicle management help...TODO</p>
    </HelpPanel>
  );
}

export function DashboardHeader({ actions }: { actions: React.ReactNode }) {
  return (
    <Header
      variant="h1"
      actions={actions}
    >
      Vehicle Dashboard
    </Header>
  );
}
