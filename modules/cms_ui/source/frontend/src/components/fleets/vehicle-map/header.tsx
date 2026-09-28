// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import React from "react";
import { HelpPanel, Header } from "@cloudscape-design/components";
import { ExternalLinkGroup } from "../../commons";

export function FleetVehiclesMapInfo() {
  return (
    <HelpPanel
      header={<h2>Fleet</h2>}
      footer={
        <ExternalLinkGroup
          items={[{ href: "#", text: "User Guide for CMS" }]}
        />
      }
    >
      <p>View fleet vehicles on a map...TODO</p>
    </HelpPanel>
  );
}

export function FleetVehiclesMapHeader({
  actions,
}: {
  actions: React.ReactNode;
}) {
  return (
    <Header
      variant="h1"
      actions={actions}
    >
      Fleet Vehicles Map
    </Header>
  );
}
