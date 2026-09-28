// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0


import { Container} from "@cloudscape-design/components";
import { Content } from "./content";
import {
  FleetManagementContextProvider,
} from "./FleetManagementContext";

export default function FleetManagementView() {
  return (
    <FleetManagementContextProvider>
      <FleetManagementViewWithContext />
    </FleetManagementContextProvider>
  );
}

const FleetManagementViewWithContext = () => {
  return <Content />;
};
