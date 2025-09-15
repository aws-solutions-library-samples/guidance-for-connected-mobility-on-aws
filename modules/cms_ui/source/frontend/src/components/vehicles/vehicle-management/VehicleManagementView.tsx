// Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
// SPDX-License-Identifier: Apache-2.0

import { Container } from "@cloudscape-design/components";
import { Content } from "./content";
import {
  VehicleManagementContextProvider,
} from "./VehicleManagementContext";

export default function VehicleManagementView() {
  return (
    <VehicleManagementContextProvider>
      <VehicleManagementViewWithContext />
    </VehicleManagementContextProvider>
  );
}

const VehicleManagementViewWithContext = () => {
  return (
    <Container>
      <Content />
    </Container>
  );
};
